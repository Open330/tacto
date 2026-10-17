// Owned integration fixture: a control that draws itself and tells the
// accessibility API nothing about what is inside it.
//
// This is the case `point` exists for. The canvas is one AXGroup with no
// children, no title and no actions — an observation of this app can see that
// something is there and cannot see the three targets painted on it. The only
// way to reach them is to look at a picture and say where.
//
// Build: swiftc -parse-as-library NativeCanvasFixture.swift -o NativeCanvasFixture
import AppKit
import Foundation

/// The three targets, in canvas coordinates from its TOP-left.
let targets: [(name: String, rect: NSRect, colour: NSColor)] = [
    ("red",   NSRect(x:  20, y: 20, width: 120, height: 80), .systemRed),
    ("green", NSRect(x: 170, y: 20, width: 120, height: 80), .systemGreen),
    ("blue",  NSRect(x: 320, y: 20, width: 120, height: 80), .systemBlue),
]

@MainActor
final class CanvasView: NSView {
    /// Which target was last hit, written by the view's own hit handling —
    /// the witness. Nothing else sets it.
    var onHit: ((String) -> Void)?

    override var isFlipped: Bool { true }   // top-left origin, like AX

    override func draw(_ dirty: NSRect) {
        NSColor.windowBackgroundColor.setFill()
        bounds.fill()
        for target in targets {
            target.colour.setFill()
            NSBezierPath(roundedRect: target.rect, xRadius: 8, yRadius: 8).fill()
        }
    }

    override func mouseDown(with event: NSEvent) {
        let where_ = convert(event.locationInWindow, from: nil)
        for target in targets where target.rect.contains(where_) {
            onHit?(target.name)
            return
        }
        onHit?("none")
    }

    /// One opaque thing to anyone WALKING the tree; three buttons to anyone
    /// who asks about a position.
    ///
    /// This is the shape that makes a coordinate worth having, and it is the
    /// ordinary shape of a custom view rather than a contrived one.
    /// `accessibilityChildren` returns nothing, so an observation of this app
    /// sees a single group and cannot name the targets — while
    /// `accessibilityHitTest` returns a real element for the target under a
    /// point, and that element implements press.
    ///
    /// A view that answered NEITHER — no children and no hit test — would be
    /// unreachable by any accessibility-driven tool, this one and Codex
    /// Computer Use alike, since neither posts mouse events. That is a real
    /// limit and not what this fixture is about.
    override func isAccessibilityElement() -> Bool { true }
    override func accessibilityRole() -> NSAccessibility.Role? { .group }
    override func accessibilityLabel() -> String? { "canvas" }
    override func accessibilityChildren() -> [Any]? { [] }

    /// Built once and kept. A hit test that MADE an element and returned it
    /// handed back something already on its way out: by the time the caller
    /// pressed it, AX answered -25202, kAXErrorInvalidUIElement. An accessible
    /// element has to outlive the question that asked for it.
    private lazy var targetElements: [String: TargetElement] = {
        var made: [String: TargetElement] = [:]
        for target in targets {
            made[target.name] = TargetElement(name: target.name, view: self)
        }
        return made
    }()

    override func accessibilityHitTest(_ screenPoint: NSPoint) -> Any? {
        guard let window else { return self }
        let inWindow = window.convertPoint(fromScreen: screenPoint)
        let local = convert(inWindow, from: nil)
        for target in targets where target.rect.contains(local) {
            return targetElements[target.name] ?? self
        }
        return self
    }
}

/// A target, made real only for whoever asked about its position.
final class TargetElement: NSAccessibilityElement {
    private let name: String
    private weak var view: CanvasView?

    init(name: String, view: CanvasView) {
        self.name = name
        self.view = view
        super.init()
        setAccessibilityRole(.button)
        setAccessibilityLabel(name)
        setAccessibilityParent(view)
    }

    override func accessibilityPerformPress() -> Bool {
        guard let view else { return false }
        DispatchQueue.main.async { view.onHit?(self.name) }
        return true
    }
}

@MainActor
final class CanvasDelegate: NSObject, NSApplicationDelegate {
    private var window: NSWindow?
    private let hit = NSTextField(labelWithString: "none")
    private let count = NSTextField(labelWithString: "0")
    private var hits = 0
    private let lifetime: TimeInterval

    init(lifetime: TimeInterval) {
        self.lifetime = lifetime
        super.init()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let window = NSWindow(
            contentRect: NSRect(x: 140, y: 140, width: 460, height: 200),
            styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "NLC Native Canvas Fixture"
        window.isReleasedWhenClosed = false
        self.window = window

        let canvas = CanvasView(frame: NSRect(x: 0, y: 0, width: 460, height: 120))
        canvas.onHit = { [weak self] name in
            guard let self else { return }
            self.hits += 1
            self.hit.stringValue = name
            self.count.stringValue = String(self.hits)
        }
        // The witnesses. These ARE accessible, on purpose: the point of the
        // fixture is that the target cannot be found through AX while the
        // result of hitting it can be read back through AX.
        hit.identifier = NSUserInterfaceItemIdentifier("tacto-canvas-hit")
        hit.setAccessibilityIdentifier("tacto-canvas-hit")
        hit.frame = NSRect(x: 20, y: 130, width: 200, height: 24)
        count.identifier = NSUserInterfaceItemIdentifier("tacto-canvas-count")
        count.setAccessibilityIdentifier("tacto-canvas-count")
        count.frame = NSRect(x: 240, y: 130, width: 120, height: 24)

        window.contentView?.addSubview(canvas)
        window.contentView?.addSubview(hit)
        window.contentView?.addSubview(count)

        // Behind everything and never activated: reaching this must not need
        // the window in front, which is the property the whole project is for.
        window.orderBack(nil)
        let timer = Timer(timeInterval: lifetime, repeats: false) { _ in
            NSApplication.shared.terminate(nil)
        }
        RunLoop.main.add(timer, forMode: .common)
        RunLoop.main.add(timer, forMode: .eventTracking)
        print("TACTO_NATIVE_CANVAS_FIXTURE pid=\(ProcessInfo.processInfo.processIdentifier)")
    }
}

@main
enum NativeCanvasFixture {
    @MainActor static func main() {
        var lifetime: TimeInterval = 90
        let args = Array(CommandLine.arguments.dropFirst())
        var index = 0
        while index < args.count {
            guard index + 1 < args.count else { exit(2) }
            if args[index] == "--timeout", let seconds = Double(args[index + 1]),
               seconds.isFinite, seconds >= 1, seconds <= 300 {
                lifetime = seconds
            } else { exit(2) }
            index += 2
        }
        DispatchQueue.global().asyncAfter(deadline: .now() + lifetime + 5) { exit(0) }
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)
        let delegate = CanvasDelegate(lifetime: lifetime)
        app.delegate = delegate
        withExtendedLifetime(delegate) { app.run() }
    }
}
