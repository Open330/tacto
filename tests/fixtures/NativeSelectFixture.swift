// Owned integration fixture: ordinary AppKit controls, no AX overrides.
// Build: swiftc -parse-as-library NativeSelectFixture.swift -o NativeSelectFixture
// A caller may package the executable in its own .app with a bundle identifier.
// Running it is a separate, explicitly coordinated UI test.
import AppKit
import Foundation

@MainActor
final class FixtureDelegate: NSObject, NSApplicationDelegate, NSComboBoxDelegate {
    private var window: NSWindow?
    private var selectionCount = 0
    private let selectedIndex = NSTextField(labelWithString: "0")
    private let result = NSTextField(labelWithString: "Alpha")
    private let count = NSTextField(labelWithString: "0")
    private let lifetime: TimeInterval
    private let decoys: Int
    private let foreground: Bool
    private let options: Int
    private let duplicates: Bool
    private let combo: Bool
    private let replacement:Bool
    private let visibleItems:Int
    private weak var comboView:NSComboBox?
    private let replacementCount=NSTextField(labelWithString:"0")

    init(lifetime: TimeInterval, decoys: Int, foreground: Bool, options: Int, duplicates: Bool, combo: Bool,replacement:Bool,visibleItems:Int) {
        self.lifetime = lifetime
        self.decoys = decoys
        self.foreground = foreground
        self.options = options
        self.duplicates = duplicates
        self.combo = combo
        self.replacement=replacement
        self.visibleItems=visibleItems
        super.init()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let window = NSWindow(
            contentRect: NSRect(x: 120, y: 120, width: 520, height: decoys > 0 ? 520 : 180),
            styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "TACTO Native Select Fixture"
        window.isReleasedWhenClosed = false
        self.window = window

        let choice = NSPopUpButton(frame: NSRect(x: 24, y: 110, width: 220, height: 28),
                                   pullsDown: false)
        choice.identifier = NSUserInterfaceItemIdentifier("tacto-fixture-choice")
        choice.setAccessibilityIdentifier("tacto-fixture-choice")
        choice.addItems(withTitles: ["Alpha", "Beta", "Disabled"])
        choice.addItems(withTitles:(0..<options).map { String(format:"Choice%03d",$0) })
        // NSPopUpButton's convenience API replaces matching titles. Add to
        // the menu directly so the refusal fixture truly contains two items.
        if duplicates {
            for _ in 0..<2 {
                choice.menu?.addItem(NSMenuItem(title:"Duplicate",action:nil,keyEquivalent:""))
            }
        }
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
        if combo {
            let box=NSComboBox(frame:choice.frame)
            box.setAccessibilityIdentifier("tacto-fixture-choice")
            box.isEditable=false
            if visibleItems>0 {box.numberOfVisibleItems=visibleItems}
            box.addItems(withObjectValues:["Alpha","Beta","Gamma"] + (0..<options).map {String(format:"Choice%03d",$0)})
            box.selectItem(at:0)
            box.delegate=self
            window.contentView?.addSubview(box)
            comboView=box
            selectedIndex.frame=NSRect(x:340,y:36,width:100,height:24)
            selectedIndex.setAccessibilityIdentifier("tacto-fixture-selected-index")
            window.contentView?.addSubview(selectedIndex)
        } else { window.contentView?.addSubview(choice) }
        window.contentView?.addSubview(result)
        window.contentView?.addSubview(count)
        if replacement {
            let button=NSButton(title:"Replace owned ComboBox",target:self,action:#selector(replaceChoice(_:)))
            button.frame=NSRect(x:340,y:110,width:160,height:28)
            button.setAccessibilityIdentifier("tacto-fixture-replace")
            window.contentView?.addSubview(button)
            replacementCount.frame=NSRect(x:340,y:72,width:100,height:24)
            replacementCount.setAccessibilityIdentifier("tacto-fixture-replacement-count")
            window.contentView?.addSubview(replacementCount)
        }

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

    @objc private func replaceChoice(_ sender:NSButton) {
        guard let box=comboView,let content=window?.contentView else {return}
        let frame=box.frame
        box.delegate=nil
        box.removeFromSuperview()
        let choice=NSPopUpButton(frame:frame,pullsDown:false)
        choice.setAccessibilityIdentifier("tacto-fixture-choice")
        choice.addItems(withTitles:["Alpha","Beta"])
        choice.selectItem(at:0)
        choice.target=self;choice.action=#selector(selectionChanged(_:))
        content.addSubview(choice)
        replacementCount.stringValue="1"
        sender.isEnabled=false
    }

    func comboBoxSelectionDidChange(_ notification: Notification) {
        guard let box=notification.object as? NSComboBox else { return }
        selectionCount += 1
        result.stringValue=box.objectValueOfSelectedItem as? String ?? ""
        count.stringValue=String(selectionCount)
        selectedIndex.stringValue=String(box.indexOfSelectedItem)
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
        var options = 0
        var duplicates = false
        var combo = false
        var replacement=false
        var visibleItems=0
        var index = 0
        while index < args.count {
            if args[index] == "--combo" { combo=true;index+=1;continue }
            if args[index] == "--replace-combo" {replacement=true;index+=1;continue}
            if args[index] == "--duplicate-options" {
                duplicates = true; index += 1; continue
            }
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
            } else if args[index] == "--options", let count = Int(args[index + 1]),
                      count >= 0, count <= 256 {
                options = count
            } else if args[index]=="--visible-items",let count=Int(args[index+1]),count>=1,count<=20 {
                visibleItems=count
            } else { exit(2) }
            index += 2
        }
        // Last-resort self-exit if AppKit's main thread never services timers.
        guard !replacement || combo else {exit(2)}
        guard visibleItems==0 || (combo && !replacement) else {exit(2)}
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
                                       foreground: foreground, options: options, duplicates: duplicates, combo:combo,replacement:replacement,visibleItems:visibleItems)
        app.delegate = delegate
        withExtendedLifetime(delegate) {
            app.run()
        }
    }
}
