// nlc — the signed entry point, the trust anchor, and the only thing that talks
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
// What running this still buys a caller is the NLC command surface — flows,
// which are declarative, policy-checked, shown on screen and interruptible —
// not raw control of the accessibility API.

import Foundation
import Darwin
import Security

// MARK: - Where we are

func executablePath() -> String {
    var size = UInt32(4096)
    var buf = [CChar](repeating: 0, count: Int(size))
    guard _NSGetExecutablePath(&buf, &size) == 0 else { return "" }
    return String(cString: buf)
}

let exe = URL(fileURLWithPath: executablePath()).resolvingSymlinksInPath()
guard !exe.path.isEmpty else {
    FileHandle.standardError.write(Data("nlc: 실행 경로를 확인하지 못했습니다\n".utf8))
    exit(2)
}
let root = exe.deletingLastPathComponent().deletingLastPathComponent()
let python = root.appendingPathComponent(".venv/bin/python")
let helperBin = root.appendingPathComponent(
    "helper/NLC Helper.app/Contents/MacOS/NLCHelper")
let helperBare = root.appendingPathComponent("helper/nlc-helper")

guard FileManager.default.isExecutableFile(atPath: python.path) else {
    FileHandle.standardError.write(Data(
        "nlc: \(python.path) 이 없습니다. `uv sync` 를 먼저 실행하세요.\n".utf8))
    exit(2)
}

// MARK: - Message vetting

// `ask` is answered by this process, never forwarded: the child asks a
// question, we put it to a model out here where the user's PATH still exists,
// and hand back the text. That is the whole reason the child's PATH is fixed —
// it holds screen content, which is exactly what must not be able to choose
// what runs. Asking needs neither the helper channel nor a program name.
let allowedTypes: Set<String> = ["ping", "list_targets", "observe", "act",
                                 "probe", "selection", "ask"]
