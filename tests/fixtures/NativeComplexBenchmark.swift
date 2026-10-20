import AppKit
import Foundation

@MainActor final class ComplexDelegate: NSObject, NSApplicationDelegate {
    let directory: URL
    var window: NSWindow!
    var fields: [String:NSTextField] = [:]
    var stage: NSTextField!, preview: NSTextField!, count: NSTextField!
    var bonus: NSButton!, compute: NSButton!, audit: NSButton!, finishButton: NSButton!, twin: NSButton!
    var sequence=0, job="", phase="entry", mode="normal", total=0, version=1
    var commits: [[String:Any]]=[], actions: [[String:Any]]=[]
    init(_ path:String) { directory=URL(fileURLWithPath:path) }
    func label(_ text:String,_ id:String,_ y:CGFloat) -> NSTextField {
        let view=NSTextField(labelWithString:text);view.frame=NSRect(x:20,y:y,width:580,height:25)
        view.setAccessibilityIdentifier(id);window.contentView?.addSubview(view);return view
    }
    func button(_ text:String,_ id:String,_ x:CGFloat,_ action:Selector) -> NSButton {
        let view=NSButton(title:text,target:self,action:action);view.frame=NSRect(x:x,y:30,width:130,height:30)
        view.setAccessibilityIdentifier(id);window.contentView?.addSubview(view);return view
    }
    func applicationDidFinishLaunching(_ notification:Notification) {
        window=NSWindow(contentRect:NSRect(x:200,y:180,width:610,height:470),styleMask:[.titled,.closable],backing:.buffered,defer:false)
        window.title="TACTO Complex Benchmark";window.isReleasedWhenClosed=false
        for (i,name) in ["amount","factor","discount","reference","audit-input"].enumerated() {
            let y=CGFloat(425-i*45);_=label(name,"caption-"+name,y)
            let field=NSTextField(string:"0");field.frame=NSRect(x:160,y:y,width:420,height:26)
            field.setAccessibilityIdentifier("complex-"+name);window.contentView?.addSubview(field);fields[name]=field
        }
        bonus=NSButton(checkboxWithTitle:"Include bonus 5",target:nil,action:nil);bonus.frame=NSRect(x:20,y:175,width:240,height:28)
        bonus.setAccessibilityIdentifier("complex-bonus");window.contentView?.addSubview(bonus)
        stage=label("entry","complex-stage",135);preview=label("pending","complex-preview",105);count=label("0","complex-commits",75)
        _=button("Review","complex-review",20,#selector(review))
        compute=button("Compute","complex-compute",165,#selector(calculate))
        audit=button("Audit summary","complex-audit",310,#selector(checkSummary))
        finishButton=button("Finish once","complex-finish",455,#selector(finish))
        twin=button("Audit summary","complex-audit",310,#selector(checkSummary));twin.frame.origin.y=2;twin.isHidden=true
        window.orderBack(nil)
        Timer.scheduledTimer(withTimeInterval:0.05,repeats:true) { [weak self] _ in MainActor.assumeIsolated { self?.poll() } }
        DispatchQueue.global().asyncAfter(deadline:.now()+3600) { exit(3) }
    }
    func record(_ operation:String) { actions.append(["sequence":sequence,"job":job,"operation":operation,"phase":phase]) }
    @objc func review() {
        record("review");guard phase=="entry" else { publish();return }
        if mode=="delayed" {
            phase="reviewing";publish();let n=sequence
            DispatchQueue.main.asyncAfter(deadline:.now()+0.25) { if self.sequence==n { self.phase="review";self.publish() } }
        } else { phase="review";publish() }
    }
    @objc func calculate() {
        record("compute")
        guard phase=="review",let a=Int(fields["amount"]!.stringValue),let b=Int(fields["factor"]!.stringValue),let d=Int(fields["discount"]!.stringValue) else { publish();return }
        total=a*b+(bonus.state == .on ? 5:0)-d
        if mode=="delayed" {
            phase="calculating";publish();let n=sequence
            DispatchQueue.main.asyncAfter(deadline:.now()+0.25) { if self.sequence==n { self.phase="computed";self.publish() } }
        } else { phase="computed";publish() }
    }
    @objc func checkSummary() {
        record("audit")
        if phase=="computed",fields["audit-input"]!.stringValue==String(total),!fields["reference"]!.stringValue.isEmpty { phase="audited" }
        publish()
    }
    @objc func finish() {
        record("finish")
        if phase=="audited" {
            commits.append(["sequence":sequence,"job":job,"amount":fields["amount"]!.stringValue,"factor":fields["factor"]!.stringValue,
                            "discount":fields["discount"]!.stringValue,"reference":fields["reference"]!.stringValue,"result":total,"bonus":bonus.state == .on])
            phase=mode=="unknown-finish" ? "committed":"done"
        }
        publish()
    }
    func poll() {
        if let data=try? Data(contentsOf:directory.appendingPathComponent("control.json")),let d=try? JSONSerialization.jsonObject(with:data) as? [String:Any],
           let n=d["sequence"] as? Int,n>sequence,let next=d["job"] as? String {
            sequence=n;job=next;mode=d["mode"] as? String ?? "normal";version=d["version"] as? Int ?? 1;phase="entry";total=0
            fields.values.forEach { $0.stringValue="0" };bonus.state = .off
            compute.title=version==1 ? "Compute":"Recalculate";compute.setAccessibilityIdentifier(version==1 ? "complex-compute":"complex-compute-v2")
            audit.title=version==1 ? "Audit summary":"Recheck summary";audit.setAccessibilityIdentifier(version==1 ? "complex-audit":"complex-audit-v2")
            twin.title=audit.title;twin.setAccessibilityIdentifier(version==1 ? "complex-audit":"complex-audit-v2");twin.isHidden=mode != "ambiguous"
            finishButton.setAccessibilityIdentifier(mode=="triple-drift" ? "complex-finish-v2":"complex-finish")
        }
        publish()
    }
    func publish() {
        stage.stringValue=phase;preview.stringValue=["computed","audited","done","committed"].contains(phase) ? String(total):"pending"
        let own=commits.filter { ($0["sequence"] as? Int)==sequence };count.stringValue=String(own.count)
        let state:[String:Any]=["sequence":sequence,"job":job,"phase":phase,"mode":mode,"version":version,"result":total,
            "amount":fields["amount"]!.stringValue,"factor":fields["factor"]!.stringValue,"discount":fields["discount"]!.stringValue,
            "reference":fields["reference"]!.stringValue,"audit_input":fields["audit-input"]!.stringValue,"bonus":bonus.state == .on,
            "commits":commits,"actions":actions,"active":NSApplication.shared.isActive]
        if let data=try? JSONSerialization.data(withJSONObject:state,options:[.sortedKeys]) { try? data.write(to:directory.appendingPathComponent("oracle.json"),options:.atomic) }
    }
}
@main enum ComplexBenchmark {
    @MainActor static func main() {
        guard CommandLine.arguments.count==2 else { exit(2) }
        let app=NSApplication.shared;app.setActivationPolicy(.accessory)
        let delegate=ComplexDelegate(CommandLine.arguments[1]);app.delegate=delegate
        withExtendedLifetime(delegate) { app.run() }
    }
}
