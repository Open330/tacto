// Ordinary AppKit responder-chain probe. Building does not launch the fixture.
// swiftc -parse-as-library NativeMenuFixture.swift -o NativeMenuFixture
import AppKit
import Foundation

@MainActor
final class ResponderView: NSView {
    let counter: NSTextField
    private var commandCount = 0

    init(counter: NSTextField) {
        self.counter = counter
        super.init(frame: NSRect(x: 0, y: 0, width: 620, height: 220))
    }

    required init?(coder: NSCoder) { fatalError("coder initialization unsupported") }
    override var acceptsFirstResponder: Bool { true }

    @objc func responderCommand(_ sender: Any?) {
        commandCount += 1
        counter.stringValue = String(commandCount)
    }
}

@MainActor
final class MenuFixtureDelegate: NSObject, NSApplicationDelegate {
    private var window: NSWindow?
    private var directCount = 0
    private var makeKeyCount = 0
    private let responderCounter = NSTextField(labelWithString: "0")
    private let directCounter = NSTextField(labelWithString: "0")
    private let makeKeyCounter = NSTextField(labelWithString: "0")
    private let state = NSTextField(labelWithString: "starting")
    private let lifetime: TimeInterval

    init(lifetime: TimeInterval) {
        self.lifetime = lifetime
        super.init()
    }

    private func identify(_ view: NSView, _ value: String) {
        view.identifier = NSUserInterfaceItemIdentifier(value)
        view.setAccessibilityIdentifier(value)
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let window = NSWindow(contentRect: NSRect(x: 120, y: 120, width: 620, height: 220),
                              styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "NLC Native Menu Fixture"
        window.isReleasedWhenClosed = false
        self.window = window
        let content = ResponderView(counter: responderCounter)
        window.contentView = content
        identify(responderCounter, "nlc-fixture-responder-count")
        identify(directCounter, "nlc-fixture-direct-count")
        identify(makeKeyCounter, "nlc-fixture-make-key-count")
        identify(state, "nlc-fixture-state")
        responderCounter.frame = NSRect(x: 24, y: 160, width: 260, height: 24)
        directCounter.frame = NSRect(x: 24, y: 125, width: 260, height: 24)
        makeKeyCounter.frame = NSRect(x: 320, y: 125, width: 260, height: 24)
        state.frame = NSRect(x: 24, y: 82, width: 580, height: 32)
        content.addSubview(responderCounter)
        content.addSubview(directCounter)
        content.addSubview(makeKeyCounter)
        content.addSubview(state)

        let button = NSButton(title: "Make Fixture Window Key", target: self,
                              action: #selector(makeWindowKey(_:)))
        identify(button, "nlc-fixture-make-key")
        button.frame = NSRect(x: 24, y: 24, width: 270, height: 32)
        content.addSubview(button)

        let mainMenu = NSMenu(title: "Main")
        // AppKit reserves the first top-level menu for the application name.
        // Keep Probe separate so its observed title is not rewritten.
        let applicationItem = NSMenuItem(title: "NLC Menu Fixture", action: nil,
                                         keyEquivalent: "")
        let applicationMenu = NSMenu(title: "NLC Menu Fixture")
        let quit = NSMenuItem(title: "Quit NLC Menu Fixture",
                              action: #selector(NSApplication.terminate(_:)), keyEquivalent: "")
        quit.target = NSApplication.shared
        applicationMenu.addItem(quit)
        applicationItem.submenu = applicationMenu
        mainMenu.addItem(applicationItem)
        let probeItem = NSMenuItem(title: "Probe", action: nil, keyEquivalent: "")
        let probe = NSMenu(title: "Probe")
        // Leave ordinary AppKit auto-validation enabled. Nil target must be
        // resolved through the real responder chain, not a fixture override.
        let responder = NSMenuItem(title: "Responder Command",
                                   action: #selector(ResponderView.responderCommand(_:)),
                                   keyEquivalent: "")
        responder.target = nil
        let direct = NSMenuItem(title: "Direct Command",
                                action: #selector(directCommand(_:)), keyEquivalent: "")
        direct.target = self
        probe.addItem(responder)
        probe.addItem(direct)
        probeItem.submenu = probe
        mainMenu.addItem(probeItem)
        let hardeningRoot = NSMenuItem(title: "Hardening", action: nil, keyEquivalent: "")
        let hardeningMenu = NSMenu(title: "Hardening")
        hardeningMenu.autoenablesItems = false
        for title in ["Duplicate", "Duplicate", "Disabled", "Enabled"] {
            let item = NSMenuItem(title: title, action: #selector(directCommand(_:)), keyEquivalent: "")
            item.target = self
            item.isEnabled = title != "Disabled"
            hardeningMenu.addItem(item)
        }
        hardeningRoot.submenu = hardeningMenu
        mainMenu.addItem(hardeningRoot)
        NSApplication.shared.mainMenu = mainMenu

        window.makeFirstResponder(content)
        window.orderBack(nil)
        refreshState()
        let refresh = Timer(timeInterval: 0.1, repeats: true) { [weak self] _ in
            // This timer is installed only on RunLoop.main below.
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

    @objc private func makeWindowKey(_ sender: Any?) {
        makeKeyCount += 1
        makeKeyCounter.stringValue = String(makeKeyCount)
        // Deliberately no activate/orderFront. This is the isolated hypothesis.
        window?.makeKey()
    }

    @objc private func directCommand(_ sender: Any?) {
        directCount += 1
        directCounter.stringValue = String(directCount)
    }

    private func refreshState() {
        guard let window else { return }
        let app = NSApplication.shared
        state.stringValue = "isActive=\(app.isActive) isKeyWindow=\(window.isKeyWindow) "
            + "keyWindow=\(app.keyWindow?.windowNumber ?? -1) "
            + "mainWindow=\(app.mainWindow?.windowNumber ?? -1) "
            + "fixtureWindow=\(window.windowNumber)"
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

@main
enum NativeMenuFixture {
    @MainActor static func main() {
        let args = Array(CommandLine.arguments.dropFirst())
        var lifetime: TimeInterval = 60
        if !args.isEmpty {
            guard args.count == 2, args[0] == "--timeout", let seconds = Double(args[1]),
                  seconds.isFinite, seconds >= 1, seconds <= 300 else {
                fputs("usage: NativeMenuFixture [--timeout seconds (1...300)]\n", stderr)
                exit(2)
            }
            lifetime = seconds
        }
        DispatchQueue.global().asyncAfter(deadline: .now() + lifetime + 5) { exit(0) }
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)
        let delegate = MenuFixtureDelegate(lifetime: lifetime)
        app.delegate = delegate
        withExtendedLifetime(delegate) { app.run() }
    }
}
