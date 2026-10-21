import Foundation

/// Spike only: measures whether a launchd-managed, on-demand XPC agent can
/// replace the per-command helper launch. Nothing here ships.
let spikeMachService = "ai.callabo.tacto.spike.xpc"
let spikeAgentLabel = "ai.callabo.tacto.spike.agent"

@objc protocol SpikeAgentProtocol {
    /// One round trip: who answered, whether it holds the grants, and how
    /// many calls THIS connection has made (state must not cross connections).
    func ping(reply: @escaping (NSDictionary) -> Void)
}

func teamRequirement(identifier: String, team: String) -> String {
    "identifier \"\(identifier)\" and anchor apple generic and certificate leaf[subject.OU] = \"\(team)\""
}

/// The team of this process's own verified signature, never configuration.
func ownTeam() -> String? {
    var code: SecCode?
    guard SecCodeCopySelf([], &code) == errSecSuccess, let code else { return nil }
    var staticCode: SecStaticCode?
    guard SecCodeCopyStaticCode(code, [], &staticCode) == errSecSuccess, let staticCode else { return nil }
    var info: CFDictionary?
    guard SecCodeCopySigningInformation(staticCode, SecCSFlags(rawValue: kSecCSSigningInformation), &info) == errSecSuccess,
          let dict = info as? [String: Any] else { return nil }
    return dict[kSecCodeInfoTeamIdentifier as String] as? String
}
