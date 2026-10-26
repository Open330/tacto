// tacto — the signed entry point, the trust anchor, and the only thing that talks
// to the privileged helper.
//
// Three properties, each of which was missing at some point and put back after
// review:
//
// 1. It is a PRINCIPAL, not a relay someone else can drive. An earlier design
//    had a separate signed broker; any process could run that broker and feed
//    it well-formed JSON, and the helper — which only asks "is this the broker
//    binary?" — accepted. So the privileged channel lives here, in the process
//    that is already the trust anchor, and the child receives it as an
//    INHERITED socket it cannot obtain any other way.
//
// 2. It is NARROW. Being signed is not a licence to forward anything: only the
//    L2 vocabulary passes, every field is checked by name and type, and the AX
//    action names that reach AXUIElementPerformAction are enumerated. Nothing
//    that crosses can carry a path, a command or a URL.
//
// 3. It cannot be redirected. The interpreter and module are fixed, resolved
//    from the real executable path (not argv[0], which the caller chooses), run
//    isolated so the caller's directory is not on sys.path, with an allowlisted
//    environment and a fixed PATH.
//
// What running this still buys a caller is the Tacto command surface — flows,
// which are declarative, policy-checked, shown on screen and interruptible —
// not raw control of the accessibility API.

import Foundation
import Darwin
import Security
import AppKit

// MARK: - Where we are

func executablePath() -> String {
    var size = UInt32(4096)
    var buf = [CChar](repeating: 0, count: Int(size))
    guard _NSGetExecutablePath(&buf, &size) == 0 else { return "" }
    return String(cString: buf)
}

let exe = URL(fileURLWithPath: executablePath()).resolvingSymlinksInPath()
guard !exe.path.isEmpty else {
    FileHandle.standardError.write(Data("tacto: 실행 경로를 확인하지 못했습니다\n".utf8))
    exit(2)
}
let root = exe.deletingLastPathComponent().deletingLastPathComponent()
let python = root.appendingPathComponent(".venv/bin/python")
let helperApp = root.appendingPathComponent("helper/Tacto Helper.app")

guard FileManager.default.isExecutableFile(atPath: python.path) else {
    FileHandle.standardError.write(Data(
        "tacto: \(python.path) 이 없습니다. `uv sync` 를 먼저 실행하세요.\n".utf8))
    exit(2)
}

// MARK: - Message vetting

// `ask` is answered by this process, never forwarded: the child asks a
// question, we put it to a model out here where the user's PATH still exists,
// and hand back the text. That is the whole reason the child's PATH is fixed —
// it holds screen content, which is exactly what must not be able to choose
// what runs. Asking needs neither the helper channel nor a program name.
let allowedTypes: Set<String> = ["ping", "list_targets", "observe", "act",
                                 "probe", "selection", "ask", "document_text", "document_window_status", "document_rtf_export", "document_attachment_metadata", "save_panel", "menu_state",
                                 "menu_items", "capture", "readiness_read_cost", "readiness_preflight"]
// The largest complete document this channel will carry, in UTF-16 units. The
// same ceiling the helper enforces: a budget is a promise about how much this
// process will allocate, so both ends have to mean the same number.
let maxDocumentUTF16Units = 1_000_000
// Per-action exact schemas, not a union of every field. A union let `click`
// arrive carrying `text`, `action` and `pages` as well (advisor finding);
// requiring an exact key set makes each action mean one thing.
//
// `press_key` is here now, and narrowly: a NAMED set of six keys that move the
// focus ring, validated against the same list the helper checks. Escape is not
// among them — it is how the person stops this helper, and a key the agent can
// also press makes "the user pressed Esc" indistinguishable from "the agent
// did". Both sides validate, because the helper holds the grant and does not
// depend on a caller having filtered on its behalf.
//
// A generic key primitive stays out: even a window-bound key must be from
// the named navigation set. An unrestricted key turns an input field into
// a command channel —
// Return commits, and a modifier combination invokes. When it returns it should
// return split into a navigation capability and an explicit submit/shortcut one,
// so a recipe can fill a form without also being able to execute. Most of what
// people reached for it to do — save, copy, open — is a named menu command, and
// `menu` expresses those without a keystroke primitive at all.
let actionSchema: [String: (required: Set<String>, optional: Set<String>)] = [
    // `element_index` OR `point` for anything that acts on one element: a
    // coordinate is an address that has not been resolved yet, which is how a
    // control that draws itself and exposes no AX element becomes reachable.
    // Exactly one of the two, checked below — both would be two answers to one
    // question. `prepare_window` is not in the set on purpose: it takes a
    // WINDOW, and a point inside one resolves to whatever is drawn there.
    "click":            (["name"], ["element_index", "point", "window_index"]),
    "click_point":      (["name", "element_index", "point"], []),
    "drag":             (["name", "element_index", "from", "to", "duration"], ["via"]),
    // The global pointer, no buttons; restored afterwards (PointerHover.swift).
    "hover":            (["name", "element_index", "to", "duration", "hold"], ["via", "capture"]),
    "vroid_focus":      (["name", "element_index", "title"], []),
    "vroid_drag":       (["name", "element_index", "from", "to", "duration", "title"], ["via", "graph_bounds"]),
    // AXOpen where offered, else two window-bound clicks on the element.
    "double_click":     (["name", "element_index"], []),
    "window_input":     (["name", "element_index"], ["key", "text"]),
    "prepare_window":   (["name", "element_index"], []),
    "set_value":        (["name", "value"], ["element_index", "point", "window_index"]),
    "type_text":        (["name", "text"], ["element_index", "point", "window_index"]),
    "scroll":           (["name", "direction"], ["element_index", "point", "window_index", "pages"]),
    "secondary_action": (["name", "action"], ["element_index", "point", "window_index"]),
    // `menu` carries a title path instead of an element index: menu items are
    // not in the observation, because an app's whole menu bar is thousands of
    // items and paying for it on every observe would make the cheap path
    // expensive. The path is what distinguishes File > Save from File > Move to
    // Trash, so it is vetted as strictly as an element index would be.
    "menu":             (["name", "path"], ["focused_document"]),
    // Finder only (the helper checks): an existing folder's absolute path,
    // opened by LaunchServices without activating Finder — no keys, no sheet.
    "open_folder":      (["name", "path"], []),
    "reopen_window":    (["name"], []),
    "close_menu":       (["name"], []),
    // Reads the screen and changes nothing, so it carries no target at all.
    // `element_index` is optional here, not forbidden: a read of one named
    // element is how a batch answers with the screen AFTER its earlier
    // actions, and refusing the field made that unreachable through the signed
    // path while it worked in-process (peer request).
    "read":             (["name"], ["element_index", "point", "window_index"]),
    // Keys are bound to the observed app input, never global focus.
    "press_key":        (["name", "key", "element_index"], []),
    // `value` is accepted for shape-compatibility with the browser's
    // select_option, and must equal `option_label`: a native menu item has a
    // title and nothing behind it, so there is no value space to name.
    "select_option":    (["name", "element_index", "option_label"], ["value"]),
    // The two-step form. `open_select` opens a popup and stops — it names no
    // option because the whole reason it exists is that the caller does not
    // yet know what is in there.
    "open_select":      (["name", "element_index"], ["opening_action"]),
    // And this one chooses, from the observation that followed. `value` is
    // accepted only for shape-compatibility with the browser and must equal
    // `option_label`, for the same reason it must in `select_option`.
    "select_custom_option": (["name", "element_index", "option_label"], ["value"]),
]

// The vetting helpers below run on the connection thread as well as the main
// one, so they are @Sendable: without it this is a warning today and an error
// under the Swift 6 language mode. They hold no state, which is what makes the
// annotation honest rather than a silencer.

/// Text that is inserted must stay text. A newline or carriage return is what
/// turns an insertion into a submission, and control characters are not
/// something a person types into a form.
@Sendable func isPlainText(_ s: String) -> Bool {
    !s.unicodeScalars.contains { $0.value < 0x20 || $0.value == 0x7F }
}

