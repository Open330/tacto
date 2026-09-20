// Owned foreground/caret sentinel. Launch never activates or makes a window key.
// No input synthesis, event taps, or observation of other applications.
import AppKit
import Foundation

@MainActor
final class SentinelTextView: NSTextView {
    var keyEventCount = 0
    var onStateChange: (() -> Void)?

    override func keyDown(with event: NSEvent) {
        keyEventCount += 1
        super.keyDown(with: event)
        onStateChange?()
    }

    override func keyUp(with event: NSEvent) {
        keyEventCount += 1
        super.keyUp(with: event)
        onStateChange?()
    }
}

@MainActor
final class SentinelDelegate: NSObject, NSApplicationDelegate, NSTextViewDelegate {
    private var window: NSWindow?
    private var editor: SentinelTextView?
    private let caret = NSTextField(labelWithString: "location=4 length=0")
    private let keyCount = NSTextField(labelWithString: "0")
    private let state = NSTextField(labelWithString: "starting")
    private let lifetime: TimeInterval

    init(lifetime: TimeInterval) {
        self.lifetime = lifetime
        super.init()
    }

    private func identify(_ view: NSView, _ identifier: String) {
        view.identifier = NSUserInterfaceItemIdentifier(identifier)
        view.setAccessibilityIdentifier(identifier)
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let window = NSWindow(contentRect: NSRect(x: 120, y: 120, width: 580, height: 310),
                              styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "NLC Native Input Sentinel"
        window.isReleasedWhenClosed = false
        self.window = window
        let scroll = NSScrollView(frame: NSRect(x: 20, y: 110, width: 540, height: 175))
        scroll.hasVerticalScroller = true
        scroll.borderType = .bezelBorder
        let editor = SentinelTextView(frame: scroll.contentView.bounds)
        editor.isEditable = true
        editor.isSelectable = true
        editor.isRichText = false
        editor.font = NSFont.monospacedSystemFont(ofSize: 16, weight: .regular)
        editor.string = "NLC sentinel 한글 😀\nHarmless text for caret preservation."
        editor.setSelectedRange(NSRange(location: 4, length: 0))
        identify(editor, "nlc-sentinel-text")
        editor.delegate = self
        editor.onStateChange = { [weak self] in self?.refreshState() }
        scroll.documentView = editor
        self.editor = editor
        window.contentView?.addSubview(scroll)

        identify(caret, "nlc-sentinel-caret")
        identify(keyCount, "nlc-sentinel-key-count")
        identify(state, "nlc-sentinel-state")
        caret.frame = NSRect(x: 20, y: 76, width: 540, height: 24)
        keyCount.frame = NSRect(x: 20, y: 46, width: 540, height: 24)
        state.frame = NSRect(x: 20, y: 16, width: 540, height: 24)
        for label in [caret, keyCount, state] { window.contentView?.addSubview(label) }
        // The person must establish foreground focus themselves. The editor
        // keeps normal NSTextView responder and AXSelectedTextRange behavior.
        window.orderBack(nil)
        refreshState()
        let refresh = Timer(timeInterval: 0.1, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.refreshState() }
        }
        let terminate = Timer(timeInterval: lifetime, repeats: false) { _ in
            NSApplication.shared.terminate(nil)
        }
        for timer in [refresh, terminate] {
            RunLoop.main.add(timer, forMode: .common)
            RunLoop.main.add(timer, forMode: .eventTracking)
        }
    }

    func textViewDidChangeSelection(_ notification: Notification) { refreshState() }
    func textDidChange(_ notification: Notification) { refreshState() }

    private func refreshState() {
        guard let editor, let window else { return }
        let range = editor.selectedRange()
        caret.stringValue = "location=\(range.location) length=\(range.length)"
        keyCount.stringValue = String(editor.keyEventCount)
        state.stringValue = "isActive=\(NSApplication.shared.isActive) "
            + "isKeyWindow=\(window.isKeyWindow) editorFirstResponder=\(window.firstResponder === editor)"
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

@main
enum NativeInputSentinel {
    @MainActor static func main() {
        let args = Array(CommandLine.arguments.dropFirst())
        var lifetime: TimeInterval = 90
        if !args.isEmpty {
            guard args.count == 2, args[0] == "--timeout", let seconds = Double(args[1]),
                  seconds.isFinite, seconds >= 1, seconds <= 300 else {
                fputs("usage: NativeInputSentinel [--timeout seconds (1...300)]\n", stderr)
                exit(2)
            }
            lifetime = seconds
        }
        DispatchQueue.global().asyncAfter(deadline: .now() + lifetime + 5) { exit(0) }
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)
        let delegate = SentinelDelegate(lifetime: lifetime)
        app.delegate = delegate
        withExtendedLifetime(delegate) { app.run() }
    }
}
