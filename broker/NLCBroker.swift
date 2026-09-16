// NLCBroker — the signed process that is allowed to talk to the helper.
//
// Peer authentication only means something if the peer is a binary the OS can
// distinguish. Python cannot be: a shared interpreter looks the same whoever is
// running it. So the broker is the peer — signed, hardened, and deliberately
// narrow — and Python talks to the broker.
//
// The narrowness is the point. If the broker relayed whatever it was handed,
// authenticating it would prove nothing: an attacker would drive the helper
// through it. So the broker accepts only the L2 vocabulary, checks the shape of
// every message, and refuses anything else. What passes through is the
// observe/act protocol, not arbitrary commands.
//
// Transport: Python -> broker on stdin/stdout (unprivileged), broker -> helper
// over the unix socket where the helper checks the broker's audit token.

import Foundation
import Darwin

let allowedTypes: Set<String> = ["ping", "list_targets", "observe", "act"]
let allowedActions: Set<String> = ["click", "set_value", "type_text", "press_key",
                                   "scroll", "secondary_action"]

/// Reject anything that is not a well-formed L2 message. A broker that forwards
/// blindly is a confused deputy with a signature.
func vetted(_ msg: [String: Any]) -> String? {
    guard let type = msg["type"] as? String else { return "type 없음" }
    guard allowedTypes.contains(type) else { return "허용되지 않은 type: \(type)" }
    if type == "observe" {
        guard msg["target"] is String else { return "observe 에 target 없음" }
    }
    if type == "act" {
        guard msg["obs_id"] is String, msg["target"] is String,
              let action = msg["action"] as? [String: Any],
              let name = action["name"] as? String else { return "act 형식 오류" }
        guard allowedActions.contains(name) else { return "허용되지 않은 action: \(name)" }
        // An element index must be an index; a payload must be text. Nothing
        // here carries a path, a command or a URL, so nothing can smuggle one.
        for (k, v) in action {
            switch k {
            case "name", "direction", "action": if !(v is String) { return "\(k) 형식 오류" }
            case "text", "value", "key":        if !(v is String) { return "\(k) 형식 오류" }
            case "element_index", "pages":      if !(v is NSNumber) { return "\(k) 형식 오류" }
            default: return "허용되지 않은 필드: \(k)"
            }
        }
    }
    return nil
}

func connectToHelper(_ path: String) -> Int32? {
    let fd = socket(AF_UNIX, SOCK_STREAM, 0)
    guard fd >= 0 else { return nil }
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
    let ok = withUnsafePointer(to: &addr) { p in
        p.withMemoryRebound(to: sockaddr.self, capacity: 1) { connect(fd, $0, size) }
    }
    return ok == 0 ? fd : nil
}

// MARK: - main

let args = CommandLine.arguments
guard let i = args.firstIndex(of: "--helper-socket"), i + 1 < args.count else {
    FileHandle.standardError.write(Data("nlc-broker: --helper-socket <path> 필요\n".utf8))
    exit(2)
}
guard let helperFD = connectToHelper(args[i + 1]) else {
    FileHandle.standardError.write(Data("nlc-broker: 도우미에 연결하지 못했습니다\n".utf8))
    exit(3)
}

func send(_ fd: Int32, _ obj: [String: Any]) {
    guard var d = try? JSONSerialization.data(withJSONObject: obj) else { return }
    d.append(0x0A)
    _ = d.withUnsafeBytes { write(fd, $0.baseAddress, d.count) }
}

func readLine(from fd: Int32, buffer: inout Data) -> [String: Any]? {
    while true {
        if let nl = buffer.firstIndex(of: 0x0A) {
            let line = buffer[buffer.startIndex..<nl]
            buffer = buffer[buffer.index(after: nl)...]
            return (try? JSONSerialization.jsonObject(with: line)) as? [String: Any]
        }
        var chunk = [UInt8](repeating: 0, count: 65536)
        let n = read(fd, &chunk, chunk.count)
        if n <= 0 { return nil }
        buffer.append(contentsOf: chunk[0..<n])
    }
}

var fromHelper = Data()
let stdinFD = FileHandle.standardInput.fileDescriptor
var fromClient = Data()

while let msg = readLine(from: stdinFD, buffer: &fromClient) {
    if let why = vetted(msg) {
        send(1, ["type": "error", "error": "rejected_by_broker", "detail": why])
        continue
    }
    send(helperFD, msg)
    guard let reply = readLine(from: helperFD, buffer: &fromHelper) else { break }
    send(1, reply)
}
close(helperFD)
