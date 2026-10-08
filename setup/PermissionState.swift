import Foundation
struct PermissionReceipt: Decodable {
    let accessibility_trusted: Bool
    let screen_capture_preflight: Bool
    let bundle_id: String
    let pid: Int
    func valid(for expected: String) -> Bool { bundle_id == expected && pid > 0 }
}
struct SetupState {
    var accessibility = false
    var screen = false
    var ready: Bool { accessibility && screen }
    mutating func receive(_ data: Data, expected: String) -> Bool {
        guard let receipt = try? JSONDecoder().decode(PermissionReceipt.self, from: data),
              receipt.valid(for: expected) else {
            accessibility = false; screen = false; return false
        }
        accessibility = receipt.accessibility_trusted
        screen = receipt.screen_capture_preflight
        return true
    }
}
