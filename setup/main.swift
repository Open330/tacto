import AppKit

private let contentWidth: CGFloat = 656

/// The helper's icon, draggable as its file URL into a System Settings list.
final class HelperIcon: NSImageView, NSDraggingSource {
    var helperURL: URL?
    override func mouseDown(with event: NSEvent) {
        guard let item = draggingItem() else { return }
        beginDraggingSession(with: [item], event: event, source: self)
    }
    func draggingItem() -> NSDraggingItem? {
        guard let url = helperURL else { return nil }
        let item = NSDraggingItem(pasteboardWriter: url as NSURL)
        item.setDraggingFrame(bounds, contents: image)
        return item
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
        detail = label("", size: 11, color: .secondaryLabelColor, width: 454)
        action = NSButton(title: "켜기", target: target, action: selector)
        super.init(frame: .zero)
        orientation = .horizontal; alignment = .top; spacing = 14
        edgeInsets = NSEdgeInsets(top: 14, left: 16, bottom: 18, right: 16)
        wantsLayer = true
        layer?.cornerRadius = 12
        layer?.borderWidth = 1
        layer?.backgroundColor = NSColor.controlBackgroundColor.cgColor
        status.widthAnchor.constraint(equalToConstant: 24).isActive = true
        let text = NSStackView(views: [
            label(permission.title, size: 15, weight: .semibold, width: 454),
            label(permission == .control ? "버튼을 누르고 글자를 입력합니다." : "작업에 필요한 앱 화면을 확인합니다.", size: 13, color: .secondaryLabelColor, width: 454),
            detail])
        text.orientation = .vertical; text.alignment = .leading; text.spacing = 3
        action.bezelStyle = .rounded
        action.widthAnchor.constraint(equalToConstant: 118).isActive = true
        action.setAccessibilityLabel("\(permission.title) 설정 열기")
        addArrangedSubview(status); addArrangedSubview(text); addArrangedSubview(action)
        widthAnchor.constraint(equalToConstant: contentWidth).isActive = true
    }
    func showApp(_ app: URL) {
        detail.stringValue = "허용할 앱: " + app.lastPathComponent
        detail.toolTip = permission.location
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
            action.title = "설정 열기"; action.isEnabled = true; border = .systemOrange
        case .needed:
            status.image = symbol("circle", .tertiaryLabelColor)
            action.title = "설정 열기"; action.isEnabled = true; border = .separatorColor
        case .unknown:
            status.image = symbol("questionmark.circle", .tertiaryLabelColor)
            action.title = "설정 열기"; action.isEnabled = true; border = .separatorColor
        }
        layer?.borderColor = border.cgColor
        status.setAccessibilityLabel(look == .granted ? "켜짐" : look == .unknown ? "확인되지 않음" : "꺼짐")
    }
}

