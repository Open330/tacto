// Read-only modality inventory fixture. No input injection or app activation.
import AppKit

@MainActor final class ModalityDelegate: NSObject, NSApplicationDelegate {
    let mode: String, nonce: String
    var windows: [NSWindow] = []
    var panel: NSWindow?
    var nestedPanel: NSWindow?
    var popover: NSPopover?
    var alert: NSAlert?
    var anchor: NSView?
    var sequence = 0
    init(mode: String, nonce: String) { self.mode=mode;self.nonce=nonce }
    func applicationDidFinishLaunching(_ notification: Notification) {
        for (index,suffix) in ["A","B"].enumerated() {
            let window=NSWindow(contentRect:NSRect(x:180+index*450,y:230,width:420,height:220),
                styleMask:[.titled,.closable],backing:.buffered,defer:false)
            window.title="TACTO Modality \(suffix)";window.isReleasedWhenClosed=false
            let field=NSTextField(string:"Owned")
            field.frame=NSRect(x:20,y:150,width:350,height:30)
            window.contentView?.addSubview(field);window.makeFirstResponder(field)
            if suffix=="B" {anchor=field}
            if suffix=="B" && mode=="deep_sheet" {
                let root=NSView(frame:NSRect(x:20,y:20,width:350,height:100))
                root.setAccessibilityElement(true);root.setAccessibilityRole(.group)
                root.setAccessibilityIdentifier("modality-deep-root")
                var parent: Any=root
                var first: NSAccessibilityElement?
                for depth in 0..<5 {
                    let group=NSAccessibilityElement()
                    group.setAccessibilityRole(depth==4 ? .sheet:.group)
                    group.setAccessibilityParent(parent)
                    group.setAccessibilityFrameInParentSpace(root.bounds)
                    group.setAccessibilityIdentifier(depth==4 ? "modality-deep-sheet":"modality-group-\(depth)")
                    if let element=parent as? NSAccessibilityElement {element.setAccessibilityChildren([group])}
                    else {first=group}
                    parent=group
                }
                root.setAccessibilityChildren(first.map {[$0]} ?? [])
                window.contentView?.addSubview(root)
            }
            window.orderBack(nil);windows.append(window)
        }
        if mode=="window_sheet" || mode=="nested_sheet" {
            let sheet=NSWindow(contentRect:NSRect(x:0,y:0,width:260,height:90),
                styleMask:[.titled],backing:.buffered,defer:false)
            sheet.title="Owned Sheet";sheet.isReleasedWhenClosed=false
            let label=NSTextField(labelWithString:"Owned modal inventory")
            label.frame=NSRect(x:15,y:25,width:220,height:30);sheet.contentView?.addSubview(label)
            panel=sheet;windows[1].beginSheet(sheet)
            if mode=="nested_sheet" {
                let nested=NSWindow(contentRect:NSRect(x:0,y:0,width:220,height:70),
                    styleMask:[.titled],backing:.buffered,defer:false)
                nested.title="Owned Nested Sheet";nested.isReleasedWhenClosed=false
                nestedPanel=nested;sheet.beginCriticalSheet(nested)
            }
        }
        if mode=="panel" {
            let floating=NSPanel(contentRect:NSRect(x:230,y:270,width:250,height:100),
                styleMask:[.titled,.utilityWindow,.nonactivatingPanel],backing:.buffered,defer:false)
            floating.title="Owned Floating Panel";floating.isReleasedWhenClosed=false
            floating.isFloatingPanel=true;floating.worksWhenModal=true
            panel=floating;floating.orderBack(nil)
        }
        if mode.hasPrefix("popover_"), let anchor=anchor {
            let popup=NSPopover();popup.behavior=mode=="popover_transient" ? .transient:.semitransient
            let controller=NSViewController();controller.view=NSView(frame:NSRect(x:0,y:0,width:220,height:90))
            popup.contentViewController=controller;popover=popup
            popup.show(relativeTo:anchor.bounds,of:anchor,preferredEdge:.minY)
        }
        let timer=Timer(timeInterval:0.2,repeats:true) { [weak self] _ in
            MainActor.assumeIsolated {self?.emit()}
        }
        RunLoop.main.add(timer,forMode:.common);RunLoop.main.add(timer,forMode:.modalPanel)
        let stop=Timer(timeInterval:60,repeats:false) {_ in
            NSApp.abortModal();NSApp.terminate(nil)
        }
        RunLoop.main.add(stop,forMode:.common);RunLoop.main.add(stop,forMode:.modalPanel)
        emit()
        if mode=="app_modal" {
            let modal=NSAlert();modal.messageText="Owned Application Modal";alert=modal
            DispatchQueue.main.async {NSApp.runModal(for:modal.window)}
        }
    }
    func emit() {
        sequence+=1
        let record:[String:Any]=["nonce":nonce,"seq":sequence,"mode":mode,
            "appkit_sheet_attached":windows.count==2 && windows[1].attachedSheet != nil,
            "appkit_modal_window":NSApp.modalWindow != nil,
            "custom_sheet_is_synthetic":mode=="deep_sheet",
            "panel_visible":mode=="panel" && panel?.isVisible==true,
            "popover_shown":popover?.isShown==true,
            "nested_sheet_attached":panel?.attachedSheet===nestedPanel && nestedPanel != nil]
        if var bytes=try? JSONSerialization.data(withJSONObject:record) {
            bytes.append(10);FileHandle.standardOutput.write(bytes)
        }
    }
}

@main enum NativeModalityFixture {
    @MainActor static func main() {
        guard CommandLine.arguments.count==3,
              ["clear","window_sheet","deep_sheet","app_modal","panel","popover_transient","popover_semitransient","nested_sheet"].contains(CommandLine.arguments[1]),
              CommandLine.arguments[2].count==32 else {exit(2)}
        DispatchQueue.global().asyncAfter(deadline:.now()+65) {exit(0)}
        let app=NSApplication.shared;app.setActivationPolicy(.accessory)
        let delegate=ModalityDelegate(mode:CommandLine.arguments[1],nonce:CommandLine.arguments[2])
        app.delegate=delegate;withExtendedLifetime(delegate) {app.run()}
    }
}
