import Foundation
import CoreGraphics

enum AgentConnection { case missing, current, other, conflict }

struct FirstRunAgent {
    let name: String
    let executable: URL
}

/// Discover installed CLIs without running a shell, provider or login check.
/// GUI apps have a short PATH, so include the usual per-user install locations.
func firstRunAgents(home: URL, environment: [String: String],
                    executable: (String) -> Bool = {
                        FileManager.default.isExecutableFile(atPath: $0)
                            && ((try? URL(fileURLWithPath: $0).resolvingSymlinksInPath().resourceValues(forKeys: [.isRegularFileKey]).isRegularFile) == true)
                    },
                    listDirectory: (String) -> [String] = {
                        (try? FileManager.default.contentsOfDirectory(atPath: $0)) ?? []
                    }) -> [FirstRunAgent] {
    let paths = (environment["PATH"] ?? "").split(separator: ":").map(String.init)
        + [home.appendingPathComponent(".local/bin").path,
           home.appendingPathComponent(".npm-global/bin").path,
           home.appendingPathComponent(".volta/bin").path,
           "/opt/homebrew/bin", "/usr/local/bin"]
        + nodeManagerBins(home: home, environment: environment, listDirectory: listDirectory)
    return [("Claude Code", "claude"), ("Codex", "codex")].compactMap { name, command in
        guard let folder = paths.first(where: { $0.hasPrefix("/") && executable(URL(fileURLWithPath: $0).appendingPathComponent(command).path) }) else { return nil }
        return FirstRunAgent(name: name, executable: URL(fileURLWithPath: folder).appendingPathComponent(command))
    }
}

/// Node version managers keep each version's CLIs in its own bin directory,
/// which a GUI app's PATH never contains. One directory level is listed per
/// manager (no recursion, no shell), newest version first.
func nodeManagerBins(home: URL, environment: [String: String],
                     listDirectory: (String) -> [String]) -> [String] {
    func version(_ name: String) -> [Int] {
        name.drop(while: { $0 == "v" }).split(separator: ".").map { Int($0) ?? -1 }
    }
    func newestFirst(_ root: String, _ suffix: String) -> [String] {
        listDirectory(root).filter { !$0.hasPrefix(".") }
            .sorted { version($1).lexicographicallyPrecedes(version($0)) }
            .map { "\(root)/\($0)/\(suffix)" }
    }
    let nvm = environment["NVM_DIR"].flatMap { $0.hasPrefix("/") ? $0 : nil }
        ?? home.appendingPathComponent(".nvm").path
    var fnmRoots = [home.appendingPathComponent("Library/Application Support/fnm").path,
                    home.appendingPathComponent(".local/share/fnm").path,
                    home.appendingPathComponent(".fnm").path]
    if let custom = environment["FNM_DIR"], custom.hasPrefix("/") { fnmRoots.insert(custom, at: 0) }
    return newestFirst(nvm + "/versions/node", "bin")
        + fnmRoots.flatMap { newestFirst($0 + "/node-versions", "installation/bin") }
}

/// What the first-run button may do after a Terminal session was opened.
/// The launcher deleting itself only says the session started; nothing here
/// observes, or claims, that the agent finished the task.
enum FirstRunStart: Equatable {
    case idle             // nothing pending; the button may start a session
    case opening          // Terminal has the launcher; it has not run yet
    case handedOff        // the launcher ran and removed itself
}

let firstRunHold: TimeInterval = 20

func firstRunStartState(startedAt: Date?, launcherExists: Bool, now: Date,
                        hold: TimeInterval = firstRunHold) -> FirstRunStart {
    guard let startedAt, now.timeIntervalSince(startedAt) < hold, now >= startedAt else { return .idle }
    return launcherExists ? .opening : .handedOff
}

func firstRunShellQuote(_ value: String) -> String {
    "'" + value.replacingOccurrences(of: "'", with: "'\\''") + "'"
}

/// Start a fresh, normal interactive CLI: no resume, permission bypass,
/// model overrides or noninteractive tool approval flags.
/// The agent's own bin directory goes first on PATH: an npm-installed CLI
/// starts with `#!/usr/bin/env node`, and that node lives beside it.
func firstRunCommand(agent: FirstRunAgent, prompt: String, directory: URL) -> String {
    let bin = agent.executable.deletingLastPathComponent().path
    return "#!/bin/zsh\ncd -- \(firstRunShellQuote(directory.path)) || exit 1\nrm -- \"$0\"\n"
        + "export PATH=\(firstRunShellQuote(bin))\"${PATH:+:$PATH}\"\n"
        + "exec \(firstRunShellQuote(agent.executable.path)) \(firstRunShellQuote(prompt))\n"
}