// Per-action exact schemas, not a union of every field. A union let `click`
// arrive carrying `text`, `action` and `pages` as well (advisor finding);
// requiring an exact key set makes each action mean one thing.
//
// `press_key` is deliberately absent. The helper does not implement it, and a
// generic key primitive is how "type into a field" becomes "run a command" —
// Return commits, and a modifier combination invokes. When it returns it should
// return split into a navigation capability and an explicit submit/shortcut one,
// so a recipe can fill a form without also being able to execute. Most of what
// people reached for it to do — save, copy, open — is a named menu command, and
// `menu` expresses those without a keystroke primitive at all.
let actionSchema: [String: (required: Set<String>, optional: Set<String>)] = [
    "click":            (["name", "element_index"], []),
    "set_value":        (["name", "element_index", "value"], []),
    "type_text":        (["name", "element_index", "text"], []),
    "scroll":           (["name", "element_index", "direction"], ["pages"]),
    "secondary_action": (["name", "element_index", "action"], []),
    // `menu` carries a title path instead of an element index: menu items are
    // not in the observation, because an app's whole menu bar is thousands of
    // items and paying for it on every observe would make the cheap path
    // expensive. The path is what distinguishes File > Save from File > Move to
    // Trash, so it is vetted as strictly as an element index would be.
    "menu":             (["name", "path"], []),
    // Reads the screen and changes nothing, so it carries no target at all.
    "read":             (["name"], []),
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
let topLevelKeys: Set<String> = ["type", "obs_id", "target", "action", "mode",
                                 "require_window", "prompt", "json",
                                 "expect", "element_index"]

// What a caller may claim its target still is. Reading one element's own
// attributes is about a millisecond; re-walking Mail's tree to find out is
// nine and a half SECONDS, measured. So the claim travels with the action and
// the helper checks it there — which also leaves no gap between checking and
// acting. It names attributes to compare, never a program or a permission.
let expectKeys: Set<String> = ["role", "label", "no_modal", "selection"]

// A question is a string and nothing else. No program, no arguments, no
// environment — the child names none of those, here or anywhere.
let maxPromptBytes = 400_000

@Sendable func isInt(_ v: Any) -> Bool {
    // CFBoolean bridges to NSNumber, so a bare NSNumber test would let `true`
    // through where an index is expected.
    guard let n = v as? NSNumber else { return false }
    return CFGetTypeID(n) != CFBooleanGetTypeID()
}

func vetted(_ msg: [String: Any]) -> String? {
    guard let type = msg["type"] as? String else { return "type 없음" }
    guard allowedTypes.contains(type) else { return "허용되지 않은 type: \(type)" }
    for key in msg.keys where !topLevelKeys.contains(key) {
        return "허용되지 않은 최상위 필드: \(key)"
    }
    if type == "selection" {
        guard msg["target"] is String else { return "selection 에 target 없음" }
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
    }
    if type == "observe" {
        guard msg["target"] is String else { return "observe 에 target 없음" }
        if let rw = msg["require_window"], !(rw is Bool) {
            return "require_window 형식 오류"
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
            if let n = d["no_modal"], !(n is Bool) { return "expect.no_modal 형식 오류" }
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
        for (k, v) in action {
            switch k {
            case "name", "direction": if !(v is String) { return "\(k) 형식 오류" }
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
            case "path": if !isMenuPath(v) { return "menu path 형식 오류" }
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

let helperRequirement =
    "identifier \"ai.callabo.nlc.helper\" and anchor apple generic and " +
    "certificate leaf[subject.OU] = \"48H4RZNB97\""

/// Authenticate the SERVER we just connected to.
///
/// A 0700 directory keeps other users out, but the threat model is a process
/// running as this same user — which can unlink and rebind the socket and
/// answer as a fake helper. Authentication has to run both ways, so the
/// launcher checks the helper's audit token exactly as the helper checks ours.
@Sendable func peerSatisfies(_ fd: Int32, _ requirement: String) -> Bool {
    var token = audit_token_t()
    var len = socklen_t(MemoryLayout<audit_token_t>.size)
    let got = withUnsafeMutablePointer(to: &token) { ptr -> Bool in
        getsockopt(fd, SOL_LOCAL, LOCAL_PEERTOKEN, ptr, &len) == 0
    }
    guard got else { return false }
    let data = withUnsafeBytes(of: &token) { Data($0) } as CFData
    var code: SecCode?
    guard SecCodeCopyGuestWithAttributes(
            nil, [kSecGuestAttributeAudit: data] as CFDictionary, [], &code)
            == errSecSuccess, let code else { return false }
    var req: SecRequirement?
    guard SecRequirementCreateWithString(requirement as CFString, [], &req)
            == errSecSuccess, let req else { return false }
    return SecCodeCheckValidity(code, [], req) == errSecSuccess
}

func connectSocket(_ path: String) -> Int32? {
    guard var addr = fillAddr(path) else { return nil }
    let fd = socket(AF_UNIX, SOCK_STREAM, 0)
    guard fd >= 0 else { return nil }
    let size = socklen_t(MemoryLayout<sockaddr_un>.size)
    let ok = withUnsafePointer(to: &addr) { p in
        p.withMemoryRebound(to: sockaddr.self, capacity: 1) { connect(fd, $0, size) }
    }
    if ok != 0 { close(fd); return nil }
    guard peerSatisfies(fd, helperRequirement) else {
        FileHandle.standardError.write(Data(
            "nlc: 소켓 반대편이 진짜 도우미가 아닙니다 — 연결을 끊습니다\n".utf8))
        close(fd); return nil
    }
    return fd
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
    _ = d.withUnsafeBytes { write(fd, $0.baseAddress, d.count) }
}

// MARK: - Environment

let allowedEnv = ["HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TZ",
                  "SSL_CERT_FILE", "TERM"]
var env: [String: String] = [:]
let inherited = ProcessInfo.processInfo.environment
for key in allowedEnv { if let v = inherited[key] { env[key] = v } }
env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
env["NLC_LAUNCHER"] = "1"

// A private per-run socket inside a 0700 directory we just created: an attacker
// cannot pre-bind a path they cannot predict or enter, which closes the
// fake-server substitution a fixed /tmp name allowed.
var template = Array("/tmp/nlc.XXXXXXXX".utf8CString)
guard let dirC = mkdtemp(&template) else {
    FileHandle.standardError.write(Data("nlc: 임시 디렉터리 생성 실패\n".utf8)); exit(1)
}
let runDir = String(cString: dirC)
let helperSocket = runDir + "/helper.sock"
defer { unlink(helperSocket); rmdir(runDir) }

// MARK: - Helper

let helperPath = FileManager.default.isExecutableFile(atPath: helperBin.path)
    ? helperBin.path : helperBare.path
var helperPID: pid_t = 0
func startHelper() -> Bool {
    var fileActions: posix_spawn_file_actions_t?
    posix_spawn_file_actions_init(&fileActions)
    defer { posix_spawn_file_actions_destroy(&fileActions) }
    let argv: [String] = [helperPath, "--listen", helperSocket]
    var cArgs = argv.map { strdup($0) } + [nil]
    var cEnv = env.map { strdup("\($0.key)=\($0.value)") } + [nil]
    defer { cArgs.forEach { free($0) }; cEnv.forEach { free($0) } }
    let rc = posix_spawn(&helperPID, helperPath, &fileActions, nil, &cArgs, &cEnv)
    if rc != 0 { return false }
    for _ in 0..<60 {
        if access(helperSocket, F_OK) == 0 { return true }
        usleep(100_000)
    }
    return false
}

// MARK: - Child on an inherited socket

var pair: [Int32] = [0, 0]
guard socketpair(AF_UNIX, SOCK_STREAM, 0, &pair) == 0 else {
    FileHandle.standardError.write(Data("nlc: 채널 생성 실패\n".utf8)); exit(1)
}
let relayEnd = pair[0], childEnd = pair[1]
// CLOEXEC on both ends: without it the relay end leaks into the helper we spawn
// later, and any exec inherits a channel that was authenticated for someone else.
_ = fcntl(relayEnd, F_SETFD, FD_CLOEXEC)
_ = fcntl(childEnd, F_SETFD, FD_CLOEXEC)

env["NLC_HELPER_FD"] = "3"

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
let childArgs = [python.path, "-I", "-m", "nlc.cli"] + Array(CommandLine.arguments.dropFirst())
var cArgs = childArgs.map { strdup($0) } + [nil]
var cEnv = env.map { strdup("\($0.key)=\($0.value)") } + [nil]
var childPID: pid_t = 0
let spawned = posix_spawn(&childPID, python.path, &actions, nil, &cArgs, &cEnv)
posix_spawn_file_actions_destroy(&actions)
cArgs.forEach { free($0) }; cEnv.forEach { free($0) }
close(childEnd)
guard spawned == 0 else {
    FileHandle.standardError.write(Data("nlc: 실행 실패\n".utf8)); exit(1)
}

// MARK: - Asking a model, out here
//
// Run OUR python, with OUR environment, so the provider table and the account
// routing live in one place instead of being restated in Swift. The asking
// process gets no helper channel and no fd 3: it sees a prompt and returns
// text, and cannot touch the screen whatever the prompt says.

func askModel(_ prompt: String, _ wantJSON: Bool) -> String? {
    let proc = Process()
    proc.executableURL = python
    proc.arguments = ["-I", "-m", "nlc.ask_provider"] + (wantJSON ? ["--json"] : [])
    proc.currentDirectoryURL = root
    proc.environment = ProcessInfo.processInfo.environment
    let inPipe = Pipe(), outPipe = Pipe()
    proc.standardInput = inPipe
    proc.standardOutput = outPipe
    proc.standardError = FileHandle.standardError
    do { try proc.run() } catch { return nil }
    inPipe.fileHandleForWriting.write(Data(prompt.utf8))
    try? inPipe.fileHandleForWriting.close()
    let data = outPipe.fileHandleForReading.readDataToEndOfFile()
    proc.waitUntilExit()
    guard proc.terminationStatus == 0 else { return nil }
    let text = String(decoding: data, as: UTF8.self)
        .trimmingCharacters(in: .whitespacesAndNewlines)
    return text.isEmpty ? nil : text
}

// MARK: - Relay
//
// Runs only while the child asks for it. The helper is started lazily, so a
// command that never touches the screen never starts a privileged process.

let relayQueue = DispatchQueue(label: "nlc.relay")
relayQueue.async {
    var fromChild = Data(), fromHelper = Data()
    var helperFD: Int32 = -1
    while let msg = readJSONLine(relayEnd, &fromChild) {
        if let why = vetted(msg) {
            writeJSONLine(relayEnd, ["type": "error", "error": "rejected_by_launcher",
                                     "detail": why])
            continue
        }
        if msg["type"] as? String == "ask" {
            let prompt = msg["prompt"] as! String
            let wantJSON = (msg["json"] as? Bool) ?? false
            if let text = askModel(prompt, wantJSON) {
                writeJSONLine(relayEnd, ["type": "answer", "text": text])
            } else {
                writeJSONLine(relayEnd, ["type": "error", "error": "no_answer",
                                         "detail": "모델에서 답을 받지 못했습니다"])
            }
            continue
        }
        if helperFD < 0 {
            if access(helperSocket, F_OK) != 0, !startHelper() {
                writeJSONLine(relayEnd, ["type": "error", "error": "helper_unavailable",
                                         "detail": "도우미를 시작하지 못했습니다"])
                continue
            }
            guard let fd = connectSocket(helperSocket) else {
                writeJSONLine(relayEnd, ["type": "error", "error": "helper_unavailable",
                                         "detail": "도우미에 연결하지 못했습니다"])
                continue
            }
            helperFD = fd
        }
        writeJSONLine(helperFD, msg)
        guard let reply = readJSONLine(helperFD, &fromHelper) else {
            writeJSONLine(relayEnd, ["type": "error", "error": "helper_unavailable",
                                     "detail": "도우미 연결이 끊어졌습니다"])
            helperFD = -1
            continue
        }
        writeJSONLine(relayEnd, reply)
    }
    if helperFD >= 0 { close(helperFD) }
}

var status: Int32 = 0
waitpid(childPID, &status, 0)
if helperPID > 0 { kill(helperPID, SIGTERM) }
exit((status & 0x7F) == 0 ? (status >> 8) & 0xFF : 1)
