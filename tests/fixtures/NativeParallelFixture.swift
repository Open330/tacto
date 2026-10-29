// Owned background AX concurrency fixture; writes only its supplied test report.
import AppKit
let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let output = CommandLine.arguments[1]
let label = CommandLine.arguments[2]
let window = NSWindow(contentRect:NSRect(x:120,y:180,width:400,height:180),styleMask:[.titled,.closable],backing:.buffered,defer:false)
window.title = label; window.isReleasedWhenClosed = false
let field = NSTextField(string: "initial-"+label)
field.frame = NSRect(x:20,y:85,width:350,height:36)
field.setAccessibilityIdentifier("parallel-editor")
window.contentView!.addSubview(field)
window.orderBack(nil)
var started: Double = 0, ended: Double = 0
func report() {
 let data = try! JSONSerialization.data(withJSONObject:["started":started,"ended":ended,"text":field.stringValue,"pid":getpid(),"front":NSWorkspace.shared.frontmostApplication?.processIdentifier as Any? ?? -1])
 try! data.write(to:URL(fileURLWithPath:output),options:.atomic)
}
final class Receiver: NSObject {
    @objc func press(_ sender: Any?) {
        started = Date().timeIntervalSince1970
        Thread.sleep(forTimeInterval:0.8)
        field.stringValue = "pressed-"+label
        ended = Date().timeIntervalSince1970
        report()
    }
}
let receiver = Receiver()
let button = NSButton(title:"Parallel press",target:receiver,action:#selector(Receiver.press(_:)))
button.frame = NSRect(x:20,y:25,width:200,height:35)
button.setAccessibilityIdentifier("parallel-press")
window.contentView!.addSubview(button)
report()
Timer.scheduledTimer(withTimeInterval:0.05,repeats:true) { _ in report() }
Timer.scheduledTimer(withTimeInterval:300,repeats:false) { _ in app.terminate(nil) }
app.run()
