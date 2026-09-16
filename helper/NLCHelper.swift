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

// MARK: - AX reading

let interactableRoles: Set<String> = [
    "AXButton", "AXTextField", "AXTextArea", "AXCheckBox", "AXRadioButton",
    "AXPopUpButton", "AXMenuButton", "AXMenuItem", "AXLink", "AXComboBox",
    "AXSlider", "AXIncrementor", "AXDisclosureTriangle", "AXTabGroup",
    "AXStaticText", "AXCell", "AXRow", "AXScrollArea", "AXGroup", "AXImage",
]

func attr(_ el: AXUIElement, _ name: String) -> CFTypeRef? {
    var value: CFTypeRef?
    return AXUIElementCopyAttributeValue(el, name as CFString, &value) == .success ? value : nil
}

func str(_ el: AXUIElement, _ name: String) -> String? {
    guard let v = attr(el, name) else { return nil }
    if let s = v as? String { return s.isEmpty ? nil : s }
    if let n = v as? NSNumber { return n.stringValue }
    return nil
}

/// One line per element, in the exact format the Python `Selector` parses:
/// `<indent><index> <Role>  Key: value, Key2: value [disabled]`
func walk(_ el: AXUIElement, depth: Int, out: inout [String], elements: inout [AXUIElement]) {
    if depth > 25 { return }
    let role = str(el, kAXRoleAttribute as String) ?? "?"
    var parts: [String] = []
    for (key, label) in [(kAXTitleAttribute as String, "Title"),
                         (kAXDescriptionAttribute as String, "Description"),
                         (kAXValueAttribute as String, "Value"),
                         (kAXHelpAttribute as String, "Help"),
                         ("AXIdentifier", "Identifier"),
                         ("AXPlaceholderValue", "Placeholder")] {
        if let v = str(el, key) { parts.append("\(label): \(v)") }
    }
    let label = parts.joined(separator: ", ")
    if interactableRoles.contains(role) || !label.isEmpty {
        let idx = elements.count
        elements.append(el)
        let enabled = (attr(el, kAXEnabledAttribute as String) as? Bool) ?? true
        let short = role.hasPrefix("AX") ? String(role.dropFirst(2)) : role
        out.append(String(repeating: "  ", count: depth) + "\(idx) \(short)"
                   + (label.isEmpty ? "" : "  \(label)") + (enabled ? "" : " [disabled]"))
    }
    for child in (attr(el, kAXChildrenAttribute as String) as? [AXUIElement]) ?? [] {
        walk(child, depth: depth + 1, out: &out, elements: &elements)
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
var latestTargetName: String = ""

// MARK: - Menu bar
//
// A large share of everyday commands (save, copy, open a folder) exist only in
// the menu bar, so a helper that can press buttons but not menu items cannot run
// the tasks people actually ask for. Menus are addressed by their user-visible
// title path rather than walked into the observation: an app's full menu bar
// runs to thousands of items, and the title is the same word the person says.

/// Children of a menu node, stepping through the AXMenu wrapper that nests
/// under every menu bar item and submenu.
func menuChildren(_ el: AXUIElement) -> [AXUIElement] {
    let kids = (attr(el, kAXChildrenAttribute as String) as? [AXUIElement]) ?? []
    if let menu = kids.first(where: { str($0, kAXRoleAttribute as String) == "AXMenu" }) {
        return (attr(menu, kAXChildrenAttribute as String) as? [AXUIElement]) ?? []
    }
    return kids
}

/// Resolve a menu command by title path. Each element is a list of acceptable
/// titles, so one recipe survives a localized menu ("Format" / "서식").
func menuItem(pid p: pid_t, path: [[String]]) -> (AXUIElement, [String])? {
    let app = AXUIElementCreateApplication(p)
    guard var node = attr(app, "AXMenuBar") as! AXUIElement? else { return nil }
    var walked: [String] = []
    for wants in path {
        guard let hit = menuChildren(node).first(where: {
            guard let t = str($0, kAXTitleAttribute as String) else { return false }
            return wants.contains(t)
        }) else { return nil }
        walked.append(str(hit, kAXTitleAttribute as String) ?? "")
        node = hit
    }
    return (node, walked)
}

/// Bring an app forward and wait until it really is forward. An app validates
/// its menus only while active, so a background TextEdit reports Edit > Select
/// All as disabled even though nothing is wrong with the command.
///
/// Raised through AXFrontmost rather than NSRunningApplication.activate: the
/// helper is a background process, and macOS may decline to let one of those
/// change which app is active. Setting frontmost is the assistive-technology
/// path, which is exactly the grant this helper holds — and it is asked and
/// answered through the same API, so the wait cannot disagree with the request.
func activate(pid p: pid_t, timeout: TimeInterval = 2.0) -> Bool {
    let app = AXUIElementCreateApplication(p)
    if (attr(app, kAXFrontmostAttribute as String) as? Bool) == true { return true }
    AXUIElementSetAttributeValue(app, kAXFrontmostAttribute as CFString,
                                 kCFBooleanTrue)
    NSRunningApplication(processIdentifier: p)?
        .activate(options: [.activateIgnoringOtherApps])
    let deadline = Date().addingTimeInterval(timeout)
    while Date() < deadline {
        if (attr(app, kAXFrontmostAttribute as String) as? Bool) == true { return true }
        usleep(50_000)
    }
    return false
}

func pid(forBundle bundle: String) -> pid_t? {
    for app in NSWorkspace.shared.runningApplications {
        if app.bundleIdentifier?.lowercased() == bundle.lowercased()
            || app.localizedName?.lowercased() == bundle.lowercased() {
            return app.processIdentifier
        }
    }
    return nil
}

func observe(_ target: String) -> [String: Any] {
    guard let p = pid(forBundle: target) else {
        let gui = NSWorkspace.shared.runningApplications.filter { $0.activationPolicy == .regular }
        if gui.isEmpty {
            return ["type": "error", "error": "gui_session_unavailable", "target": target]
        }
        return ["type": "error", "error": "target_not_found", "target": target]
    }
    let app = AXUIElementCreateApplication(p)
    let windows = ((attr(app, kAXWindowsAttribute as String) as? [AXUIElement]) ?? [])
        .filter { str($0, kAXRoleAttribute as String) == "AXWindow" }
    if windows.isEmpty {
        return ["type": "error", "error": "no_window_content", "target": target]
    }
    // Reading another app's contents is not neutral: an accessibility tree can
    // carry messages, names and account numbers. Show the outline for reads too,
    // so nothing is taken off the screen invisibly (advisor finding: observe
    // was silent, leaving confidentiality unprotected while integrity was not).
    if aborted {
        return ["type": "error", "error": "aborted_by_user"]
    }
    guard DispatchQueue.main.sync(execute: { showOverlay(forPID: p) }) else {
        return ["type": "error", "error": "indicator_unavailable",
                "detail": "조작 표시를 띄울 수 없어 중단했습니다"]
    }
    var lines: [String] = []
    var elements: [AXUIElement] = []
    for w in windows { walk(w, depth: 0, out: &lines, elements: &elements) }
    let obsID = "s" + UUID().uuidString.prefix(8)
    latestObsID = obsID
    latestElements = elements
    latestTargetPID = p
    latestTargetName = target
    return ["type": "observation", "obs_id": obsID, "target": target,
            "tree": lines.joined(separator: "\n"), "element_count": elements.count]
}

// MARK: - Acting
//
// Input is posted to the TARGET pid, never the global tap: a background target
// would otherwise receive keystrokes into whatever app currently has focus.

let eventSource: CGEventSource? = {
    let s = CGEventSource(stateID: .privateState)
    s?.userData = 0x4E4C4331            // 'NLC1' — our synthetic input is tagged
    return s
}()

func act(_ obsID: String, _ target: String, _ action: [String: Any]) -> [String: Any] {
    guard obsID == latestObsID else {
        return ["type": "error", "error": "stale_observation",
                "detail": "observe again and re-derive element_index"]
    }
    guard let p = pid(forBundle: target) else {
        return ["type": "error", "error": "target_not_found"]
    }
    // The observation and the action must concern the same app.
    guard p == latestTargetPID else {
        return ["type": "error", "error": "target_mismatch",
                "detail": "obs_id was taken on \(latestTargetName)"]
    }
    let elements = latestElements
    if aborted {
        return ["type": "error", "error": "aborted_by_user",
                "detail": "사용자가 Esc로 중단했습니다"]
    }
    guard DispatchQueue.main.sync(execute: { showOverlay(forPID: p) }) else {
        return ["type": "error", "error": "indicator_unavailable",
                "detail": "조작 표시를 띄울 수 없어 중단했습니다"]
    }
    let name = action["name"] as? String ?? ""

    func element() -> AXUIElement? {
        guard let i = action["element_index"] as? Int, i >= 0, i < elements.count else { return nil }
        return elements[i]
    }

    switch name {
    case "click":
        guard let el = element() else {
            return ["type": "error", "error": "action_failed", "detail": "element_index out of range"]
        }
        if AXUIElementPerformAction(el, kAXPressAction as CFString) != .success {
            return ["type": "error", "error": "action_failed", "detail": "AXPress failed"]
        }
    case "set_value":
        guard let el = element(), let v = action["value"] as? String else {
            return ["type": "error", "error": "action_failed", "detail": "bad set_value"]
        }
        if AXUIElementSetAttributeValue(el, kAXValueAttribute as CFString, v as CFTypeRef) != .success {
            return ["type": "error", "error": "action_failed", "detail": "AXValue set failed"]
        }
    case "type_text":
        // Typing requires a target element, and that element must be somewhere
        // text belongs. Before this, type_text posted key events to the PID
        // without consulting any element at all — which is what made "fill in a
        // field" and "type a command into a terminal" the same primitive.
        guard let text = action["text"] as? String, let el = element() else {
            return ["type": "error", "error": "action_failed",
                    "detail": "type_text 에는 element_index 가 필요합니다"]
        }
        let role = str(el, kAXRoleAttribute as String) ?? ""
        let textRoles: Set<String> = ["AXTextField", "AXTextArea", "AXComboBox",
                                      "AXSearchField"]
        guard textRoles.contains(role) else {
            return ["type": "error", "error": "action_failed",
                    "detail": "텍스트 입력 요소가 아닙니다: \(role)"]
        }
        guard !text.unicodeScalars.contains(where: { $0.value < 0x20 || $0.value == 0x7F })
        else {
            return ["type": "error", "error": "action_failed",
                    "detail": "제어문자는 입력할 수 없습니다"]
        }
        for ch in text {
            if aborted {
                return ["type": "error", "error": "aborted_by_user",
                        "detail": "입력 도중 사용자가 중단했습니다"]
            }
            for down in [true, false] {
                guard let ev = CGEvent(keyboardEventSource: eventSource, virtualKey: 0, keyDown: down)
                else { continue }
                var u = Array(String(ch).utf16)
                ev.keyboardSetUnicodeString(stringLength: u.count, unicodeString: &u)
                ev.postToPid(p)
            }
            usleep(10_000)
        }
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
                           "detail": "menu path 형식 오류"] }
        }
        if path.isEmpty || path.count > 6 {
            return ["type": "error", "error": "action_failed",
                    "detail": "menu path 길이가 올바르지 않습니다"]
        }
        if !activate(pid: p) {
            return ["type": "error", "error": "action_failed",
                    "detail": "앱을 앞으로 가져오지 못해 메뉴를 쓸 수 없습니다"]
        }
        guard let (item, walked) = menuItem(pid: p, path: path) else {
            return ["type": "error", "error": "action_failed",
                    "detail": "메뉴를 찾지 못했습니다"]
        }
        if (attr(item, kAXEnabledAttribute as String) as? Bool) == false {
            return ["type": "error", "error": "action_failed",
                    "detail": "메뉴 '\(walked.joined(separator: " > "))' 가 비활성 상태입니다"]
        }
        if AXUIElementPerformAction(item, kAXPressAction as CFString) != .success {
            return ["type": "error", "error": "action_failed",
                    "detail": "메뉴 실행 실패"]
        }
        return ["type": "acted", "action": name, "obs_id": obsID,
                "menu_path": walked, "note": "not verified — observe again"]
    case "secondary_action":
        // Duplicated here on purpose: the helper holds the grant, so it does not
        // depend on a caller having filtered on its behalf.
        let safeAXActions: Set<String> = ["AXPress", "AXShowMenu", "AXIncrement",
                                          "AXDecrement", "AXConfirm", "AXCancel"]
        guard let el = element(), let a = action["action"] as? String,
              safeAXActions.contains(a) else {
            return ["type": "error", "error": "action_failed",
                    "detail": "허용되지 않은 AX action"]
        }
        if AXUIElementPerformAction(el, a as CFString) != .success {
            return ["type": "error", "error": "action_failed", "detail": "secondary action failed"]
        }
    default:
        return ["type": "error", "error": "action_failed", "detail": "unknown action \(name)"]
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
// the actions — a caller cannot skip them by not drawing an overlay.

// Touched by the key monitor on main and by workers off it, so it goes through
// a lock rather than being read and written concurrently as a plain Bool.
private let abortLock = NSLock()
private var _aborted = false
var aborted: Bool {
    get { abortLock.lock(); defer { abortLock.unlock() }; return _aborted }
    set { abortLock.lock(); _aborted = newValue; abortLock.unlock() }
}
var overlay: NSWindow?

/// Returns false when the outline could not be drawn. Callers treat that as a
/// refusal: acting without the person being able to see it is the situation
/// this whole mechanism exists to prevent, so a drawing failure must stop the
/// action rather than silently proceed (advisor finding).
@discardableResult
func showOverlay(forPID pid: pid_t) -> Bool {
    let app = AXUIElementCreateApplication(pid)
    guard let win = attr(app, kAXFocusedWindowAttribute as String)
            ?? (attr(app, kAXWindowsAttribute as String) as? [AXUIElement])?.first
    else { return false }
    let winEl = win as! AXUIElement
    guard let posV = attr(winEl, kAXPositionAttribute as String),
          let sizeV = attr(winEl, kAXSizeAttribute as String) else { return false }
    var pt = CGPoint.zero, sz = CGSize.zero
    AXValueGetValue(posV as! AXValue, .cgPoint, &pt)
    AXValueGetValue(sizeV as! AXValue, .cgSize, &sz)
    // AX y is measured from the top of the PRIMARY screen; AppKit from the
    // bottom. screens[0] is the menu-bar screen, which is that origin — `main`
    // follows keyboard focus and would misplace the outline.
    guard let primary = NSScreen.screens.first else { return false }
    let frame = NSRect(x: pt.x, y: primary.frame.height - pt.y - sz.height,
                       width: sz.width, height: sz.height)

    // Follow the window rather than drawing once: a recipe that moves or
    // switches windows would otherwise outline the wrong place.
    if let existing = overlay {
        existing.setFrame(frame, display: true)
        existing.orderFrontRegardless()
        return true
    }

    let panel = NSPanel(contentRect: frame,
                        styleMask: [.borderless, .nonactivatingPanel],
                        backing: .buffered, defer: false)
    panel.isOpaque = false
    panel.backgroundColor = .clear
    panel.ignoresMouseEvents = true
    panel.level = .statusBar    // above normal and floating windows
    panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .ignoresCycle]
    panel.hasShadow = false

    let view = NSView(frame: NSRect(origin: .zero, size: frame.size))
    view.wantsLayer = true
    view.layer?.borderWidth = 4
    view.layer?.cornerRadius = 8
    view.layer?.borderColor = NSColor(srgbRed: 0.05, green: 0.62, blue: 0.55, alpha: 1).cgColor
    let label = NSTextField(labelWithString: "NLC가 이 창을 조작 중입니다 · Esc로 중단")
    label.font = .boldSystemFont(ofSize: 13)
    label.textColor = .white
    label.backgroundColor = NSColor(srgbRed: 0.05, green: 0.62, blue: 0.55, alpha: 1)
    label.drawsBackground = true
    label.sizeToFit()
    label.frame.origin = CGPoint(x: 14, y: frame.size.height - label.frame.height - 14)
    view.addSubview(label)
    panel.contentView = view
    panel.orderFrontRegardless()
    overlay = panel
    return true
}

