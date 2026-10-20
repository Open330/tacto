// Owned list fixture: rows that offer no AXPress and open on a double click,
// and a "Profile" control that offers no AXPress but counts mouse downs —
// the shapes of a messenger's room list (KakaoTalk, 2026-10-10). Every effect
// is written by the receiving views themselves into one read-only label.
import AppKit

@MainActor final class ProfileView: NSView {
    var downs = 0, errors = 0
    var contextMenu: NSMenu?
    // AXShowMenu, as a messenger's list items offer it.
    override func accessibilityPerformShowMenu() -> Bool {
        guard let contextMenu else { return false }
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            contextMenu.popUp(positioning: nil, at: NSPoint(x: 10, y: 10), in: self)
        }
        return true
    }
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

@MainActor final class ListDelegate: NSObject, NSApplicationDelegate, NSTableViewDataSource, NSTableViewDelegate, NSMenuDelegate {
    let rooms = ["Room A", "Room B", "Room C"]
    var window: NSWindow!, table: RoomTable!, profile: ProfileView!, label: NSTextField!
    var message: MessageField!, transcript: NSStackView!
    var opens = 0, opened = "none", singleClicks = 0, sent = 0, lastSent = "none"
    var menuOpen = false, menuPicks = 0, reopens = 0
    var roomWindows: [NSWindow] = []
    var consumedCloseSignal = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(contentRect: NSRect(x: 220, y: 220, width: 420, height: 360),
                          styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "TACTO List Fixture"
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
        let menu = NSMenu(title: "Profile")
        let pick = NSMenuItem(title: "Open Chat", action: #selector(picked), keyEquivalent: "")
        pick.target = self
        menu.addItem(pick); menu.delegate = self
        profile.contextMenu = menu
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
        // .common: the label keeps updating while a menu is tracking.
        let timer = Timer(timeInterval: 0.05, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.refresh() }
        }
        RunLoop.main.add(timer, forMode: .common)
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
    @objc func picked() { menuPicks += 1 }
    func menuWillOpen(_ menu: NSMenu) { menuOpen = true }
    func menuDidClose(_ menu: NSMenu) { menuOpen = false }
    // The Dock's "reopen": show the main window again if it was closed, without
    // activating (the request is the host's; this fixture just honours it).
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        reopens += 1
        if !window.isVisible { window.orderFront(nil) }
        return false
    }
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
        // Host-owned fixture setup only: close this process's own main window
        // once, without Python AX input or commands to any other application.
        let args = CommandLine.arguments
        if !consumedCloseSignal, let index = args.firstIndex(of: "--close-owned-window"),
           index + 1 < args.count,
           (try? String(contentsOfFile: args[index + 1], encoding: .utf8)) == "close-owned-main" {
            consumedCloseSignal = true
            window.close()
        }
        label.stringValue = "profileDowns=\(profile.downs) routeErrors=\(profile.errors) "
            + "singleClicks=\(singleClicks) opens=\(opens) opened=\(opened.replacingOccurrences(of: " ", with: "_")) "
            + "selected=\(table.selectedRow) key=\(window.isKeyWindow) "
            + "menuOpen=\(menuOpen) menuPicks=\(menuPicks) reopens=\(reopens) visible=\(window.isVisible) "
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
