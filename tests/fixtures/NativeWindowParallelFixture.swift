// Four owned windows in one process. Every edit is reported independently.
import AppKit
let app=NSApplication.shared
app.setActivationPolicy(.accessory)
let output=CommandLine.arguments[1]
final class WitnessWindow:NSWindow {
    var inputs=0
    override func sendEvent(_ event:NSEvent) {
        if [.keyDown,.keyUp,.leftMouseDown,.leftMouseUp].contains(event.type) { inputs += 1 }
        super.sendEvent(event)
    }
}
var windows:[WitnessWindow]=[],fields:[NSTextField]=[],receivers:[Receiver]=[],presses=[Int](repeating:0,count:4)
final class Receiver:NSObject {
    let index:Int
    init(_ index:Int) { self.index=index }
    @objc func press(_ sender:Any?) {
        presses[index] += 1; fields[index].stringValue += "|pressed"; report()
    }
}
func report() {
    let rows=windows.enumerated().map { i,w -> [String:Any] in
        ["index":i,"window_id":w.windowNumber,"value":fields[i].stringValue,"presses":presses[i],"input_events":w.inputs]
    }
    let bytes=try! JSONSerialization.data(withJSONObject:["windows":rows,"pid":getpid(),"front":NSWorkspace.shared.frontmostApplication?.processIdentifier ?? -1])
    try! bytes.write(to:URL(fileURLWithPath:output),options:.atomic)
}
for i in 0..<4 {
    let window=WitnessWindow(contentRect:NSRect(x:80+i*35,y:100+i*35,width:360,height:170),styleMask:[.titled,.closable],backing:.buffered,defer:false)
    window.title="Tacto scoped window \(i)";window.isReleasedWhenClosed=false
    let field=NSTextField(string:"initial-\(i)");field.frame=NSRect(x:20,y:90,width:310,height:30)
    field.setAccessibilityIdentifier("window-editor")
    let receiver=Receiver(i)
    let button=NSButton(title:"Window press",target:receiver,action:#selector(Receiver.press(_:)))
    button.frame=NSRect(x:20,y:30,width:200,height:35);button.setAccessibilityIdentifier("window-press")
    window.contentView!.addSubview(field);window.contentView!.addSubview(button)
    windows.append(window);fields.append(field);receivers.append(receiver);window.orderBack(nil)
}
report()
Timer.scheduledTimer(withTimeInterval:0.05,repeats:true) { _ in report() }
Timer.scheduledTimer(withTimeInterval:600,repeats:false) { _ in app.terminate(nil) }
app.run()