func hideOverlay() {
    overlay?.orderOut(nil)
    overlay = nil
}

func watchForAbort() {
    // Esc anywhere stops the run. The helper watches for it itself, so a caller
    // cannot leave the user without a way out.
    NSEvent.addGlobalMonitorForEvents(matching: .keyDown) { ev in
        if ev.keyCode == 53 { aborted = true }          // Esc
    }
}

func respond(_ obj: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: obj),
          let line = String(data: data, encoding: .utf8) else { return }
    print(line)
    fflush(stdout)
}

func handle(_ msg: [String: Any]) -> [String: Any] {
    switch msg["type"] as? String {
    case "ping":
        return ["type": "pong", "trusted": AXIsProcessTrusted()]
    case "list_targets":
        let apps = NSWorkspace.shared.runningApplications
            .filter { $0.activationPolicy == .regular }
            .map { ["id": $0.bundleIdentifier ?? "", "displayName": $0.localizedName ?? "",
                    "pid": Int($0.processIdentifier), "isRunning": true] as [String: Any] }
        return ["type": "targets", "apps": apps]
    case "observe":
        return observe(msg["target"] as? String ?? "")
    case "act":
        return act(msg["obs_id"] as? String ?? "", msg["target"] as? String ?? "",
                   msg["action"] as? [String: Any] ?? [:])
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
guard CommandLine.arguments.contains("--listen") else {
    FileHandle.standardError.write(Data(
        "nlc-helper: --listen 이 필요합니다 (서명된 런처가 실행합니다)\n".utf8))
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
let parentSigningID = parentSigningTelemetry(parentPID) ?? "unknown"
DispatchQueue.global().async {
    while true {
        Thread.sleep(forTimeInterval: 1.0)
        if getppid() != parentPID || parentPID <= 1 { exit(0) }
    }
}

if !AXIsProcessTrusted() {
    respond(["type": "error", "error": "accessibility_permission_missing",
             "detail": "System Settings > Privacy & Security > Accessibility 에서 NLCHelper 를 켜주세요"])
}

// MARK: - Socket mode (peer-authenticated)
//
// With `--listen <path>` the helper serves one connection at a time over a unix
// socket and authenticates the CONNECTING PROCESS via its audit token, rather
// than inferring trust from ancestry. That distinction matters: ancestry admits
// anything descended from the signed launcher, so one attacker-controlled link
// in the chain was enough; a peer check names the process actually talking.

func serveSocket(at path: String) -> Never {
    unlink(path)
    let listener = socket(AF_UNIX, SOCK_STREAM, 0)
    guard listener >= 0 else {
        FileHandle.standardError.write(Data("nlc-helper: socket() 실패\n".utf8)); exit(4)
    }
    var addr = sockaddr_un()
    addr.sun_family = sa_family_t(AF_UNIX)
    let maxPath = MemoryLayout.size(ofValue: addr.sun_path) - 1
    let bytes = Array(path.utf8.prefix(maxPath)) + [0]
    withUnsafeMutableBytes(of: &addr.sun_path) { dst in
        bytes.withUnsafeBytes { src in
            dst.copyMemory(from: UnsafeRawBufferPointer(
                start: src.baseAddress, count: min(src.count, dst.count)))
        }
    }
    let size = socklen_t(MemoryLayout<sockaddr_un>.size)
    let bound = withUnsafePointer(to: &addr) { p in
        p.withMemoryRebound(to: sockaddr.self, capacity: 1) { bind(listener, $0, size) }
    }
    guard bound == 0, listen(listener, 1) == 0 else {
        FileHandle.standardError.write(Data("nlc-helper: bind/listen 실패\n".utf8)); exit(4)
    }
    // Only this user may reach the socket; the peer check does the rest.
    chmod(path, 0o600)

    while true {
        let conn = accept(listener, nil, nil)
        if conn < 0 { continue }
        let (ok, why) = PeerAuth.satisfies(fd: conn, requirement: clientRequirement)
        if !ok {
            let refusal = ["type": "error", "error": "peer_not_authorized",
                           "detail": why] as [String: Any]
            if let d = try? JSONSerialization.data(withJSONObject: refusal) {
                _ = d.withUnsafeBytes { write(conn, $0.baseAddress, d.count) }
                _ = "\n".withCString { write(conn, $0, 1) }
            }
            close(conn)
            continue
        }
        FileHandle.standardError.write(Data(
            "nlc-helper: peer \(PeerAuth.describe(fd: conn)) 인증됨\n".utf8))
        // Fresh state per connection.
        latestObsID = ""
        latestElements = []
        latestTargetPID = -1
        latestTargetName = ""
        serve(connection: conn)
        close(conn)
    }
}

/// Write the whole frame: a single write() can be partial, and without the loop
/// a long reply would be silently truncated into invalid JSON.
func writeAll(_ fd: Int32, _ data: Data) {
    var sent = 0
    data.withUnsafeBytes { raw in
        while sent < data.count {
            let n = write(fd, raw.baseAddress!.advanced(by: sent), data.count - sent)
            if n <= 0 { if errno == EINTR { continue } else { return } }
            sent += n
        }
    }
}

func serve(connection fd: Int32) {
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

let listenPath: String? = {
    let args = CommandLine.arguments
    guard let i = args.firstIndex(of: "--listen"), i + 1 < args.count else { return nil }
    return args[i + 1]
}()

DispatchQueue.global().async {
    serveSocket(at: listenPath!)   // peer-authenticated; never returns
}
nsApp.run()
