// Owned navigation control: two windows, two focusable controls per window.
// Launch does not activate the app or make a window key. Independent labels
// expose the receiving window and real first responder, never another app.
import AppKit

@MainActor
final class NavigationField: NSView {
    let name: String
    var position = 0
    init(name: String, frame: NSRect) {
        self.name = name
        super.init(frame: frame)
        setAccessibilityElement(true)
        setAccessibilityRole(.textArea)
        setAccessibilityIdentifier(name)
        setAccessibilityValue("position=0")
    }
    required init?(coder: NSCoder) { fatalError() }
    override var acceptsFirstResponder: Bool { true }
    override func isAccessibilityFocused() -> Bool { window?.firstResponder === self }
    override func keyDown(with event: NSEvent) {
        switch event.keyCode {
        case 48:
            if event.modifierFlags.contains(.shift) { window?.selectPreviousKeyView(self) }
            else { window?.selectNextKeyView(self) }
        case 123: position -= 1
        case 124: position += 1
        case 125: position -= 10
        case 126: position += 10
        default: break
        }
        setAccessibilityValue("position=\(position)")
        needsDisplay = true
    }
    override func keyUp(with event: NSEvent) {}
    override func draw(_ dirtyRect: NSRect) {
        NSColor.windowBackgroundColor.setFill(); bounds.fill()
        ("\(name): position=\(position)" as NSString).draw(at: NSPoint(x: 8, y: 8),
            withAttributes: [.font: NSFont.monospacedSystemFont(ofSize: 15, weight: .regular)])
    }
}

@MainActor
final class NavigationWindow: NSWindow {
    var downs = 0, ups = 0, routeErrors = 0
    override func sendEvent(_ event: NSEvent) {
        if event.type == .keyDown || event.type == .keyUp {
            if event.windowNumber != windowNumber { routeErrors += 1 }
            if event.type == .keyDown { downs += 1 } else { ups += 1 }
        }
        super.sendEvent(event)
    }
}

@MainActor
final class NavigationDelegate: NSObject, NSApplicationDelegate {
    var windows: [NavigationWindow] = []
    var labels: [NSTextField] = []
    func applicationDidFinishLaunching(_ notification: Notification) {
        for (number, suffix) in ["A", "B"].enumerated() {
            let window = NavigationWindow(contentRect: NSRect(x: 170 + number * 460, y: 200,
                width: 420, height: 230), styleMask: [.titled, .closable], backing: .buffered, defer: false)
            window.title = "TACTO Navigation Fixture \(suffix)"
            window.isReleasedWhenClosed = false
            let first = NavigationField(name: "nav-\(suffix)-first", frame: NSRect(x: 20, y: 130, width: 380, height: 50))
            let second = NavigationField(name: "nav-\(suffix)-second", frame: NSRect(x: 20, y: 65, width: 380, height: 50))
            first.nextKeyView = second; second.nextKeyView = first
            window.contentView?.addSubview(first); window.contentView?.addSubview(second)
            let label = NSTextField(labelWithString: "starting")
            label.setAccessibilityIdentifier("nav-\(suffix)-state")
            label.frame = NSRect(x: 20, y: 12, width: 380, height: 42)
            window.contentView?.addSubview(label)
            window.makeFirstResponder(first)
            window.orderBack(nil)
            windows.append(window); labels.append(label)
        }
        refresh()
        Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.refresh() }
        }
        Timer.scheduledTimer(withTimeInterval: 120, repeats: false) { _ in NSApplication.shared.terminate(nil) }
    }
    func refresh() {
        for (window, label) in zip(windows, labels) {
            label.stringValue = "downs=\(window.downs) ups=\(window.ups) routeErrors=\(window.routeErrors) "
                + "key=\(window.isKeyWindow) focus=\((window.firstResponder as? NavigationField)?.name ?? "other")"
        }
    }
}

@main enum NativeNavigationFixture {
    @MainActor static func main() {
        DispatchQueue.global().asyncAfter(deadline: .now() + 125) { exit(0) }
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)
        let delegate = NavigationDelegate()
        app.delegate = delegate
        withExtendedLifetime(delegate) { app.run() }
    }
}