/// A menu path: up to six levels, each one title or a few localized
/// alternatives. Bounded because the path is the whole address of the command —
/// it is to `menu` what element_index is to `click`.
@Sendable func isMenuPath(_ v: Any) -> Bool {
    guard let path = v as? [Any], !path.isEmpty, path.count <= 6 else { return false }
    func title(_ t: Any) -> Bool {
        guard let s = t as? String else { return false }
        return !s.isEmpty && s.count <= 200 && isPlainText(s)
    }
    for element in path {
        if title(element) { continue }
        guard let alts = element as? [Any], !alts.isEmpty, alts.count <= 8,
              alts.allSatisfy(title) else { return false }
    }
    return true
}
// An AX action name goes straight to AXUIElementPerformAction, so it is an
// escape hatch unless enumerated.
let allowedAXActions: Set<String> = ["AXPress", "AXShowMenu", "AXIncrement",
                                     "AXDecrement", "AXConfirm", "AXCancel"]
let topLevelKeys: Set<String> = ["credential_origin", "save_panel_token", "window_input_authorized", "coordinate_authorized", "submit_authorized", "click_deadline_uptime", "type", "obs_id", "target", "action", "mode",
                                 "require_window", "window_identity", "reveal", "prompt", "image", "model", "json", "path",
                                 "expect", "element_index", "provider",
                                 "document_token", "document_path", "max_utf16_units", "timeout_seconds",
                                 "window_index", "window_element_index", "panel_token", "require_format", "element_indices", "read_strategy", "titlebar_probe"]

// What a caller may claim its target still is. Reading one element's own
// attributes is about a millisecond; re-walking Mail's tree to find out is
// nine and a half SECONDS, measured. So the claim travels with the action and
// the helper checks it there — which also leaves no gap between checking and
// acting. It names attributes to compare, never a program or a permission.
let expectKeys: Set<String> = ["role", "label", "attribute", "enabled", "value",
                               "no_modal", "selection"]
// Which attribute a label was read from, so the check compares that one
// exactly instead of searching a rendered line for a substring.
let expectAttributes: Set<String> = ["Title", "Description", "Value", "Help",
                                     "Identifier", "Placeholder", "Subrole", "URL"]

// The only keys that may be asked for. Spelled out rather than parsed from a
// modifier grammar: a grammar is how `cmd+q` becomes reachable by spelling.
let navigationKeys: Set<String> = ["Tab", "shift+Tab", "Up", "Down",
                                   "Left", "Right"]
// Commit keys, kept apart from navigation (see the note on actionSchema).
let submitKeys: Set<String> = ["Return"]

// A question is a string and nothing else. No program, no arguments, no
// environment — the child names none of those, here or anywhere.
let maxPromptBytes = 400_000

// Which providers a question may ask for. The child names one of these or
// none; it never names a program, a path or an argument. Kept in step with
// `tacto/authoring.py::PROVIDER_ORDER`.
let knownProviders: Set<String> = ["auto", "claude", "codex", "opencode", "gemini", "cxweb"]

@Sendable func isInt(_ v: Any) -> Bool {
    // CFBoolean bridges to NSNumber, so a bare NSNumber test would let `true`
    // through where an index is expected.
    guard let n = v as? NSNumber else { return false }
    guard CFGetTypeID(n) != CFBooleanGetTypeID() else { return false }
    // And 1.5 is an NSNumber too. An index that is not a whole number is not a
    // smaller index — it is a value nothing downstream has a rule for, and
    // JSONSerialization hands it over without complaint (peer request).
    let d = n.doubleValue
    guard d.isFinite, d == d.rounded(.towardZero) else { return false }
    return abs(d) <= 9_007_199_254_740_992      // beyond this, not exact
}