/// The small window beside System Settings: drag this, flip that switch.
final class AccessoryPanel: NSPanel {
    let heading = label("", size: 15, weight: .semibold, width: 268)
    let state = label("", size: 12, color: .secondaryLabelColor, width: 268)
    let icon = HelperIcon()
    private(set) var app: URL
    let name = label("", size: 14, weight: .semibold, width: 268)
    let identity = label("", size: 12, color: .secondaryLabelColor, width: 268)
    let steps = label("", size: 13, width: 268)
    let dropArrow = NSImageView()
    let dropWhere = label("", size: 12, width: 232)
    var alternative: NSStackView!
    var helpButton: NSButton!
    init(helper: URL) {
        self.app = helper
        super.init(contentRect: NSRect(x: 0, y: 0, width: 300, height: 480),
                   styleMask: [.titled, .closable, .utilityWindow, .nonactivatingPanel],
                   backing: .buffered, defer: false)
        title = "Tacto 권한 도우미"
        level = .floating; hidesOnDeactivate = false; isReleasedWhenClosed = false
        collectionBehavior = [.moveToActiveSpace, .fullScreenAuxiliary]
        icon.helperURL = helper
        icon.image = NSWorkspace.shared.icon(forFile: helper.path)
        icon.imageScaling = .scaleProportionallyUpOrDown
        icon.widthAnchor.constraint(equalToConstant: 80).isActive = true
        icon.heightAnchor.constraint(equalToConstant: 80).isActive = true
        icon.setAccessibilityLabel("Tacto 권한용 앱 NLC Helper. 이 아이콘을 설정 창의 앱 목록으로 끌어다 놓으세요.")
        icon.toolTip = "이 아이콘을 누른 채 설정 창의 앱 목록으로 옮긴 뒤 놓으세요."
        name.alignment = .center
        let source = label("1. 이 앱 아이콘을 끌어오세요", size: 13, weight: .semibold, width: 268)
        source.alignment = .center
        dropArrow.image = symbol("arrow.left", .controlAccentColor, size: 24)
        dropArrow.widthAnchor.constraint(equalToConstant: 24).isActive = true
        dropArrow.heightAnchor.constraint(equalToConstant: 30).isActive = true
        let dropBox = NSStackView(views: [dropArrow, dropWhere])
        dropBox.orientation = .horizontal; dropBox.alignment = .centerY; dropBox.spacing = 8
        dropBox.edgeInsets = NSEdgeInsets(top: 12, left: 10, bottom: 12, right: 10)
        dropBox.wantsLayer = true
        dropBox.layer?.cornerRadius = 10
        dropBox.layer?.backgroundColor = NSColor.controlAccentColor.withAlphaComponent(0.10).cgColor
        helpButton = NSButton(title: "드래그가 안 되나요? 다른 방법 보기", target: nil, action: nil)
        helpButton.target = self; helpButton.action = #selector(toggleHelp)
        helpButton.bezelStyle = .rounded
        let reveal = NSButton(title: "Finder에서 이 앱 보기", target: nil, action: nil)
        reveal.target = self; reveal.action = #selector(revealHelper)
        reveal.bezelStyle = .rounded
        let copy = NSButton(title: "이 앱 위치 복사", target: nil, action: nil)
        copy.target = self; copy.action = #selector(copyHelperPath)
        copy.bezelStyle = .rounded
        let fallback = label("Finder에서 선택된 앱을 끌어다 놓아도 됩니다.\n+ 버튼을 쓸 때는 ‘이 앱 위치 복사’를 누르고, 파일 선택 창에서 Shift + Command + G를 누른 다음 Command + V → Return으로 이동해 ‘열기’를 누르세요.",
                             size: 12, color: .secondaryLabelColor, width: 268)
        let approval = label("암호나 Touch ID를 요청하면 Mac 로그인에 쓰는 방법으로 승인하세요. macOS가 앱 재시작을 요청하면 안내를 따라 주세요.", size: 11, color: .secondaryLabelColor, width: 268)
        heading.alignment = .center; state.alignment = .center
        alternative = NSStackView(views: [reveal, copy, fallback])
        alternative.orientation = .vertical; alternative.alignment = .centerX; alternative.spacing = 8
        alternative.isHidden = true
        let stack = NSStackView(views: [heading, source, icon, name, identity, dropBox, steps, helpButton, alternative, approval, state])
        stack.orientation = .vertical; stack.alignment = .centerX; stack.spacing = 10
        stack.edgeInsets = NSEdgeInsets(top: 16, left: 16, bottom: 16, right: 16)
        contentView = stack
        stack.layoutSubtreeIfNeeded()
        setContentSize(stack.fittingSize)
    }
    @objc func toggleHelp() {
        alternative.isHidden.toggle()
        helpButton.title = alternative.isHidden ? "드래그가 안 되나요? 다른 방법 보기" : "다른 방법 접기"
        fitContent()
    }
    func fitContent() {
        contentView?.layoutSubtreeIfNeeded()
        if let view = contentView { setContentSize(view.fittingSize) }
    }
    @objc func revealHelper() {
        NSWorkspace.shared.activateFileViewerSelecting([app])
    }
    @objc func copyHelperPath() {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(app.path, forType: .string)
        state.stringValue = "위치를 복사했어요. + 버튼의 파일 선택 창에서 사용하세요."
        fitContent()
    }
    override var canBecomeKey: Bool { true }
    func pointTowardSettings(_ frame: CGRect) {
        let direction = frame.midX < self.frame.midX ? "arrow.left" : "arrow.right"
        dropArrow.image = symbol(direction, .controlAccentColor, size: 24)
        dropArrow.setAccessibilityLabel("옆의 시스템 설정 창에 있는 앱 목록으로 옮기세요")
    }
    func show(for permission: Permission, app: URL) {
        self.app = app
        icon.helperURL = app
        icon.image = NSWorkspace.shared.icon(forFile: app.path)
        icon.setAccessibilityLabel("\(app.lastPathComponent). 이 아이콘을 옆 시스템 설정 창의 앱 목록으로 끌어다 놓으세요.")
        name.stringValue = app.lastPathComponent
        identity.stringValue = "이 단계에서 허용할 앱입니다. 목록에 같은 앱이 있으면 스위치만 켜세요."
        dropWhere.stringValue = permission == .screen
            ? "2. 옆의 ‘시스템 설정’ 창\n위쪽 ‘화면 및 시스템 오디오 기록’의 앱 목록 안에 놓으세요."
            : "2. 옆의 ‘시스템 설정’ 창\n‘손쉬운 사용 / Device Control and Data Access’의 앱 목록 안에 놓으세요."
        steps.stringValue = "앱 이름과 스위치가 줄지어 있는 목록의 빈 곳이나 행 사이에 놓으세요.\n3. ‘\(app.deletingPathExtension().lastPathComponent)’ 옆 스위치를 켜세요."
        heading.stringValue = "\(permission == .control ? "1 / 2" : "2 / 2") · \(permission.title) 권한 켜기"
        state.stringValue = "스위치가 켜지면 자동으로 확인합니다."
        alternative.isHidden = true
        helpButton.title = "드래그가 안 되나요? 다른 방법 보기"
        fitContent()
        state.textColor = .secondaryLabelColor
    }
    func granted() {
        state.stringValue = "켜졌어요. 이 창은 곧 닫힙니다."
        state.textColor = .systemGreen
    }
}

