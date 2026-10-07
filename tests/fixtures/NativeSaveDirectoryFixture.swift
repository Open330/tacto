// Owned background NSSavePanel, never approves a save or activates an app.
import AppKit
import Foundation

@MainActor
final class SaveDirectoryDelegate: NSObject, NSApplicationDelegate, NSOpenSavePanelDelegate {
    var window: NSWindow?
    var panel: NSSavePanel?
    let directory: String
    let changedDirectory: String?
    let trigger: String?
    var changed = false
    var presentedChanged = false
    var lastNavigatedDirectory = ""
    func panel(_ sender: Any, didChangeToDirectoryURL url: URL?) {
        lastNavigatedDirectory = url?.path ?? ""
    }
    init(directory: String, changedDirectory: String?, trigger: String?) {
        self.directory = directory
        self.changedDirectory = changedDirectory
        self.trigger = trigger
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        let window = NSWindow(contentRect: NSRect(x: 120, y: 120, width: 500, height: 160),
                              styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "NLC Owned Save Directory Fixture"
        window.isReleasedWhenClosed = false
        self.window = window
        window.orderBack(nil)
        present(directory, on: window)
        // The host knows the actual in-process destination; AX reads must
        // agree independently. No writer is ever called in this fixture.
        Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [weak self] _ in
            guard let self, let panel = self.panel else { return }
            if !self.changed, let trigger = self.trigger,
               self.changedDirectory != nil,
               FileManager.default.fileExists(atPath: trigger) {
                self.changed = true
                panel.cancel(nil)
            }
            let state: [String: Any] = ["directory": panel.directoryURL?.path ?? "",
                                      "proposed_url": panel.url?.path ?? "",
                                      "last_navigated_directory": self.lastNavigatedDirectory,
                                      "expanded": panel.isExpanded,
                                      "active": NSApplication.shared.isActive]
            if let data = try? JSONSerialization.data(withJSONObject: state) {
                print(String(decoding: data, as: UTF8.self))
                fflush(stdout)
            }
        }
    }

    func present(_ directory: String, on window: NSWindow) {
        let panel = NSSavePanel()
        self.panel = panel
        panel.delegate = self
        panel.directoryURL = URL(fileURLWithPath: directory, isDirectory: true)
        panel.nameFieldStringValue = "never-saved.rtf"
        panel.beginSheetModal(for: window) { [weak self] _ in
            guard let self else { return }
            if self.changed, !self.presentedChanged, let next = self.changedDirectory {
                self.presentedChanged = true
                self.present(next, on: window)
            } else {
                NSApplication.shared.terminate(nil)
            }
        }
    }
}

@main
enum NativeSaveDirectoryFixture {
    @MainActor static func main() {
        let args = CommandLine.arguments
        guard args.count == 2 || args.count == 4 else { exit(2) }
        UserDefaults.standard.set(true, forKey: "NSNavPanelExpandedStateForSaveMode")
        UserDefaults.standard.set(2, forKey: "NSNavLastListMode")
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)
        let delegate = SaveDirectoryDelegate(directory: args[1],
                    changedDirectory: args.count == 4 ? args[2] : nil,
                    trigger: args.count == 4 ? args[3] : nil)
        app.delegate = delegate
        DispatchQueue.global().asyncAfter(deadline: .now() + 25) { exit(0) }
        withExtendedLifetime(delegate) { app.run() }
    }
}