func vetted(_ msg: [String: Any]) -> String? {
    guard let type = msg["type"] as? String else { return "type 없음" }
    guard allowedTypes.contains(type) else { return "허용되지 않은 type: \(type)" }
    for key in msg.keys where !topLevelKeys.contains(key) {
        return "허용되지 않은 최상위 필드: \(key)"
    }
    if let raw = msg["reveal"] {
        guard type == "observe", let labels = raw as? [String], (1...8).contains(labels.count),
              labels.allSatisfy({ !$0.isEmpty && $0.unicodeScalars.count <= 256 &&
                  $0.rangeOfCharacter(from: .controlCharacters.union(.newlines)) == nil }) else {
            return "invalid observe reveal labels"
        }
    }
    if let value = msg["window_identity"] {
        guard type == "observe", CFGetTypeID(value as CFTypeRef) == CFBooleanGetTypeID() else {
            return "window_identity requires an observe boolean"
        }
    }
    if (msg["read_strategy"] != nil || msg["titlebar_probe"] != nil) && type != "readiness_preflight" {
        return "read strategy is diagnostic-only"
    }
    if type == "readiness_preflight" {
        let fields = Set(["type","target","obs_id","element_index"])
        guard fields.isSubset(of: Set(msg.keys)), Set(msg.keys).isSubset(of: fields.union(["read_strategy", "titlebar_probe"])),
              msg["read_strategy"] == nil || ["batch","single"].contains(msg["read_strategy"] as? String ?? ""),
              msg["titlebar_probe"] == nil || CFGetTypeID(msg["titlebar_probe"]! as CFTypeRef) == CFBooleanGetTypeID(),
              let target = msg["target"] as? String,
              ["com.apple.finder","ai.callabo.tacto.modality-fixture"].contains(target),
              let obs = msg["obs_id"] as? String, !obs.isEmpty, obs.count <= 128,
              let value = msg["element_index"], isInt(value), let number = value as? NSNumber,
              !CFNumberIsFloatType(number as CFNumber), (0..<2500).contains(number.intValue) else {
            return "invalid readiness preflight request"
        }
        return nil
    }
    if type == "readiness_read_cost" {
        guard Set(msg.keys) == Set(["type","target","obs_id","element_indices"]),
              msg["target"] as? String == "com.apple.finder",
              let obs = msg["obs_id"] as? String, !obs.isEmpty, obs.count <= 128,
              let values = msg["element_indices"] as? [Any], (1...16).contains(values.count) else {
            return "invalid readiness cost request"
        }
        var indices: Set<Int> = []
        for value in values {
            guard isInt(value), let number = value as? NSNumber,
                  !CFNumberIsFloatType(number as CFNumber),
                  (0..<2500).contains(number.intValue), indices.insert(number.intValue).inserted else {
                return "invalid readiness cost indices"
            }
        }
        return nil
    }
    if msg["element_indices"] != nil { return "element_indices is only for readiness cost" }
    if msg["image"] != nil || msg["model"] != nil {
        guard type == "ask", let image = msg["image"] as? [String: Any],
              Set(image.keys) == Set(["media_type", "data"]),
              image["media_type"] as? String == "image/png",
              let encoded = image["data"] as? String,
              encoded.utf8.count <= 4 * ((5 * 1024 * 1024 + 2) / 3),
              let bytes = Data(base64Encoded: encoded), !bytes.isEmpty,
              bytes.count <= 5 * 1024 * 1024,
              bytes.starts(with: [137, 80, 78, 71, 13, 10, 26, 10]),
              bytes.base64EncodedString() == encoded,
              let provider = msg["provider"] as? String,
              ["claude", "codex"].contains(provider) else { return "invalid image request" }
        if let raw = msg["model"] {
            guard let model = raw as? String, !model.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                  model.utf8.count <= 128, !model.hasPrefix("-"),
                  !model.unicodeScalars.contains(where: { $0.value < 32 || $0.value == 127 }) else {
                return "invalid image model"
            }
        }
    }
    if let raw = msg["credential_origin"] {
        guard type == "act", let origin = raw as? String, origin.utf8.count <= 2048,
              let action = msg["action"] as? [String:Any],
              ["set_value","click","read"].contains(action["name"] as? String ?? ""),
              let index = action["element_index"], isInt(index),
              ((index as? NSNumber)?.intValue ?? -1) >= 0 else { return "invalid credential scope" }
    }
    if msg["window_input_authorized"] != nil || (msg["action"] as? [String:Any])?["name"] as? String == "window_input" {
        guard type == "act", (msg["action"] as? [String:Any])?["name"] as? String == "window_input",
              let value = msg["window_input_authorized"] as? NSNumber,
              CFGetTypeID(value) == CFBooleanGetTypeID(), value.boolValue else {
            return "opaque window input authorization required"
        }
    }
    if msg["coordinate_authorized"] != nil || ["click_point", "drag", "hover", "vroid_drag", "vroid_focus"].contains((msg["action"] as? [String:Any])?["name"] as? String ?? "") {
        guard type == "act", ["click_point", "drag", "hover", "vroid_drag", "vroid_focus"].contains((msg["action"] as? [String:Any])?["name"] as? String ?? ""),
              let value = msg["coordinate_authorized"] as? NSNumber,
              CFGetTypeID(value) == CFBooleanGetTypeID(), value.boolValue else {
            return "coordinate authorization required"
        }
    }
    if msg["submit_authorized"] != nil {
        guard type == "act", let action = msg["action"] as? [String:Any],
              action["name"] as? String == "press_key", action["key"] as? String == "Return",
              let value = msg["submit_authorized"] as? NSNumber,
              CFGetTypeID(value) == CFBooleanGetTypeID(), value.boolValue else {
            return "submit authorization is only for press_key Return"
        }
    }
    if type != "save_panel" && ["window_element_index", "panel_token", "require_format"].contains(where: { msg[$0] != nil }) {
        return "저장 패널 필드는 save_panel 에만 허용됩니다"
    }
    if let raw = msg["save_panel_token"] {
        guard type == "act", let token = raw as? String, !token.isEmpty, token.count <= 128,
              let action = msg["action"] as? [String:Any],
              Set(action.keys) == Set(["name", "element_index"]),
              action["name"] as? String == "click", isInt(action["element_index"] as Any),
              let index = action["element_index"] as? NSNumber, index.intValue >= 0 else {
            return "invalid save panel dispatch guard"
        }
    }
    if msg["document_token"] != nil && !["observe", "document_text", "document_window_status", "document_rtf_export", "document_attachment_metadata", "save_panel"].contains(type) {
        return "document_token 은 observe/document_text/save_panel 에만 허용됩니다"
    }
    if msg["document_path"] != nil && type != "document_text" {
        return "document_path 는 document_text 에만 허용됩니다"
    }
    if msg["path"] != nil && !["menu_state", "menu_items"].contains(type) {
        return "최상위 path 는 menu_state/menu_items 에만 허용됩니다"
    }
    if type == "menu_state" || type == "menu_items" {
        guard Set(msg.keys) == Set(["type", "target", "path"]),
              let target = msg["target"] as? String, !target.isEmpty,
              let path = msg["path"],
              isMenuPath(path) || (type == "menu_items" && (path as? [Any])?.isEmpty == true) else {
            return "menu_state 형식 오류"
        }
    }
    if type == "selection" {
        guard msg["target"] is String else { return "selection 에 target 없음" }
    }
    if type == "capture" {
        // One observed window, named by the observation that described it.
        // No path, no region, no display — the narrowing is the contract, so
        // the channel checks it rather than trusting the helper to.
        guard Set(msg.keys).isSubset(of: ["type", "target", "obs_id", "window_index", "timeout_seconds"]),
              let target = msg["target"] as? String, !target.isEmpty,
              let obsID = msg["obs_id"] as? String, !obsID.isEmpty, obsID.count <= 128 else {
            return "capture 형식 오류"
        }
        if let raw = msg["window_index"] {
            guard let number = raw as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(),
                  number.doubleValue == Double(number.intValue),
                  number.intValue >= 0, number.intValue < 64 else {
                return "capture window_index 오류"
            }
        }
        if let raw = msg["timeout_seconds"] {
            guard let number = raw as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(),
                  number.doubleValue.isFinite, number.doubleValue > 0,
                  number.doubleValue <= 10 else { return "capture timeout 오류" }
        }
    }
    if type == "probe" {
        guard msg["obs_id"] is String, msg["target"] is String,
              isInt(msg["element_index"] as Any) else { return "probe 형식 오류" }
    }
    if type == "ask" {
        guard let p = msg["prompt"] as? String else { return "ask 에 prompt 없음" }
        guard !p.isEmpty else { return "빈 prompt" }
        guard p.utf8.count <= maxPromptBytes else { return "prompt 가 너무 깁니다" }
        if let j = msg["json"], !(j is Bool) { return "json 형식 오류" }
        guard modelTimeout(msg["timeout_seconds"]) != nil else { return "invalid model timeout" }
        if let pv = msg["provider"] {
            // A name from a fixed list, never a path or an argument: the child
            // holds screen content and must not get to say what runs.
            guard let name = pv as? String, knownProviders.contains(name) else {
                return "알 수 없는 provider"
            }
        }
    }
    if type == "save_panel" {
        guard let target = msg["target"] as? String, !target.isEmpty,
              let obsID = msg["obs_id"] as? String, !obsID.isEmpty, obsID.count <= 128,
              let index = msg["window_element_index"], isInt(index),
              let number = index as? NSNumber, number.intValue >= 0, number.intValue < 5000 else {
            return "save_panel 관측 형식 오류"
        }
        if let raw = msg["require_format"], CFGetTypeID(raw as CFTypeRef) != CFBooleanGetTypeID() {
            return "require_format 은 boolean 이어야 합니다"
        }
        if let raw = msg["panel_token"] {
            guard let token = raw as? String, !token.isEmpty, token.count <= 128 else { return "panel_token 형식 오류" }
        }
        if let raw = msg["document_token"] {
            guard target == "com.apple.TextEdit", let token = raw as? String,
                  !token.isEmpty, token.count <= 128 else { return "save_panel document_token 형식 오류" }
        }
        for key in msg.keys where !["type", "target", "obs_id", "window_element_index", "panel_token", "require_format", "document_token"].contains(key) {
            return "save_panel 에 허용되지 않은 필드"
        }
    }
    if type == "document_text" {
        // Reading only, and only TextEdit — the same target restriction the
        // in-process reader states, checked here so a different bundle id
        // never reaches the helper at all.
        guard let target = msg["target"] as? String else { return "document_text 에 target 없음" }
        guard target == "com.apple.TextEdit" else {
            return "document_text 는 TextEdit 만 지원합니다"
        }
        if let token = msg["document_token"] {
            guard let text = token as? String, !text.isEmpty, text.count <= 128 else {
                return "document_token 형식 오류"
            }
        }
        if let raw = msg["document_path"] {
            guard msg["document_token"] == nil, let path = raw as? String,
                  path.hasPrefix("/"), !path.hasPrefix("//"), !path.contains("\\"),
                  path.utf8.count <= 4096,
                  !path.unicodeScalars.contains(where: { $0.value < 32 || $0.value == 127 }) else {
                return "document_path 형식 오류"
            }
        }
        if let budget = msg["max_utf16_units"] {
            // Rejected here rather than clamped: a caller that asked for a
            // gigabyte should be told no, not quietly given something else.
            guard isInt(budget), let n = budget as? NSNumber,
                  n.intValue >= 0, n.intValue <= maxDocumentUTF16Units else {
                return "max_utf16_units 형식 오류"
            }
        }
        for key in msg.keys where !["type", "target", "document_token", "document_path",
                                    "max_utf16_units"].contains(key) {
            return "document_text 에 허용되지 않은 필드: \(key)"
        }
    }
    if type == "document_window_status" {
        guard Set(msg.keys) == Set(["type", "target", "document_token"]),
              msg["target"] as? String == "com.apple.TextEdit",
              let token = msg["document_token"] as? String, !token.isEmpty, token.count <= 128 else {
            return "invalid document window status request"
        }
    }
    if type == "document_rtf_export" || type == "document_attachment_metadata" {
        guard Set(msg.keys).isSubset(of:["type","target","document_token","max_utf16_units","timeout_seconds"]),
              msg["target"] as? String == "com.apple.TextEdit",
              let token = msg["document_token"] as? String, !token.isEmpty, token.count <= 128 else {
            return "invalid document RTF export"
        }
        if let raw = msg["max_utf16_units"] {
            guard isInt(raw), let number = raw as? NSNumber, !CFNumberIsFloatType(number as CFNumber), number.intValue >= 0,
                  number.intValue <= (type == "document_attachment_metadata" ? 100_000 : maxDocumentUTF16Units) else { return "invalid document evidence text budget" }
        }
        if let raw = msg["timeout_seconds"] {
            guard let number = raw as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(),
                  number.doubleValue.isFinite, number.doubleValue > 0, number.doubleValue <= 6 else {
                return "invalid RTF export timeout"
            }
        }
    }
    if type == "observe" {
        guard msg["target"] is String else { return "observe 에 target 없음" }
        if let raw = msg["document_token"] {
            guard msg["target"] as? String == "com.apple.TextEdit", let token = raw as? String,
                  !token.isEmpty, token.count <= 128 else { return "document_token 형식 오류" }
        }
        if let rw = msg["require_window"], !(rw is Bool) {
            return "require_window 형식 오류"
        }
    }
    if let raw = msg["click_deadline_uptime"] {
        guard type == "act", let action = msg["action"] as? [String: Any],
              ["click", "click_point", "drag", "hover", "vroid_drag", "vroid_focus", "prepare_window", "press_key", "window_input"].contains(action["name"] as? String ?? ""),
              let number = raw as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(),
              number.doubleValue.isFinite, number.doubleValue > 0 else {
            return "invalid click deadline"
        }
    }
    if type == "act" {
        if let e = msg["expect"] {
            guard let d = e as? [String: Any] else { return "expect 형식 오류" }
            for k in d.keys where !expectKeys.contains(k) {
                return "허용되지 않은 expect 필드: \(k)"
            }
            if let r = d["role"], !(r is String) { return "expect.role 형식 오류" }
            if let l = d["label"], !(l is String) { return "expect.label 형식 오류" }
            if let value = d["value"] {
                guard let text = value as? String, text.utf8.count <= 16384 else { return "expect.value 형식 오류" }
            }
            if let n = d["no_modal"], !(n is Bool) { return "expect.no_modal 형식 오류" }
            if let en = d["enabled"], !(en is Bool) { return "expect.enabled 형식 오류" }
            if let at = d["attribute"] {
                guard let name = at as? String, expectAttributes.contains(name) else {
                    return "expect.attribute 가 알려진 속성이 아닙니다"
                }
            }
            if let sd = d["selection"] {
                guard let t = sd as? String, t.count <= 64 else {
                    return "expect.selection 형식 오류"
                }
            }
        }
        guard msg["obs_id"] is String, msg["target"] is String,
              let action = msg["action"] as? [String: Any],
              let name = action["name"] as? String else { return "act 형식 오류" }
        guard let schema = actionSchema[name] else {
            return "허용되지 않은 action: \(name)"
        }
        let keys = Set(action.keys)
        if !schema.required.isSubset(of: keys) {
            return "\(name) 에 필수 필드 누락: \(schema.required.subtracting(keys))"
        }
        if !keys.isSubset(of: schema.required.union(schema.optional)) {
            return "\(name) 에 허용되지 않은 필드: "
                 + "\(keys.subtracting(schema.required.union(schema.optional)))"
        }
        // Every action that acts on one element accepts either address.
        // `read` is the exception that may carry neither: a bare read is about
        // the screen, not about an element.
        let pointAddressable: Set<String> = ["click", "set_value", "type_text",
                                             "scroll", "secondary_action", "read"]
        if pointAddressable.contains(name) {
            let byIndex = action["element_index"] != nil
            let byPoint = action["point"] != nil
            if byIndex && byPoint {
                return "\(name) 은 element_index 또는 point 중 하나만 받습니다"
            }
            if !byIndex && !byPoint && name != "read" {
                return "\(name) 에 element_index 또는 point 가 필요합니다"
            }
            if byPoint {
                guard let raw = action["point"] as? [String: Any],
                      Set(raw.keys) == Set(["x", "y"]),
                      let x = raw["x"] as? NSNumber, let y = raw["y"] as? NSNumber,
                      CFGetTypeID(x) != CFBooleanGetTypeID(),
                      CFGetTypeID(y) != CFBooleanGetTypeID(),
                      x.doubleValue.isFinite, y.doubleValue.isFinite,
                      x.doubleValue >= 0, y.doubleValue >= 0,
                      x.doubleValue <= 100_000, y.doubleValue <= 100_000 else {
                    return "point 은 {x, y} 의 유한한 0 이상 숫자여야 합니다"
                }
            } else if action["window_index"] != nil {
                return "window_index 는 point 와 함께만 씁니다"
            }
        } else if name == "click_point" {
            guard let point = action["point"] as? [String:Any], Set(point.keys) == Set(["x","y"]),
                  let x = point["x"] as? NSNumber, let y = point["y"] as? NSNumber,
                  CFGetTypeID(x) != CFBooleanGetTypeID(), CFGetTypeID(y) != CFBooleanGetTypeID(),
                  x.doubleValue.isFinite, y.doubleValue.isFinite, x.doubleValue >= 0, y.doubleValue >= 0 else {
                return "invalid window-relative coordinate"
            }
        } else if action["point"] != nil || action["window_index"] != nil {
            return "\(name) 은 point 를 받지 않습니다"
        }
        if name == "window_input" {
            guard let index = action["element_index"] as? NSNumber,
                  CFGetTypeID(index) != CFBooleanGetTypeID(),
                  !CFNumberIsFloatType(unsafeBitCast(index, to: CFNumber.self)),
                  index.doubleValue.isFinite, index.doubleValue >= 0 else { return "opaque input requires a Window index" }
            if Set(action.keys) == Set(["name", "element_index", "key"]) {
                guard let key = action["key"] as? String, navigationKeys.contains(key) else { return "invalid opaque navigation key" }
            } else if Set(action.keys) == Set(["name", "element_index", "text"]), let text = action["text"] as? String {
                guard !text.isEmpty, text.utf16.count <= 64, !text.unicodeScalars.contains(where: {
                    $0.properties.generalCategory == .control || $0.value == 0x2028 || $0.value == 0x2029 || (0xF700...0xF8FF).contains($0.value)
                }) else { return "invalid opaque Unicode text" }
            } else { return "opaque input requires exactly one key or text" }
        }
        if name == "press_key" {
            guard let key = action["key"] as? String else { return "key 형식 오류" }
            // Return is the explicit submit key the note above asked for, split
            // from navigation: passed on for press_key only (never window_input),
            // and the helper takes it only into the Go to Folder sheet's path
            // field — the launcher cannot see roles, so the scoping is there.
            guard navigationKeys.contains(key) || submitKeys.contains(key) else {
                return "탐색 키가 아닙니다: \(key)"
            }
        }
        // An expectation about an element, on an action that names none, is a
        // condition nothing can check. Refused here as well as in the helper,
        // because silently dropping it is how a caller comes to believe its
        // checks ran.
        let takesElement = schema.required.contains("element_index")
                        || schema.optional.contains("element_index")
        if !takesElement, let e = msg["expect"] as? [String: Any] {
            for field in ["role", "label", "attribute", "enabled", "value"]
            where e[field] != nil {
                return "\(name) 은 대상 요소를 지정하지 않는데 expect.\(field) 가 있습니다"
            }
        }
        for (k, v) in action {
            switch k {
            case "opening_action":
                guard let opening=v as? String,["AXShowMenu","AXPress"].contains(opening) else {
                    return "opening_action 형식 오류"
                }
            case "key", "option_label":
                if !(v is String) { return "\(k) 형식 오류" }
            case "name", "direction": if !(v is String) { return "\(k) 형식 오류" }
            case "from", "to":
                guard ["drag","vroid_drag"].contains(name) || (name == "hover" && k == "to"), let point=v as? [String:Any], Set(point.keys)==Set(["x","y"]),
                      let x=point["x"] as? NSNumber, let y=point["y"] as? NSNumber,
                      CFGetTypeID(x) != CFBooleanGetTypeID(), CFGetTypeID(y) != CFBooleanGetTypeID(),
                      x.doubleValue.isFinite,y.doubleValue.isFinite,x.doubleValue>=0,y.doubleValue>=0 else {
                    return "invalid drag point"
                }
            case "graph_bounds":
                guard name=="vroid_drag",let rect=v as? [String:Any],
                      Set(rect.keys)==Set(["x","y","width","height"]) else {return "invalid curve graph bounds"}
                for key in ["x","y","width","height"] {
                    guard let n=rect[key] as? NSNumber,CFGetTypeID(n) != CFBooleanGetTypeID(),
                          n.doubleValue.isFinite,n.doubleValue>=0 else {return "invalid curve graph bounds"}
                }
                guard (rect["width"] as! NSNumber).doubleValue>0,
                      (rect["height"] as! NSNumber).doubleValue>0 else {return "empty curve graph bounds"}
            case "via":
                guard ["drag","vroid_drag","hover"].contains(name), let points=v as? [[String:Any]], points.count<=12 else {
                    return "invalid drag path"
                }
                for point in points {
                    guard Set(point.keys)==Set(["x","y"]),
                          let x=point["x"] as? NSNumber, let y=point["y"] as? NSNumber,
                          CFGetTypeID(x) != CFBooleanGetTypeID(), CFGetTypeID(y) != CFBooleanGetTypeID(),
                          x.doubleValue.isFinite,y.doubleValue.isFinite,x.doubleValue>=0,y.doubleValue>=0 else {
                        return "invalid drag path point"
                    }
                }
            case "capture":
                guard name == "hover", let b=v as? NSNumber, CFGetTypeID(b) == CFBooleanGetTypeID() else {return "invalid hover capture"}
            case "duration" where name == "hover", "hold":
                guard name == "hover", let d=v as? NSNumber, CFGetTypeID(d) != CFBooleanGetTypeID(),
                      d.doubleValue.isFinite, d.doubleValue >= 0, d.doubleValue <= 2 else {return "invalid hover \(k)"}
            case "duration":
                guard ["drag","vroid_drag"].contains(name),let d=v as? NSNumber,CFGetTypeID(d) != CFBooleanGetTypeID(),
                      d.doubleValue.isFinite,d.doubleValue >= (name == "drag" ? 0.1 : 0.5),d.doubleValue<=2 else {return "invalid drag duration"}
            case "title":
                guard let t=v as? String,t.utf8.count<=512,
                      (name=="vroid_drag" && t.contains(".vroid")) || (name=="vroid_focus" && (t=="VRoid Studio" || t=="VRoid Studio 2.14.0" || t.hasPrefix("VRoid Studio 2.14.0 - ") && t.contains(".vroid"))) else {return "invalid VRoid title"}
            case "point":
                if !(v is [String: Any]) { return "point 형식 오류" }
            case "window_index":
                guard let n = v as? NSNumber, CFGetTypeID(n) != CFBooleanGetTypeID(),
                      n.intValue >= 0, n.intValue <= 63 else {
                    return "window_index 형식 오류"
                }
            case "value": if !(v is String) { return "value 형식 오류" }
            case "text":
                guard let t = v as? String, isPlainText(t) else {
                    return "text 에 제어문자/개행은 넣을 수 없습니다"
                }
            case "action":
                guard let a = v as? String, allowedAXActions.contains(a) else {
                    return "허용되지 않은 AX action"
                }
            case "element_index", "pages": if !isInt(v) { return "\(k) 형식 오류" }
            case "focused_document":
                guard name == "menu", let text = v as? String, text.hasPrefix("/"), text.utf8.count <= 1024,
                      !text.unicodeScalars.contains(where: { $0.properties.generalCategory == .control }) else {
                    return "focused_document 형식 오류"
                }
            case "path":
                if name == "open_folder" {
                    guard let text = v as? String, text.hasPrefix("/"), text.utf8.count <= 1024,
                          !text.unicodeScalars.contains(where: { $0.properties.generalCategory == .control }) else {
                        return "open_folder path 형식 오류"
                    }
                } else if !isMenuPath(v) { return "menu path 형식 오류" }
            default: return "허용되지 않은 필드: \(k)"
            }
        }
    }
    return nil
}