final class Setup: NSObject, NSApplicationDelegate, NSWindowDelegate {
    var window: NSWindow!
    var panel: AccessoryPanel!
    var rows: [PermissionRow] = []
    var state = SetupState()
    var notice: String?
    var known = false                        // has a receipt ever been read?
    var root: URL!
    var helper: URL!
    var bundledRuntime = false
    var setupApp = Bundle.main.bundleURL
    func appFor(_ permission: Permission) -> URL {
        permissionApp(permission, helper: helper, setupApp: setupApp, bundledRuntime: bundledRuntime)
    }
    let status = label("", size: 12, color: .secondaryLabelColor, width: contentWidth)
    let message = label("", size: 12, color: .secondaryLabelColor, width: contentWidth - 32)
    let badge = label("확인 중", size: 12, weight: .semibold)
    var statusItem: NSStatusItem?
    var statusMenuTitle: NSMenuItem?
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
        // A link into a mounted installer disappears when that image is ejected.
        // Require a writable installed copy before permission or skill setup.
        if (try? Bundle.main.bundleURL.resourceValues(forKeys: [.volumeIsReadOnlyKey]))?.volumeIsReadOnly == true {
            let alert = NSAlert()
            alert.messageText = "먼저 Tacto를 Applications로 옮겨 주세요"
            alert.informativeText = "설치 이미지 안에서는 설정을 진행하지 않습니다. Tacto.app을 Applications 폴더로 끌어다 놓은 뒤, 옮긴 앱을 다시 열어 주세요."
            alert.addButton(withTitle: "Applications 폴더 열기")
            alert.addButton(withTitle: "닫기")
            if alert.runModal() == .alertFirstButtonReturn {
                NSWorkspace.shared.open(URL(fileURLWithPath: "/Applications", isDirectory: true))
            }
            NSApp.terminate(nil); return
        }
        guard let config = Bundle.main.url(forResource: "runtime", withExtension: "json"),
              let data = try? Data(contentsOf: config),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: String],
              let resources = Bundle.main.resourceURL,
              let runtime = setupRuntimeRoot(object, resources: resources) else {
            let alert = NSAlert()
            alert.messageText = "Tacto를 시작할 수 없어요"
            alert.informativeText = "앱에 필요한 파일을 찾지 못했습니다. 설치 이미지에서 Tacto.app 전체를 Applications 폴더로 다시 옮긴 뒤 열어 주세요."
            alert.addButton(withTitle: "닫기"); alert.runModal()
            NSApp.terminate(nil); return
        }
        root = runtime
        bundledRuntime = object["bundledRoot"] == "runtime"
        helper = root.appendingPathComponent("helper/NLC Helper.app")
        build()
        installMenus()
        window.center(); window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        // Reading the helper's receipt requests nothing and changes nothing;
        // it is what lets every row start out true. Settings, permission
        // requests and installation still each wait for their own click.
        check()
    }

    func applicationDidBecomeActive(_ notification: Notification) {
        // Back from System Settings: say what changed without being asked.
        if helper != nil { check() }
    }

    func build() {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 720, height: 640),
                          styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
        window.title = "Tacto"
        window.delegate = self
        window.isReleasedWhenClosed = false
        window.standardWindowButton(.closeButton)?.setAccessibilityIdentifier("tacto.close")
        window.titlebarAppearsTransparent = true
        window.backgroundColor = .windowBackgroundColor
        panel = AccessoryPanel(helper: helper)
        let stack = NSStackView(); stack.orientation = .vertical; stack.alignment = .leading; stack.spacing = 12
        stack.edgeInsets = NSEdgeInsets(top: 24, left: 32, bottom: 24, right: 32)
        stack.wantsLayer = true
        stack.layer?.backgroundColor = NSColor.windowBackgroundColor.cgColor
        window.contentView = stack
        let brand = label("Tacto", size: 28, weight: .bold)
        let spacer = NSView()
        let header = NSStackView(views: [brand, spacer, badge])
        header.orientation = .horizontal; header.alignment = .centerY
        header.widthAnchor.constraint(equalToConstant: contentWidth).isActive = true
        stack.addArrangedSubview(header)
        stack.addArrangedSubview(label("에이전트가 여러분 대신 Mac 앱을 사용할 수 있도록.",
                                       size: 14, color: .secondaryLabelColor, width: contentWidth))
        stack.setCustomSpacing(22, after: stack.arrangedSubviews.last!)
        stack.addArrangedSubview(label("Mac 권한", size: 13, weight: .semibold, width: contentWidth))
        for permission in Permission.allCases {
            let row = PermissionRow(permission, target: self, selector: #selector(turnOn(_:)))
            row.showApp(appFor(permission))
            rows.append(row); stack.addArrangedSubview(row)
        }
        stack.addArrangedSubview(status)
        stack.setCustomSpacing(18, after: status)
        connectButton = NSButton(title: "에이전트에 연결", target: self, action: #selector(connect))
        connectButton.bezelStyle = .rounded; connectButton.keyEquivalent = "\r"; connectButton.isEnabled = false
        let connectionHeader = NSStackView(views: [label("에이전트 연결", size: 15, weight: .semibold), NSView(), connectButton])
        connectionHeader.orientation = .horizontal; connectionHeader.alignment = .centerY
        connectionHeader.widthAnchor.constraint(equalToConstant: contentWidth - 32).isActive = true
        let connection = NSStackView(views: [connectionHeader,
            label("Claude Code · Codex에 명령과 스킬을 설치합니다.\n권한 완료와 에이전트 연결은 별개입니다.", size: 12, color: .secondaryLabelColor, width: contentWidth - 32), message])
        connection.orientation = .vertical; connection.alignment = .leading; connection.spacing = 8
        connection.edgeInsets = NSEdgeInsets(top: 16, left: 16, bottom: 16, right: 16)
        connection.wantsLayer = true; connection.layer?.cornerRadius = 12
        connection.layer?.backgroundColor = NSColor.controlBackgroundColor.cgColor
        stack.addArrangedSubview(connection)
        stack.setCustomSpacing(18, after: connection)
        stack.addArrangedSubview(label("도우미는 작업할 때 자동 실행됩니다", size: 13, weight: .semibold, width: contentWidth))
        stack.addArrangedSubview(label("별도 helper를 만들거나 실행할 필요가 없습니다. 창을 닫으면 메뉴 막대에서 다시 열 수 있어요. Tacto를 종료해도 설치된 에이전트 명령은 도우미를 자동 실행합니다.", size: 12, color: .secondaryLabelColor, width: contentWidth))
        render()
        stack.layoutSubtreeIfNeeded()
        window.setContentSize(stack.fittingSize)
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
        connectButton.isEnabled = known && state.ready
        badge.stringValue = !known ? "권한 확인 중" : state.ready ? "권한 준비 완료" : "권한 설정 필요"
        badge.textColor = known && state.ready ? .systemGreen : .secondaryLabelColor
        statusMenuTitle?.title = "Tacto 설정 · " + badge.stringValue
        statusItem?.button?.toolTip = "Tacto 설정 · " + badge.stringValue
        if let notice { status.stringValue = notice }
        else if state.ready { status.stringValue = "두 권한이 모두 켜져 있습니다. 다시 설정할 필요가 없어요." }
        else if let watching = watch { status.stringValue = "\(watching.permission.title) 권한이 켜지기를 기다리는 중… 켜면 자동으로 넘어갑니다." }
        else if !known { status.stringValue = "권한 상태를 확인하고 있어요…" }
        else { status.stringValue = "‘설정 열기’를 누르면 설정이 열리고, 옆에 작은 도우미 창이 함께 뜹니다." }
    }

    // MARK: turning a permission on

    @objc func turnOn(_ sender: NSButton) {
        guard let row = rows.first(where: { $0.action === sender }) else { return }
        guard FileManager.default.fileExists(atPath: helper.path) else {
            notice = "권한용 앱을 찾지 못했어요. Tacto.app 전체를 Applications 폴더로 다시 옮긴 뒤 열어 주세요."; render(); return
        }
        let permission = row.permission
        notice = nil
        NSWorkspace.shared.open(permission.settingsURL)
        panel.show(for: permission, app: appFor(permission))
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
        panel.pointTowardSettings(settings)
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
            notice = "\(finished.permission.title) 권한 확인 시간이 지났어요. ‘설정 열기’를 눌러 다시 진행하세요."
        }
        render()
        if granted, finished != nil, state.ready {
            status.stringValue = "두 권한이 모두 켜졌어요. 이제 에이전트에 연결하세요."
        }
    }

    // MARK: reading the helper's receipt

    /// One read-only receipt from the helper's own LaunchServices launch.
    /// Never asks a shared interpreter, and never two checks at once.
    func check() {
        guard !checkInFlight else { return }
        guard FileManager.default.fileExists(atPath: helper.path) else {
            known = state.refresh(nil, expected: "ai.callabo.nlc.helper")
            notice = "권한용 앱을 찾지 못했어요. Tacto.app 전체를 Applications 폴더로 다시 옮긴 뒤 열어 주세요."
            render(); return
        }
        checkInFlight = true
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("tacto-setup-" + UUID().uuidString)
        do { try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false,
                                                     attributes: [.posixPermissions: 0o700]) }
        catch {
            checkInFlight = false; known = state.refresh(nil, expected: "ai.callabo.nlc.helper")
            notice = "권한을 확인할 임시 파일을 만들지 못했어요. Mac의 저장 공간을 확인하고 Tacto를 다시 열어 주세요."
            render(); return
        }
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
        known = state.refresh(data, expected: "ai.callabo.nlc.helper")
        notice = known ? nil : "권한 상태를 확인하지 못했어요. ‘설정 열기’를 눌러 다시 확인하세요. 계속 실패하면 Tacto를 닫고 다시 열어 주세요."
        if let watching = watch, state.granted(watching.permission) { endWatch(granted: true); return }
        render()
    }

    // MARK: connecting the agent

    @objc func connect() {
        guard known && state.ready else { return }
        connectButton.isEnabled = false; message.stringValue = "명령과 스킬을 연결하고 있어요…"
        let script = root.appendingPathComponent("skill/install.sh")
        DispatchQueue.global().async {
            let process = Process(); process.executableURL = URL(fileURLWithPath: "/bin/bash")
            process.arguments = [script.path]
            process.standardOutput = FileHandle.nullDevice; process.standardError = FileHandle.nullDevice
            var ok = false
            do { try process.run(); process.waitUntilExit(); ok = process.terminationStatus == 0 } catch {}
            DispatchQueue.main.async {
                self.connectButton.isEnabled = self.known && self.state.ready
                self.message.stringValue = ok
                    ? "연결했어요. Claude Code나 Codex에서 새 대화를 열고 ‘Tacto로 작업해 줘’라고 요청하세요."
                    : "연결을 끝내지 못했어요. 기존 설치 경로나 파일 충돌을 확인해주세요."
            }
        }
    }

    // The GUI is a settings surface. Automation helpers belong to their CLI
    // launcher, never to this window or this menu bar item's lifetime.
    func installMenus() {
        let main = NSMenu()
        let appItem = NSMenuItem(); main.addItem(appItem)
        let appMenu = NSMenu(); appItem.submenu = appMenu
        let open = appMenu.addItem(withTitle: "Tacto 열기", action: #selector(reopen), keyEquivalent: "0")
        open.target = self
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Tacto 종료", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        let windowItem = NSMenuItem(); main.addItem(windowItem)
        let windowMenu = NSMenu(title: "윈도우"); windowItem.submenu = windowMenu
        let close = windowMenu.addItem(withTitle: "창 닫기", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        close.target = window
        NSApp.mainMenu = main
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        item.button?.image = NSImage(systemSymbolName: "slider.horizontal.3", accessibilityDescription: "Tacto 설정")
        item.button?.image?.isTemplate = true
        item.button?.setAccessibilityLabel("Tacto 설정")
        let menu = NSMenu()
        statusMenuTitle = menu.addItem(withTitle: "Tacto 설정 · " + badge.stringValue, action: nil, keyEquivalent: "")
        menu.addItem(.separator())
        let show = menu.addItem(withTitle: "Tacto 열기", action: #selector(reopen), keyEquivalent: "")
        show.target = self
        let refresh = menu.addItem(withTitle: "권한 다시 확인", action: #selector(refreshPermissions), keyEquivalent: "")
        refresh.target = self
        menu.addItem(.separator())
        menu.addItem(withTitle: "Tacto 종료", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "")
        item.menu = menu; statusItem = item
        render()
    }
    @objc func reopen() {
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        check()
    }
    @objc func refreshPermissions() { check() }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        reopen(); return true
    }
    func windowWillClose(_ notification: Notification) {
        guard notification.object as? NSWindow === window else { return }
        watchTimer?.invalidate(); watchTimer = nil; watch = nil
        placeTimer?.invalidate(); placeTimer = nil
        panel.orderOut(nil)
        render()
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
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
        helper = URL(fileURLWithPath: "/Applications/NLC Helper.app")   // offscreen fixture only
        bundledRuntime = CommandLine.arguments.contains("--standalone")
        setupApp = URL(fileURLWithPath: "/Applications/Tacto.app")
        root = URL(fileURLWithPath: "/nonexistent")
        build()
        state = snapshotState; known = true
        let permission = waiting ?? state.nextNeeded ?? .control
        panel.show(for: permission, app: appFor(permission))
        if let waiting { watch = PermissionWatch(permission: waiting, started: Date()) }
        if CommandLine.arguments.contains("--show-help") { panel.toggleHelp() }
        if CommandLine.arguments.contains("--check-failed") {
            known = state.refresh(nil, expected: "ai.callabo.nlc.helper")
            notice = "권한 상태를 확인하지 못했어요. ‘설정 열기’를 눌러 다시 확인하세요."
        }
        render()
        assert(connectButton.isEnabled == (known && state.ready))
        assert(!applicationShouldTerminateAfterLastWindowClosed(NSApp))
        assert(panel.icon.helperURL == appFor(permission))
        assert((panel.icon.draggingItem()?.item as? NSURL) == appFor(permission) as NSURL)
        assert(panel.frame.height > 400)
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
