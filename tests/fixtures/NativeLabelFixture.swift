import AppKit
let app=NSApplication.shared
app.setActivationPolicy(.accessory)
let output=CommandLine.arguments[1]
let window=NSWindow(contentRect:NSRect(x:100,y:100,width:650,height:260),styleMask:[.titled,.closable],backing:.buffered,defer:false)
window.title="Tacto owned multiline identity fixture";window.isReleasedWhenClosed=false
let result=NSTextField(labelWithString:"0,0");result.frame=NSRect(x:20,y:20,width:300,height:25);result.setAccessibilityIdentifier("label-result")
var counts=[0,0]
let titles=["미디어 크리에이터 아이콘\n\n크리에이터 공인, 콘텐츠 제작자, 아티스트 및 인플루언서에게 적합합니다.\nmedia_creator","미디어 크리에이터 아이콘\\n\\n크리에이터 공인, 콘텐츠 제작자, 아티스트 및 인플루언서에게 적합합니다.\\nmedia_creator"]
final class Receiver:NSObject {
 let index:Int
 init(_ index:Int){self.index=index}
 @objc func press(_ sender:Any?){counts[index] += 1;result.stringValue=counts.map(String.init).joined(separator:",");report()}
}
var receivers:[Receiver]=[]
func report(){try! JSONSerialization.data(withJSONObject:["counts":counts]).write(to:URL(fileURLWithPath:output),options:.atomic)}
for i in 0..<2 {
 let receiver=Receiver(i);receivers.append(receiver)
 let button=NSButton(title:titles[i],target:receiver,action:#selector(Receiver.press(_:)))
 button.setAccessibilityTitle(titles[i]);button.frame=NSRect(x:20,y:70+i*85,width:610,height:75);window.contentView!.addSubview(button)
}
window.contentView!.addSubview(result);window.orderBack(nil);report()
Timer.scheduledTimer(withTimeInterval:120,repeats:false){_ in app.terminate(nil)}
app.run()