// MARK: - Socket plumbing

@Sendable func fillAddr(_ path: String) -> sockaddr_un? {
    var addr = sockaddr_un()
    addr.sun_family = sa_family_t(AF_UNIX)
    let maxPath = MemoryLayout.size(ofValue: addr.sun_path) - 1
    guard path.utf8.count <= maxPath else { return nil }   // never truncate silently
    let bytes = Array(path.utf8) + [0]
    withUnsafeMutableBytes(of: &addr.sun_path) { dst in
        bytes.withUnsafeBytes { src in
            dst.copyMemory(from: UnsafeRawBufferPointer(
                start: src.baseAddress, count: min(src.count, dst.count)))
        }
    }
    return addr
}

let helperRequirement = TactoSigning.peerRequirement(identifier: "ai.callabo.tacto.helper")

/// Authenticate the SERVER we just connected to.
///
/// A 0700 directory keeps other users out, but the threat model is a process
/// running as this same user — which can unlink and rebind the socket and
/// answer as a fake helper. Authentication has to run both ways, so the
/// launcher checks the helper's audit token exactly as the helper checks ours.
/// The kernel's account of who is on the other end of a connected socket.
@Sendable func peerToken(_ fd: Int32) -> audit_token_t? {
    var token = audit_token_t()
    var len = socklen_t(MemoryLayout<audit_token_t>.size)
    let got = withUnsafeMutablePointer(to: &token) { ptr -> Bool in
        getsockopt(fd, SOL_LOCAL, LOCAL_PEERTOKEN, ptr, &len) == 0
    }
    return got ? token : nil
}

