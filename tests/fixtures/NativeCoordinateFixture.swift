// Owned coordinate fallback fixture. No activation or input synthesis.
import AppKit
import Foundation

@MainActor
final class CoordinateTargetView: NSView {
    private let counter: NSTextField
    private var pressedInside = false
    private var clicks = 0

    init(counter: NSTextField) {
        self.counter = counter
        super.init(frame: NSRect(x: 30, y: 70, width: 240, height: 70))
        setAccessibilityElement(true)
        setAccessibilityRole(.button)
        setAccessibilityEnabled(true)
        setAccessibilityLabel("Coordinate target")
        setAccessibilityIdentifier("tacto-coordinate-button")
        identifier = NSUserInterfaceItemIdentifier("tacto-coordinate-button")
    }

    required init?(coder: NSCoder) { fatalError("coder initialization unsupported") }

    // Advertise the ordinary button role but no AX actions: the only successful
    // interaction is an actual in-bounds mouse down/up pair handled below.
    override func accessibilityActionNames() -> [NSAccessibility.Action] { [] }
    override func accessibilityPerformPress() -> Bool { false }
    override func isAccessibilitySelectorAllowed(_ selector: Selector) -> Bool {
        if selector == #selector(accessibilityPerformPress) { return false }
        return super.isAccessibilitySelectorAllowed(selector)
    }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

    override func draw(_ dirtyRect: NSRect) {
        NSColor.controlColor.setFill()
        NSBezierPath(roundedRect: bounds, xRadius: 8, yRadius: 8).fill()
        let title = "Coordinate target" as NSString
        title.draw(at: NSPoint(x: 20, y: 26), withAttributes: [
            .font: NSFont.systemFont(ofSize: 16),
            .foregroundColor: NSColor.labelColor,
        ])
    }

    override func mouseDown(with event: NSEvent) {
        pressedInside = bounds.contains(convert(event.locationInWindow, from: nil))
    }

    override func mouseUp(with event: NSEvent) {
        let endsInside = bounds.contains(convert(event.locationInWindow, from: nil))
        if pressedInside && endsInside {
            clicks += 1
            counter.stringValue = String(clicks)
        }
        pressedInside = false
    }
}

@MainActor
final class CoordinateFixtureDelegate: NSObject, NSApplicationDelegate {
    private var window: NSWindow?
    private let lifetime: TimeInterval

    init(lifetime: TimeInterval) {
        self.lifetime = lifetime
        super.init()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let window = NSWindow(contentRect: NSRect(x: 120, y: 120, width: 360, height: 190),
                              styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "NLC Native Coordinate Fixture"
        window.isReleasedWhenClosed = false
        self.window = window
        let counter = NSTextField(labelWithString: "0")
        counter.frame = NSRect(x: 30, y: 25, width: 240, height: 24)
        counter.identifier = NSUserInterfaceItemIdentifier("tacto-coordinate-count")
        counter.setAccessibilityIdentifier("tacto-coordinate-count")
        window.contentView?.addSubview(CoordinateTargetView(counter: counter))
        window.contentView?.addSubview(counter)
        window.orderBack(nil)
        let timer = Timer(timeInterval: lifetime, repeats: false) { _ in
            NSApplication.shared.terminate(nil)
        }
        RunLoop.main.add(timer, forMode: .common)
        RunLoop.main.add(timer, forMode: .eventTracking)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

@main
enum NativeCoordinateFixture {
    @MainActor static func main() {
        let args = Array(CommandLine.arguments.dropFirst())
        var lifetime: TimeInterval = 90
        if !args.isEmpty {
            guard args.count == 2, args[0] == "--timeout", let seconds = Double(args[1]),
                  seconds.isFinite, seconds >= 1, seconds <= 300 else {
                fputs("usage: NativeCoordinateFixture [--timeout seconds (1...300)]\n", stderr)
                exit(2)
            }
            lifetime = seconds
        }
        DispatchQueue.global().asyncAfter(deadline: .now() + lifetime + 5) { exit(0) }
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)
        let delegate = CoordinateFixtureDelegate(lifetime: lifetime)
        app.delegate = delegate
        withExtendedLifetime(delegate) { app.run() }
    }
}
