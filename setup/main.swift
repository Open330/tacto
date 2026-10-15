import AppKit

final class HelperIcon: NSImageView, NSDraggingSource {
    var helperURL: URL?
    override func mouseDown(with event: NSEvent) {
        guard let url = helperURL else { return }
        let item = NSDraggingItem(pasteboardWriter: url as NSURL)
        item.setDraggingFrame(bounds, contents: image)
        beginDraggingSession(with: [item], event: event, source: self)
    }
    func draggingSession(_ session: NSDraggingSession, sourceOperationMaskFor context: NSDraggingContext) -> NSDragOperation { .copy }
}
final class Setup: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    var state = SetupState()
    var root: URL!
    var helper: URL!
    let status = NSTextField(wrappingLabelWithString: "권한을 확인하면 다음 단계가 표시됩니다.")
    let screenStatus = NSTextField(labelWithString: "화면 기록: 조작 표시를 위해 필요합니다")
    let message = NSTextField(wrappingLabelWithString: "앱 조작에는 손쉬운 사용과 화면 기록 권한이 필요합니다.")
    var checkButton: NSButton!
    var connectButton: NSButton!
    var timer: Timer?
    var reportDirectory: URL?

    func applicationDidFinishLaunching(_ notification: Notification) {
        guard let config = Bundle.main.url(forResource: "runtime", withExtension: "json"),
              let data = try? Data(contentsOf: config),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: String],
              let path = object["root"] else { NSApp.terminate(nil); return }
        root = URL(fileURLWithPath: path)
        helper = root.appendingPathComponent("helper/NLC Helper.app")
        window = NSWindow(contentRect: NSRect(x:0,y:0,width:640,height:620),
                          styleMask:[.titled,.closable,.miniaturizable], backing:.buffered, defer:false)
        window.title = "Tacto 시작하기 · 개발용 미리보기"
        window.center()
        let stack = NSStackView(); stack.orientation = .vertical; stack.alignment = .leading; stack.spacing = 16
        stack.translatesAutoresizingMaskIntoConstraints = false
        window.contentView!.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo:window.contentView!.leadingAnchor,constant:28),
            stack.trailingAnchor.constraint(equalTo:window.contentView!.trailingAnchor,constant:-28),
            stack.topAnchor.constraint(equalTo:window.contentView!.topAnchor,constant:28)
        ])
        func label(_ text: String, size: CGFloat) -> NSTextField {
            let field = NSTextField(wrappingLabelWithString:text)
            field.font = .systemFont(ofSize:size)
            field.widthAnchor.constraint(equalToConstant:584).isActive = true
            return field
        }
        stack.addArrangedSubview(label("Tacto를 사용할 준비",size:26))
        stack.addArrangedSubview(label("1. 아래 도우미를 권한 목록에 추가하고 켜주세요.",size:16))
        let icon = HelperIcon(); icon.helperURL = helper
        icon.image = NSWorkspace.shared.icon(forFile:helper.path)
        icon.imageScaling = .scaleProportionallyUpOrDown
        icon.widthAnchor.constraint(equalToConstant:64).isActive = true
        icon.heightAnchor.constraint(equalToConstant:64).isActive = true
        // A separate label avoids imposing the vertical stack's text width on a row.
        let dragText = NSTextField(wrappingLabelWithString:"NLC Helper\n이 아이콘을 권한 목록으로 드래그하세요.")
        dragText.widthAnchor.constraint(equalToConstant:470).isActive = true
        let row = NSStackView(views:[icon,dragText]); row.orientation = .horizontal; row.spacing = 18
        stack.addArrangedSubview(row)
        stack.addArrangedSubview(label("드래그가 안 되면 설정의 + 버튼으로 같은 도우미를 추가하세요.\n'Finder에서 보기'로 파일을 찾을 수 있습니다.",size:13))
        let actions = NSStackView(); actions.orientation = .horizontal; actions.spacing = 10
        actions.addArrangedSubview(button("권한 설정 열기",#selector(accessibilitySettings)))
        actions.addArrangedSubview(button("Finder에서 보기",#selector(revealHelper)))
        checkButton = button("다시 확인",#selector(checkPermissions)); actions.addArrangedSubview(checkButton)
        stack.addArrangedSubview(actions)
        status.widthAnchor.constraint(equalToConstant:584).isActive = true; stack.addArrangedSubview(status)
        stack.addArrangedSubview(label("2. 사용할 에이전트에 연결하세요.",size:16))
        connectButton = button("Tacto 명령·스킬 연결",#selector(connect)); connectButton.isEnabled = false
        stack.addArrangedSubview(connectButton)
        stack.addArrangedSubview(screenStatus)
        stack.addArrangedSubview(button("화면 기록 설정 열기",#selector(screenSettings)))
        message.widthAnchor.constraint(equalToConstant:584).isActive = true; stack.addArrangedSubview(message)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps:true)
        // No permission request, settings opening, installation or helper launch
        // occurs automatically. Every such action requires its own button click.
    }
    func button(_ text:String,_ selector:Selector) -> NSButton {
        let button = NSButton(title:text,target:self,action:selector); button.bezelStyle = .rounded; return button
    }
    @objc func accessibilitySettings() {
        NSWorkspace.shared.open(URL(string:"x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")!)
    }
    @objc func screenSettings() {
        NSWorkspace.shared.open(URL(string:"x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture")!)
    }
    @objc func revealHelper() { NSWorkspace.shared.activateFileViewerSelecting([helper]) }
    func clearReport() {
        timer?.invalidate(); timer = nil
        if let directory = reportDirectory { try? FileManager.default.removeItem(at:directory) }
        reportDirectory = nil
    }
    @objc func checkPermissions() {
        clearReport(); state = SetupState(); connectButton.isEnabled = false
        guard FileManager.default.fileExists(atPath:helper.path) else {
            status.stringValue = "NLC Helper를 찾지 못했습니다. 현재 Tacto 설치 경로를 확인해주세요."; return
        }
        // Use the helper's own LaunchServices receipt. Never ask a shared Python
        // interpreter for its grant or run doctor --check's current UI side effect.
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("nlc-setup-"+UUID().uuidString)
        do { try FileManager.default.createDirectory(at:directory,withIntermediateDirectories:false,attributes:[.posixPermissions:0o700]) }
        catch { status.stringValue = "권한 상태 확인을 준비하지 못했습니다."; return }
        reportDirectory = directory
        let file = directory.appendingPathComponent("permission.json")
        let config = NSWorkspace.OpenConfiguration(); config.activates = false; config.createsNewApplicationInstance = true
        config.arguments = ["--screen-capture-preflight",file.path]
        status.stringValue = "NLC Helper의 권한을 확인하고 있습니다…"; checkButton.isEnabled = false
        let deadline = Date().addingTimeInterval(10)
        NSWorkspace.shared.openApplication(at:helper,configuration:config) { _, error in
            if error != nil { DispatchQueue.main.async {
                guard self.reportDirectory == directory else { return }
                self.finishCheck(nil)
            } }
        }
        timer = Timer.scheduledTimer(withTimeInterval:0.15,repeats:true) { _ in
            guard self.reportDirectory == directory else { return }
            if let data = try? Data(contentsOf:file) { self.finishCheck(data) }
            else if Date() >= deadline { self.finishCheck(nil) }
        }
    }
    func finishCheck(_ data:Data?) {
        clearReport(); checkButton.isEnabled = true
        guard let data = data, state.receive(data,expected:"ai.callabo.nlc.helper") else {
            status.stringValue = "권한 상태를 확인하지 못했습니다. 다시 확인해주세요."; return
        }
        status.stringValue = state.accessibility ? "✓ 앱 조작 권한이 확인됐습니다." : "앱 조작 권한을 켠 뒤 '다시 확인'을 눌러주세요."
        screenStatus.stringValue = state.screen ? "✓ 화면 기록 권한도 확인됐습니다." : "화면 기록 권한을 켠 뒤 다시 확인해주세요."
        connectButton.isEnabled = state.ready
    }
    @objc func connect() {
        guard state.ready else { return }
        connectButton.isEnabled = false; message.stringValue = "명령과 스킬을 연결하고 있습니다…"
        let script = root.appendingPathComponent("skill/install.sh")
        DispatchQueue.global().async {
            let process = Process(); process.executableURL = URL(fileURLWithPath:"/bin/bash")
            process.arguments = [script.path]; process.standardOutput = FileHandle.nullDevice; process.standardError = FileHandle.nullDevice
            var ok = false
            do { try process.run(); process.waitUntilExit(); ok = process.terminationStatus == 0 } catch {}
            DispatchQueue.main.async {
                self.connectButton.isEnabled = true
                self.message.stringValue = ok ? "연결했습니다. 에이전트에서 Tacto 스킬을 새로 불러와주세요." : "연결을 완료하지 못했습니다. 기존 설치 경로와 파일 충돌을 확인해주세요."
            }
        }
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender:NSApplication)->Bool { true }
    func applicationWillTerminate(_ notification:Notification) { clearReport() }
}
let app = NSApplication.shared
let delegate = Setup(); app.delegate = delegate; app.setActivationPolicy(.regular); app.run()
