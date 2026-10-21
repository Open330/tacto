import Foundation
import ServiceManagement

/// `register`, `unregister` or `status` for the spike agent in this bundle.
let service = SMAppService.agent(plistName: "\(spikeAgentLabel).plist")
func describe(_ s: SMAppService.Status) -> String {
    switch s {
    case .notRegistered: return "notRegistered"
    case .enabled: return "enabled"
    case .requiresApproval: return "requiresApproval"
    case .notFound: return "notFound"
    @unknown default: return "unknown(\(s.rawValue))"
    }
}
let command = CommandLine.arguments.dropFirst().first ?? "status"
do {
    switch command {
    case "register": try service.register()
    case "unregister": try service.unregister()
    default: break
    }
    print("status: \(describe(service.status))")
} catch {
    print("error: \(error) status: \(describe(service.status))")
    exit(1)
}