/// Our own, so a peer's audit session can be compared against something.
@Sendable func selfAuditToken() -> audit_token_t? {
    var token = audit_token_t()
    var count = mach_msg_type_number_t(
        MemoryLayout<audit_token_t>.size / MemoryLayout<natural_t>.size)
    let kr = withUnsafeMutablePointer(to: &token) { p -> kern_return_t in
        p.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
            task_info(mach_task_self_, task_flavor_t(TASK_AUDIT_TOKEN), $0, &count)
        }
    }
    return kr == KERN_SUCCESS ? token : nil
}

/// Does the peer satisfy `requirement`? A refusal names what failed: this is
/// the check that decides whether the privileged channel opens at all, and one
/// that can only say "no" is one nobody can tell apart from a build mistake.
@Sendable func peerSatisfies(_ fd: Int32, _ requirement: String) -> (Bool, String) {
    guard var token = peerToken(fd) else {
        return (false, "연결한 프로세스의 감사 토큰을 얻지 못했습니다")
    }
    let data = withUnsafeBytes(of: &token) { Data($0) } as CFData
    var code: SecCode?
    let copied = SecCodeCopyGuestWithAttributes(
        nil, [kSecGuestAttributeAudit: data] as CFDictionary, [], &code)
    guard copied == errSecSuccess, let code else {
        return (false, "연결한 프로세스의 코드 정보를 얻지 못했습니다 (OSStatus \(copied))")
    }
    var req: SecRequirement?
    guard SecRequirementCreateWithString(requirement as CFString, [], &req)
            == errSecSuccess, let req else {
        return (false, "requirement 문자열이 잘못되었습니다")
    }
    let status = SecCodeCheckValidity(code, [], req)
    if status == errSecSuccess { return (true, "ok") }
    return (false, "서명 요구 조건을 만족하지 않습니다 (OSStatus \(status), "
                   + "identifier \(peerIdentifier(code)))")
}

/// The peer's signing identifier, for a refusal message. Diagnostics only.
@Sendable func peerIdentifier(_ code: SecCode) -> String {
    var staticCode: SecStaticCode?
    guard SecCodeCopyStaticCode(code, [], &staticCode) == errSecSuccess,
          let staticCode else { return "unknown" }
    var info: CFDictionary?
    guard SecCodeCopySigningInformation(
            staticCode, SecCSFlags(rawValue: kSecCSSigningInformation), &info)
            == errSecSuccess, let dict = info as? [String: Any] else { return "unsigned" }
    return (dict["identifier"] as? String) ?? "unsigned"
}

func readJSONLine(_ fd: Int32, _ buffer: inout Data) -> [String: Any]? {
    while true {
        if let nl = buffer.firstIndex(of: 0x0A) {
            let line = buffer[buffer.startIndex..<nl]
            buffer = buffer[buffer.index(after: nl)...]
            return (try? JSONSerialization.jsonObject(with: line)) as? [String: Any]
        }
        if buffer.count > 8 * 1024 * 1024 { return nil }     // frame size limit
        var chunk = [UInt8](repeating: 0, count: 65536)
        let n = read(fd, &chunk, chunk.count)
        if n <= 0 { return nil }
        buffer.append(contentsOf: chunk[0..<n])
    }
}

func writeJSONLine(_ fd: Int32, _ obj: [String: Any]) {
    guard var d = try? JSONSerialization.data(withJSONObject: obj) else { return }
    d.append(0x0A)
    // One `write()` can be partial. Without the loop a long frame — a full
    // accessibility tree, or a model's answer — is silently cut into invalid
    // JSON, and the reader takes the remainder as the start of the next one:
    // the protocol desyncs and every later reply is wrong (peer request).
    var sent = 0
    d.withUnsafeBytes { raw in
        while sent < d.count {
            let n = write(fd, raw.baseAddress!.advanced(by: sent), d.count - sent)
            if n <= 0 {
                if errno == EINTR { continue }
                return                      // the far end is gone
            }
            sent += n
        }
    }
}

// MARK: - Environment

let allowedEnv = ["HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TZ",
                  "SSL_CERT_FILE", "TERM", "BW_SESSION"]
var env: [String: String] = [:]
let inherited = ProcessInfo.processInfo.environment
for key in allowedEnv { if let v = inherited[key] { env[key] = v } }
env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
env["TACTO_LAUNCHER"] = "1"

