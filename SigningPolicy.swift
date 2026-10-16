import Foundation
import Security

/// Bind both peers to the actual team that signed this executable. The OS
/// signature, never an environment variable, supplies the trust anchor.
enum TactoSigning {
    static func requirement(identifier: String, team: String) -> String? {
        guard identifier.range(of: "^[A-Za-z0-9.-]+$", options: .regularExpression) != nil,
              team.range(of: "^[A-Z0-9]{10}$", options: .regularExpression) != nil else { return nil }
        return "identifier \"\(identifier)\" and anchor apple generic and certificate leaf[subject.OU] = \"\(team)\""
    }

    static func ownTeam() -> String? {
        var own: SecCode?
        guard SecCodeCopySelf([], &own) == errSecSuccess, let own else { return nil }
        var anchor: SecRequirement?
        guard SecRequirementCreateWithString("anchor apple generic" as CFString, [], &anchor) == errSecSuccess,
              let anchor, SecCodeCheckValidity(own, [], anchor) == errSecSuccess else { return nil }
        var binary: SecStaticCode?
        guard SecCodeCopyStaticCode(own, [], &binary) == errSecSuccess, let binary else { return nil }
        var raw: CFDictionary?
        guard SecCodeCopySigningInformation(binary, SecCSFlags(rawValue: kSecCSSigningInformation), &raw) == errSecSuccess,
              let info = raw as? [String: Any],
              let team = info[kSecCodeInfoTeamIdentifier as String] as? String,
              requirement(identifier: "ai.callabo.nlc", team: team) != nil else { return nil }
        return team
    }

    static func peerRequirement(identifier: String) -> String {
        guard let team = ownTeam(), let value = requirement(identifier: identifier, team: team) else {
            FileHandle.standardError.write(Data("Tacto requires a valid Apple-issued team signature. Rebuild with NLC_SIGN_IDENTITY.\n".utf8))
            exit(78)
        }
        return value
    }
}
