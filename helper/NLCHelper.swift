// NLCHelper — the process that holds the OS permissions.
//
// Why this exists (PROTOCOL.md §3.8): macOS TCC identifies a client by its
// binary, and a virtualenv's `python` is normally a symlink to a SHARED
// interpreter. Granting the Python side Accessibility therefore grants it to
// every script run with that interpreter — demonstrated on this machine, where
// an unrelated /tmp script read other apps' windows. A signed, single-purpose
// helper is identified by its code signature instead, so the grant belongs to
// NLC alone, survives updates, and keeps OS privilege out of the model loop
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

/// Say something where it can still be heard.
///
/// LaunchServices hands a launched app no stdout and no stderr, so everything
/// this process said about why it refused a client used to go into a void —
/// the launcher saw only that we had gone. The unified log is the one channel
/// that survives having no parent:
///
///     log stream --predicate 'subsystem == "ai.callabo.nlc"'
///     log show --last 5m --predicate 'subsystem == "ai.callabo.nlc"'
let rendezvousLog = Logger(subsystem: "ai.callabo.nlc", category: "rendezvous")

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
                  "AXIdentifier", "AXPlaceholderValue"]
let labelNames = ["Title", "Description", "Value", "Help",
                  "Identifier", "Placeholder"]

/// The element's label attributes in ONE round trip. Some apps compute these
/// lazily per element — a Mail row costs 20ms read one at a time and 3ms read
/// together — and a mailbox has thousands of rows.
@Sendable func label(_ el: AXUIElement) -> String {
    var raw: CFArray?
    var parts: [String] = []
    if AXUIElementCopyMultipleAttributeValues(
        el, labelAttrs as CFArray, AXCopyMultipleAttributeOptions(rawValue: 0), &raw)
        == .success, let values = raw as? [Any], values.count == labelAttrs.count {
        for (i, v) in values.enumerated() {
            // An attribute the element lacks comes back as an AXValue holding
            // an error, not as nil, so only real strings and numbers count.
            if let s = v as? String, !s.isEmpty { parts.append("\(labelNames[i]): \(s)") }
            else if let n = v as? NSNumber, CFGetTypeID(n) != CFBooleanGetTypeID() {
                parts.append("\(labelNames[i]): \(n.stringValue)")
            }
        }
        return parts.joined(separator: ", ")
    }
    for (i, key) in labelAttrs.enumerated() {
        if let v = str(el, key) { parts.append("\(labelNames[i]): \(v)") }
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

/// One line per element, in the exact format the Python `Selector` parses:
/// `<indent><index> <Role>  Key: value, Key2: value [disabled]`
func walk(_ el: AXUIElement, depth: Int, out: inout [String],
          elements: inout [AXUIElement], visited: inout Int,
          truncated: inout Bool) {
    if depth > 25 { return }
    if visited >= maxTreeNodes { truncated = true; return }
    visited += 1
    let role = str(el, kAXRoleAttribute as String) ?? "?"
    let text = label(el)
    if interactableRoles.contains(role) || !text.isEmpty {
        let idx = elements.count
        elements.append(el)
        let enabled = (attr(el, kAXEnabledAttribute as String) as? Bool) ?? true
        let short = role.hasPrefix("AX") ? String(role.dropFirst(2)) : role
        let extra = secondaryActionsOf(el)
        let actions = extra.isEmpty ? ""
            : ", Secondary Actions: " + extra.map { String($0.dropFirst(2)) }
                                             .joined(separator: ", ")
        out.append(String(repeating: "  ", count: depth) + "\(idx) \(short)"
                   + (text.isEmpty ? "" : "  \(text)") + (enabled ? "" : " [disabled]")
                   + actions)
    }
    let children = (attr(el, kAXChildrenAttribute as String) as? [AXUIElement]) ?? []
    for child in children.prefix(maxChildrenPerNode) {
        walk(child, depth: depth + 1, out: &out, elements: &elements,
             visited: &visited, truncated: &truncated)
    }
    if children.count > maxChildrenPerNode {
        truncated = true
        out.append(String(repeating: "  ", count: depth + 1)
                   + "… (\(children.count - maxChildrenPerNode)개 더 있음)")
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
var latestElements: [AXUIElement] = []
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

    init(pid: pid_t, index: Int, control: AXUIElement, identifier: String?,
         role: String, label: String, menu: AXUIElement) {
        self.pid = pid
        self.index = index; self.control = control; self.identifier = identifier
        self.role = role; self.label = label; self.menu = menu
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
func menuOptions(_ menu: AXUIElement) -> [[String: Any]]? {
    var out: [[String: Any]] = []
    for item in (attr(menu, kAXChildrenAttribute as String) as? [AXUIElement]) ?? [] {
        if out.count >= maxMenuOptions { return nil }
        guard let title = str(item, kAXTitleAttribute as String), !title.isEmpty else {
            continue                          // separators
        }
        out.append(["label": title,
                    "disabled": (attr(item, kAXEnabledAttribute as String) as? Bool) == false])
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
    appMenus(p).contains { CFEqual($0, pending.menu) }
}

/// A menu this helper opened, already spent on a selection, that did not close
/// itself. Nothing acts on it any more, but it is still on the person's screen
/// — and an observation calling that control "closed" would tell the next step
/// a menu is out of the way when it is not.
var spentMenu: (pid: pid_t, control: AXUIElement, menu: AXUIElement)?

/// Is a menu this helper opened still on screen? One slot, one menu.
///
/// Asked BEFORE anything is dispatched, and it mutates nothing. Two ways the
/// slot is taken: a pin nobody has spent, or a menu already spent that did not
/// close itself. Either way a second AXShowMenu would put a menu on top of a
/// menu, and this helper will not dismiss the first to make room — so it
/// refuses without sending anything.
///
/// A pin is NOT liveness-checked here. The observation path drops a pin whose
/// menu has gone, and checking it here would let a look at one app throw away
/// what is standing in another (peer review).
func menuSlotBusy() -> String? {
    if pendingSelect != nil {
        return "이미 열어 둔 선택지가 있습니다 — 먼저 고르거나 화면을 다시 관측하세요"
    }
    guard let spent = spentMenu else { return nil }
    if !appMenus(spent.pid).contains(where: { CFEqual($0, spent.menu) }) {
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
func customSelects(_ p: pid_t, _ elements: [AXUIElement], obsID: String) -> [String: Any] {
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
              !appMenus(p).contains(where: { CFEqual($0, held.menu) }) {
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
    for (i, el) in elements.enumerated() {
        let role = str(el, kAXRoleAttribute as String) ?? ""
        guard ["AXPopUpButton", "AXComboBox"].contains(role) else { continue }
        guard let pending, CFEqual(el, pending.control) else {
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
        pending.index = i
        pending.options = options
        pending.optionsObsID = obsID
        out[String(i)] = ["state": "open", "options": options]
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
@Sendable func scrollBar(near element: AXUIElement, vertical: Bool) -> AXUIElement? {
    var node: AXUIElement? = element
    var seen = 0
    while let here = node, seen < 12 {
        seen += 1
        if (attr(here, kAXRoleAttribute as String) as? String) == "AXScrollArea" {
            let kids = (attr(here, kAXChildrenAttribute as String) as? [AXUIElement]) ?? []
            for child in kids.prefix(40) {
                guard (attr(child, kAXRoleAttribute as String) as? String) == "AXScrollBar" else { continue }
                let want = vertical ? "AXVerticalOrientation" : "AXHorizontalOrientation"
                if (attr(child, kAXOrientationAttribute as String) as? String) == want {
                    return child
                }
            }
            return nil
        }
        node = attr(here, kAXParentAttribute as String) as! AXUIElement?
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
    var byBundle: [NSRunningApplication] = []
    var byName: [NSRunningApplication] = []
    for app in NSWorkspace.shared.runningApplications {
        if app.bundleIdentifier?.lowercased() == want { byBundle.append(app) }
        else if app.localizedName?.lowercased() == want { byName.append(app) }
    }
    // A name never means a background agent; a bundle id is explicit and may.
    byName = byName.filter { $0.activationPolicy == .regular }
    for group in [byBundle, byName] where !group.isEmpty {
        let regular = group.filter { $0.activationPolicy == .regular }
        return (regular.first ?? group.first)?.processIdentifier
    }
    return nil
}

func observe(_ target: String, requireWindow: Bool = true, documentToken: String? = nil) -> [String: Any] {
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
    // _NS:8` — this helper's own way of saying "NLC is working here" walked
    // into the tree it was working on, and shifted every element index behind
    // it.
    let windows: [AXUIElement]
    do {
        windows = try documentToken.map { [try documentWindowForToken(target, $0)] } ?? realWindows(of: app)
    } catch {
        return ["type":"error", "error":"document_snapshot_unavailable", "effect":"not_started"]
    }
    if windows.isEmpty && requireWindow {
        return ["type": "error", "error": "no_window_content", "target": target]
    }
    // Reading another app's contents is not neutral: an accessibility tree can
    // carry messages, names and account numbers. Show the outline for reads too,
    // so nothing is taken off the screen invisibly (advisor finding: observe
    // was silent, leaving confidentiality unprotected while integrity was not).
    if abortState.isStopped {
        return ["type": "error", "error": "aborted_by_user"]
    }
    guard ensureVisibleControl(forPID: p, windowless: true) else {
        return ["type": "error", "error": "indicator_unavailable",
                "detail": "조작 표시를 켤 수 없어 중단했습니다 — 시스템 설정 > 개인정보 보호 및 보안 > 화면 기록 에서 'NLC Helper' 를 켜주세요. "
                          + "조작 중임을 화면에 표시하는 수단이라 이것 없이는 진행하지 않습니다."]
    }
    var lines: [String] = []
    var elements: [AXUIElement] = []
    var visited = 0
    var truncated = false
    for w in windows {
        let start = lines.count
        walk(w, depth: 0, out: &lines, elements: &elements,
             visited: &visited, truncated: &truncated)
        // Mark the frontmost window, exactly as the Python driver does. The two
        // must produce the same tree for the same app, or a recipe that scopes
        // to the front window silently matches nothing under one of them.
        if lines.count > start,
           (attr(w, "AXMain") as? Bool) == true || (attr(w, "AXFocused") as? Bool) == true {
            lines[start] += " [focused]"
        }
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
            // Reading only; nothing here opens a menu. A closed popup reports
            // no options because it HAS none until it is opened.
            "custom_selects": customSelects(p, elements, obsID: obsID)]
    if let documentToken { reply["document_token"] = documentToken }
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
                     control: AXUIElement? = nil) -> String? {
    var sameID: [AXUIElement] = []
    var alive: AXUIElement? = nil
    var truncated = false
    var visited = 0
    func read(_ node: AXUIElement, _ key: String, optional: Bool = false) -> CFTypeRef? {
        var value: CFTypeRef?
        let error = AXUIElementCopyAttributeValue(node, key as CFString, &value)
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
    if let original = alive { return attr(original, kAXValueAttribute as String) as? String }
    guard identity?.isEmpty == false, !truncated, sameID.count == 1 else { return nil }
    return attr(sameID[0], kAXValueAttribute as String) as? String
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
func modalSheet(_ p: pid_t) -> Bool {
    let app = AXUIElementCreateApplication(p)
    for w in (attr(app, kAXWindowsAttribute as String) as? [AXUIElement]) ?? [] {
        if let sheets = attr(w, "AXSheets") as? [AXUIElement], !sheets.isEmpty { return true }
        if (attr(w, "AXModal") as? Bool) == true { return true }
    }
    return false
}

/// Why this is no longer what the caller meant — or nil if it still is.
func mismatch(_ expect: [String: Any], element: AXUIElement?, pid p: pid_t) -> String? {
    if (expect["no_modal"] as? Bool) == true, modalSheet(p) {
        return "화면에 대화상자가 열렸습니다"
    }
    if let wantSel = expect["selection"] as? String, wantSel != selectionDigest(p) {
        return "고른 것이 바뀌었습니다 — 같은 버튼이라도 대상이 다릅니다"
    }
    guard let el = element else {
        // No element to compare against. An expectation that names one is not
        // weakly satisfied here, it is unanswerable — and an ignored
        // precondition looks exactly like a met one, which is the failure this
        // contract was built to stop. Refused, not skipped.
        let named = ["role", "label", "attribute", "enabled"]
            .filter { expect[$0] != nil }
        if !named.isEmpty {
            return "이 동작은 대상 요소를 지정하지 않는데 "
                 + "\(named.joined(separator: ", ")) 을(를) 기대했습니다 — "
                 + "확인할 수 없는 조건입니다"
        }
        return nil
    }
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
                        "Placeholder": kAXPlaceholderValueAttribute as String]
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
func realWindows(of app: AXUIElement) -> [AXUIElement] {
    let all = (attr(app, kAXWindowsAttribute as String) as? [AXUIElement]) ?? []
    let byRole = all.filter { str($0, kAXRoleAttribute as String) == kAXWindowRole as String }

    func frame(_ element: AXUIElement) -> CGRect? {
        guard let positionValue = attr(element, kAXPositionAttribute as String),
              let sizeValue = attr(element, kAXSizeAttribute as String) else { return nil }
        var origin = CGPoint.zero, size = CGSize.zero
        AXValueGetValue(positionValue as! AXValue, .cgPoint, &origin)
        AXValueGetValue(sizeValue as! AXValue, .cgSize, &size)
        return CGRect(origin: origin, size: size)
    }
    let frames = byRole.map { frame($0) }

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

func prepareBackgroundWindow(_ obsID: String, _ target: String, _ element: AXUIElement,
                             _ pid: pid_t, deadline: Double, key: String? = nil,
                             expectation: [String: Any]? = nil, point: CGPoint? = nil,
                             windowInput: NativeOpaqueWindowInput? = nil) -> [String: Any] {
    let elements = latestElements
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
              latestElements.count == elements.count,
              zip(latestElements, elements).allSatisfy({ CFEqual($0, $1) }) else {
            throw NativeWindowInputError.refused("window observation or process changed")
        }
    }
    do {
        let scope = NativeWindowReadScope(deadline: deadline, check: context)
        try scope.ready()
        let binding = try captureNativeWindowBinding(element, pid, expectedStart: start,
            pointKind: windowInput != nil ? .windowInput : point != nil ? .windowPoint : key == nil ? .titlebar : .elementCenter,
            scope: scope, relativePoint: point)
        let routing: ((NativeWindowBinding, Bool) throws -> Void)?
        if windowInput != nil || point != nil { routing = { binding, _ in try requireNativeWindowTarget(binding, scope: scope) } }
        else if key != nil { routing = { try requireNativeKeyTarget($0, element: $1, scope: scope) } }
        else { routing = nil }
        let result = try dispatchNativeWindowPreparation(binding, deadline: deadline,
            stopped: { abortState.isStopped }, context: context,
            validate: { try validateNativeWindowBinding($0, scope: scope) }, key: key,
            routing: routing,
            preflight: key == nil && point == nil && windowInput == nil ? nil : {
                try scope.ready()
                if let expectation, let why = mismatch(expectation, element: element, pid: pid) {
                    throw NativeWindowInputError.refused(why)
                }
                try scope.ready()
            }, pointClick: point != nil, windowInput: windowInput)
        return ["type":"acted", "action":windowInput != nil ? "window_input" : point != nil ? "click_point" : key == nil ? "prepare_window" : "press_key", "obs_id":obsID,
                "input_route":windowInput != nil ? "window_input" : point != nil ? "window_bound_mouse" : key == nil ? "window_prepare" : "window_key", "mechanism":"pid_window_events",
                "posted_events":result.posted, "window_id":result.windowID,
                "reobserve_required":true, "unverified":"observe_effect"]
    } catch let failure as NativeWindowDispatchFailure {
        return ["type":"error", "error":failure.stopped ? "aborted_by_user" : "action_failed",
                "effect":failure.attempted > 0 ? "unknown" : "not_started",
                "posted_or_attempted_events":failure.attempted, "detail":failure.detail]
    } catch {
        let detail: String
        switch error {
        case NativeWindowInputError.refused(let value), NativeWindowInputError.unavailable(let value),
             NativeWindowInputError.needsForeground(let value): detail = value
        default: detail = "window preparation observation failed"
        }
        return ["type":"error", "error":abortState.isStopped ? "aborted_by_user" : "action_failed",
                "effect":"not_started", "detail":detail]
    }
}

func prepareWindow(_ obsID: String, _ target: String, _ element: AXUIElement,
                   _ pid: pid_t, deadline: Double) -> [String: Any] {
    var attempted = false
    func refuse(_ error: String, _ detail: String, foreground: Bool = false) -> [String: Any] {
        var out: [String: Any] = ["type": "error", "error": error,
                                  "effect": attempted ? "unknown" : "not_started", "detail": detail]
        if foreground { out["needs_foreground"] = true }
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
    var wrote: [String] = []
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
    guard !abortState.isStopped, ProcessInfo.processInfo.systemUptime < deadline,
          isFrontmost(pid: pid) else {
        return refuse("action_failed", "window preparation context changed")
    }
    let became = (attr(element, "AXMain") as? Bool) == true
              || (attr(element, "AXFocused") as? Bool) == true
    var out: [String: Any] = ["type": "acted", "action": "prepare_window", "obs_id": obsID,
                              "input_route": "window_prepare", "wrote": wrote,
                              "window_reports_key": became]
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
              latestElements.count == elements.count,
              zip(latestElements, elements).allSatisfy({ CFEqual($0, $1) }) else {
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

func act(_ obsID: String, _ target: String, _ action: [String: Any],
         expect: [String: Any]? = nil, clickDeadline: Double? = nil, coordinateAuthorized: Bool = false,
         savePanelToken: String? = nil, windowInputAuthorized: Bool = false) -> [String: Any] {
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
    if action["name"] as? String == "click_point", !coordinateAuthorized {
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
    if latestDocumentToken != nil {
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
    let elements = latestElements
    if abortState.isStopped {
        return ["type": "error", "error": "aborted_by_user",
                "effect": "not_started",
                "detail": "사용자가 Esc로 중단했습니다"]
    }
    guard ensureVisibleControl(forPID: p, windowless: (action["name"] as? String) == "menu") else {
        return ["type": "error", "error": "indicator_unavailable",
                "effect": "not_started",
                "detail": "조작 표시를 켤 수 없어 중단했습니다 — 시스템 설정 > 개인정보 보호 및 보안 > 화면 기록 에서 'NLC Helper' 를 켜주세요. "
                          + "조작 중임을 화면에 표시하는 수단이라 이것 없이는 진행하지 않습니다."]
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
        var advertised: CFArray?
        let listed = AXUIElementCopyActionNames(el, &advertised)
        if listed == .success, let names = advertised as? [String],
           !names.contains(kAXPressAction as String) {
            return ["type": "error", "error": "action_failed",
                      "effect": "not_started", "needs_pointer": true,
                      "detail": "이 요소는 누를 수 있다고 알리지 않습니다 — 이 도우미는 "
                                + "좌표 클릭을 합성하지 않습니다. 사람이 직접 눌러야 합니다"]
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
            return ["type": "error", "error": "action_failed",
                      "effect": "not_started", "needs_pointer": true,
                      "detail": "이 요소는 누를 수 있다고 알리지 않습니다 — 이 도우미는 "
                                + "좌표 클릭을 합성하지 않습니다. 사람이 직접 눌러야 합니다"]
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
            out["note"] = "not verified — observe again; the app brought itself to the front"
        }
        return out
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
        let before = appMenus(p)
        var opened = AXUIElementPerformAction(el, "AXShowMenu" as CFString)
        if opened == AXError(rawValue: -25206) {
            // actionUnsupported: provably nothing happened — this element has
            // no AXShowMenu — so trying the other way is not a second attempt
            // at the same thing.
            opened = AXUIElementPerformAction(el, kAXPressAction as CFString)
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
        spentMenu = (pid: p, control: el, menu: menu)

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
        let role = str(el, kAXRoleAttribute as String) ?? ""
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
        // Same as above: reported, never arranged.
        let wasFront = isFrontmost(pid: p)
        let before = appMenus(p)
        var showed = AXUIElementPerformAction(el, "AXShowMenu" as CFString)
        if showed == AXError(rawValue: -25206) {
            // actionUnsupported: provably nothing happened — this element has
            // no AXShowMenu — so trying the other way is not a second attempt
            // at the same thing.
            showed = AXUIElementPerformAction(el, kAXPressAction as CFString)
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
        let fresh = appMenus(p).filter { candidate in
            !before.contains(where: { CFEqual($0, candidate) })
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
            identifier: str(el, "AXIdentifier"), role: role,
            label: label(el), menu: fresh[0])
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
        if str(el, "AXIdentifier") != pending.identifier {
            return drop("컨트롤의 식별자가 바뀌었습니다")
        }
        if label(el) != pending.label {
            return drop("컨트롤의 이름이 바뀌었습니다")
        }
        if (attr(el, kAXEnabledAttribute as String) as? Bool) == false {
            return keep("이 컨트롤은 지금 쓸 수 없습니다")
        }
        if p != pending.pid {
            return drop("선택지를 연 앱과 고르려는 앱이 다릅니다")
        }
        if !menuIsOurs(pending.pid, pending) {
            return drop("열어 둔 선택지 목록이 사라졌거나 다른 목록으로 바뀌었습니다")
        }
        // Not "the item we want is still there" — the whole list. An item
        // appearing above the one we mean moves nothing we can see, and is
        // exactly how a choice lands on the wrong row.
        guard let now = menuOptions(pending.menu), sameOptions(now, shown) else {
            return keep("관측한 뒤 선택지 목록이 달라졌습니다")
        }
        let items = ((attr(pending.menu, kAXChildrenAttribute as String) as? [AXUIElement]) ?? [])
            .filter { str($0, kAXTitleAttribute as String) == want }
        if items.isEmpty {
            let here = shown.prefix(12).compactMap { $0["label"] as? String }.joined(separator: ", ")
            return keep("'\(want)' 라는 선택지가 없습니다 — 있는 것: "
                        + (here.isEmpty ? "(없음)" : here))
        }
        if items.count > 1 {
            return keep("'\(want)' 라는 선택지가 \(items.count)개라 어느 것인지 알 수 없습니다")
        }
        if (attr(items[0], kAXEnabledAttribute as String) as? Bool) == false {
            return keep("'\(want)' 는 지금 고를 수 없습니다")
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
        let menu = pending.menu, chosenRole = pending.role, identity = pending.identifier
        forgetPin()                           // spent, whatever happens next
        // Recorded BEFORE the press. If the press fails we may not be able to
        // say whether the menu is still up, and a menu this helper opened must
        // never be described to the next observation as closed. The next
        // observation of this app drops it once it is really gone.
        spentMenu = (pid: p, control: el, menu: menu)
        let pressed = AXUIElementPerformAction(items[0], kAXPressAction as CFString)
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
            facts["menu_left_open"] = appMenus(p).contains { CFEqual($0, menu) }
            return facts
        }
        usleep(300_000)
        let stillOpen = appMenus(p).contains { CFEqual($0, menu) }
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
        // Return commits — it sends a message, submits a form, saves. So it
        // is taken only into the Go to Folder sheet's path field (see
        // returnTargetRefusal), where it navigates. The flow must also declare
        // the step a submit; see replay's press_key contract.
        let submit = nativeSubmitKey(key) != nil
        if submit, let why = returnTargetRefusal(el, read: boundedAXRead(until: deadline)) {
            return ["type":"error", "error":"action_failed", "effect":"not_started",
                    "detail":"Return refused: \(why)"]
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
        let pages = min(max((action["pages"] as? Int) ?? 1, 1), 10)
        let dir = (action["direction"] as? String) ?? "down"
        guard ["up", "down", "left", "right"].contains(dir) else {
            return ["type": "error", "error": "action_failed",
                    "effect": "not_started", "detail": "scroll 방향이 아닙니다: \(dir)"]
        }
        let wantVertical = dir == "up" || dir == "down"
        guard let bar = scrollBar(near: el, vertical: wantVertical) else {
            return ["type": "error", "error": "action_failed",
                    "effect": "not_started",
                    "detail": "\(wantVertical ? "세로" : "가로") 스크롤 막대를 "
                              + "찾지 못했습니다 — 이 요소는 그 방향으로 스크롤되지 않습니다"]
        }
        var barSettable = DarwinBoolean(false)
        guard AXUIElementIsAttributeSettable(bar, kAXValueAttribute as CFString,
                                             &barSettable) == .success,
              barSettable.boolValue,
              let current = attr(bar, kAXValueAttribute as String) as? Double else {
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
            return ["type": "error", "error": "action_failed",
                    "effect": "not_started",
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
// Python interpreter: any script using that interpreter looks identical to NLC,
// which is the very problem the helper exists to fix. What DOES hold regardless
// of who is calling is that the person can see what is happening and stop it.
// So the indicator and the abort key belong HERE, in the process that performs
// the actions — a caller cannot skip them by not showing the indicator.

// The stop itself lives in `abortState`, which holds it together with the
// moment this run began — the two only mean anything as a pair, and a second
// locked copy of "is this stopped" would be a second answer to the one
// question that must have one.
/// The menu-bar item that says this is happening. Nil when nothing is running.
var controlItem: NSStatusItem?

/// Show that NLC is working, and say on which app.
///
/// This was an outline drawn around the target window, and it was wrong in two
/// ways. It covered part of someone's screen with our own drawing, and — the
/// report that started this — it carried `canJoinAllSpaces`, so switching
/// desktops brought the box along and drew it over unrelated work, around a
/// window that was not on that screen at all.
///
/// A menu-bar item is the same promise without either problem: it hides
/// nothing, it does not follow anyone anywhere, and it costs no permission.
/// The other half of the promise is macOS's own: when this helper actually
/// captures the screen, the system lights its recording indicator, which we do
/// not draw, cannot fake and cannot suppress. Two signals whose scopes differ
/// exactly as the access does — the menu bar for "NLC is acting", the system
/// indicator for "NLC is looking".
///
/// Returns false when the item could not be created. Callers treat that as a
/// refusal: acting without the person being able to see it is the situation
/// this whole mechanism exists to prevent, so a failure here must stop the
/// action rather than silently proceed (advisor finding).
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
        note("nlc-helper: 표시 메뉴에서 중단했습니다")
        DispatchQueue.main.async {
            controlItem?.button?.title = "● NLC 중단됨"
        }
    }
}

@discardableResult
func showControlIndicator(forPID pid: pid_t) -> Bool {
    let who = NSRunningApplication(processIdentifier: pid)?.localizedName
    // Short on purpose: menu bar space is the person's, and "● NLC · Calculator
    // · Esc" crowded it (reported). Which app, and how to stop, are one click
    // away in the menu and on hover in the tooltip.
    let title = "● NLC"
    let tip = (who.map { "NLC가 \($0) 을(를) 조작하고 있습니다" } ?? "NLC가 이 화면을 조작하고 있습니다")
        + ". Esc로 즉시 멈춥니다."
    if let existing = controlItem {
        existing.button?.title = title
        existing.button?.toolTip = tip      // the target can change mid-run
        existing.menu = controlMenu(for: who)
        return true
    }
    let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    guard let button = item.button else {
        NSStatusBar.system.removeStatusItem(item)
        return false
    }
    button.title = title
    button.toolTip = tip
    // A menu, because an indicator you cannot act on is only half of what it
    // promises. Esc stops the run, and someone who does not know that was
    // being told something was happening and given nothing to do about it.
    item.menu = controlMenu(for: who)
    controlItem = item
    return true
}

/// The indicator's menu: what is being driven, and a way to stop it.
func controlMenu(for appName: String?) -> NSMenu {
    let menu = NSMenu()
    let what = appName.map { "NLC이 \($0) 을(를) 조작하고 있습니다" }
        ?? "NLC이 이 화면을 조작하고 있습니다"
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

/// The capture stream whose only product is macOS's own recording indicator.
///
/// The menu-bar item above is not enough on its own, and this is measured
/// rather than assumed: on a full menu bar macOS collapses extra items behind
/// an overflow chevron, and the item reports `isVisible == true` the whole
/// time. A promise that the person can see this happening cannot rest on a
/// signal that hides itself and then says it did not.
///
/// The system's recording indicator does not do that. Photographed while a
/// stream was held, it took a slot at the front of the cluster and pushed the
/// overflow chevron out rather than going behind it. It is also the one signal
/// here that is not ours: we do not draw it, cannot fake it and cannot
/// suppress it, which is exactly what makes it worth more than anything we
/// could draw.
///
/// It costs what it says it costs. Lighting it means really capturing, so this
/// asks for the smallest, slowest stream that keeps it lit and throws every
/// frame away — and it means a run needs Screen Recording even when it only
/// ever touches accessibility APIs. That is the honest trade: the indicator is
/// truthful precisely because the access behind it is real.
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
    guard let windowID = controlWindowID(forPID: pid) else {
        note("nlc-helper: 조작 표시를 켜지 못했습니다 — 대상 창을 찾지 못했습니다")
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
        guard let window = content?.windows.first(where: { $0.windowID == windowID }) else {
            why = "window not shareable: \(error?.localizedDescription ?? "none")"
            ready.signal(); return
        }
        // ONE WINDOW. `SCContentFilter(display:)` stood here and it was wrong:
        // it captures the whole screen, so lighting an indicator that says
        // "NLC is looking" meant looking at everything — including every other
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
                controlStream = stream
                controlStreamWindow = windowID
                started = true
            }
            ready.signal()
        }
    }
    if ready.wait(timeout: .now() + 8) != .success {
        note("nlc-helper: 조작 표시를 켜지 못했습니다 (시간 초과)")
        return false
    }
    // Both outcomes, not just the bad one. "Did the indicator actually come
    // on during that run?" was not answerable from the log, and the photograph
    // that was supposed to answer it raced the run and missed.
    note(started ? "nlc-helper: 조작 표시 켜짐 — 창 \(windowID) 하나만 캡처합니다"
                 : "nlc-helper: 조작 표시를 켜지 못했습니다 — \(why)")
    return started
}

/// Both signals, and only one of them decides.
///
/// The menu-bar item is best effort: when the bar has room it names the app
/// being driven, which the system indicator cannot do. When it does not, it
/// hides without telling anyone — so what the refusal hangs on is the stream,
/// the signal that cannot hide.
func ensureVisibleControl(forPID pid: pid_t, windowless: Bool = false) -> Bool {
    let shown = DispatchQueue.main.sync { showControlIndicator(forPID: pid) }
    // An app with no window of its own (Finder showing only the desktop, an
    // app whose documents are all closed) has nothing to look at, so there is
    // no window to stream and nothing for the recording indicator to report.
    // Refusing here blocked the very menu command that would open a window,
    // and blamed a Screen Recording permission that was granted (2026-10-07).
    //
    // Only a SUCCESSFUL read that says "no windows" counts: `realWindows`
    // turns a failed read into [], and a transient AX failure on an app with
    // windows must not skip the stream (Codex review of 2b6b400). And the
    // menu-bar item is the gate here only while the system says it is on
    // screen. That does not rule out the overflow chevron (see
    // controlItemOnScreen) — on this path the promise that acting is visible
    // is weaker than with a stream, and is limited to menu requests on an app
    // with no window. Otherwise: the stream path, which refuses when it
    // cannot stream.
    // `windowless` is said by the caller, and only for what can be done to an
    // app with no window: read its menus, run a menu command. Everything else
    // keeps the stream as its gate even when the app shows no window.
    if windowless && appSaysItHasNoWindows(pid) {
        if let running = controlStream {
            running.stopCapture { _ in }
            controlStream = nil
            controlStreamWindow = nil
        }
        return shown && DispatchQueue.main.sync { controlItemOnScreen() }
    }
    return ensureControlStream(forPID: pid)
}

/// True only when AXWindows was read successfully and is an empty array.
/// A failed read, a wrong type, or any window at all is not "no windows".
func appSaysItHasNoWindows(_ pid: pid_t) -> Bool {
    var value: CFTypeRef?
    let err = AXUIElementCopyAttributeValue(AXUIElementCreateApplication(pid),
                                            kAXWindowsAttribute as CFString, &value)
    guard err == .success, let windows = value as? [AXUIElement] else { return false }
    return windows.isEmpty
}

/// The status item's button is in a window the system reports as visible
/// and on a screen. A necessary condition, not proof the person can see it:
/// behind the menu-bar overflow chevron, `isVisible` was measured to stay
/// true, and whether `occlusionState` drops `.visible` there has not been
/// measured. It refuses the cases it can see (no window, ordered out,
/// off-screen); the overflow case is an open gap, not one this closes.
func controlItemOnScreen() -> Bool {
    guard let window = controlItem?.button?.window else { return false }
    return window.isVisible && window.occlusionState.contains(.visible)
        && NSScreen.screens.contains { $0.frame.intersects(window.frame) }
}

func hideControlIndicator() {
    if let item = controlItem {
        NSStatusBar.system.removeStatusItem(item)
    }
    controlItem = nil
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
/// reaches further than a click. `ai.callabo.nlc.helper` is one identity the
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
        return refuse("indicator_unavailable", "조작 표시를 켤 수 없어 중단했습니다 — 시스템 설정 > 개인정보 보호 및 보안 > 화면 기록 에서 'NLC Helper' 를 켜주세요")
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
                    "grant_holder": "NLC Helper",
                    "helper_preflight": preflight,
                    "helper_binary": Bundle.main.executablePath ?? "unknown",
                    "helper_bundle_id": Bundle.main.bundleIdentifier ?? "none",
                    "fix": "시스템 설정 > 개인정보 보호 및 보안 > 화면 기록 에서 "
                         + "'NLC Helper' 를 켜주세요"]
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

    let directory = NSTemporaryDirectory() + "nlc-capture-\(getpid())"
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
let helperCapabilities: [String] = [
    "observe", "act", "probe", "selection", "menu_state", "menu_items",
    "expect.role", "expect.label", "expect.attribute", "expect.enabled",
    "expect.no_modal", "expect.selection",
    "effect",                       // errors say whether an effect is possible
    "action.click.deadline_uptime", "action.prepare_window", "action.prepare_window.background", "action.click", "action.set_value", "action.type_text", "action.read",
    "action.menu", "action.scroll", "action.secondary_action",
    "action.select_option", "evidence.visual",
    // Native menu items have a title and nothing behind it, so `value` must
    // agree with `option_label` rather than name something else.
    "select_option.label_only",
    // The two-step form, for a caller that does not already know what the
    // popup contains: open it, look at the observation that follows, then
    // choose from THAT. Same words the browser backend uses.
    "action.open_select", "action.select_custom_option",
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
    "action.window_input", "window_input.window_bound", "window_input.host_authorization",
    // Not "press_key.navigation_only": this helper also takes Return, narrowly.
    "action.press_key", "press_key.no_escape",
    "press_key.element_bound",
    // Return, only to the Go to Folder sheet's path field.
    "press_key.return_in_go_to_folder", "action.press_key.deadline_uptime",
    // Reading only, and deliberately NOT document_snapshot: this witnesses what
    // a document contains and where it lives, never its source format or where
    // a save panel would put it.
    "evidence.document_text", "evidence.document_text.path_selector", "observe.document_token", "evidence.save_panel",
    "action.click.save_panel_guard", "evidence.document_rtf_export",
]


// MARK: - Read-only document text
//
// The same contract nlc/native_document.py states, spoken over the signed
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
                                    selectedPath: selectedPath)
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
    func refuse(_ detail: String) -> [String: Any] {
        ["type":"error", "error":abortState.isStopped ? "aborted_by_user" : "save_panel_unavailable",
         "effect":"not_started", "detail":detail]
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
                "authorizes_save":false]
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
                                 "selection", "document_text", "save_panel", "document_rtf_export"]
    if let kind, reaching.contains(kind) || kind == "capture",
       let target = msg["target"] as? String, let p = pid(forBundle: target) {
        guard ensureVisibleControl(forPID: p, windowless: kind == "menu_state" || kind == "menu_items") else {
            return ["type": "error", "error": "indicator_unavailable",
                    "effect": "not_started",
                    "detail": "조작 표시를 켤 수 없어 \(kind) 를 중단했습니다 — 시스템 설정 > "
                              + "개인정보 보호 및 보안 > 화면 기록 에서 'NLC Helper' 를 켜주세요"]
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
        // Python asking about itself answered for the wrong app, and `nlc
        // doctor` told people to enable "this app" without saying which.
        return ["type": "pong", "trusted": AXIsProcessTrusted(),
                "screen_capture": CGPreflightScreenCaptureAccess(),
                "capabilities": helperCapabilities]
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
        if let raw = msg["document_token"], !(raw is String) {
            return ["type":"error", "error":"document_snapshot_unavailable", "effect":"not_started"]
        }
        return observe(msg["target"] as? String ?? "",
                       requireWindow: msg["require_window"] as? Bool ?? true,
                       documentToken: msg["document_token"] as? String)
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
        let coordinate = (msg["action"] as? [String:Any])?["name"] as? String == "click_point"
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
            guard let action = msg["action"] as? [String: Any], ["click", "click_point", "prepare_window", "press_key", "window_input"].contains(action["name"] as? String ?? ""),
                  let number = raw as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(),
                  number.doubleValue.isFinite, number.doubleValue > 0 else {
                return ["type": "error", "error": "invalid_click_deadline", "effect": "not_started"]
            }
            clickDeadline = number.doubleValue
        }
        return act(msg["obs_id"] as? String ?? "", msg["target"] as? String ?? "",
                   msg["action"] as? [String: Any] ?? [:],
                   expect: msg["expect"] as? [String: Any], clickDeadline: clickDeadline,
                   coordinateAuthorized: coordinateAuthorized, savePanelToken: savePanelToken, windowInputAuthorized: windowInputAuthorized)
    case "probe":
        return probe(msg["obs_id"] as? String ?? "", msg["target"] as? String ?? "",
                     msg["element_index"] as? Int ?? -1)
    case "selection":
        return selectionOf(msg["target"] as? String ?? "")
    case "save_panel":
        return readSavePanel(msg)
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
        let menu = controlItem?.menu
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
        note("nlc-helper: indicator menu probe \(report)")
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
        note("nlc-helper: hold-indicator target=\(holdTarget) -> \(ok)")
        // Give the indicator a moment to appear, then photograph.
        if let shot = CommandLine.arguments.firstIndex(of: "--probe-shot"),
           shot + 1 < CommandLine.arguments.count {
            let path = CommandLine.arguments[shot + 1]
            DispatchQueue.global().async {
            Thread.sleep(forTimeInterval: 4)
            SCShareableContent.getExcludingDesktopWindows(false, onScreenWindowsOnly: true) {
                content, _ in
                guard let display = content?.displays.first else { return }
                let f = SCContentFilter(display: display, excludingWindows: [])
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
                    note("nlc-helper: hold-indicator photographed, stream=\(controlStream != nil)")
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

// `--indicator-probe <seconds>`: show the menu-bar item and hold it.
//
// Whether a status item is actually ON SCREEN is not something its own API
// will tell you: `isVisible` answered true for an item nobody could see. The
// suspicion it exists to test is that the menu bar belongs to the console
// login session, and a process in another one — an ssh session, say — gets a
// status bar that is not the one the person is looking at.
if let index = CommandLine.arguments.firstIndex(of: "--indicator-probe"),
   index + 1 < CommandLine.arguments.count,
   let seconds = Double(CommandLine.arguments[index + 1]) {
    let app = NSApplication.shared
    let policySet = app.setActivationPolicy(.accessory)
    let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    item.button?.title = "●NLC"
    // The audit session id, read here rather than in the interpolation: the
    // type checker gives up on a long one, and the session is the whole point.
    var auditToken = audit_token_t()
    var tokenWords = mach_msg_type_number_t(
        MemoryLayout<audit_token_t>.size / MemoryLayout<natural_t>.size)
    let gotToken = withUnsafeMutablePointer(to: &auditToken) { p -> kern_return_t in
        p.withMemoryRebound(to: integer_t.self, capacity: Int(tokenWords)) {
            task_info(mach_task_self_, task_flavor_t(TASK_AUDIT_TOKEN), $0, &tokenWords)
        }
    } == KERN_SUCCESS
    let asid: String = gotToken ? String(auditToken.val.6) : "null"
    let hasButton: Bool = item.button != nil
    let visible: Bool = item.isVisible
    let pidNow: Int32 = getpid()
    // Where macOS actually PUT it. `isVisible` is the item's own opinion; the
    // button's window frame is the screen talking. An item the menu bar had no
    // room for still answers true and lives at a frame nobody can see.
    let buttonWindow = item.button?.window
    let frame = buttonWindow?.frame ?? .zero
    let screen = NSScreen.main?.frame ?? .zero
    let onScreen: Bool = buttonWindow?.isVisible ?? false
    var report = "{\"policy_set\": \(policySet)"
    report += ", \"has_button\": \(hasButton)"
    report += ", \"visible\": \(visible)"
    report += ", \"button_window_visible\": \(onScreen)"
    report += ", \"frame\": [\(frame.origin.x), \(frame.origin.y), "
    report += "\(frame.size.width), \(frame.size.height)]"
    report += ", \"screen\": [\(screen.size.width), \(screen.size.height)]"
    report += ", \"asid\": \(asid)"
    report += ", \"pid\": \(pidNow)}"
    // Reported from INSIDE the run loop, not before it. The menu bar lays a
    // status item out as part of running; read beforehand the button's window
    // is 48 wide and 0 high at the origin, which looks exactly like "macOS
    // refused to place it" and is really "nothing has run yet". That mistake
    // cost a round of this investigation.
    DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
        let laidOut = item.button?.window?.frame ?? .zero
        var after = report
        after += " /after-runloop/ {\"frame\": [\(laidOut.origin.x), "
        after += "\(laidOut.origin.y), \(laidOut.size.width), \(laidOut.size.height)]"
        after += ", \"visible\": \(item.isVisible)"
        after += ", \"window_visible\": \(item.button?.window?.isVisible ?? false)}"
        note("nlc-helper: indicator probe \(after)")
        if let out = CommandLine.arguments.firstIndex(of: "--probe-out"),
           out + 1 < CommandLine.arguments.count {
            try? after.write(toFile: CommandLine.arguments[out + 1],
                             atomically: true, encoding: .utf8)
        }
    }
    // And then photograph the menu bar, because every reading so far has been
    // the API's opinion of itself. `isVisible` said true for an item nobody
    // could see; a frame says where it would be. A picture is the only one of
    // the three that is evidence.
    if let shot = CommandLine.arguments.firstIndex(of: "--probe-shot"),
       shot + 1 < CommandLine.arguments.count {
        let path = CommandLine.arguments[shot + 1]
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.5) {
            SCShareableContent.getExcludingDesktopWindows(false, onScreenWindowsOnly: true) {
                content, _ in
                guard let display = content?.displays.first else { return }
                let filter = SCContentFilter(display: display, excludingWindows: [])
                let configuration = SCStreamConfiguration()
                configuration.width = display.width * 2
                configuration.height = display.height * 2
                configuration.showsCursor = false
                SCScreenshotManager.captureImage(contentFilter: filter,
                                                 configuration: configuration) { image, _ in
                    guard let image,
                          let destination = CGImageDestinationCreateWithURL(
                            URL(fileURLWithPath: path) as CFURL, UTType.png.identifier as CFString,
                            1, nil) else { return }
                    CGImageDestinationAddImage(destination, image, nil)
                    CGImageDestinationFinalize(destination)
                    note("nlc-helper: menu bar photographed to \(path)")
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
        guard let display = content?.displays.first else {
            report = "{\"started\": false, \"why\": \"no display: "
                + "\(error?.localizedDescription ?? "none")\"}"
            done.signal(); return
        }
        let filter = SCContentFilter(display: display, excludingWindows: [])
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
                        guard let display = content?.displays.first else { return }
                        let f = SCContentFilter(display: display, excludingWindows: [])
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

guard connectPath != nil else {
    FileHandle.standardError.write(Data(
        "nlc-helper: --connect 가 필요합니다 (서명된 런처가 실행합니다)\n".utf8))
    exit(3)
}

/// Does the process that launched us satisfy the code requirement?
///
/// This is the check that makes the helper refuse to be anyone's deputy: the
/// grant it holds is usable only through the signed `nlc` launcher, not by any
/// process that can execute this binary. It is a genuine OS check —
/// `SecCodeCheckValidity` against a requirement, not a name comparison —
/// though a PID can in principle be reused, so it is made once at startup and
/// the parent is pinned thereafter.
///
/// The requirement is COMPILED IN, never taken from the environment. An
/// environment override would have let a caller replace the check with one that
/// passes — the boundary would have been advisory (advisor finding). Override
/// at build time with -DNLC_DEV_REQUIREMENT for a different signing identity.
///
/// `anchor apple generic` alone only means "an Apple-issued identity", not
/// "ours", so the team is checked explicitly.
// Only the launcher. A separate broker binary was removed: anyone could run it
// and feed it well-formed JSON, so authenticating it proved nothing (advisor
// finding). The relay now lives inside the launcher, which hands its child an
// inherited socket nothing else can obtain.
let clientRequirement =
    "identifier \"ai.callabo.nlc\" and anchor apple generic and " +
    "certificate leaf[subject.OU] = \"48H4RZNB97\""

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
/// has to have been started by it, and the launcher runs only NLC's own code —
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
                       + "'NLC Helper' 를 켜주세요"])
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
        note("nlc-helper: \(why)")
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
    note("nlc-helper: peer \(PeerAuth.describe(fd: fd)) 인증됨")
    return fd
}

@Sendable func serveLauncher(at path: String) -> Never {
    guard let conn = connectToLauncher(path) else {
        note("nlc-helper: 런처에 연결하지 못했습니다")
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

// A panel and a key monitor need an app object and a run loop; the helper is an
// accessory (no Dock icon, never takes focus).
let nsApp = NSApplication.shared
nsApp.setActivationPolicy(.accessory)
watchForAbort()

DispatchQueue.global().async {
    serveLauncher(at: connectPath!)   // peer-authenticated; never returns
}
nsApp.run()