// A private per-run socket inside a 0700 directory we just created: an attacker
// cannot pre-bind a path they cannot predict or enter, which closes the
// fake-server substitution a fixed /tmp name allowed.
var template = Array("/tmp/tacto.XXXXXXXX".utf8CString)
guard let dirC = mkdtemp(&template) else {
    FileHandle.standardError.write(Data("tacto: 임시 디렉터리 생성 실패\n".utf8)); exit(1)
}
let runDir = String(cString: dirC)
let rendezvousPath = runDir + "/rendezvous.sock"
defer { unlink(rendezvousPath); rmdir(runDir) }

// MARK: - Helper
//
// The helper is opened through LaunchServices, not posix_spawn, and the reason
// is TCC. A screen-recording grant is not checked against the process that
// calls for it but against the identity its execution is attributed to, and a
// spawned child is attributed to whoever spawned it — here, the terminal
// session, which holds no grant. Measured on this machine with the same signed
// binary three ways (`launchctl procinfo`, 2026-09-21):
//
//   from a shell       responsible path = /usr/libexec/sshd-keygen-wrapper
//   posix_spawned      responsible path = /usr/libexec/sshd-keygen-wrapper
//   LaunchServices     responsible path = .../Tacto Helper.app/.../TactoHelper
//
// Only the third is the helper's own identity, and only the third can use the
// grant the user ticked against "Tacto Helper". Accessibility was never affected,
// which is why this went unnoticed: the two services do not attribute alike.
//
// What LaunchServices takes away is the parent-child relationship: no inherited
// descriptors, no ppid, no exit status. So the rendezvous is inverted. THIS
// process binds and listens first, and the helper connects back to a path only
// it was told. Binding before launching is what closes the substitution window
// a "wait for the helper's socket to appear" rendezvous leaves open — under the
// old direction a same-user process could bind that path first and answer in
// the helper's place, and only the peer check stood between us and it.

var helperRunning: NSRunningApplication?

/// The listening end of the rendezvous, bound before anything is launched.
@Sendable func openRendezvous() -> Int32? {
    let listener = socket(AF_UNIX, SOCK_STREAM, 0)
    guard listener >= 0, var addr = fillAddr(rendezvousPath) else { return nil }
    let size = socklen_t(MemoryLayout<sockaddr_un>.size)
    let bound = withUnsafePointer(to: &addr) { p in
        p.withMemoryRebound(to: sockaddr.self, capacity: 1) { bind(listener, $0, size) }
    }
    guard bound == 0, listen(listener, 1) == 0 else { close(listener); return nil }
    chmod(rendezvousPath, 0o600)
    return listener
}

/// Accept one connection, or give up. A blocking accept() would wait forever
/// for a helper that never started, and LaunchServices reports no exit status
/// we could have waited on instead.
@Sendable func acceptWithin(_ listener: Int32, seconds: Double) -> Int32? {
    var pfd = pollfd(fd: listener, events: Int16(POLLIN), revents: 0)
    let deadline = ProcessInfo.processInfo.systemUptime + seconds
    while ProcessInfo.processInfo.systemUptime < deadline {
        let remaining = deadline - ProcessInfo.processInfo.systemUptime
        let rc = poll(&pfd, 1, Int32(max(1, min(1000, remaining * 1000))))
        if rc < 0 { if errno == EINTR { continue }; return nil }
        if rc == 0 { continue }
        let conn = accept(listener, nil, nil)
        if conn >= 0 { return conn }
        if errno == EINTR || errno == ECONNABORTED { continue }
        return nil
    }
    return nil
}

/// Open the helper through LaunchServices and wait for the instance it made.
///
/// `createsNewApplicationInstance` is what makes this OUR helper rather than
/// one somebody else left running, and `allowsRunningApplicationSubstitution`
/// off refuses the substitution outright instead of quietly handing back a
/// stranger. `activates` stays off because the whole point of this project is
/// that the user's focus is not ours to take.
@Sendable func launchHelper() -> NSRunningApplication? {
    var isDir: ObjCBool = false
    guard FileManager.default.fileExists(atPath: helperApp.path, isDirectory: &isDir),
          isDir.boolValue else { return nil }
    let configuration = NSWorkspace.OpenConfiguration()
    configuration.activates = false
    configuration.addsToRecentItems = false
    configuration.createsNewApplicationInstance = true
    configuration.allowsRunningApplicationSubstitution = false
    configuration.arguments = ["--connect", rendezvousPath]
    configuration.environment = env

    let done = DispatchSemaphore(value: 0)
    var started: NSRunningApplication?
    NSWorkspace.shared.openApplication(at: helperApp, configuration: configuration) {
        app, _ in started = app; done.signal()
    }
    return done.wait(timeout: .now() + 10) == .success ? started : nil
}

/// Is the process on the other end the helper we just launched?
///
/// Two questions. The code requirement says it is our signed helper; the pid
/// says it is the instance LaunchServices made for us and not some other copy
/// that found the path. Neither alone is enough — a pid can be reused, and
/// "our signed helper" does not by itself mean "the one we asked for" — which
/// is also why the lifetime below is held through `NSRunningApplication` rather
/// than by remembering a number.
///
/// A same-audit-session check stood here too, and was wrong: measured on
/// 2026-09-21, an ssh-side launcher had asid 100228 and the helper
/// LaunchServices started for it had 100018, the console session. Driving the
/// screen from a remote shell is a thing this project is for, and the helper
/// has to be in the session that owns the screen, so those two sessions differ
/// by design and equality could never have held.
@Sendable func rendezvousAccepted(_ fd: Int32, expecting app: NSRunningApplication) -> String? {
    guard let token = peerToken(fd) else { return "연결한 프로세스를 확인하지 못했습니다" }
    let (signed, why) = peerSatisfies(fd, helperRequirement)
    guard signed else { return "연결한 프로세스가 서명된 도우미가 아닙니다 — " + why }
    // audit_token_t is eight words: [auid, euid, egid, ruid, rgid, pid, asid,
    // pidversion]. Swift cannot see libbsm's accessor macros, so the pid is
    // read by position.
    guard pid_t(bitPattern: token.val.5) == app.processIdentifier else {
        return "연결한 프로세스가 우리가 시작한 인스턴스가 아닙니다"
    }
    return nil
}

/// Launch, then take the one connection that answers. Returns the authenticated
/// descriptor, or nil with the reason already written to stderr.
@Sendable func startHelper() -> Int32? {
    guard let listener = openRendezvous() else {
        FileHandle.standardError.write(Data("tacto: 랑데부 소켓을 열지 못했습니다\n".utf8))
        return nil
    }
    defer { close(listener) }
    guard let app = launchHelper() else {
        FileHandle.standardError.write(Data(
            "tacto: \(helperApp.path) 을 열지 못했습니다\n".utf8))
        return nil
    }
    helperRunning = app
    guard let conn = acceptWithin(listener, seconds: 20) else {
        app.forceTerminate()
        helperRunning = nil
        FileHandle.standardError.write(Data("tacto: 도우미가 연결해 오지 않았습니다\n".utf8))
        return nil
    }
    if let why = rendezvousAccepted(conn, expecting: app) {
        close(conn)
        // A helper that connected and then left says so by having no code
        // object left to check. Reporting that as "not our helper" sent the
        // reader looking for a signing problem that was not there — and the
        // helper's own account of why it left is in the unified log, because
        // LaunchServices gave it no stderr to write on.
        let vanished = app.isTerminated
        app.forceTerminate()
        helperRunning = nil
        FileHandle.standardError.write(Data((vanished
            ? "tacto: 도우미가 연결 직후 종료했습니다 — 이유는 다음에서 확인하세요:\n"
              + "    log show --last 2m --predicate 'subsystem == \"ai.callabo.tacto\"'\n"
            : "tacto: \(why)\n").utf8))
        return nil
    }
    return conn
}

