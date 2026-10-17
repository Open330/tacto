// Owned integration fixture: ordinary AppKit controls, no AX overrides.
// Build: swiftc -parse-as-library NativeSelectFixture.swift -o NativeSelectFixture
// A caller may package the executable in its own .app with a bundle identifier.
// Running it is a separate, explicitly coordinated UI test.
import AppKit
import Foundation

@MainActor
final class FixtureDelegate: NSObject, NSApplicationDelegate {
    private var window: NSWindow?
    private var selectionCount = 0
    private let result = NSTextField(labelWithString: "Alpha")
    private let count = NSTextField(labelWithString: "0")
    private let lifetime: TimeInterval
    private let decoys: Int
    private let foreground: Bool

    init(lifetime: TimeInterval, decoys: Int, foreground: Bool) {
        self.lifetime = lifetime
        self.decoys = decoys
        self.foreground = foreground
        super.init()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let window = NSWindow(
            contentRect: NSRect(x: 120, y: 120, width: 520, height: decoys > 0 ? 520 : 180),
            styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "NLC Native Select Fixture"
        window.isReleasedWhenClosed = false
        self.window = window

        let choice = NSPopUpButton(frame: NSRect(x: 24, y: 110, width: 220, height: 28),
                                   pullsDown: false)
        choice.identifier = NSUserInterfaceItemIdentifier("tacto-fixture-choice")
        choice.setAccessibilityIdentifier("tacto-fixture-choice")
        choice.addItems(withTitles: ["Alpha", "Beta", "Disabled"])
        choice.menu?.autoenablesItems = false
        choice.item(withTitle: "Disabled")?.isEnabled = false
        choice.selectItem(withTitle: "Alpha")
        choice.target = self
        choice.action = #selector(selectionChanged(_:))

        result.frame = NSRect(x: 24, y: 72, width: 300, height: 24)
        result.identifier = NSUserInterfaceItemIdentifier("tacto-fixture-result")
        result.setAccessibilityIdentifier("tacto-fixture-result")
        count.frame = NSRect(x: 24, y: 36, width: 300, height: 24)
        count.identifier = NSUserInterfaceItemIdentifier("tacto-fixture-count")
        count.setAccessibilityIdentifier("tacto-fixture-count")
        for index in 0..<decoys {
            let other = NSPopUpButton(frame: NSRect(x: 24 + (index % 3) * 160,
                y: 180 + (index / 3) * 30, width: 150, height: 26), pullsDown: false)
            other.setAccessibilityIdentifier("tacto-fixture-decoy-\(index)")
            other.addItems(withTitles: ["Other \(index)", "Alternative \(index)"])
            window.contentView?.addSubview(other)
        }
        window.contentView?.addSubview(choice)
        window.contentView?.addSubview(result)
        window.contentView?.addSubview(count)

        // Default: show behind other windows, do not activate, do not ask for
        // key focus. That is what the background-refusal probes need — a window
        // that is demonstrably not in front.
        //
        // `--foreground` is the opposite, and it exists because the two
        // questions are opposite. Checking that `select_option` really picks an
        // option needs a menu that can open, and a menu opens only in the app
        // that is in front. Finder used to be that subject; on macOS 27 its
        // view popup answers AXPress with kAXErrorSuccess and opens nothing.
        // So the fixture can be asked to come forward, by a harness that says
        // so out loud — never by the driver, which refuses instead.
        if foreground {
            window.makeKeyAndOrderFront(nil)
            NSApplication.shared.activate(ignoringOtherApps: true)
        } else {
            window.orderBack(nil)
        }
        let timer = Timer(timeInterval: lifetime, repeats: false) { _ in
            NSApplication.shared.terminate(nil)
        }
        // Menu tracking uses a nested run loop; a default-mode-only timer can
        // otherwise wait indefinitely for the menu to be dismissed.
        RunLoop.main.add(timer, forMode: .common)
        RunLoop.main.add(timer, forMode: .eventTracking)
        print("TACTO_NATIVE_SELECT_FIXTURE pid=\(ProcessInfo.processInfo.processIdentifier) timeout=\(lifetime)")
    }

    @objc private func selectionChanged(_ sender: NSPopUpButton) {
        selectionCount += 1
        result.stringValue = sender.titleOfSelectedItem ?? ""
        count.stringValue = String(selectionCount)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }
}

@main
enum NativeSelectFixture {
    @MainActor static func main() {
        let args = Array(CommandLine.arguments.dropFirst())
        var lifetime: TimeInterval = 90
        var decoys = 0
        var foreground = false
        var index = 0
        while index < args.count {
            // A flag with no value, so it is consumed before the pair parsing
            // below can demand one.
            if args[index] == "--foreground" {
                foreground = true
                index += 1
                continue
            }
            guard index + 1 < args.count else { exit(2) }
            if args[index] == "--timeout", let seconds = Double(args[index + 1]),
               seconds.isFinite, seconds >= 1, seconds <= 300 {
                lifetime = seconds
            } else if args[index] == "--decoys", let count = Int(args[index + 1]),
                      count >= 0, count <= 30 {
                decoys = count
            } else { exit(2) }
            index += 2
        }
        // Last-resort self-exit if AppKit's main thread never services timers.
        // This terminates only this disposable fixture, never another process.
        DispatchQueue.global().asyncAfter(deadline: .now() + lifetime + 5) {
            exit(0)
        }
        let app = NSApplication.shared
        // An accessory app cannot be frontmost, and a popup menu only opens in
        // the app that is. Which policy this runs under is therefore part of
        // what is being asked, not a detail.
        app.setActivationPolicy(foreground ? .regular : .accessory)
        let delegate = FixtureDelegate(lifetime: lifetime, decoys: decoys,
                                       foreground: foreground)
        app.delegate = delegate
        withExtendedLifetime(delegate) {
            app.run()
        }
    }
}
