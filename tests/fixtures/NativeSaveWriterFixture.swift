// Owned producer: writes only once, only from the NSSavePanel OK callback.
import AppKit
import Foundation
import CryptoKit
import Darwin

@MainActor final class SaveWriter: NSObject, NSApplicationDelegate {
    let directory: String, filename: String, nonce: String
    var window: NSWindow?, panel: NSSavePanel?
    var consumed = false
    init(_ directory: String, _ filename: String, _ nonce: String) {
        self.directory=directory; self.filename=filename; self.nonce=nonce
    }
    func emit(_ value: [String:Any]) {
        if let data=try? JSONSerialization.data(withJSONObject:value) {
            print(String(decoding:data,as:UTF8.self)); fflush(stdout)
        }
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        let window=NSWindow(contentRect:NSRect(x:120,y:120,width:500,height:160),
                            styleMask:[.titled,.closable],backing:.buffered,defer:false)
        self.window=window; window.title="NLC Owned Save Writer"; window.isReleasedWhenClosed=false
        window.orderBack(nil)
        let panel=NSSavePanel(); self.panel=panel
        panel.directoryURL=URL(fileURLWithPath:directory,isDirectory:true)
        panel.nameFieldStringValue=filename
        let accessory=NSView(frame:NSRect(x:0,y:0,width:420,height:64))
        let format=NSPopUpButton(frame:NSRect(x:0,y:32,width:280,height:26),pullsDown:false)
        format.addItems(withTitles:["Rich Text Document"])
        format.setAccessibilityIdentifier("ContentTypesPopup")
        accessory.addSubview(format)
        let keep=NSButton(checkboxWithTitle:"Keep changes in original document",target:nil,action:nil)
        keep.frame=NSRect(x:0,y:0,width:400,height:26)
        keep.setAccessibilityIdentifier("KeepChangesButton");keep.state = .off
        accessory.addSubview(keep);panel.accessoryView=accessory
        panel.beginSheetModal(for:window) { response in
            guard !self.consumed else { return }; self.consumed=true
            guard response == .OK, let selected=panel.url else {
                self.emit(["callback":"refused","reason":"response","nonce":self.nonce]); NSApplication.shared.terminate(nil); return
            }
            // The new leaf does not exist yet: realpath must resolve its
            // existing parent, including macOS /tmp -> /private/tmp.
            guard let parent=realpath(selected.deletingLastPathComponent().path,nil) else {
                self.emit(["callback":"refused","reason":"destination","nonce":self.nonce]);NSApplication.shared.terminate(nil);return
            }
            let canonical=URL(fileURLWithPath:String(cString:parent)).appendingPathComponent(selected.lastPathComponent)
            free(parent)
            guard canonical.path == URL(fileURLWithPath:self.directory).appendingPathComponent(self.filename).standardizedFileURL.path else {
                self.emit(["callback":"refused","reason":"destination","selected":canonical.path,"expected":self.directory+"/"+self.filename,"nonce":self.nonce]); NSApplication.shared.terminate(nil); return
            }
            guard format.titleOfSelectedItem == "Rich Text Document", keep.state == .off else {
                self.emit(["callback":"refused","reason":"format","nonce":self.nonce]);NSApplication.shared.terminate(nil);return
            }
            let bytes=Data(("{\\rtf1\\ansi NLC owned save "+self.nonce+"}").utf8)
            do {
                try bytes.write(to:canonical,options:.withoutOverwriting)
                self.emit(["callback":"written","nonce":self.nonce,"path":canonical.path,
                           "sha256":SHA256.hash(data:bytes).map{String(format:"%02x",$0)}.joined(),
                           "response":response.rawValue])
            } catch { self.emit(["callback":"write_failed","nonce":self.nonce]) }
            NSApplication.shared.terminate(nil)
        }
        emit(["callback":"ready","nonce":nonce])
    }
}
@main enum NativeSaveWriterFixture {
    @MainActor static func main() {
        let args=CommandLine.arguments
        guard args.count==8 else { exit(2) }
        let app=NSApplication.shared; app.setActivationPolicy(.accessory)
        let delegate=SaveWriter(args[1],args[2],args[3]); app.delegate=delegate
        DispatchQueue.global().asyncAfter(deadline:.now()+30) { exit(3) }
        withExtendedLifetime(delegate) { app.run() }
    }
}
