// Owned opaque canvas: no accessibility input control exists in the painted area.
// Only the independent receiving-view counters are exposed as read-only labels.
import AppKit

@MainActor final class OpaqueCanvas: NSView {
    var downs = 0, ups = 0, greens = 0, errors = 0
    var last = "none"
    override var acceptsFirstResponder: Bool { true }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func isAccessibilityElement() -> Bool { false }
    override func accessibilityChildren() -> [Any]? { [] }
    override func mouseDown(with event: NSEvent) {
        downs += 1
        if event.windowNumber != window?.windowNumber { errors += 1 }
        let point = convert(event.locationInWindow, from: nil)
        last = point.x >= bounds.midX ? "green" : "red"
        if last == "green" { greens += 1 }
        needsDisplay = true
    }
    override func mouseUp(with event: NSEvent) {
        ups += 1
        if event.windowNumber != window?.windowNumber { errors += 1 }
    }
    override func draw(_ dirtyRect: NSRect) {
        NSColor.systemRed.setFill()
        NSRect(x: 0, y: 0, width: bounds.width/2, height: bounds.height).fill()
        NSColor.systemGreen.setFill()
        NSRect(x: bounds.width/2, y: 0, width: bounds.width/2, height: bounds.height).fill()
    }
}

@MainActor final class OpaqueDelegate: NSObject, NSApplicationDelegate {
    var windows: [NSWindow] = [], canvases: [OpaqueCanvas] = [], labels: [NSTextField] = []
    func applicationDidFinishLaunching(_ notification: Notification) {
        for (number, suffix) in ["A", "B"].enumerated() {
            let window = NSWindow(contentRect: NSRect(x: 170 + number*460, y: 200, width: 420, height: 230),
                styleMask: [.titled, .closable], backing: .buffered, defer: false)
            window.title = "NLC Opaque Point Fixture \(suffix)"
            window.isReleasedWhenClosed = false
            let canvas = OpaqueCanvas(frame: NSRect(x: 0, y: 55, width: 420, height: 175))
            let label = NSTextField(labelWithString: "starting")
            label.setAccessibilityIdentifier("opaque-\(suffix)-state")
            label.frame = NSRect(x: 12, y: 10, width: 400, height: 40)
            window.contentView?.addSubview(canvas); window.contentView?.addSubview(label)
            window.orderBack(nil)
            windows.append(window); canvases.append(canvas); labels.append(label)
        }
        refresh()
        Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.refresh() }
        }
        Timer.scheduledTimer(withTimeInterval: 120, repeats: false) { _ in NSApplication.shared.terminate(nil) }
    }
    func refresh() {
        for index in windows.indices {
            let window = windows[index], canvas = canvases[index], label = labels[index]
            label.stringValue = "downs=\(canvas.downs) ups=\(canvas.ups) greens=\(canvas.greens) "
                + "last=\(canvas.last) routeErrors=\(canvas.errors) key=\(window.isKeyWindow)"
        }
    }
}

@main enum OpaquePointFixture {
    @MainActor static func main() {
        DispatchQueue.global().asyncAfter(deadline: .now()+125) { exit(0) }
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)
        let delegate = OpaqueDelegate(); app.delegate = delegate
        app.run(); withExtendedLifetime(delegate) {}
    }
}