// Opt-in only. No helper mode setting reaches Python or screen data.
var effectiveHelperMode = "per_command"
var requestedHelperMode = "per_command"
var helperStartError = "helper_unavailable"
@Sendable func startSelectedHelper() -> Int32? {
    let settings = TactoRuntimeStatus.helperSettings(TactoRuntimeStatus.preferences(),environment:inherited)
    let mode = settings.mode
    requestedHelperMode = mode
    guard mode == "warm" else { return startHelper() }
    guard let key = TactoMode.buildKey(helperApp.appendingPathComponent("Contents/MacOS/TactoHelper")) else {
        modeDiagnostic(mode,"per_command","missing_build"); return startHelper()
    }
    helperStartError = "helper_unavailable"
    let attempt = acquireModeConnection(key,idleSeconds:settings.idleSeconds,cancelled:{
        var channel = pollfd(fd:relayEnd,events:0,revents:0)
        _ = poll(&channel,1,0)
        // Do not poll global key state here: only the existing helper owns
        // task interruption. Waiting adds no Input Monitoring requirement.
        return channel.revents & Int16(POLLHUP | POLLERR | POLLNVAL) != 0
    })
    switch attempt {
    case .ready(let fd,let actual):
        effectiveHelperMode = actual; modeDiagnostic(mode,actual, pid:peerToken(fd).map { Int($0.val.5) }); return fd
    case .busy:
        helperStartError = "helper_busy"
        modeDiagnostic(mode,"busy","driving_lease_wait_timeout"); return nil
    case .cancelled:
        helperStartError = "aborted_by_user"
        modeDiagnostic(mode,"cancelled","lease_wait_cancelled"); return nil
    case .unavailable(let reason):
        modeDiagnostic(mode,"per_command",reason); return startHelper()
    }
}

// MARK: - Child on an inherited socket

var pair: [Int32] = [0, 0]
guard socketpair(AF_UNIX, SOCK_STREAM, 0, &pair) == 0 else {
    FileHandle.standardError.write(Data("tacto: 채널 생성 실패\n".utf8)); exit(1)
}
let relayEnd = pair[0], childEnd = pair[1]
// A model deadline deliberately closes the child channel. A late reply must
// fail its write, not kill this launcher with SIGPIPE before child cleanup.
var relayNoSigPipe: Int32 = 1
guard setsockopt(relayEnd, SOL_SOCKET, SO_NOSIGPIPE, &relayNoSigPipe,
                 socklen_t(MemoryLayout<Int32>.size)) == 0 else {
    FileHandle.standardError.write(Data("tacto: 채널 종료 처리 설정 실패\n".utf8)); exit(1)
}
// CLOEXEC on both ends: without it the relay end leaks into the helper we spawn
// later, and any exec inherits a channel that was authenticated for someone else.
_ = fcntl(relayEnd, F_SETFD, FD_CLOEXEC)
_ = fcntl(childEnd, F_SETFD, FD_CLOEXEC)

env["TACTO_HELPER_FD"] = "3"

var actions: posix_spawn_file_actions_t?
posix_spawn_file_actions_init(&actions)
// Order matters: file actions run in sequence. Closing the relay end AFTER the
// dup2 closed the descriptor we had just created, because socketpair often
// hands back 3 and 4 — so the relay end IS fd 3 until we close it.
// Order matters: actions run in sequence, and socketpair often hands back 3 and
// 4, so the relay end IS fd 3 until closed. dup2 also clears CLOEXEC on the new
// descriptor, which is what lets fd 3 survive into the child — and only fd 3:
// the original childEnd is closed so the child holds exactly one copy.
posix_spawn_file_actions_addclose(&actions, relayEnd)
posix_spawn_file_actions_adddup2(&actions, childEnd, 3)
posix_spawn_file_actions_addclose(&actions, childEnd)

// -I (isolated): without it `python -m` puts the CALLER's working directory
// first on sys.path, so a fixed module would not be fixed code.
let childArgs = [python.path, "-I", "-B", "-m", "tacto.cli"] + Array(CommandLine.arguments.dropFirst())
var cArgs = childArgs.map { strdup($0) } + [nil]
var cEnv = env.map { strdup("\($0.key)=\($0.value)") } + [nil]
var childPID: pid_t = 0
let spawned = posix_spawn(&childPID, python.path, &actions, nil, &cArgs, &cEnv)
posix_spawn_file_actions_destroy(&actions)
cArgs.forEach { free($0) }; cEnv.forEach { free($0) }
close(childEnd)
guard spawned == 0 else {
    FileHandle.standardError.write(Data("tacto: 실행 실패\n".utf8)); exit(1)
}

// MARK: - Asking a model, out here
//
// Run OUR python, with OUR environment, so the provider table and the account
// routing live in one place instead of being restated in Swift. The asking
// process gets no helper channel and no fd 3: it sees a prompt and returns
// text, and cannot touch the screen whatever the prompt says.

@Sendable func modelTimeout(_ raw: Any?) -> Double? {
    guard let raw else { return 420 }
    guard let number = raw as? NSNumber,
          CFGetTypeID(number) != CFBooleanGetTypeID(),
          number.doubleValue.isFinite, number.doubleValue > 0,
          number.doubleValue <= 420 else { return nil }
    return number.doubleValue
}

enum ModelProcessError: Error { case timeout, ioFailure, outputLimit }
let modelCleanupGroup = DispatchGroup()

// Bound stdin, stdout (including inherited pipe writers), and process waiting.
// Ask our Python wrapper to unwind its CLI runners before forced termination.
// Cleanup stays off the relay thread. Noncooperative/detached descendants and
// remote inference are not guaranteed cancelled by this outer watchdog.
@Sendable func boundedModelProcess(_ proc: Process, prompt: String, timeout: Double) throws -> Data {
    let deadline = ProcessInfo.processInfo.systemUptime + timeout
    let input = Pipe(), output = Pipe()
    proc.standardInput = input
    proc.standardOutput = output
    // Register before launch, so shutdown cannot miss an active watchdog that
    // has not reached its cleanup defer yet.
    modelCleanupGroup.enter()
    do { try proc.run() }
    catch { modelCleanupGroup.leave(); throw error }
    let inputFD = input.fileHandleForWriting.fileDescriptor
    let outputFD = output.fileHandleForReading.fileDescriptor
    var inputOpen = true
    defer {
        if inputOpen { try? input.fileHandleForWriting.close() }
        try? output.fileHandleForReading.close()
        if proc.isRunning { kill(proc.processIdentifier, SIGTERM) }
        // A short grace allows the wrapper to kill/reap its own CLI group.
        // A hung wrapper still gets killed; relay responses retain their deadline.
        DispatchQueue.global().async {
            defer { modelCleanupGroup.leave() }
            let cleanupDeadline = ProcessInfo.processInfo.systemUptime + 0.75
            while proc.isRunning && ProcessInfo.processInfo.systemUptime < cleanupDeadline {
                usleep(10_000)
            }
            if proc.isRunning { kill(proc.processIdentifier, SIGKILL) }
            proc.waitUntilExit()
        }
    }
    let inputFlags = fcntl(inputFD, F_GETFL), outputFlags = fcntl(outputFD, F_GETFL)
    guard inputFlags >= 0, outputFlags >= 0,
          fcntl(inputFD, F_SETFL, inputFlags | O_NONBLOCK) >= 0,
          fcntl(outputFD, F_SETFL, outputFlags | O_NONBLOCK) >= 0,
          fcntl(inputFD, F_SETNOSIGPIPE, 1) >= 0 else { throw ModelProcessError.ioFailure }
    let bytes = Array(prompt.utf8)
    var sent = 0, result = Data()
    var outputEnded = false
    while true {
        let remaining = deadline - ProcessInfo.processInfo.systemUptime
        if remaining <= 0 { throw ModelProcessError.timeout }
        if inputOpen && sent == bytes.count {
            try input.fileHandleForWriting.close()
            inputOpen = false
        }
        if outputEnded && !proc.isRunning { return result }
        var fds = [pollfd(fd: outputEnded ? -1 : outputFD, events: Int16(POLLIN), revents: 0),
                   pollfd(fd: inputOpen ? inputFD : -1, events: Int16(POLLOUT), revents: 0)]
        let ready = poll(&fds, nfds_t(fds.count), Int32(max(1, min(50, remaining * 1000))))
        if ready < 0 {
            if errno == EINTR { continue }
            throw ModelProcessError.ioFailure
        }
        if inputOpen && fds[1].revents != 0 {
            let written = bytes.withUnsafeBytes { raw in
                Darwin.write(inputFD, raw.baseAddress!.advanced(by: sent), bytes.count - sent)
            }
            if written > 0 { sent += written }
            else if written < 0 && errno != EAGAIN && errno != EINTR { throw ModelProcessError.ioFailure }
        }
        if !outputEnded && fds[0].revents != 0 {
            var buffer = [UInt8](repeating: 0, count: 65536)
            let count = Darwin.read(outputFD, &buffer, buffer.count)
            if count == 0 { outputEnded = true }
            else if count > 0 {
                result.append(contentsOf: buffer.prefix(count))
                if result.count > 4 * 1024 * 1024 { throw ModelProcessError.outputLimit }
            } else if errno != EAGAIN && errno != EINTR { throw ModelProcessError.ioFailure }
        }
    }
}

