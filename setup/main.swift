import AppKit

/// The helper's icon, draggable as its file URL into a System Settings list.
final class HelperIcon: NSImageView, NSDraggingSource {
    var helperURL: URL?
    override func mouseDown(with event: NSEvent) {
        guard let url = helperURL else { return }
        let item = NSDraggingItem(pasteboardWriter: url as NSURL)
        item.setDraggingFrame(bounds, contents: image)
        beginDraggingSession(with: [item], event: event, source: self)
    }
    func draggingSession(_ session: NSDraggingSession, sourceOperationMaskFor context: NSDraggingContext) -> NSDragOperation { .copy }
    override func resetCursorRects() { addCursorRect(bounds, cursor: .openHand) }
}

func label(_ text: String, size: CGFloat, weight: NSFont.Weight = .regular,
           color: NSColor = .labelColor, width: CGFloat? = nil) -> NSTextField {
    let field = NSTextField(wrappingLabelWithString: text)
    field.font = .systemFont(ofSize: size, weight: weight)
    field.textColor = color
    if let width { field.widthAnchor.constraint(equalToConstant: width).isActive = true }
    return field
}

func symbol(_ name: String, _ color: NSColor, size: CGFloat = 20) -> NSImage? {
    // Filled symbols have two layers: keep the glyph (✓) white on the color.
    let colors: [NSColor] = name.hasSuffix(".fill") ? [.white, color] : [color]
    let config = NSImage.SymbolConfiguration(pointSize: size, weight: .medium)
        .applying(.init(paletteColors: colors))
    return NSImage(systemSymbolName: name, accessibilityDescription: nil)?.withSymbolConfiguration(config)
}

/// One permission: what it is, why, where — and its live state.
final class PermissionRow: NSStackView {
    let permission: Permission
    let status = NSImageView()
    let detail: NSTextField
    let action: NSButton
    init(_ permission: Permission, target: AnyObject, selector: Selector) {
        self.permission = permission
        detail = label(permission.location, size: 11, color: .tertiaryLabelColor, width: 412)
        action = NSButton(title: "켜기", target: target, action: selector)
        super.init(frame: .zero)
        orientation = .horizontal; alignment = .top; spacing = 14
        edgeInsets = NSEdgeInsets(top: 14, left: 16, bottom: 18, right: 16)
        wantsLayer = true
        layer?.cornerRadius = 12
        layer?.borderWidth = 1
        status.widthAnchor.constraint(equalToConstant: 24).isActive = true
        let text = NSStackView(views: [
            label(permission.title, size: 15, weight: .semibold, width: 412),
            label(permission.reason, size: 13, color: .secondaryLabelColor, width: 412),
            detail])
        text.orientation = .vertical; text.alignment = .leading; text.spacing = 3
        action.bezelStyle = .rounded
        action.widthAnchor.constraint(equalToConstant: 118).isActive = true
        action.setAccessibilityLabel("\(permission.title) 켜기")
        addArrangedSubview(status); addArrangedSubview(text); addArrangedSubview(action)
        widthAnchor.constraint(equalToConstant: 624).isActive = true
    }
    required init?(coder: NSCoder) { nil }

    enum Look { case needed, current, waiting, granted, unknown }
    func show(_ look: Look) {
        let border: NSColor
        switch look {
        case .granted:
            status.image = symbol("checkmark.circle.fill", .systemGreen)
            action.title = "완료"; action.isEnabled = false; border = .separatorColor
        case .waiting:
            status.image = symbol("hourglass.circle", .systemOrange)
            action.title = "설정 다시 열기"; action.isEnabled = true; border = .systemOrange
        case .current:
            status.image = symbol("circle", .systemOrange)
            action.title = "켜기"; action.isEnabled = true; border = .systemOrange
        case .needed:
            status.image = symbol("circle", .tertiaryLabelColor)
            action.title = "켜기"; action.isEnabled = true; border = .separatorColor
        case .unknown:
            status.image = symbol("questionmark.circle", .tertiaryLabelColor)
            action.title = "켜기"; action.isEnabled = true; border = .separatorColor
        }
        layer?.borderColor = border.cgColor
        status.setAccessibilityLabel(look == .granted ? "켜짐" : "꺼짐")
    }
}

