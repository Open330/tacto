import AppKit
import Foundation

// Owned, disposable multi-stage task. The private oracle is host-only evidence,
// not an interface supplied to the agent. No user files or remote effects.
@MainActor final class RepeatDelegate: NSObject, NSApplicationDelegate {
    let directory: URL
    var window: NSWindow!
    var fields: [String:NSTextField] = [:]
    var stage: NSTextField!, preview: NSTextField!, task: NSTextField!
    var bonus: NSButton!, compute: NSButton!
    var sequence = 0, job = "", phase = "entry", total = 0, version = 1
    var commits: [[String:Any]] = []
    init(_ path: String) { directory = URL(fileURLWithPath:path) }
    func label(_ title:String,_ id:String,_ y:CGFloat) -> NSTextField {
        let view=NSTextField(labelWithString:title);view.frame=NSRect(x:20,y:y,width:530,height:28)
        view.setAccessibilityIdentifier(id);window.contentView?.addSubview(view);return view
    }
    func button(_ title:String,_ id:String,_ x:CGFloat,_ action:Selector) -> NSButton {
        let view=NSButton(title:title,target:self,action:action)
        view.frame=NSRect(x:x,y:40,width:160,height:32);view.setAccessibilityIdentifier(id)
        window.contentView?.addSubview(view);return view
    }
    func applicationDidFinishLaunching(_ notification:Notification) {
        window=NSWindow(contentRect:NSRect(x:220,y:220,width:580,height:340),
                        styleMask:[.titled,.closable],backing:.buffered,defer:false)
        window.title="TACTO Repeat Benchmark";window.isReleasedWhenClosed=false
        task=label("Waiting for owned task","bench-task",290)
        for (i,name) in ["amount","factor"].enumerated() {
            _=label(name,"caption-"+name,CGFloat(245-i*45))
            let view=NSTextField(string:"0");view.frame=NSRect(x:160,y:CGFloat(245-i*45),width:200,height:28)
            view.setAccessibilityIdentifier("bench-"+name);window.contentView?.addSubview(view);fields[name]=view
        }
        bonus=NSButton(checkboxWithTitle:"Include bonus 5",target:nil,action:nil)
        bonus.frame=NSRect(x:20,y:155,width:240,height:30);bonus.setAccessibilityIdentifier("bench-bonus")
        window.contentView?.addSubview(bonus)
        stage=label("entry","bench-stage",115);preview=label("pending","bench-preview",80)
        _=button("Review","bench-review",20,#selector(review))
        compute=button("Compute","bench-compute",200,#selector(calculate))
        _=button("Finish once","bench-finish",380,#selector(finish))
        window.orderBack(nil)
        Timer.scheduledTimer(withTimeInterval:0.05,repeats:true) { [weak self] _ in
            MainActor.assumeIsolated { self?.poll() }
        }
        DispatchQueue.global().asyncAfter(deadline:.now()+3600) { exit(3) }
    }
    @objc func review() { if phase == "entry" { phase="review";publish() } }
    @objc func calculate() {
        if phase == "review", let a=Int(fields["amount"]!.stringValue),let b=Int(fields["factor"]!.stringValue) {
            total=a*b+(bonus.state == .on ? 5:0);phase="computed";publish()
        }
    }
    @objc func finish() {
        if phase == "computed" {
            commits.append(["job":job,"amount":fields["amount"]!.stringValue,
                            "factor":fields["factor"]!.stringValue,"bonus":bonus.state == .on,
                            "result":total]);phase="done";publish()
        }
    }
    func poll() {
        let path=directory.appendingPathComponent("control.json")
        if let data=try? Data(contentsOf:path),let d=try? JSONSerialization.jsonObject(with:data) as? [String:Any],
           let n=d["sequence"] as? Int,n>sequence,let next=d["job"] as? String {
            sequence=n;job=next;phase="entry";total=0
            version=(d["changed_control"] as? Bool == true) ? 2:1
            fields.values.forEach { $0.stringValue="0" };bonus.state = .off
            compute.title=version==1 ? "Compute":"Recalculate"
            compute.setAccessibilityIdentifier(version==1 ? "bench-compute":"bench-compute-v2")
        }
        publish()
    }
    func publish() {
        stage.stringValue=phase;preview.stringValue=(phase=="computed" || phase=="done") ? String(total):"pending"
        task.stringValue="Job "+job+" — amount × factor, plus 5 only when requested"
        let state:[String:Any]=["sequence":sequence,"job":job,"phase":phase,
            "amount":fields["amount"]!.stringValue,"factor":fields["factor"]!.stringValue,
            "bonus":bonus.state == .on,"result":total,"commits":commits,
            "active":NSApplication.shared.isActive,"control_version":version]
        if let data=try? JSONSerialization.data(withJSONObject:state,options:[.sortedKeys]) {
            try? data.write(to:directory.appendingPathComponent("oracle.json"),options:.atomic)
        }
    }
}
@main enum RepeatBenchmark {
    @MainActor static func main() {
        guard CommandLine.arguments.count==2 else { exit(2) }
        let app=NSApplication.shared;app.setActivationPolicy(.accessory)
        let delegate=RepeatDelegate(CommandLine.arguments[1]);app.delegate=delegate
        withExtendedLifetime(delegate) { app.run() }
    }
}
