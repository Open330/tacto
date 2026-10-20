// A user-operated foreground typing witness. No other app/document is read.
// No explicit activation/key-window call. Launch effects remain unmeasured.
import AppKit
import CryptoKit

@MainActor
final class TypingWitnessWindow: NSWindow {
    var keyDowns = 0
    var keyUps = 0
    var wrongWindowEvents = 0
    override func sendEvent(_ event: NSEvent) {
        if event.type == .keyDown || event.type == .keyUp {
            if event.windowNumber != windowNumber { wrongWindowEvents += 1 }
            if event.type == .keyDown { keyDowns += 1 } else { keyUps += 1 }
        }
        super.sendEvent(event)
    }
}

@MainActor
final class TypingWitnessDelegate: NSObject, NSApplicationDelegate {
    var window: TypingWitnessWindow?
    var editor: NSTextView?
    var state: NSTextField?
    func applicationDidFinishLaunching(_ notification: Notification) {
        let window = TypingWitnessWindow(contentRect: NSRect(x: 160, y: 180, width: 620, height: 330),
            styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "TACTO User Typing Witness — test text only"
        window.isReleasedWhenClosed = false
        let help = NSTextField(wrappingLabelWithString:
            "사용자가 이 창을 선택하고 시험용 텍스트만 입력하세요. 다른 앱이나 문서는 읽지 않습니다. "
            + "이 창에서 관측하는 것은 입력 횟수·선택 범위·텍스트 hash이며 실제 사용자 입력을 자동 생성하지 않습니다.")
        help.frame = NSRect(x: 18, y: 257, width: 584, height: 55)
        window.contentView?.addSubview(help)
        let scroll = NSScrollView(frame: NSRect(x: 18, y: 80, width: 584, height: 165))
        scroll.hasVerticalScroller = true
        scroll.borderType = .bezelBorder
        let editor = NSTextView(frame: NSRect(x: 0, y: 0, width: 580, height: 165))
        editor.isRichText = false
        editor.isAutomaticQuoteSubstitutionEnabled = false
        editor.isAutomaticDashSubstitutionEnabled = false
        editor.isAutomaticTextReplacementEnabled = false
        editor.isAutomaticSpellingCorrectionEnabled = false
        editor.isAutomaticTextCompletionEnabled = false
        editor.font = NSFont.monospacedSystemFont(ofSize: 16, weight: .regular)
        editor.setAccessibilityIdentifier("typing-witness-editor")
        scroll.documentView = editor
        window.contentView?.addSubview(scroll)
        let state = NSTextField(wrappingLabelWithString: "starting")
        state.setAccessibilityIdentifier("typing-witness-state")
        state.frame = NSRect(x: 18, y: 12, width: 584, height: 55)
        window.contentView?.addSubview(state)
        window.makeFirstResponder(editor)
        window.orderBack(nil)
        self.window = window; self.editor = editor; self.state = state
        refresh()
        Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.refresh() }
        }
        Timer.scheduledTimer(withTimeInterval: 3600, repeats: false) { _ in NSApplication.shared.terminate(nil) }
    }
    func refresh() {
        guard let window, let editor, let state else { return }
        let range = editor.selectedRange()
        let hash = SHA256.hash(data: Data(editor.string.utf8)).map { String(format: "%02x", $0) }.joined()
        state.stringValue = "downs=\(window.keyDowns) ups=\(window.keyUps) wrongWindow=\(window.wrongWindowEvents) "
            + "utf16=\(editor.string.utf16.count) selection=\(range.location),\(range.length) key=\(window.isKeyWindow) "
            + "focused=\(window.firstResponder === editor) sha256=\(hash)"
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

@main enum NativeTypingWitnessFixture {
    @MainActor static func main() {
        DispatchQueue.global().asyncAfter(deadline: .now() + 3605) { exit(0) }
        let app = NSApplication.shared
        app.setActivationPolicy(.regular)
        let delegate = TypingWitnessDelegate()
        app.delegate = delegate
        withExtendedLifetime(delegate) { app.run() }
    }
}
