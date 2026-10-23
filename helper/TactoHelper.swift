// TactoHelper — the process that holds the OS permissions.
//
// Why this exists (PROTOCOL.md §3.8): macOS TCC identifies a client by its
// binary, and a virtualenv's `python` is normally a symlink to a SHARED
// interpreter. Granting the Python side Accessibility therefore grants it to
// every script run with that interpreter — demonstrated on this machine, where
// an unrelated /tmp script read other apps' windows. A signed, single-purpose
// helper is identified by its code signature instead, so the grant belongs to
// Tacto alone, survives updates, and keeps OS privilege out of the model loop
// (decision D8: the agent that talks to a model must not hold the permissions).
//
// It speaks the same L2 protocol the Python driver already speaks — one JSON
// object per line on stdin, one per line on stdout — so swapping it in is a
// driver change, not a protocol change.

import Foundation
import ApplicationServices
import AppKit
import ScreenCaptureKit
import CoreMedia
import CryptoKit
import os
import ImageIO
import UniformTypeIdentifiers

let permissionHolderName = Bundle.main.object(forInfoDictionaryKey: "CFBundleDisplayName") as? String ?? "Tacto Helper"

/// Say something where it can still be heard.
///
/// LaunchServices hands a launched app no stdout and no stderr, so everything
/// this process said about why it refused a client used to go into a void —
/// the launcher saw only that we had gone. The unified log is the one channel
/// that survives having no parent:
///
///     log stream --predicate 'subsystem == "ai.callabo.tacto"'
///     log show --last 5m --predicate 'subsystem == "ai.callabo.tacto"'
let rendezvousLog = Logger(subsystem: "ai.callabo.tacto", category: "rendezvous")

@Sendable func note(_ message: String) {
    rendezvousLog.notice("\(message, privacy: .public)")
    FileHandle.standardError.write(Data((message + "\n").utf8))
}

// Declared here, at the top, and the position is load-bearing. `main.swift`
// initialises top-level globals in source order, so a `note()` called from a
// diagnostic flag that exits before this line ran wrote to stderr and silently
// dropped its unified-log half — which looked exactly like "that code never
// ran" and cost two rounds of this investigation. Anything every path may call
// has to be initialised before every path.


// A failed call is not generally proof of no effect. Only unsupported is
// classified as definitely not started; uncertain failures must not be retried.
func axActionFailureEffect(_ error: AXError) -> String {
    return error == AXError(rawValue: -25206) ? "not_started" : "unknown"
}

// MARK: - AX reading

let interactableRoles: Set<String> = [
    "AXButton", "AXTextField", "AXTextArea", "AXCheckBox", "AXRadioButton",
    "AXPopUpButton", "AXMenuButton", "AXMenuItem", "AXLink", "AXComboBox",
    "AXSlider", "AXIncrementor", "AXDisclosureTriangle", "AXTabGroup",
    "AXStaticText", "AXCell", "AXRow", "AXScrollArea", "AXGroup", "AXImage",
    // Preserve unlabelled modal boundaries for recovery and scoped selectors.
    "AXSheet",
    // Keep an unlabelled outer page visible, so an iframe cannot become the
    // shallowest observed WebArea merely because its parent has no URL yet.
    "AXWebArea",
]

@Sendable func attr(_ el: AXUIElement, _ name: String) -> CFTypeRef? {
    var value: CFTypeRef?
    return AXUIElementCopyAttributeValue(el, name as CFString, &value) == .success ? value : nil
}

@Sendable func str(_ el: AXUIElement, _ name: String) -> String? {
    guard let v = attr(el, name) else { return nil }
    if let s = v as? String { return s.isEmpty ? nil : s }
    if let n = v as? NSNumber { return n.stringValue }
    return nil
}

// The same budgets the Python driver applies, for the same reason and with the
// same numbers: the two must produce identical trees, or a recipe works
// in-process and fails across the permission boundary. Mail walks to tens of
// thousands of elements, and one enormous homogeneous list — a mailbox — is
// what makes it expensive.
let maxTreeNodes = 2500
let maxChildrenPerNode = 60
let truncationMark = "… (트리가 커서 여기서 잘렸습니다 — 더 좁은 창/앱을 대상으로 하세요)"

let labelAttrs = ["AXTitle", "AXDescription", "AXValue", "AXHelp",
                  "AXIdentifier", "AXPlaceholderValue", "AXSubrole"]
let labelNames = ["Title", "Description", "Value", "Help",
                  "Identifier", "Placeholder", "Subrole"]

/// The element's label attributes in ONE round trip. Some apps compute these
/// lazily per element — a Mail row costs 20ms read one at a time and 3ms read
/// together — and a mailbox has thousands of rows.
@Sendable func quotedLabelValue(_ value:String) -> String? {
    guard let data=try? JSONSerialization.data(withJSONObject:value,options:[.fragmentsAllowed,.withoutEscapingSlashes]),
          let encoded=String(data:data,encoding:.utf8) else { return nil }
    return encoded.replacingOccurrences(of:"\u{0085}",with:"\\u0085")
        .replacingOccurrences(of:"\u{2028}",with:"\\u2028")
        .replacingOccurrences(of:"\u{2029}",with:"\\u2029")
}

@Sendable func oneLineLabel(_ value:String) -> String {
    var text=value.replacingOccurrences(of:"\r\n",with:"\n")
    for scalar:UnicodeScalar in ["\n","\r","\u{000B}","\u{000C}","\u{001C}","\u{001D}","\u{001E}","\u{0085}","\u{2028}","\u{2029}"] {
        text=text.replacingOccurrences(of:String(scalar),with:"\\n")
    }
    return text
}

@Sendable func label(_ el: AXUIElement, quoteValues: Bool = false) -> String {
    func field(_ name:String,_ value:String) -> String {
        if quoteValues, let encoded=quotedLabelValue(value) { return "\(name): \(encoded)" }
        return "\(name): \(oneLineLabel(value))"
    }
    var raw: CFArray?
    var parts: [String] = []
    if AXUIElementCopyMultipleAttributeValues(
        el, labelAttrs as CFArray, AXCopyMultipleAttributeOptions(rawValue: 0), &raw)
        == .success, let values = raw as? [Any], values.count == labelAttrs.count {
        for (i, v) in values.enumerated() {
            if labelNames[i] == "Subrole" && (!parts.isEmpty || str(el, kAXRoleAttribute as String) != "AXButton") { continue }
            // An attribute the element lacks comes back as an AXValue holding
            // an error, not as nil, so only real strings and numbers count.
            if let s = v as? String, !s.isEmpty { parts.append(field(labelNames[i],s)) }
            else if let n = v as? NSNumber, CFGetTypeID(n) != CFBooleanGetTypeID() {
                parts.append(field(labelNames[i],n.stringValue))
            }
        }
        return parts.joined(separator: ", ")
    }
    for (i, key) in labelAttrs.enumerated() {
        if labelNames[i] == "Subrole" && (!parts.isEmpty || str(el, kAXRoleAttribute as String) != "AXButton") { continue }
        if let v = str(el, key) { parts.append(field(labelNames[i],v)) }
    }
    return parts.joined(separator: ", ")
}

/// What `secondary_action` will actually carry.
///
/// An AX action name goes straight to `AXUIElementPerformAction`, so the
/// launcher enumerates what may cross; an observation advertising anything
/// else would be describing a refusal. Press is not here because it is what
/// `click` already means — what is here is what was otherwise invisible.
/// Nothing said which elements accept a secondary action, so a caller could
/// only find out by trying one and reading the error.
let secondaryActions = ["AXShowMenu", "AXIncrement", "AXDecrement",
                        "AXConfirm", "AXCancel"]

func secondaryActionsOf(_ el: AXUIElement) -> [String] {
    var names: CFArray?
    guard AXUIElementCopyActionNames(el, &names) == .success,
          let offered = names as? [String] else { return [] }
    return secondaryActions.filter { offered.contains($0) }
}

final class ObservationReadMetrics {
    var packetAttempts = 0
    var packetFallbacks = 0
    var secondaryActionReads = 0
    // Where an observation's time goes, kept apart from the closed count set
    // above (probes accept exactly those keys): reported as observation_timing_ms.
    var packetNanos: UInt64 = 0
    var secondaryActionNanos: UInt64 = 0
    var metadata: [String: Int] {
        ["packet_attempts":packetAttempts,"packet_successes":packetAttempts-packetFallbacks,
         "packet_fallbacks":packetFallbacks,"secondary_action_reads":secondaryActionReads]
    }
}

/// One node's descriptive observation data; never reused by probes/actions or
/// another node/observation. AX multiple attributes are not an atomic snapshot.
struct ObservationNodeSnapshot {
    let values: [String: Any]
    init?(_ element: AXUIElement) {
        let names = [kAXRoleAttribute as String] + labelAttrs +
            [kAXEnabledAttribute as String, kAXSelectedAttribute as String,
             kAXChildrenAttribute as String, "AXDocument", "AXURL"]
        var raw: CFArray?
        guard AXUIElementCopyMultipleAttributeValues(element, names as CFArray,
                AXCopyMultipleAttributeOptions(rawValue: 0), &raw) == .success,
              let entries = raw as? [Any], entries.count == names.count else { return nil }
        values = Dictionary(uniqueKeysWithValues: zip(names, entries))
    }
    func value(_ name: String) -> Any? {
        guard let raw = values[name] else { return nil }
        if CFGetTypeID(raw as CFTypeRef) == AXValueGetTypeID(),
           AXValueGetType(raw as! AXValue) == .axError { return nil }
        return raw
    }
    func string(_ name: String) -> String? {
        if let text = value(name) as? String { return text.isEmpty ? nil : text }
        if let number = value(name) as? NSNumber { return number.stringValue }
        return nil
    }
    func label(role: String, quoteValues: Bool) -> String {
        var parts: [String] = []
        for (index, key) in labelAttrs.enumerated() {
            if labelNames[index] == "Subrole" && (!parts.isEmpty || role != "AXButton") { continue }
            let text: String
            if let raw = value(key) as? String, !raw.isEmpty { text = raw }
            else if let raw = value(key) as? NSNumber, CFGetTypeID(raw) != CFBooleanGetTypeID() {
                text = raw.stringValue
            } else { continue }
            parts.append(labelNames[index] + ": " +
                (quoteValues ? quotedLabelValue(text) ?? oneLineLabel(text) : oneLineLabel(text)))
        }
        return parts.joined(separator: ", ")
    }
}

/// A reveal is descriptive data, never a substring search or an AX action.
func validRevealRequest(_ raw: Any) -> [String]? {
    guard let labels = raw as? [String], (1...8).contains(labels.count),
          labels.allSatisfy({ !$0.isEmpty && $0.unicodeScalars.count <= 256 &&
              $0.rangeOfCharacter(from: .controlCharacters.union(.newlines)) == nil }) else { return nil }
    return labels
}

/// Separate search work from the ordinary 2500-node rendered tree. Every omitted
/// child is probed only shallowly, and every probe shares one bounded scan budget.
final class ObservationRevealScan {
    let labels: [String]
    var complete = true
    var scannedNodes = 0
    var revealed = 0
    var started: Double? = nil
    let now: () -> Double
    let snapshot: (AXUIElement, Double) -> ObservationNodeSnapshot?
    init(_ labels: [String], now: @escaping () -> Double = { ProcessInfo.processInfo.systemUptime },
         snapshot: @escaping (AXUIElement, Double) -> ObservationNodeSnapshot? = { element, remaining in
             // Set and reset on this element only; a slow app must not turn a
             // bounded reveal scan into thousands of system-default AX waits.
             guard AXUIElementSetMessagingTimeout(element, Float(min(0.2, remaining))) == .success else { return nil }
             defer { _ = AXUIElementSetMessagingTimeout(element, 0) }
             return ObservationNodeSnapshot(element)
         }) {
        self.labels = labels; self.now = now; self.snapshot = snapshot
    }
    var available: Bool {
        if started == nil { started = now() }
        let allowed = scannedNodes < 5000 && now() - started! < 3
        if !allowed { complete = false }
        return allowed
    }
    func matches(_ root: AXUIElement) -> Bool {
        var localNodes = 0
        func probe(_ element: AXUIElement, _ depth: Int) -> Bool {
            guard available else { return false }
            guard localNodes < 24 else { complete = false; return false }
            localNodes += 1; scannedNodes += 1
            let remaining = 3 - (now() - started!)
            guard remaining > 0, let packet = snapshot(element, remaining) else { complete = false; return false }
            guard now() - started! < 3 else { complete = false; return false }
            // Swift String equality folds canonical Unicode equivalents;
            // reveal, like Python, compares the raw scalar sequence exactly.
            func exact(_ text: String) -> Bool { labels.contains { $0.utf8.elementsEqual(text.utf8) } }
            if ["AXTitle", "AXDescription", "AXValue", "AXIdentifier"].contains(where: {
                if let text = packet.value($0) as? String { return exact(text) }
                if let number = packet.value($0) as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID() {
                    return exact(number.stringValue)
                }
                return false
            }) { return true }
            // Unsupported/absent attributes are ordinary leaves; transport
            // failures inside an otherwise successful batch are not absence.
            for key in ["AXTitle", "AXDescription", "AXValue", "AXIdentifier", kAXChildrenAttribute as String] {
                if let raw = packet.values[key], CFGetTypeID(raw as CFTypeRef) == AXValueGetTypeID(),
                   AXValueGetType(raw as! AXValue) == .axError {
                    var error = AXError.success
                    if !AXValueGetValue(raw as! AXValue, .axError, &error) ||
                        ![AXError.attributeUnsupported, AXError.noValue].contains(error) { complete = false }
                }
            }
            let children = packet.value(kAXChildrenAttribute as String) as? [AXUIElement] ?? []
            if depth >= 3 {
                if !children.isEmpty { complete = false }
                return false
            }
            for (position, child) in children.enumerated() {
                if probe(child, depth + 1) { return true }
                if position + 1 < children.count && (localNodes >= 24 || !available) {
                    complete = false; break
                }
            }
            return false
        }
        return probe(root, 0)
    }
}

/// One line per element, in the exact format the Python `Selector` parses:
/// `<indent><index> <Role>  Key: value, Key2: value [disabled] [selected]`
func walk(_ el: AXUIElement, depth: Int, out: inout [String],
          elements: inout [AXUIElement], visited: inout Int,
          truncated: inout Bool, nodeLimit: Int = maxTreeNodes,
          deferred: ((AXUIElement, Int) -> Void)? = nil,
          metrics: ObservationReadMetrics? = nil, reveal: ObservationRevealScan? = nil,
          selectedByParent: Bool = false) {
    if depth > 25 { reveal?.complete = false; return }
    if visited >= min(maxTreeNodes, nodeLimit) {
        reveal?.complete = false
        if let deferred { deferred(el, depth) } else { truncated = true }
        return
    }
    visited += 1
    metrics?.packetAttempts += 1
    let packetStart = DispatchTime.now().uptimeNanoseconds
    let snapshot = ObservationNodeSnapshot(el)
    metrics?.packetNanos += DispatchTime.now().uptimeNanoseconds &- packetStart
    if snapshot == nil { metrics?.packetFallbacks += 1 }
    let role = snapshot.map { $0.string(kAXRoleAttribute as String) ?? "?" }
        ?? str(el, kAXRoleAttribute as String) ?? "?"
    let quoteValues = role == kAXWindowRole as String || role == "AXWebArea" || role == "AXLink"
    var text = snapshot?.label(role:role,quoteValues:quoteValues)
        ?? label(el,quoteValues:quoteValues)
    // A window says which file it shows, so an agent can name that file
    // (textedit_save needs the absolute path; the title is only a name).
    // file:// only, decoded and composed; anything else is not shown.
    let document = snapshot.map { $0.string("AXDocument") } ??
        (role == kAXWindowRole as String ? str(el, "AXDocument") : nil)
    if role == kAXWindowRole as String, let doc = document,
       let path = documentURLSyntaxPath(doc), !path.hasPrefix("/.file/") {
        if let encoded=quotedLabelValue(path.precomposedStringWithCanonicalMapping) {
            text += (text.isEmpty ? "" : ", ") + "Document: \(encoded)"
        }
    }
    if ["AXWebArea", "AXLink"].contains(role),
       let url = (snapshot?.value("AXURL") as? URL) ?? (attr(el, "AXURL") as? URL) {
        let doc = url.absoluteString
        if doc.count <= 2048,
              doc.rangeOfCharacter(from: .controlCharacters.union(.newlines)) == nil,
              let parts = URLComponents(string: doc),
              let scheme = parts.scheme?.lowercased(), ["http", "https"].contains(scheme),
              doc.lowercased().hasPrefix(scheme + "://"),
              let host = parts.host, !host.isEmpty,
              parts.user == nil, parts.password == nil,
              let encoded = quotedLabelValue(doc) {
            // Safari exposes the page URL as NSURL on WebArea, independently
            // of the editable address bar. Preserve identity without truncation.
            text += (text.isEmpty ? "" : ", ") + "URL: \(encoded)"
        }
    }
    if interactableRoles.contains(role) || !text.isEmpty {
        let idx = elements.count
        elements.append(el)
        let enabled = snapshot.map { ($0.value(kAXEnabledAttribute as String) as? Bool) ?? true }
            ?? (attr(el, kAXEnabledAttribute as String) as? Bool) ?? true
        // AXSelected is how a chosen button in a set says so (System
        // Settings' Light / Dark / Auto); shown like [disabled] (2026-10-09).
        // A list can hold the selection itself, its items never saying
        // AXSelected (Finder's column view, 2026-10-23: a selected file read
        // as unselected, so a correct selection looked like a failure). The
        // item is often an unlabeled group, so its labeled contents say it.
        let selected = selectedByParent || (snapshot.map { ($0.value(kAXSelectedAttribute as String) as? Bool) == true }
            ?? ((attr(el, kAXSelectedAttribute as String) as? Bool) == true))
        let short = role.hasPrefix("AX") ? String(role.dropFirst(2)) : role
        metrics?.secondaryActionReads += 1
        let actionStart = DispatchTime.now().uptimeNanoseconds
        let extra = secondaryActionsOf(el)
        metrics?.secondaryActionNanos += DispatchTime.now().uptimeNanoseconds &- actionStart
        // "  Secondary Actions: …" when there is no label before it, so the
        // role token never carries a comma ("Cell," matched nothing).
        let actions = extra.isEmpty ? ""
            : (text.isEmpty ? "  " : ", ") + "Secondary Actions: " + extra.map { String($0.dropFirst(2)) }
                                             .joined(separator: ", ")
        out.append(String(repeating: "  ", count: depth) + "\(idx) \(short)"
                   + (text.isEmpty ? "" : "  \(text)") + (enabled ? "" : " [disabled]")
                   + (selected ? " [selected]" : "") + actions)
    }
    let children = snapshot.map { ($0.value(kAXChildrenAttribute as String) as? [AXUIElement]) ?? [] }
        ?? (attr(el, kAXChildrenAttribute as String) as? [AXUIElement]) ?? []
    let chosen = role == "AXList" ? (attr(el, "AXSelectedChildren") as? [AXUIElement]) ?? [] : []
    for child in children.prefix(maxChildrenPerNode) {
        walk(child, depth: depth + 1, out: &out, elements: &elements,
             visited: &visited, truncated: &truncated, nodeLimit: nodeLimit, deferred: deferred, metrics: metrics, reveal: reveal,
             selectedByParent: selectedByParent || chosen.contains(child))
    }
    if children.count > maxChildrenPerNode {
        truncated = true
        var revealed = 0
        if let reveal {
            for child in children.dropFirst(maxChildrenPerNode) {
                guard visited < min(maxTreeNodes, nodeLimit) else { reveal.complete = false; break }
                guard reveal.available else { break }
                if reveal.matches(child) {
                    let before = visited
                    walk(child, depth: depth + 1, out: &out, elements: &elements,
                         visited: &visited, truncated: &truncated, nodeLimit: nodeLimit,
                         deferred: deferred, metrics: metrics, reveal: reveal,
                         selectedByParent: selectedByParent || chosen.contains(child))
                    if visited > before { revealed += 1; reveal.revealed += 1 }
                }
            }
        }
        out.append(String(repeating: "  ", count: depth + 1)
                   + "… (\(children.count - maxChildrenPerNode)개 더 있음"
                   + (revealed > 0 ? "; 찾는 항목 \(revealed)개 표시" : "") + ")")
    }
}

// MARK: - Session state

// Only the newest observation is actionable. Keeping every obs_id valid forever
// would let a caller act on a tree captured before the UI changed — the same
// staleness rule the Python driver enforces has to hold here too, or routing
// through the helper would weaken it (advisor finding).
// Connection-local in effect: reset when a new connection is accepted, so a
// later client never inherits the previous one's observation (they were global
// and survived across connections — advisor finding).
var latestObsID: String = ""
// Every replacement and in-place Array mutation invalidates the map, even
// when a caller accidentally retains the observation ID or element count.
// The connection worker owns these writes. Checking this generation avoids
// rescanning the whole observed tree before and after every AX operation.
var latestElementGeneration = UUID()
var latestElements: [AXUIElement] = [] {
    didSet { latestElementGeneration = UUID() }
}
// The observation belongs to ONE app. Without this, an obs_id taken from app A
// could be presented while acting on app B — and `type_text` does not even
// consult the element list, so it would simply type into the other app
// (advisor finding).
var latestTargetPID: pid_t = -1
var latestProcessStart: Double? = nil
var latestTargetName: String = ""
var latestDocumentToken: String? = nil

// MARK: - The one menu we opened
//
// `select_option` opens, reads and presses in a single call, which only works
// when the caller already knows the option's name. A general run does not:
// nothing in the tree says what a closed popup contains, and the one way to
// find out is to open it — which is a change to the screen, not a reading.
//
// So the steps are separated. `open_select` opens and stops; the observation
// that follows reports what is now visible; `select_custom_option` chooses from
// THAT observation. Observing never opens anything, and nothing re-opens a menu
// by itself.
//
// Both identities are pinned. `control` is the popup that was pressed and
// `menu` the menu that press produced, so a later choice cannot land in a menu
// some other control opened, nor press an item in ours on behalf of a control
// that has since been replaced.
final class PendingSelect {
    // Which app this menu belongs to. Without it, observing a DIFFERENT app
    // would look for our menu among that app's menus, fail to find it, and
    // throw away a menu still standing in the first one (peer review).
    let pid: pid_t
    // Per-observation, so it is re-pinned each time the screen is read; the
    // CF identity below is what actually persists.
    var index: Int
    let control: AXUIElement
    let identifier: String?
    let role: String
    let label: String
    let menu: AXUIElement
    // Filled in by the observation that reports this menu — never at open
    // time. A choice may only be made from a list the caller was shown.
    var options: [[String: Any]]?
    var optionsObsID: String?
    let popupWindowID:CGWindowID?
    let processStart:Double?

    init(pid: pid_t, index: Int, control: AXUIElement, identifier: String?,
         role: String, label: String, menu: AXUIElement,processStart:Double?=nil) {
        self.pid = pid
        self.index = index; self.control = control; self.identifier = identifier
        self.role = role; self.label = label; self.menu = menu
        self.popupWindowID=comboBoxPopupWindowID(menu)
        self.processStart=processStart
    }
}

// A registry of ONE. A second open would leave a menu standing that nothing is
// tracking, and the caller would have no way to say which of them it meant.
var pendingSelect: PendingSelect?

/// How many items a popup may offer before this helper stops claiming it can
/// report them. The choice is checked against the FULL option set, so a list we
/// only partly read could hide the very change that check exists to catch.
let maxMenuOptions = 200

/// Every item this menu offers, in order, as {label, disabled}.
///
/// No `value`: a native menu item has a title and nothing behind it, and
/// inventing a value space would promise a distinction this surface does not
/// have. `nil` means the menu offers more than we will vouch for.
func menuOptions(_ menu: AXUIElement, scope:NativeWindowReadScope? = nil) -> [[String: Any]]? {
    let menuRole=comboBoxString(menu,kAXRoleAttribute as String,scope:scope)
    // Failed scoped reads cannot fall through to an unscoped menu query.
    guard scope == nil || menuRole != nil else {return nil}
    if menuRole=="AXList" {
        guard let rows=comboBoxListRows(menu,scope:scope) else { return nil }
        return rows.map { ["label":$0.label,"disabled":$0.disabled] }
    }
    var out: [[String: Any]] = []
    for item in (comboBoxAttribute(menu,kAXChildrenAttribute as String,scope:scope) as? [AXUIElement]) ?? [] {
        if out.count >= maxMenuOptions { return nil }
        guard let title = comboBoxString(item,kAXTitleAttribute as String,scope:scope), !title.isEmpty else {
            continue                          // separators
        }
        out.append(["label": title,
                    "disabled": (comboBoxAttribute(item,kAXEnabledAttribute as String,scope:scope) as? Bool) == false])
    }
    return out
}

/// Two option lists, compared item by item in order.
///
/// `[[String: Any]]` is not Equatable, and the two fields are the whole of what
/// this surface reports, so they are the whole of what has to agree. Order
/// counts: an item inserted above the one we mean changes nothing about the
/// item itself and is exactly how a choice lands on the wrong row.
func sameOptions(_ a: [[String: Any]], _ b: [[String: Any]]) -> Bool {
    guard a.count == b.count else { return false }
    for (x, y) in zip(a, b) {
        if (x["label"] as? String) != (y["label"] as? String) { return false }
        if (x["disabled"] as? Bool) != (y["disabled"] as? Bool) { return false }
    }
    return true
}

/// Is the menu we pinned still the menu that is up? CFEqual against what the
/// app currently owns: a menu that closed and a menu that was replaced by a
/// different one are the same answer here — not ours, so nothing may be
/// pressed in it.
func menuIsOurs(_ p: pid_t, _ pending: PendingSelect) -> Bool {
    selectionContainerIsOurs(pending.control,pending.menu,p,knownPopupWindowID:pending.popupWindowID,knownProcessStart:pending.processStart)
}

func selectionContainerIsOurs(_ control:AXUIElement,_ container:AXUIElement,_ pid:pid_t,
    knownPopupWindowID:CGWindowID?=nil,knownProcessStart:Double?=nil)->Bool {
    if knownPopupWindowID != nil || str(container,kAXRoleAttribute as String)=="AXList" {
        switch comboBoxContainerState(control,pid) {
        case .closed:
            if let id=knownPopupWindowID {return (try? nativePopupVisibility(id,pid)) != false}
            return false
        case .open(let current):return CFEqual(current,container)
        case .unknown:
            // A definitively invalid old control plus absence of its pinned
            // physical popup can free a slot. Timeout/unknown reads cannot.
            if let id=knownPopupWindowID {
                var raw:CFTypeRef?
                let invalid=AXUIElementCopyAttributeValue(control,kAXRoleAttribute as CFString,&raw) == .invalidUIElement
                if (invalid || nativeProcessInstanceEnded(pid,expectedStart:knownProcessStart)),
                   (try? nativePopupVisibility(id,pid))==false {return false}
            }
            return true
        }
    }
    return appMenus(pid).contains { CFEqual($0,container) }
}

/// Scalar metadata used during pinned selection shares the binding read scope.
/// Observation callers retain the existing unscoped read contract.
func comboBoxAttribute(_ node:AXUIElement,_ name:String,scope:NativeWindowReadScope?=nil)->CFTypeRef? {
    guard let scope else {return attr(node,name)}
    guard let (error,value)=try? scope.read(node,name),error == .success else {return nil}
    return value
}

func comboBoxString(_ node:AXUIElement,_ name:String,scope:NativeWindowReadScope?=nil)->String? {
    guard let value=comboBoxAttribute(node,name,scope:scope) else {return nil}
    if let text=value as? String {return text.isEmpty ? nil:text}
    if let number=value as? NSNumber {return number.stringValue}
    return nil
}

func comboBoxListVisibility(_ list:AXUIElement,_ pid:pid_t,scope:NativeWindowReadScope?=nil)->Bool? {
    guard let id=comboBoxPopupWindowID(list,scope:scope) else {return nil}
    return try? nativePopupVisibility(id,pid,scope:scope)
}

func comboBoxPopupWindowID(_ list:AXUIElement,scope:NativeWindowReadScope?=nil)->CGWindowID? {
    guard comboBoxString(list,kAXRoleAttribute as String,scope:scope)=="AXList" else {return nil}
    guard let raw=comboBoxAttribute(list,kAXParentAttribute as String,scope:scope),CFGetTypeID(raw)==AXUIElementGetTypeID(),
          let id=nativeWindowID(raw as! AXUIElement,scope:scope) else {return nil}
    return id
}

func comboBoxChildren(_ node:AXUIElement,_ limit:Int,scope:NativeWindowReadScope?=nil)->[AXUIElement]? {
    guard let raw=comboBoxAttribute(node,kAXChildrenAttribute as String,scope:scope),
          CFGetTypeID(raw)==CFArrayGetTypeID(),let values=raw as? [AnyObject],
          values.count<=limit,values.allSatisfy({CFGetTypeID($0)==AXUIElementGetTypeID()}) else { return nil }
    return values.map {$0 as! AXUIElement}
}

enum ComboBoxContainerState {case closed,open(AXUIElement),unknown}

/// Only classify the measured direct-child AppKit shape. Missing/malformed
/// children, ownership, roles or list data are never an invitation to press.
func comboBoxContainerState(_ control:AXUIElement,_ pid:pid_t,scope:NativeWindowReadScope?=nil)->ComboBoxContainerState {
    var owner:pid_t=0
    guard pid>0,AXUIElementGetPid(control,&owner) == .success,owner==pid,
          let role=comboBoxAttribute(control,kAXRoleAttribute as String,scope:scope),CFGetTypeID(role)==CFStringGetTypeID(),
          role as? String=="AXComboBox",let children=comboBoxChildren(control,8,scope:scope) else {return .unknown}
    var buttons=0,texts=0,scrolls=0
    for child in children {
        var childPID:pid_t=0
        guard AXUIElementGetPid(child,&childPID) == .success,childPID==pid,
              let parent=comboBoxAttribute(child,kAXParentAttribute as String,scope:scope),CFGetTypeID(parent)==AXUIElementGetTypeID(),
              CFEqual(parent,control),let raw=comboBoxAttribute(child,kAXRoleAttribute as String,scope:scope),
              CFGetTypeID(raw)==CFStringGetTypeID(),let role=raw as? String else {return .unknown}
        switch role {
        case "AXButton":buttons+=1
        case "AXTextField":texts+=1
        case "AXScrollArea":scrolls+=1
        default:return .unknown
        }
    }
    guard buttons==1,texts<=1,scrolls<=1 else {return .unknown}
    if scrolls==0 {return .closed}
    guard let list=comboBoxOwnedList(control,pid,scope:scope),let visible=comboBoxListVisibility(list,pid,scope:scope) else {return .unknown}
    return visible ? .open(list):.closed
}

func comboBoxOwnedList(_ control:AXUIElement,_ pid:pid_t,scope:NativeWindowReadScope?=nil)->AXUIElement? {
    func owned(_ node:AXUIElement,_ parent:AXUIElement,_ role:String)->Bool {
        var owner:pid_t=0
        guard AXUIElementGetPid(node,&owner) == .success,owner==pid,
              comboBoxString(node,kAXRoleAttribute as String,scope:scope)==role,
              let actual=comboBoxAttribute(node,kAXParentAttribute as String,scope:scope),
              CFGetTypeID(actual)==AXUIElementGetTypeID(),CFEqual(actual,parent) else { return false }
        return true
    }
    var owner:pid_t=0
    guard AXUIElementGetPid(control,&owner) == .success,owner==pid,
          comboBoxString(control,kAXRoleAttribute as String,scope:scope)=="AXComboBox",
          let children=comboBoxChildren(control,8,scope:scope),
          children.allSatisfy({comboBoxString($0,kAXRoleAttribute as String,scope:scope) != nil}) else { return nil }
    let scrolls=children.filter {comboBoxString($0,kAXRoleAttribute as String,scope:scope)=="AXScrollArea"}
    guard scrolls.count==1,owned(scrolls[0],control,"AXScrollArea"),
          let contents=comboBoxChildren(scrolls[0],2,scope:scope),!contents.isEmpty else {return nil}
    var lists:[AXUIElement]=[],bars=0
    for child in contents {
        guard let raw=comboBoxAttribute(child,kAXRoleAttribute as String,scope:scope),CFGetTypeID(raw)==CFStringGetTypeID(),
              let role=raw as? String,["AXList","AXScrollBar"].contains(role),
              owned(child,scrolls[0],role) else {return nil}
        if role=="AXList" {lists.append(child)} else {bars+=1}
    }
    guard lists.count==1,bars<=1 else {return nil}
    return lists[0]
}

/// All fields remain required. Batch transport unsupported may use the same
/// strict scalar reads; timeout/malformed replies never invite a second API.
/// No row or validation result is cached across these complete list reads.
/// Selection binding supplies its existing read scope, so batch and scalar
/// fallback share the observation deadline and per-read context/abort checks.
func comboBoxListAttributes(_ node:AXUIElement,_ names:[String],scope:NativeWindowReadScope?=nil)->[CFTypeRef]? {
    do {
        if let fields=try nativeReadinessBatch(node,names:names,scope:scope) {
            guard fields.count==names.count,fields.allSatisfy({$0.0 == .success && $0.1 != nil}) else {return nil}
            return fields.map {$0.1!}
        }
        var values:[CFTypeRef]=[]
        for name in names {
            if let scope {
                let (error,value)=try scope.read(node,name)
                guard error == .success,let value else {return nil}
                values.append(value)
            } else {
                guard let value=attr(node,name) else {return nil}
                values.append(value)
            }
        }
        return values
    } catch {return nil}
}

func comboBoxListRows(_ list:AXUIElement,scope:NativeWindowReadScope?=nil)->[(node:AXUIElement,text:AXUIElement,label:String,disabled:Bool)]? {
    func children(_ value:CFTypeRef,_ limit:Int)->[AXUIElement]? {
        guard CFGetTypeID(value)==CFArrayGetTypeID(),let values=value as? [AnyObject],
              values.count<=limit,values.allSatisfy({CFGetTypeID($0)==AXUIElementGetTypeID()}) else {return nil}
        return values.map {$0 as! AXUIElement}
    }
    var pid:pid_t=0
    guard AXUIElementGetPid(list,&pid) == .success,pid>0,
          let listFields=comboBoxListAttributes(list,["AXRole","AXChildren"],scope:scope),
          CFGetTypeID(listFields[0])==CFStringGetTypeID(),listFields[0] as? String=="AXList",
          let rows=children(listFields[1],maxMenuOptions),!rows.isEmpty else {return nil}
    var result:[(node:AXUIElement,text:AXUIElement,label:String,disabled:Bool)]=[]
    for row in rows {
        var rowPID:pid_t=0,textPID:pid_t=0
        guard AXUIElementGetPid(row,&rowPID) == .success,rowPID==pid,
              let rowFields=comboBoxListAttributes(row,["AXRole","AXParent","AXChildren"],scope:scope),
              CFGetTypeID(rowFields[0])==CFStringGetTypeID(),rowFields[0] as? String=="AXGroup",
              CFGetTypeID(rowFields[1])==AXUIElementGetTypeID(),CFEqual(rowFields[1],list),
              let cells=children(rowFields[2],1),cells.count==1,
              AXUIElementGetPid(cells[0],&textPID) == .success,textPID==pid,
              let textFields=comboBoxListAttributes(cells[0],["AXRole","AXParent","AXValue","AXEnabled"],scope:scope),
              CFGetTypeID(textFields[0])==CFStringGetTypeID(),textFields[0] as? String=="AXTextField",
              CFGetTypeID(textFields[1])==AXUIElementGetTypeID(),CFEqual(textFields[1],row),
              let value=textFields[2] as? String,!value.isEmpty,
              CFGetTypeID(textFields[3])==CFBooleanGetTypeID() else {return nil}
        result.append((row,cells[0],value,(textFields[3] as? Bool)==false))
    }
    return result
}

/// Estimate the existing scrollbar action's tenth-of-range page count. The
/// complete row span supplies the range; provider padding/nonlinear scrolling
/// may differ, so this is a hint and never evidence that a row became visible.
func comboBoxScrollPagesHint(_ row: CGRect, _ viewport: CGRect, _ contentHeight: CGFloat) -> Int? {
    let numbers = [row.minX,row.maxX,row.minY,row.maxY,row.width,row.height,
                   viewport.minX,viewport.maxX,viewport.minY,viewport.maxY,
                   viewport.width,viewport.height,contentHeight]
    guard numbers.allSatisfy({$0.isFinite}), row.width > 0, row.height > 0,
          viewport.width > 0, viewport.height > 0, row.height <= viewport.height,
          row.minX >= viewport.minX, row.maxX <= viewport.maxX,
          contentHeight > viewport.height else {return nil}
    let gap = row.minY < viewport.minY ? viewport.minY-row.minY : row.maxY-viewport.maxY
    guard gap > 0 else {return nil}
    let pages = ceil(gap / ((contentHeight-viewport.height) * 0.1))
    guard pages.isFinite else {return nil}
    return Int(max(1,min(10,pages)))
}

/// Read-only planning hints, never authority for a later click. Dispatch still
/// binds the physical popup and fully contained row against fresh live state.
func comboBoxVisibleOptions(_ list:AXUIElement,_ pid:pid_t,_ expectedID:CGWindowID)->[[String:Any]]? {
    guard comboBoxPopupWindowID(list)==expectedID,
          comboBoxListVisibility(list,pid)==true,
          let raw=attr(list,kAXParentAttribute as String),CFGetTypeID(raw)==AXUIElementGetTypeID(),
          let rows=comboBoxListRows(list) else {return nil}
    let scroll=raw as! AXUIElement
    var owner:pid_t=0
    guard AXUIElementGetPid(scroll,&owner) == .success,owner==pid,
          let bounds=try? windowInputFrame(scroll),
          (try? windowInputCGFrame(expectedID,pid))==bounds else {return nil}
    var rectangles:[CGRect]=[]
    for row in rows {
        guard AXUIElementGetPid(row.node,&owner) == .success,owner==pid,
              let rect=try? windowInputFrame(row.node) else {return nil}
        rectangles.append(rect)
    }
    guard let top=rectangles.map({$0.minY}).min(),
          let bottom=rectangles.map({$0.maxY}).max() else {return nil}
    var options:[[String:Any]]=[]
    for (row,rect) in zip(rows,rectangles) {
        var option:[String:Any]=["label":row.label,"disabled":row.disabled,
            "visible":rect.minX>=bounds.minX && rect.maxX<=bounds.maxX &&
                      rect.minY>=bounds.minY && rect.maxY<=bounds.maxY]
        if rect.width<=bounds.width,rect.height<=bounds.height,
           rect.minX>=bounds.minX,rect.maxX<=bounds.maxX {
            if rect.minY<bounds.minY {option["scroll_direction"]="up"}
            else if rect.maxY>bounds.maxY {option["scroll_direction"]="down"}
            if let pages=comboBoxScrollPagesHint(rect,bounds,bottom-top) {
                option["scroll_pages_hint"]=pages
            }
        }
        options.append(option)
    }
    return options
}

/// A menu this helper opened, already spent on a selection, that did not close
/// itself. Nothing acts on it any more, but it is still on the person's screen
/// — and an observation calling that control "closed" would tell the next step
/// a menu is out of the way when it is not.
var spentMenu: (pid: pid_t, control: AXUIElement, menu: AXUIElement,popupWindowID:CGWindowID?,processStart:Double?)?

/// Is a menu this helper opened still on screen? One slot, one menu.
///
/// Asked BEFORE anything is dispatched, and it mutates nothing. Two ways the
/// slot is taken: a pin nobody has spent, or a menu already spent that did not
/// close itself. Either way a second AXShowMenu would put a menu on top of a
/// menu, and this helper will not dismiss the first to make room — so it
/// refuses without sending anything.
///
/// A live pin is NOT AX-liveness-checked here. The observation path drops a pin whose
/// menu has gone, and checking it here would let a look at one app throw away
/// what is standing in another. Exception: its recorded process instance ended
/// AND its pinned physical popup is confirmed absent; no old app input remains.
func menuSlotBusy() -> String? {
    if let pending=pendingSelect {
        if let id=pending.popupWindowID,
           nativeProcessInstanceEnded(pending.pid,expectedStart:pending.processStart),
           (try? nativePopupVisibility(id,pending.pid))==false {
            pendingSelect=nil
        } else {
            return "이미 열어 둔 선택지가 있습니다 — 먼저 고르거나 화면을 다시 관측하세요"
        }
    }
    guard let spent = spentMenu else { return nil }
    if !selectionContainerIsOurs(spent.control,spent.menu,spent.pid,knownPopupWindowID:spent.popupWindowID,knownProcessStart:spent.processStart) {
        spentMenu = nil                       // it closed; the slot is free
        return nil
    }
    return "앞서 연 메뉴가 아직 열려 있습니다 — 자동으로 닫지 않으므로 새로 열지 않습니다"
}

/// Stop tracking the menu. Send nothing.
///
/// This used to `AXCancel` the menu on the way out, and that cancel is what
/// In the one case that was measured, a popup whose menu had been dismissed
/// that way then refused to post a menu at all — every later AXShowMenu
/// returned success and nothing appeared (native-select-blocker.md). That is
/// what is known: an automatic cancel from here was followed by an unusable
/// control. It is not a claim about menus being closed some other way.
///
/// So a refusal now leaves the menu exactly where it is. The caller is told it
/// is open; what to do about it belongs to whoever can see the screen, not to
/// the step that failed.
/// Some AppKit ComboBoxes expose their open action only on one direct button.
/// Never search descendants or choose a first match; the caller may use this
/// only after BOTH actions on the combo itself returned actionUnsupported.
func comboBoxOpenButton(_ control: AXUIElement, _ pid: pid_t) -> AXUIElement? {
    guard str(control,kAXRoleAttribute as String)=="AXComboBox",
          let children=comboBoxChildren(control,8) else { return nil }
    var buttons:[AXUIElement]=[]
    for child in children {
        guard let role=str(child,kAXRoleAttribute as String),!role.isEmpty else { return nil }
        if role=="AXButton" { buttons.append(child) }
    }
    guard buttons.count==1 else { return nil }
    let button=buttons[0]
    var controlPID:pid_t=0,buttonPID:pid_t=0
    guard AXUIElementGetPid(control,&controlPID) == .success,controlPID==pid,
          AXUIElementGetPid(button,&buttonPID) == .success,buttonPID==pid,
          let parent=attr(button,kAXParentAttribute as String),
          CFGetTypeID(parent)==AXUIElementGetTypeID(),CFEqual(parent,control),
          let enabled=attr(button,kAXEnabledAttribute as String),
          CFGetTypeID(enabled)==CFBooleanGetTypeID(),enabled as? Bool==true else { return nil }
    var names:CFArray?
    guard AXUIElementCopyActionNames(button,&names) == .success,
          let actions=names as? [String],actions.count<=64,
          actions.filter({$0==kAXPressAction as String}).count==1 else { return nil }
    return button
}

func forgetPin() {
    pendingSelect = nil
}

/// What each popup on this screen offers — WITHOUT opening anything.
///
/// A closed native popup does not publish its items: the menu does not exist
/// until it is opened. So a closed one is reported with no options and the
/// caller is offered `open_select` instead, and the only menu read here is the
/// one this helper opened, found again by the identity it was pinned under
/// rather than by being whichever menu happens to be up.
func customSelects(_ p: pid_t, _ elements: [AXUIElement], obsID: String, candidates: [Int]? = nil) -> [String: Any] {
    // Another app's screen says nothing about the menu we left standing over
    // here, so it is neither consulted nor thrown away: as far as THIS
    // observation is concerned, every popup in it is closed (peer review).
    // A look at ANOTHER app says nothing about a menu left standing over here,
    // so it is not consulted and not thrown away. Only an observation of the
    // same app, finding the menu really gone, drops the record (peer review).
    var spent = spentMenu
    if let held = spent, held.pid != p {
        spent = nil
    } else if let held = spent,
              !selectionContainerIsOurs(held.control,held.menu,p,knownPopupWindowID:held.popupWindowID,knownProcessStart:held.processStart) {
        spentMenu = nil
        spent = nil
    }
    var pending = pendingSelect
    if let held = pending, held.pid != p {
        pending = nil
    } else if let held = pending, !menuIsOurs(p, held) {
        pendingSelect = nil                   // it went away on its own
        pending = nil
    }
    var out: [String: Any] = [:]
    // The walk already knows every element's role; asking each one again
    // cost two seconds in a crowded Finder (2,500 elements, rc.4 faults).
    for i in candidates ?? Array(elements.indices) where i >= 0 && i < elements.count {
        let el = elements[i]
        let role = str(el, kAXRoleAttribute as String) ?? ""
        guard ["AXPopUpButton", "AXComboBox"].contains(role) else { continue }
        if role=="AXComboBox",case .unknown=comboBoxContainerState(el,p) {
            out[String(i)]=["state":"unsupported","options":[],"selection_supported":false,
                "note":"ComboBox 목록 상태를 읽을 수 없어 열기·선택 입력을 보내지 않습니다"]
            continue
        }
        guard let pending, CFEqual(el, pending.control) else {
            if role=="AXComboBox",let list=comboBoxOwnedList(el,p),comboBoxListVisibility(list,p) != false {
                out[String(i)]=["state":"unsupported","options":menuOptions(list) ?? [],
                    "selection_supported":false,"note":"열려 있는 ComboBox 목록을 안전하게 선택할 수 없습니다"]
                continue
            }
            if let spent, spent.pid == p, CFEqual(el, spent.control) {
                // Its menu is still up, and this helper will not take it down
                // — dismissing is what leaves the control unable to open
                // again. "closed" would be false, and offering `open_select`
                // would offer something that cannot work while a menu is
                // already posted. So: neither.
                out[String(i)] = ["state": "unsupported", "options": [],
                                  "note": "이 컨트롤의 메뉴가 아직 열려 있어 "
                                        + "자동으로 닫지 않았습니다 — 이전 실앱 "
                                        + "검사에서 자동 AXCancel 뒤 재열림 실패가 "
                                        + "있었습니다."]
                continue
            }
            out[String(i)] = ["state": "closed", "options": []]
            continue
        }
        guard let options = menuOptions(pending.menu) else {
            // More items than we will vouch for. "unsupported" offers no
            // operation at all, which is the honest answer — better than a
            // list we cannot re-check.
            out[String(i)] = ["state": "unsupported", "options": []]
            continue
        }
        if role=="AXComboBox",pending.popupWindowID==nil {
            out[String(i)]=["state":"unsupported","options":options,"selection_supported":false,
                "note":"popup_id_unavailable — 열 때 물리 창 ID를 고정하지 못해 선택하지 않습니다"]
            continue
        }
        pending.index = i
        pending.options = options
        pending.optionsObsID = obsID
        var shownOptions=options
        if role=="AXComboBox",let id=pending.popupWindowID {
            guard let visible=comboBoxVisibleOptions(pending.menu,p,id) else {
                out[String(i)]=["state":"unsupported","options":options,"selection_supported":false,
                    "note":"ComboBox 목록의 보이는 범위를 확인하지 못했습니다"]
                continue
            }
            shownOptions=visible
        }
        out[String(i)] = ["state": "open", "options": shownOptions,
                         "selection_supported":true]
    }
    return out
}

// MARK: - Menu bar
//
// A large share of everyday commands (save, copy, open a folder) exist only in
// the menu bar, so a helper that can press buttons but not menu items cannot run
// the tasks people actually ask for. Menus are addressed by their user-visible
// title path rather than walked into the observation: an app's full menu bar
// runs to thousands of items, and the title is the same word the person says.

/// Children of a menu node, stepping through the AXMenu wrapper that nests
/// under every menu bar item and submenu.
@Sendable func menuChildren(_ el: AXUIElement, read: (AXUIElement, String) -> Any? = attr) -> [AXUIElement] {
    let kids = (read(el, kAXChildrenAttribute as String) as? [AXUIElement]) ?? []
    if let menu = kids.first(where: { (read($0, kAXRoleAttribute as String) as? String) == "AXMenu" }) {
        return (read(menu, kAXChildrenAttribute as String) as? [AXUIElement]) ?? []
    }
    return kids
}

/// Resolve a menu command by title path. Each element is a list of acceptable
/// titles, so one recipe survives a localized menu ("Format" / "서식").
@Sendable func menuItem(pid p: pid_t, path: [[String]], read: (AXUIElement, String) -> Any? = attr) -> (AXUIElement, [String])? {
    let app = AXUIElementCreateApplication(p)
    guard let rawBar = read(app, "AXMenuBar"),
          CFGetTypeID(rawBar as CFTypeRef) == AXUIElementGetTypeID() else { return nil }
    var node = rawBar as! AXUIElement // Type checked above, including injected readers.
    var walked: [String] = []
    for wants in path {
        var matches: [(AXUIElement, String)] = []
        for child in menuChildren(node, read: read) {
            if let title = read(child, kAXTitleAttribute as String) as? String,
               wants.contains(title) {
                matches.append((child, title))
                if matches.count > 1 { return nil }
            }
        }
        guard let hit = matches.first else { return nil }
        walked.append(hit.1)
        node = hit.0
    }
    return (node, walked)
}

/// Bring an app forward and wait until it really is forward. An app validates
/// its menus only while active, so a background TextEdit reports Edit > Select
/// All as disabled even though nothing is wrong with the command.
///
/// Is this app the one the person is working in? A question, not a request.
///
/// What used to live here raised the app: an `AXFrontmost` write plus
/// `NSRunningApplication.activate()`. Both are gone, and the reason is the
/// whole point of the change — every caller took the person's focus as a side
/// effect of an action they asked for somewhere else, and a switch to turn
/// that off would still leave the path sitting there for the next caller.
///
/// So there is no raise-the-app path in this helper any more. Callers that
/// genuinely need the app in front say so in their refusal, and the layer that
/// can ask a person decides what to do about it.
/// The scroll bar that governs this element, looking outward.
///
/// The observed element is usually the content rather than the scroller, so
/// this walks up to the enclosing scroll area instead of expecting the caller
/// to have named one.
@Sendable func scrollBar(near element: AXUIElement, vertical: Bool, expectedPID:pid_t) -> AXUIElement? {
    guard expectedPID>0 else {return nil}
    func owned(_ node:AXUIElement)->Bool {
        var pid:pid_t=0
        return AXUIElementGetPid(node,&pid) == .success && pid==expectedPID
    }
    var node: AXUIElement? = element
    var seen:[AXUIElement]=[]
    while let here=node,seen.count<12 {
        guard owned(here),!seen.contains(where:{CFEqual($0,here)}),
              let role=attr(here,kAXRoleAttribute as String) as? String,!role.isEmpty else {return nil}
        seen.append(here)
        if role == "AXScrollArea" {
            guard let kids=comboBoxChildren(here,40) else {return nil}
            var matches:[AXUIElement]=[]
            for child in kids {
                guard let childRole=attr(child,kAXRoleAttribute as String) as? String else {return nil}
                guard childRole == "AXScrollBar" else {continue}
                guard owned(child),let raw=attr(child,kAXParentAttribute as String),
                      CFGetTypeID(raw)==AXUIElementGetTypeID(),CFEqual(raw as! AXUIElement,here),
                      let orientation=attr(child,kAXOrientationAttribute as String) as? String,
                      ["AXVerticalOrientation","AXHorizontalOrientation"].contains(orientation) else {return nil}
                let want = vertical ? "AXVerticalOrientation" : "AXHorizontalOrientation"
                if orientation==want {matches.append(child)}
            }
            return matches.count==1 ? matches[0]:nil
        }
        guard let raw=attr(here,kAXParentAttribute as String),CFGetTypeID(raw)==AXUIElementGetTypeID() else {return nil}
        node=raw as! AXUIElement
    }
    return nil
}

@Sendable func isFrontmost(pid p: pid_t) -> Bool {
    // NOT `AXFrontmost`. That asks the app about itself, and a background
    // TextEdit was measured answering "yes" for six hours while the person
    // worked elsewhere (2026-09-20); two apps claimed it at the same moment.
    // The window server owns this fact.
    p > 0 && nativeFrontmostProcessPID() == p
}

/// An app's everyday name is not unique: "Messages" also names
/// com.apple.messages.AssistantExtension, a background agent with no windows
/// and no menu bar. A bundle id is an exact answer; a name means the app a
/// person can see, so regular apps beat agents and extensions.
func pid(forBundle bundle: String) -> pid_t? {
    let want = bundle.lowercased()
    // Resolve from one fresh workspace snapshot. Exact bundle matches take
    // precedence, so unrelated localized names need not be read on that path.
    // No cached PID or alternative registry ordering is used.
    let apps = NSWorkspace.shared.runningApplications
    let byBundle = apps.filter { $0.bundleIdentifier?.lowercased() == want }
    if !byBundle.isEmpty {
        let regular = byBundle.filter { $0.activationPolicy == .regular }
        return (regular.first ?? byBundle.first)?.processIdentifier
    }
    // A name never means a background agent; a bundle id is explicit and may.
    return apps.first { $0.localizedName?.lowercased() == want
        && $0.activationPolicy == .regular }?.processIdentifier
}

func observe(_ target: String, requireWindow: Bool = true, documentToken: String? = nil, windowIdentity: Bool = false, reveal: [String]? = nil) -> [String: Any] {
    guard let p = pid(forBundle: target) else {
        let gui = NSWorkspace.shared.runningApplications.filter { $0.activationPolicy == .regular }
        if gui.isEmpty {
            return ["type": "error", "error": "gui_session_unavailable", "target": target]
        }
        return ["type": "error", "error": "target_not_found", "target": target]
    }
    let processStart = nativeProcessStartIdentity(p)
    let app = AXUIElementCreateApplication(p)
    // The same list `capture` and point-addressing use, and for the same
    // reason: macOS's screen-capture badge is a window in the app's own
    // AXWindows, so while the control indicator was up every observation of the
    // app being driven began with a phantom `Window  Title: Window, Identifier:
    // _NS:8` — this helper's own way of saying "Tacto is working here" walked
    // into the tree it was working on, and shifted every element index behind
    // it.
    let windows: [AXUIElement]
    var windowListing = WindowListingDiagnostic()
    func milliseconds(since start: UInt64) -> Int {
        Int((DispatchTime.now().uptimeNanoseconds &- start) / 1_000_000)
    }
    let listingStart = DispatchTime.now().uptimeNanoseconds
    do {
        windows = try documentToken.map { [try documentWindowForToken(target, $0)] }
            ?? realWindows(of: app, diagnostic: &windowListing)
    } catch {
        return ["type":"error", "error":"document_snapshot_unavailable", "effect":"not_started"]
    }
    if documentToken == nil && !["ok", "no_value"].contains(windowListing.status) {
        var reply: [String: Any] = ["type":"error","error":"window_list_unavailable","target":target,
                "detail":"앱의 창 목록을 읽지 못했습니다. 창이 없다고 판단하지 않고 중단합니다.",
                "window_listing_diagnostic":windowListing.metadata]
        // The window server can say whether the app really has windows on
        // screen. When it does and accessibility lists none, the app itself
        // has stopped answering accessibility (a long-running Safari,
        // 2026-10-23: System Events failed the same way; restarting Safari
        // fixed it). Say so, instead of leaving an unknown cause.
        let onScreen = onScreenWindowCount(p)
        reply["onscreen_window_count"] = onScreen
        if let onScreen, onScreen > 0 {
            reply["reason"] = "app_accessibility_unresponsive"
            reply["hint"] = "화면에는 이 앱의 창이 \(onScreen)개 있지만 앱이 손쉬운 사용에 창 목록을 내주지 않습니다. 저장하지 않은 작업이 없는지 사용자에게 확인한 뒤 앱을 다시 시작하면 대개 해결됩니다."
        }
        return reply
    }
    if windows.isEmpty && requireWindow {
        return ["type": "error", "error": "no_window_content", "target": target,
                "window_listing_diagnostic":windowListing.metadata]
    }
    // Reading another app's contents is not neutral: an accessibility tree can
    // carry messages, names and account numbers. Show the outline for reads too,
    // so nothing is taken off the screen invisibly (advisor finding: observe
    // was silent, leaving confidentiality unprotected while integrity was not).
    if abortState.isStopped {
        return ["type": "error", "error": "aborted_by_user"]
    }
    let listingMs = milliseconds(since: listingStart)
    let indicatorStart = DispatchTime.now().uptimeNanoseconds
    guard ensureVisibleControl(forPID: p, windowless: true) else {
        return indicatorRefusal()
    }
    let indicatorMs = milliseconds(since: indicatorStart)
    let walkStart = DispatchTime.now().uptimeNanoseconds
    var lines: [String] = []
    var elements: [AXUIElement] = []
    var visited = 0
    let metrics = ObservationReadMetrics()
    let revealScan = reveal.map { ObservationRevealScan($0) }
    var truncated = windows.count > maxTreeNodes
    if truncated { revealScan?.complete = false }
    // A large first window must not hide every later window, including a
    // file chooser. Divide the remaining budget among remaining windows so
    // later roots AND controls can be read. Resume suspended frontiers with
    // leftover budget; never reread a visited node. Window blocks stay ordered,
    // while their observation-local indices may be non-monotonic after resuming.
    let windowCount = min(windows.count, maxTreeNodes)
    var blocks: [[String]] = []
    var frontiers: [[(AXUIElement, Int)]] = []
    var partial: [Bool] = []
    for (position, w) in windows.prefix(windowCount).enumerated() {
        var block: [String] = []
        var pending: [(AXUIElement, Int)] = []
        var windowTruncated = false
        let windowBudget = (maxTreeNodes - visited) / (windowCount - position)
        walk(w, depth: 0, out: &block, elements: &elements,
             visited: &visited, truncated: &windowTruncated,
             nodeLimit: visited + windowBudget, deferred: { pending.append(($0, $1)) },metrics:metrics,reveal:revealScan)
        blocks.append(block); frontiers.append(pending); partial.append(windowTruncated)
    }
    for position in 0..<windowCount where visited < maxTreeNodes {
        let pending = frontiers[position]
        frontiers[position] = []
        var block = blocks[position]
        var unfinished: [(AXUIElement, Int)] = []
        var windowTruncated = partial[position]
        for (node, depth) in pending {
            walk(node, depth: depth, out: &block, elements: &elements,
                 visited: &visited, truncated: &windowTruncated,
                 deferred: { unfinished.append(($0, $1)) },metrics:metrics,reveal:revealScan)
        }
        blocks[position] = block; frontiers[position] = unfinished; partial[position] = windowTruncated
    }
    for (position, w) in windows.prefix(windowCount).enumerated() {
        var block = blocks[position]
        // Mark the frontmost window, exactly as the Python driver does. The two
        // must produce the same tree for the same app, or a recipe that scopes
        // to the front window silently matches nothing under one of them.
        if !block.isEmpty,
           (attr(w, "AXMain") as? Bool) == true || (attr(w, "AXFocused") as? Bool) == true {
            block[0] += " [focused]"
        }
        if partial[position] || !frontiers[position].isEmpty {
            truncated = true
            block.append("  … (창별 관측이 잘렸습니다)")
        }
        lines.append(contentsOf: block)
    }
    let obsID = "s" + UUID().uuidString.prefix(8)
    if let documentToken {
        do {
            guard CFEqual(try documentWindowForToken(target, documentToken), windows[0]) else {
                throw DocumentError.fail("selected document window changed")
            }
        } catch {
            return ["type":"error", "error":"document_snapshot_unavailable", "effect":"not_started"]
        }
    }
    latestObsID = obsID
    latestElements = elements
    latestTargetPID = p
    latestProcessStart = processStart
    latestTargetName = target
    latestDocumentToken = documentToken
    if truncated { lines.append(truncationMark) }
    var reply: [String: Any] = ["type": "observation", "obs_id": obsID, "target": target,
            "tree": windows.isEmpty ? "(창 없음 — 메뉴만 사용 가능)"
                                    : lines.joined(separator: "\n"),
            "element_count": elements.count, "truncated": truncated,
            "observation_metrics":metrics.metadata]
    let walkMs = milliseconds(since: walkStart)
    // Reading only; nothing here opens a menu. A closed popup reports
    // no options because it HAS none until it is opened.
    let selectsStart = DispatchTime.now().uptimeNanoseconds
    let selectLines = lines.joined(separator: "\n").split(separator: "\n").compactMap { line -> Int? in
        let words = line.split(separator: " ", maxSplits: 2)
        guard words.count >= 2, ["PopUpButton", "ComboBox"].contains(String(words[1])) else { return nil }
        return Int(words[0])
    }
    reply["custom_selects"] = customSelects(p, elements, obsID: obsID, candidates: selectLines)
    reply["observation_timing_ms"] = ["window_listing":listingMs, "indicator":indicatorMs, "walk":walkMs,
        "packets":Int(metrics.packetNanos / 1_000_000),
        "secondary_actions":Int(metrics.secondaryActionNanos / 1_000_000),
        "custom_selects":milliseconds(since: selectsStart)]
    if let revealScan {
        reply["reveal_scan_complete"] = revealScan.complete
        reply["reveal_scanned_nodes"] = revealScan.scannedNodes
        reply["revealed"] = revealScan.revealed
    }
    if let documentToken { reply["document_token"] = documentToken }
    else { reply["window_listing_diagnostic"] = windowListing.metadata }
    if windowIdentity {
        guard let processStart else {
            return ["type":"error", "error":"window_identity_unavailable", "effect":"not_started"]
        }
        reply["window_identity"] = ["pid":Int(p), "process_start":processStart,
            "windows":windows.compactMap { window -> [String:Int]? in
                guard let index = elements.firstIndex(where: { CFEqual($0, window) }),
                      let id = nativeWindowID(window) else { return nil }
                return ["element_index":index, "window_id":Int(id)]
            }]
    }
    return reply
}

// MARK: - Acting
//
// Input is posted to the TARGET pid, never the global tap: a background target
// would otherwise receive keystrokes into whatever app currently has focus.

// MARK: - What the caller believed it was acting on
//
// Python sends `expect` with every action and the launcher lets it through,
// and this helper used to ignore it entirely — so "stop if the selection moved"
// and "stop if a dialog appeared" held against the in-process driver and not
// against the signed path that is the recommended one. Same for `probe` and
// `selection`, which arrived here as `unknown_message`. The checks live where
// the grant lives, or they are checks about a different program (peer review,
// 2026-09-19).

/// Every AXMenu currently in this app's hierarchy.
///
/// A popup's menu does NOT appear under the button — measured: pressing it
/// leaves `AXChildren` empty and the menu turns up elsewhere in the app,
/// alongside the twenty-odd the menu bar already owns. So the way to find the
/// one that just opened is to know which were already there.
func appMenus(_ p: pid_t, depth: Int = 7) -> [AXUIElement] {
    var found: [AXUIElement] = []
    func walk(_ node: AXUIElement, _ d: Int) {
        if d > depth { return }
        if str(node, kAXRoleAttribute as String) == "AXMenu" { found.append(node) }
        for child in ((attr(node, kAXChildrenAttribute as String) as? [AXUIElement]) ?? []).prefix(60) {
            walk(child, d + 1)
        }
    }
    walk(AXUIElementCreateApplication(p), 0)
    return found
}

/// Re-read the original control by native identity; only a complete unique
/// identifier match may replace a vanished control. Role alone is not identity.
func rereadSelection(_ p: pid_t, role: String, identity: String?,
                     control: AXUIElement? = nil, scope:NativeWindowReadScope? = nil) -> String? {
    var sameID: [AXUIElement] = []
    var alive: AXUIElement? = nil
    var truncated = false
    var visited = 0
    func read(_ node: AXUIElement, _ key: String, optional: Bool = false) -> CFTypeRef? {
        var value: CFTypeRef?
        let error: AXError
        if let scope {
            guard let reply=try? scope.read(node,key) else {truncated=true;return nil}
            error=reply.0;value=reply.1
        } else {error=AXUIElementCopyAttributeValue(node,key as CFString,&value)}
        if error.rawValue == 0, let value = value {
            if key != kAXChildrenAttribute as String && !(value is String) {
                truncated = true
                return nil
            }
            return value
        }
        if optional && [-25205, -25212].contains(error.rawValue) { return nil }
        truncated = true
        return nil
    }
    func walk(_ node: AXUIElement, _ d: Int) {
        if alive != nil { return }
        if d > 12 || visited >= 3000 { truncated = true; return }
        visited += 1
        if (read(node, kAXRoleAttribute as String) as? String) == role {
            if let original = control, CFEqual(node, original) { alive = node }
            if let want = identity, !want.isEmpty, (read(node, "AXIdentifier", optional: true) as? String) == want {
                sameID.append(node)
            }
        }
        guard let value = read(node, kAXChildrenAttribute as String, optional: true) else { return }
        guard let children = value as? [AXUIElement] else { truncated = true; return }
        if children.count > 60 { truncated = true }
        for child in children.prefix(60) { walk(child, d + 1) }
    }
    walk(AXUIElementCreateApplication(p), 0)
    if let original = alive { return read(original, kAXValueAttribute as String) as? String }
    guard identity?.isEmpty == false, !truncated, sameID.count == 1 else { return nil }
    return read(sameID[0], kAXValueAttribute as String) as? String
}

/// First selection lookup has no input effects. Keep full live validation on
/// every attribute RPC; this phase does not borrow a routing/binding cache.
func selectionLookupAttribute(_ element:AXUIElement,_ name:String,
    scope:NativeWindowReadScope) throws -> CFTypeRef? {
    let (error,value)=try scope.read(element,name)
    if error == .success {return value}
    if error == .attributeUnsupported || error == .noValue {return nil}
    throw NativeWindowInputError.refused("selection metadata unavailable")
}

func selectionLookupString(_ element:AXUIElement,_ name:String,
    scope:NativeWindowReadScope) throws -> String? {
    guard let value=try selectionLookupAttribute(element,name,scope:scope) else {return nil}
    if let text=value as? String {return text.isEmpty ? nil:text}
    if let number=value as? NSNumber {return number.stringValue}
    return nil
}

/// Positive ownership needs a current path from this application. Incomplete
/// traversal never establishes absence and never releases an unknown pin.
func selectionOpeningMenus(_ pid:pid_t,scope:NativeWindowReadScope) throws -> [AXUIElement] {
    var queue:[(AXUIElement,Int)]=[(AXUIElementCreateApplication(pid),0)]
    var seen:[AXUIElement]=[],found:[AXUIElement]=[],cursor=0
    while cursor<queue.count {
        let (node,depth)=queue[cursor];cursor+=1
        if seen.contains(where:{CFEqual($0,node)}) {continue}
        guard seen.count<5000 else {throw NativeWindowInputError.refused("selection menu traversal incomplete")}
        seen.append(node)
        guard let rawRole=try selectionLookupAttribute(node,kAXRoleAttribute as String,scope:scope),
              CFGetTypeID(rawRole)==CFStringGetTypeID(),let role=rawRole as? String,!role.isEmpty else {
            throw NativeWindowInputError.refused("selection menu role unavailable")
        }
        if role=="AXMenu" {found.append(node)}
        let raw=try selectionLookupAttribute(node,kAXChildrenAttribute as String,scope:scope)
        guard raw == nil || CFGetTypeID(raw!)==CFArrayGetTypeID(),
              let values=raw == nil ? []:raw as? [AnyObject],
              values.allSatisfy({CFGetTypeID($0)==AXUIElementGetTypeID()}) else {
            throw NativeWindowInputError.refused("selection menu children unavailable")
        }
        guard values.count<=60,depth<7 || values.isEmpty else {
            throw NativeWindowInputError.refused("selection menu traversal incomplete")
        }
        queue += values.map {($0 as! AXUIElement,depth+1)}
    }
    try scope.ready()
    return found
}

func selectionLookupMenuIsOwned(_ pid:pid_t,_ menu:AXUIElement,
    scope:NativeWindowReadScope) throws -> Bool {
    var queue:[(AXUIElement,Int)]=[(AXUIElementCreateApplication(pid),0)]
    var cursor=0
    while cursor<queue.count {
        let (node,depth)=queue[cursor];cursor+=1
        guard let role=try selectionLookupString(node,kAXRoleAttribute as String,scope:scope) else {
            throw NativeWindowInputError.refused("selection menu role unavailable")
        }
        if CFEqual(node,menu) {return role == "AXMenu"}
        let raw=try selectionLookupAttribute(node,kAXChildrenAttribute as String,scope:scope)
        guard raw == nil || CFGetTypeID(raw!) == CFArrayGetTypeID(),
              let values=raw == nil ? []:raw as? [AnyObject],
              values.allSatisfy({CFGetTypeID($0)==AXUIElementGetTypeID()}) else {
            throw NativeWindowInputError.refused("selection menu children unavailable")
        }
        let children=values.map {$0 as! AXUIElement}
        guard children.count<=60,depth<7 || children.isEmpty else {
            throw NativeWindowInputError.refused("selection menu traversal incomplete")
        }
        queue += children.map {($0,depth+1)}
    }
    try scope.ready()
    return false
}

func menuIsOurs(_ pid:pid_t,_ pending:PendingSelect,scope:NativeWindowReadScope) throws -> Bool {
    if try pending.popupWindowID != nil ||
        selectionLookupString(pending.menu,kAXRoleAttribute as String,scope:scope) == "AXList" {
        switch comboBoxContainerState(pending.control,pid,scope:scope) {
        case .open(let current):return CFEqual(current,pending.menu)
        case .closed:
            if let id=pending.popupWindowID {return try nativePopupVisibility(id,pid,scope:scope)}
            return false
        case .unknown:throw NativeWindowInputError.refused("selection container state unavailable")
        }
    }
    return try selectionLookupMenuIsOwned(pid,pending.menu,scope:scope)
}

/// Same unquoted label as the pinned observation, with scoped RPCs throughout.
func selectionLookupLabel(_ element:AXUIElement,scope:NativeWindowReadScope) throws -> String {
    var parts:[String]=[]
    if let fields=try nativeReadinessBatch(element,names:labelAttrs,scope:scope) {
        for (i,field) in fields.enumerated() {
            guard field.0 == .success || field.0 == .attributeUnsupported || field.0 == .noValue else {
                throw NativeWindowInputError.refused("selection label field unavailable")
            }
            guard let value=field.1 else {continue}
            if labelNames[i] == "Subrole" {
                if !parts.isEmpty {continue}
                if try selectionLookupString(element,kAXRoleAttribute as String,scope:scope) != "AXButton" {continue}
            }
            if let text=value as? String,!text.isEmpty {parts.append(labelNames[i]+": "+oneLineLabel(text))}
            else if let number=value as? NSNumber,CFGetTypeID(number) != CFBooleanGetTypeID() {
                parts.append(labelNames[i]+": "+oneLineLabel(number.stringValue))
            }
        }
    } else {
        for (i,key) in labelAttrs.enumerated() {
            if labelNames[i] == "Subrole" {
                if !parts.isEmpty {continue}
                if try selectionLookupString(element,kAXRoleAttribute as String,scope:scope) != "AXButton" {continue}
            }
            if let text=try selectionLookupString(element,key,scope:scope) {
                parts.append(labelNames[i]+": "+oneLineLabel(text))
            }
        }
    }
    try scope.ready()
    return parts.joined(separator:", ")
}

func initialSelectionReadScope(_ obsID:String,_ target:String,_ control:AXUIElement,
    _ ownerPID:pid_t,deadline:Double) throws -> NativeWindowReadScope {
    let elements=latestElements,generation=latestElementGeneration,observedName=latestTargetName
    guard let start=latestProcessStart,latestDocumentToken == nil,
          elements.filter({CFEqual($0,control)}).count==1 else {
        throw NativeWindowInputError.refused("selection observation identity unavailable")
    }
    let scope=NativeWindowReadScope(deadline:deadline,check:{
        guard !abortState.isStopped else {throw NativeWindowInputError.refused("user stopped input")}
        guard obsID==latestObsID,ownerPID==latestTargetPID,
              observedName.lowercased()==latestTargetName.lowercased(),
              generation==latestElementGeneration,elements.count==latestElements.count,
              latestDocumentToken == nil,nativeProcessStartIdentity(ownerPID)==start,
              pid(forBundle:target)==ownerPID else {
            throw NativeWindowInputError.refused("selection observation or process changed")
        }
        try validateDocumentObservation(target)
    })
    try scope.ready()
    return scope
}

/// Read-only receiving proof, within both the observation and 400ms transition
/// budgets. A late positive read is not confirmation and never triggers input.
func confirmComboBoxSelectionTransition(_ control:AXUIElement,_ menu:AXUIElement,_ pid:pid_t,
    role:String,identity:String?,want:String,scope:NativeWindowReadScope,
    checkpoint:() throws -> Void) throws {
    let until=min(scope.effectiveReadDeadline,ProcessInfo.processInfo.systemUptime+0.4)
    try scope.withReadDeadline(until) {
        while ProcessInfo.processInfo.systemUptime<until {
            try checkpoint()
            if comboBoxListVisibility(menu,pid,scope:scope)==false,
               rereadSelection(pid,role:role,identity:identity,control:control,scope:scope)==want {
                try checkpoint()
                return
            }
            let remaining=until-ProcessInfo.processInfo.systemUptime
            if remaining>0 {usleep(useconds_t(min(0.02,remaining)*1_000_000))}
        }
        throw NativeWindowInputError.refused("ComboBox selection transition unconfirmed")
    }
}

/// Clicking an item of a list that keeps its own selection means selecting
/// it, and the list can be told so directly: no pointer, no geometry. Finder's
/// column view needed this (2026-10-23): the file's column lay outside the
/// narrow window, so a pointer click was refused, and after scrolling the
/// background click did not select. The item is the element or one of its
/// two nearest ancestors that the list holds. Finder answers the set with an
/// error even when it took effect, so the list is read back and only that
/// decides. nil when this route does not apply, so the pointer route runs.
func selectListItem(_ el: AXUIElement, deadline: Double) -> [String: Any]? {
    var item = el
    for _ in 0..<3 {
        guard let parent = attr(item, kAXParentAttribute as String) else { return nil }
        let list = parent as! AXUIElement
        if str(list, kAXRoleAttribute as String) == kAXListRole as String {
            var settable: DarwinBoolean = false
            guard AXUIElementIsAttributeSettable(list, kAXSelectedChildrenAttribute as CFString, &settable) == .success,
                  settable.boolValue else { return nil }
            guard !abortState.isStopped, ProcessInfo.processInfo.systemUptime < deadline else {
                return ["type": "error", "error": "input_cancelled_or_expired", "effect": "not_started"]
            }
            let error = AXUIElementSetAttributeValue(list, kAXSelectedChildrenAttribute as CFString, [item] as CFArray)
            Thread.sleep(forTimeInterval: 0.1)
            let now = (attr(list, kAXSelectedChildrenAttribute as String) as? [AXUIElement]) ?? []
            guard now.count == 1, CFEqual(now[0], item) else {
                return ["type": "error", "error": "action_failed", "effect": "unknown",
                        "input_route": "ax_list_selection", "detail": "list selection did not read back (\(error.rawValue))"]
            }
            return ["type": "acted", "input_route": "ax_list_selection", "selection_read_back": true,
                    "note": "selected through the list; observe again"]
        }
        item = list
    }
    return nil
}

/// The table counterpart of `selectListItem`: the row (the element or one of
/// its two nearest ancestors) of an outline or table whose selected rows can
/// be set, judged only by reading the selection back. Finder selects rows
/// directly, without spending the input budget on a pointer readiness walk.
/// Other apps use this only after the pointer refused without sending input.
func selectTableRow(_ el: AXUIElement, deadline: Double) -> [String: Any]? {
    var row = el
    for _ in 0..<3 {
        guard let parent = attr(row, kAXParentAttribute as String) else { return nil }
        let table = parent as! AXUIElement
        if str(row, kAXRoleAttribute as String) == kAXRowRole as String,
           [kAXOutlineRole as String, kAXTableRole as String].contains(str(table, kAXRoleAttribute as String) ?? "") {
            var settable: DarwinBoolean = false
            guard AXUIElementIsAttributeSettable(table, kAXSelectedRowsAttribute as CFString, &settable) == .success,
                  settable.boolValue else { return nil }
            guard !abortState.isStopped, ProcessInfo.processInfo.systemUptime < deadline else {
                return ["type": "error", "error": "input_cancelled_or_expired", "effect": "not_started"]
            }
            let error = AXUIElementSetAttributeValue(table, kAXSelectedRowsAttribute as CFString, [row] as CFArray)
            Thread.sleep(forTimeInterval: 0.1)
            let now = (attr(table, kAXSelectedRowsAttribute as String) as? [AXUIElement]) ?? []
            guard now.count == 1, CFEqual(now[0], row) else {
                return ["type": "error", "error": "action_failed", "effect": "unknown",
                        "input_route": "ax_row_selection", "detail": "row selection did not read back (\(error.rawValue))"]
            }
            return ["type": "acted", "input_route": "ax_row_selection", "selection_read_back": true,
                    "note": "selected through the table; observe again"]
        }
        row = table
    }
    return nil
}

/// Normal-layer windows the window server shows on screen for this process,
/// or nil when the list cannot be read. Owner and layer need no screen
/// recording grant; titles are not read.
func onScreenWindowCount(_ pid: pid_t) -> Int? {
    guard let entries = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID)
            as? [[String: Any]] else { return nil }
    return entries.filter { ($0[kCGWindowOwnerPID as String] as? Int).map { pid_t($0) } == pid
                            && ($0[kCGWindowLayer as String] as? Int) == 0 }.count
}

/// Everything this app currently has selected and focused, as one short
/// string. Two or three attribute reads on the front window — deliberately not
/// a tree walk, since the point is that confirming a target costs about a
/// millisecond where re-observing Mail costs seconds.
func selectionDigest(_ p: pid_t) -> String {
    let app = AXUIElementCreateApplication(p)
    var parts: [String] = []
    if let focused = attr(app, kAXFocusedUIElementAttribute as String) {
        parts.append("focus:" + String(label(focused as! AXUIElement).prefix(80)))
    }
    if let win = attr(app, kAXFocusedWindowAttribute as String) {
        let w = win as! AXUIElement
        for key in ["AXSelectedRows", "AXSelectedChildren", "AXSelectedTextRange"] {
            guard let v = attr(w, key) else { continue }
            if let rows = v as? [AXUIElement] {
                parts.append("\(key):\(rows.count):" + rows.prefix(8)
                    .map { String(label($0).prefix(40)) }.joined(separator: "|"))
            } else {
                parts.append("\(key):\(v)")
            }
        }
    }
    return sha1Hex(parts.joined(separator: "\n")).prefix(12).description
}

/// Is a sheet or modal dialog waiting for an answer in this app?
enum NativeModalPresence: String { case clear, present, unobservable }

func nativeObservedModalPresence(_ app: AXUIElement, maxNodes: Int = 20000,
                                maxDepth: Int = 64,
                                read: (AXUIElement, String) throws -> (AXError, CFTypeRef?)) -> NativeModalPresence {
    guard maxNodes > 0, maxDepth >= 0 else { return .unobservable }
    func nodes(_ value: CFTypeRef?) -> [AXUIElement]? {
        guard let value, CFGetTypeID(value) == CFArrayGetTypeID(),
              CFArrayGetCount((value as! CFArray)) <= maxNodes,
              let values = value as? [AXUIElement],
              values.allSatisfy({ CFGetTypeID($0) == AXUIElementGetTypeID() }) else { return nil }
        return values
    }
    do {
        let (status, raw) = try read(app, "AXWindows")
        guard status == .success, let roots = nodes(raw) else { return .unobservable }
        var remaining = maxNodes
        func inspect(_ node: AXUIElement) throws -> NativeModalPresence {
            guard remaining > 0 else { return .unobservable }
            remaining -= 1
            let (roleStatus, roleValue) = try read(node, "AXRole")
            guard roleStatus == .success, let role = roleValue as? String,
                  !role.isEmpty else { return .unobservable }
            if role == "AXSheet" { return .present }
            let (modalStatus, modal) = try read(node, "AXModal")
            if modalStatus == .success {
                guard let modal, CFGetTypeID(modal) == CFBooleanGetTypeID(),
                      let flag = modal as? Bool else { return .unobservable }
                if flag { return .present }
            } else if modalStatus != .attributeUnsupported && modalStatus != .noValue { return .unobservable }
            let (sheetStatus, rawSheets) = try read(node, "AXSheets")
            if sheetStatus == .success {
                guard let sheets = nodes(rawSheets) else { return .unobservable }
                if !sheets.isEmpty { return .present }
            } else if sheetStatus != .attributeUnsupported && sheetStatus != .noValue { return .unobservable }
            return .clear
        }
        for window in roots {
            let state = try inspect(window)
            if state != .clear { return state }
            let (childStatus, rawChildren) = try read(window, "AXChildren")
            guard childStatus == .success, let children = nodes(rawChildren),
                  children.count <= remaining else { return .unobservable }
            for child in children {
                let state = try inspect(child)
                if state != .clear { return state }
            }
        }
        let (focusStatus, rawFocus) = try read(app, "AXFocusedUIElement")
        if focusStatus == .attributeUnsupported || focusStatus == .noValue { return .clear }
        guard focusStatus == .success, let rawFocus,
              CFGetTypeID(rawFocus) == AXUIElementGetTypeID() else { return .unobservable }
        var focused = rawFocus as! AXUIElement
        for _ in 0...maxDepth {
            if CFEqual(focused, app) { return .clear }
            let state = try inspect(focused)
            if state != .clear { return state }
            let (parentStatus, rawParent) = try read(focused, "AXParent")
            guard parentStatus == .success, let rawParent,
                  CFGetTypeID(rawParent) == AXUIElementGetTypeID() else { return .unobservable }
            focused = rawParent as! AXUIElement
        }
        return .unobservable
    } catch { return .unobservable }
}

func modalSheet(_ p: pid_t) -> Bool {
    let scope = NativeWindowReadScope(deadline: ProcessInfo.processInfo.systemUptime + 2) {
        if abortState.isStopped { throw NativeWindowInputError.refused("modal observation stopped") }
    }
    return nativeObservedModalPresence(AXUIElementCreateApplication(p), read: scope.read) != .clear
}

/// Why this is no longer what the caller meant — or nil if it still is.
func mismatch(_ expect: [String: Any], element: AXUIElement?, pid p: pid_t) -> String? {
    if (expect["no_modal"] as? Bool) == true, modalSheet(p) {
        return "대화상자가 열렸거나 유무를 확인할 수 없습니다"
    }
    if let wantSel = expect["selection"] as? String, wantSel != selectionDigest(p) {
        return "고른 것이 바뀌었습니다 — 같은 버튼이라도 대상이 다릅니다"
    }
    guard let el = element else {
        // No element to compare against. An expectation that names one is not
        // weakly satisfied here, it is unanswerable — and an ignored
        // precondition looks exactly like a met one, which is the failure this
        // contract was built to stop. Refused, not skipped.
        let named = ["role", "label", "attribute", "enabled", "value"]
            .filter { expect[$0] != nil }
        if !named.isEmpty {
            return "이 동작은 대상 요소를 지정하지 않는데 "
                 + "\(named.joined(separator: ", ")) 을(를) 기대했습니다 — "
                 + "확인할 수 없는 조건입니다"
        }
        return nil
    }
    if let expectedValue = expect["value"] as? String,
       (attr(el,kAXValueAttribute as String) as? String) != expectedValue { return "대상 값이 달라졌습니다" }
    if (expect["enabled"] as? Bool) == true,
       (attr(el, kAXEnabledAttribute as String) as? Bool) == false {
        return "대상이 비활성 상태입니다"
    }
    if let wantRole = expect["role"] as? String {
        // The element's OWN role. Looking for the word "Button" inside its
        // label can never match, and the Python side shipped that bug for
        // several commits with every action refused.
        var role = str(el, kAXRoleAttribute as String) ?? ""
        if role.hasPrefix("AX") { role = String(role.dropFirst(2)) }
        if role != wantRole {
            return "대상이 달라졌습니다 (기대 \(wantRole), 지금 \(role.isEmpty ? "?" : role))"
        }
    }
    if expect["attribute"] as? String == "URL", let expectedURL = expect["label"] as? String {
        guard let url = attr(el, "AXURL") as? URL, url.absoluteString == expectedURL else {
            return "Link destination changed or unavailable"
        }
        return nil
    }
    if let wantLabel = expect["label"] as? String {
        // Read the attribute off the element, not out of a rendered line: the
        // caller says WHICH attribute its label came from, so the comparison
        // is that attribute exactly rather than a substring search that would
        // let "Save" match "SaveAs".
        let axByName = ["Title": kAXTitleAttribute as String,
                        "Description": kAXDescriptionAttribute as String,
                        "Value": kAXValueAttribute as String,
                        "Help": kAXHelpAttribute as String,
                        "Identifier": "AXIdentifier",
                        "Placeholder": kAXPlaceholderValueAttribute as String,
                        "Subrole": kAXSubroleAttribute as String]
        let names = (expect["attribute"] as? String).map { [$0] }
            ?? ["Title", "Description", "Value", "Help", "Identifier", "Placeholder"]
        var anyPresent = false
        var matched = false
        for name in names {
            guard let key = axByName[name], let got = str(el, key) else { continue }
            anyPresent = true
            if normalizedLabel(got, attribute: name)
                == normalizedLabel(wantLabel, attribute: name) { matched = true; break }
        }
        if !anyPresent { return "대상이 화면에서 사라졌습니다" }
        if !matched {
            return "대상이 달라졌습니다 (기대 \(wantLabel), 지금 \(label(el).prefix(60)))"
        }
    }
    return nil
}

/// The same normalization the Python side applies, so one expectation means one
/// thing on every transport.
///
/// An AXIdentifier can carry live state after a ';' — a calculator's display is
/// `StandardInputView;value:408`, which changes with every keystroke. Only a
/// suffix whose key is known to be state is dropped; an unrecognised one stays,
/// because `control;operation:delete` and `control;operation:preview` are two
/// different controls.
func normalizedLabel(_ value: String, attribute: String) -> String {
    // Format characters some apps wrap their text in (the LTR mark macOS
    // Calculator puts around digits) are not part of what a thing is called.
    var text = String(value.unicodeScalars.filter {
        !(0x200B...0x200F).contains($0.value) && !(0x202A...0x202E).contains($0.value)
    }).trimmingCharacters(in: .whitespaces)
    guard attribute == "Identifier", let semi = text.firstIndex(of: ";") else { return text }
    let tail = text[text.index(after: semi)...].trimmingCharacters(in: .whitespaces)
        .lowercased()
    for key in ["value:", "state:", "selected:", "index:", "count:"]
    where tail.hasPrefix(key) {
        text = String(text[..<semi]).trimmingCharacters(in: .whitespaces)
        break
    }
    return text
}

func sha1Hex(_ text: String) -> String {
    // A digest, not a secret: it only has to change when the selection does.
    var h: [UInt32] = [0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476, 0xC3D2E1F0]
    var msg = Array(text.utf8)
    let bitLen = UInt64(msg.count) * 8
    msg.append(0x80)
    while msg.count % 64 != 56 { msg.append(0) }
    for i in (0..<8).reversed() { msg.append(UInt8(truncatingIfNeeded: bitLen >> UInt64(i * 8))) }
    for chunk in stride(from: 0, to: msg.count, by: 64) {
        var w = [UInt32](repeating: 0, count: 80)
        for i in 0..<16 {
            let o = chunk + i * 4
            w[i] = (UInt32(msg[o]) << 24) | (UInt32(msg[o+1]) << 16)
                 | (UInt32(msg[o+2]) << 8) | UInt32(msg[o+3])
        }
        for i in 16..<80 {
            let v = w[i-3] ^ w[i-8] ^ w[i-14] ^ w[i-16]
            w[i] = (v << 1) | (v >> 31)
        }
        var (a, b, c, d, e) = (h[0], h[1], h[2], h[3], h[4])
        for i in 0..<80 {
            let (f, k): (UInt32, UInt32)
            switch i {
            case 0..<20:  (f, k) = ((b & c) | (~b & d), 0x5A827999)
            case 20..<40: (f, k) = (b ^ c ^ d, 0x6ED9EBA1)
            case 40..<60: (f, k) = ((b & c) | (b & d) | (c & d), 0x8F1BBCDC)
            default:      (f, k) = (b ^ c ^ d, 0xCA62C1D6)
            }
            let t = ((a << 5) | (a >> 27)) &+ f &+ e &+ k &+ w[i]
            (e, d, c, b, a) = (d, c, (b << 30) | (b >> 2), a, t)
        }
        h[0] = h[0] &+ a; h[1] = h[1] &+ b; h[2] = h[2] &+ c
        h[3] = h[3] &+ d; h[4] = h[4] &+ e
    }
    return h.map { String(format: "%08x", $0) }.joined()
}

/// One element's own attributes, with no tree walk.
func probe(_ obsID: String, _ target: String, _ index: Int) -> [String: Any] {
    guard obsID == latestObsID else {
        return ["type": "error", "error": "stale_observation",
                "effect": "not_started"]
    }
    guard let p = pid(forBundle: target), p == latestTargetPID else {
        return ["type": "error", "error": "target_not_found",
                "effect": "not_started"]
    }
    guard index >= 0, index < latestElements.count else {
        return ["type": "error", "error": "target_missing",
                "effect": "not_started",
                "detail": "element_index out of range"]
    }
    let el = latestElements[index]
    do { try validateDocumentObservation(target) }
    catch { return ["type":"error", "error":"document_snapshot_unavailable", "effect":"not_started"] }
    if let token = latestDocumentToken {
        do { try documentValidateElement(el, window: documentWindowForToken(target, token)) }
        catch { return ["type":"error", "error":"document_snapshot_unavailable", "effect":"not_started"] }
    }
    var role = str(el, kAXRoleAttribute as String) ?? "?"
    if role.hasPrefix("AX") { role = String(role.dropFirst(2)) }
    return ["type": "probe", "line": "  \(index) \(role)  " + label(el)]
}

/// Read-only cold traversal. The response is never an input binding/permit.
func readinessPreflight(_ msg: [String: Any]) -> [String: Any] {
    let began = ProcessInfo.processInfo.systemUptime
    let requestedStrategy = msg["read_strategy"] as? String ?? "batch"
    let readStrategy = ["batch","single"].contains(requestedStrategy) ? requestedStrategy : "invalid"
    var scope: NativeWindowReadScope?
    var titlebarHits: [[String: Any]]?
    func result(_ type: String, _ reason: String) -> [String: Any] {
        var out: [String:Any] = ["type":type,"reason":reason,"read_only":true,"read_strategy":readStrategy,
            "authorizes_input":false,"elapsed_ms":Int(min(600000,max(0,(ProcessInfo.processInfo.systemUptime-began)*1000))),
            "scoped_ax_operations":scope?.operationCount ?? 0,
            "ax_operation_elapsed_ms":scope?.operationElapsedMS ?? 0,
            "context_elapsed_ms":scope?.contextElapsedMS ?? 0]
        if let progress = scope?.readinessProgress { out["readiness_progress"] = progress }
        if let titlebarHits { out["titlebar_hits"] = titlebarHits }
        if let scope {
            out["readiness_role_costs"] = scope.readinessRoleCosts
            out["readiness_attribute_costs"] = scope.readinessAttributeCosts
            if let counts=scope.readinessIdentityCounts {out["readiness_identity_counts"]=counts}
            if let error = scope.readinessAXError { out["readiness_ax_error"] = error }
            if let failure = scope.readinessFailure { out["readiness_failure"] = failure }
        }
        return out
    }
    let fields = Set(["type","target","obs_id","element_index"])
    guard fields.isSubset(of: Set(msg.keys)), Set(msg.keys).isSubset(of: fields.union(["read_strategy", "titlebar_probe"])),
          msg["read_strategy"] == nil || ["batch","single"].contains(msg["read_strategy"] as? String ?? ""),
          msg["titlebar_probe"] == nil || CFGetTypeID(msg["titlebar_probe"]! as CFTypeRef) == CFBooleanGetTypeID(),
          msg["type"] as? String == "readiness_preflight",
          let target = msg["target"] as? String,
          ["com.apple.finder","ai.callabo.tacto.modality-fixture"].contains(target),
          let obs = msg["obs_id"] as? String, obs == latestObsID,
          latestTargetName == target, latestDocumentToken == nil,
          let number = documentIsWholeNumber(msg["element_index"]), number.intValue >= 0,
          number.intValue < latestElements.count,
          let pid = pid(forBundle:target), pid == latestTargetPID,
          let start = latestProcessStart, nativeProcessStartIdentity(pid) == start else {
        return result("error","invalid_or_stale_preflight")
    }
    let generation = latestElementGeneration
    let window = latestElements[number.intValue]
    var owner: pid_t = 0
    guard AXUIElementGetPid(window,&owner) == .success, owner == pid else {
        return result("error","preflight_window_owner_unavailable")
    }
    func identity() throws {
        guard !abortState.isStopped, latestObsID == obs, latestTargetPID == pid,
              latestTargetName == target,
              latestElementGeneration == generation, latestDocumentToken == nil,
              nativeProcessStartIdentity(pid) == start else {
            throw NativeWindowInputError.refused("preflight identity or abort")
        }
    }
    let readScope = NativeWindowReadScope(deadline:began+15,incrementalCheck:identity,
        collectReadinessRoleCosts:true,check:{
        try identity()
        guard nativeObservedBundleMatches(pid,target) else {
            throw NativeWindowInputError.refused("preflight bundle changed")
        }
    })
    scope = readScope
    do {
        let (error,role) = try readScope.read(window,kAXRoleAttribute as String)
        guard error == .success, role as? String == "AXWindow" else {
            return result("error","preflight_window_role_unavailable")
        }
        let singleRead: AXRead? = readStrategy == "single" ? { node, name in
            try readScope.read(node, name)
        } : nil
        try nativeRequireWindowReady(window,pid,scope:readScope,read:singleRead,isFinder:target == "com.apple.finder")
        // Diagnostic reads only: no titlebar point is accepted for dispatch here.
        if msg["titlebar_probe"] as? Bool == true {
            titlebarHits = try nativeTitlebarHitSummary(window, pid, scope: readScope)
        }
        try readScope.ready(forceFull:true)
        return result("readiness_preflight","read_complete")
    } catch NativeWindowInputError.refused(let reason) {
        let code: String
        switch reason {
        case "readiness budget exceeded: deadline", "window observation deadline expired": code = "preflight_deadline"
        case "readiness budget exceeded: nodes": code = "preflight_nodes"
        default: code = "preflight_unavailable"
        }
        // Preserve combined native predicates: these codes do not assert that a
        // sheet/modal was positively identified when the guard also rejects unknown.
        let cause: String
        switch reason {
        case "attached sheet or malformed role": cause = "sheet_or_malformed_role"
        case "modal or unknown window": cause = "modal_or_unknown_top"
        case "modal or unknown element": cause = "modal_or_unknown_element"
        case "sheet or malformed sheet list": cause = "sheet_or_malformed_list"
        case "sheet attached during validation": cause = "sheet_attached_recheck"
        case "window became modal during validation": cause = "modal_recheck"
        case "readiness budget exceeded: deadline", "window observation deadline expired": cause = "deadline"
        case "readiness budget exceeded: nodes": cause = "nodes"
        case "preflight identity or abort", "preflight bundle changed": cause = "identity_or_abort"
        case "readiness batch malformed", "window children malformed": cause = "malformed"
        default: cause = "unavailable"
        }
        var out = result("error",code); out["refusal_cause"] = cause; return out
    } catch {
        var out = result("error","preflight_unavailable"); out["refusal_cause"] = "unavailable"; return out
    }
}

/// Diagnostic reads only; no state here may authorize an input or skip a guard.
func readinessReadCost(_ msg: [String: Any]) -> [String: Any] {
    var rows: [[String: Any]] = []
    func refuse(_ reason: String) -> [String: Any] {
        ["type":"error","error":reason,"effect":"not_started","rows":rows]
    }
    guard Set(msg.keys) == Set(["type","target","obs_id","element_indices"]),
          msg["type"] as? String == "readiness_read_cost",
          msg["target"] as? String == "com.apple.finder",
          let obs = msg["obs_id"] as? String, obs == latestObsID,
          latestTargetName == "com.apple.finder", latestDocumentToken == nil,
          let raw = msg["element_indices"] as? [Any], (1...16).contains(raw.count) else {
        return refuse("invalid_readiness_cost_request")
    }
    var indices: [Int] = []
    for value in raw {
        guard let number = documentIsWholeNumber(value), number.intValue >= 0,
              number.intValue < latestElements.count, !indices.contains(number.intValue) else {
            return refuse("invalid_readiness_cost_request")
        }
        indices.append(number.intValue)
    }
    guard let pid = pid(forBundle:"com.apple.finder"), pid == latestTargetPID,
          let start = latestProcessStart, nativeProcessStartIdentity(pid) == start else {
        return refuse("stale_observation")
    }
    let generation = latestElementGeneration
    let scope = NativeWindowReadScope(deadline:ProcessInfo.processInfo.systemUptime+2, check:{
        guard !abortState.isStopped, latestObsID == obs, latestTargetPID == pid,
              latestElementGeneration == generation, nativeProcessStartIdentity(pid) == start,
              nativeObservedBundleMatches(pid,"com.apple.finder") else {
            throw NativeWindowInputError.refused("diagnostic identity or abort")
        }
    })
    let names = [kAXRoleAttribute as String,"AXModal",kAXChildrenAttribute as String]
    func shape(_ values: [(AXError, CFTypeRef?)]) -> [[String: Any]] {
        values.map { error, value in
            var item: [String:Any] = ["ax_error":Int(error.rawValue),"kind":"nil"]
            if let value {
                let type = CFGetTypeID(value)
                if type == CFStringGetTypeID() { item["kind"] = "string" }
                else if type == CFBooleanGetTypeID() { item["kind"] = "boolean" }
                else if type == CFArrayGetTypeID() {
                    item["kind"] = "array"; item["count"] = CFArrayGetCount((value as! CFArray))
                } else { item["kind"] = "other" }
            }
            return item
        }
    }
    do {
        for (ordinal,index) in indices.enumerated() {
            let element = latestElements[index]
            for round in 0..<2 {
                var row: [String:Any] = ["ordinal":ordinal,"round":round,
                    "batch_first":(ordinal+round)%2 == 0]
                defer { rows.append(row) }
                func batch() throws {
                    var seconds = 0.0
                    let result = try nativeReadinessBatch(element,names:names,read:{ node,keys in
                        try scope.withTimeout(node) {
                            let began = ProcessInfo.processInfo.systemUptime
                            defer { seconds += ProcessInfo.processInfo.systemUptime-began }
                            var values: CFArray?
                            let error = AXUIElementCopyMultipleAttributeValues(node,keys as CFArray,
                                AXCopyMultipleAttributeOptions(rawValue:0),&values)
                            return (error,values)
                        }
                    })
                    row["batch_ax_us"] = Int(max(0,min(2000000,seconds*1000000)))
                    row["batch_transport"] = result == nil ? "unsupported":"ok"
                    row["batch_attributes"] = result.map(shape) as Any? ?? NSNull()
                }
                func singles() throws {
                    var seconds = 0.0
                    let values = try names.map { name in
                        try scope.withTimeout(element) {
                            let began = ProcessInfo.processInfo.systemUptime
                            defer { seconds += ProcessInfo.processInfo.systemUptime-began }
                            var value: CFTypeRef?
                            let error = AXUIElementCopyAttributeValue(element,name as CFString,&value)
                            return (error,value)
                        }
                    }
                    row["single_ax_us"] = Int(max(0,min(2000000,seconds*1000000)))
                    row["single_attributes"] = shape(values)
                }
                if (ordinal+round)%2 == 0 { try batch(); try singles() }
                else { try singles(); try batch() }
            }
        }
        try scope.ready()
        return ["type":"readiness_read_cost","rows":rows,"scoped_ax_operations":scope.operationCount,
                "read_only":true,"atomic_snapshot":false]
    } catch { return refuse("readiness_cost_unavailable") }
}

func selectionOf(_ target: String) -> [String: Any] {
    guard let p = pid(forBundle: target) else {
        return ["type": "error", "error": "target_not_found",
                "effect": "not_started"]
    }
    return ["type": "selection", "digest": selectionDigest(p)]
}

private func processForWindowTarget(_ target: String) -> pid_t? { pid(forBundle: target) }

// Runs on the serial authenticated connection worker. All input is PID-addressed.
// The local preparation deadline does not promise cancellation of remote work.
/// Make this window the key one by asking it, not by clicking its title bar.
///
/// What stood here posted a synthetic mouse down/up pair at the window's title
/// bar, addressed to the process — a gesture mimed at an app in the hope it
/// reads it the way a person's click would. It also needed the app in front
/// first, because an inactive app has no key window to give.
///
/// `AXMain` and `AXFocused` are the same request written as a question the
/// window can answer. The frontmost requirement stays: this picks which window
/// of an app the person is already in becomes key, and is not a way to bring an
/// app forward.
/// The app's real windows, in order, with nothing else in the list.
///
/// `AXWindows` is not a list of windows. Measured on Calculator with a tooltip
/// up, index 0 was an `AXHelpTag` 85x20 and the actual window was index 1 —
/// so a `window_index` meant different things depending on whether the mouse
/// happened to be resting on a button. An index into a list that changes shape
/// under you is worse than a wrong index: it is one that is right sometimes.
struct WindowListingDiagnostic {
    var status = "ok"
    var axError = 0
    var rawCount = 0
    var roleWindowCount = 0
    var roleUnavailableCount = 0
    var roleTransientCount = 0
    var filteredCount = 0
    var metadata: [String: Any] {
        ["version":1,"status":status,"ax_error":axError,"raw_count":rawCount,
         "role_window_count":roleWindowCount,"role_unavailable_count":roleUnavailableCount,
         "role_transient_count":roleTransientCount,
         "filtered_count":filteredCount,"badge_count":roleWindowCount-filteredCount]
    }
}

func realWindows(of app: AXUIElement) -> [AXUIElement] {
    var diagnostic = WindowListingDiagnostic()
    return realWindows(of: app, diagnostic: &diagnostic)
}

func realWindows(of app: AXUIElement, diagnostic: inout WindowListingDiagnostic) -> [AXUIElement] {
    diagnostic = WindowListingDiagnostic()
    var raw: CFTypeRef?
    let error = AXUIElementCopyAttributeValue(app, kAXWindowsAttribute as CFString, &raw)
    diagnostic.axError = Int(error.rawValue)
    if error == .noValue { diagnostic.status = "no_value"; return [] }
    guard error == .success else { diagnostic.status = "ax_error"; return [] }
    guard let raw else { diagnostic.status = "no_value"; return [] }
    guard CFGetTypeID(raw) == CFArrayGetTypeID(), let entries = raw as? [AnyObject] else {
        diagnostic.status = "malformed_array"; return []
    }
    diagnostic.rawCount = entries.count
    guard entries.allSatisfy({ CFGetTypeID($0) == AXUIElementGetTypeID() }) else {
        diagnostic.status = "malformed_array"; return []
    }
    let all = entries.map { $0 as! AXUIElement }
    let byRole = all.filter { node in
        var value: CFTypeRef?
        let roleError = AXUIElementCopyAttributeValue(node, kAXRoleAttribute as CFString, &value)
        guard roleError == .success, let role = value as? String, !role.isEmpty else {
            diagnostic.roleUnavailableCount += 1
            if roleError == .cannotComplete || roleError == .invalidUIElement {
                diagnostic.roleTransientCount += 1
            }
            return false
        }
        return role == kAXWindowRole as String
    }
    diagnostic.roleWindowCount = byRole.count
    if diagnostic.roleUnavailableCount > 0 { diagnostic.status = "role_unavailable" }

    func frame(_ element: AXUIElement) -> CGRect? {
        guard let positionValue = attr(element, kAXPositionAttribute as String),
              let sizeValue = attr(element, kAXSizeAttribute as String) else { return nil }
        guard CFGetTypeID(positionValue) == AXValueGetTypeID(),
              CFGetTypeID(sizeValue) == AXValueGetTypeID(),
              AXValueGetType(positionValue as! AXValue) == .cgPoint,
              AXValueGetType(sizeValue as! AXValue) == .cgSize else { return nil }
        var origin = CGPoint.zero, size = CGSize.zero
        guard AXValueGetValue(positionValue as! AXValue, .cgPoint, &origin),
              AXValueGetValue(sizeValue as! AXValue, .cgSize, &size),
              origin.x.isFinite, origin.y.isFinite, size.width.isFinite, size.height.isFinite else { return nil }
        return CGRect(origin: origin, size: size)
    }
    let frames = byRole.map { frame($0) }

    var filteredWindowCount = byRole.count
    defer { diagnostic.filteredCount = filteredWindowCount }
    // Drop attachments. macOS puts its screen-capture badge ON the window it
    // is capturing, and that badge turns up in the app's own AXWindows list:
    // measured at 66x20 inside a 460x232 window, subrole AXDialog, title
    // "Window", identifier "_NS:8", with no close, minimize or zoom button
    // and neither main nor focused (2026-09-21).
    //
    // That is ours. Holding the control-indicator stream is what puts it
    // there, so this helper's own way of SAYING it is working was making the
    // window it works on ambiguous — capture refused, and a point had two
    // windows to be relative to.
    //
    // Geometry alone cannot identify a badge: real file panels can be contained
    // and have no close button. Exclude only the measured badge signature.
    return byRole.enumerated().filter { index, window in
        guard str(window, kAXSubroleAttribute as String) == "AXDialog",
              str(window, kAXTitleAttribute as String) == "Window",
              str(window, kAXIdentifierAttribute as String) == "_NS:8",
              attr(window, kAXCloseButtonAttribute as String) == nil,
              (attr(window, kAXMainAttribute as String) as? Bool) != true,
              (attr(window, kAXFocusedAttribute as String) as? Bool) != true,
              let mine = frames[index], mine.width > 0, mine.width <= 100,
              mine.height > 0, mine.height <= 32 else { return true }
        let inside = frames.enumerated().contains { other, rect in
            other != index && rect.map { $0.contains(mine) && $0 != mine } == true
        }
        if inside { filteredWindowCount -= 1 }
        return !inside
    }.map { $0.element }
}

// MARK: - A point is an element we have not looked up yet
//
// Every action here has needed an `element_index` from an accessibility
// observation, and controls that draw themselves expose no such element — so
// canvases, custom-drawn widgets and anything an app renders without telling
// AX about it were simply unreachable, and got `needs_pointer` and a handoff.
//
// The current point-action path resolves the app's own AX element and asks
// that element to act. This does not reach a truly opaque surface without an
// AX action. Background window preparation has a separate PID-addressed
// event boundary in WindowDispatch.swift. Binary imports or public product
// observations do not establish another product's private input mechanism.

/// The element at a screen point, asked of the APP rather than the screen.
///
/// Asked of the system-wide element this is a z-order question: it answers
/// about whatever window is in front at that coordinate. Measured — a point
/// inside a background fixture's window returned an `AXWebArea` belonging to a
/// browser that happened to be on top (2026-09-21). That is the wrong answer
/// twice over: it is not the app the caller named, and it would make a point
/// mean different things depending on what the person had in front.
///
/// Asked of the application element it is a question about that app's own
/// hierarchy, which is what a window-relative coordinate meant all along, and
/// it answers correctly for a window nobody has brought forward — the property
/// this whole project is built on.
///
/// The pid check stays as a second lock rather than the only one.
func elementAtPoint(_ point: CGPoint, pid p: pid_t) -> AXUIElement? {
    let app = AXUIElementCreateApplication(p)
    var found: AXUIElement?
    guard AXUIElementCopyElementAtPosition(app, Float(point.x), Float(point.y),
                                           &found) == .success,
          let element = found else { return nil }
    var owner: pid_t = 0
    guard AXUIElementGetPid(element, &owner) == .success, owner == p else { return nil }
    return element
}

/// Option metadata can include owned rows beyond the bounded outline. Such a
/// row is addressed only through its unique observed ComboBox and complete live
/// owned-list path, never by adding an unseen node to the observation map.
func nativePopupSelectionAnchorMatches(_ control:AXUIElement,_ element:AXUIElement,
                                       _ popup:AXUIElement,_ pid:pid_t,scope:NativeWindowReadScope?=nil)->Bool {
    guard let list=comboBoxOwnedList(control,pid,scope:scope),
          let raw=comboBoxAttribute(list,kAXParentAttribute as String,scope:scope),
          CFGetTypeID(raw)==AXUIElementGetTypeID(),CFEqual(raw,popup),
          let rows=comboBoxListRows(list,scope:scope) else {return false}
    let matches=rows.filter {CFEqual($0.node,element)}
    return matches.count==1 && !matches[0].disabled
}

func prepareBackgroundWindow(_ obsID: String, _ target: String, _ element: AXUIElement,
                             _ pid: pid_t, deadline: Double, key: String? = nil,
                             expectation: [String: Any]? = nil, point: CGPoint? = nil,
                             drag: NativeWindowDrag? = nil,
                             windowInput: NativeOpaqueWindowInput? = nil,
                             elementClicks: Int = 0, submitKey: Bool = false,
                             popup: AXUIElement? = nil, observedControl:AXUIElement? = nil,
                             selectionCheck: ((NativeWindowReadScope) throws -> Void)? = nil,
                             finalTransition: ((_ checkpoint: () throws -> Void, _ scope:NativeWindowReadScope) throws -> Void)? = nil) -> [String: Any] {
    let diagnosticStart = ProcessInfo.processInfo.systemUptime
    var phase = "identity"
    var identityCause: String?
    func identityRequirement(_ matches: Bool, _ cause: String) -> Bool {
        if !matches { identityCause = cause }
        return matches
    }
    var readScope: NativeWindowReadScope?
    var dispatchHeadroom: [String: Any]?
    func diagnosed(_ result: [String: Any]) -> [String: Any] {
        let detail = result["detail"] as? String ?? ""
        let reason: String
        switch detail {
        case "readiness budget exceeded: deadline": reason = "readiness_deadline"
        case "readiness budget exceeded: nodes": reason = "readiness_nodes"
        case "readiness batch failed": reason = "readiness_batch_failed"
        case "window state unavailable: AXWindows", "window state unavailable: AXMinimized",
             "window state unavailable: AXRole", "window state unavailable: AXModal",
             "window state unavailable: AXChildren", "window state unavailable: AXSheets",
             "window state unavailable: AXParent": reason = "readiness_attribute_failed"
        case "readiness batch malformed", "readiness batch error malformed": reason = "readiness_batch_malformed"
        case "window validation headroom insufficient": reason = "validation_headroom"
        case "window observation deadline expired": reason = "observation_deadline"
        case "window AX timeout unavailable": reason = "ax_timeout_unavailable"
        case "window AX timeout reset failed": reason = "ax_timeout_reset_failed"
        case "no blank native titlebar point": reason = "titlebar_point_unavailable"
        case "modal or unknown window", "sheet or malformed sheet list": reason = "modal_or_unverifiable"
        case "window observation identity unavailable", "window observation or process changed": reason = "identity_changed"
        case "target window is not the app focused window": reason = "target_window_not_focused"
        case "target input element or window changed": reason = "target_input_changed"
        case "focus is outside the bound window": reason = "target_focus_outside_window"
        case "the bound input is not the keyboard focus": reason = "target_input_not_focused"
        case "the selected row is not in the focused list": reason = "target_row_not_in_focused_list"
        case "user focus unavailable": reason = "user_focus_unavailable"
        case "user focus unsupported": reason = "user_focus_unsupported"
        case "user focus malformed": reason = "user_focus_malformed"
        case "user focus query failed": reason = "user_focus_query_failed"
        case "user foreground changed": reason = "user_foreground_changed"
        case "user focus deadline expired": reason = "user_focus_deadline"
        default: reason = result["type"] as? String == "acted" ? "dispatched_unverified" : "unclassified_refusal"
        }
        var out = result
        var diagnostic: [String: Any] = ["phase": phase, "reason": reason,
            "elapsed_ms": Int(max(0, min(600000, (ProcessInfo.processInfo.systemUptime - diagnosticStart) * 1000))),
            "scoped_ax_operations": readScope?.operationCount ?? 0,
            "context_elapsed_ms": readScope?.contextElapsedMS ?? 0,
            "ax_operation_elapsed_ms": readScope?.operationElapsedMS ?? 0,
            "full_context_checks": readScope?.fullContextChecks ?? 0,
            "incremental_context_checks": readScope?.incrementalContextChecks ?? 0]
        if reason == "identity_changed", let identityCause { diagnostic["identity_cause"] = identityCause }
        if let dispatchHeadroom { diagnostic["headroom"] = dispatchHeadroom }
        if let error = readScope?.readinessAXError { diagnostic["readiness_ax_error"] = error }
        if let failure = readScope?.readinessFailure { diagnostic["readiness_failure"] = failure }
        if let progress = readScope?.readinessProgress { diagnostic["readiness_progress"] = progress }
        out["preparation_diagnostic"] = diagnostic
        return out
    }
    let elements = latestElements
    let elementGeneration = latestElementGeneration
    guard let start = latestProcessStart, nativeProcessStartIdentity(pid) == start,
          elements.filter({ CFEqual($0, observedControl ?? element) }).count == 1 else {
        identityCause = "initial_binding"
        return diagnosed(["type":"error", "error":"action_failed", "effect":"not_started",
                "detail":"window observation identity unavailable"])
    }
    // An exact bundle request stays bound to the observed process instance.
    // A later same-bundle process must not redirect this window transaction.
    // Name aliases retain the resolver's existing precedence contract.
    let observedBundle = nativeObservedBundleMatches(pid, target)
    let observedDocumentToken = latestDocumentToken
    let observedDocument = observedDocumentToken.flatMap { token in
        documentTokens.first(where: { $0.token == token })?.document
    }
    func contextIdentity() throws {
        guard !abortState.isStopped else { throw NativeWindowInputError.refused("user stopped input") }
        guard identityRequirement(obsID == latestObsID, "observation_id"),
              identityRequirement(pid == latestTargetPID, "process_id"),
              identityRequirement(target.lowercased() == latestTargetName.lowercased(), "target_name"),
              identityRequirement(nativeProcessStartIdentity(pid) == start, "process_start"),
              identityRequirement(latestElementGeneration == elementGeneration, "element_map"),
              identityRequirement(latestElements.count == elements.count, "element_map"),
              identityRequirement(latestDocumentToken == observedDocumentToken, "document_token") else {
            throw NativeWindowInputError.refused("window observation or process changed")
        }
        if let token = observedDocumentToken {
            guard let pinned = observedDocument,
                  let current = documentTokens.first(where: { $0.token == token })?.document,
                  current.pid == pinned.pid, current.stamp == pinned.stamp,
                  CFEqual(current.window, pinned.window), CFEqual(current.body, pinned.body),
                  current.selectedPath == pinned.selectedPath,
                  current.lifecycleWindowID == pinned.lifecycleWindowID else {
                identityCause = "document_pin"
                throw NativeWindowInputError.refused("window observation or process changed")
            }
        }
    }
    func context() throws {
        do { try validateDocumentObservation(target) }
        catch { identityCause = "document_live"; throw error }
        try contextIdentity()
        guard identityRequirement(observedBundle ? nativeObservedBundleMatches(pid, target)
                             : processForWindowTarget(target) == pid, "target_resolution") else {
            throw NativeWindowInputError.refused("window observation or process changed")
        }
    }
    do {
        // Exact-bundle readiness may amortize live document enumeration only
        // inside the read-only phase. Pins/identity/abort stay per-RPC; full
        // document validation remains at phase boundaries and before input.
        // Alias transactions retain full per-RPC resolver validation.
        let scope = NativeWindowReadScope(deadline: deadline,
            incrementalCheck: observedBundle ? contextIdentity : nil,
            check: context)
        readScope = scope
        phase = "context"
        try scope.ready()
        phase = "binding"
        func capture() throws -> NativeWindowBinding {
            if let popup {
                guard elementClicks==1,selectionCheck != nil,finalTransition != nil,
                      key==nil,point==nil,windowInput==nil,!submitKey else {
                    throw NativeWindowInputError.refused("popup requires a pinned selection transition")
                }
                // Popup captures are complete read-only binding phases too.
                // Keep all option/path/readiness reads, with full live context
                // at both boundaries and identity/abort/deadline on each scoped RPC.
                return try scope.withBindingContext {
                    try scope.ready();try selectionCheck?(scope);try scope.ready()
                    if let observedControl {
                        guard nativePopupSelectionAnchorMatches(observedControl,element,popup,pid,scope:scope) else {
                            throw NativeWindowInputError.refused("observed ComboBox option path changed")
                        }
                        try scope.ready()
                    }
                    return try captureNativePopupWindowBinding(element,popup,pid,expectedStart:start,scope:scope)
                }
            }
            guard selectionCheck==nil,finalTransition==nil,observedControl==nil else {
                throw NativeWindowInputError.refused("selection transition requires a popup binding")
            }
            return try scope.withBindingContext {
                try captureNativeWindowBinding(element, pid, expectedStart: start,
                    pointKind: windowInput != nil ? .windowInput : point != nil ? .windowPoint
                        : (key == nil && elementClicks == 0) ? .titlebar : .elementCenter,
                    scope: scope, relativePoint: point)
            }
        }
        let binding=try capture()
        let routing: ((NativeWindowBinding, Bool) throws -> Void)?
        if windowInput != nil || point != nil || elementClicks != 0 { routing = { binding, _ in try requireNativeWindowTarget(binding, scope: scope) } }
        else if submitKey { routing = { binding, _ in try requireNativeSubmitTarget(binding, scope: scope) } }
        else if key != nil { routing = { try requireNativeKeyTarget($0, element: $1, scope: scope) } }
        else { routing = nil }
        phase = "dispatch"
        let transition: ((_ checkpoint: () throws -> Void) throws -> Void)?
        if let finalTransition {transition={checkpoint in try finalTransition(checkpoint,scope)}}
        else {transition=nil}
        let result: NativeWindowPreparation
        if let drag {
            try context()
            if let expectation,let why=mismatch(expectation,element:element,pid:pid) {
                throw NativeWindowInputError.refused(why)
            }
            result = try dispatchNativeWindowDrag(binding,drag:drag,deadline:deadline,
                stopped:{abortState.isStopped},context:contextIdentity,
                validate:{try scope.withBindingContext {try validateNativeWindowBinding(binding,scope:scope)}
                          try requireNativeWindowTarget(binding,scope:scope)},
                sameProcess:{nativeProcessStartIdentity(pid)==start && (try? windowInputCGFrame(binding.windowID,pid)) != nil},
                windowUnchanged:{
                    guard try windowInputCGFrame(binding.windowID,pid)==binding.windowBounds else {
                        throw NativeWindowInputError.refused("drag window moved or disappeared")
                    }
                })
        } else {
        result = try dispatchNativeWindowPreparation(binding, deadline: deadline,
            stopped: { abortState.isStopped }, context: context,
            validate: { previous in
                if popup != nil {
                    let fresh=try capture()
                    guard CFEqual(fresh.window,previous.window),fresh.windowID==previous.windowID,
                          fresh.windowBounds==previous.windowBounds,fresh.elementBounds==previous.elementBounds,
                          fresh.point==previous.point else {
                        throw NativeWindowInputError.refused("popup binding changed")
                    }
                } else {try scope.withBindingContext {try validateNativeWindowBinding(previous,scope:scope)}}
            }, key: key,
            routing: routing,
            preflight: key == nil && point == nil && windowInput == nil && elementClicks == 0 ? nil : {
                try scope.ready()
                if let expectation, let why = mismatch(expectation, element: element, pid: pid) {
                    throw NativeWindowInputError.refused(why)
                }
                try scope.ready()
            }, pointClick: point != nil, windowInput: windowInput, elementClicks: elementClicks, submitKey: submitKey,
            finalTransition: transition, headroomDiagnostic: { dispatchHeadroom = $0 },
            foregroundClickFocus: { previous, focused in
                try requireNativeForegroundClickFocus(previous, focused, scope: scope)
            })
        }
        return diagnosed(["type":"acted", "action":drag != nil ? "drag" : elementClicks == 2 ? "double_click" : elementClicks == 1 ? "click" : windowInput != nil ? "window_input" : point != nil ? "click_point" : key == nil ? "prepare_window" : "press_key", "obs_id":obsID,
                "input_route":drag != nil ? "window_bound_drag" : submitKey ? "window_bound_submit_key" : elementClicks == 2 ? "window_bound_double_click" : elementClicks == 1 ? "window_bound_element_click" : windowInput != nil ? "window_input" : point != nil ? "window_bound_mouse" : key == nil ? "window_prepare" : "window_key", "mechanism":"pid_window_events",
                "posted_events":result.posted, "window_id":result.windowID,
                "reobserve_required":true, "unverified":"observe_effect"])
    } catch let failure as NativeWindowDispatchFailure {
        return diagnosed(["type":"error", "error":failure.stopped ? "aborted_by_user" : "action_failed",
                "effect":failure.attempted > 0 ? "unknown" : "not_started",
                "posted_or_attempted_events":failure.attempted, "detail":failure.detail])
    } catch {
        let detail: String
        switch error {
        case NativeWindowInputError.refused(let value), NativeWindowInputError.unavailable(let value),
             NativeWindowInputError.needsForeground(let value): detail = value
        default: detail = "window preparation observation failed"
        }
        return diagnosed(["type":"error", "error":abortState.isStopped ? "aborted_by_user" : "action_failed",
                "effect":"not_started", "detail":detail])
    }
}

/// Choosing another window of the frontmost app is what prepare_window is for,
/// but that app is the one the person is in, and a Finder run measured the
/// write moving their focus (2026-10-18). Their keyboard focus decides: an
/// editable field outside the target window keeps its typing unless they
/// approve, and a focus that cannot be read is not called safe.
/// True when the window is already prepared and nothing needs writing.
func foregroundPreparationIsDone(_ pid: pid_t, _ window: AXUIElement) throws -> Bool {
    let app = AXUIElementCreateApplication(pid)
    if (attr(window, "AXMain") as? Bool) == true, let front = attr(app, "AXFocusedWindow"),
       CFGetTypeID(front) == AXUIElementGetTypeID(), CFEqual(front, window) {
        return true
    }
    var raw: AnyObject?
    let error = AXUIElementCopyAttributeValue(app, "AXFocusedUIElement" as CFString, &raw)
    guard let focused = try nativeUserFocusedElement(error, raw) else { return false }
    if let owner = attr(focused, "AXWindow"), CFGetTypeID(owner) == AXUIElementGetTypeID(),
       CFEqual(owner, window) {
        return false
    }
    var settable = DarwinBoolean(false)
    let text = (attr(focused, "AXValue") as? String) != nil
        && AXUIElementIsAttributeSettable(focused, "AXValue" as CFString, &settable) == .success
        && settable.boolValue
    let role = attr(focused, "AXRole") as? String ?? ""
    if text || ["AXTextField", "AXTextArea", "AXComboBox"].contains(role) {
        throw NativeWindowInputError.needsForeground(
            "keyboard focus is in an editable field outside the target window")
    }
    return false
}

func prepareWindow(_ obsID: String, _ target: String, _ element: AXUIElement,
                   _ pid: pid_t, deadline: Double) -> [String: Any] {
    var attempted = false
    let diagnosticStart = ProcessInfo.processInfo.systemUptime
    var phase = "identity"
    func refuse(_ error: String, _ detail: String, foreground: Bool = false) -> [String: Any] {
        var out: [String: Any] = ["type": "error", "error": error,
                                  "effect": attempted ? "unknown" : "not_started", "detail": detail]
        if foreground { out["needs_foreground"] = true }
        out["preparation_diagnostic"] = ["phase": phase,
            "reason": "foreground_refusal",
            "elapsed_ms": Int(max(0, min(600000, (ProcessInfo.processInfo.systemUptime - diagnosticStart) * 1000)))]
        return out
    }
    guard obsID == latestObsID, pid == latestTargetPID,
          processForWindowTarget(target) == pid else {
        return refuse("action_failed", "observation or target changed")
    }
    guard !abortState.isStopped else {
        return refuse("aborted_by_user", "사용자가 중단했습니다")
    }
    guard ProcessInfo.processInfo.systemUptime < deadline else {
        return refuse("action_failed", "input deadline expired")
    }
    guard isFrontmost(pid: pid) else {
        return prepareBackgroundWindow(obsID, target, element, pid, deadline: deadline)
    }
    guard (attr(element, "AXMinimized") as? Bool) == false else {
        return refuse("action_failed", "최소화된 창은 준비할 수 없습니다")
    }
    do {
        if try foregroundPreparationIsDone(pid, element) {
            // Nothing written; the caller still confirms by observing again.
            return ["type": "acted", "action": "prepare_window", "obs_id": obsID,
                    "input_route": "window_prepare", "wrote": [String](),
                    "window_reports_key": true, "foreground_route": true,
                    "unverified": "observe_effect", "reobserve_required": true]
        }
    } catch NativeWindowInputError.needsForeground(let detail) {
        return refuse("action_failed", detail, foreground: true)
    } catch NativeWindowInputError.unavailable(let detail) {
        return refuse("action_failed", detail)
    } catch {
        return refuse("action_failed", "user focus unreadable")
    }
    var wrote: [String] = []
    phase = "foreground_write"
    for name in ["AXMain", "AXFocused"] {
        var settable = DarwinBoolean(false)
        guard AXUIElementIsAttributeSettable(element, name as CFString, &settable) == .success,
              settable.boolValue else { continue }
        guard !abortState.isStopped else {
            return refuse("aborted_by_user", "사용자가 중단했습니다")
        }
        guard ProcessInfo.processInfo.systemUptime < deadline else {
            return refuse("action_failed", "input deadline expired")
        }
        guard isFrontmost(pid: pid) else {
            return refuse("action_failed", "창 준비 중 전경 앱이 바뀌었습니다", foreground: true)
        }
        attempted = true
        guard AXUIElementSetAttributeValue(element, name as CFString,
                                        kCFBooleanTrue) == .success else {
            return refuse("action_failed", "window preparation AX write failed")
        }
        wrote.append(name)
    }
    guard !wrote.isEmpty else {
        return refuse("action_failed",
                      "이 창은 AXMain/AXFocused 를 받지 않습니다 — 준비하지 못했습니다")
    }
    // Ask the window what actually happened rather than assuming the write
    // took: a window can accept the attribute and still not become key.
    phase = "revalidate"
    guard !abortState.isStopped, ProcessInfo.processInfo.systemUptime < deadline,
          isFrontmost(pid: pid) else {
        return refuse("action_failed", "window preparation context changed")
    }
    let became = (attr(element, "AXMain") as? Bool) == true
              || (attr(element, "AXFocused") as? Bool) == true
    var out: [String: Any] = ["type": "acted", "action": "prepare_window", "obs_id": obsID,
                              "input_route": "window_prepare", "wrote": wrote,
                              "window_reports_key": became, "foreground_route": true]
    out["preparation_diagnostic"] = ["phase": phase, "reason": "dispatched_unverified",
        "elapsed_ms": Int(max(0, min(600000, (ProcessInfo.processInfo.systemUptime - diagnosticStart) * 1000)))]
    if !became {
        out["unverified"] = "observe_effect"
        out["reobserve_required"] = true
    }
    return out
}

/// press_key Return: the Go to Folder sheet path only (submitReturnInSheet).
/// Same observation-identity checks as prepareBackgroundWindow; one keyDown;
/// the receipt says so (window_submit_key, posted 1) and that the effect must
/// be read back — the flow's exact postcondition is the evidence, not this.
func submitReturnBackground(_ obsID: String, _ target: String, _ element: AXUIElement,
                            _ pid: pid_t, deadline: Double) -> [String: Any] {
    let elements = latestElements
    let elementGeneration = latestElementGeneration
    guard let start = latestProcessStart, nativeProcessStartIdentity(pid) == start,
          elements.filter({ CFEqual($0, element) }).count == 1 else {
        return ["type":"error", "error":"action_failed", "effect":"not_started",
                "detail":"window observation identity unavailable"]
    }
    func context() throws {
        try validateDocumentObservation(target)
        guard !abortState.isStopped else { throw NativeWindowInputError.refused("user stopped input") }
        guard obsID == latestObsID, pid == latestTargetPID,
              target.lowercased() == latestTargetName.lowercased(),
              processForWindowTarget(target) == pid, nativeProcessStartIdentity(pid) == start,
              latestElementGeneration == elementGeneration,
              latestElements.count == elements.count else {
            throw NativeWindowInputError.refused("window observation or process changed")
        }
    }
    do {
        let scope = NativeWindowReadScope(deadline: deadline, check: context)
        let result = try submitReturnInSheet(element, pid, expectedStart: start, scope: scope,
            deadline: deadline, stopped: { abortState.isStopped }, context: context,
            refusal: { returnTargetRefusal($0, read: $1) })
        return ["type":"acted", "action":"press_key", "obs_id":obsID,
                "input_route":"window_submit_key", "mechanism":"pid_window_events",
                "posted_events":result.posted, "window_id":result.sheetID,
                "reobserve_required":true, "unverified":"observe_effect"]
    } catch let failure as NativeWindowDispatchFailure {
        return ["type":"error", "error":failure.stopped ? "aborted_by_user" : "action_failed",
                "effect":failure.attempted == 0 ? "not_started" : "unknown",
                "posted_or_attempted_events":failure.attempted, "detail":failure.detail]
    } catch NativeWindowInputError.refused(let detail) {
        return ["type":"error", "error":"action_failed", "effect":"not_started",
                "posted_or_attempted_events":0, "detail":detail]
    } catch NativeWindowInputError.unavailable(let detail) {
        return ["type":"error", "error":"action_failed", "effect":"not_started",
                "posted_or_attempted_events":0, "detail":detail]
    } catch {
        return ["type":"error", "error":"action_failed", "effect":"not_started",
                "posted_or_attempted_events":0, "detail":"Return context failed"]
    }
}

/// Semantic URL confirmation only; nil keeps the existing keyboard route.
func confirmSafariAddress(_ obsID:String, _ target:String, _ el:AXUIElement, _ p:pid_t,
                          deadline:Double, expect:[String:Any]?, submitAuthorized:Bool) -> [String:Any]? {
    guard submitAuthorized, target == "com.apple.Safari", latestDocumentToken == nil,
          let start = latestProcessStart, let expectedURL = expect?["value"] as? String else { return nil }
    let elements = latestElements
    let generation = latestElementGeneration
    var attempted = false
    do {
        let scope = NativeWindowReadScope(deadline:deadline,check:{
            guard !abortState.isStopped, latestObsID == obsID, latestTargetPID == p,
                  latestElementGeneration == generation,
                  nativeProcessStartIdentity(p) == start, nativeObservedBundleMatches(p,target) else {
                throw NativeWindowInputError.refused("Safari navigation context changed")
            }
        })
        let eligible = try nativeSafariAddressConfirmEligible(el,read:scope.read,actions:{ node in
            let result = try scope.withTimeout(node) { () -> (AXError,CFArray?) in
                var raw: CFArray?
                let status = AXUIElementCopyActionNames(node,&raw)
                return (status,raw)
            }
            guard result.0 == .success, let actions = result.1 as? [String] else {
                throw NativeWindowInputError.refused("Safari address actions unavailable")
            }
            return actions
        })
        if eligible {
            guard try scope.read(el,"AXValue").1 as? String == expectedURL else {
                throw NativeWindowInputError.refused("Safari address value changed")
            }
            var owner: pid_t = 0
            guard AXUIElementGetPid(el,&owner) == .success, owner == p,
                  elements.filter({CFEqual($0,el)}).count == 1 else {
                throw NativeWindowInputError.refused("Safari address owner changed")
            }
            try scope.ready(forceFull:true)
            if let want = expect, mismatch(want,element:el,pid:p) != nil {
                throw NativeWindowInputError.refused("Safari navigation binding changed")
            }
            let status = try scope.withTimeout(el, maximumSeconds:2) {
                attempted = true
                return AXUIElementPerformAction(el,"AXConfirm" as CFString)
            }
            return nativeSafariAddressConfirmReceipt(status,obsID:obsID)
        }
    } catch {
        return ["type":"error","error":abortState.isStopped ? "aborted_by_user" : "action_failed",
                "effect":attempted ? "unknown" : "not_started",
                "ax_confirmation_attempted":attempted,"posted_or_attempted_events":0,
                "detail":"Safari address confirmation context unavailable"]
    }
    return nil
}

func nativeOpeningParametersProblem(_ action:[String:Any]) -> String? {
    if action.keys.contains("opening_action") {
        guard let requested=action["opening_action"] as? String,
              ["AXShowMenu","AXPress"].contains(requested) else {return "invalid_opening_action"}
    }
    return nil
}

func nativeSelectionParametersProblem(_ action: [String: Any]) -> String? {
    guard let label = action["option_label"] as? String, !label.isEmpty else {
        return "option_label_required"
    }
    if action.keys.contains("value") {
        guard let value = action["value"] as? String, value.utf8.elementsEqual(label.utf8) else {
            return "native_option_value_mismatch"
        }
    }
    return nil
}

func act(_ obsID: String, _ target: String, _ action: [String: Any],
         expect: [String: Any]? = nil, clickDeadline: Double? = nil, coordinateAuthorized: Bool = false,
         savePanelToken: String? = nil, windowInputAuthorized: Bool = false,
         submitAuthorized: Bool = false) -> [String: Any] {
    if action["name"] as? String == "open_select",let problem=nativeOpeningParametersProblem(action) {
        return ["type":"error","error":"invalid_selection_parameters","effect":"not_started","detail":problem]
    }
    if ["select_option", "select_custom_option"].contains(action["name"] as? String ?? ""),
       let problem = nativeSelectionParametersProblem(action) {
        return ["type":"error", "error":"invalid_selection_parameters",
                "effect":"not_started", "detail":problem]
    }
    if let token = savePanelToken {
        guard !token.isEmpty, token.count <= 128, Set(action.keys) == Set(["name", "element_index"]),
              action["name"] as? String == "click",
              let index = documentIsWholeNumber(action["element_index"] as Any), index.intValue >= 0 else {
            return ["type":"error", "error":"save_panel_guard_unavailable", "effect":"not_started"]
        }
    }
    if action["name"] as? String == "window_input", !windowInputAuthorized {
        return ["type":"error", "error":"window_input_authorization_required", "effect":"not_started"]
    }
    if ["click_point", "drag", "hover", "vroid_drag", "vroid_focus"].contains(action["name"] as? String ?? ""), !coordinateAuthorized {
        return ["type":"error", "error":"coordinate_authorization_required", "effect":"not_started"]
    }
    let deadline = min(clickDeadline ?? .infinity, ProcessInfo.processInfo.systemUptime + 5)
    // Every refusal here precedes dispatch, and says so: the Python side reads
    // `effect` to decide whether re-running or undoing is even allowed, and an
    // error without it is read as "may have happened" (peer review).
    guard obsID == latestObsID else {
        return ["type": "error", "error": "stale_observation",
                "effect": "not_started",
                "detail": "observe again and re-derive element_index"]
    }
    guard let p = pid(forBundle: target) else {
        return ["type": "error", "error": "target_not_found",
                "effect": "not_started"]
    }
    // The observation and the action must concern the same app.
    guard p == latestTargetPID else {
        return ["type": "error", "error": "target_mismatch",
                "effect": "not_started",
                "detail": "obs_id was taken on \(latestTargetName)"]
    }
    do { try validateDocumentObservation(target) }
    catch { return ["type":"error", "error":"document_snapshot_unavailable", "effect":"not_started"] }
    if let token = latestDocumentToken {
        let selectedPath = documentTokens.first(where: { $0.token == token })?.document.selectedPath
        if documentScopedSaveAction(action, selectedPath: selectedPath) {
            guard expect?["selection"] == nil else {
                return ["type":"error", "error":"unverifiable_action", "effect":"not_started"]
            }
        } else {
            guard let index = documentIsWholeNumber(action["element_index"] as Any),
                  index.intValue >= 0, index.intValue < latestElements.count,
                  ["read", "click", "prepare_window", "press_key", "set_value", "type_text", "secondary_action", "scroll"].contains(action["name"] as? String ?? ""),
                  expect?["selection"] == nil else {
                return ["type":"error", "error":"unverifiable_action", "effect":"not_started"]
            }
            do {
                try documentValidateElement(latestElements[index.intValue],
                                            window: documentWindowForToken(target, latestDocumentToken!))
            } catch {
                return ["type":"error", "error":"document_snapshot_unavailable", "effect":"not_started"]
            }
        }
    }
    let elements = latestElements
    if abortState.isStopped {
        return ["type": "error", "error": "aborted_by_user",
                "effect": "not_started",
                "detail": "사용자가 Esc로 중단했습니다"]
    }
    guard ensureVisibleControl(forPID: p, windowless: ["menu", "open_folder", "reopen_window", "close_menu"].contains((action["name"] as? String) ?? "")) else {
        return indicatorRefusal()
    }
    let name = action["name"] as? String ?? ""

    /// The element this action is about — named by index, or by a point that
    /// has not been resolved into one yet.
    ///
    /// `point` is in WINDOW coordinates of `window_index` (default 0), the same
    /// space `capture` reports its viewport in, so a coordinate read off a
    /// screenshot can be handed straight back. It is bounds-checked against
    /// that window: a point outside it is not this app's to press, and
    /// `elementAtPoint` refuses anything belonging to another process anyway.
    func element() -> AXUIElement? {
        if let i = action["element_index"] as? Int, i >= 0, i < elements.count {
            if let token = latestDocumentToken {
                do { try documentValidateElement(elements[i], window: documentWindowForToken(target, token)) }
                catch { return nil }
            }
            return elements[i]
        }
        guard let raw = action["point"] as? [String: Any],
              let x = (raw["x"] as? NSNumber)?.doubleValue,
              let y = (raw["y"] as? NSNumber)?.doubleValue,
              x.isFinite, y.isFinite else { return nil }
        let app = AXUIElementCreateApplication(p)
        let windows = realWindows(of: app)
        let index = action["window_index"] as? Int ?? 0
        guard index >= 0, index < windows.count,
              let positionValue = attr(windows[index], kAXPositionAttribute as String),
              let sizeValue = attr(windows[index], kAXSizeAttribute as String)
        else { return nil }
        var origin = CGPoint.zero, size = CGSize.zero
        AXValueGetValue(positionValue as! AXValue, .cgPoint, &origin)
        AXValueGetValue(sizeValue as! AXValue, .cgSize, &size)
        guard x >= 0, y >= 0, x < size.width, y < size.height else { return nil }
        return elementAtPoint(CGPoint(x: origin.x + x, y: origin.y + y), pid: p)
    }

    // Before anything is performed, and after the indicator so the check sees
    // the same screen the action will.
    if let want = expect, let why = mismatch(want, element: element(), pid: p) {
        return ["type": "error", "error": "target_changed",
                "effect": "not_started", "detail": why]
    }

    switch name {
    case "window_input":
        let input: NativeOpaqueWindowInput
        if Set(action.keys) == Set(["name", "element_index", "key"]), let value = action["key"] as? String {
            input = .key(value)
        } else if Set(action.keys) == Set(["name", "element_index", "text"]), let value = action["text"] as? String {
            input = .text(value)
        } else {
            return ["type":"error", "error":"window_input_unavailable", "effect":"not_started"]
        }
        guard input.valid, let index = documentIsWholeNumber(action["element_index"]),
              !CFNumberIsFloatType(unsafeBitCast(index, to: CFNumber.self)),
              index.intValue >= 0, index.intValue < elements.count,
              let el = element(), str(el, kAXRoleAttribute as String) == kAXWindowRole else {
            return ["type":"error", "error":"window_input_unavailable", "effect":"not_started"]
        }
        return prepareBackgroundWindow(obsID, target, el, p, deadline: deadline,
                                       expectation: expect, windowInput: input)
#if TACTO_EXPERIMENTAL_FOREGROUND
    case "vroid_focus":
        guard target=="net.pixiv.vroid.macosx", latestDocumentToken==nil,
              Set(action.keys)==Set(["name","element_index","title"]),
              let el=element(), str(el,kAXRoleAttribute as String)==kAXWindowRole,
              let title=action["title"] as? String, str(el,kAXTitleAttribute as String)==title,
              let start=latestProcessStart else {
            return ["type":"error","error":"invalid_vroid_focus","effect":"not_started"]
        }
        do {
            let binding=try captureNativeWindowBinding(el,p,expectedStart:start,pointKind:.windowPoint,relativePoint:CGPoint(x:1,y:1))
            let generation=latestElementGeneration
            return dispatchVroidFocus(binding,title:title,deadline:deadline,stopped:{abortState.isStopped},context:{
                guard latestObsID==obsID, latestTargetPID==p,
                      latestElementGeneration==generation, nativeProcessStartIdentity(p)==start else {
                    throw NativeWindowInputError.refused("focus observation changed")
                }
            })
        } catch {
            return ["type":"error","error":"vroid_focus_binding_failed","effect":"not_started","detail":String(describing:error)]
        }
    case "vroid_drag":
        func parsePoint(_ value:Any?) -> CGPoint? {
            guard let raw=value as? [String:Any], Set(raw.keys)==Set(["x","y"]),
                  let x=raw["x"] as? NSNumber, let y=raw["y"] as? NSNumber,
                  CFGetTypeID(x) != CFBooleanGetTypeID(), CFGetTypeID(y) != CFBooleanGetTypeID(),
                  x.doubleValue.isFinite, y.doubleValue.isFinite else { return nil }
            return CGPoint(x:x.doubleValue,y:y.doubleValue)
        }
        let baseKeys = Set(["name","element_index","from","to","duration","title"])
        var graphBounds: CGRect? = nil
        if let value = action["graph_bounds"] {
            guard let rect=value as? [String:Any],Set(rect.keys)==Set(["x","y","width","height"]) else {
                return ["type":"error","error":"invalid_curve_graph_bounds","effect":"not_started"]
            }
            var numbers: [Double] = []
            for key in ["x","y","width","height"] {
                guard let n=rect[key] as? NSNumber,CFGetTypeID(n) != CFBooleanGetTypeID(),n.doubleValue.isFinite else {
                    return ["type":"error","error":"invalid_curve_graph_bounds","effect":"not_started"]
                }
                numbers.append(n.doubleValue)
            }
            graphBounds=CGRect(x:numbers[0],y:numbers[1],width:numbers[2],height:numbers[3])
        }
        var via: [CGPoint] = []
        if let value = action["via"] {
            guard let values = value as? [Any], values.count <= 12 else {
                return ["type":"error","error":"invalid_vroid_drag","effect":"not_started"]
            }
            for value in values {
                guard let point = parsePoint(value) else {
                    return ["type":"error","error":"invalid_vroid_drag","effect":"not_started"]
                }
                via.append(point)
            }
        }
        guard target=="net.pixiv.vroid.macosx", latestDocumentToken==nil,
              Set(action.keys).isSuperset(of:baseKeys),
              Set(action.keys).isSubset(of:baseKeys.union(["via","graph_bounds"])),
              let el=element(), str(el,kAXRoleAttribute as String)==kAXWindowRole,
              let title=action["title"] as? String, str(el,kAXTitleAttribute as String)==title,
              let from=parsePoint(action["from"]), let to=parsePoint(action["to"]),
              let duration=action["duration"] as? NSNumber, CFGetTypeID(duration) != CFBooleanGetTypeID(),
              let start=latestProcessStart else {
            return ["type":"error","error":"invalid_vroid_drag","effect":"not_started"]
        }
        do {
            let binding=try captureNativeWindowBinding(el,p,expectedStart:start,pointKind:.windowPoint,relativePoint:from)
            let generation=latestElementGeneration
            return dispatchVroidDrag(binding,title:title,from:from,to:to,duration:duration.doubleValue,via:via,graphBounds:graphBounds,
                deadline:deadline,stopped:{abortState.isStopped},context:{
                    guard latestObsID==obsID, latestTargetPID==p,
                          latestElementGeneration==generation, nativeProcessStartIdentity(p)==start else {
                        throw NativeWindowInputError.refused("drag observation changed")
                    }
                })
        } catch {
            return ["type":"error","error":"vroid_drag_binding_failed","effect":"not_started","detail":String(describing:error)]
        }
#endif
    case "drag":
        guard Set(action.keys).isSubset(of:Set(["name","element_index","from","to","duration","via"])),
              let index=documentIsWholeNumber(action["element_index"]),index.intValue>=0,index.intValue<elements.count,
              let el=element(),str(el,kAXRoleAttribute as String)==kAXWindowRole,
              let duration=action["duration"] as? NSNumber,CFGetTypeID(duration) != CFBooleanGetTypeID(),
              duration.doubleValue.isFinite,duration.doubleValue>=0.1,duration.doubleValue<=2 else {
            return ["type":"error","error":"invalid_drag","effect":"not_started"]
        }
        func dragPoint(_ raw:Any?) -> CGPoint? {
            guard let point=raw as? [String:Any],Set(point.keys)==Set(["x","y"]),
                  let x=point["x"] as? NSNumber,let y=point["y"] as? NSNumber,
                  CFGetTypeID(x) != CFBooleanGetTypeID(),CFGetTypeID(y) != CFBooleanGetTypeID(),
                  x.doubleValue.isFinite,y.doubleValue.isFinite,x.doubleValue>=0,y.doubleValue>=0 else {return nil}
            return CGPoint(x:x.doubleValue,y:y.doubleValue)
        }
        guard let from=dragPoint(action["from"]),let to=dragPoint(action["to"]),
              action["via"] == nil || action["via"] is [Any] else {
            return ["type":"error","error":"invalid_drag_point","effect":"not_started"]
        }
        let rawVia=action["via"] as? [Any] ?? []
        let via=rawVia.compactMap {dragPoint($0)}
        guard rawVia.count<=12,via.count==rawVia.count else {
            return ["type":"error","error":"invalid_drag_path","effect":"not_started"]
        }
        return prepareBackgroundWindow(obsID,target,el,p,deadline:deadline,expectation:expect,point:from,
            drag:NativeWindowDrag(path:[from]+via+[to],duration:duration.doubleValue))
    case "hover":
        // The global pointer, never a button: see PointerHover.swift.
        guard Set(action.keys).isSuperset(of:["name","element_index","to","duration","hold"]),
              Set(action.keys).isSubset(of:["name","element_index","to","duration","hold","via","capture"]),
              action["capture"] == nil || (action["capture"] as? NSNumber).map({ CFGetTypeID($0) == CFBooleanGetTypeID() }) == true,
              let index=documentIsWholeNumber(action["element_index"]),index.intValue>=0,index.intValue<elements.count,
              let el=element(),str(el,kAXRoleAttribute as String)==kAXWindowRole,
              let to=pointerHoverPoint(action["to"]),
              action["via"] == nil || action["via"] is [Any],
              let duration=action["duration"] as? NSNumber,CFGetTypeID(duration) != CFBooleanGetTypeID(),
              let hold=action["hold"] as? NSNumber,CFGetTypeID(hold) != CFBooleanGetTypeID(),
              let start=latestProcessStart,nativeProcessStartIdentity(p)==start else {
            return ["type":"error","error":"invalid_hover","effect":"not_started"]
        }
        let rawVia=action["via"] as? [Any] ?? []
        let via=rawVia.compactMap {pointerHoverPoint($0)}
        guard rawVia.count<=12,via.count==rawVia.count else {
            return ["type":"error","error":"invalid_hover","effect":"not_started"]
        }
        let generation=latestElementGeneration
        return dispatchPointerHover(pid:p,path:via+[to],duration:duration.doubleValue,hold:hold.doubleValue,
            deadline:deadline,stopped:{abortState.isStopped},context:{
                guard latestObsID==obsID, latestTargetPID==p,
                      latestElementGeneration==generation, nativeProcessStartIdentity(p)==start else {
                    throw NativeWindowInputError.refused("hover observation changed")
                }
            },io:pointerHoverSystemIO(window:el),
            atHoldEnd:(action["capture"] as? Bool) == true ? {
                // Capture names windows by their place among the app's windows,
                // not by the observation's element index.
                guard let window=realWindows(of:AXUIElementCreateApplication(p)).firstIndex(where:{CFEqual($0,el)}) else {
                    return ["type":"error","error":"window_id_unavailable","effect":"not_started"]
                }
                return captureWindow(obsID,target,windowIndex:window,
                                     deadline:min(deadline-0.2,ProcessInfo.processInfo.systemUptime+1))
            } : nil)
    case "click_point":
        guard Set(action.keys) == Set(["name", "element_index", "point"]),
              let index = documentIsWholeNumber(action["element_index"]),
              index.intValue >= 0, index.intValue < elements.count,
              let raw = action["point"] as? [String: Any], Set(raw.keys) == Set(["x", "y"]),
              let x = raw["x"] as? NSNumber, let y = raw["y"] as? NSNumber,
              CFGetTypeID(x) != CFBooleanGetTypeID(), CFGetTypeID(y) != CFBooleanGetTypeID(),
              x.doubleValue.isFinite, y.doubleValue.isFinite, x.doubleValue >= 0, y.doubleValue >= 0,
              let el = element(), str(el, kAXRoleAttribute as String) == kAXWindowRole else {
            return ["type":"error", "error":"action_failed", "effect":"not_started", "detail":"invalid observed window point"]
        }
        return prepareBackgroundWindow(obsID, target, el, p, deadline: deadline, expectation: expect,
                                       point: CGPoint(x: x.doubleValue, y: y.doubleValue))
    case "double_click":
        // AX has no double-click. Where the element offers AXOpen (what a
        // double click means in a list of files or chats), that is used and
        // no pointer event is made. Otherwise: two window-bound clicks at the
        // element's hit-tested centre, the second with click count 2, in the
        // app's focused window, the app in the background (2026-10-10:
        // opening a KakaoTalk chat from its list needed this).
        guard Set(action.keys) == Set(["name", "element_index"]), let el = element() else {
            return ["type": "error", "error": "action_failed", "effect": "not_started",
                    "detail": "double_click requires one observed element_index"]
        }
        var names: CFArray?
        if AXUIElementCopyActionNames(el, &names) == .success,
           let list = names as? [String], list.contains("AXOpen") {
            let error = AXUIElementPerformAction(el, "AXOpen" as CFString)
            if error != .success {
                return ["type": "error", "error": "action_failed",
                        "effect": axActionFailureEffect(error), "detail": "AXOpen 실행 실패"]
            }
            return ["type": "acted", "action": "double_click", "obs_id": obsID,
                    "input_route": "ax_open", "note": "not verified — observe again"]
        }
        return prepareBackgroundWindow(obsID, target, el, p, deadline: deadline,
                                       expectation: expect, elementClicks: 2)
    case "prepare_window":
        guard Set(action.keys) == Set(["name", "element_index"]), let el = element(),
              str(el, kAXRoleAttribute as String) == kAXWindowRole else {
            return ["type": "error", "error": "action_failed", "effect": "not_started",
                    "detail": "prepare_window requires only an observed AXWindow index"]
        }
        return prepareWindow(obsID, target, el, p, deadline: deadline)
    case "click":
        guard let el = element() else {
            // Which address failed, since there are two kinds now.
            let detail = action["point"] != nil
                ? "point 로 요소를 찾지 못했습니다 — 창 밖이거나 그 자리에 요소가 없습니다"
                : "element_index out of range"
            return ["type": "error", "error": "action_failed",
                    "effect": "not_started", "detail": detail]
        }
        // No AXPress (KakaoTalk's chat rows and profile buttons, 2026-10-10):
        // one window-bound click at the element's hit-tested centre, the app
        // left in the background — never inside a save panel, whose guard
        // speaks only AXPress.
        func pointerClick() -> [String: Any] {
            guard savePanelToken == nil else {
                return ["type": "error", "error": "action_failed",
                        "effect": "not_started", "needs_pointer": true,
                        "detail": "저장 패널 안에서는 누를 수 없는 요소를 포인터로 누르지 않습니다"]
            }
            return prepareBackgroundWindow(obsID, target, el, p, deadline: deadline,
                                           expectation: expect, elementClicks: 1)
        }
        var advertised: CFArray?
        let listed = AXUIElementCopyActionNames(el, &advertised)
        if listed == .success, let names = advertised as? [String],
           !names.contains(kAXPressAction as String), savePanelToken == nil,
           let selected = selectListItem(el, deadline: deadline) {
            return selected.merging(["action": name, "obs_id": obsID]) { a, _ in a }
        }
        // Finder row clicks mean selection. Use the table's semantic operation
        // first: a crowded-window readiness walk can consume the entire shared
        // deadline, leaving no time for a fallback even though it sent no input.
        if target.lowercased() == "com.apple.finder", listed == .success,
           let names = advertised as? [String], !names.contains(kAXPressAction as String),
           savePanelToken == nil, let selected = selectTableRow(el, deadline: deadline) {
            return selected.merging(["action": name, "obs_id": obsID]) { a, _ in a }
        }
        if listed == .success, let names = advertised as? [String],
           !names.contains(kAXPressAction as String) {
            // A row the pointer route refused before sending anything (the app
            // in front, or too many windows to validate in time — rc.2's
            // Finder runs, 2026-10-23) can still be selected through its table.
            let pointer = pointerClick()
            if pointer["type"] as? String == "error", pointer["effect"] as? String == "not_started",
               (pointer["posted_or_attempted_events"] as? Int ?? 0) == 0, savePanelToken == nil,
               var selected = selectTableRow(el, deadline: deadline) {
                selected["pointer_refusal"] = pointer["detail"] ?? pointer["error"]
                return selected.merging(["action": name, "obs_id": obsID]) { a, _ in a }
            }
            return pointer
        }
        if let token = savePanelToken,
           let refusal = validateSavePanelDispatch(obsID, target, action, token, deadline: deadline) {
            return refusal
        }
        guard !abortState.isStopped, ProcessInfo.processInfo.systemUptime < deadline else {
            return ["type": "error", "error": "input_cancelled_or_expired", "effect": "not_started"]
        }
        let err = AXUIElementPerformAction(el, kAXPressAction as CFString)
        if err == AXError(rawValue: -25206) {
            return pointerClick()
        }
        if err == AXError(rawValue: -25204) {
            // Apple's AXUIElement.h: an action callback doing modal processing
            // can time out and return this HAVING done the work. Pressing again
            // to be safe is how a Send button sends twice, so it is reported
            // and the postcondition decides — same as the in-process driver.
            return ["type": "acted", "action": name, "obs_id": obsID,
                    "unverified": "cannot_complete",
                    "note": "AXPress timed out; it may still have happened"]
        }
        if err != .success {
            return ["type": "error", "error": "action_failed",
                    "effect": axActionFailureEffect(err),
                    "detail": "AXPress failed (\(err.rawValue)); do not retry an uncertain effect"]
        }
    case "set_value":
        guard let el = element(), let v = action["value"] as? String else {
            return ["type": "error", "error": "action_failed",
                    "effect": "not_started", "detail": "bad set_value"]
        }
        if AXUIElementSetAttributeValue(el, kAXValueAttribute as CFString, v as CFTypeRef) != .success {
            return ["type": "error", "error": "action_failed",
                    "effect": "not_started", "detail": "AXValue set failed"]
        }
    case "type_text":
        // Typing requires a target element, and that element must be somewhere
        // text belongs. Before this, type_text posted key events to the PID
        // without consulting any element at all — which is what made "fill in a
        // field" and "type a command into a terminal" the same primitive.
        guard let text = action["text"] as? String, let el = element() else {
            return ["type": "error", "error": "action_failed",
                    "effect": "not_started",
                    "detail": "type_text 에는 element_index 가 필요합니다"]
        }
        let role = str(el, kAXRoleAttribute as String) ?? ""
        let textRoles: Set<String> = ["AXTextField", "AXTextArea", "AXComboBox",
                                      "AXSearchField"]
        guard textRoles.contains(role) else {
            return ["type": "error", "error": "action_failed",
                    "effect": "not_started",
                    "detail": "텍스트 입력 요소가 아닙니다: \(role)"]
        }
        guard !text.unicodeScalars.contains(where: { $0.value < 0x20 || $0.value == 0x7F })
        else {
            return ["type": "error", "error": "action_failed",
                    "effect": "not_started",
                    "detail": "제어문자는 입력할 수 없습니다"]
        }
        // The role says this element CAN hold text. A keystroke cannot say
        // WHICH element: it goes wherever the first responder is, so checking
        // the role and then posting keys to the process left the app to decide
        // where the text went while the caller believed it had named a field.
        //
        // Everything that used to stand here was built around watching that
        // gap — a focus check before, another between every pair of
        // characters, and a partial-write `unknown` for when the answer
        // changed halfway. `AXSelectedText` closes it: the write names the
        // element, so it lands there or not at all. With an empty selection
        // that is an insert at the caret, which is what typing does.
        //
        // Verified 2026-09-20 against a background editable NSTextView: the
        // text arrived with Korean and an emoji intact while the fixture's own
        // key-event counter stayed at 0.
        var settable = DarwinBoolean(false)
        guard AXUIElementIsAttributeSettable(el, "AXSelectedText" as CFString,
                                             &settable) == .success,
              settable.boolValue else {
            return ["type": "error", "error": "action_failed",
                    "effect": "not_started",
                    "detail": "이 요소는 텍스트 삽입을 지원하지 않습니다 — "
                              + "AXSelectedText 를 쓸 수 없습니다"]
        }
        if abortState.isStopped {
            return ["type": "error", "error": "aborted_by_user",
                    "effect": "not_started",
                    "detail": "사용자가 중단했습니다 — 아무것도 입력하지 않았습니다"]
        }
        let wrote = AXUIElementSetAttributeValue(el, "AXSelectedText" as CFString,
                                                 text as CFTypeRef)
        if wrote == .cannotComplete {
            // As with AXPress: a callback doing modal processing can time out
            // having done the work. Writing again "to be safe" is how one
            // sentence becomes two.
            return ["type": "error", "error": "action_failed", "effect": "unknown",
                    "detail": "텍스트 삽입이 시간 초과했습니다 — 들어갔을 수도 있습니다"]
        }
        guard wrote == .success else {
            return ["type": "error", "error": "action_failed",
                    "effect": "not_started",
                    "detail": "텍스트 삽입에 실패했습니다 (\(wrote.rawValue))"]
        }
    case "read":
        // Look, change nothing — the observation and indicator above are the
        // whole of it. Lets a recipe read one part of the screen before using
        // that value somewhere else.
        break
    case "menu":
        // Normalized to lists so a path element may offer localized
        // alternatives; the launcher has already checked the shape, and this
        // check is repeated because the helper holds the grant and must not
        // depend on a caller having validated on its behalf.
        var path: [[String]] = []
        for element in (action["path"] as? [Any] ?? []) {
            if let one = element as? String { path.append([one]) }
            else if let many = element as? [String], !many.isEmpty { path.append(many) }
            else { return ["type": "error", "error": "action_failed",
                           "effect": "not_started",
                           "detail": "menu path 형식 오류"] }
        }
        if path.isEmpty || path.count > 6 {
            return ["type": "error", "error": "action_failed",
                    "detail": "menu path 길이가 올바르지 않습니다"]
        }
        // This used to raise the app first, because an app validates its menus
        // while it is the active one. That is a real precondition, but taking
        // the person's focus to satisfy it is not this action's to decide — so
        // the attempt is made where the app stands and the refusal says
        // whether being in the background is the likely reason.
        let wasFront = isFrontmost(pid: p)
        guard let (item, walked) = menuItem(pid: p, path: path) else {
            return ["type": "error", "error": "action_failed", "effect": "not_started",
                    "detail": "메뉴를 유일하게 찾지 못했습니다"]
        }
        guard let enabled = attr(item, kAXEnabledAttribute as String),
              CFGetTypeID(enabled as CFTypeRef) == CFBooleanGetTypeID() else {
            return ["type": "error", "error": "menu_state_unavailable",
                    "effect": "not_started", "detail": "메뉴의 활성 상태를 읽지 못했습니다"]
        }
        if (enabled as? Bool) == false {
            // An app validates its menus while it is the active one, so a
            // background app reports items disabled that are perfectly fine.
            // Said here rather than fixed by raising the app: which of those
            // two it is decides whether asking the person is worth it.
            return ["type": "error", "error": "action_failed",
                    "effect": "not_started",
                    "needs_foreground": !wasFront,
                    "detail": "메뉴 '\(walked.joined(separator: " > "))' 가 비활성 상태입니다"
                              + (wasFront ? "" : " (이 앱이 앞에 있지 않아 그렇게 보일 수 있습니다)")]
        }
        // A command that acts on "the document in front" (File > Save) may be
        // bound to one file: right before the press, the app's focused AND
        // main windows must show exactly that file — canonical path, Unicode
        // composed (TextEdit reports names decomposed). A name match was not
        // enough: two files called note.txt in different folders (Codex review
        // of 4d04b80). Otherwise nothing is pressed.
        if let wanted = action["focused_document"] {
            if let token = latestDocumentToken {
                // Menus may have taken time to resolve. The same pinned window
                // and path must still exist before the focused/main check.
                do { _ = try documentWindowForToken(target, token) }
                catch { return ["type":"error", "error":"document_snapshot_unavailable", "effect":"not_started"] }
            }
            let why: String
            if let want = wanted as? String, want.hasPrefix("/") {
                why = focusedDocumentMismatch(pid: p, want: want)
            } else {
                why = "invalid focused_document"
            }
            guard why.isEmpty else {
                return ["type": "error", "error": "action_failed", "effect": "not_started",
                        "detail": "포커스된 문서가 승인한 파일이 아니어서 누르지 않았습니다 (\(why))"]
            }
            // The check took time; stop and the deadline are asked again
            // right before the press (Codex review of 31955f0).
            guard !abortState.isStopped, ProcessInfo.processInfo.systemUptime < deadline else {
                return ["type": "error", "error": abortState.isStopped ? "aborted_by_user" : "input_deadline_expired",
                        "effect": "not_started", "detail": "문서 확인 뒤 중단되었거나 기한이 지났습니다"]
            }
        }
        let menuError = AXUIElementPerformAction(item, kAXPressAction as CFString)
        if menuError != .success {
            return ["type": "error", "error": "action_failed",
                    "effect": axActionFailureEffect(menuError), "detail": "메뉴 실행 실패"]
        }
        // Some commands make the app bring itself forward (Finder's Go > Go to
        // Folder…, measured 2026-10-08). Not prevented — the press was the
        // app's to answer — but watched briefly and said, so a caller knows
        // the person's focus moved because of this step.
        var activated = false
        if !wasFront {
            for _ in 0..<5 {
                Thread.sleep(forTimeInterval: 0.1)
                if isFrontmost(pid: p) { activated = true; break }
            }
        }
        var out: [String: Any] = ["type": "acted", "action": name, "obs_id": obsID,
                                  "menu_path": walked, "note": "not verified — observe again"]
        if activated {
            out["activated_target"] = true
            out["note"] = "not verified — observe again; the app was observed in front after the press"
        }
        return out
    case "reopen_window":
        // An app whose window was closed, or went to another Space after a
        // context menu (KakaoTalk, 2026-10-10), shows nothing to act in. What
        // the Dock does — ask the running app to reopen — goes through
        // LaunchServices with activates=false. That flag is a request, not a
        // guarantee: whether the app came forward is observed and reported,
        // and whether a window appeared is for a fresh observation to show.
        guard Set(action.keys) == Set(["name"]) else {
            return ["type": "error", "error": "action_failed", "effect": "not_started",
                    "detail": "reopen_window 은 인자를 받지 않습니다"]
        }
        guard let running = NSRunningApplication(processIdentifier: p), let appURL = running.bundleURL,
              running.bundleIdentifier?.lowercased() == target.lowercased() else {
            return ["type": "error", "error": "action_failed", "effect": "not_started",
                    "detail": "실행 중인 앱의 번들을 찾지 못했습니다"]
        }
        let appElement = AXUIElementCreateApplication(p)
        let windowsBefore = realWindows(of: appElement).count
        let wasFront = isFrontmost(pid: p)
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.activates = false
        configuration.addsToRecentItems = false
        configuration.promptsUserIfNeeded = false
        configuration.createsNewApplicationInstance = false
        switch openFolderBounded(deadline: deadline, stopped: { abortState.isStopped }, request: { done in
            NSWorkspace.shared.openApplication(at: appURL, configuration: configuration) { opened, error in
                done(error == nil && opened?.processIdentifier == p)
            }
        }) {
        case .notStarted(let why):
            return ["type": "error", "error": abortState.isStopped ? "aborted_by_user" : "input_deadline_expired",
                    "effect": "not_started", "detail": "다시 열기 전에 멈췄습니다: \(why)"]
        case .unknown(let why):
            return ["type": "error", "error": "action_failed", "effect": "unknown",
                    "detail": "앱이 다시 열기 요청을 받았는지 확인하지 못했습니다: \(why)"]
        case .opened:
            break
        }
        var windowsAfter = windowsBefore, activated = false
        for _ in 0..<10 {
            guard !abortState.isStopped, deadline - ProcessInfo.processInfo.systemUptime > 0.1 else { break }
            Thread.sleep(forTimeInterval: 0.1)
            if !wasFront && isFrontmost(pid: p) { activated = true }
            windowsAfter = realWindows(of: appElement).count
            if windowsAfter > windowsBefore { break }
        }
        var reopened: [String: Any] = ["type": "acted", "action": name, "obs_id": obsID,
            "windows_before": windowsBefore, "windows_after": windowsAfter,
            "note": "not verified — observe again"]
        if activated {
            reopened["activated_target"] = true
            reopened["note"] = "not verified — observe again; the app was observed in front after reopening"
        }
        return reopened
    case "close_menu":
        // An open context or pop-up menu, closed the way AX offers (AXCancel
        // on the menu), not with Escape: Escape goes to whatever has the key
        // focus, which is not necessarily the menu (2026-10-10, a KakaoTalk
        // context menu left open after AXShowMenu). Only menus of this app
        // that are not part of its menu bar.
        guard Set(action.keys) == Set(["name"]) else {
            return ["type": "error", "error": "action_failed", "effect": "not_started",
                    "detail": "close_menu 는 인자를 받지 않습니다"]
        }
        let openMenus = openContextMenus(pid: p)
        guard !openMenus.isEmpty else {
            return ["type": "error", "error": "action_failed", "effect": "not_started",
                    "detail": "열린 메뉴가 없습니다"]
        }
        guard !abortState.isStopped, ProcessInfo.processInfo.systemUptime < deadline else {
            return ["type": "error", "error": "input_cancelled_or_expired", "effect": "not_started"]
        }
        var failures = 0
        for menu in openMenus where AXUIElementPerformAction(menu, "AXCancel" as CFString) != .success {
            failures += 1
        }
        Thread.sleep(forTimeInterval: 0.15)
        let remaining = openContextMenus(pid: p).count
        if failures == openMenus.count {
            return ["type": "error", "error": "action_failed", "effect": "unknown",
                    "detail": "메뉴가 AXCancel 을 받지 않았습니다", "menus_open": remaining]
        }
        return ["type": "acted", "action": name, "obs_id": obsID, "menus_closed": openMenus.count - remaining,
                "menus_open": remaining,
                "note": remaining == 0 ? "menus closed" : "not verified — a menu is still open"]
    case "open_folder":
        // Finder's Go to Folder sheet cannot be confirmed from the background,
        // and Go > Go to Folder… brings Finder forward (measured 2026-10-08).
        // LaunchServices can show a folder in Finder without activating it
        // (measured: frontmost unchanged). Only an existing, real folder —
        // never a package or app, which "opening" would launch.
        guard Set(action.keys) == Set(["name", "path"]), let raw = action["path"] as? String,
              raw.hasPrefix("/"), !raw.contains("\u{0}") else {
            return ["type": "error", "error": "action_failed", "effect": "not_started",
                    "detail": "open_folder 에는 절대 경로 하나가 필요합니다"]
        }
        guard target.lowercased() == "com.apple.finder" else {
            return ["type": "error", "error": "action_failed", "effect": "not_started",
                    "detail": "open_folder 는 Finder 에만 쓸 수 있습니다"]
        }
        let folder = URL(fileURLWithPath: raw).standardizedFileURL.resolvingSymlinksInPath()
        var isDirectory: ObjCBool = false
        guard FileManager.default.fileExists(atPath: folder.path, isDirectory: &isDirectory),
              isDirectory.boolValue,
              let kind = try? folder.resourceValues(forKeys: [.isPackageKey, .isApplicationKey]),
              kind.isPackage == false, kind.isApplication != true else {
            return ["type": "error", "error": "action_failed", "effect": "not_started",
                    "detail": "존재하는 일반 폴더가 아닙니다 (패키지·앱은 열지 않습니다)"]
        }
        guard let finderApp = NSWorkspace.shared.urlForApplication(withBundleIdentifier: "com.apple.finder") else {
            return ["type": "error", "error": "action_failed", "effect": "not_started",
                    "detail": "Finder 를 찾지 못했습니다"]
        }
        let wasFrontForOpen = isFrontmost(pid: p)
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.activates = false
        configuration.addsToRecentItems = false
        configuration.promptsUserIfNeeded = false
        // The stop/deadline check sits inside openFolderBounded, immediately
        // before the request; it waits no longer than the deadline, and does
        // not claim a cancellation it cannot make (Codex review of 74f33e2).
        switch openFolderBounded(deadline: deadline, stopped: { abortState.isStopped }, request: { done in
            NSWorkspace.shared.open([folder], withApplicationAt: finderApp, configuration: configuration) { _, error in
                done(error == nil)
            }
        }) {
        case .notStarted(let why):
            return ["type": "error", "error": abortState.isStopped ? "aborted_by_user" : "input_deadline_expired",
                    "effect": "not_started", "detail": "폴더를 열기 전에 멈췄습니다: \(why)"]
        case .unknown(let why):
            return ["type": "error", "error": "action_failed", "effect": "unknown",
                    "detail": "Finder 가 폴더를 열었는지 확인하지 못했습니다: \(why)"]
        case .opened:
            break
        }
        var activatedByOpen = false
        if !wasFrontForOpen {
            for _ in 0..<5 {
                guard !abortState.isStopped,
                      deadline - ProcessInfo.processInfo.systemUptime > 0.1 else { break }
                Thread.sleep(forTimeInterval: 0.1)
                if isFrontmost(pid: p) { activatedByOpen = true; break }
            }
        }
        // Like menu: the press is reported and the flow's postcondition (a
        // fresh observation) decides whether the folder is showing.
        var opened: [String: Any] = ["type": "acted", "action": name, "obs_id": obsID,
                                     "folder": folder.path, "note": "not verified — observe again"]
        if activatedByOpen {
            opened["activated_target"] = true
            opened["note"] = "not verified — observe again; Finder was observed in front after opening"
        }
        return opened
    case "select_option":
        // Open the menu, read it, press the item, and check the control reads
        // back as what was asked for. The same path a person takes, with every
        // step of it checked.
        guard let el = element() else {
            return ["type": "error", "error": "action_failed",
                    "effect": "not_started",
                    "detail": "select_option 에는 element_index 가 필요합니다"]
        }
        guard let want = action["option_label"] as? String, !want.isEmpty else {
            return ["type": "error", "error": "action_failed",
                    "effect": "not_started",
                    "detail": "select_option 에 option_label 이 필요합니다"]
        }
        if let value = action["value"] as? String, value != want {
            // A native menu item has a title and nothing behind it. Accepting
            // a different `value` would promise a distinction this surface
            // does not have.
            return ["type": "error", "error": "action_failed",
                    "effect": "not_started",
                    "detail": "이 화면의 선택지에는 값이 따로 없습니다 — "
                              + "value(\(value)) 와 option_label(\(want)) 이 "
                              + "달라 무엇을 고를지 알 수 없습니다"]
        }
        let role = str(el, kAXRoleAttribute as String) ?? ""
        // `AXMenuButton` is deliberately absent: it is usually a COMMAND menu
        // — Finder's Actions offers Delete and Share — and choosing "Delete"
        // from one is not picking a value, it is running a command. Sharing an
        // action would have it classified by the parent control's label, since
        // the effect classifier never sees which option was chosen (peer
        // review). Commands keep the `menu` path, which names what it invokes.
        guard ["AXPopUpButton", "AXComboBox"].contains(role) else {
            return ["type": "error", "error": "action_failed",
                    "effect": "not_started",
                    "detail": "선택지를 가진 컨트롤이 아닙니다: \(role)"]
        }
        if (attr(el, kAXEnabledAttribute as String) as? Bool) == false {
            return ["type": "error", "error": "action_failed",
                    "effect": "not_started", "detail": "이 컨트롤은 지금 쓸 수 없습니다"]
        }
        if let busy = menuSlotBusy() {
            return ["type": "error", "error": "action_failed",
                    "effect": "not_started", "detail": busy]
        }
        // Captured before anything is pressed: the control may not survive
        // being used. Only what does NOT move with the value — a description
        // like "column view" becomes "icon view" and stops naming the control.
        let identity = str(el, "AXIdentifier")
        // Called directly, the way `menu` does it: wrapping this in
        // `DispatchQueue.main.sync` would deadlock if we are already on main.
        // A popup posts its menu while its app is frontmost. We do not make
        // that true — raising the app is the thing this helper no longer does —
        // so the attempt is made as-is and the refusal names the reason.
        let wasFront = isFrontmost(pid: p)
        // CFEqual, not object identity: each AX call can hand back a NEW
        // wrapper around the same element, so comparing wrappers compares
        // nothing about the menus (peer review).
        if role=="AXComboBox" {
            switch comboBoxContainerState(el,p) {
            case .closed:break
            case .open:
                return ["type":"error","error":"selection_already_open","effect":"not_started",
                        "detail":"이미 열려 있는 ComboBox 목록을 다시 누르지 않습니다"]
            case .unknown:
                return ["type":"error","error":"selection_state_unavailable","effect":"not_started",
                        "detail":"ComboBox 목록 상태를 확인할 수 없어 열지 않았습니다"]
            }
        }
        let before = appMenus(p)
        var opened = AXUIElementPerformAction(el, "AXShowMenu" as CFString)
        if opened == AXError(rawValue: -25206) {
            // actionUnsupported: provably nothing happened — this element has
            // no AXShowMenu — so trying the other way is not a second attempt
            // at the same thing.
            opened = AXUIElementPerformAction(el, kAXPressAction as CFString)
        }
        if opened == AXError(rawValue:-25206),let button=comboBoxOpenButton(el,p) {
            opened=AXUIElementPerformAction(button,kAXPressAction as CFString)
        }
        if opened == AXError(rawValue: -25204) {
            // A timeout is not a failure: the callback may have done the work
            // and not returned in time. Pressing again "to be safe" is how one
            // open becomes two — the ambiguous duplicate `click` already
            // refuses to commit (peer review).
            return ["type": "error", "error": "action_failed",
                    "effect": "unknown", "reobserve_required": true,
                    "detail": "선택지를 여는 동작이 시간 내에 끝나지 않았습니다 — "
                              + "열렸을 수도 있어 다시 누르지 않았습니다"]
        }
        guard opened == .success else {
            // Unexplained is not "nothing happened".
            return ["type": "error", "error": "action_failed",
                    "effect": "unknown", "reobserve_required": true,
                    "detail": "선택지를 열지 못했습니다 (\(opened.rawValue))"]
        }
        usleep(400_000)
        let appeared = appMenus(p).filter { candidate in
            !before.contains(where: { CFEqual($0, candidate) })
        }
        if appeared.isEmpty {
            // The action reported success and nothing appeared. Whether the UI
            // moved is precisely what we cannot say — something was pressed.
            return ["type": "error", "error": "action_failed",
                    "effect": "unknown", "reobserve_required": true,
                    "needs_foreground": !wasFront,
                    "detail": "선택지를 열었다는데 목록이 나타나지 않았습니다"
                              + (wasFront ? "" : " (이 앱이 앞에 있지 않습니다)")]
        }
        if appeared.count > 1 {
            // Taking the first would be choosing at random which menu this
            // control owns (peer review) — and so would cancelling them all.
            // Nothing is dismissed: which of them belongs here is exactly what
            // could not be worked out.
            return ["type": "error", "error": "action_failed", "effect": "unknown",
                    "reobserve_required": true, "menus_opened": appeared.count,
                    "menu_left_open": true, "option_selected": false,
                    "selections_made": 0,
                    "detail": "메뉴가 \(appeared.count)개 열려 어느 것이 이 "
                              + "컨트롤의 것인지 알 수 없습니다"]
        }
        let menu = appeared[0]
        // Tracked from the moment there is exactly one menu and we know which.
        // Waiting until just before the press left every refusal in between
        // describing an open menu as a closed popup (peer review).
        spentMenu = (pid: p, control: el, menu: menu,popupWindowID:comboBoxPopupWindowID(menu),processStart:latestProcessStart)

        /// Refuse, and leave the menu exactly where this action put it.
        ///
        /// This used to cancel the menu and then report `not_started` when the
        /// cancel succeeded. Both halves were wrong. In the one measured case,
        /// an automatic cancel from here was followed by the control refusing
        /// to post a menu again (native-select-blocker.md). And `not_started`
        /// was a false claim: THIS action opened the menu, so the screen is
        /// not where it was, whatever happened to the selection.
        func refuseAfterOpening(_ why: String) -> [String: Any] {
            ["type": "error", "error": "action_failed", "effect": "unknown",
             "reobserve_required": true, "menu_opened_by_this_action": true,
             "menu_left_open": true, "option_selected": false,
             "selections_made": 0, "detail": why]
        }
        /// Same, for a failure AFTER the item was pressed. Nothing here may
        /// say how many selections happened: the press was attempted and its
        /// outcome is precisely what the error leaves unknown.
        func refuseAfterPress(_ why: String) -> [String: Any] {
            ["type": "error", "error": "action_failed", "effect": "unknown",
             "reobserve_required": true, "menu_opened_by_this_action": true,
             "press_attempted": true,
             "menu_left_open": appMenus(p).contains { CFEqual($0, menu) },
             "detail": why]
        }

        var titles: [String] = []
        var matches: [AXUIElement] = []
        for item in (attr(menu, kAXChildrenAttribute as String) as? [AXUIElement]) ?? [] {
            guard let t = str(item, kAXTitleAttribute as String), !t.isEmpty else { continue }
            titles.append(t)
            if t == want { matches.append(item) }
        }
        if matches.isEmpty {
            return refuseAfterOpening("'\(want)' 라는 선택지가 없습니다 — 있는 것: "
                                      + titles.prefix(12).joined(separator: ", "))
        }
        if matches.count > 1 {
            return refuseAfterOpening("'\(want)' 라는 선택지가 \(matches.count)개라 "
                                      + "어느 것인지 알 수 없습니다")
        }
        if (attr(matches[0], kAXEnabledAttribute as String) as? Bool) == false {
            return refuseAfterOpening("'\(want)' 는 지금 고를 수 없습니다")
        }
        // The menu has been on screen for a moment and the person may have
        // stopped the run in it. Checked again HERE, not only before it opened:
        // the whole point of the stop is that it lands between the thing being
        // offered and the thing being done (peer review).
        if abortState.isStopped {
            return ["type": "error", "error": "aborted_by_user",
                    "effect": "unknown", "reobserve_required": true,
                    "menu_opened_by_this_action": true, "menu_left_open": true,
                    "option_selected": false, "selections_made": 0,
                    "detail": "선택지를 여는 사이에 사용자가 중단했습니다"]
        }
        let pressed = AXUIElementPerformAction(matches[0], kAXPressAction as CFString)
        if pressed != .success, pressed != AXError(rawValue: -25204) {
            return refuseAfterPress("'\(want)' 를 고르지 못했습니다 (\(pressed.rawValue))")
        }
        usleep(300_000)
        // The menu normally closes itself when an item is pressed. If this one
        // did not, it is still this control's menu — reported, never dismissed.
        let stillOpen = appMenus(p).contains { CFEqual($0, menu) }
        if !stillOpen { spentMenu = nil }
        let now = rereadSelection(p, role: role, identity: identity, control: el)
        if now == want {
            var ok: [String: Any] = ["type": "acted", "action": name,
                                     "obs_id": obsID, "selected": want]
            if stillOpen {
                ok["menu_left_open"] = true
                ok["note"] = "고른 뒤에도 메뉴가 남아 있습니다 — 자동으로 닫지 않았습니다"
            }
            return ok
        }
        // Pressed, and it does not read back as asked. Not "did not happen" —
        // it happened and did not take, or this control names its value
        // differently from its menu.
        return ["type": "acted", "action": name, "obs_id": obsID,
                "selected": want, "unverified": "selection_unconfirmed",
                "reads_as": (now as Any?) ?? NSNull(), "menu_left_open": stillOpen,
                "note": "'\(want)' 를 눌렀는데 컨트롤이 그렇게 읽히지 않습니다"]
    case "open_select":
        // Open a popup's menu and stop there. Stopping is the point: what the
        // menu contains is not knowable until it is open, so this buys exactly
        // one observation's worth of knowledge and nothing else. It does not
        // choose, and it does not report what it found — a reading taken here
        // would be one the caller never saw.
        guard let el = element() else {
            return ["type": "error", "error": "action_failed",
                    "effect": "not_started",
                    "detail": "open_select 에는 element_index 가 필요합니다"]
        }
        let openingScope:NativeWindowReadScope
        let role:String,openingIdentifier:String?,openingLabel:String,openingEnabled:CFTypeRef?
        do {
            openingScope=try initialSelectionReadScope(obsID,target,el,p,deadline:deadline)
            role=try selectionLookupString(el,kAXRoleAttribute as String,scope:openingScope) ?? ""
            openingEnabled=try selectionLookupAttribute(el,kAXEnabledAttribute as String,scope:openingScope)
            if let enabled=openingEnabled,CFGetTypeID(enabled) != CFBooleanGetTypeID() {
                throw NativeWindowInputError.unavailable("selection enabled state malformed")
            }
            openingIdentifier=try selectionLookupString(el,"AXIdentifier",scope:openingScope)
            openingLabel=try selectionLookupLabel(el,scope:openingScope)
            try openingScope.ready()
        } catch {
            return ["type":"error","error":"selection_state_unavailable","effect":"not_started",
                    "reobserve_required":true,"detail":"선택지를 열기 전 관측 상태나 읽기를 확인할 수 없습니다"]
        }
        guard ["AXPopUpButton", "AXComboBox"].contains(role) else {
            return ["type": "error", "error": "action_failed",
                    "effect": "not_started",
                    "detail": "선택지를 가진 컨트롤이 아닙니다: \(role)"]
        }
        if (openingEnabled as? Bool) == false {
            return ["type": "error", "error": "action_failed",
                    "effect": "not_started", "detail": "이 컨트롤은 지금 쓸 수 없습니다"]
        }
        if let busy = menuSlotBusy() {
            return ["type": "error", "error": "action_failed",
                    "effect": "not_started", "detail": busy]
        }
        // Same as above: reported, never arranged.
        if role=="AXComboBox" {
            switch comboBoxContainerState(el,p,scope:openingScope) {
            case .closed:break
            case .open:
                return ["type":"error","error":"selection_already_open","effect":"not_started",
                        "detail":"이미 열려 있는 ComboBox 목록을 다시 누르지 않습니다"]
            case .unknown:
                return ["type":"error","error":"selection_state_unavailable","effect":"not_started",
                        "detail":"ComboBox 목록 상태를 확인할 수 없어 열지 않았습니다"]
            }
        }
        let wasFront = isFrontmost(pid: p)
        let before:[AXUIElement]
        do {
            before=role=="AXComboBox" ? []:try selectionOpeningMenus(p,scope:openingScope)
            try openingScope.ready()
        } catch {
            return ["type":"error","error":"selection_state_unavailable","effect":"not_started",
                    "reobserve_required":true,"detail":"선택지를 열기 전 관측 상태나 읽기 기한이 바뀌었습니다"]
        }
        let openingAction=action["opening_action"] as? String ?? "AXShowMenu"
        var showed:AXError
        if openingAction=="AXPress" {
            var attempted=false
            do {showed=try openingScope.withTimeout(el) {attempted=true;return AXUIElementPerformAction(el,kAXPressAction as CFString)}}
            catch {return ["type":"error","error":"selection_state_unavailable","effect":attempted ? "unknown":"not_started",
                           "reobserve_required":true,"detail":"명시적 opening의 관측 또는 기한을 확인하지 못했습니다"]}
            if showed == .actionUnsupported {
                return ["type":"error","error":"action_failed","effect":"not_started",
                        "detail":"명시적 AXPress opening이 지원되지 않아 다른 동작을 시도하지 않았습니다"]
            }
        } else {showed=AXUIElementPerformAction(el,"AXShowMenu" as CFString)}
        if showed == AXError(rawValue: -25206) {
            // actionUnsupported: provably nothing happened — this element has
            // no AXShowMenu — so trying the other way is not a second attempt
            // at the same thing.
            do {try openingScope.ready()} catch {
                return ["type":"error","error":"selection_state_unavailable","effect":"not_started",
                        "reobserve_required":true,"detail":"지원되지 않는 action 뒤 관측 상태가 바뀌어 입력하지 않았습니다"]
            }
            showed = AXUIElementPerformAction(el, kAXPressAction as CFString)
        }
        if showed == AXError(rawValue:-25206),let button=comboBoxOpenButton(el,p) {
            do {try openingScope.ready()} catch {
                return ["type":"error","error":"selection_state_unavailable","effect":"not_started",
                        "reobserve_required":true,"detail":"ComboBox 버튼을 읽은 뒤 관측 상태가 바뀌어 입력하지 않았습니다"]
            }
            showed=AXUIElementPerformAction(button,kAXPressAction as CFString)
        }
        if showed == AXError(rawValue: -25204) {
            // A timeout is not a failure: the callback may have done the work
            // and not returned in time. Pressing again "to be safe" is how one
            // open becomes two — the ambiguous duplicate `click` already
            // refuses to commit (peer review).
            return ["type": "error", "error": "action_failed",
                    "effect": "unknown", "reobserve_required": true,
                    "detail": "선택지를 여는 동작이 시간 내에 끝나지 않았습니다 — "
                              + "열렸을 수도 있어 다시 누르지 않았습니다"]
        }
        guard showed == .success else {
            // Unexplained is not "nothing happened".
            return ["type": "error", "error": "action_failed",
                    "effect": "unknown", "reobserve_required": true,
                    "detail": "선택지를 열지 못했습니다 (\(showed.rawValue))"]
        }
        usleep(400_000)                       // drawn, then populated
        let fresh:[AXUIElement]
        do {
            if role=="AXComboBox" {
                guard let list=comboBoxOwnedList(el,p,scope:openingScope),
                      comboBoxListVisibility(list,p,scope:openingScope)==true else {
                    throw NativeWindowInputError.unavailable("selection list unavailable")
                }
                fresh=[list]
            } else {
                fresh=try selectionOpeningMenus(p,scope:openingScope).filter { candidate in
                    !before.contains(where:{CFEqual($0,candidate)})
                }
            }
            try openingScope.ready()
        } catch {
            return ["type":"error","error":"selection_state_unavailable","effect":"unknown",
                    "reobserve_required":true,"detail":"입력 뒤 선택지 목록을 완전히 확인할 수 없어 다시 입력하지 않았습니다"]
        }
        if fresh.isEmpty {
            return ["type": "error", "error": "action_failed",
                    "effect": "unknown", "reobserve_required": true,
                    "needs_foreground": !wasFront,
                    "detail": "선택지를 열었다는데 목록이 나타나지 않았습니다"
                              + (wasFront ? "" : " (이 앱이 앞에 있지 않습니다)")]
        }
        if fresh.count > 1 {
            // Not dismissed, for the same reason: we could not attribute them.
            return ["type": "error", "error": "action_failed",
                    "effect": "unknown", "reobserve_required": true,
                    "menus_opened": fresh.count, "menu_left_open": true,
                    "option_selected": false, "selections_made": 0,
                    "detail": "메뉴가 \(fresh.count)개 열려 어느 것이 이 "
                              + "컨트롤의 것인지 알 수 없습니다"]
        }
        pendingSelect = PendingSelect(
            pid: p, index: action["element_index"] as? Int ?? -1, control: el,
            identifier: openingIdentifier, role: role,
            label: openingLabel, menu: fresh[0],processStart:latestProcessStart)
        // NOT `unverified`. Exactly one menu appeared and we hold its identity,
        // so what this action set out to do is confirmed — and a caller that
        // treats `unverified` as a failure would stop the very loop this
        // action exists to start (peer review). `opened` says the menu is up;
        // it is deliberately a different word from the one a completed
        // selection answers with.
        return ["type": "acted", "action": name, "obs_id": obsID,
                "opened": true, "reobserve_required": true,
                "note": "선택지를 열었습니다 — 다시 관측한 뒤 그 화면에서 고르세요"]
    case "select_custom_option":
        // Choose from the menu THIS helper opened, as the caller last saw it.
        //
        // Two kinds of refusal, and the difference is whether the thing we
        // pinned is still there. `keep` means the pin is good and the caller
        // can fix its request — re-observe and name an option that exists;
        // the menu stays open so the corrected attempt has something to choose
        // from. `drop` means what we pinned is not on screen any more, so the
        // reference is worthless and we stop tracking it.
        //
        // Neither dismisses anything, and neither presses anything: every
        // refusal below happens before the press, so zero selections is a fact
        // and not a guess.
        guard let want = action["option_label"] as? String, !want.isEmpty else {
            return ["type": "error", "error": "action_failed",
                    "effect": "not_started",
                    "detail": "select_custom_option 에 option_label 이 필요합니다"]
        }
        if let value = action["value"] as? String, value != want {
            return ["type": "error", "error": "action_failed",
                    "effect": "not_started",
                    "detail": "이 화면의 선택지에는 값이 따로 없습니다 — "
                              + "value(\(value)) 와 option_label(\(want)) 이 "
                              + "달라 무엇을 고를지 알 수 없습니다"]
        }
        guard let pending = pendingSelect else {
            return ["type": "error", "error": "action_failed",
                    "effect": "not_started",
                    "detail": "열어 둔 선택지가 없습니다 — open_select 로 먼저 여세요"]
        }
        guard pending.optionsObsID == obsID, let shown = pending.options else {
            // Opened, then chosen from without looking. The options this
            // choice names came from nowhere the caller was shown.
            return keep("선택지를 연 뒤 화면을 다시 관측하지 않았습니다 — "
                        + "관측한 목록에서만 고를 수 있습니다")
        }
        /// The pin is still good; the request was not. Nothing is dismissed.
        func keep(_ why: String) -> [String: Any] {
            ["type": "error", "error": "action_failed", "effect": "not_started",
             "menu_open": true, "selections_made": 0, "pin_kept": true,
             "retry_after_reobserve": true, "reobserve_required": true,
             "detail": why]
        }
        /// What we pinned is gone. We stop tracking it and dismiss nothing.
        func drop(_ why: String) -> [String: Any] {
            forgetPin()
            return ["type": "error", "error": "action_failed",
                    "effect": "not_started", "selections_made": 0,
                    "pin_kept": false, "retry_after_reobserve": false,
                    "reobserve_required": true, "detail": why]
        }
        guard let index = action["element_index"] as? Int, index == pending.index else {
            return keep("열어 둔 선택지는 \(pending.index) 번 요소의 것입니다")
        }
        guard let el = element(), CFEqual(el, pending.control) else {
            return drop("선택지를 연 컨트롤이 그 자리에 없습니다")
        }
        if p != pending.pid {
            return drop("선택지를 연 앱과 고르려는 앱이 다릅니다")
        }
        let lookupScope:NativeWindowReadScope
        do {lookupScope=try initialSelectionReadScope(obsID,target,el,p,deadline:deadline)}
        catch {return keep("선택 전 관측 상태나 읽기 기한을 확인할 수 없습니다")}
        let controlIdentifier:String?,controlLabel:String,controlEnabled:CFTypeRef?
        let containerIsOurs:Bool
        do {
            controlIdentifier=try selectionLookupString(el,"AXIdentifier",scope:lookupScope)
            controlLabel=try selectionLookupLabel(el,scope:lookupScope)
            controlEnabled=try selectionLookupAttribute(el,kAXEnabledAttribute as String,scope:lookupScope)
            containerIsOurs=try menuIsOurs(pending.pid,pending,scope:lookupScope)
            try lookupScope.ready()
        } catch {return keep("선택 전 컨트롤이나 목록의 읽기를 확인할 수 없습니다")}
        if controlIdentifier != pending.identifier {
            return drop("컨트롤의 식별자가 바뀌었습니다")
        }
        if controlLabel != pending.label {
            return drop("컨트롤의 이름이 바뀌었습니다")
        }
        if (controlEnabled as? Bool) == false {
            return keep("이 컨트롤은 지금 쓸 수 없습니다")
        }
        if !containerIsOurs {
            return drop("열어 둔 선택지 목록이 사라졌거나 다른 목록으로 바뀌었습니다")
        }
        // Not "the item we want is still there" — the whole list. An item
        // appearing above the one we mean moves nothing we can see, and is
        // exactly how a choice lands on the wrong row.
        guard let now = menuOptions(pending.menu,scope:lookupScope), sameOptions(now, shown) else {
            return keep("관측한 뒤 선택지 목록이 달라졌습니다")
        }
        guard let menuRole=comboBoxString(pending.menu,kAXRoleAttribute as String,scope:lookupScope) else {
            return keep("선택지 목록 상태를 확인할 수 없습니다")
        }
        let isComboList=menuRole=="AXList"
        let items:[AXUIElement]
        if isComboList {
            guard let rows=comboBoxListRows(pending.menu,scope:lookupScope) else { return keep("ComboBox 목록을 확인할 수 없습니다") }
            items=rows.filter {$0.label==want}.map {$0.node}
        } else {
            items=((comboBoxAttribute(pending.menu,kAXChildrenAttribute as String,scope:lookupScope) as? [AXUIElement]) ?? [])
                .filter {comboBoxString($0,kAXTitleAttribute as String,scope:lookupScope)==want}
        }
        if items.isEmpty {
            let here = shown.prefix(12).compactMap { $0["label"] as? String }.joined(separator: ", ")
            return keep("'\(want)' 라는 선택지가 없습니다 — 있는 것: "
                        + (here.isEmpty ? "(없음)" : here))
        }
        if items.count > 1 {
            return keep("'\(want)' 라는 선택지가 \(items.count)개라 어느 것인지 알 수 없습니다")
        }
        if (comboBoxAttribute(items[0],kAXEnabledAttribute as String,scope:lookupScope) as? Bool) == false {
            return keep("'\(want)' 는 지금 고를 수 없습니다")
        }
        if isComboList {
            guard let pinnedID=pending.popupWindowID,
                  case .open(let live)=comboBoxContainerState(el,p,scope:lookupScope),CFEqual(live,pending.menu),
                  comboBoxPopupWindowID(live,scope:lookupScope)==pinnedID else {
                return keep("ComboBox 목록 상태를 확인할 수 없어 선택하지 않았습니다")
                    .merging(["error":"selection_state_unavailable"]) {_,new in new}
            }
            guard let before=rereadSelection(p,role:pending.role,identity:pending.identifier,control:el,scope:lookupScope) else {
                return keep("ComboBox initial selection unavailable")
            }
            if before==want {
                return keep("이미 선택된 값이라 선택 입력을 보내지 않았습니다")
                    .merging(["error":"already_selected"]) {_,new in new}
            }
            guard let raw=comboBoxAttribute(pending.menu,kAXParentAttribute as String,scope:lookupScope),
                  CFGetTypeID(raw)==AXUIElementGetTypeID() else {
                return keep("ComboBox popup parent unavailable")
            }
            let popup=raw as! AXUIElement,chosen=items[0],menu=pending.menu
            func selectionCheck(_ scope:NativeWindowReadScope) throws {
                guard case .open(let live)=comboBoxContainerState(el,p,scope:scope),CFEqual(live,menu),
                      comboBoxPopupWindowID(live,scope:scope)==pinnedID,
                      let options=menuOptions(menu,scope:scope),sameOptions(options,shown),
                      let rows=comboBoxListRows(menu,scope:scope) else {
                    throw NativeWindowInputError.refused("pinned ComboBox list changed")
                }
                let matches=rows.filter {$0.label==want}
                guard matches.count==1,!matches[0].disabled,CFEqual(matches[0].node,chosen),
                      rereadSelection(p,role:pending.role,identity:pending.identifier,control:el,scope:scope)==before,
                      let parent=comboBoxAttribute(menu,kAXParentAttribute as String,scope:scope),
                      CFGetTypeID(parent)==AXUIElementGetTypeID(),CFEqual(parent,popup) else {
                    throw NativeWindowInputError.refused("pinned ComboBox option changed")
                }
            }
            let result=prepareBackgroundWindow(obsID,target,chosen,p,deadline:deadline,
                elementClicks:1,popup:popup,observedControl:el,selectionCheck:selectionCheck,finalTransition:{checkpoint,scope in
                    try confirmComboBoxSelectionTransition(el,menu,p,role:pending.role,
                        identity:pending.identifier,want:want,scope:scope,checkpoint:checkpoint)
                })
            if result["type"] as? String != "acted" {
                if result["effect"] as? String=="not_started" {
                    return result.merging(["pin_kept":true,"menu_open":true,"selections_made":0]) {_,new in new}
                }
                forgetPin();spentMenu=(pid:p,control:el,menu:menu,popupWindowID:pending.popupWindowID,processStart:pending.processStart)
                return result.merging(["pin_kept":false,"reobserve_required":true]) {_,new in new}
            }
            forgetPin()
            // Both mouse events and the control's receiving transition were
            // checked; no highlight setter, Escape, activation or retry.
            var verified=result
            verified.removeValue(forKey:"unverified")
            verified["action"]=name;verified["selected"]=want
            verified["input_route"]="combo_popup_bound_click"
            verified["selection_transition_verified"]=true
            return verified
        }
        // Checked HERE, with the menu on screen and the press one line away:
        // the whole point of the stop is that it lands between the thing being
        // offered and the thing being done.
        if abortState.isStopped {
            return ["type": "error", "error": "aborted_by_user",
                    "effect": "not_started", "menu_open": true,
                    "selections_made": 0, "pin_kept": true,
                    "retry_after_reobserve": true, "reobserve_required": true,
                    "detail": "고르기 직전에 사용자가 중단했습니다"]
        }
        do {try lookupScope.ready()}
        catch {return keep("고르기 전 관측 상태나 읽기 기한이 바뀌었습니다")}
        let menu = pending.menu, chosenRole = pending.role, identity = pending.identifier
        forgetPin()                           // spent, whatever happens next
        // Recorded BEFORE the press. If the press fails we may not be able to
        // say whether the menu is still up, and a menu this helper opened must
        // never be described to the next observation as closed. The next
        // observation of this app drops it once it is really gone.
        spentMenu = (pid: p, control: el, menu: menu,popupWindowID:pending.popupWindowID,processStart:pending.processStart)
        let pressed=AXUIElementPerformAction(items[0],kAXPressAction as CFString)
        if pressed != .success, pressed != AXError(rawValue: -25204) {
            // A backend error is not "no effect". The press was ALREADY
            // attempted, so how many selections happened is not something this
            // code knows — claiming zero would be inventing evidence. Only the
            // attempt is certain; whether the menu is still up is re-read.
            // Nothing is dismissed.
            var facts: [String: Any] = ["type": "error", "error": "action_failed",
                                        "effect": "unknown", "press_attempted": true,
                                        "pin_kept": false, "reobserve_required": true,
                                        "detail": "'\(want)' 를 고르지 못했습니다 (\(pressed.rawValue))"]
            facts["menu_left_open"] = selectionContainerIsOurs(el,menu,p)
            return facts
        }
        usleep(300_000)
        let stillOpen = selectionContainerIsOurs(el,menu,p)
        if !stillOpen { spentMenu = nil }     // it closed itself, as menus do
        let readsAs = rereadSelection(p, role: chosenRole, identity: identity, control: el)
        if readsAs == want {
            // Reported, not tidied. A menu that stays up after a successful
            // choice is a fact about the screen the caller has to know, and
            // this helper does not cancel it (see forgetPin).
            var ok: [String: Any] = ["type": "acted", "action": name,
                                     "obs_id": obsID, "selected": want]
            if stillOpen {
                ok["menu_left_open"] = true
                ok["note"] = "고른 뒤에도 메뉴가 남아 있습니다 — 자동으로 닫지 않았습니다"
            }
            return ok
        }
        // Pressed, and the control does not read back as asked. Not "did not
        // happen" — it happened and did not take, or this control names its
        // value differently from its menu.
        return ["type": "acted", "action": name, "obs_id": obsID,
                "selected": want, "unverified": "selection_unconfirmed",
                "reads_as": (readsAs as Any?) ?? NSNull(), "menu_left_open": stillOpen,
                "note": "'\(want)' 를 눌렀는데 컨트롤이 그렇게 읽히지 않습니다"]
    case "press_key":
        guard Set(action.keys) == Set(["name", "element_index", "key"]),
              let key = action["key"] as? String,
              nativeNavigationKey(key) != nil || nativeSubmitKey(key) != nil,
              let index = action["element_index"] as? NSNumber,
              CFGetTypeID(index) != CFBooleanGetTypeID(), index.doubleValue.isFinite,
              index.doubleValue.rounded(.towardZero) == index.doubleValue,
              index.doubleValue >= 0, index.doubleValue < Double(elements.count),
              let el = element() else {
            return ["type":"error", "error":"action_failed", "effect":"not_started",
                    "detail":"press_key requires an observed input element and Tab/shift+Tab/arrows, or Return in the Go to Folder path field"]
        }
        if key == "Return", let result = confirmSafariAddress(obsID,target,el,p,
                deadline:deadline,expect:expect,submitAuthorized:submitAuthorized) {
            return result
        }
        // Return commits — it sends a message, submits a form, saves. So it
        // is taken only into the Go to Folder sheet's path field (see
        // returnTargetRefusal), where it navigates. The flow must also declare
        // the step a submit; see replay's press_key contract.
        let submit = nativeSubmitKey(key) != nil
        if submit, let why = returnTargetRefusal(el, read: boundedAXRead(until: deadline)) {
            // Elsewhere only when the host vouched for the step — declared a
            // submit, bound, with a postcondition that did not yet hold — and
            // only into a plain text input or a selected list row (a chat's
            // message field, a room list; 2026-10-10).
            guard submitAuthorized else {
                return ["type":"error", "error":"action_failed", "effect":"not_started",
                        "detail":"Return refused: \(why)"]
            }
            if let refusal = boundReturnTargetRefusal(el, read: boundedAXRead(until: deadline)) {
                return ["type":"error", "error":"action_failed", "effect":"not_started",
                        "detail":"Return refused: \(refusal)"]
            }
            return prepareBackgroundWindow(obsID, target, el, p, deadline: deadline,
                                           key: key, expectation: expect, submitKey: true)
        }
        if submit {
            return submitReturnBackground(obsID, target, el, p, deadline: deadline)
        }
        return prepareBackgroundWindow(obsID, target, el, p, deadline: deadline,
                                       key: key, expectation: expect)
    case "scroll":
        // A wheel event has no address: it goes to whatever sits under a
        // pointer position we invented, in a process we named. A scroll bar
        // is a real element with a real value, so this asks the app to be
        // where we want instead of miming a gesture at it. `AXValue` on a
        // scroll bar runs 0.0 at the top to 1.0 at the bottom; a page is a
        // tenth, matching the ten lines the wheel event used.
        guard let el = element() else {
            return ["type": "error", "error": "action_failed",
                    "effect": "not_started",
                    "detail": "scroll 에는 element_index 가 필요합니다"]
        }
        guard let number=(action["pages"] ?? NSNumber(value:1)) as? NSNumber,
              CFGetTypeID(number)==CFNumberGetTypeID(),
              !CFNumberIsFloatType(unsafeBitCast(number,to:CFNumber.self)),
              number.doubleValue>=1,number.doubleValue<=10 else {
            return ["type":"error","error":"action_failed","effect":"not_started",
                    "detail":"스크롤 페이지 수는 1~10 정수여야 합니다"]
        }
        let pages=number.intValue
        guard let dir=(action["direction"] ?? "down") as? String,
              ["up", "down", "left", "right"].contains(dir) else {
            return ["type": "error", "error": "action_failed",
                    "effect": "not_started", "detail": "스크롤 방향이 올바르지 않습니다"]
        }
        let wantVertical = dir == "up" || dir == "down"
        guard let bar = scrollBar(near: el, vertical: wantVertical,expectedPID:p) else {
            return ["type": "error", "error": "action_failed",
                    "effect": "not_started",
                    "detail": "\(wantVertical ? "세로" : "가로") 스크롤 막대를 "
                              + "찾지 못했습니다 — 이 요소는 그 방향으로 스크롤되지 않습니다"]
        }
        var barSettable = DarwinBoolean(false)
        guard AXUIElementIsAttributeSettable(bar, kAXValueAttribute as CFString,
                                             &barSettable) == .success,
              barSettable.boolValue,
              let raw=attr(bar,kAXValueAttribute as String),CFGetTypeID(raw)==CFNumberGetTypeID(),
              let current=raw as? Double,current.isFinite,current>=0,current<=1 else {
            return ["type": "error", "error": "action_failed",
                    "effect": "not_started",
                    "detail": "스크롤 막대의 값을 읽거나 바꿀 수 없습니다"]
        }
        if abortState.isStopped {
            return ["type": "error", "error": "aborted_by_user",
                    "effect": "not_started", "detail": "사용자가 중단했습니다"]
        }
        let step = 0.1 * Double(pages) * ((dir == "up" || dir == "left") ? -1 : 1)
        let wanted = min(1.0, max(0.0, current + step))
        let scrolled = AXUIElementSetAttributeValue(bar, kAXValueAttribute as CFString,
                                                    wanted as CFTypeRef)
        guard scrolled == .success else {
            // A setter can take effect before its reply times out. No failed
            // write receipt proves that the scroll did not happen.
            return ["type": "error", "error": "action_failed",
                    "effect": "unknown",
                    "detail": "스크롤에 실패했습니다 (\(scrolled.rawValue))"]
        }
    case "secondary_action":
        // Duplicated here on purpose: the helper holds the grant, so it does not
        // depend on a caller having filtered on its behalf.
        let safeAXActions: Set<String> = ["AXPress", "AXShowMenu", "AXIncrement",
                                          "AXDecrement", "AXConfirm", "AXCancel"]
        guard let el = element(), let a = action["action"] as? String,
              safeAXActions.contains(a) else {
            return ["type": "error", "error": "action_failed",
                    "effect": "not_started", "detail": "허용되지 않은 AX action"]
        }
        let secondaryError = AXUIElementPerformAction(el, a as CFString)
        if secondaryError != .success {
            return ["type": "error", "error": "action_failed",
                    "effect": axActionFailureEffect(secondaryError), "detail": "secondary action failed"]
        }
    default:
        return ["type": "error", "error": "action_failed",
                "effect": "not_started", "detail": "unknown action \(name)"]
    }
    return ["type": "acted", "action": name, "obs_id": obsID,
            "note": "not verified — observe again"]
}

// MARK: - L2 loop

// MARK: - The user is the authority
//
// Caller identity cannot protect this interface while the client is a shared
// Python interpreter: any script using that interpreter looks identical to Tacto,
// which is the very problem the helper exists to fix. What DOES hold regardless
// of who is calling is that the person can see what is happening and stop it.
// So the indicator and the abort key belong HERE, in the process that performs
// the actions — a caller cannot skip them by not showing the indicator.

// The stop itself lives in `abortState`, which holds it together with the
// moment this run began — the two only mean anything as a pair, and a second
// locked copy of "is this stopped" would be a second answer to the one
// question that must have one.
/// The menu-bar item that says this is happening. Nil when nothing is running.

/// A visible stop control for AX work, without capturing anyone's screen.
/// Never key/main, never moved across Spaces, and never activates the helper.
final class TactoControlPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}
final class TactoStopButton: NSButton {
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
}
var controlPanel: TactoControlPanel?
var controlPanelLabel: NSTextField?
var controlPanelDiagnostic: [String: Bool] = [:]

func controlPanelOnScreen() -> Bool {
    guard let panel = controlPanel else { return false }
    controlPanelDiagnostic = ["created":true,"visible":panel.isVisible,
        "active_space":panel.isOnActiveSpace,"not_minimized": !panel.isMiniaturized,
        "opaque":panel.alphaValue >= 0.99,"not_occluded":panel.occlusionState.contains(.visible),
        "inside_screen":NSScreen.screens.contains { $0.visibleFrame.contains(panel.frame) },
        "display_awake":NSScreen.screens.contains { screen in
            guard screen.visibleFrame.contains(panel.frame),
                  let number = screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber else { return false }
            return CGDisplayIsAsleep(number.uint32Value) == 0
        }]
    return controlPanelDiagnostic.values.allSatisfy { $0 }
}

/// Place the stop control wholly inside a screen and outside every target window.
func availableControlFrame(screens: [CGRect], avoiding targets: [CGRect], size: CGSize) -> CGRect? {
    for screen in screens {
        for origin in [CGPoint(x: screen.maxX-size.width-12, y: screen.maxY-size.height-12),
                       CGPoint(x: screen.minX+12, y: screen.maxY-size.height-12),
                       CGPoint(x: screen.maxX-size.width-12, y: screen.minY+12),
                       CGPoint(x: screen.minX+12, y: screen.minY+12)] {
            let candidate = CGRect(origin: origin, size: size)
            if screen.contains(candidate) && !targets.contains(where: { $0.intersects(candidate) }) {
                return candidate
            }
        }
    }
    return nil
}

/// Preserve the outside-target rule when only a narrow screen gutter is free.
/// The compact layout keeps a labeled Stop button; Esc and menu status remain.
func availableControlPanelFrame(screens: [CGRect], avoiding targets: [CGRect]) -> CGRect? {
    for width in [CGFloat(292), CGFloat(96)] {
        if let frame = availableControlFrame(screens:screens, avoiding:targets,
                                             size:CGSize(width:width,height:56)) { return frame }
    }
    return nil
}

func controlTargetFrames(forPID pid: pid_t, primaryTop: CGFloat) -> [CGRect]? {
    var listing = WindowListingDiagnostic()
    let windows = realWindows(of: AXUIElementCreateApplication(pid), diagnostic: &listing)
    guard ["ok","no_value"].contains(listing.status),
          !windows.isEmpty || appSaysItHasNoWindows(pid) else { return nil }
    var frames: [CGRect] = []
    for window in windows {
        guard let position = attr(window, kAXPositionAttribute as String),
              let size = attr(window, kAXSizeAttribute as String),
              CFGetTypeID(position) == AXValueGetTypeID(), CFGetTypeID(size) == AXValueGetTypeID(),
              AXValueGetType(position as! AXValue) == .cgPoint,
              AXValueGetType(size as! AXValue) == .cgSize else { return nil }
        var point = CGPoint.zero, dimensions = CGSize.zero
        guard AXValueGetValue(position as! AXValue, .cgPoint, &point),
              AXValueGetValue(size as! AXValue, .cgSize, &dimensions),
              point.x.isFinite, point.y.isFinite, dimensions.width.isFinite, dimensions.height.isFinite,
              dimensions.width > 0, dimensions.height > 0 else { return nil }
        // AX uses top-left global coordinates; AppKit uses bottom-left.
        frames.append(CGRect(x:point.x, y:primaryTop-point.y-dimensions.height,
                             width:dimensions.width, height:dimensions.height))
    }
    return frames
}

func showControlPanel(forPID pid: pid_t, targets: [CGRect]) -> Bool {
    // Every call says why it failed this time; a no-room reason left over from
    // an earlier call must not excuse a panel that is now on another Space.
    controlPanelDiagnostic = [:]
    let who = NSRunningApplication(processIdentifier: pid)?.localizedName ?? "앱"
    guard let frame = availableControlPanelFrame(screens: NSScreen.screens.map { $0.visibleFrame },
                            avoiding: targets) else {
        controlPanelDiagnostic=["frame_available":false];return false
    }
    let size = frame.size
    if let panel = controlPanel {
        guard panel.isOnActiveSpace else { controlPanelDiagnostic=["active_space":false]; return false }
        panel.setFrame(frame, display:true)
    }
    if controlPanel == nil {
        let panel = TactoControlPanel(contentRect: frame,
            styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        panel.level = .floating
        panel.hidesOnDeactivate = false
        panel.becomesKeyOnlyIfNeeded = true
        panel.isReleasedWhenClosed = false
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.collectionBehavior = [.fullScreenAuxiliary, .transient, .ignoresCycle]
        let content = NSView(frame: NSRect(origin: .zero, size: size))
        content.wantsLayer = true
        content.layer?.backgroundColor = NSColor.windowBackgroundColor.cgColor
        content.layer?.cornerRadius = 12
        content.layer?.borderWidth = 1
        content.layer?.borderColor = NSColor.separatorColor.cgColor
        let title = NSTextField(labelWithString: "")
        title.font = NSFont.systemFont(ofSize: 13, weight: .semibold)
        title.textColor = .labelColor
        title.lineBreakMode = .byTruncatingTail
        title.frame = NSRect(x: 16, y: 28, width: 185, height: 18)
        let help = NSTextField(labelWithString: "앱 조작 중 · Esc로 즉시 중단")
        help.identifier = NSUserInterfaceItemIdentifier("control-help")
        help.font = NSFont.systemFont(ofSize: 11)
        help.textColor = .secondaryLabelColor
        help.frame = NSRect(x: 16, y: 10, width: 185, height: 16)
        let stop = TactoStopButton(title: "중단", target: ControlMenuTarget.shared,
                            action: #selector(ControlMenuTarget.stopNow(_:)))
        stop.identifier = NSUserInterfaceItemIdentifier("control-stop")
        stop.bezelStyle = .rounded
        stop.frame = NSRect(x: 215, y: 12, width: 63, height: 32)
        stop.setAccessibilityLabel("Tacto 작업 중단")
        content.addSubview(title); content.addSubview(help); content.addSubview(stop)
        panel.contentView = content
        controlPanel = panel; controlPanelLabel = title
        // orderFront does not activate the application or make this panel key.
        panel.orderFrontRegardless()
    }
    let compact = frame.width < 292
    controlPanelLabel?.isHidden = compact
    controlPanel?.contentView?.subviews.first(where: { $0.identifier?.rawValue == "control-help" })?.isHidden = compact
    if let stop = controlPanel?.contentView?.subviews.first(where: { $0.identifier?.rawValue == "control-stop" }) as? NSButton {
        stop.frame = compact ? NSRect(x:12,y:12,width:frame.width-24,height:32)
                             : NSRect(x:215,y:12,width:63,height:32)
        stop.toolTip = "Tacto · \(who) · Esc로 즉시 중단"
    }
    controlPanelLabel?.stringValue = "Tacto · \(who)"
    // If the person changes Spaces or covers/closes the control, refuse work.
    // Do not drag the panel into their new workspace or activate it.
    return controlPanelOnScreen()
}

/// Supplementary menu-bar status; the nonactivating Tacto stop panel is the
/// visible control for AX work. It is Tacto's own UI, not an OS capture proof.
/// Actual image capture retains Screen Recording permission and macOS's own
/// recording indicator. AX-only access never starts a capture stream merely
/// to light an indicator. The panel stays outside target windows and refuses
/// work when it cannot remain visible without covering them, unless the user
/// explicitly disabled this optional banner in settings.
/// What the indicator's menu does when someone chooses to stop.
///
/// `static let` rather than a global, and the position is load-bearing for the
/// same reason the logger's was: `main.swift` initialises top-level globals in
/// source order, and `showControlIndicator` is reachable from diagnostic flags
/// that exit long before `abortState`'s line would have run. A type's static
/// is created on first use, so it cannot be read before it exists.
final class ControlMenuTarget: NSObject {
    static let shared = ControlMenuTarget()

    @objc func stopNow(_ sender: Any?) {
        abortState.stopNow()
        note("tacto-helper: 표시 메뉴에서 중단했습니다")
        DispatchQueue.main.async {
            RuntimePublisher.shared.publish()
            controlPanelLabel?.stringValue = "Tacto · 중단됨"
        }
    }
}

/// Reuse the installed app's persistent menu. No helper-owned status item.
final class RuntimePublisher {
    static let shared = RuntimePublisher()
    var id = UUID().uuidString
    var showBanner = TactoRuntimeStatus.showBanner()
    var target = "Tacto"
    var active = false
    var timer: Timer?
    var observers: [NSObjectProtocol] = []
    var started = false
    var lastPublish = Date.distantPast

    func publish(active: Bool = true) {
        self.active = active
        DistributedNotificationCenter.default().postNotificationName(TactoRuntimeStatus.status,
            object:nil, userInfo:["schema":1,"uid":getuid(),"pid":getpid(),"id":id,
                "target":target,"active":active,"stopped":abortState.isStopped], deliverImmediately:true)
    }
    func start(pid: pid_t) {
        target = String((NSRunningApplication(processIdentifier:pid)?.localizedName ?? "앱").prefix(64))
        if !started {
            started = true
            let center = DistributedNotificationCenter.default()
            observers.append(center.addObserver(forName:TactoRuntimeStatus.query,object:nil,queue:.main) { [weak self] _ in
                guard let self, self.active else { return }; self.publish()
            })
            observers.append(center.addObserver(forName:TactoRuntimeStatus.stop,object:nil,queue:.main) { [weak self] note in
                guard let self, self.active, note.userInfo?["id"] as? String == self.id else { return }
                ControlMenuTarget.shared.stopNow(nil)
                self.publish()
            })
            timer = Timer.scheduledTimer(withTimeInterval:1,repeats:true) { [weak self] _ in
                guard let self, self.active else { return }; self.publish()
            }
            // Launch only the same signed product, in the background, if needed.
            let identifier = "ai.callabo.tacto.setup.standalone.preview"
            if NSRunningApplication.runningApplications(withBundleIdentifier:identifier).isEmpty,
               let team = TactoSigning.ownTeam(),
               let text = TactoSigning.requirement(identifier:identifier,team:team) {
                let url = URL(fileURLWithPath:"/Applications/Tacto.app")
                var code: SecStaticCode?; var requirement: SecRequirement?
                if SecStaticCodeCreateWithPath(url as CFURL,[],&code) == errSecSuccess,
                   SecRequirementCreateWithString(text as CFString,[],&requirement) == errSecSuccess,
                   let code, let requirement, SecStaticCodeCheckValidity(code,[],requirement) == errSecSuccess {
                    let config = NSWorkspace.OpenConfiguration()
                    config.activates = false; config.addsToRecentItems = false
                    config.arguments = ["--background"]
                    NSWorkspace.shared.openApplication(at:url,configuration:config) { _,_ in }
                }
            }
        }
        if Date().timeIntervalSince(lastPublish) >= 0.5 {
            lastPublish = Date(); publish()
        }
    }
}

@discardableResult
func showControlIndicator(forPID pid: pid_t) -> Bool {
    RuntimePublisher.shared.start(pid:pid)
    return true
}

/// The indicator's menu: what is being driven, and a way to stop it.
func controlMenu(for appName: String?) -> NSMenu {
    let menu = NSMenu()
    let what = appName.map { "Tacto가 \($0) 을(를) 조작하고 있습니다" }
        ?? "Tacto가 이 화면을 조작하고 있습니다"
    let header = NSMenuItem(title: what, action: nil, keyEquivalent: "")
    header.isEnabled = false
    menu.addItem(header)
    menu.addItem(.separator())
    let stop = NSMenuItem(title: "중단", action: #selector(ControlMenuTarget.stopNow(_:)),
                          keyEquivalent: "\u{1b}")          // Esc, shown as ⎋
    stop.keyEquivalentModifierMask = []
    stop.target = ControlMenuTarget.shared
    menu.addItem(stop)
    return menu
}

/// Legacy capture-indicator diagnostic stream. Basic AX work uses the visible
/// Tacto stop panel and never starts this merely to show activity. Any actual
/// pixel capture remains scoped and subject to Screen Recording permission.
var controlStream: SCStream?

/// Which window the held stream is scoped to, so a moved target is noticed.
var controlStreamWindow: CGWindowID?

/// Frames are not wanted. Holding the stream is the entire point.
final class DiscardingSink: NSObject, SCStreamOutput {
    func stream(_ stream: SCStream, didOutputSampleBuffer buffer: CMSampleBuffer,
                of type: SCStreamOutputType) {}
}
let controlSink = DiscardingSink()

/// The window this run is acting on, as ScreenCaptureKit knows it.
func controlWindowID(forPID pid: pid_t) -> CGWindowID? {
    let app = AXUIElementCreateApplication(pid)
    guard let windows = attr(app, kAXWindowsAttribute as String) as? [AXUIElement]
    else { return nil }
    let focused = attr(app, kAXFocusedWindowAttribute as String) as! AXUIElement?
    for candidate in [focused].compactMap({ $0 }) + windows {
        if let id = nativeWindowID(candidate) { return id }
    }
    return nil
}

func ensureControlStream(forPID pid: pid_t) -> Bool {
    let connectionEpoch = ModeEpoch.shared.snapshot()
    guard let windowID = controlWindowID(forPID: pid) else {
        note("tacto-helper: 조작 표시를 켜지 못했습니다 — 대상 창을 찾지 못했습니다")
        return false
    }
    if controlStream != nil && controlStreamWindow == windowID { return true }
    if let running = controlStream {
        // The run moved to another window. The indicator must move with it,
        // or it would be reporting capture of something we stopped touching.
        running.stopCapture { _ in }
        controlStream = nil
        controlStreamWindow = nil
    }

    let ready = DispatchSemaphore(value: 0)
    var started = false
    var why = "no answer"
    SCShareableContent.getExcludingDesktopWindows(true, onScreenWindowsOnly: false) {
        content, error in
        guard connectionEpoch == ModeEpoch.shared.snapshot() else {
            why = "session_changed"; ready.signal(); return
        }
        guard let window = content?.windows.first(where: { $0.windowID == windowID }) else {
            why = "window not shareable: \(error?.localizedDescription ?? "none")"
            ready.signal(); return
        }
        // ONE WINDOW. `SCContentFilter(display:)` stood here and it was wrong:
        // it captures the whole screen, so lighting an indicator that says
        // "Tacto is looking" meant looking at everything — including every other
        // window the person had open and never offered us (user report,
        // 2026-09-21). The filter is the same one `captureWindow` uses, and it
        // sees the target window and nothing else.
        let filter = SCContentFilter(desktopIndependentWindow: window)
        let configuration = SCStreamConfiguration()
        // Small and slow deliberately. Nothing reads these frames; the stream
        // exists so the indicator is lit, and taking more than that would be
        // the opposite of what the indicator promises.
        configuration.width = 320
        configuration.height = 200
        configuration.minimumFrameInterval = CMTime(value: 1, timescale: 1)
        configuration.showsCursor = false
        configuration.queueDepth = 3
        let stream = SCStream(filter: filter, configuration: configuration, delegate: nil)
        do {
            try stream.addStreamOutput(controlSink, type: .screen,
                                       sampleHandlerQueue: DispatchQueue.global())
        } catch {
            why = "addStreamOutput: \(error.localizedDescription)"
            ready.signal(); return
        }
        stream.startCapture { startError in
            if let startError {
                why = "startCapture: \(startError.localizedDescription)"
            } else {
                if !ModeEpoch.shared.perform(connectionEpoch, {
                    controlStream = stream
                    controlStreamWindow = windowID
                    started = true
                }) {
                    stream.stopCapture { _ in }
                    why = "session_changed"
                }
            }
            ready.signal()
        }
    }
    if ready.wait(timeout: .now() + 8) != .success {
        note("tacto-helper: 조작 표시를 켜지 못했습니다 (시간 초과)")
        return false
    }
    // Both outcomes, not just the bad one. "Did the indicator actually come
    // on during that run?" was not answerable from the log, and the photograph
    // that was supposed to answer it raced the run and missed.
    note(started ? "tacto-helper: 조작 표시 켜짐 — 창 \(windowID) 하나만 캡처합니다"
                 : "tacto-helper: 조작 표시를 켜지 못했습니다 — \(why)")
    return started
}

/// Retry only transient geometry reads within a bounded readiness window.
func readyControlTargetFrames(timeout: TimeInterval = 0.5,
                              read: () -> [CGRect]?, stopped: () -> Bool,
                              now: () -> TimeInterval = { ProcessInfo.processInfo.systemUptime },
                              pause: (TimeInterval) -> Void = { Thread.sleep(forTimeInterval:$0) }) -> [CGRect]? {
    let deadline = now() + timeout
    while !stopped() {
        // Window-close/reopen transitions can briefly invalidate AX geometry.
        // Retry reads only; a missing frame never authorizes target input.
        let frames = read()
        if stopped() { return nil }
        if let frames { return frames }
        let remaining = deadline - now()
        if remaining <= 0 { return nil }
        pause(min(0.05, remaining))
    }
    return nil
}

/// The user can hide the optional stop panel. Esc and session checks always apply.
///
func ensureVisibleControl(forPID pid: pid_t, windowless: Bool = false) -> Bool {
    // AX access does not read pixels. Keep its visible stop control independent
    // from Screen Recording; actual image APIs retain their permission checks.
    func sessionVisible() -> Bool {
        guard let session = CGSessionCopyCurrentDictionary() as? [String:Any] else { return false }
        return session[kCGSessionOnConsoleKey as String] as? Bool == true
            && session["CGSSessionScreenIsLocked"] as? Bool != true
    }

    // The target's windows fill every screen (a maximized browser), so there
    // is no corner outside them for the panel. The panel is optional — a
    // person can switch it off in Settings — so continue as that setting
    // does: the menu status, Esc and the session checks still apply.
    // Covering the target instead could put the panel under a click.
    func proceedWithoutPanel() -> Bool {
        DispatchQueue.main.sync { controlPanel?.orderOut(nil) }
        lastIndicatorMode = "menu_only_no_room"
        return !abortState.isStopped && sessionVisible()
    }

    guard sessionVisible() else {
        lastIndicatorRefusal="session_not_visible";return false
    }
    guard !abortState.isStopped else { lastIndicatorRefusal="aborted_by_user"; return false }
    // Explicit user preference hides only the panel. Session and Esc guards remain.
    let banner = DispatchQueue.main.sync { () -> Bool in
        _ = showControlIndicator(forPID:pid)
        return RuntimePublisher.shared.showBanner
    }
    if !banner { lastIndicatorMode = "menu_only"; return !abortState.isStopped && sessionVisible() }
    func targetFrames() -> [CGRect]? {
        guard let top = DispatchQueue.main.sync(execute:{ NSScreen.screens.first?.frame.maxY }) else { return nil }
        // AX reads must stay off the main loop so the Stop control responds.
        return controlTargetFrames(forPID:pid,primaryTop:top)
    }
    guard let targets = readyControlTargetFrames(read:targetFrames, stopped:{ abortState.isStopped }) else {
        controlPanelDiagnostic=["target_frames_available":false]
        lastIndicatorRefusal = abortState.isStopped ? "aborted_by_user" : "control_panel_unavailable"
        return false
    }
    guard sessionVisible() && !abortState.isStopped else {
        lastIndicatorRefusal = abortState.isStopped ? "aborted_by_user" : "session_not_visible"
        return false
    }
    let ready = DispatchQueue.main.sync {
        showControlPanel(forPID: pid, targets:targets)
    }
    if ready { lastIndicatorMode = "panel"; return true }
    if controlPanelHasNoRoom(controlPanelDiagnostic) { return proceedWithoutPanel() }
    // WindowServer visibility arrives on a later main-loop turn. Poll only
    // this panel's readiness; never activate it or send target input here.
    let created = DispatchQueue.main.sync { controlPanel != nil }
    guard created else { lastIndicatorRefusal = "control_panel_unavailable"; return false }
    var shown = false
    for _ in 0..<10 {
        Thread.sleep(forTimeInterval:0.05)
        guard sessionVisible() && !abortState.isStopped else { break }
        guard let currentTargets = targetFrames() else {
            controlPanelDiagnostic=["target_frames_available":false]; continue
        }
        shown = DispatchQueue.main.sync { showControlPanel(forPID: pid, targets:currentTargets) }
        if shown { break }
    }
    if !shown && controlPanelHasNoRoom(controlPanelDiagnostic) { return proceedWithoutPanel() }
    guard shown else { lastIndicatorRefusal = "control_panel_not_visible"; return false }
    lastIndicatorMode = "panel"
    return true
}

/// Only "no free place outside the target windows" falls back to the menu.
/// A panel that exists but is hidden, minimized or on another Space still
/// refuses: that is a panel someone cannot see, not one with nowhere to go.
func controlPanelHasNoRoom(_ diagnostic: [String: Bool]) -> Bool {
    diagnostic == ["frame_available": false]
}

/// How the last accepted action was shown: "panel", "menu_only" (the person
/// switched the panel off) or "menu_only_no_room".
var lastIndicatorMode = ""

/// Why the last ensureVisibleControl refused. One message told everyone to
/// grant Screen Recording; on june-mba (granted) the real reason was another
/// (2026-10-09), so the refusal now says which gate it was.
var lastIndicatorRefusal = ""

func indicatorRefusal(_ what: String = "") -> [String: Any] {
    let reason = lastIndicatorRefusal.isEmpty ? "control_panel_unavailable" : lastIndicatorRefusal
    if reason == "aborted_by_user" {
        return ["type":"error", "error":"aborted_by_user", "effect":"not_started", "detail":"사용자가 중단했습니다"]
    }
    let lead = "조작 표시를 켤 수 없어 \(what.isEmpty ? "" : what + " 를 ")중단했습니다 — "
    let advice: String
    switch reason {
    case "session_not_visible":
        advice = "화면이 잠겼거나 사용자 화면 세션이 보이지 않습니다. 화면을 확인한 뒤 다시 진행하세요."
    case "control_panel_unavailable", "control_panel_not_visible":
        advice = "Tacto의 중단 패널이 현재 화면에 보이지 않습니다. 작업을 중단했습니다."
    case "menu_item_not_on_screen":
        advice = "메뉴바의 'Tacto' 표시가 화면에 보이지 않습니다(메뉴바가 가득 차 넘침 영역에 들어갔을 수 있습니다). "
               + "다른 메뉴바 항목을 줄이거나 앱의 창을 하나 연 뒤 다시 시도해 주세요."
    case "menu_item_unavailable":
        advice = "메뉴바에 'Tacto' 표시를 만들지 못했습니다."
    default:
        advice = "화면 기록 표시를 시작하지 못했습니다. 시스템 설정 > 개인정보 보호 및 보안 > 화면 기록 에서 "
               + "'\(permissionHolderName)' 가 켜져 있는지 확인해 주세요."
    }
    return ["type": "error", "error": "indicator_unavailable", "effect": "not_started",
            "reason": reason, "control_panel_diagnostic":controlPanelDiagnostic,
            "detail": lead + advice + " 조작 중임을 화면에 표시하는 수단이라 이것 없이는 진행하지 않습니다."]
}

/// True only when AXWindows was read successfully and is empty — or, for
/// Finder only, holds nothing but Finder's desktop: an AXScrollArea whose
/// parent is the application itself (measured on this Mac and june-mba,
/// 2026-10-09: Finder with no window lists exactly that). Any other entry —
/// a window, a sheet, a dialog, an unknown role, a scroll area in another
/// app or with another parent — or any failed read is not "no windows", and
/// goes to the stream gate (Codex reviews of 2b6b400 and d032cad).
func noWindowsAmong(_ app: AXUIElement, isFinder: Bool, read: AXRead) -> Bool {
    guard let (error, value) = try? read(app, kAXWindowsAttribute as String),
          error == .success, let windows = value as? [AXUIElement] else { return false }
    if windows.isEmpty { return true }
    guard isFinder else { return false }
    for element in windows {
        guard let (roleError, role) = try? read(element, kAXRoleAttribute as String),
              roleError == .success, (role as? String) == "AXScrollArea",
              let (parentError, parent) = try? read(element, kAXParentAttribute as String),
              parentError == .success, let parent,
              CFGetTypeID(parent) == AXUIElementGetTypeID(),
              CFEqual(parent as! AXUIElement, app) else { return false }
    }
    return true
}

func appSaysItHasNoWindows(_ pid: pid_t) -> Bool {
    let isFinder = NSRunningApplication(processIdentifier: pid)?.bundleIdentifier == "com.apple.finder"
    return noWindowsAmong(AXUIElementCreateApplication(pid), isFinder: isFinder, read: { element, name in
        var value: CFTypeRef?
        let error = AXUIElementCopyAttributeValue(element, name as CFString, &value)
        return (error, value)
    })
}

/// Open menus of an app that are not its menu bar's. A context menu shows up
/// as an AXMenu child of the element it was opened on (measured 2026-10-10:
/// window > group > menu), sometimes of the application itself. A bounded
/// breadth-first walk that never enters the menu bar.
func openContextMenus(pid: pid_t, nodeBudget: Int = 4000, depthLimit: Int = 14) -> [AXUIElement] {
    let app = AXUIElementCreateApplication(pid)
    var found: [AXUIElement] = []
    var queue: [(AXUIElement, Int)] = [(app, 0)], head = 0, visited = 0
    while head < queue.count, visited < nodeBudget {
        let (node, depth) = queue[head]; head += 1; visited += 1
        let role = str(node, kAXRoleAttribute as String)
        if role == kAXMenuBarRole as String { continue }
        if role == kAXMenuRole as String {
            found.append(node)
            continue   // its items and submenus close with it
        }
        guard depth < depthLimit else { continue }
        for child in (attr(node, kAXChildrenAttribute as String) as? [AXUIElement]) ?? [] {
            queue.append((child, depth + 1))
        }
    }
    return found
}

func hideControlIndicator() {
    controlPanel?.orderOut(nil)
    controlPanel?.close()
    controlPanel = nil
    controlPanelLabel = nil
    RuntimePublisher.shared.publish(active:false)
}

// Capture lives beside the indicator on purpose: both are about what this
// helper may see of someone's screen, and both are refused when the other
// cannot be arranged.
func sha256Hex(_ data: Data) -> String {
    SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
}

/// A picture of ONE observed window — never the desktop, never a region.
///
/// The screen-recording grant lives on this helper for the same reason the
/// accessibility grant does: a permission held by a shared Python interpreter
/// is a permission held by every script that runs on it, and a screenshot
/// reaches further than a click. `ai.callabo.tacto.helper` is one identity the
/// person can see in System Settings and revoke by itself.
///
/// Pixels are written to a file this process creates and owns, not returned
/// inline: a window-sized PNG does not belong in a JSON frame, and a path the
/// CLIENT chose would let a client make this process write wherever it liked.
func captureWindow(_ obsID: String, _ target: String,
                   windowIndex: Int?, deadline: Double) -> [String: Any] {
    func refuse(_ error: String, _ detail: String) -> [String: Any] {
        ["type": "error", "error": error, "effect": "not_started", "detail": detail]
    }
    guard obsID == latestObsID, let p = pid(forBundle: target), p == latestTargetPID else {
        return refuse("stale_observation", "observe again before capturing")
    }
    guard !abortState.isStopped else {
        return refuse("aborted_by_user", "사용자가 중단했습니다")
    }
    guard let start = nativeProcessStartIdentity(p) else {
        return refuse("target_changed", "process identity unavailable")
    }

    // Which window the caller means, decided from the ACCESSIBILITY tree —
    // the same window the observation described. Matching by title alone
    // would hand back whatever else happened to be called that.
    let app = AXUIElementCreateApplication(p)
    let windows = realWindows(of: app)
    guard !windows.isEmpty else {
        return refuse("no_window_content", "this app has no AX window to capture")
    }
    let index = windowIndex ?? 0
    guard index >= 0, index < windows.count else {
        return refuse("window_index_out_of_range", "\(windows.count) window(s)")
    }
    guard windows.count == 1 || windowIndex != nil else {
        // Name them. "2 windows" tells a caller that it must choose and
        // nothing about how, and it tells whoever is debugging even less.
        let described = windows.enumerated().map { index, window -> String in
            let title = str(window, kAXTitleAttribute as String) ?? ""
            let subrole = str(window, kAXSubroleAttribute as String) ?? "?"
            return "\(index)=\(subrole)\(title.isEmpty ? "" : " '\(title)'")"
        }.joined(separator: ", ")
        return refuse("ambiguous_window",
                      "\(windows.count) windows: name window_index (\(described))")
    }
    guard let windowID = nativeWindowID(windows[index]) else {
        return refuse("window_id_unavailable", "cannot correlate the AX window")
    }

    guard ensureVisibleControl(forPID: p) else {
        return refuse("indicator_unavailable", "조작 표시를 켤 수 없어 중단했습니다 — 시스템 설정 > 개인정보 보호 및 보안 > 화면 기록 에서 '\(permissionHolderName)' 를 켜주세요")
    }

    // ScreenCaptureKit answers asynchronously; the wait is bounded and a late
    // answer is discarded rather than written.
    let gate = DispatchSemaphore(value: 0)
    var picked: SCWindow?
    var failure: String?
    SCShareableContent.getExcludingDesktopWindows(true, onScreenWindowsOnly: false) { content, error in
        if let error { failure = "shareable_content: \(error.localizedDescription)" }
        picked = content?.windows.first { $0.windowID == windowID }
        gate.signal()
    }
    let budget = deadline - ProcessInfo.processInfo.systemUptime
    guard budget > 0 else { return refuse("capture_timeout", "capture deadline expired") }
    guard gate.wait(timeout: .now() + budget) == .success else {
        return refuse("capture_timeout", "screen content did not answer in time")
    }
    if let failure {
        // A refused permission is not a broken capture. ScreenCaptureKit says
        // "declined TCCs" and a caller that reads that as a failure will keep
        // asking; a person who reads it as a failure will look at the app
        // instead of at System Settings.
        // What THIS process thinks it is allowed, so a report can tell a
        // granted-but-not-to-me from a not-granted-at-all. TCC attributes a
        // spawned binary to a responsible process, which is not always the
        // identity someone ticked in System Settings.
        let preflight = CGPreflightScreenCaptureAccess()
        if failure.lowercased().contains("tcc")
            || failure.lowercased().contains("declined")
            || failure.lowercased().contains("permission") {
            return ["type": "error", "error": "screen_capture_permission_missing",
                    "effect": "not_started", "detail": failure,
                    "grant_holder": permissionHolderName,
                    "helper_preflight": preflight,
                    "helper_binary": Bundle.main.executablePath ?? "unknown",
                    "helper_bundle_id": Bundle.main.bundleIdentifier ?? "none",
                    "fix": "시스템 설정 > 개인정보 보호 및 보안 > 화면 기록 에서 "
                         + "'\(permissionHolderName)' 를 켜주세요"]
        }
        return refuse("visual_read_failed", failure)
    }
    guard let window = picked else {
        return refuse("window_not_shareable",
                      "the observed window is not in shareable content "
                      + "(권한은 있는데 창이 사라졌을 수 있습니다)")
    }
    guard window.owningApplication?.processID == p else {
        return refuse("target_changed", "that window belongs to another process now")
    }

    let filter = SCContentFilter(desktopIndependentWindow: window)
    let configuration = SCStreamConfiguration()
    configuration.width = Int(window.frame.width * 2)
    configuration.height = Int(window.frame.height * 2)
    configuration.scalesToFit = true
    configuration.ignoreShadowsSingleWindow = true
    if #available(macOS 14.2, *) { configuration.includeChildWindows = false }
    configuration.showsCursor = false      // ours is not the person's pointer
    var image: CGImage?
    var shotFailure: String?
    let shot = DispatchSemaphore(value: 0)
    SCScreenshotManager.captureImage(contentFilter: filter,
                                     configuration: configuration) { made, error in
        if let error { shotFailure = error.localizedDescription }
        image = made
        shot.signal()
    }
    let remaining = deadline - ProcessInfo.processInfo.systemUptime
    guard remaining > 0 else { return refuse("capture_timeout", "capture deadline expired") }
    guard shot.wait(timeout: .now() + remaining) == .success else {
        return refuse("capture_timeout", "the screenshot did not arrive in time")
    }
    if let shotFailure { return refuse("visual_read_failed", shotFailure) }
    guard let image else { return refuse("visual_read_failed", "no image") }
    guard ProcessInfo.processInfo.systemUptime < deadline else {
        return refuse("capture_timeout", "screenshot arrived after the deadline")
    }

    // Nothing is written until the target is still the target: an image taken
    // of one app must not be handed over labelled as another.
    guard pid(forBundle: target) == p, nativeProcessStartIdentity(p) == start,
          !abortState.isStopped else {
        return refuse("target_changed", "the app changed while capturing")
    }

    let directory = NSTemporaryDirectory() + "tacto-capture-\(getpid())"
    try? FileManager.default.createDirectory(atPath: directory,
        withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
    let path = directory + "/\(UUID().uuidString).png"
    guard let destination = CGImageDestinationCreateWithURL(
            URL(fileURLWithPath: path) as CFURL, "public.png" as CFString, 1, nil) else {
        return refuse("visual_read_failed", "cannot open the destination")
    }
    CGImageDestinationAddImage(destination, image, nil)
    guard CGImageDestinationFinalize(destination) else {
        return refuse("visual_read_failed", "cannot write the image")
    }
    try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: path)
    let bytes = (try? Data(contentsOf: URL(fileURLWithPath: path))) ?? Data()

    return ["type": "capture", "capture_id": "nv" + UUID().uuidString,
            "obs_id": obsID, "target": target, "pid": Int(p),
            "window_id": Int(windowID), "window_index": index,
            "path": path, "bytes": bytes.count,
            "sha256": sha256Hex(bytes),
            "width": image.width, "height": image.height,
            "window_frame": [window.frame.origin.x, window.frame.origin.y, window.frame.width, window.frame.height],
            "image_mapping": "full_window_no_shadow",
            "viewport": ["x": window.frame.origin.x, "y": window.frame.origin.y,
                         "width": window.frame.width, "height": window.frame.height],
            "capture_kind": "native_window", "coordinate_space": "window_pixels",
            // A picture is something to READ. It does not authorize an action
            // and it does not witness a commit — the same rule the Python
            // driver and the browser path state, so one transport cannot
            // quietly mean more than another.
            "evidence_kind": "visual_read", "authorizes_action": false,
            "commit_witness": false]
}

// The stop, and the moment this run began, behind one lock. They are read and
// written from two places — the event monitor on the main run loop, and the
// serving thread — and they only mean anything together: an Esc is this run's
// if it happened after this run started. Two unsynchronized globals can be
// seen half-updated, which is a stop that does not hold or one that holds when
// it should not (peer review).
final class AbortState: @unchecked Sendable {
    private let lock = NSLock()
    private var stopped = false
    private var epoch: TimeInterval = 0

    /// A new connection: a new launcher run, a new intention.
    func begin() {
        lock.lock(); defer { lock.unlock() }
        stopped = false
        epoch = ProcessInfo.processInfo.systemUptime
    }

    /// An Esc, honoured when it happened at or after this run began.
    ///
    /// Events reach the run loop after the fact, so one from the previous run's
    /// queue would otherwise stop something nobody aimed it at — hence the
    /// comparison at all.
    ///
    /// Two mistakes are possible here and they are not worth the same.
    /// Ignoring a real stop leaves someone's computer being driven after they
    /// asked it to stop. Honouring a stale one costs a launcher run. So the
    /// comparison is biased toward the stop, in both of the ways it can be.
    ///
    /// `NSEvent.timestamp` and `ProcessInfo.systemUptime` are both "seconds
    /// since the system started" and are the same base in practice — but they
    /// are NOT documented as identical, and an earlier version of this comment
    /// claimed a skew could only ever make a stale press count. That was wrong
    /// (peer review): if the event clock runs BEHIND, a press made just after
    /// this run began arrives with a timestamp before `epoch`, and a plain
    /// `>=` drops it — a live stop, ignored.
    ///
    /// A grace window was tried for that and made things worse in a way that
    /// is easy to reproduce: with a window, two runs a second apart share it,
    /// so the Esc that stopped the first run also starts the second one
    /// stopped. "A new run is clean" stopped being true exactly when a person
    /// retries immediately, which is when they always retry.
    ///
    /// So the comparison is exact, and the residual risk is stated rather than
    /// argued away: if the two clocks were skewed with the event clock behind,
    /// a press made just after a run began could be dropped. That has not been
    /// observed and is not proven impossible. Closing it properly means taking
    /// both times from ONE clock — deriving the epoch from an event timestamp
    /// rather than from `systemUptime` — which is the fix, not this comment.
    func note(at when: TimeInterval) {
        lock.lock(); defer { lock.unlock() }
        if when >= epoch { stopped = true }
    }

    /// A stop that is not a queued event, so there is nothing to compare.
    ///
    /// The timestamp check above exists because an Esc can reach the run loop
    /// after the run it was aimed at has ended. A click on the indicator
    /// cannot be stale in that way: the indicator only exists while this run
    /// does, and the click happened now, on it. Routing it through `note` with
    /// a manufactured timestamp would be asking a question whose answer is
    /// known.
    func stopNow() {
        lock.lock(); defer { lock.unlock() }
        stopped = true
    }

    var isStopped: Bool {
        lock.lock(); defer { lock.unlock() }
        return stopped
    }
}

let abortState = AbortState()

func watchForAbort() {
    // Esc anywhere stops the run. The helper watches for it itself, so a caller
    // cannot leave the user without a way out.
    NSEvent.addGlobalMonitorForEvents(matching: .keyDown) { ev in
        // Only presses that happened AFTER the client said it was starting
        // over. Events are delivered on the run loop, so an Esc from before
        // the handshake can arrive after it and abort a command the user never
        // saw — which is how one aborted run poisoned every later one, several
        // checks deep and intermittently.
        guard ev.keyCode == 53 else { return }
        abortState.note(at: ev.timestamp)
    }
}

func respond(_ obj: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: obj),
          let line = String(data: data, encoding: .utf8) else { return }
    print(line)
    fflush(stdout)
}

/// Why Return may not go to this element, or nil when it may. Allowed only
/// in the Go to Folder sheet's path field (identifier `PathTextField` under a
/// sheet with identifier `GoToWindow` — Finder's, and the same sheet in
/// open/save panels): there Return navigates, nothing is sent or saved.
/// Not any dialog text field: a save panel's name field, a login sheet, a
/// find-and-replace field all commit on Return (Codex review criteria).
///
/// `read` is the AX read to use: the dispatch's bounded scope at the last
/// check, `boundedAXRead(until:)` on arrival — both under the input
/// deadline. Each read separates success, legitimately absent (noValue / attributeUnsupported — only for subrole and
/// identifiers, never a role) and failure; a failure refuses (Codex review of
/// 0eb24cf: an unreadable subrole had counted as "not secure").
// AXRead: see WindowInput.swift.

/// A read bounded per call (0.5s) AND by the caller's overall deadline, so
/// a walk up 32 ancestors cannot add up past the input deadline. A timeout
/// that cannot be set or reset refuses (Codex review of 8983943).
func boundedAXRead(until deadline: Double) -> AXRead {
    return { element, name in
        let remaining = deadline - ProcessInfo.processInfo.systemUptime
        guard deadline.isFinite, remaining > 0,
              AXUIElementSetMessagingTimeout(element, Float(min(0.5, remaining))) == .success else {
            throw NativeWindowInputError.unavailable("AX read deadline")
        }
        var value: CFTypeRef?
        let error = AXUIElementCopyAttributeValue(element, name as CFString, &value)
        guard AXUIElementSetMessagingTimeout(element, 0) == .success,
              ProcessInfo.processInfo.systemUptime < deadline else {
            throw NativeWindowInputError.unavailable("AX read deadline")
        }
        return (error, value)
    }
}

/// The bound Return's targets: a text area, a text field that is not secure,
/// or a list row. Anything unreadable refuses.
func boundReturnTargetRefusal(_ element: AXUIElement, read: AXRead) -> String? {
    func string(_ name: String, optional: Bool) -> (ok: Bool, value: String?) {
        guard let (err, value) = try? read(element, name) else { return (false, nil) }
        if err == .success, let value, CFGetTypeID(value) == CFStringGetTypeID() { return (true, value as? String) }
        if optional && (err == .noValue || err == .attributeUnsupported) { return (true, nil) }
        return (false, nil)
    }
    let role = string(kAXRoleAttribute as String, optional: false)
    guard role.ok, let value = role.value else { return "role unreadable" }
    guard [kAXTextAreaRole as String, kAXTextFieldRole as String, kAXRowRole as String].contains(value) else {
        return "not a text input or list row (\(value))"
    }
    let subrole = string(kAXSubroleAttribute as String, optional: true)
    guard subrole.ok else { return "subrole unreadable" }
    if subrole.value == kAXSecureTextFieldSubrole as String { return "secure text field" }
    return nil
}

func returnTargetRefusal(_ element: AXUIElement, read rawRead: AXRead) -> String? {
    enum Got { case value(CFTypeRef), absent, failed }
    func get(_ el: AXUIElement, _ name: String, optional: Bool = false) -> Got {
        guard let (err, value) = try? rawRead(el, name) else { return .failed }
        if err == .success, let value { return .value(value) }
        if optional && (err == .noValue || err == .attributeUnsupported) { return .absent }
        return .failed
    }
    func text(_ el: AXUIElement, _ name: String, optional: Bool = false) -> (ok: Bool, value: String?) {
        switch get(el, name, optional: optional) {
        case .value(let v): return CFGetTypeID(v) == CFStringGetTypeID() ? (true, v as? String) : (false, nil)
        case .absent: return (true, nil)
        case .failed: return (false, nil)
        }
    }
    let role = text(element, kAXRoleAttribute as String)
    guard role.ok, let roleValue = role.value else { return "role unreadable" }
    guard roleValue == kAXTextFieldRole as String else { return "not a text field (\(roleValue))" }
    let subrole = text(element, kAXSubroleAttribute as String, optional: true)
    guard subrole.ok else { return "subrole unreadable" }
    if subrole.value == kAXSecureTextFieldSubrole as String { return "secure text field" }
    let ident = text(element, kAXIdentifierAttribute as String, optional: true)
    guard ident.ok else { return "identifier unreadable" }
    guard ident.value == "PathTextField" else { return "not the Go to Folder path field" }
    var current = element
    for _ in 0..<32 {
        guard case .value(let parentValue) = get(current, kAXParentAttribute as String),
              CFGetTypeID(parentValue) == AXUIElementGetTypeID() else {
            return "ancestry unreadable"
        }
        let node = parentValue as! AXUIElement
        let nodeRole = text(node, kAXRoleAttribute as String)
        guard nodeRole.ok, let nodeRoleValue = nodeRole.value else { return "ancestor role unreadable" }
        if nodeRoleValue == "AXSheet" {
            let sheetID = text(node, kAXIdentifierAttribute as String, optional: true)
            guard sheetID.ok else { return "sheet identifier unreadable" }
            return sheetID.value == "GoToWindow" ? nil : "not in the Go to Folder sheet"
        }
        if nodeRoleValue == kAXWindowRole as String || nodeRoleValue == kAXApplicationRole as String {
            return "not in the Go to Folder sheet"
        }
        current = node
    }
    return "ancestry too deep"
}

// Keys that only move the focus ring. Named one by one rather than derived
// from a modifier grammar, because a grammar is how `cmd+q` becomes reachable
// by spelling.
//
// Escape is NOT here, and not by oversight — see the refusal in `press_key`.
let navigationKeys: [String: (CGKeyCode, CGEventFlags)] = [
    "Tab":       (48,  []),
    "shift+Tab": (48,  .maskShift),
    "Up":        (126, []),
    "Down":      (125, []),
    "Left":      (123, []),
    "Right":     (124, []),
]

// Bumped when this helper starts honouring something it used to ignore, or
// stops. A caller compares it against what it needs before sending anything.
#if TACTO_EXPERIMENTAL_FOREGROUND
let experimentalForegroundCapabilities = ["action.vroid_drag", "action.vroid_focus", "experimental.foreground_input"]
#else
let experimentalForegroundCapabilities: [String] = []
#endif

let helperCapabilities: [String] = [
    "observe.reveal",
    "observe", "act", "probe", "selection", "menu_state", "menu_items",
    "expect.role", "expect.label", "expect.attribute", "expect.attribute.subrole", "expect.attribute.url", "observe.subrole", "expect.enabled",
    "expect.no_modal", "expect.selection",
    "effect",                       // errors say whether an effect is possible
    "action.click.deadline_uptime", "action.prepare_window", "action.prepare_window.background", "action.click", "action.set_value", "action.type_text", "action.read",
    "press_key.safari_address_confirm",
    "action.menu", "action.scroll", "action.secondary_action",
    // Finder only: show an existing folder without activating Finder.
    "action.open_folder",
    // menu may carry focused_document: pressed only when the focused and
    // main windows show exactly that file.
    "action.menu.focused_document", "action.menu.document_token_save",
    "action.select_option", "evidence.visual",
    // Native menu items have a title and nothing behind it, so `value` must
    // agree with `option_label` rather than name something else.
    "select_option.label_only",
    "select_option.strict_parameters",
    // The two-step form, for a caller that does not already know what the
    // popup contains: open it, look at the observation that follows, then
    // choose from THAT. Same words the browser backend uses.
    "action.open_select", "action.open_select.explicit_ax", "action.select_custom_option",
    "custom_select.label_only",
    // Said separately because the browser's `press_key` includes Escape and
    // this one does not. A client that needs Escape should see the difference
    // rather than discover it.
    // The observation names which elements accept a secondary action.
    // Announcing `action.secondary_action` while saying nothing about where it
    // applies left a caller to find out by trying one and reading the refusal
    // — a capability that was advertised and unusable.
    "observe.secondary_actions",
    // A coordinate can be an address. `capture` reports a viewport in window
    // points, and a point in that space resolves to the real element under it
    // — which is how a control that draws itself and exposes no AX element of
    // its own becomes reachable without synthesising anything.
    "action.click.by_point",
    "action.click_point", "click_point.window_bound", "click_point.host_authorization",
    "action.drag", "drag.window_bound",
    // The one route that moves the person's cursor: no buttons, no activation,
    // restored afterwards unless they moved it themselves.
    "action.hover", "hover.pointer_restored",
    // double_click: AXOpen where offered, else two window-bound clicks at the
    // observed element's hit-tested centre (click count 1 then 2).
    "action.double_click",
    // click on an element with no AXPress: one window-bound click at its
    // hit-tested centre, instead of a refusal.
    "click.window_bound_element",
    // Return into a bound text input or selected row, when the host vouches
    // for the step (submit_authorized); one keyDown, reobserve.
    "press_key.return_bound",
    // Ask a running app to reopen its window (LaunchServices, activates=false;
    // whether it came forward is observed) and close an open context menu
    // with AXCancel instead of Escape.
    "action.reopen_window", "action.close_menu",
    "action.window_input", "window_input.window_bound", "window_input.host_authorization",
    // Not "press_key.navigation_only": this helper also takes Return, narrowly.
    "action.press_key", "press_key.no_escape",
    "press_key.element_bound",
    // Return, only to the Go to Folder sheet's path field.
    "press_key.return_in_go_to_folder", "action.press_key.deadline_uptime",
    // Reading only, and deliberately NOT document_snapshot: this witnesses what
    // a document contains and where it lives, never its source format or where
    // a save panel would put it.
    "diagnostic.readiness_read_cost", "diagnostic.readiness_preflight", "observe.window_identity",
    "evidence.document_window_status", "evidence.document_text", "evidence.document_text.path_selector", "observe.document_token", "evidence.save_panel",
    "action.click.save_panel_guard", "evidence.document_rtf_export", "evidence.document_attachment_metadata",
] + experimentalForegroundCapabilities





// MARK: - Read-only document text
//
// The same contract tacto/native_document.py states, spoken over the signed
// channel. Reading only: no launch, no activation, no AX mutation, no
// synthesized input. It says what a document CONTAINS and where it lives; it
// does not claim a source format or a save-panel destination, which is why the
// capability it declares is `evidence.document_text` and not
// `evidence.document_snapshot`.
//
// Identity is the whole difficulty. A pinned document is a window and a body
// compared by CFEqual, under a process pinned by pid AND launch date, so a
// relaunched app with a recycled pid cannot answer for the document somebody
// pinned before. Both are checked again after the text is read: a witness that
// was true of a different document is not a witness.

enum DocumentError: Error {
    case fail(String)
}

struct PinnedDocument {
    let pid: pid_t
    let stamp: Double
    let window: AXUIElement
    let body: AXUIElement
    var selectedPath: String? = nil
    var lifecycleWindowID: CGWindowID? = nil
}

/// Tokens are per connection and bounded. A new authenticated connection is a
/// new client with no right to the previous one's pins, so the store is reset
/// there alongside the other session state.
let documentTokenLimit = 32
var documentTokens: [(token: String, document: PinnedDocument)] = []
let documentTextBudgetLimit = 1_000_000
let documentNodeBudget = 5000
let documentDepthBudget = 64

func documentProcessIdentity(_ target: String) throws -> (pid_t, Double) {
    guard let p = pid(forBundle: target) else {
        throw DocumentError.fail("document application is not running")
    }
    guard let app = NSRunningApplication(processIdentifier: p), !app.isTerminated,
          app.bundleIdentifier == target else {
        throw DocumentError.fail("document application identity unavailable")
    }
    guard let launched = app.launchDate else {
        throw DocumentError.fail("application launch identity unavailable")
    }
    let stamp = launched.timeIntervalSince1970
    guard stamp.isFinite else {
        throw DocumentError.fail("invalid application launch identity")
    }
    return (p, stamp)
}

/// Checked before every AX read, not once at the door.
///
/// A complete document read can walk five thousand nodes, and a stop that only
/// applies at the start is a stop the user watches the machine ignore. The call
/// already in flight cannot be recalled — that limit is real and stated — but
/// nothing after it happens, and no body and no token come back (peer review).
var documentOperationDeadline: Double? = nil
func documentCheckAbort() throws {
    if abortState.isStopped {
        throw DocumentError.fail("사용자가 중단해 문서 읽기를 멈췄습니다")
    }
    if let deadline = documentOperationDeadline, ProcessInfo.processInfo.systemUptime >= deadline {
        throw DocumentError.fail("document read deadline elapsed")
    }
}

func documentRequired(_ element: AXUIElement, _ name: String) throws -> Any {
    try documentCheckAbort()
    var out: AnyObject?
    let err = AXUIElementCopyAttributeValue(element, name as CFString, &out)
    guard err == .success, let value = out else {
        throw DocumentError.fail("\(name) unavailable: \(err.rawValue)")
    }
    return value
}

/// Every document this app has open, or an error. A partial enumeration is
/// never returned: a budget that runs out fails rather than answering with the
/// documents it happened to reach first.
func documentEnumerate(_ p: pid_t, selectedWindow: AXUIElement? = nil,
                       selectedPath: String? = nil) throws -> [(window: AXUIElement, body: AXUIElement)] {
    let app = AXUIElementCreateApplication(p)
    guard let windows = try documentRequired(app, kAXWindowsAttribute as String) as? [AXUIElement] else {
        throw DocumentError.fail("AXWindows unavailable")
    }
    var found: [(window: AXUIElement, body: AXUIElement)] = []
    var remaining = documentNodeBudget
    for window in windows {
        if let selectedWindow, !CFEqual(window, selectedWindow) { continue }
        guard let subrole = try documentRequired(window, kAXSubroleAttribute as String) as? String else {
            throw DocumentError.fail("AXSubrole unavailable")
        }
        if subrole != "AXStandardWindow" { continue }
        if let selectedPath, try documentPath(window) != selectedPath { continue }
        var bodies: [AXUIElement] = []
        var stack: [(AXUIElement, Int)] = [(window, 0)]
        while let (element, depth) = stack.popLast() {
            try documentCheckAbort()
            remaining -= 1
            if remaining < 0 || depth > documentDepthBudget {
                throw DocumentError.fail("document traversal budget exceeded")
            }
            guard let role = try documentRequired(element, kAXRoleAttribute as String) as? String else {
                throw DocumentError.fail("AXRole unavailable")
            }
            if role == "AXTextArea" {
                guard let identifier = try documentRequired(element, "AXIdentifier") as? String else {
                    throw DocumentError.fail("AXIdentifier unavailable")
                }
                if identifier == "First Text View" { bodies.append(element) }
            }
            var childrenOut: AnyObject?
            let err = AXUIElementCopyAttributeValue(element, kAXChildrenAttribute as CFString,
                                                    &childrenOut)
            if err == AXError(rawValue: -25205) || err == AXError(rawValue: -25212) {
                continue                      // unsupported / no value: a valid leaf
            }
            guard err == .success, let children = childrenOut as? [AXUIElement] else {
                throw DocumentError.fail("document children unavailable: \(err.rawValue)")
            }
            if children.count > remaining {
                throw DocumentError.fail("document traversal budget exceeded")
            }
            for child in children.reversed() { stack.append((child, depth + 1)) }
        }
        if bodies.count != 1 {
            throw DocumentError.fail("document body is absent or ambiguous")
        }
        found.append((window, bodies[0]))
    }
    return found
}

/// Where this document lives, or nil when it has never been saved.
///
/// -25212 (kAXErrorNoValue) is the ONLY reading that means "unsaved"; every
/// other failure is a failure and says so. The URL is then held to being a
/// local file URL naming a canonical absolute path, because a path that is not
/// canonical does not identify one file.
/// "" when the app's focused and main windows both show `want` (canonical,
/// NFC); otherwise why not. Any failed read is a mismatch. `shown` reads one
/// window attribute's document path (nil: unreadable, none, or not a file);
/// `canonical` resolves a path (nil: unresolvable). Both are injected so the
/// real function is tested with fakes.
func focusedDocumentMismatch(want: String, shown: (String) -> String?,
                             canonical: (String) -> String?) -> String {
    guard want.hasPrefix("/"), let wanted = canonical(want),
          wanted == want.precomposedStringWithCanonicalMapping else {
        return "approved path is not canonical"
    }
    for name in [kAXFocusedWindowAttribute as String, kAXMainWindowAttribute as String] {
        guard let path = shown(name), let real = canonical(path), real == wanted else {
            return "\(name) shows another or no file"
        }
    }
    return ""
}

func focusedDocumentMismatch(pid: pid_t, want: String,
                             setTimeout: (AXUIElement, Float) -> AXError = { AXUIElementSetMessagingTimeout($0, $1) },
                             copy: (AXUIElement, String) -> (AXError, CFTypeRef?) = { element, name in
                                 var value: CFTypeRef?
                                 let error = AXUIElementCopyAttributeValue(element, name as CFString, &value)
                                 return (error, value)
                             },
                             windowPath: (AXUIElement) -> String? = { (try? documentPath($0)) ?? nil })
    -> String {
    let app = AXUIElementCreateApplication(pid)
    // Bounded reads: a timeout that cannot be set, or reset to 0 afterwards,
    // is a refusal (Codex review of b031a57: the reset result was ignored).
    // This bounds each read; it is not an end-to-end time guarantee, and the
    // reset is to 0 (the system default), not to an earlier custom value.
    guard setTimeout(app, 0.5) == .success else { return "AX timeout unavailable" }
    var resetFailed = false
    let why = focusedDocumentMismatch(want: want, shown: { name in
        let (error, value) = copy(app, name)
        guard error == .success, let window = value,
              CFGetTypeID(window) == AXUIElementGetTypeID() else { return nil }
        let element = window as! AXUIElement
        guard setTimeout(element, 0.5) == .success else { return nil }
        let path = windowPath(element)
        if setTimeout(element, 0) != .success { resetFailed = true; return nil }
        return path
    }, canonical: { path in
        guard let real = realpath(path, nil) else { return nil }
        defer { free(real) }
        return String(cString: real).precomposedStringWithCanonicalMapping
    })
    if setTimeout(app, 0) != .success || resetFailed { return "AX timeout reset failed" }
    return why
}

func documentPath(_ window: AXUIElement) throws -> String? {
    try documentCheckAbort()
    var out: AnyObject?
    let err = AXUIElementCopyAttributeValue(window, "AXDocument" as CFString, &out)
    if err == AXError(rawValue: -25212) { return nil }
    guard err == .success, let text = out as? String, !text.isEmpty else {
        throw DocumentError.fail("AXDocument unavailable")
    }
    return try documentURLPath(text)
}

/// Resolve file references only after strict URL syntax validation.
/// A local file URL's absolute path by syntax alone, or nil — the checks of
/// documentURLPath up to decoding, with no file-system access and no
/// reference resolution. Used for a window line's "Document:"; the same rules
/// as Python's document_url_syntax_path (Codex review of 66433c5).
func documentURLSyntaxPath(_ text: String) -> String? {
    // Raw-text policy first, the same as Python's (Codex review of a33bcaa):
    // "file://" exactly, lower case; the raw authority exactly "" or
    // "localhost" (Foundation would decode %6cocalhost to localhost).
    guard text.hasPrefix("file://") else { return nil }
    let rest = text.dropFirst("file://".count)
    guard let slash = rest.firstIndex(of: "/"),
          ["", "localhost"].contains(String(rest[rest.startIndex..<slash])) else { return nil }
    if text.unicodeScalars.contains(where: { $0.value < 0x20 || $0.value == 0x7F }) { return nil }
    let lowered = text.lowercased()
    if lowered.contains("%2f") || lowered.contains("%5c") || lowered.contains("%00")
        || text.contains("?") || text.contains("#") { return nil }
    guard documentEscapesAreWellFormed(text),
          let parts = URLComponents(string: text), parts.scheme == "file",
          parts.query == nil, parts.fragment == nil,
          parts.user == nil, parts.password == nil, parts.port == nil,
          (parts.host == nil || parts.host == "" || parts.host == "localhost"),
          let path = documentDecodePercent(parts.percentEncodedPath),
          path.hasPrefix("/"), !path.hasPrefix("//"), !path.contains("\\"),
          !path.unicodeScalars.contains(where: { $0.value < 0x20 || $0.value == 0x7F }) else { return nil }
    return path
}

func documentURLPath(_ text: String, resolvingReference: Bool = false) throws -> String {
    if text.unicodeScalars.contains(where: { $0.value < 0x20 || $0.value == 0x7F }) {
        throw DocumentError.fail("invalid document file URL")
    }
    let lowered = text.lowercased()
    if lowered.contains("%2f") || lowered.contains("%5c") || lowered.contains("%00")
        || text.contains("?") || text.contains("#") {
        throw DocumentError.fail("invalid document file URL")
    }
    // Checked on the RAW text, before URLComponents ever sees it. Foundation
    // repairs a malformed escape on the way in — a bare `%` becomes `%25` —
    // so by the time `percentEncodedPath` is read the escape looks well formed
    // and the strict decoder below accepts exactly what it was written to
    // refuse. Measured: `file:///…/%zz` and `file:///…/%` both got through
    // (peer review, reproduced verbatim).
    guard documentEscapesAreWellFormed(text) else {
        throw DocumentError.fail("invalid document file URL")
    }
    guard let parts = URLComponents(string: text), parts.scheme == "file",
          parts.query == nil, parts.fragment == nil else {
        throw DocumentError.fail("invalid document file URL")
    }
    // An empty or `localhost` authority and NOTHING else. Checking the host
    // alone let `file://user@localhost/path` and `file://localhost:80/path`
    // through, which the Python reader rejects by comparing the whole netloc
    // (peer review).
    guard parts.user == nil, parts.password == nil, parts.port == nil,
          (parts.host == nil || parts.host == "" || parts.host == "localhost") else {
        throw DocumentError.fail("invalid document file URL")
    }
    // Percent escapes decoded by hand, so a malformed escape or a byte string
    // that is not UTF-8 is a refusal rather than a replacement character.
    guard let path = documentDecodePercent(parts.percentEncodedPath) else {
        throw DocumentError.fail("invalid document file URL")
    }
    guard path.hasPrefix("/"), !path.hasPrefix("//"), !path.contains("\\"),
          !path.unicodeScalars.contains(where: { $0.value < 0x20 || $0.value == 0x7F }) else {
        throw DocumentError.fail("document path is not canonical absolute")
    }
    if path.hasPrefix("/.file/") {
        guard !resolvingReference, let reference = NSURL(string: text),
              reference.isFileReferenceURL(), let resolved = reference.filePathURL else {
            throw DocumentError.fail("document file reference is unreachable")
        }
        return try documentURLPath(resolved.absoluteString, resolvingReference: true)
    }
    // AXDocument may identify an RTFD package with a directory URL. Normalize
    // exactly one terminal slash, only for an existing canonical directory.
    if path.hasSuffix("/"), path != "/" {
        let candidate = String(path.dropLast())
        var isDirectory: ObjCBool = false
        guard documentIsCanonical(candidate),
              FileManager.default.fileExists(atPath: candidate, isDirectory: &isDirectory),
              isDirectory.boolValue else {
            throw DocumentError.fail("document path is not canonical absolute")
        }
        return candidate
    }
    guard documentIsCanonical(path) else {
        throw DocumentError.fail("document path is not canonical absolute")
    }
    return path
}

/// Every `%` in the raw URL followed by exactly two hex digits.
///
/// `%25zz` passes and must: that is a well-formed escape for a literal `%`
/// followed by two ordinary letters. `%zz` and a trailing `%` do not.
func documentEscapesAreWellFormed(_ raw: String) -> Bool {
    let scalars = Array(raw.unicodeScalars)
    var index = 0
    while index < scalars.count {
        if scalars[index] == "%" {
            guard index + 2 < scalars.count,
                  Character(scalars[index + 1]).isHexDigit,
                  Character(scalars[index + 2]).isHexDigit else { return false }
            index += 3
            continue
        }
        index += 1
    }
    return true
}

/// Percent-decode strictly: every `%` needs two hex digits, and the bytes that
/// come out have to be UTF-8. Foundation's own decoder answers nil for some of
/// this and substitutes for the rest, and substituting turns a bad path into a
/// plausible one.
func documentDecodePercent(_ encoded: String) -> String? {
    var bytes: [UInt8] = []
    var rest = Substring(encoded)
    while let index = rest.firstIndex(of: "%") {
        bytes.append(contentsOf: Array(rest[rest.startIndex..<index].utf8))
        let after = rest.index(after: index)
        guard let second = rest.index(after, offsetBy: 2, limitedBy: rest.endIndex) else {
            return nil
        }
        guard let byte = UInt8(rest[after..<second], radix: 16),
              rest[after..<second].allSatisfy({ $0.isHexDigit }) else { return nil }
        bytes.append(byte)
        rest = rest[second...]
    }
    bytes.append(contentsOf: Array(rest.utf8))
    return String(bytes: bytes, encoding: .utf8)
}

/// Canonical-absolute the way Python's os.path.realpath decides it.
///
/// realpath(3) requires the file to EXIST; Python's function does not, and
/// AXDocument is not evidence that a file is still there — a document open on
/// a file somebody has since deleted is an ordinary situation. Rejecting it
/// here while the in-process reader accepts it would make the two transports
/// disagree about the same document, which is the one thing this contract
/// exists to prevent (peer review).
///
/// So: resolve the deepest ancestor that does exist, and require the part that
/// does not to be lexically canonical — no empty, `.` or `..` components —
/// which is exactly what Python's function guarantees for a missing tail.
func documentIsCanonical(_ path: String) -> Bool {
    if let resolved = realpath(path, nil) {
        defer { free(resolved) }
        return String(cString: resolved) == path
    }
    if errno != ENOENT { return false }
    var components = path.split(separator: "/", omittingEmptySubsequences: false).map(String.init)
    var missing: [String] = []
    while components.count > 1 {
        let tail = components.removeLast()
        if tail.isEmpty || tail == "." || tail == ".." { return false }
        missing.insert(tail, at: 0)
        let joined = components.joined(separator: "/")
        let ancestor = joined.isEmpty ? "/" : joined
        if let resolved = realpath(ancestor, nil) {
            defer { free(resolved) }
            let base = String(cString: resolved)
            let prefix = (base == "/") ? "" : base
            return prefix + "/" + missing.joined(separator: "/") == path
        }
        if errno != ENOENT { return false }
    }
    return false
}

/// True is an NSNumber in this bridge, and so is 1.0. Neither is a count.
/// `is Bool` cannot be relied on either — NSNumber(0) and NSNumber(1) bridge to
/// Bool — so the question is asked of CoreFoundation, which knows what the
/// object actually is (peer review).
func documentIsWholeNumber(_ value: Any?) -> NSNumber? {
    guard let number = value as? NSNumber else { return nil }
    if CFGetTypeID(number) == CFBooleanGetTypeID() { return nil }
    if CFNumberIsFloatType(number as CFNumber) { return nil }
    return number
}

func documentTextCount(_ element: AXUIElement, _ budget: Int) throws -> Int {
    guard let number = documentIsWholeNumber(try documentRequired(element, "AXNumberOfCharacters")) else {
        throw DocumentError.fail("AXNumberOfCharacters must be a nonnegative integer")
    }
    let count = number.intValue
    if count < 0 { throw DocumentError.fail("AXNumberOfCharacters must be a nonnegative integer") }
    if count > budget { throw DocumentError.fail("AX text exceeds the UTF-16 budget") }
    return count
}

func documentTextValue(_ element: AXUIElement, _ count: Int) throws -> String {
    guard let text = try documentRequired(element, kAXValueAttribute as String) as? String else {
        throw DocumentError.fail("AX text value is not a string")
    }
    if text.utf16.count != count {
        throw DocumentError.fail("AX text length differs from AXNumberOfCharacters")
    }
    return text
}

func documentTextRange(_ element: AXUIElement, _ count: Int) throws -> String {
    try documentCheckAbort()
    var range = CFRange(location: 0, length: count)
    guard let parameter = AXValueCreate(.cfRange, &range) else {
        throw DocumentError.fail("cannot construct AX text range")
    }
    var out: AnyObject?
    let err = AXUIElementCopyParameterizedAttributeValue(
        element, "AXStringForRange" as CFString, parameter, &out)
    guard err == .success, let text = out as? String else {
        throw DocumentError.fail("AXStringForRange unavailable: \(err.rawValue)")
    }
    if text.utf16.count != count {
        throw DocumentError.fail("AX text length differs from AXNumberOfCharacters")
    }
    return text
}

/// AXValue cross-checked against a full AXStringForRange, then the count and
/// AXValue read again — the range is read once, and saying "both" made this
/// sound like it verifies more than it does (peer review).
/// Sequential reads catch disagreement; they cannot rule out an edit that
/// changes the text and puts it back between observations.
func documentCompleteText(_ body: AXUIElement, budget: Int) throws -> String {
    let count = try documentTextCount(body, budget)
    let value = try documentTextValue(body, count)
    let ranged = try documentTextRange(body, count)
    if ranged != value {
        throw DocumentError.fail("AXValue and AXStringForRange disagree")
    }
    let afterCount = try documentTextCount(body, budget)
    let afterValue = try documentTextValue(body, afterCount)
    if afterCount != count || afterValue != value {
        throw DocumentError.fail("AX text changed while taking the snapshot")
    }
    return value
}

func documentMatches(_ documents: [(window: AXUIElement, body: AXUIElement)],
                     _ pinned: PinnedDocument) -> Int {
    documents.filter { CFEqual($0.window, pinned.window) && CFEqual($0.body, pinned.body) }.count
}

func documentText(_ target: String, token requested: String?, budget: Int,
                  selectedPath: String? = nil) -> [String: Any] {
    func refuse(_ detail: String) -> [String: Any] {
        ["type": "error", "error": "document_snapshot_unavailable",
         "effect": "not_started", "detail": detail]
    }
    guard target == "com.apple.TextEdit" else {
        return refuse("only TextEdit document reads are supported")
    }
    if let requested, requested.isEmpty || requested.count > 128 { return refuse("invalid document token") }
    if let selectedPath {
        guard requested == nil, selectedPath.hasPrefix("/"), !selectedPath.hasPrefix("//"),
              !selectedPath.contains("\\"), selectedPath.utf8.count <= 4096,
              !selectedPath.unicodeScalars.contains(where: { $0.value < 32 || $0.value == 127 }),
              documentIsCanonical(selectedPath) else { return refuse("invalid document path selector") }
    }
    do {
        var pinned: PinnedDocument?
        if let requested {
            guard let entry = documentTokens.first(where: { $0.token == requested }) else {
                return refuse("unknown or expired document token")
            }
            pinned = entry.document
        }
        let (p, stamp) = try documentProcessIdentity(target)
        let documents = try documentEnumerate(p, selectedWindow: pinned?.window,
                                              selectedPath: pinned?.selectedPath ?? selectedPath)
        if pinned == nil {
            guard documents.count == 1 else {
                return refuse("initial document selection is ambiguous or absent")
            }
            pinned = PinnedDocument(pid: p, stamp: stamp,
                                    window: documents[0].window, body: documents[0].body,
                                    selectedPath: selectedPath,
                                    lifecycleWindowID: nativeWindowID(documents[0].window))
        } else if pinned!.pid != p || pinned!.stamp != stamp
                    || documentMatches(documents, pinned!) != 1 {
            documentTokens.removeAll { $0.token == requested }
            return refuse("document token is stale")
        }
        let held = pinned!
        let path = try documentPath(held.window)
        if let expected = held.selectedPath, path != expected {
            return refuse("selected document path changed before text read")
        }
        let text = try documentCompleteText(held.body, budget: budget)
        let afterDocuments = try documentEnumerate(p, selectedWindow: held.window,
                                                   selectedPath: held.selectedPath)
        let (afterPid, afterStamp) = try documentProcessIdentity(target)
        if afterPid != held.pid || afterStamp != held.stamp
            || documentMatches(afterDocuments, held) != 1 {
            if let requested { documentTokens.removeAll { $0.token == requested } }
            return refuse("document identity changed during observation")
        }
        let afterPath = try documentPath(held.window)
        if afterPath != path {
            return refuse("document path changed during observation")
        }
        // Last gate before anything is handed back. A stop that landed while
        // the text was being read must not be answered with the text.
        try documentCheckAbort()
        var issued = requested
        if issued == nil {
            let fresh = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
            documentTokens.append((token: fresh, document: held))
            if documentTokens.count > documentTokenLimit { documentTokens.removeFirst() }
            issued = fresh
        }
        var reply: [String: Any] = ["type": "document_text", "document_token": issued!,
                                    "text": text, "text_complete": true]
        reply["document_path"] = path ?? NSNull()
        return reply
    } catch let DocumentError.fail(detail) {
        if detail.hasPrefix("사용자가 중단해") {
            return ["type": "error", "error": "aborted_by_user",
                    "effect": "not_started", "detail": detail]
        }
        return refuse(detail)
    } catch {
        return refuse("document observation failed")
    }
}

/// Read-only window lifecycle, retaining the pin when absence is observable.
/// A stale token, failed AX read or process restart is never "closed".
/// Metadata only: no body read, AX action, launch, focus or file deletion.
func documentWindowStatus(_ msg: [String:Any]) -> [String:Any] {
    func refuse(_ reason: String = "native_read_failed") -> [String:Any] {
        ["type":"error", "error":"document_window_status_unavailable", "effect":"not_started", "reason_code":reason]
    }
    guard Set(msg.keys) == Set(["type","target","document_token"]),
          msg["target"] as? String == "com.apple.TextEdit",
          let token = msg["document_token"] as? String, !token.isEmpty, token.count <= 128,
          let pinned = documentTokens.first(where: { $0.token == token })?.document else { return refuse("request_invalid_or_pin_missing") }
    let previousDeadline = documentOperationDeadline
    documentOperationDeadline = min(previousDeadline ?? .infinity,
                                    ProcessInfo.processInfo.systemUptime + 6)
    defer { documentOperationDeadline = previousDeadline }
    do {
        func read() throws -> (Bool, [AXUIElement]) {
            let (pid, stamp) = try documentProcessIdentity("com.apple.TextEdit")
            guard pid == pinned.pid, stamp == pinned.stamp else { throw DocumentError.fail("process changed") }
            let app = AXUIElementCreateApplication(pid)
            guard let windows = try documentRequired(app, kAXWindowsAttribute as String) as? [AXUIElement],
                  windows.count <= 128 else { throw DocumentError.fail("window enumeration unavailable") }
            var present = false
            var pathMatches = 0
            for window in windows {
                try documentCheckAbort()
                guard let subrole = try documentRequired(window, kAXSubroleAttribute as String) as? String else {
                    throw DocumentError.fail("window role unavailable")
                }
                if subrole != "AXStandardWindow" {
                    if CFEqual(window, pinned.window) { throw DocumentError.fail("window changed") }
                    continue
                }
                let path = try documentPath(window)
                if let selected = pinned.selectedPath, path == selected { pathMatches += 1 }
                if CFEqual(window, pinned.window) {
                    if let selected = pinned.selectedPath, path != selected {
                        throw DocumentError.fail("document path changed")
                    }
                    present = true
                }
            }
            if pinned.selectedPath != nil && pathMatches != (present ? 1 : 0) {
                throw DocumentError.fail("document path is ambiguous or reopened")
            }
            try documentCheckAbort()
            if !present {
                guard let windowID = pinned.lifecycleWindowID, windowID > 0,
                      let allWindows = CGWindowListCopyWindowInfo(.optionAll, 0) as? [[String:Any]],
                      allWindows.count <= 8192 else { throw DocumentError.fail("global windows unavailable") }
                // Lifecycle uses required numeric metadata only, never titles
                // or pixels. Image-capture authorization is unrelated to this
                // read; nil/malformed metadata still cannot prove closure.
                for info in allWindows {
                    try documentCheckAbort()
                    guard let owner = documentIsWholeNumber(info[kCGWindowOwnerPID as String]),
                          let number = documentIsWholeNumber(info[kCGWindowNumber as String]),
                          owner.int64Value > 0, owner.int64Value < Int64(Int32.max),
                          number.int64Value > 0, number.int64Value <= Int64(UInt32.max) else {
                        throw DocumentError.fail("global window identity malformed")
                    }
                    if owner.int32Value == pinned.pid && number.uint32Value == windowID {
                        throw DocumentError.fail("window still exists outside AX enumeration")
                    }
                }
            }
            try documentCheckAbort()
            let after = try documentProcessIdentity("com.apple.TextEdit")
            guard after.0 == pinned.pid, after.1 == pinned.stamp else { throw DocumentError.fail("process changed") }
            return (present, windows)
        }
        let first = try read(), second = try read()
        guard first.0 == second.0, first.1.count == second.1.count,
              zip(first.1, second.1).allSatisfy({ CFEqual($0.0, $0.1) }) else { return refuse("window_snapshot_changed") }
        try documentCheckAbort()
        return ["type":"document_window_status", "document_token":token,
                "state":first.0 ? "open" : "closed", "same_process_verified":true]
    } catch let DocumentError.fail(detail) {
        let codes = ["process changed":"process_changed",
                     "document application is not running":"process_unavailable",
                     "document application identity unavailable":"process_unavailable",
                     "application launch identity unavailable":"process_unavailable",
                     "invalid application launch identity":"process_unavailable",
                     "window enumeration unavailable":"window_enumeration_unavailable",
                     "window role unavailable":"window_role_unavailable",
                     "window changed":"window_changed",
                     "document path changed":"document_path_changed",
                     "document path is ambiguous or reopened":"document_path_ambiguous_or_reopened",
                     "global windows unavailable":"global_windows_unavailable",
                     "global window identity malformed":"global_window_identity_malformed",
                     "window still exists outside AX enumeration":"global_window_still_present",
                     "document read deadline elapsed":"deadline_or_abort",
                     "사용자가 중단해 문서 읽기를 멈췄습니다":"deadline_or_abort"]
        return refuse(codes[detail] ?? "native_read_failed")
    } catch { return refuse() }
}

func documentRTFExport(_ msg: [String:Any]) -> [String:Any] {
    func refuse() -> [String:Any] {
        ["type":"error", "error":"document_rtf_export_unavailable", "effect":"not_started"]
    }
    guard Set(msg.keys).isSubset(of: ["type","target","document_token","max_utf16_units","timeout_seconds"]),
          msg["target"] as? String == "com.apple.TextEdit",
          let token = msg["document_token"] as? String, !token.isEmpty, token.count <= 128,
          let budget = documentIsWholeNumber(msg["max_utf16_units"] ?? 1_000_000),
          budget.intValue >= 0, budget.intValue <= 1_000_000,
          let timeout = (msg["timeout_seconds"] ?? 3.0) as? NSNumber,
          CFGetTypeID(timeout) != CFBooleanGetTypeID(), timeout.doubleValue.isFinite,
          timeout.doubleValue > 0, timeout.doubleValue <= 6,
          let pinned = documentTokens.first(where: { $0.token == token })?.document else { return refuse() }
    let previousDeadline = documentOperationDeadline
    documentOperationDeadline = min(previousDeadline ?? .infinity,
        ProcessInfo.processInfo.systemUptime + timeout.doubleValue)
    defer { documentOperationDeadline = previousDeadline }
    do {
        let path = try documentPath(pinned.window)
        func check() throws {
            try documentCheckAbort()
            guard CFEqual(try documentWindowForToken("com.apple.TextEdit", token), pinned.window),
                  let current = documentTokens.first(where: { $0.token == token })?.document,
                  current.pid == pinned.pid, current.stamp == pinned.stamp,
                  CFEqual(current.body, pinned.body), try documentPath(pinned.window) == path else {
                throw DocumentError.fail("document export identity or path changed")
            }
            try documentCheckAbort()
        }
        func timed<T>(_ operation: () throws -> T) throws -> T {
            try check()
            let remaining = documentOperationDeadline! - ProcessInfo.processInfo.systemUptime
            guard remaining > 0, AXUIElementSetMessagingTimeout(pinned.body, Float(min(0.5, remaining))) == .success else {
                throw DocumentError.fail("document export AX timeout unavailable")
            }
            defer { _ = AXUIElementSetMessagingTimeout(pinned.body, 0) }
            let result = try operation()
            guard AXUIElementSetMessagingTimeout(pinned.body, 0) == .success else {
                throw DocumentError.fail("document export AX timeout reset failed")
            }
            try check()
            return result
        }
        func export(_ count: Int) throws -> Data {
            try timed {
                var range = CFRange(location:0, length:count)
                guard let parameter = AXValueCreate(.cfRange, &range) else { throw DocumentError.fail("range unavailable") }
                var raw: AnyObject?
                let error = AXUIElementCopyParameterizedAttributeValue(pinned.body, "AXRTFForRange" as CFString, parameter, &raw)
                guard error == .success, let raw, CFGetTypeID(raw) == CFDataGetTypeID(),
                      let data = raw as? Data, !data.isEmpty, data.count <= 2 * 1024 * 1024,
                      data.starts(with: Data("{\\rtf".utf8)) else { throw DocumentError.fail("bounded RTF export unavailable") }
                return data
            }
        }
        let text = try timed { try documentCompleteText(pinned.body, budget:budget.intValue) }
        let first = try export(text.utf16.count)
        guard try timed({ try documentCompleteText(pinned.body, budget:budget.intValue) }) == text,
              try export(text.utf16.count) == first,
              try timed({ try documentCompleteText(pinned.body, budget:budget.intValue) }) == text else {
            return refuse()
        }
        try check()
        let reply: [String:Any] = ["type":"document_rtf_export", "document_token":token,
                "document_path":path as Any? ?? NSNull(), "utf16_length":text.utf16.count,
                "rtf_base64":first.base64EncodedString(), "rtf_sha256":sha256Hex(first),
                "text_sha256":sha256Hex(Data(text.utf8)), "evidence_kind":"ax_range_rtf_export",
                "source_format_verified":false, "attachment_completeness_verified":false,
                "authorizes_save":false]
        try check()
        return reply
    } catch { return refuse() }
}

func documentWindowForToken(_ target: String, _ token: String) throws -> AXUIElement {
    guard target == "com.apple.TextEdit", !token.isEmpty, token.count <= 128,
          let pinned = documentTokens.first(where: { $0.token == token })?.document else {
        throw DocumentError.fail("unknown or expired document token")
    }
    let (p, stamp) = try documentProcessIdentity(target)
    let documents = try documentEnumerate(p, selectedWindow: pinned.window, selectedPath: pinned.selectedPath)
    guard p == pinned.pid, stamp == pinned.stamp, documentMatches(documents, pinned) == 1 else {
        documentTokens.removeAll { $0.token == token }
        throw DocumentError.fail("document token is stale")
    }
    return pinned.window
}

func documentAttachmentMetadata(_ msg: [String:Any]) -> [String:Any] {
    func refuse() -> [String:Any] {
        ["type":"error", "error":"document_attachment_metadata_unavailable", "effect":"not_started"]
    }
    guard Set(msg.keys).isSubset(of: ["type","target","document_token","max_utf16_units","timeout_seconds"]),
          msg["target"] as? String == "com.apple.TextEdit",
          let token = msg["document_token"] as? String, !token.isEmpty, token.count <= 128,
          let budget = documentIsWholeNumber(msg["max_utf16_units"] ?? 100_000),
          budget.intValue >= 0, budget.intValue <= 100_000,
          let timeout = (msg["timeout_seconds"] ?? 3.0) as? NSNumber,
          CFGetTypeID(timeout) != CFBooleanGetTypeID(), timeout.doubleValue.isFinite,
          timeout.doubleValue > 0, timeout.doubleValue <= 6,
          let pinned = documentTokens.first(where: { $0.token == token })?.document else { return refuse() }
    let previousDeadline = documentOperationDeadline
    documentOperationDeadline = min(previousDeadline ?? .infinity,
        ProcessInfo.processInfo.systemUptime + timeout.doubleValue)
    defer { documentOperationDeadline = previousDeadline }
    do {
        let path = try documentPath(pinned.window)
        func check() throws {
            try documentCheckAbort()
            guard CFEqual(try documentWindowForToken("com.apple.TextEdit", token), pinned.window),
                  let current = documentTokens.first(where: { $0.token == token })?.document,
                  current.pid == pinned.pid, current.stamp == pinned.stamp,
                  CFEqual(current.body, pinned.body), try documentPath(pinned.window) == path else {
                throw DocumentError.fail("document export identity or path changed")
            }
            try documentCheckAbort()
        }
        func timed<T>(_ operation: () throws -> T) throws -> T {
            try check()
            let remaining = documentOperationDeadline! - ProcessInfo.processInfo.systemUptime
            guard remaining > 0, AXUIElementSetMessagingTimeout(pinned.body, Float(min(0.5, remaining))) == .success else {
                throw DocumentError.fail("document export AX timeout unavailable")
            }
            defer { _ = AXUIElementSetMessagingTimeout(pinned.body, 0) }
            let result = try operation()
            guard AXUIElementSetMessagingTimeout(pinned.body, 0) == .success else {
                throw DocumentError.fail("document export AX timeout reset failed")
            }
            try check()
            return result
        }
        let text = try timed { try documentCompleteText(pinned.body, budget:budget.intValue) }
        let count = text.utf16.count
        var positions: [Int] = []
        for (index, unit) in text.utf16.enumerated() {
            try documentCheckAbort()
            if unit == 0xFFFC {
                guard positions.count < 256 else { return refuse() }
                positions.append(index)
            }
        }
        func snapshot() throws -> [[String:Any]] {
            try timed {
                var range = CFRange(location:0, length:count)
                guard let parameter = AXValueCreate(.cfRange, &range) else { throw DocumentError.fail("range unavailable") }
                var raw: AnyObject?
                guard AXUIElementCopyParameterizedAttributeValue(pinned.body, "AXAttributedStringForRange" as CFString, parameter, &raw) == .success,
                      let raw, CFGetTypeID(raw) == CFAttributedStringGetTypeID(),
                      let value = raw as? NSAttributedString, value.length == count else {
                    throw DocumentError.fail("attributed metadata unavailable")
                }
                let captured = value.attributedSubstring(from:NSRange(location:0, length:count))
                try documentCheckAbort()
                guard value.length == count, captured.length == count, captured.string == text else {
                    throw DocumentError.fail("attributed text changed")
                }
                var rows: [[String:Any]] = []
                for index in positions {
                    try documentCheckAbort()
                    let attachment = captured.attribute(NSAttributedString.Key("AXAttachment"), at:index, effectiveRange:nil)
                    let isElement = attachment.map { CFGetTypeID($0 as CFTypeRef) == AXUIElementGetTypeID() } ?? false
                    rows.append(["utf16_index":index, "ax_attachment_present":attachment != nil,
                                 "ax_element_reference":isElement])
                    try documentCheckAbort()
                }
                return rows
            }
        }
        let first = try snapshot()
        guard try timed({ try documentCompleteText(pinned.body, budget:budget.intValue) }) == text,
              (try snapshot() as NSArray).isEqual(to:first),
              try timed({ try documentCompleteText(pinned.body, budget:budget.intValue) }) == text else { return refuse() }
        let reply: [String:Any] = ["type":"document_attachment_metadata", "document_token":token,
                "document_path":path as Any? ?? NSNull(), "utf16_length":count,
                "text_sha256":sha256Hex(Data(text.utf8)), "occurrences":first,
                "evidence_kind":"ax_attributed_attachment_metadata", "sampled_stable":true,
                "proves_no_intermediate_change":false, "source_format_verified":false,
                "attachment_completeness_verified":false, "attachment_payload_verified":false,
                "authorizes_save":false]
        try check()
        return reply
    } catch { return refuse() }
}

func documentScopedSaveAction(_ action: [String: Any], selectedPath: String?) -> Bool {
    guard Set(action.keys) == Set(["name", "path", "focused_document"]),
          action["name"] as? String == "menu",
          let selectedPath, let wanted = action["focused_document"] as? String,
          wanted.precomposedStringWithCanonicalMapping == selectedPath.precomposedStringWithCanonicalMapping,
          let path = action["path"] as? [Any], path.count == 2,
          path[0] as? String == "File",
          let titles = path[1] as? [String], titles == ["Save", "저장"] else { return false }
    return true
}

func validateDocumentObservation(_ target: String) throws {
    if let token = latestDocumentToken {
        _ = try documentWindowForToken(target, token)
    }
}

func documentValidateElement(_ element: AXUIElement, window: AXUIElement) throws {
    var node = element
    for _ in 0..<64 {
        try documentCheckAbort()
        if CFEqual(node, window) { return }
        if let owner = attr(node, "AXWindow") {
            guard CFGetTypeID(owner) == AXUIElementGetTypeID(), CFEqual(owner, window) else {
                throw DocumentError.fail("element belongs to a different document window")
            }
            return
        }
        guard let parent = attr(node, "AXParent"), CFGetTypeID(parent) == AXUIElementGetTypeID() else { break }
        node = unsafeBitCast(parent, to: AXUIElement.self)
    }
    throw DocumentError.fail("element document ancestry unavailable")
}

// Panel pins are connection-owned read-only witnesses, never save receipts.
struct PinnedSavePanel {
    let pid: pid_t
    let start: Double
    let window: AXUIElement
    let documentToken: String?
    let witness: NativeSavePanelWitness
}
var savePanelTokens: [(token: String, panel: PinnedSavePanel)] = []

func saveDirectoryResource(_ raw: AnyObject) throws -> NativeSaveDirectoryResource {
    let rawText: String
    if let url = raw as? NSURL, let absolute = url.absoluteString { rawText = absolute }
    else if CFGetTypeID(raw) == CFStringGetTypeID(), let text = raw as? String { rawText = text }
    else { throw NativeSavePanelError.unavailable("directory URL unavailable") }
    let text = rawText.hasSuffix("/") && rawText != "file:///" ? String(rawText.dropLast()) : rawText
    let path = try documentURLPath(text)
    var info = stat()
    guard lstat(path, &info) == 0, info.st_mode & S_IFMT == S_IFDIR else {
        throw NativeSavePanelError.unavailable("selected resource is not a directory")
    }
    return NativeSaveDirectoryResource(path: path, device: Int64(info.st_dev), inode: UInt64(info.st_ino))
}

// A single-use pre-dispatch guard, not source-format or saved-file evidence.
// Native AX reads are not atomic with AXPress; ABA remains a host limitation.
func validateSavePanelDispatch(_ obsID: String, _ target: String, _ action: [String: Any],
                               _ token: String, deadline: Double) -> [String: Any]? {
    func refuse() -> [String: Any] {
        ["type":"error", "error":"save_panel_guard_unavailable", "effect":"not_started"]
    }
    guard !abortState.isStopped, ProcessInfo.processInfo.systemUptime < deadline,
          obsID == latestObsID, Set(action.keys) == Set(["name", "element_index"]),
          action["name"] as? String == "click",
          let index = documentIsWholeNumber(action["element_index"] as Any),
          index.intValue >= 0, index.intValue < latestElements.count,
          let saved = savePanelTokens.first(where: { $0.token == token }),
          saved.panel.pid == latestTargetPID,
          CFEqual(saved.panel.witness.commit, latestElements[index.intValue]) else { return refuse() }
    let windows = latestElements.indices.filter { CFEqual(latestElements[$0], saved.panel.window) }
    guard windows.count == 1 else { return refuse() }
    var request: [String: Any] = ["type":"save_panel", "obs_id":obsID, "target":target,
        "window_element_index":windows[0], "panel_token":token, "require_format":true]
    if let documentToken = saved.panel.documentToken { request["document_token"] = documentToken }
    let result = readSavePanel(request, operationDeadline: deadline)
    guard result["type"] as? String == "save_panel", result["panel_token"] as? String == token,
          let control = result["commit_control"] as? [String:Any],
          control["obs_id"] as? String == obsID, control["enabled"] as? Bool == true,
          let bound = control["action"] as? [String:Any], bound["name"] as? String == "click",
          let boundIndex = documentIsWholeNumber(bound["element_index"] as Any),
          boundIndex.intValue == index.intValue,
          !abortState.isStopped, ProcessInfo.processInfo.systemUptime < deadline else { return refuse() }
    // Consume before the one AXPress attempt, including any uncertain effect.
    // A new observation and new read-only witness are required for later use.
    savePanelTokens.removeAll { $0.token == token }
    return nil
}

func readSavePanel(_ msg: [String: Any], operationDeadline: Double? = nil) -> [String: Any] {
    var budgetUsage: [String: Int]?
    func refuse(_ detail: String) -> [String: Any] {
        var result: [String: Any] = ["type":"error", "error":abortState.isStopped ? "aborted_by_user" : "save_panel_unavailable",
                                    "effect":"not_started", "detail":detail]
        if let budgetUsage { result["observation_budget"] = budgetUsage }
        return result
    }
    let allowed: Set<String> = ["type", "target", "obs_id", "window_element_index", "panel_token", "require_format", "document_token"]
    guard Set(msg.keys).isSubset(of: allowed), let target = msg["target"] as? String, !target.isEmpty,
          let obsID = msg["obs_id"] as? String, !obsID.isEmpty, obsID.count <= 128,
          let rawIndex = msg["window_element_index"], let index = documentIsWholeNumber(rawIndex),
          index.intValue >= 0, index.intValue < latestElements.count else {
        return refuse("invalid save panel observation request")
    }
    var requireFormat = false
    if let raw = msg["require_format"] {
        guard CFGetTypeID(raw as CFTypeRef) == CFBooleanGetTypeID(), let boolean = raw as? Bool else {
            return refuse("require_format must be a boolean")
        }
        requireFormat = boolean
    }
    var requested: String?
    if let raw = msg["panel_token"] {
        guard let text = raw as? String, !text.isEmpty, text.count <= 128 else { return refuse("invalid panel token") }
        requested = text
    }
    var documentToken: String?
    if let raw = msg["document_token"] {
        guard target == "com.apple.TextEdit", let token = raw as? String,
              !token.isEmpty, token.count <= 128 else { return refuse("invalid document token") }
        documentToken = token
    }
    guard latestObsID == obsID, latestTargetName.lowercased() == target.lowercased(),
          let p = pid(forBundle: target), p == latestTargetPID,
          let start = latestProcessStart, nativeProcessStartIdentity(p) == start else {
        return refuse("stale native observation")
    }
    let window = latestElements[index.intValue]
    let map = latestElements
    do {
        let deadline = min(operationDeadline ?? .infinity, ProcessInfo.processInfo.systemUptime + 6)
        var document: PinnedDocument?
        if let documentToken {
            guard let pinned = documentTokens.first(where: { $0.token == documentToken })?.document,
                  pinned.pid == p, CFEqual(pinned.window, window) else {
                return refuse("unknown, expired or mismatched document token")
            }
            document = pinned
        }
        func context() throws {
            try documentCheckAbort()
            guard ProcessInfo.processInfo.systemUptime < deadline,
                  latestObsID == obsID, latestTargetPID == p, latestElements.count == map.count,
                  zip(latestElements, map).allSatisfy({ CFEqual($0, $1) }),
                  pid(forBundle: target) == p, nativeProcessStartIdentity(p) == start else {
                throw NativeSavePanelError.unavailable("native observation changed or expired")
            }
            if let document, let documentToken {
                _ = try documentWindowForToken(target, documentToken)
                guard let pinned = documentTokens.first(where: { $0.token == documentToken })?.document,
                      pinned.pid == document.pid, pinned.stamp == document.stamp,
                      CFEqual(pinned.window, document.window), CFEqual(pinned.body, document.body) else {
                    throw NativeSavePanelError.unavailable("document token changed or expired")
                }
                let (documentPID, stamp) = try documentProcessIdentity(target)
                guard documentPID == p, stamp == document.stamp else {
                    throw NativeSavePanelError.unavailable("document process identity changed")
                }
            }
        }
        func timedRead(_ element: AXUIElement, _ name: String) throws -> (AXError, AnyObject?) {
            try context()
            let remaining = deadline - ProcessInfo.processInfo.systemUptime
            guard remaining > 0, AXUIElementSetMessagingTimeout(element, Float(min(1, remaining))) == .success else {
                throw NativeSavePanelError.unavailable("AX timeout unavailable")
            }
            var result: AnyObject?
            let error = AXUIElementCopyAttributeValue(element, name as CFString, &result)
            guard AXUIElementSetMessagingTimeout(element, 0) == .success else {
                throw NativeSavePanelError.unavailable("AX timeout reset failed")
            }
            try context()
            return (error, result)
        }
        func validate() throws {
            let (error, raw) = try timedRead(AXUIElementCreateApplication(p), "AXWindows")
            guard error == .success, let raw, CFGetTypeID(raw) == CFArrayGetTypeID(),
                  let items = raw as? NSArray, items.count <= 5000 else {
                throw NativeSavePanelError.unavailable("application window list unavailable")
            }
            var matches = 0
            for item in items {
                try context()
                guard CFGetTypeID(item as CFTypeRef) == AXUIElementGetTypeID() else {
                    throw NativeSavePanelError.unavailable("non-native application window")
                }
                if CFEqual(item as! AXUIElement, window) { matches += 1 }
            }
            guard matches == 1 else {
                throw NativeSavePanelError.unavailable("save window no longer owned by application")
            }
        }
        let (roleError, roleValue) = try timedRead(window, "AXRole")
        guard roleError == .success, roleValue as? String == "AXWindow" else { return refuse("window element required") }
        var owner: pid_t = -1
        guard AXUIElementGetPid(window, &owner) == .success, owner == p else { return refuse("window owner changed") }
        try validate()
        let reader = try NativeSavePanelReader(readAttribute: timedRead,
            directoryResource: saveDirectoryResource, check: context)
        defer { budgetUsage = reader.budgetUsage }
        if let document { try reader.validateDocument(window, body: document.body) }
        var witness: NativeSavePanelWitness
        if let requested {
            guard let saved = savePanelTokens.first(where: { $0.token == requested }),
                  saved.panel.pid == p, saved.panel.start == start, CFEqual(saved.panel.window, window),
                  saved.panel.documentToken == documentToken else {
                return refuse("unknown, expired or mismatched panel token")
            }
            witness = saved.panel.witness
            try reader.revalidate(window, witness: witness, requireFormat: requireFormat)
        } else { witness = try reader.read(window, requireFormat: requireFormat) }
        let commitIndex = try reader.bindCommit(witness, elements: map)
        try reader.revalidate(window, witness: witness, requireFormat: requireFormat)
        if let document { try reader.validateDocument(window, body: document.body) }
        try validate()
        let token = requested ?? UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        if requested == nil {
            savePanelTokens.append((token, PinnedSavePanel(pid:p, start:start, window:window,
                documentToken:documentToken, witness:witness)))
            if savePanelTokens.count > 32 { savePanelTokens.removeFirst() }
        }
        var response: [String: Any] = ["type":"save_panel", "panel_token":token, "obs_id":obsID,
                "window_element_index":index.intValue, "directory":witness.directory.path,
                "filename":witness.filename, "format":witness.format ?? NSNull(),
                "format_value":witness.formatValue ?? NSNull(),
                "original_changes_setting":witness.keepChanges as Any? ?? NSNull(),
                "commit_control":["obs_id":obsID, "enabled":true,
                                  "action":["name":"click", "element_index":commitIndex]],
                "authorizes_save":false, "observation_budget":reader.budgetUsage]
        if let documentToken { response["document_token"] = documentToken }
        return response
    } catch NativeSavePanelError.unavailable(let detail) {
        return refuse(detail)
    } catch let DocumentError.fail(detail) {
        return refuse(detail)
    } catch { return refuse("save panel observation failed") }
}

// Read-only: resolve only a running process, never prepare/activate the app.
func readMenuState(_ msg: [String: Any]) -> [String: Any] {
    func refuse(_ error: String) -> [String: Any] {
        ["type": "error", "error": error, "effect": "not_started"]
    }
    let listing = msg["type"] as? String == "menu_items"
    guard Set(msg.keys) == Set(["type", "target", "path"]),
          let target = msg["target"] as? String, !target.isEmpty,
          let rawPath = msg["path"] as? [Any], (listing || !rawPath.isEmpty), rawPath.count <= 6 else {
        return refuse("invalid_menu_path")
    }
    var path: [[String]] = []
    for raw in rawPath {
        let titles: [String]
        if let title = raw as? String { titles = [title] }
        else if let alternatives = raw as? [String] { titles = alternatives }
        else { return refuse("invalid_menu_path") }
        guard !titles.isEmpty, titles.count <= 8, titles.allSatisfy({
            !$0.isEmpty && $0.count <= 200 && !$0.unicodeScalars.contains {
                $0.value < 0x20 || $0.value == 0x7F
            }
        }) else { return refuse("invalid_menu_path") }
        path.append(titles)
    }
    let deadline = ProcessInfo.processInfo.systemUptime + 5
    var readError: String?
    var reads = 0
    func read(_ element: AXUIElement, _ attribute: String, operation: String = "value", count: Int = 0) -> Any? {
        if readError != nil { return nil }
        if attribute == kAXChildrenAttribute as String && operation == "value" {
            guard let size = read(element, attribute, operation: "count") as? Int else { return nil }
            guard size >= 0 && size <= 5000 else { readError = "menu_read_budget_exceeded"; return nil }
            let children: [AXUIElement]
            if size == 0 { children = [] }
            else {
                guard let found = read(element, attribute, operation: "range", count: size) as? [AXUIElement] else {
                    if readError == nil { readError = "menu_state_changed" }; return nil
                }
                children = found
            }
            guard let after = read(element, attribute, operation: "count") as? Int else {
                if readError == nil { readError = "menu_state_changed" }; return nil
            }
            guard after == size && children.count == size else {
                readError = "menu_state_changed"; return nil
            }
            return children
        }
        if abortState.isStopped { readError = "aborted_by_user"; return nil }
        if ProcessInfo.processInfo.systemUptime >= deadline {
            readError = "menu_read_timeout"; return nil
        }
        guard reads < 5000 else { readError = "menu_read_budget_exceeded"; return nil }
        reads += 1
        let remaining = deadline - ProcessInfo.processInfo.systemUptime
        let timeout = Float(min(1, remaining))
        guard timeout > 0 else { readError = "menu_read_timeout"; return nil }
        guard AXUIElementSetMessagingTimeout(element, timeout) == .success else {
            readError = "menu_timeout_unavailable"; return nil
        }
        // These are fresh menu-query objects. Reset only this object's override,
        // never the process-wide timeout (equal AX objects do not share it).
        defer {
            if AXUIElementSetMessagingTimeout(element, 0) != .success && readError == nil {
                readError = "menu_timeout_reset_failed"
            }
        }
        var value: CFTypeRef?
        let status: AXError
        if operation == "count" {
            var size: CFIndex = 0
            status = AXUIElementGetAttributeValueCount(element, attribute as CFString, &size)
            value = NSNumber(value: size)
        } else if operation == "range" {
            var array: CFArray?
            status = AXUIElementCopyAttributeValues(element, attribute as CFString, 0, count, &array)
            value = array
        } else {
            status = AXUIElementCopyAttributeValue(element, attribute as CFString, &value)
        }
        if abortState.isStopped { readError = "aborted_by_user"; return nil }
        if ProcessInfo.processInfo.systemUptime >= deadline {
            readError = "menu_read_timeout"; return nil
        }
        if status != .success {
            if status.rawValue == -25205 || status.rawValue == -25212 { return nil }
            readError = "menu_read_failed"
            return nil
        }
        if attribute == "AXMenuBar", let value = value,
           CFGetTypeID(value) != AXUIElementGetTypeID() {
            readError = "menu_read_failed"; return nil
        }
        if attribute == kAXChildrenAttribute as String, operation != "count", value != nil,
           !(value is [AXUIElement]) {
            readError = "menu_read_failed"; return nil
        }
        if attribute == kAXChildrenAttribute as String, operation != "count",
           let children = value as? [AXUIElement], children.count > 5000 {
            readError = "menu_read_budget_exceeded"; return nil
        }
        return value
    }
    guard let p = pid(forBundle: target) else { return refuse("target_not_found") }
    guard let start = nativeProcessStartIdentity(p) else { return refuse("target_changed") }
    if listing {
        // One level only. All reads share the deadline and operation budget.
        guard let rawBar = read(AXUIElementCreateApplication(p), "AXMenuBar"),
              CFGetTypeID(rawBar as CFTypeRef) == AXUIElementGetTypeID() else {
            return refuse(readError ?? "menu_not_found")
        }
        var node = rawBar as! AXUIElement
        var walked: [String] = []
        func titledChildren(_ parent: AXUIElement) -> [(AXUIElement, String)] {
            var found: [(AXUIElement, String)] = []
            var seen = Set<String>()
            guard let rawChildren = read(parent, kAXChildrenAttribute as String) as? [AXUIElement] else {
                readError = readError ?? "menu_state_unavailable"; return []
            }
            var children = rawChildren
            for child in rawChildren {
                if (read(child, kAXRoleAttribute as String) as? String) == "AXMenu" {
                    guard let nested = read(child, kAXChildrenAttribute as String) as? [AXUIElement] else {
                        readError = readError ?? "menu_state_unavailable"; return []
                    }
                    children = nested; break
                }
            }
            for child in children {
                guard let raw = read(child, kAXTitleAttribute as String) else { continue }
                guard let title = raw as? String, title.count <= 200,
                      !title.unicodeScalars.contains(where: { $0.value < 0x20 || $0.value == 0x7F }) else {
                    readError = readError ?? "invalid_menu_title"; return []
                }
                if title.isEmpty { continue }
                guard seen.insert(title).inserted else {
                    readError = readError ?? "menu_ambiguous"; return []
                }
                found.append((child, title))
            }
            return found
        }
        for wants in path {
            let matches = titledChildren(node).filter { wants.contains($0.1) }
            if let error = readError { return refuse(error) }
            guard matches.count <= 1 else { return refuse("menu_ambiguous") }
            guard let hit = matches.first else { return refuse("menu_not_found") }
            node = hit.0; walked.append(hit.1)
        }
        var items: [[String: Any]] = []
        for (child, title) in titledChildren(node) {
            let rawEnabled = read(child, kAXEnabledAttribute as String)
            let enabled: Any = rawEnabled.flatMap {
                CFGetTypeID($0 as CFTypeRef) == CFBooleanGetTypeID() ? $0 : nil
            } ?? NSNull()
            let children = read(child, kAXChildrenAttribute as String) as? [AXUIElement]
            var submenu: Any = NSNull()
            if let children = children {
                var hasMenu = false
                var unknownRole = false
                for descendant in children {
                    if let role = read(descendant, kAXRoleAttribute as String) as? String {
                        if role == "AXMenu" { hasMenu = true }
                    } else { unknownRole = true }
                }
                if hasMenu { submenu = true }
                else if !unknownRole { submenu = false }
            }
            items.append(["title": title, "path": walked + [title],
                          "enabled": enabled, "has_submenu": submenu])
        }
        if let error = readError { return refuse(error) }
        if abortState.isStopped { return refuse("aborted_by_user") }
        guard pid(forBundle: target) == p, nativeProcessStartIdentity(p) == start else {
            return refuse("target_changed")
        }
        return ["type": "menu_items", "path": walked, "items": items, "complete": true]
    }
    guard let (item, walked) = menuItem(pid: p, path: path, read: { read($0, $1) }) else {
        return refuse(readError ?? "menu_not_found")
    }
    guard let enabled = read(item, kAXEnabledAttribute as String),
          CFGetTypeID(enabled as CFTypeRef) == CFBooleanGetTypeID() else {
        return refuse(readError ?? "menu_state_unavailable")
    }
    let rawFrontmost = read(AXUIElementCreateApplication(p), "AXFrontmost")
    let frontmost: Any = rawFrontmost.flatMap {
        CFGetTypeID($0 as CFTypeRef) == CFBooleanGetTypeID() ? $0 : nil
    } ?? NSNull()
    let mark = read(item, "AXMenuItemMarkChar") as? String
    let shortcut = read(item, "AXMenuItemCmdChar") as? String
    if let error = readError { return refuse(error) }
    if abortState.isStopped { return refuse("aborted_by_user") }
    guard pid(forBundle: target) == p, nativeProcessStartIdentity(p) == start else { return refuse("target_changed") }
    return ["type": "menu_state", "path": walked, "enabled": enabled, "target_ax_frontmost": frontmost,
            "mark": mark as Any? ?? NSNull(), "shortcut": shortcut as Any? ?? NSNull()]
}

func handle(_ msg: [String: Any]) -> [String: Any] {
    let kind = msg["type"] as? String
    // After the user stops this run, nothing looks at their screen either.
    // Reading is not a lesser thing than acting here: the stop is about the
    // computer no longer being driven, and an agent that can still watch has
    // not been stopped (peer review). `ping` is exempt so a client can find
    // out that this is what happened.
    if kind != "ping", abortState.isStopped {
        return ["type": "error", "error": "aborted_by_user",
                "effect": "not_started",
                "detail": "사용자가 Esc로 중단했습니다 — 이 실행에서는 더 진행하지 않습니다"]
    }
    // Every message that reaches into another app says so on screen, decided
    // here rather than in each case. `observe` and `act` already did it and
    // the rest did not, so a caller could read a document, walk a menu bar or
    // take a selection with nothing on screen to say it was happening — the
    // same gap the Python driver had, found the same way (2026-09-20).
    //
    // Reading is not the lesser thing: the note on `observe` is that showing
    // the outline for reads too is what keeps confidentiality protected, not
    // just integrity. So these refuse when the outline cannot be drawn,
    // exactly as `observe` does.
    let reaching: Set<String> = ["menu_state", "menu_items", "probe",
                                 "selection", "document_text", "save_panel", "document_rtf_export", "document_attachment_metadata", "readiness_read_cost", "readiness_preflight"]
    if let kind, reaching.contains(kind) || kind == "capture",
       let target = msg["target"] as? String, let p = pid(forBundle: target) {
        guard ensureVisibleControl(forPID: p, windowless: kind == "menu_state" || kind == "menu_items") else {
            return indicatorRefusal(kind)
        }
    }

    switch kind {
    case "ping":
        // NOT where the abort clears. A client that could ping its way past Esc
        // is a client that can ignore the stop, and the stop is the one thing
        // the user holds over this helper (peer correction — an earlier version
        // of this cleared it here, which was wrong in principle and would have
        // been wrong quietly).
        //
        // What this build can actually honour. A client that needs `expect`
        // has no way to tell a helper that checks it from one that ignores it
        // — and an ignored precondition looks exactly like a satisfied one.
        // Naming them here lets the caller refuse instead of guess (peer
        // request).
        // Screen capture is granted to THIS process's identity (it is opened
        // through LaunchServices), so only it can say whether it holds it; a
        // Python asking about itself answered for the wrong app, and `tacto
        // doctor` told people to enable "this app" without saying which.
        var reply: [String: Any] = ["type": "pong", "trusted": AXIsProcessTrusted(),
                "screen_capture": CGPreflightScreenCaptureAccess(),
                "capabilities": helperCapabilities]
        if let start = nativeProcessStartIdentity(getpid()) {
            reply["helper_process"] = ["pid": Int(getpid()), "start_time": start]
        }
        return reply
    case "list_targets":
        let apps = NSWorkspace.shared.runningApplications
            .filter { $0.activationPolicy == .regular }
            .map { ["id": $0.bundleIdentifier ?? "", "displayName": $0.localizedName ?? "",
                    "pid": Int($0.processIdentifier), "isRunning": true] as [String: Any] }
        return ["type": "targets", "apps": apps]
    case "capture":
        guard Set(msg.keys).isSubset(of: ["type", "target", "obs_id", "window_index", "timeout_seconds"]),
              let target = msg["target"] as? String, !target.isEmpty,
              let obsID = msg["obs_id"] as? String, !obsID.isEmpty else {
            return ["type": "error", "error": "invalid_capture_request",
                    "effect": "not_started"]
        }
        var windowIndex: Int?
        if let raw = msg["window_index"] {
            guard let number = raw as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(),
                  number.doubleValue == Double(number.intValue),
                  number.intValue >= 0, number.intValue < 64 else {
                return ["type": "error", "error": "invalid_capture_request",
                        "effect": "not_started", "detail": "window_index"]
            }
            windowIndex = number.intValue
        }
        var timeout = 5.0
        if let raw = msg["timeout_seconds"] {
            guard let number = raw as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(),
                  number.doubleValue.isFinite, number.doubleValue > 0,
                  number.doubleValue <= 10 else {
                return ["type": "error", "error": "invalid_capture_request",
                        "effect": "not_started", "detail": "timeout_seconds"]
            }
            timeout = number.doubleValue
        }
        return captureWindow(obsID, target, windowIndex: windowIndex,
                             deadline: ProcessInfo.processInfo.systemUptime + timeout)
    case "menu_state", "menu_items":
        return readMenuState(msg)
    case "observe":
        if let raw = msg["reveal"], validRevealRequest(raw) == nil {
            return ["type":"error", "error":"invalid_reveal", "effect":"not_started"]
        }
        if let raw = msg["window_identity"], CFGetTypeID(raw as CFTypeRef) != CFBooleanGetTypeID() {
            return ["type":"error", "error":"invalid_window_identity_request", "effect":"not_started"]
        }
        if let raw = msg["document_token"], !(raw is String) {
            return ["type":"error", "error":"document_snapshot_unavailable", "effect":"not_started"]
        }
        return observe(msg["target"] as? String ?? "",
                       requireWindow: msg["require_window"] as? Bool ?? true,
                       documentToken: msg["document_token"] as? String,
                       windowIdentity: msg["window_identity"] as? Bool ?? false,
                       reveal: msg["reveal"] as? [String])
    case "act":
        var savePanelToken: String?
        if let raw = msg["save_panel_token"] {
            guard let token = raw as? String, !token.isEmpty, token.count <= 128 else {
                return ["type":"error", "error":"save_panel_guard_unavailable", "effect":"not_started"]
            }
            savePanelToken = token
        }
        let opaque = (msg["action"] as? [String:Any])?["name"] as? String == "window_input"
        var windowInputAuthorized = false
        if opaque || msg["window_input_authorized"] != nil {
            guard opaque, let value = msg["window_input_authorized"] as? NSNumber,
                  CFGetTypeID(value) == CFBooleanGetTypeID(), value.boolValue else {
                return ["type":"error", "error":"window_input_authorization_required", "effect":"not_started"]
            }
            windowInputAuthorized = true
        }
        let returnKey = (msg["action"] as? [String:Any]).map {
            $0["name"] as? String == "press_key" && $0["key"] as? String == "Return" } ?? false
        var submitAuthorized = false
        if msg["submit_authorized"] != nil {
            guard returnKey, let value = msg["submit_authorized"] as? NSNumber,
                  CFGetTypeID(value) == CFBooleanGetTypeID(), value.boolValue else {
                return ["type":"error", "error":"submit_authorization_invalid", "effect":"not_started"]
            }
            submitAuthorized = true
        }
        let coordinate = ["click_point", "drag", "hover", "vroid_drag", "vroid_focus"].contains((msg["action"] as? [String:Any])?["name"] as? String ?? "")
        var coordinateAuthorized = false
        if coordinate || msg["coordinate_authorized"] != nil {
            guard coordinate, let value = msg["coordinate_authorized"] as? NSNumber,
                  CFGetTypeID(value) == CFBooleanGetTypeID(), value.boolValue else {
                return ["type":"error", "error":"coordinate_authorization_required", "effect":"not_started"]
            }
            coordinateAuthorized = true
        }
        var clickDeadline: Double?
        if let raw = msg["click_deadline_uptime"] {
            guard let action = msg["action"] as? [String: Any], ["click", "click_point", "drag", "hover", "vroid_drag", "vroid_focus", "prepare_window", "press_key", "window_input"].contains(action["name"] as? String ?? ""),
                  let number = raw as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(),
                  number.doubleValue.isFinite, number.doubleValue > 0 else {
                return ["type": "error", "error": "invalid_click_deadline", "effect": "not_started"]
            }
            clickDeadline = number.doubleValue
        }
        return act(msg["obs_id"] as? String ?? "", msg["target"] as? String ?? "",
                   msg["action"] as? [String: Any] ?? [:],
                   expect: msg["expect"] as? [String: Any], clickDeadline: clickDeadline,
                   coordinateAuthorized: coordinateAuthorized, savePanelToken: savePanelToken, windowInputAuthorized: windowInputAuthorized,
                   submitAuthorized: submitAuthorized)
    case "readiness_preflight":
        return readinessPreflight(msg)
    case "readiness_read_cost":
        return readinessReadCost(msg)
    case "probe":
        return probe(msg["obs_id"] as? String ?? "", msg["target"] as? String ?? "",
                     msg["element_index"] as? Int ?? -1)
    case "selection":
        return selectionOf(msg["target"] as? String ?? "")
    case "save_panel":
        return readSavePanel(msg)
    case "document_window_status":
        return documentWindowStatus(msg)
    case "document_attachment_metadata":
        return documentAttachmentMetadata(msg)
    case "document_rtf_export":
        return documentRTFExport(msg)
    case "document_text":
        // Budget is the caller's, bounded here as well: a client asking for a
        // gigabyte of text is asking this process to allocate it.
        var budget = documentTextBudgetLimit
        if let asked = msg["max_utf16_units"] {
            guard let number = documentIsWholeNumber(asked), number.intValue >= 0,
                  number.intValue <= documentTextBudgetLimit else {
                return ["type": "error", "error": "document_snapshot_unavailable",
                        "effect": "not_started", "detail": "invalid max_utf16_units"]
            }
            budget = number.intValue
        }
        if msg["document_token"] != nil, !(msg["document_token"] is String) {
            return ["type": "error", "error": "document_snapshot_unavailable",
                    "effect": "not_started", "detail": "invalid document token"]
        }
        if msg["document_path"] != nil, !(msg["document_path"] is String) {
            return ["type":"error", "error":"document_snapshot_unavailable",
                    "effect":"not_started", "detail":"invalid document path selector"]
        }
        return documentText(msg["target"] as? String ?? "",
                            token: msg["document_token"] as? String, budget: budget,
                            selectedPath: msg["document_path"] as? String)
    default:
        return ["type": "error", "error": "unknown_message"]
    }
}

// The helper holds the permissions, so it must not be a general-purpose remote:
// it serves exactly one client — the process that spawned it — and exits when
// that process goes away. It speaks only over the stdio pair it was launched
// with, never a socket, so there is no channel for another process to reach.
// A parent-PID liveness check is not authentication: any local process could
// spawn this signed binary and inherit its Accessibility grant. Requiring a
// secret the launcher passes in raises that bar — a process that cannot read
// our environment cannot drive us. It is a mitigation, not a boundary: proper
// caller authentication needs XPC with the client's audit token, which is the
// documented next step (PROTOCOL.md §3.8).
// LIMITATION, stated plainly: this token is NOT an authentication boundary. A
// local process that can execute this binary can also set this variable for its
// own child. What it does buy is that the helper cannot be driven by accident
// or by something that merely inherited a pipe, and that every launch is
// deliberate. A real boundary requires the OS to tell us who the caller is —
// an XPC service checking the client's audit token and code-signing
// requirement — which is the documented next step (PROTOCOL.md §3.8).
// Socket mode only. A stdio fallback used to exist, guarded by a launch token
// and an "is a signed launcher among my ancestors" check — but the Python the
// launcher starts IS such a descendant, so it could spawn this helper itself
// and reach handle() without passing through the launcher's message vetting
// (advisor finding: a bypass of the boundary we had just built). No fallback.
// `--indicator-menu-probe <seconds>`: show the indicator, then CHOOSE STOP
// from its own menu and report whether the run is actually stopped.
//
// An indicator that changes its own label and leaves the run going would be
// worse than no indicator, so the menu item is not trusted to mean what it
// says: this performs the real NSMenuItem's action through its real target and
// then asks `abortState`, which is the thing every action consults.
if let index = CommandLine.arguments.firstIndex(of: "--indicator-menu-probe"),
   index + 1 < CommandLine.arguments.count,
   let seconds = Double(CommandLine.arguments[index + 1]) {
    let app = NSApplication.shared
    _ = app.setActivationPolicy(.accessory)
    DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
        abortState.begin()
        let shown = showControlIndicator(forPID: getpid())
        let menu: NSMenu? = controlMenu(for:NSRunningApplication.current.localizedName)
        let stop = menu?.items.last
        var report = "{\"shown\": \(shown)"
        report += ", \"menu_items\": \(menu?.items.count ?? 0)"
        report += ", \"stop_title\": \"\(stop?.title ?? "none")\""
        report += ", \"header\": \"\(menu?.items.first?.title ?? "none")\""
        report += ", \"stopped_before\": \(abortState.isStopped)"
        // The real item, its real target, its real action.
        if let stop, let target = stop.target, let action = stop.action {
            _ = target.perform(action, with: stop)
        }
        Thread.sleep(forTimeInterval: 0.3)
        report += ", \"stopped_after\": \(abortState.isStopped)}"
        note("tacto-helper: indicator menu probe \(report)")
        if let out = CommandLine.arguments.firstIndex(of: "--probe-out"),
           out + 1 < CommandLine.arguments.count {
            try? report.write(toFile: CommandLine.arguments[out + 1],
                              atomically: true, encoding: .utf8)
        }
    }
    DispatchQueue.global().asyncAfter(deadline: .now() + min(120, max(1, seconds))) {
        exit(0)
    }
    app.run()
    exit(0)
}

// `--hold-indicator <seconds>`: run the PRODUCTION gate and photograph it.
//
// Every previous attempt to see this either raced the run or tested a probe
// that merely resembled the real path. This calls `ensureVisibleControl` — the
// exact function every gated message calls — holds it, and photographs the
// menu bar from inside the same process, so the picture cannot miss the
// window it is supposed to show.
if let index = CommandLine.arguments.firstIndex(of: "--hold-indicator"),
   index + 1 < CommandLine.arguments.count,
   let seconds = Double(CommandLine.arguments[index + 1]) {
    let app = NSApplication.shared
    _ = app.setActivationPolicy(.accessory)
    // Whose window to hold the indicator on. Defaults to this process, which
    // has no windows — so a target has to be named for the test to mean
    // anything.
    var holdTarget = getpid()
    if let t = CommandLine.arguments.firstIndex(of: "--hold-target"),
       t + 1 < CommandLine.arguments.count, let given = Int32(CommandLine.arguments[t + 1]) {
        holdTarget = given
    }
    // On the main queue AFTER `app.run()` begins servicing it, not from a
    // background thread before. `ensureVisibleControl` does a
    // `DispatchQueue.main.sync`, and a main.sync issued while the main queue is
    // still on its way to `app.run()` simply waits — which is why this block
    // produced no log at all and looked like it never ran.
    DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
        _ = showControlIndicator(forPID: holdTarget)
        let ok = ensureControlStream(forPID: holdTarget)
        note("tacto-helper: hold-indicator target=\(holdTarget) -> \(ok)")
        // Give the indicator a moment to appear, then photograph.
        if let shot = CommandLine.arguments.firstIndex(of: "--probe-shot"),
           shot + 1 < CommandLine.arguments.count {
            let path = CommandLine.arguments[shot + 1]
            DispatchQueue.global().async {
            Thread.sleep(forTimeInterval: 4)
            SCShareableContent.getExcludingDesktopWindows(false, onScreenWindowsOnly: true) {
                content, _ in
                guard let content, let display = content.displays.first else { return }
                let f = SCContentFilter(display: display, excludingWindows: content.windows.filter { $0.owningApplication?.processID == getpid() })
                let c = SCStreamConfiguration()
                c.width = display.width * 2
                c.height = display.height * 2
                c.showsCursor = false
                SCScreenshotManager.captureImage(contentFilter: f, configuration: c) {
                    image, _ in
                    guard let image,
                          let dst = CGImageDestinationCreateWithURL(
                            URL(fileURLWithPath: path) as CFURL,
                            UTType.png.identifier as CFString, 1, nil) else { return }
                    CGImageDestinationAddImage(dst, image, nil)
                    CGImageDestinationFinalize(dst)
                    note("tacto-helper: hold-indicator photographed, stream=\(controlStream != nil)")
                }
            }
            }
        }
    }
    DispatchQueue.global().asyncAfter(deadline: .now() + min(120, max(1, seconds))) {
        exit(0)
    }
    app.run()
    exit(0)
}

// Historical flag now publishes metadata through the existing app menu.
if let index = CommandLine.arguments.firstIndex(of: "--indicator-probe"),
   index + 1 < CommandLine.arguments.count,
   let seconds = Double(CommandLine.arguments[index + 1]) {
    let app = NSApplication.shared
    app.setActivationPolicy(.accessory)
    abortState.begin()
    _ = showControlIndicator(forPID:getpid())
    DispatchQueue.main.asyncAfter(deadline:.now() + min(120,max(1,seconds))) {
        RuntimePublisher.shared.publish(active:false)
        exit(0)
    }
    app.run()
    exit(0)
}

// `--recording-indicator-probe <seconds>`: hold a capture stream and stop.
//
// The question this answers is not answerable from the API surface: does macOS
// light its own screen-recording indicator for a running SCStream, and does it
// stay lit for as long as the stream does? The indicator's state is not
// readable — there is no API for "is the indicator showing" — so the only way
// to know is to run one and look at the screen.
//
// A one-shot `SCScreenshotManager.captureImage` is NOT the same question. That
// is what the helper uses today, and a single frame may light nothing at all.
if let index = CommandLine.arguments.firstIndex(of: "--recording-indicator-probe"),
   index + 1 < CommandLine.arguments.count,
   let seconds = Double(CommandLine.arguments[index + 1]) {
    final class Sink: NSObject, SCStreamOutput {
        var frames = 0
        func stream(_ stream: SCStream, didOutputSampleBuffer buffer: CMSampleBuffer,
                    of type: SCStreamOutputType) {
            frames += 1
        }
    }
    let sink = Sink()
    let done = DispatchSemaphore(value: 0)
    var report = "{\"error\": \"no answer\"}"
    SCShareableContent.getExcludingDesktopWindows(true, onScreenWindowsOnly: true) {
        content, error in
        guard let content, let display = content.displays.first else {
            report = "{\"started\": false, \"why\": \"no display: "
                + "\(error?.localizedDescription ?? "none")\"}"
            done.signal(); return
        }
        let filter = SCContentFilter(display: display, excludingWindows: content.windows.filter { $0.owningApplication?.processID == getpid() })
        let configuration = SCStreamConfiguration()
        // Small and slow on purpose. Frames are not the point here; holding the
        // stream is. Anything captured is dropped on the floor.
        configuration.width = 320
        configuration.height = 200
        configuration.minimumFrameInterval = CMTime(value: 1, timescale: 2)
        configuration.showsCursor = false
        let stream = SCStream(filter: filter, configuration: configuration, delegate: nil)
        do {
            try stream.addStreamOutput(sink, type: .screen,
                                       sampleHandlerQueue: DispatchQueue.global())
        } catch {
            report = "{\"started\": false, \"why\": \"addStreamOutput: "
                + "\(error.localizedDescription)\"}"
            done.signal(); return
        }
        stream.startCapture { startError in
            if let startError {
                report = "{\"started\": false, \"why\": \"startCapture: "
                    + "\(startError.localizedDescription)\"}"
                done.signal(); return
            }
            let hold = min(120, max(1, seconds))
            FileHandle.standardError.write(Data(
                "스트림 시작됨 — \(hold)초 동안 화면 상단의 녹화 표시를 확인하세요\n".utf8))
            // Photograph the menu bar WHILE this stream is held. A menu-bar
            // item turned out to collapse behind the overflow chevron on a
            // full bar — reported visible, seen by nobody — so whether the
            // system's own indicator gets a slot it cannot lose is a question
            // to answer with a picture, not with an API's opinion.
            if let shot = CommandLine.arguments.firstIndex(of: "--probe-shot"),
               shot + 1 < CommandLine.arguments.count {
                let path = CommandLine.arguments[shot + 1]
                DispatchQueue.global().asyncAfter(deadline: .now() + 3) {
                    SCShareableContent.getExcludingDesktopWindows(false,
                                                                  onScreenWindowsOnly: true) {
                        content, _ in
                        guard let content, let display = content.displays.first else { return }
                        let f = SCContentFilter(display: display, excludingWindows: content.windows.filter { $0.owningApplication?.processID == getpid() })
                        let c = SCStreamConfiguration()
                        c.width = display.width * 2
                        c.height = display.height * 2
                        c.showsCursor = false
                        SCScreenshotManager.captureImage(contentFilter: f, configuration: c) {
                            image, _ in
                            guard let image,
                                  let dst = CGImageDestinationCreateWithURL(
                                    URL(fileURLWithPath: path) as CFURL,
                                    UTType.png.identifier as CFString, 1, nil) else { return }
                            CGImageDestinationAddImage(dst, image, nil)
                            CGImageDestinationFinalize(dst)
                        }
                    }
                }
            }
            DispatchQueue.global().asyncAfter(deadline: .now() + hold) {
                stream.stopCapture { _ in
                    report = "{\"started\": true, \"held_seconds\": \(hold), "
                        + "\"frames\": \(sink.frames)}"
                    done.signal()
                }
            }
        }
    }
    _ = done.wait(timeout: .now() + min(140, max(5, seconds + 20)))
    print(report)
    if let out = CommandLine.arguments.firstIndex(of: "--probe-out"),
       out + 1 < CommandLine.arguments.count {
        try? report.write(toFile: CommandLine.arguments[out + 1],
                          atomically: true, encoding: .utf8)
    }
    exit(0)
}

// `--request-accessibility`: ask for the grant in this process's own name.
//
// It has to be opened through LaunchServices to mean anything. The prompt names
// the identity the request is ATTRIBUTED to, and for a spawned child that is
// whoever spawned it — which is how the helper came to be running on the
// terminal's accessibility grant while believing the grant was its own.
if CommandLine.arguments.contains("--request-accessibility") {
    let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue()
                     as String: true] as CFDictionary
    let trusted = AXIsProcessTrustedWithOptions(options)
    let answer = "{\"accessibility_trusted\": \(trusted), "
        + "\"bundle_id\": \"\(Bundle.main.bundleIdentifier ?? "none")\", "
        + "\"pid\": \(getpid()), \"ppid\": \(getppid())}"
    print(answer)
    if let index = CommandLine.arguments.firstIndex(of: "--request-accessibility"),
       index + 1 < CommandLine.arguments.count,
       !CommandLine.arguments[index + 1].hasPrefix("-") {
        try? answer.write(toFile: CommandLine.arguments[index + 1],
                          atomically: true, encoding: .utf8)
    }
    // The switch is thrown in System Settings, not here, and the process that
    // asked has to still exist when it is — TCC records the grant against the
    // identity that is asking. Stay up long enough for that to happen.
    if !trusted { Thread.sleep(forTimeInterval: 120) }
    exit(trusted ? 0 : 3)
}

// `--screen-capture-preflight`: say what THIS process is allowed to see and
// exit. TCC attributes a request to the RESPONSIBLE process, which for a
// posix_spawn child is its parent — so a grant ticked against this helper's
// own identity may not be the identity being asked about. Running the same
// binary two ways and comparing is the only way to tell those apart.
if CommandLine.arguments.contains("--screen-capture-preflight") {
    // Both services, side by side. Screen Recording is the one that broke, but
    // "does Accessibility answer the same way under the same launch?" is the
    // question that says whether attribution is one rule or two — and it is the
    // question a grant that silently moved would otherwise hide.
    let allowed = CGPreflightScreenCaptureAccess()
    let answer = "{\"screen_capture_preflight\": \(allowed), "
        + "\"accessibility_trusted\": \(AXIsProcessTrusted()), "
        + "\"binary\": \"\(Bundle.main.executablePath ?? "unknown")\", "
        + "\"bundle_id\": \"\(Bundle.main.bundleIdentifier ?? "none")\", "
        + "\"pid\": \(getpid()), \"ppid\": \(getppid())}"
    print(answer)
    // `open` shows no stdout, and the question is precisely what happens when
    // LaunchServices — not a shell — is what started this.
    if let index = CommandLine.arguments.firstIndex(of: "--screen-capture-preflight"),
       index + 1 < CommandLine.arguments.count {
        try? answer.write(toFile: CommandLine.arguments[index + 1],
                          atomically: true, encoding: .utf8)
    }
    // `launchctl procinfo` needs a live pid, and the answer to "which identity
    // is this attributed to" is only observable while the process exists.
    if let index = CommandLine.arguments.firstIndex(of: "--hold"),
       index + 1 < CommandLine.arguments.count,
       let seconds = Double(CommandLine.arguments[index + 1]) {
        Thread.sleep(forTimeInterval: min(600, max(1, seconds)))
    }
    exit(allowed ? 0 : 3)
}

// `--connect <path>`: the launcher is the listening end. It binds the
// rendezvous before it opens us, so there is no moment at which that path
// exists unclaimed for someone else to bind in its place — which is what the
// old direction, where we listened and the launcher waited for our socket to
// appear, left open to a process running as this same user.
//
// LaunchServices may append arguments of its own, so the flag is looked up by
// name rather than read off a fixed position.
let connectPath: String? = {
    let args = CommandLine.arguments
    guard let i = args.firstIndex(of: "--connect"), i + 1 < args.count else { return nil }
    return args[i + 1]
}()

let persistentMode: String? = CommandLine.arguments.contains("--warm-server") ? "warm" : nil
guard connectPath != nil || persistentMode != nil else {
    FileHandle.standardError.write(Data(
        "tacto-helper: --connect 가 필요합니다 (서명된 런처가 실행합니다)\n".utf8))
    exit(3)
}

/// Does the process that launched us satisfy the code requirement?
///
/// This is the check that makes the helper refuse to be anyone's deputy: the
/// grant it holds is usable only through the signed `tacto` launcher, not by any
/// process that can execute this binary. It is a genuine OS check —
/// `SecCodeCheckValidity` against a requirement, not a name comparison —
/// though a PID can in principle be reused, so it is made once at startup and
/// the parent is pinned thereafter.
///
/// The peer identifier is fixed; its required team is derived from this
/// executable's verified Apple-issued signature, never from the environment.
/// A caller cannot replace the trust requirement through configuration.
///
/// `anchor apple generic` alone only means "an Apple-issued identity", not
/// "ours", so the team is checked explicitly.
// Only the launcher. A separate broker binary was removed: anyone could run it
// and feed it well-formed JSON, so authenticating it proved nothing (advisor
// finding). The relay now lives inside the launcher, which hands its child an
// inherited socket nothing else can obtain.
let clientRequirement = TactoSigning.peerRequirement(identifier: "ai.callabo.tacto")

/// The PID of `pid`'s parent, via sysctl.
func parentOf(_ pid: pid_t) -> pid_t? {
    var info = kinfo_proc()
    var size = MemoryLayout<kinfo_proc>.stride
    var mib: [Int32] = [CTL_KERN, KERN_PROC, KERN_PROC_PID, pid]
    guard sysctl(&mib, 4, &info, &size, nil, 0) == 0 else { return nil }
    let ppid = info.kp_eproc.e_ppid
    return ppid > 0 ? ppid : nil
}

/// Walk the ancestors looking for the signed launcher.
///
/// The immediate parent is the Python process the launcher started, so checking
/// only one level would reject our own client. Descending FROM the signed
/// launcher is the property that matters: to be one of its descendants a process
/// has to have been started by it, and the launcher runs only Tacto's own code —
/// it takes no script path, interpreter override or injectable environment. An
/// unrelated process has no signed ancestor and is refused.
func ancestrySatisfiesRequirement(_ start: pid_t, maxDepth: Int = 6) -> (Bool, String) {
    var pid: pid_t? = start
    var depth = 0
    var last = "부모 프로세스를 확인하지 못했습니다"
    while let p = pid, depth < maxDepth, p > 1 {
        let (ok, why) = parentSatisfiesRequirement(p)
        if ok { return (true, "ok (depth \(depth))") }
        last = why
        pid = parentOf(p)
        depth += 1
    }
    return (false, last)
}

func parentSatisfiesRequirement(_ pid: pid_t) -> (Bool, String) {
    var code: SecCode?
    let attrs = [kSecGuestAttributePid: NSNumber(value: Int32(pid))] as CFDictionary
    guard SecCodeCopyGuestWithAttributes(nil, attrs, [], &code) == errSecSuccess,
          let code else { return (false, "부모 프로세스의 코드 정보를 얻지 못했습니다") }
    var requirement: SecRequirement?
    guard SecRequirementCreateWithString(clientRequirement as CFString, [],
                                         &requirement) == errSecSuccess,
          let requirement else { return (false, "requirement 문자열이 잘못되었습니다") }
    let status = SecCodeCheckValidity(code, [], requirement)
    if status == errSecSuccess { return (true, "ok") }
    return (false, "부모가 요구 조건을 만족하지 않습니다 (OSStatus \(status))")
}

/// The signing identifier of whatever launched us, recorded for diagnostics.
func parentSigningTelemetry(_ pid: pid_t) -> String? {
    var code: SecCode?
    let attrs = [kSecGuestAttributePid: NSNumber(value: Int32(pid))] as CFDictionary
    guard SecCodeCopyGuestWithAttributes(nil, attrs, [], &code) == errSecSuccess,
          let code else { return nil }
    var staticCode: SecStaticCode?
    guard SecCodeCopyStaticCode(code, [], &staticCode) == errSecSuccess,
          let staticCode else { return nil }
    var info: CFDictionary?
    guard SecCodeCopySigningInformation(staticCode, SecCSFlags(rawValue: kSecCSSigningInformation),
                                        &info) == errSecSuccess,
          let dict = info as? [String: Any] else { return nil }
    return (dict["identifier"] as? String) ?? "unsigned"
}

let parentPID = getppid()
// Telemetry only: we record what the OS says launched us. We do NOT check
// validity, team or a requirement, so this is not an identity check and is
// never used to authorize anything (advisor finding: do not overclaim).
//
// Under LaunchServices this reads 1, and a watchdog used to stand here that
// exited when it did. It has to go, and not because it was inconvenient: it was
// never the guarantee it looked like. `getppid() == 1` is what you see both
// when your parent has died and when launchd started you on purpose, and a
// process reparented to launchd is not a process whose client has gone away.
// The connection is the thing that actually says whether anyone is still
// asking, so the connection owns the lifetime — see `connectToLauncher` and
// `rendezvousDeadline` below.
let parentSigningID = parentSigningTelemetry(parentPID) ?? "unknown"

if !AXIsProcessTrusted() {
    respond(["type": "error", "error": "accessibility_permission_missing",
             "detail": "System Settings > Privacy & Security > Accessibility "
                       + "(macOS 27: Device Control and Data Access) 에서 "
                       + "'\(permissionHolderName)' 를 켜주세요"])
}

// MARK: - Socket mode (peer-authenticated)
//
// The helper serves exactly one client and authenticates it by its AUDIT TOKEN,
// not by ancestry. That distinction matters: ancestry admits anything descended
// from the signed launcher, so one attacker-controlled link in the chain was
// enough; a peer check names the process actually talking.

/// How long we wait to be claimed. LaunchServices gives our client no way to
/// tell us it gave up, so an unclaimed helper would otherwise sit holding an
/// accessibility grant with nobody on the other end.
let rendezvousDeadline: TimeInterval = 20

/// Connect back to the launcher and prove each side to the other.
///
/// Whoever is listening on that path has to be our signed launcher, in this
/// same login session. The path is unguessable and inside a directory only its
/// creator can enter, but that is secrecy, not authentication, and the process
/// answering there is the one about to be handed an accessibility grant.
@Sendable func connectToLauncher(_ path: String) -> Int32? {
    let fd = socket(AF_UNIX, SOCK_STREAM, 0)
    guard fd >= 0 else { return nil }
    var addr = sockaddr_un()
    addr.sun_family = sa_family_t(AF_UNIX)
    let maxPath = MemoryLayout.size(ofValue: addr.sun_path) - 1
    guard path.utf8.count <= maxPath else { close(fd); return nil }
    let bytes = Array(path.utf8) + [0]
    withUnsafeMutableBytes(of: &addr.sun_path) { dst in
        bytes.withUnsafeBytes { src in
            dst.copyMemory(from: UnsafeRawBufferPointer(
                start: src.baseAddress, count: min(src.count, dst.count)))
        }
    }
    let size = socklen_t(MemoryLayout<sockaddr_un>.size)
    // The launcher binds before it opens us, so one attempt should be enough;
    // retry briefly rather than race the moment it calls listen().
    let giveUp = ProcessInfo.processInfo.systemUptime + rendezvousDeadline
    var connected = false
    repeat {
        connected = withUnsafePointer(to: &addr) { p in
            p.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                connect(fd, $0, size)
            }
        } == 0
        if !connected { usleep(100_000) }
    } while !connected && ProcessInfo.processInfo.systemUptime < giveUp
    guard connected else { close(fd); return nil }

    let (ok, why) = PeerAuth.satisfies(fd: fd, requirement: clientRequirement)
    guard ok else {
        note("tacto-helper: \(why)")
        close(fd); return nil
    }
    // A same-audit-session check stood here and was wrong. The launcher and
    // this helper are in DIFFERENT login sessions whenever the user drives the
    // screen from a remote shell, which is a thing this project is for:
    // measured on 2026-09-21, an ssh-side launcher had asid 100228 while the
    // helper LaunchServices started for it had 100018, the console session. The
    // helper has to be in the console session — that is where the screen is —
    // so equality could never have held. Signing identity is the check; the
    // rendezvous path only has to stay secret, and it is, inside a directory
    // whose name the launcher made and whose mode admits nobody else.
    note("tacto-helper: peer \(PeerAuth.describe(fd: fd)) 인증됨")
    return fd
}

@Sendable func serveLauncher(at path: String) -> Never {
    guard let conn = connectToLauncher(path) else {
        note("tacto-helper: 런처에 연결하지 못했습니다")
        exit(4)
    }
    // Fresh state for the one connection we serve, and the abort is part of
    // that state: it holds for the rest of the run the user stopped, and a NEW
    // launcher run — a new person, a new intention — starts clean. That is the
    // whole scope of it, and it is not a client's to shorten.
    //
    // `abortEpoch` moves with it so that an Esc delivered from a previous run's
    // queue cannot land on this one: events reach the run loop after the fact,
    // and one arriving late would stop something nobody aimed it at.
    latestObsID = ""
    latestElements = []
    latestProcessStart = nil
    latestTargetPID = -1
    latestTargetName = ""
    latestDocumentToken = nil
    // A menu left standing by a previous run is not this one's to press into:
    // the caller that opened it is gone, and nothing here saw the screen it was
    // opened on.
    pendingSelect = nil
    spentMenu = nil
    // Document pins belong to the client that made them. A new authenticated
    // connection is a new client, and inheriting someone else's token would let
    // it read a document it never selected.
    documentTokens.removeAll()
    savePanelTokens.removeAll()
    abortState.begin()
    serve(connection: conn)
    DispatchQueue.main.sync { RuntimePublisher.shared.publish(active:false) }
    // Serving returned, so the client's end is gone. One client, one lifetime:
    // a helper that outlived its client would be a process holding an
    // accessibility grant that nothing is asking anything of, and under
    // LaunchServices there is no parent whose death would have said so.
    close(conn)
    exit(0)
}

/// Write the whole frame: a single write() can be partial, and without the loop
/// a long reply would be silently truncated into invalid JSON.
@Sendable func writeAll(_ fd: Int32, _ data: Data) {
    var sent = 0
    data.withUnsafeBytes { raw in
        while sent < data.count {
            let n = write(fd, raw.baseAddress!.advanced(by: sent), data.count - sent)
            if n <= 0 { if errno == EINTR { continue } else { return } }
            sent += n
        }
    }
}

@Sendable func serve(connection fd: Int32) {
    let input = FileHandle(fileDescriptor: fd, closeOnDealloc: false)
    var buffer = Data()
    while true {
        let chunk = input.availableData
        if chunk.isEmpty { return }
        buffer.append(chunk)
        if buffer.count > 8 * 1024 * 1024 { return }   // frame budget, as launcher
        while let nl = buffer.firstIndex(of: 0x0A) {
            let line = buffer[buffer.startIndex..<nl]
            buffer = buffer[buffer.index(after: nl)...]
            guard let msg = (try? JSONSerialization.jsonObject(with: line))
                    as? [String: Any] else { continue }
            let reply = handle(msg)
            if var out = try? JSONSerialization.data(withJSONObject: reply) {
                out.append(0x0A)
                writeAll(fd, out)
            }
        }
    }
}

// Serial driving lease replaces per-process ownership only in explicit modes.
func resetPersistentSession(starting: Bool) {
    ModeEpoch.shared.rotate()
    if !starting { abortState.stopNow() }
    latestObsID = ""; latestElements = []; latestProcessStart = nil
    latestTargetPID = -1; latestTargetName = ""; latestDocumentToken = nil
    pendingSelect = nil; spentMenu = nil; documentTokens.removeAll(); savePanelTokens.removeAll()
    documentOperationDeadline = nil
    DispatchQueue.main.sync {
        hideControlIndicator()
        controlStream?.stopCapture { _ in }
        controlStream = nil; controlStreamWindow = nil
        controlPanelDiagnostic = [:]; lastIndicatorMode = ""; lastIndicatorRefusal = ""
        if starting {
            RuntimePublisher.shared.id = UUID().uuidString
            RuntimePublisher.shared.target = "Tacto"
            RuntimePublisher.shared.showBanner = TactoRuntimeStatus.showBanner()
            RuntimePublisher.shared.lastPublish = Date.distantPast
        }
    }
    if starting { abortState.begin() }
}

// A panel and a key monitor need an app object and a run loop; the helper is an
// accessory (no Dock icon, never takes focus).
let nsApp = NSApplication.shared
nsApp.setActivationPolicy(.accessory)
watchForAbort()

if let mode = persistentMode {
    guard let binary = Bundle.main.executableURL, let build = TactoMode.buildKey(binary) else { exit(78) }
    let args = CommandLine.arguments
    let position = args.firstIndex(of:"--mode-idle")
    let raw = position.flatMap { $0+1 < args.count ? Double(args[$0+1]) : nil } ?? 120
    let idle = raw.isFinite ? min(600,max(1,raw)) : 120
    ModeLease.shared.setInitialIdle(idle)
    guard startWarmMode(build) else { exit(4) }
    Timer.scheduledTimer(withTimeInterval:0.5,repeats:true) { _ in
        if ModeLease.shared.beginIdleExit() {
            if mode == "warm", let directory = TactoMode.directory(build) { unlink(directory+"/helper.sock") }
            exit(0)
        }
    }
} else {
    DispatchQueue.global().async {
        serveLauncher(at: connectPath!)   // peer-authenticated; never returns
    }
}
nsApp.run()