func askModel(_ prompt: String, _ wantJSON: Bool, _ provider: String,
              timeout: Double = 420, image: [String: Any]? = nil, model: String? = nil) throws -> [String: Any]? {
    let proc = Process()
    proc.executableURL = python
    proc.arguments = ["-I", "-B", "-m", "tacto.ask_provider", "--result-envelope", "--timeout", String(timeout)]
        + (wantJSON ? ["--json"] : [])
        + (provider == "auto" ? [] : ["--provider", provider])
    proc.currentDirectoryURL = root
    proc.environment = ProcessInfo.processInfo.environment
    proc.standardError = FileHandle.standardError
    var input = prompt
    if let image {
        proc.arguments! += ["--image-envelope"]
        let body: [String: Any] = ["prompt": prompt, "image": image, "model": model as Any? ?? NSNull()]
        let encoded = try JSONSerialization.data(withJSONObject: body)
        guard let text = String(data: encoded, encoding: .utf8) else { throw ModelProcessError.ioFailure }
        input = text
    }
    let data = try boundedModelProcess(proc, prompt: input, timeout: timeout)
    if proc.terminationStatus == 124 { throw ModelProcessError.timeout }
    guard proc.terminationStatus == 0 else { return nil }
    guard let result = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          result["requested_provider"] as? String == provider,
          let requestID = result["request_id"] as? String, !requestID.isEmpty,
          let status = result["status"] as? String, ["ok", "error", "timeout"].contains(status) else {
        throw ModelProcessError.ioFailure
    }
    if status == "ok" {
        guard let text = result["text"] as? String, !text.isEmpty,
              result["error"] is NSNull else { throw ModelProcessError.ioFailure }
    } else {
        guard result["text"] is NSNull, result["error"] is [String: Any] else {
            throw ModelProcessError.ioFailure
        }
    }
    return result
}

// MARK: - Relay
//
// Runs only while the child asks for it. The helper is started lazily, so a
// command that never touches the screen never starts a privileged process.

let relayQueue = DispatchQueue(label: "tacto.relay")
relayQueue.async {
    var fromChild = Data(), fromHelper = Data()
    var helperFD: Int32 = -1
    // Once the helper connection drops, it stays dropped for this run.
    var helperGone = false
    while let msg = readJSONLine(relayEnd, &fromChild) {
        if let why = vetted(msg) {
            // Nothing was forwarded, so nothing happened — and the Python side
            // reads `effect` to decide whether re-running or undoing is even
            // allowed. A refusal without it is read as "may have happened",
            // which is the most cautious reading of the least dangerous case
            // (peer review).
            writeJSONLine(relayEnd, ["type": "error", "error": "rejected_by_launcher",
                                     "effect": "not_started", "detail": why])
            continue
        }
        if msg["type"] as? String == "ask" {
            let prompt = msg["prompt"] as! String
            let wantJSON = (msg["json"] as? Bool) ?? false
            let provider = (msg["provider"] as? String) ?? "auto"
            let timeout = modelTimeout(msg["timeout_seconds"])!
            do {
                if let result = try askModel(prompt, wantJSON, provider, timeout: timeout,
                    image: msg["image"] as? [String: Any], model: msg["model"] as? String) {
                    if result["status"] as? String == "ok" {
                        writeJSONLine(relayEnd, ["type": "answer", "text": result["text"]!,
                                                "provider_result_v1": result])
                    } else {
                        let timedOut = result["status"] as? String == "timeout"
                        writeJSONLine(relayEnd, ["type": "error",
                            "error": timedOut ? "model_timeout" : "no_answer",
                            "effect": "not_started", "provider_result_v1": result])
                    }
                } else {
                    writeJSONLine(relayEnd, ["type": "error", "error": "no_answer",
                                             "effect": "not_started"])
                }
            } catch {
                let timedOut = (error as? ModelProcessError) == .timeout
                writeJSONLine(relayEnd, ["type": "error", "error": timedOut ? "model_timeout" : "no_answer",
                                         "effect": "not_started"])
            }
            continue
        }
        if helperGone {
            writeJSONLine(relayEnd, ["type": "error", "error": "helper_unavailable",
                                     "effect": "not_started",
                                     "detail": "도우미 연결이 끊어진 실행입니다 — "
                                               + "다시 실행해 주세요"])
            continue
        }
        if helperFD < 0 {
            // Starting the helper and authenticating it are now one step: the
            // connection IS the launch result, so there is no second phase in
            // which we could have a running helper and no way to reach it.
            guard let fd = startSelectedHelper() else {
                writeJSONLine(relayEnd, ["type": "error", "error": helperStartError,
                                         "effect": "not_started",
                                         "retryable": helperStartError == "helper_busy",
                                         "detail": helperStartError == "helper_busy"
                                            ? "다른 작업이 도우미를 사용 중입니다. 작업이 끝난 뒤 다시 시도해 주세요."
                                            : (helperStartError == "aborted_by_user" ? "도우미 대기를 취소했습니다" : "도우미를 시작하지 못했습니다")])
                continue
            }
            helperFD = fd
        }
        writeJSONLine(helperFD, msg)
        guard let reply = readJSONLine(helperFD, &fromHelper) else {
            // The message went out and the answer never came back: this one
            // genuinely may have happened.
            //
            // And this run is over for helper traffic. Reconnecting on the next
            // message would hand back a helper with fresh per-connection state
            // — including a cleared abort — so a dropped connection became a
            // way to resurrect a run the user had stopped (peer review). A new
            // run is a new launcher, which is the user's to start.
            close(helperFD)
            helperFD = -1
            helperGone = true
            writeJSONLine(relayEnd, ["type": "error", "error": "helper_unavailable",
                                     "effect": "unknown",
                                     "detail": "도우미 연결이 끊어졌습니다 — "
                                               + "이 실행에서는 다시 연결하지 않습니다"])
            continue
        }
        var forwarded = reply
        if msg["type"] as? String == "ping", reply["type"] as? String == "pong",
           let token = peerToken(helperFD) {
            // OS credentials on the authenticated helper connection, not the
            // child's socket (whose peer is this launcher). Metadata only.
            forwarded["helper_peer"] = ["pid": Int(token.val.5), "pid_version": Int(token.val.7)]
        }
        if requestedHelperMode == "warm" {
            forwarded["helper_transport"] = effectiveHelperMode
        }
        writeJSONLine(relayEnd, forwarded)
    }
    if helperFD >= 0 { close(helperFD) }
}

var status: Int32 = 0
waitpid(childPID, &status, 0)
// Give already scheduled watchdog cleanup its bounded grace before process exit.
// This affects launcher shutdown, never the model response deadline.
_ = modelCleanupGroup.wait(timeout: .now() + 1)
// Closing the connection is what ends the helper — it serves one client and
// exits when that client goes away, which is the whole of its lifetime now that
// there is no parent to watch. This is the backstop for a helper that is wedged
// somewhere it cannot notice the close, and it addresses the instance object
// LaunchServices handed us rather than a pid we wrote down.
if let app = helperRunning, !app.isTerminated {
    app.terminate()
    let deadline = ProcessInfo.processInfo.systemUptime + 2
    while !app.isTerminated && ProcessInfo.processInfo.systemUptime < deadline {
        // NSRunningApplication caches time-varying properties until the main
        // run loop turns (AppKit SDK contract). Sleeping here kept a departed
        // helper looking alive for the entire two-second cleanup grace.
        let remaining = deadline - ProcessInfo.processInfo.systemUptime
        if remaining > 0 {
            let tick = min(0.05, remaining)
            let began = ProcessInfo.processInfo.systemUptime
            CFRunLoopRunInMode(CFRunLoopMode.defaultMode, tick, false)
            let rest = min(tick - (ProcessInfo.processInfo.systemUptime - began),
                           deadline - ProcessInfo.processInfo.systemUptime)
            if !app.isTerminated && rest > 0 { usleep(useconds_t(rest * 1_000_000)) }
        }
    }
    if !app.isTerminated { app.forceTerminate() }
}
exit((status & 0x7F) == 0 ? (status >> 8) & 0xFF : 1)
