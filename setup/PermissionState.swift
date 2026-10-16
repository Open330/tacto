import Foundation
import CoreGraphics

struct PermissionReceipt: Decodable {
    let accessibility_trusted: Bool
    let screen_capture_preflight: Bool
    let bundle_id: String
    let pid: Int
    func valid(for expected: String) -> Bool { bundle_id == expected && pid > 0 }
}

/// The two grants the helper holds, in the order the person is asked for them.
enum Permission: CaseIterable {
    case control, screen

    var title: String {
        switch self {
        case .control: return "앱 조작"
        case .screen: return "화면 기록"
        }
    }
    /// Why, in the person's terms. Accurate to what the helper uses each grant for.
    var reason: String {
        switch self {
        case .control: return "에이전트가 부탁받은 작업에서 버튼을 누르고 글자를 입력하려면 필요해요."
        case .screen: return "접근성 정보만으로 알 수 없는 화면을 확인할 때 앱 창을 캡처하는 데 필요해요."
        }
    }
    /// Where it is in System Settings. macOS 27 renamed the Accessibility pane.
    var location: String {
        switch self {
        case .control: return "개인정보 보호 및 보안 › 손쉬운 사용 (macOS 27: Device Control and Data Access)"
        case .screen: return "개인정보 보호 및 보안 › 화면 기록 (Screen & System Audio Recording)"
        }
    }
    var settingsURL: URL {
        switch self {
        case .control: return URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")!
        case .screen: return URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture")!
        }
    }
}

struct SetupState {
    var accessibility = false
    var screen = false
    var ready: Bool { accessibility && screen }
    func granted(_ permission: Permission) -> Bool {
        permission == .control ? accessibility : screen
    }
    /// The next grant to ask for, in order; nil once both are on.
    var nextNeeded: Permission? { Permission.allCases.first { !granted($0) } }
    /// A failed refresh invalidates old grants: never offer connection from a
    /// stale successful receipt after helper launch, timeout or decoding failure.
    mutating func refresh(_ data: Data?, expected: String) -> Bool {
        guard let data else { accessibility = false; screen = false; return false }
        return receive(data, expected: expected)
    }
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

/// A bounded, read-only wait for one grant the person chose to turn on.
///
/// It exists so nobody has to press "check again": after they click "켜기",
/// the helper's own receipt is re-read until the grant appears. It never
/// requests a permission, and it ends on its own — a forgotten setup window
/// does not keep relaunching the helper.
struct PermissionWatch {
    let permission: Permission
    let started: Date
    static let limit: TimeInterval = 180
    static let interval: TimeInterval = 1.5
    func shouldContinue(at now: Date, state: SetupState) -> Bool {
        !state.granted(permission) && now.timeIntervalSince(started) < Self.limit
    }
}

/// Where the helper panel goes next to System Settings (Cocoa coordinates).
/// Beside it on the right if it fits, else on the left, else inside its
/// bottom-right corner — always fully on the screen that holds it.
func accessoryOrigin(settings: CGRect, panel: CGSize, screen: CGRect, gap: CGFloat = 12) -> CGPoint {
    let top = min(settings.maxY, screen.maxY) - panel.height
    var x: CGFloat
    if settings.maxX + gap + panel.width <= screen.maxX {
        x = settings.maxX + gap
    } else if settings.minX - gap - panel.width >= screen.minX {
        x = settings.minX - gap - panel.width
    } else {
        x = settings.maxX - panel.width - gap
    }
    x = max(screen.minX, min(x, screen.maxX - panel.width))
    let y = max(screen.minY, min(top, screen.maxY - panel.height))
    return CGPoint(x: x, y: y)
}

/// A CGWindowList rectangle (top-left origin of the primary display) in Cocoa
/// coordinates (bottom-left origin).
func cocoaRect(fromWindowList rect: CGRect, primaryHeight: CGFloat) -> CGRect {
    CGRect(x: rect.minX, y: primaryHeight - rect.maxY, width: rect.width, height: rect.height)
}


// The release layout has one fixed payload location; caller text cannot pick
// another interpreter. Absolute roots retain the checkout preview contract.
func setupRuntimeRoot(_ config: [String:String], resources: URL) -> URL? {
    if Set(config.keys) == Set(["root"]), let path = config["root"], path.hasPrefix("/") {
        return URL(fileURLWithPath:path)
    }
    guard config == ["bundledRoot":"runtime"] else { return nil }
    let base=resources.resolvingSymlinksInPath()
    let root=base.appendingPathComponent("runtime").resolvingSymlinksInPath()
    var directory: ObjCBool = false
    guard root.path.hasPrefix(base.path + "/"),
          FileManager.default.fileExists(atPath: root.path, isDirectory: &directory), directory.boolValue else { return nil }
    return root
}

/// macOS attributes the embedded helper's screen capture to its enclosing app.
/// Accessibility stays with the helper. A checkout preview has no enclosing
/// runtime app, so both permissions continue to belong to the external helper.
func permissionApp(_ permission: Permission, helper: URL, setupApp: URL, bundledRuntime: Bool) -> URL {
    permission == .screen && bundledRuntime ? setupApp : helper
}
