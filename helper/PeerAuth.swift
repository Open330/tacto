// Peer authentication — who is on the other end of this connection, per the kernel.
//
// The ancestry check this replaces really said "a descendant of signed nlc may
// act", so any attacker-controlled link in that chain qualified. What we want
// instead is to authenticate the PEER ITSELF: the process that connected.
//
// `LOCAL_PEERTOKEN` yields the connecting process's audit token and
// `kSecGuestAttributeAudit` turns it into a code object the requirement can be
// applied to.
//
// Two honest limits, both from review:
//  * This is NOT free of PID reuse. XNU's LOCAL_PEERTOKEN takes the socket's
//    last_pid and does proc_find(), so a pid lookup happens inside the kernel
//    rather than in our code.
//  * It authenticates at accept() time only. XPC's peer requirements are
//    applied by the OS on every message; a byte stream authenticated once can
//    outlive the checked process if the descriptor is passed on or survives an
//    exec. We keep the window small — one connection per run, from a socket in
//    a directory only we can enter — but it is a window.

import Foundation
import Security
import Darwin

enum PeerAuth {
    static func auditToken(of fd: Int32) -> audit_token_t? {
        var token = audit_token_t()
        var len = socklen_t(MemoryLayout<audit_token_t>.size)
        let ok = withUnsafeMutablePointer(to: &token) { ptr -> Bool in
            getsockopt(fd, SOL_LOCAL, LOCAL_PEERTOKEN, ptr, &len) == 0
        }
        return ok ? token : nil
    }

    private static func code(for fd: Int32) -> SecCode? {
        guard var token = auditToken(of: fd) else { return nil }
        let data = withUnsafeBytes(of: &token) { Data($0) } as CFData
        let attrs = [kSecGuestAttributeAudit: data] as CFDictionary
        var code: SecCode?
        guard SecCodeCopyGuestWithAttributes(nil, attrs, [], &code) == errSecSuccess
        else { return nil }
        return code
    }

    /// Does the peer satisfy `requirement`? The explanation names what failed,
    /// so a refusal is debuggable without loosening the check.
    static func satisfies(fd: Int32, requirement: String) -> (Bool, String) {
        guard let code = code(for: fd) else {
            return (false, "연결한 프로세스의 코드 정보를 얻지 못했습니다")
        }
        var req: SecRequirement?
        guard SecRequirementCreateWithString(requirement as CFString, [], &req)
                == errSecSuccess, let req else {
            return (false, "requirement 문자열이 잘못되었습니다")
        }
        let status = SecCodeCheckValidity(code, [], req)
        if status == errSecSuccess { return (true, "ok") }
        return (false, "연결한 프로세스가 요구 조건을 만족하지 않습니다 (OSStatus \(status))")
    }

    /// The peer's signing identifier, for logs.
    static func describe(fd: Int32) -> String {
        guard let code = code(for: fd) else { return "unknown" }
        var info: CFDictionary?
        guard SecCodeCopySigningInformation(code as! SecStaticCode,
                                            SecCSFlags(rawValue: 0), &info) == errSecSuccess,
              let d = info as? [String: Any] else { return "unsigned" }
        return (d["identifier"] as? String) ?? "unsigned"
    }
}
