import AppKit
let app=NSApplication.shared
app.setActivationPolicy(.accessory)
let output=CommandLine.arguments[1]
let expected="owned-Synthetic-47!"
let window=NSWindow(contentRect:NSRect(x:120,y:120,width:400,height:220),styleMask:[.titled,.closable],backing:.buffered,defer:false)
window.title="Tacto owned credential receiving test"
window.isReleasedWhenClosed=false
let password=NSSecureTextField(frame:NSRect(x:25,y:145,width:340,height:28))
password.setAccessibilityIdentifier("password")
let confirmation=NSSecureTextField(frame:NSRect(x:25,y:95,width:340,height:28))
confirmation.setAccessibilityIdentifier("confirm-password")
var checks=0
func report() {
    let state:[String:Any] = ["password_matches":password.stringValue==expected,"confirmation_matches":confirmation.stringValue==expected,"checks":checks]
    try! JSONSerialization.data(withJSONObject:state).write(to:URL(fileURLWithPath:output),options:.atomic)
}
final class Receiver:NSObject {
    @objc func check(_ sender:Any?) {checks += 1;report()}
}
let receiver=Receiver()
let button=NSButton(title:"Check fields",target:receiver,action:#selector(Receiver.check(_:)))
button.frame=NSRect(x:25,y:35,width:160,height:32)
button.setAccessibilityIdentifier("check-fields")
for view in [password,confirmation,button] {window.contentView!.addSubview(view)}
window.orderBack(nil)
Timer.scheduledTimer(withTimeInterval:0.05,repeats:true) {_ in report()}
Timer.scheduledTimer(withTimeInterval:120,repeats:false) {_ in app.terminate(nil)}
report();app.run()
