// Receiving-side witness: all state is updated by AppKit mouse handlers.
import AppKit
@MainActor final class DragCanvas:NSView {
    var events:[[String:Any]]=[]
    let receipt:URL
    init(frame:NSRect,receipt:URL) { self.receipt=receipt;super.init(frame:frame) }
    required init?(coder:NSCoder) { fatalError() }
    override var isFlipped:Bool {true}
    override var acceptsFirstResponder:Bool {true}
    override func acceptsFirstMouse(for event:NSEvent?)->Bool {true}
    override func draw(_ dirty:NSRect) {NSColor.systemBlue.setFill();bounds.fill()}
    func record(_ event:NSEvent,_ kind:String) {
        let point=convert(event.locationInWindow,from:nil)
        events.append(["kind":kind,"x":point.x,"y":point.y,"window":event.windowNumber,
                       "number":event.eventNumber,"pressure":event.pressure,"timestamp":event.timestamp])
        if let data=try? JSONSerialization.data(withJSONObject:events) {try? data.write(to:receipt,options:.atomic)}
    }
    override func mouseDown(with event:NSEvent) {record(event,"down")}
    override func mouseDragged(with event:NSEvent) {record(event,"drag")}
    override func mouseUp(with event:NSEvent) {record(event,"up")}
}
@MainActor final class DragDelegate:NSObject,NSApplicationDelegate {
    var window:NSWindow!
    var slider:NSSlider!
    let root:URL
    init(_ root:URL) {self.root=root}
    func applicationDidFinishLaunching(_ notification:Notification) {
        window=NSWindow(contentRect:NSRect(x:200,y:200,width:480,height:300),styleMask:[.titled,.closable],backing:.buffered,defer:false)
        window.title="Tacto Drag Fixture"
        window.isReleasedWhenClosed=false
        let canvas=DragCanvas(frame:NSRect(x:20,y:100,width:440,height:180),receipt:root.appendingPathComponent("events.json"))
        window.contentView?.addSubview(canvas)
        slider=NSSlider(value:20,minValue:0,maxValue:100,target:self,action:#selector(changed))
        slider.frame=NSRect(x:30,y:30,width:400,height:30);slider.isContinuous=true
        slider.setAccessibilityIdentifier("drag-slider")
        window.contentView?.addSubview(slider)
        window.orderBack(nil);changed()
        Timer.scheduledTimer(withTimeInterval:300,repeats:false) {_ in NSApplication.shared.terminate(nil)}
    }
    @objc func changed() {try? String(slider.doubleValue).write(to:root.appendingPathComponent("slider.txt"),atomically:true,encoding:.utf8)}
}
@main enum DragFixture {
    @MainActor static func main() {
        let app=NSApplication.shared;app.setActivationPolicy(.accessory)
        let delegate=DragDelegate(URL(fileURLWithPath:CommandLine.arguments[1]));app.delegate=delegate
        app.run();withExtendedLifetime(delegate) {}
    }
}
