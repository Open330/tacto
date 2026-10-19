import AppKit

@MainActor final class ContinuationDelegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!, label: NSTextField!, count=0
    func applicationDidFinishLaunching(_ notification: Notification) {
        window=NSWindow(contentRect:NSRect(x:260,y:280,width:340,height:130),
            styleMask:[.titled,.closable],backing:.buffered,defer:false)
        window.title="NLC Owned Continuation";window.isReleasedWhenClosed=false
        let button=NSButton(title:"Continue once",target:self,action:#selector(continued))
        button.frame=NSRect(x:20,y:65,width:200,height:30)
        button.setAccessibilityIdentifier("continue-once");window.contentView?.addSubview(button)
        label=NSTextField(labelWithString:"count=0");label.frame=NSRect(x:20,y:20,width:280,height:25)
        label.setAccessibilityIdentifier("continuation-state");window.contentView?.addSubview(label)
        window.orderBack(nil)
    }
    @objc func continued() { count+=1;label.stringValue="count=\(count)" }
}
@main struct ContinuationFixture {
    @MainActor static func main() {
        let app=NSApplication.shared;app.setActivationPolicy(.accessory)
        let delegate=ContinuationDelegate();app.delegate=delegate
        DispatchQueue.global().asyncAfter(deadline:.now()+20) { exit(3) }
        withExtendedLifetime(delegate) { app.run() }
    }
}
