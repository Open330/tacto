import Foundation
import ServiceManagement

let service = SMAppService.agent(plistName:TactoResident.agent+".plist")
func status() -> String {
    switch service.status {
    case .enabled: return "enabled"
    case .notRegistered: return "notRegistered"
    case .requiresApproval: return "requiresApproval"
    case .notFound: return "notFound"
    @unknown default: return "unknown"
    }
}
do {
    switch CommandLine.arguments.dropFirst().first ?? "status" {
    case "register": try service.register()
    case "unregister": try service.unregister()
    case "status": break
    default: exit(2)
    }
    let data = try JSONSerialization.data(withJSONObject:["status":status()])
    print(String(decoding:data,as:UTF8.self))
} catch {
    let data = try! JSONSerialization.data(withJSONObject:["status":status(),"error":"\(error)"])
    print(String(decoding:data,as:UTF8.self)); exit(1)
}
