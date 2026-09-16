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

var observations: [String: [AXUIElement]] = [:]   // obs_id -> elements

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
    var lines: [String] = []
    var elements: [AXUIElement] = []
    for w in windows { walk(w, depth: 0, out: &lines, elements: &elements) }
    let obsID = "s" + UUID().uuidString.prefix(8)
    observations[obsID] = elements
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
    guard let elements = observations[obsID] else {
        return ["type": "error", "error": "stale_observation"]
    }
    guard let p = pid(forBundle: target) else {
        return ["type": "error", "error": "target_not_found"]
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
        guard let text = action["text"] as? String else {
            return ["type": "error", "error": "action_failed", "detail": "bad type_text"]
        }
        for ch in text {
            for down in [true, false] {
                guard let ev = CGEvent(keyboardEventSource: eventSource, virtualKey: 0, keyDown: down)
                else { continue }
                var u = Array(String(ch).utf16)
                ev.keyboardSetUnicodeString(stringLength: u.count, unicodeString: &u)
                ev.postToPid(p)
            }
            usleep(10_000)
        }
    case "secondary_action":
        guard let el = element(), let a = action["action"] as? String else {
            return ["type": "error", "error": "action_failed", "detail": "bad secondary_action"]
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

if !AXIsProcessTrusted() {
    respond(["type": "error", "error": "accessibility_permission_missing",
             "detail": "System Settings > Privacy & Security > Accessibility 에서 NLCHelper 를 켜주세요"])
}

while let line = readLine(strippingNewline: true) {
    if line.isEmpty { continue }
    guard let data = line.data(using: .utf8),
          let msg = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else {
        respond(["type": "error", "error": "bad_message"])
        continue
    }
    respond(handle(msg))
}