/// The small window beside System Settings: drag this, flip that switch.
final class AccessoryPanel: NSPanel {
    let heading = label("", size: 13, weight: .semibold, width: 228)
    let state = label("", size: 12, color: .secondaryLabelColor, width: 228)
    let icon = HelperIcon()
    init(helper: URL) {
        super.init(contentRect: NSRect(x: 0, y: 0, width: 260, height: 300),
                   styleMask: [.titled, .closable, .utilityWindow, .nonactivatingPanel],
                   backing: .buffered, defer: false)
        title = "Tacto 권한 도우미"
        level = .floating; hidesOnDeactivate = false; isReleasedWhenClosed = false
        collectionBehavior = [.moveToActiveSpace, .fullScreenAuxiliary]
        icon.helperURL = helper
        icon.image = NSWorkspace.shared.icon(forFile: helper.path)
        icon.imageScaling = .scaleProportionallyUpOrDown
        icon.widthAnchor.constraint(equalToConstant: 72).isActive = true
        icon.heightAnchor.constraint(equalToConstant: 72).isActive = true
        icon.setAccessibilityLabel("NLC Helper — 권한 목록으로 끌어다 놓으세요")
        let name = label("NLC Helper", size: 12, weight: .medium, width: 228); name.alignment = .center
        let steps = label("1. 이 아이콘을 설정 창의 목록으로 끌어다 놓으세요.\n2. 'NLC Helper' 옆 스위치를 켜세요.",
                          size: 12, width: 228)
        let fallback = label("끌어다 놓기가 안 되면 목록 아래 + 버튼으로 같은 앱을 추가하세요.",
                             size: 11, color: .tertiaryLabelColor, width: 228)
        heading.alignment = .center; state.alignment = .center
        let stack = NSStackView(views: [heading, icon, name, steps, fallback, state])
        stack.orientation = .vertical; stack.alignment = .centerX; stack.spacing = 10
        stack.edgeInsets = NSEdgeInsets(top: 16, left: 16, bottom: 16, right: 16)
        contentView = stack
    }
    override var canBecomeKey: Bool { true }
    func show(for permission: Permission) {
        heading.stringValue = "\(permission.title) 권한 켜기"
        state.stringValue = "켜지면 자동으로 알려 드릴게요."
        state.textColor = .secondaryLabelColor
    }
    func granted() {
        state.stringValue = "✓ 켜졌어요. 이 창은 곧 닫힙니다."
        state.textColor = .systemGreen
    }
}

