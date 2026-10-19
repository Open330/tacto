import AppKit

@MainActor final class MenuReadinessDelegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!, label: NSTextField!, command: NSMenuItem!
    var invoked = 0
    let signal = CommandLine.arguments[1]
    func applicationDidFinishLaunching(_ notification: Notification) {
        let main = NSMenu()
        main.autoenablesItems = false
        let application = NSMenuItem(title: "NLC Menu Readiness Fixture", action: nil, keyEquivalent: "")
        application.submenu = NSMenu(title: "NLC Menu Readiness Fixture")
        main.addItem(application)
        let root = NSMenuItem(title: "Probe", action: nil, keyEquivalent: "")
        let menu = NSMenu(title: "Probe"); menu.autoenablesItems = false
        command = NSMenuItem(title: "Ready Command", action: #selector(received), keyEquivalent: "")
        command.target = self; command.isEnabled = false
        menu.addItem(command); root.submenu = menu; main.addItem(root)
        NSApp.mainMenu = main
        window = NSWindow(contentRect: NSRect(x: 240, y: 240, width: 400, height: 180),
            styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "NLC Menu Readiness Fixture"; window.isReleasedWhenClosed = false
        label = NSTextField(labelWithString: "")
        label.setAccessibilityIdentifier("menu-readiness-state")
        label.frame = NSRect(x: 20, y: 50, width: 350, height: 60)
        window.contentView?.addSubview(label); refresh(); window.orderBack(nil)
        Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated {
                guard let self else { return }
                if FileManager.default.fileExists(atPath: self.signal) {
                    self.command.isEnabled = true; self.refresh()
                }
            }
        }
    }
    @objc func received() { invoked += 1; refresh() }
    func refresh() { label.stringValue = "enabled=\(command.isEnabled) invoked=\(invoked)" }
}

@main struct MenuReadinessFixture {
    @MainActor static func main() {
        let app = NSApplication.shared; app.setActivationPolicy(.accessory)
        let delegate = MenuReadinessDelegate(); app.delegate = delegate
        app.run(); withExtendedLifetime(delegate) {}
    }
}
