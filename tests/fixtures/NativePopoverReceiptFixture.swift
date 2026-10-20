// Owned receiver for signed window-bound clicks. Never activates the app.
import AppKit

@MainActor final class ReceiptView: NSView {
    var downs=0, ups=0, firstMouse=false
    override func acceptsFirstMouse(for event:NSEvent?) -> Bool {firstMouse}
    override func mouseDown(with event:NSEvent) {downs+=1}
    override func mouseUp(with event:NSEvent) {ups+=1}
    override func draw(_ rect:NSRect) {NSColor.systemBlue.setFill();bounds.fill()}
}
@MainActor final class ReceiptWindow: NSWindow {
    var events:[[String:Any]]=[]
    var popupShown:()->Bool = {false}
    weak var receiver:ReceiptView?
    var recordEvent:()->Void = {}
    override func sendEvent(_ event:NSEvent) {
        if event.type == .leftMouseDown || event.type == .leftMouseUp {
            if events.count<64 {events.append(["phase":event.type == .leftMouseDown ? "down":"up",
                "window_matches":event.windowNumber==windowNumber,"popover_shown":popupShown(),
                "receiver_hit":contentView?.hitTest(event.locationInWindow) === receiver])}
            recordEvent()
        }
        super.sendEvent(event)
    }
}
@MainActor final class ReceiptDelegate:NSObject,NSApplicationDelegate,NSPopoverDelegate {
    let mode:String, nonce:String, targetIndex:Int, firstMouse:Bool
    var windows:[ReceiptWindow]=[], receivers:[ReceiptView]=[]
    var popup:NSPopover?, closes=0, sequence=0, activations=0
    init(mode:String,nonce:String,targetIndex:Int,firstMouse:Bool) {self.mode=mode;self.nonce=nonce;self.targetIndex=targetIndex;self.firstMouse=firstMouse}
    func applicationDidFinishLaunching(_ notification:Notification) {
        for index in [targetIndex,1-targetIndex] {
            let suffix=["A","B"][index]
            let window=ReceiptWindow(contentRect:NSRect(x:180+index*450,y:230,width:420,height:220),
                styleMask:[.titled,.closable],backing:.buffered,defer:false)
            window.title="TACTO Popover Receipt \(suffix)";window.isReleasedWhenClosed=false
            let receiver=ReceiptView(frame:NSRect(x:20,y:60,width:200,height:60))
            receiver.firstMouse=firstMouse
            receiver.setAccessibilityElement(true);receiver.setAccessibilityRole(.group)
            receiver.setAccessibilityIdentifier("receipt-\(suffix)")
            window.contentView?.addSubview(receiver);window.receiver=receiver
            window.recordEvent={ [weak self] in self?.emit() }
            window.popupShown={ [weak self] in self?.popup?.isShown==true }
            window.orderBack(nil)
            windows.insert(window,at:index==0 ? 0:windows.count)
            receivers.insert(receiver,at:index==0 ? 0:receivers.count)
        }
        if mode != "clear" {
            let anchor=NSTextField(labelWithString:"Owned popover anchor")
            anchor.frame=NSRect(x:20,y:170,width:200,height:25);windows[1].contentView?.addSubview(anchor)
            let popup=NSPopover();popup.behavior=mode=="transient" ? .transient:.semitransient
            let controller=NSViewController();controller.view=NSView(frame:NSRect(x:0,y:0,width:220,height:80))
            popup.contentViewController=controller;popup.delegate=self;self.popup=popup
            popup.show(relativeTo:anchor.bounds,of:anchor,preferredEdge:.maxY)
        }
        emit()
        Timer.scheduledTimer(withTimeInterval:0.1,repeats:true) { [weak self] _ in
            MainActor.assumeIsolated {self?.emit()}
        }
        Timer.scheduledTimer(withTimeInterval:60,repeats:false) {_ in NSApp.terminate(nil)}
    }
    func applicationDidBecomeActive(_ notification:Notification) {activations+=1}
    func popoverDidClose(_ notification:Notification) {closes+=1}
    func emit() {
        sequence+=1
        let record:[String:Any]=["nonce":nonce,"seq":sequence,"mode":mode,"accepts_first_mouse":firstMouse,
            "popover_shown":popup?.isShown==true,"popover_window_visible":popup?.contentViewController?.view.window?.isVisible==true,
            "popover_closes":closes,"activations":activations,"window_keys":windows.map {$0.isKeyWindow},
            "receivers":receivers.map {["downs":$0.downs,"ups":$0.ups]},
            "window_events":windows.map {$0.events}]
        if var data=try? JSONSerialization.data(withJSONObject:record) {
            data.append(10);FileHandle.standardOutput.write(data)
        }
    }
}
@main enum NativePopoverReceiptFixture {
    @MainActor static func main() {
        guard CommandLine.arguments.count==5,["clear","transient","semitransient"].contains(CommandLine.arguments[1]),
            ["A","B"].contains(CommandLine.arguments[2]),["yes","no"].contains(CommandLine.arguments[3]),CommandLine.arguments[4].count==32 else {exit(2)}
        DispatchQueue.global().asyncAfter(deadline:.now()+65) {exit(0)}
        let app=NSApplication.shared;app.setActivationPolicy(.accessory)
        let delegate=ReceiptDelegate(mode:CommandLine.arguments[1],nonce:CommandLine.arguments[4],targetIndex:CommandLine.arguments[2]=="A" ? 0:1,firstMouse:CommandLine.arguments[3]=="yes")
        app.delegate=delegate;withExtendedLifetime(delegate) {app.run()}
    }
}
