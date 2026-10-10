// Owned list fixture: rows that offer no AXPress and open on a double click,
// and a "Profile" control that offers no AXPress but counts mouse downs —
// the shapes of a messenger's room list (KakaoTalk, 2026-10-10). Every effect
// is written by the receiving views themselves into one read-only label.
import AppKit

@MainActor final class ProfileView: NSView {
    var downs = 0, errors = 0
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func isAccessibilityElement() -> Bool { true }
    override func accessibilityRole() -> NSAccessibility.Role? { .group }
    override func accessibilityLabel() -> String? { "Profile" }
    override func mouseDown(with event: NSEvent) {
        downs += 1
        if event.windowNumber != window?.windowNumber { errors += 1 }
    }
    override func draw(_ dirtyRect: NSRect) {
        NSColor.systemBlue.setFill(); bounds.fill()
    }
}

// Return on a selected row opens it; Return in the message field "sends"
// what is typed there into the fixture's own transcript.
@MainActor final class RoomTable: NSTableView {
    var onReturn: (() -> Void)?
    override func keyDown(with event: NSEvent) {
        if event.keyCode == 36 { onReturn?() } else { super.keyDown(with: event) }
    }
}

@MainActor final class MessageField: NSTextView {
    var onSend: ((String) -> Void)?
    override func keyDown(with event: NSEvent) {
        if event.keyCode == 36 {
            let text = string; string = ""; onSend?(text)
        } else { super.keyDown(with: event) }
    }
}

@MainActor final class ListDelegate: NSObject, NSApplicationDelegate, NSTableViewDataSource, NSTableViewDelegate {
    let rooms = ["Room A", "Room B", "Room C"]
    var window: NSWindow!, table: RoomTable!, profile: ProfileView!, label: NSTextField!
    var message: MessageField!, transcript: NSStackView!
    var opens = 0, opened = "none", singleClicks = 0, sent = 0, lastSent = "none"
    var roomWindows: [NSWindow] = []

    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(contentRect: NSRect(x: 220, y: 220, width: 420, height: 360),
                          styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "NLC List Fixture"
        window.isReleasedWhenClosed = false
        let scroll = NSScrollView(frame: NSRect(x: 10, y: 170, width: 300, height: 180))
        table = RoomTable(frame: scroll.bounds)
        table.onReturn = { [weak self] in self?.openRow(self?.table.selectedRow ?? -1) }
        let column = NSTableColumn(identifier: .init("name"))
        column.width = 280
        table.addTableColumn(column)
        table.headerView = nil
        table.dataSource = self; table.delegate = self
        table.target = self
        table.action = #selector(clicked)
        table.doubleAction = #selector(open)
        table.setAccessibilityIdentifier("room-list")
        scroll.documentView = table
        profile = ProfileView(frame: NSRect(x: 320, y: 290, width: 80, height: 60))
        let messageScroll = NSScrollView(frame: NSRect(x: 10, y: 105, width: 400, height: 55))
        message = MessageField(frame: messageScroll.bounds)
        message.setAccessibilityIdentifier("message-field")
        message.onSend = { [weak self] text in
            guard let self else { return }
            self.sent += 1; self.lastSent = text
            // The conversation as the sender sees it: one label per message.
            let bubble = NSTextField(labelWithString: text)
            self.transcript.addArrangedSubview(bubble)
        }
        messageScroll.documentView = message
        window.contentView?.addSubview(messageScroll)
        transcript = NSStackView(frame: NSRect(x: 320, y: 170, width: 90, height: 110))
        transcript.orientation = .vertical
        transcript.setAccessibilityElement(true)
        transcript.setAccessibilityRole(.group)
        transcript.setAccessibilityIdentifier("transcript")
        window.contentView?.addSubview(transcript)
        label = NSTextField(labelWithString: "starting")
        label.setAccessibilityIdentifier("list-state")
        label.frame = NSRect(x: 10, y: 10, width: 400, height: 90)
        label.maximumNumberOfLines = 4
        window.contentView?.addSubview(scroll)
        window.contentView?.addSubview(profile)
        window.contentView?.addSubview(label)
        window.orderBack(nil)
        refresh()
        Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.refresh() }
        }
    }
    func numberOfRows(in tableView: NSTableView) -> Int { rooms.count }
    func tableView(_ tableView: NSTableView, viewFor tableColumn: NSTableColumn?, row: Int) -> NSView? {
        let field = NSTextField(labelWithString: rooms[row])
        let cell = NSTableCellView()
        cell.addSubview(field); cell.textField = field
        field.frame = NSRect(x: 4, y: 0, width: 270, height: 17)
        return cell
    }
    @objc func clicked() { singleClicks += 1 }
    @objc func open() { openRow(table.clickedRow) }
    func openRow(_ row: Int) {
        guard row >= 0, row < rooms.count else { return }
        opens += 1; opened = rooms[row]
        let room = NSWindow(contentRect: NSRect(x: 680, y: 220 + roomWindows.count * 40, width: 260, height: 120),
                            styleMask: [.titled, .closable], backing: .buffered, defer: false)
        room.title = rooms[row]
        room.isReleasedWhenClosed = false
        room.orderBack(nil)
        roomWindows.append(room)
    }
    func refresh() {
        label.stringValue = "profileDowns=\(profile.downs) routeErrors=\(profile.errors) "
            + "singleClicks=\(singleClicks) opens=\(opens) opened=\(opened.replacingOccurrences(of: " ", with: "_")) "
            + "selected=\(table.selectedRow) key=\(window.isKeyWindow) "
            + "sent=\(sent) lastSent=\(lastSent.isEmpty ? "empty" : lastSent.replacingOccurrences(of: " ", with: "_")) "
            + "active=\(NSApplication.shared.isActive)"
    }
}

@main enum ListFixture {
    @MainActor static func main() {
        DispatchQueue.global().asyncAfter(deadline: .now() + 125) { exit(0) }
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)
        let delegate = ListDelegate(); app.delegate = delegate
        app.run(); withExtendedLifetime(delegate) {}
    }
}
