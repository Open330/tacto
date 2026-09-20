// Owned activation probe. Logs only this app's AppKit-defined events/lifecycle.
// swiftc -parse-as-library NativeActivationFixture.swift -o NativeActivationFixture
import AppKit
import Foundation

private func emit(_ fields: [String: Any]) {
    guard var bytes = try? JSONSerialization.data(withJSONObject: fields,
                                                 options: [.sortedKeys]) else { return }
    bytes.append(0x0a)
    FileHandle.standardOutput.write(bytes)
}

@MainActor
private func windowState() -> [String: Any] {
    let app = NSApplication.shared
    return ["isActive": app.isActive, "keyWindow": app.keyWindow?.windowNumber ?? -1,
            "mainWindow": app.mainWindow?.windowNumber ?? -1,
            "windows": app.windows.map { window in
                ["number": window.windowNumber, "isKey": window.isKeyWindow,
                 "isMain": window.isMainWindow, "canBecomeKey": window.canBecomeKey,
                 "canBecomeMain": window.canBecomeMain,
                 "frame": NSStringFromRect(window.frame)] as [String: Any]
            }, "timestamp": ProcessInfo.processInfo.systemUptime]
}

@objc(NLCActivationApplication)
@MainActor
final class ActivationApplication: NSApplication {
    override func sendEvent(_ event: NSEvent) {
        if event.type == .leftMouseDown || event.type == .leftMouseUp {
            var mouse: [String: Any] = ["kind":"ownedMouse", "type":event.type.rawValue,
                "windowNumber":event.windowNumber, "flags":event.modifierFlags.rawValue,
                "clickCount":event.clickCount, "buttonNumber":event.buttonNumber,
                "eventNumber":event.eventNumber, "pressure":event.pressure,
                "locationInWindow":[event.locationInWindow.x,event.locationInWindow.y],
                "timestamp":event.timestamp]
            if let cg = event.cgEvent {
                mouse["cgLocation"] = [cg.location.x,cg.location.y]
                mouse["windowUnderPointer"] = cg.getIntegerValueField(.mouseEventWindowUnderMousePointer)
                mouse["windowHandlingEvent"] = cg.getIntegerValueField(.mouseEventWindowUnderMousePointerThatCanHandleThisEvent)
                mouse["sourceUnixProcessID"] = cg.getIntegerValueField(.eventSourceUnixProcessID)
                mouse["targetUnixProcessID"] = cg.getIntegerValueField(.eventTargetUnixProcessID)
            }
            emit(mouse)
        }
        if event.type == .appKitDefined {
            var record: [String: Any] = [
                "kind": "appKitDefined", "type": event.type.rawValue,
                "subtype": event.subtype.rawValue, "windowNumber": event.windowNumber,
                "data1": event.data1, "data2": event.data2,
                "modifierFlags": event.modifierFlags.rawValue, "timestamp": event.timestamp,
                "phase": "before", "state": windowState(),
            ]
            if let cg = event.cgEvent {
                record["cgEventType"] = cg.type.rawValue
                record["targetUnixProcessID"] = cg.getIntegerValueField(.eventTargetUnixProcessID)
                record["sourceUnixProcessID"] = cg.getIntegerValueField(.eventSourceUnixProcessID)
                record["cgLocation"] = [cg.location.x, cg.location.y]
            }
            emit(record)
        }
        // Preserve the ordinary AppKit event path, including activation handling.
        super.sendEvent(event)
        if event.type == .appKitDefined && event.subtype.rawValue == 1 {
            emit(["kind": "afterActivationDispatch", "state": windowState()])
            DispatchQueue.main.async {
                emit(["kind": "afterActivationRunLoop", "state": windowState()])
            }
        }
    }
}

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
final class MenuFixtureDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
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

    private func lifecycle(_ name: String) {
        let app = NSApplication.shared
        emit(["kind": name, "isActive": app.isActive,
              "keyWindow": app.keyWindow?.windowNumber ?? -1,
              "timestamp": ProcessInfo.processInfo.systemUptime])
    }

    func applicationDidBecomeActive(_ notification: Notification) {
        lifecycle("applicationDidBecomeActive")
    }

    func applicationDidResignActive(_ notification: Notification) {
        lifecycle("applicationDidResignActive")
    }

    func windowDidBecomeKey(_ notification: Notification) {
        emit(["kind": "windowDidBecomeKey", "state": windowState(),
              "stack": Array(Thread.callStackSymbols.prefix(24))])
    }

    func windowDidBecomeMain(_ notification: Notification) {
        emit(["kind": "windowDidBecomeMain", "state": windowState(),
              "stack": Array(Thread.callStackSymbols.prefix(24))])
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
        window.delegate = self
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
enum NativeActivationFixture {
    @MainActor static func main() {
        let args = Array(CommandLine.arguments.dropFirst())
        var lifetime: TimeInterval = 60
        if !args.isEmpty {
            guard args.count == 2, args[0] == "--timeout", let seconds = Double(args[1]),
                  seconds.isFinite, seconds >= 1, seconds <= 300 else {
                fputs("usage: NativeActivationFixture [--timeout seconds (1...300)]\n", stderr)
                exit(2)
            }
            lifetime = seconds
        }
        DispatchQueue.global().asyncAfter(deadline: .now() + lifetime + 5) { exit(0) }
        // Construct the subclass before any shared-application access. Verify
        // that AppKit's singleton is this instance; never silently use a base app.
        let app = ActivationApplication.shared
        let installed = app is ActivationApplication
        emit(["kind": "startup", "applicationClass": NSStringFromClass(type(of: NSApplication.shared)),
              "subclassInstalled": installed, "pid": ProcessInfo.processInfo.processIdentifier,
              "timestamp": ProcessInfo.processInfo.systemUptime])
        guard installed else { exit(2) }
        app.setActivationPolicy(.accessory)
        let delegate = MenuFixtureDelegate(lifetime: lifetime)
        app.delegate = delegate
        withExtendedLifetime(delegate) { app.run() }
    }
}