/// Inspect the command/skill links the installer owns; never run or change them.
func agentConnectionLinks(root: URL, home: URL, environment: [String: String]) -> [(URL, URL)] {
    func value(_ key: String) -> String? { environment[key].flatMap { $0.isEmpty ? nil : $0 } }
    let bin = value("TACTO_BIN_DIR") ?? value("NLC_BIN_DIR") ?? home.appendingPathComponent(".local/bin").path
    var folders = [home.appendingPathComponent(".claude/skills"), home.appendingPathComponent(".codex/skills")]
    for key in ["CLAUDE_CONFIG_DIR", "CODEX_HOME"] {
        if let value = environment[key], !value.isEmpty {
            folders.append(URL(fileURLWithPath: value).appendingPathComponent("skills"))
        }
    }
    var links = [(URL(fileURLWithPath: bin).appendingPathComponent("tacto"), root.appendingPathComponent("launcher/tacto")),
                 (URL(fileURLWithPath: bin).appendingPathComponent("nlc"), root.appendingPathComponent("launcher/tacto"))]
    // URL equality includes the directory/trailing-slash flag. Installation
    // identity is the canonical filesystem path, including repeated profiles.
    links += Set(folders.map { $0.resolvingSymlinksInPath().path }).sorted().map {
        (URL(fileURLWithPath: $0, isDirectory: true).appendingPathComponent("tacto"), root.appendingPathComponent("skill"))
    }
    return links
}

func agentConnectionReport(root: URL, home: URL, environment: [String: String]) -> [(URL, AgentConnection)] {
    let fm = FileManager.default
    let links = agentConnectionLinks(root: root, home: home, environment: environment)
    var report: [(URL, AgentConnection)] = []
    func ownedSkill(_ folder: URL) -> Bool {
        guard let file = try? FileHandle(forReadingFrom: folder.appendingPathComponent("SKILL.md")) else { return false }
        defer { try? file.close() }
        guard let data = try? file.read(upToCount: 4096), let text = String(data: data, encoding: .utf8) else { return false }
        return text.split(separator: "\n").contains { $0 == "name: tacto" || $0 == "name: nlc" }
    }
    for (link, expected) in links {
        let destination = try? fm.destinationOfSymbolicLink(atPath: link.path)
        if destination == nil && !fm.fileExists(atPath: link.path) { report.append((link, .missing)); continue }
        let resolved = link.resolvingSymlinksInPath()
        if destination != nil && fm.fileExists(atPath: resolved.path) && resolved.path == expected.resolvingSymlinksInPath().path { report.append((link, .current)); continue }
        var previous = resolved
        if expected.lastPathComponent == "tacto" {
            guard let destination else { report.append((link, .conflict)); continue }
            let written = URL(fileURLWithPath: destination, relativeTo: link.deletingLastPathComponent()).standardizedFileURL
            if written.path.hasSuffix("/launcher/tacto") || written.path.hasSuffix("/launcher/nlc") {
                previous = written.deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("skill")
            } else if written.path.hasSuffix("/.venv/bin/nlc") || written.path.hasSuffix("/.venv/bin/tacto") {
                previous = written.deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("skill")
            } else { report.append((link, .conflict)); continue }
        }
        report.append((link, ownedSkill(previous) ? .other : .conflict))
    }
    return report
}

func agentConnection(root: URL, home: URL, environment: [String: String]) -> AgentConnection {
    let states = agentConnectionReport(root: root, home: home, environment: environment).map { $0.1 }
    if states.contains(.conflict) { return .conflict }
    if states.contains(.other) { return .other }
    return states.contains(.missing) ? .missing : .current
}

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
        case .screen: return "화면 캡처 (선택)"
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
    var ready: Bool { accessibility }
    var captureReady: Bool { accessibility && screen }
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

/// Show the actual installed bundle's name, including a genuine legacy build.
func permissionAppName(_ app: URL) -> String {
    if FileManager.default.fileExists(atPath: app.path) {
        let shown = FileManager.default.displayName(atPath: app.path)
        return shown.hasSuffix(".app") ? String(shown.dropLast(4)) : shown
    }
    let bundle = Bundle(url: app)
    for key in ["CFBundleDisplayName", "CFBundleName"] {
        if let name = bundle?.object(forInfoDictionaryKey: key) as? String, !name.isEmpty {
            return name
        }
    }
    return app.deletingPathExtension().lastPathComponent
}

/// Select the full Settings window, not a small authentication dialog. Once
/// chosen, keep its window number while a modal window moves to the front.
struct SettingsWindowCandidate {
    let id: UInt32
    let bounds: CGRect
}
func settingsMainWindow(_ candidates: [SettingsWindowCandidate], anchoredID: UInt32?) -> SettingsWindowCandidate? {
    let windows = candidates.filter { $0.bounds.width >= 500 && $0.bounds.height >= 400 }
    if let anchoredID {
        if let pinned = windows.first(where: { $0.id == anchoredID }) { return pinned }
        // Reopened Settings gets a new number. Wait out any small modal first.
        if candidates.contains(where: { $0.bounds.width < 500 || $0.bounds.height < 400 }) { return nil }
    }
    return windows.max { $0.bounds.width * $0.bounds.height < $1.bounds.width * $1.bounds.height }
}
