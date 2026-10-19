// Owned background fixture. Only receiving AX setters advance the workflow.
import AppKit

@MainActor final class StageView: NSView {
    weak var owner: RecoveryDelegate?
    override func isAccessibilityElement() -> Bool { true }
    override func accessibilityRole() -> NSAccessibility.Role? { .textField }
    override func accessibilityIdentifier() -> String { "workflow-stage" }
    override func accessibilityValue() -> Any? { String(owner?.stage ?? 0) }
    override func isAccessibilityEnabled() -> Bool { true }
    override func setAccessibilityValue(_ value: Any?) {
        guard let owner, let text = value as? String,
              let requested = Int(text), requested == owner.stage + 1,
              requested <= owner.stageLimit else { return }
        if requested == 6 { owner.stageSixRequests += 1 }
        if owner.blocked {
            owner.blockedWrites += 1
            owner.refresh()
            return
        }
        owner.stage = requested
        owner.acceptedWrites += 1
        if owner.interruptStages.contains(requested) { owner.openSheet() }
        owner.refresh()
    }
    override func draw(_ dirtyRect: NSRect) {
        NSColor.systemBlue.setFill(); bounds.fill()
    }
}

@MainActor final class RecoveryDelegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!, sheet: NSWindow!, label: NSTextField!
    var stage = 0, acceptedWrites = 0, blockedWrites = 0, cancels = 0
    var stageSixRequests = 0, otherClicks = 0
    let repeated = CommandLine.arguments.dropFirst().contains("--repeated")
    var stageLimit: Int { repeated ? 24 : 12 }
    var interruptStages: Set<Int> { repeated ? [5, 11, 17] : [5] }
    var blocked = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(contentRect: NSRect(x: 240, y: 240, width: 400, height: 180),
                          styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "NLC Recovery Fixture"
        window.isReleasedWhenClosed = false
        let field = StageView(frame: NSRect(x: 20, y: 95, width: 350, height: 55))
        field.owner = self
        window.contentView?.addSubview(field)
        let other = NSButton(title: "Other", target: self, action: #selector(otherClicked))
        other.frame = NSRect(x: 280, y: 70, width: 80, height: 25)
        window.contentView?.addSubview(other)
        label = NSTextField(labelWithString: "starting")
        label.frame = NSRect(x: 10, y: 15, width: 380, height: 60)
        label.maximumNumberOfLines = 3
        label.setAccessibilityIdentifier("recovery-state")
        window.contentView?.addSubview(label)
        window.orderBack(nil)
        refresh()
    }

    func openSheet() {
        blocked = true
        sheet = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 280, height: 100),
                         styleMask: [.titled], backing: .buffered, defer: false)
        sheet.title = "Owned interruption"
        sheet.isReleasedWhenClosed = false
        let cancel = NSButton(title: "Cancel", target: self, action: #selector(cancelSheet))
        cancel.setAccessibilityIdentifier("owned-sheet-cancel")
        cancel.frame = NSRect(x: 85, y: 25, width: 100, height: 35)
        sheet.contentView?.addSubview(cancel)
        window.beginSheet(sheet)
    }

    @objc func cancelSheet() {
        guard blocked else { return }
        cancels += 1
        window.endSheet(sheet)
        sheet.orderOut(nil)
        blocked = false
        refresh()
    }

    @objc func otherClicked() { otherClicks += 1; refresh() }

    func refresh() {
        label.stringValue = "stage=\(stage) accepted=\(acceptedWrites) blockedWrites=\(blockedWrites) cancels=\(cancels) stageSixRequests=\(stageSixRequests) otherClicks=\(otherClicks) sheet=\(blocked) active=\(NSApp.isActive)"
    }
}

@main struct RecoveryFixture {
    @MainActor static func main() {
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)
        let delegate = RecoveryDelegate()
        app.delegate = delegate
        app.run()
        withExtendedLifetime(delegate) {}
    }
}