final class Setup: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    var panel: AccessoryPanel!
    var rows: [PermissionRow] = []
    var state = SetupState()
    var known = false                        // has a receipt ever been read?
    var root: URL!
    var helper: URL!
    let status = label("", size: 12, color: .secondaryLabelColor, width: 624)
    let message = label("", size: 12, color: .secondaryLabelColor, width: 624)
    var connectButton: NSButton!
    var watch: PermissionWatch?
    var watchTimer: Timer?
    var placeTimer: Timer?
    var checkInFlight = false
    var pollTimer: Timer?
    var reportDirectory: URL?
    var snapshot: (URL, SetupState, Permission?)?

    func applicationDidFinishLaunching(_ notification: Notification) {
        if let request = snapshotRequest() { renderSnapshot(request); return }
        guard let config = Bundle.main.url(forResource: "runtime", withExtension: "json"),
              let data = try? Data(contentsOf: config),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: String],
              let resources = Bundle.main.resourceURL,
              let runtime = setupRuntimeRoot(object, resources: resources) else { NSApp.terminate(nil); return }
        root = runtime
        helper = root.appendingPathComponent("helper/NLC Helper.app")
        build()
        window.center(); window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        // Reading the helper's receipt requests nothing and changes nothing;
        // it is what lets every row start out true. Settings, permission
        // requests and installation still each wait for their own click.
        check()
    }

    func applicationDidBecomeActive(_ notification: Notification) {
        // Back from System Settings: say what changed without being asked.
        if known && !state.ready { check() }
    }

    func build() {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 680, height: 600),
                          styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
        window.title = "Tacto 시작하기"
        panel = AccessoryPanel(helper: helper)
        let stack = NSStackView(); stack.orientation = .vertical; stack.alignment = .leading; stack.spacing = 14
        stack.edgeInsets = NSEdgeInsets(top: 28, left: 28, bottom: 24, right: 28)
        window.contentView = stack
        stack.addArrangedSubview(label("Tacto를 사용할 준비", size: 24, weight: .bold, width: 624))
        stack.addArrangedSubview(label("에이전트가 여러분 대신 Mac 앱을 쓰려면 두 가지 권한이 필요해요. 한 번만 켜 두면 됩니다.",
                                       size: 13, color: .secondaryLabelColor, width: 624))
        for permission in Permission.allCases {
            let row = PermissionRow(permission, target: self, selector: #selector(turnOn(_:)))
            rows.append(row); stack.addArrangedSubview(row)
        }
        stack.addArrangedSubview(status)
        stack.setCustomSpacing(22, after: status)
        stack.addArrangedSubview(label("에이전트에 연결", size: 15, weight: .semibold, width: 624))
        stack.addArrangedSubview(label("Claude Code·Codex가 Tacto 명령과 스킬을 쓸 수 있게 연결합니다. 두 권한이 켜지면 누를 수 있어요.",
                                       size: 13, color: .secondaryLabelColor, width: 624))
        connectButton = NSButton(title: "에이전트에 연결", target: self, action: #selector(connect))
        connectButton.bezelStyle = .rounded; connectButton.keyEquivalent = "\r"; connectButton.isEnabled = false
        stack.addArrangedSubview(connectButton)
        stack.addArrangedSubview(message)
        let note = label("권한은 서명된 도우미 'NLC Helper' 하나에만 줍니다. 터미널이나 파이썬에는 주지 않아요. 요청하신 작업 중에만 조작하고, 언제든 Esc로 멈출 수 있어요.",
                         size: 11, color: .tertiaryLabelColor, width: 624)
        stack.addArrangedSubview(note)
        render()
    }

    func render() {
        for row in rows {
            let look: PermissionRow.Look
            if state.granted(row.permission) { look = .granted }
            else if watch?.permission == row.permission { look = .waiting }
            else if !known { look = .unknown }
            else if state.nextNeeded == row.permission { look = .current }
            else { look = .needed }
            row.show(look)
        }
        connectButton.isEnabled = state.ready
        if state.ready { status.stringValue = "✓ 두 권한이 모두 켜졌어요." }
        else if let watching = watch { status.stringValue = "\(watching.permission.title) 권한이 켜지기를 기다리는 중… 켜면 자동으로 넘어갑니다." }
        else if !known { status.stringValue = "권한 상태를 확인하고 있어요…" }
        else { status.stringValue = "‘켜기’를 누르면 설정이 열리고, 옆에 작은 도우미 창이 함께 뜹니다." }
    }

    // MARK: turning a permission on

    @objc func turnOn(_ sender: NSButton) {
        guard let row = rows.first(where: { $0.action === sender }) else { return }
        guard FileManager.default.fileExists(atPath: helper.path) else {
            status.stringValue = "NLC Helper를 찾지 못했습니다. Tacto 설치 경로를 확인해주세요."; return
        }
        let permission = row.permission
        NSWorkspace.shared.open(permission.settingsURL)
        panel.show(for: permission)
        watch = PermissionWatch(permission: permission, started: Date())
        render()
        placeTimer?.invalidate(); placeAccessory()
        // System Settings may take a moment to appear and can be moved.
        placeTimer = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] _ in self?.placeAccessory() }
        watchTimer?.invalidate()
        watchTimer = Timer.scheduledTimer(withTimeInterval: PermissionWatch.interval, repeats: true) { [weak self] _ in
            guard let self, let watch = self.watch else { return }
            if watch.shouldContinue(at: Date(), state: self.state) { self.check() }
            else { self.endWatch(granted: self.state.granted(watch.permission)) }
        }
    }

    func placeAccessory() {
        guard let settings = settingsWindowFrame() else {
            if !panel.isVisible { panel.center(); panel.orderFrontRegardless() }
            return
        }
        let screen = NSScreen.screens.first(where: { $0.frame.intersects(settings) })?.visibleFrame
            ?? NSScreen.main?.visibleFrame ?? settings
        panel.setFrameOrigin(accessoryOrigin(settings: settings, panel: panel.frame.size, screen: screen))
        if !panel.isVisible { panel.orderFrontRegardless() }
    }

    /// System Settings' frontmost window, from the window list. Bounds and
    /// owner need no Screen Recording grant; window titles would.
    func settingsWindowFrame() -> CGRect? {
        guard let app = NSRunningApplication.runningApplications(withBundleIdentifier: "com.apple.systempreferences").first,
              let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID)
                as? [[String: Any]],
              let primary = NSScreen.screens.first?.frame.height else { return nil }
        for entry in list where (entry[kCGWindowOwnerPID as String] as? pid_t) == app.processIdentifier
            && (entry[kCGWindowLayer as String] as? Int) == 0 {
            guard let bounds = entry[kCGWindowBounds as String] as? NSDictionary,
                  let rect = CGRect(dictionaryRepresentation: bounds), rect.width > 200 else { continue }
            return cocoaRect(fromWindowList: rect, primaryHeight: primary)
        }
        return nil
    }

    func endWatch(granted: Bool) {
        watchTimer?.invalidate(); watchTimer = nil
        let finished = watch; watch = nil
        if granted {
            panel.granted()
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.4) { [weak self] in
                guard let self, self.watch == nil else { return }
                self.placeTimer?.invalidate(); self.panel.orderOut(nil)
            }
        } else if let finished {
            placeTimer?.invalidate(); panel.orderOut(nil)
            status.stringValue = "\(finished.permission.title) 권한이 아직 꺼져 있어요. 켠 뒤 이 창으로 돌아오면 다시 확인합니다."
        }
        render()
        if granted, finished != nil, state.ready {
            status.stringValue = "✓ 두 권한이 모두 켜졌어요. 이제 에이전트에 연결하세요."
        }
    }

    // MARK: reading the helper's receipt

    /// One read-only receipt from the helper's own LaunchServices launch.
    /// Never asks a shared interpreter, and never two checks at once.
    func check() {
        guard !checkInFlight, FileManager.default.fileExists(atPath: helper.path) else { return }
        checkInFlight = true
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("tacto-setup-" + UUID().uuidString)
        do { try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false,
                                                     attributes: [.posixPermissions: 0o700]) }
        catch { checkInFlight = false; return }
        reportDirectory = directory
        let file = directory.appendingPathComponent("permission.json")
        let config = NSWorkspace.OpenConfiguration()
        config.activates = false; config.createsNewApplicationInstance = true
        config.arguments = ["--screen-capture-preflight", file.path]
        let deadline = Date().addingTimeInterval(10)
        NSWorkspace.shared.openApplication(at: helper, configuration: config) { _, error in
            if error != nil { DispatchQueue.main.async { self.finishCheck(nil, directory) } }
        }
        pollTimer = Timer.scheduledTimer(withTimeInterval: 0.15, repeats: true) { [weak self] _ in
            guard let self, self.reportDirectory == directory else { return }
            if let data = try? Data(contentsOf: file), !data.isEmpty { self.finishCheck(data, directory) }
            else if Date() >= deadline { self.finishCheck(nil, directory) }
        }
    }

    func finishCheck(_ data: Data?, _ directory: URL) {
        guard reportDirectory == directory else { return }
        pollTimer?.invalidate(); pollTimer = nil
        try? FileManager.default.removeItem(at: directory); reportDirectory = nil
        checkInFlight = false
        var next = state
        if let data, next.receive(data, expected: "ai.callabo.nlc.helper") {
            state = next; known = true
        } else if !known {
            status.stringValue = "권한 상태를 확인하지 못했어요. ‘켜기’로 진행하면 다시 확인합니다."
        }
        if let watching = watch, state.granted(watching.permission) { endWatch(granted: true); return }
        render()
    }

    // MARK: connecting the agent

    @objc func connect() {
        guard state.ready else { return }
        connectButton.isEnabled = false; message.stringValue = "명령과 스킬을 연결하고 있어요…"
        let script = root.appendingPathComponent("skill/install.sh")
        DispatchQueue.global().async {
            let process = Process(); process.executableURL = URL(fileURLWithPath: "/bin/bash")
            process.arguments = [script.path]
            process.standardOutput = FileHandle.nullDevice; process.standardError = FileHandle.nullDevice
            var ok = false
            do { try process.run(); process.waitUntilExit(); ok = process.terminationStatus == 0 } catch {}
            DispatchQueue.main.async {
                self.connectButton.isEnabled = true
                self.message.stringValue = ok
                    ? "✓ 연결했어요. 에이전트에서 Tacto 스킬을 새로 불러오면 바로 쓸 수 있어요."
                    : "연결을 끝내지 못했어요. 기존 설치 경로나 파일 충돌을 확인해주세요."
            }
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationWillTerminate(_ notification: Notification) {
        if let directory = reportDirectory { try? FileManager.default.removeItem(at: directory) }
    }

    // MARK: offscreen snapshots (design review; launches nothing, shows nothing)

    func snapshotRequest() -> (URL, SetupState, Permission?)? {
        let args = CommandLine.arguments
        guard let i = args.firstIndex(of: "--snapshot"), i + 1 < args.count else { return nil }
        var state = SetupState()
        let grants = args.firstIndex(of: "--granted").map { $0 + 1 < args.count ? args[$0 + 1] : "" } ?? ""
        state.accessibility = grants.contains("control"); state.screen = grants.contains("screen")
        let waiting = args.firstIndex(of: "--waiting").flatMap { $0 + 1 < args.count ? args[$0 + 1] : nil }
        return (URL(fileURLWithPath: args[i + 1]), state, waiting == "screen" ? .screen : waiting == "control" ? .control : nil)
    }

    func renderSnapshot(_ request: (URL, SetupState, Permission?)) {
        let (directory, snapshotState, waiting) = request
        helper = URL(fileURLWithPath: "/System/Applications/Utilities/Terminal.app")   // icon only
        root = URL(fileURLWithPath: "/nonexistent")
        build()
        state = snapshotState; known = true
        if let waiting { watch = PermissionWatch(permission: waiting, started: Date()); panel.show(for: waiting) }
        render()
        func save(_ view: NSView, _ name: String) {
            view.layoutSubtreeIfNeeded()
            let size = view.fittingSize
            view.setFrameSize(NSSize(width: max(size.width, view.frame.width), height: max(size.height, 10)))
            view.layoutSubtreeIfNeeded()
            guard let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return }
            view.cacheDisplay(in: view.bounds, to: rep)
            try? rep.representation(using: .png, properties: [:])?.write(to: directory.appendingPathComponent(name))
        }
        save(window.contentView!, "setup.png")
        save(panel.contentView!, "panel.png")
        NSApp.terminate(nil)
    }
}

let app = NSApplication.shared
let delegate = Setup(); app.delegate = delegate
app.setActivationPolicy(CommandLine.arguments.contains("--snapshot") ? .prohibited : .regular)
app.run()
