// Owned two-window AX cost fixture. No activation or global input.
import AppKit

@MainActor final class ScaleWindow: NSWindow {
    var mouseDowns = 0, routeErrors = 0
    override func sendEvent(_ event: NSEvent) {
        if event.type == .leftMouseDown {
            mouseDowns += 1
            if event.windowNumber != windowNumber { routeErrors += 1 }
        }
        super.sendEvent(event)
    }
}

@MainActor final class ScaleDelegate: NSObject, NSApplicationDelegate {
    let count: Int, nonce: String
    var windows: [ScaleWindow] = []
    var activations = 0
    var sequence = 0
    init(count: Int, nonce: String) { self.count = count; self.nonce = nonce }
    func applicationDidFinishLaunching(_ notification: Notification) {
        for (index, suffix) in ["A", "B"].enumerated() {
            let window = ScaleWindow(contentRect:NSRect(x:170 + index*460,y:200,width:420,height:230),
                styleMask:[.titled,.closable],backing:.buffered,defer:false)
            window.title = "TACTO Readiness Scale \(suffix)"
            window.isReleasedWhenClosed = false
            let label = NSTextField(labelWithString:"Owned AX scale \(suffix)")
            label.frame = NSRect(x:20,y:140,width:360,height:40)
            window.contentView?.addSubview(label)
            if suffix == "B" {
                let root = NSView(frame:NSRect(x:20,y:20,width:360,height:100))
                root.setAccessibilityElement(true); root.setAccessibilityRole(.group)
                root.setAccessibilityIdentifier("scale-root")
                var groups: [NSAccessibilityElement] = []
                for start in stride(from:0,to:count,by:100) {
                    let group = NSAccessibilityElement()
                    group.setAccessibilityRole(.group); group.setAccessibilityParent(root)
                    group.setAccessibilityFrameInParentSpace(root.bounds)
                    var leaves: [NSAccessibilityElement] = []
                    for _ in start..<min(count,start+100) {
                        let leaf = NSAccessibilityElement()
                        leaf.setAccessibilityRole(.staticText); leaf.setAccessibilityLabel("Owned leaf")
                        leaf.setAccessibilityParent(group)
                        leaf.setAccessibilityFrameInParentSpace(root.bounds)
                        leaves.append(leaf)
                    }
                    group.setAccessibilityChildren(leaves); groups.append(group)
                }
                root.setAccessibilityChildren(groups)
                window.contentView?.addSubview(root)
            }
            window.orderBack(nil); windows.append(window)
        }
        emit()
        Timer.scheduledTimer(withTimeInterval:0.2,repeats:true) { [weak self] _ in
            MainActor.assumeIsolated { self?.emit() }
        }
        Timer.scheduledTimer(withTimeInterval:90,repeats:false) { _ in NSApp.terminate(nil) }
    }
    func applicationDidBecomeActive(_ notification: Notification) { activations += 1 }
    func emit() {
        guard windows.count == 2 else { return }
        sequence += 1
        let record: [String:Any] = ["nonce":nonce,"seq":sequence,"leaf_count":count,"activations":activations,
            "A_key":windows[0].isKeyWindow,"B_key":windows[1].isKeyWindow,
            "A_mouse_downs":windows[0].mouseDowns,"B_mouse_downs":windows[1].mouseDowns,
            "route_errors":windows.reduce(0) { $0+$1.routeErrors }]
        if var bytes = try? JSONSerialization.data(withJSONObject:record) {
            bytes.append(10); FileHandle.standardOutput.write(bytes)
        }
    }
}

@main enum NativeReadinessScaleFixture {
    @MainActor static func main() {
        guard CommandLine.arguments.count == 3,
              let count = Int(CommandLine.arguments[1]), (0...6000).contains(count),
              CommandLine.arguments[2].count == 32 else { exit(2) }
        DispatchQueue.global().asyncAfter(deadline:.now()+95) { exit(0) }
        let app = NSApplication.shared; app.setActivationPolicy(.accessory)
        let delegate = ScaleDelegate(count:count,nonce:CommandLine.arguments[2])
        app.delegate = delegate
        withExtendedLifetime(delegate) { app.run() }
    }
}
