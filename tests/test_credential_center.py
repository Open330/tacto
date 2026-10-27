"""Native credential UI uses metadata previews and stdin-only local secrets."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

@unittest.skipUnless(sys.platform=='darwin','AppKit required')
class CredentialCenterTests(unittest.TestCase):
    def test_native_management_actions_and_tab_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp);(directory/'ActivityCenter.swift').write_text((ROOT/'setup/ActivityCenter.swift').read_text().replace('private ',''))
            (directory/'main.swift').write_text(r'''
import AppKit
import QuartzCore
let app=NSApplication.shared;app.setActivationPolicy(.prohibited)
final class Recorder {
 let lock=NSLock();var calls:[[String]]=[]
 func run(_ args:[String],_ data:Data?) throws -> [String:Any] {
  lock.lock();calls.append(args);lock.unlock()
  assert(!args.joined().contains("owned-ui-secret"))
  if args.first=="put" {assert(data==Data("owned-ui-secret\n".utf8))}
  if args.first=="inspect" {return ["target":"com.apple.Safari","origin":"https://example.test","level":"login","stages":2,"account_check":true,"review_digest":"owned-review-digest"]}
  if args.first=="list" {return ["grants":["owned-login"],"scopes":["owned-login":["target":"com.apple.Safari","origin":"https://example.test","level":"login","account_check":true]],"vaults":["local":["kind":"keychain"]]]}
  return ["status":"ok"]
 }
}
let recorder=Recorder();let center=CredentialCenter(runner:recorder.run)
func settle() {
 let deadline=Date().addingTimeInterval(3)
 while center.busy && Date()<deadline {RunLoop.current.run(until:Date().addingTimeInterval(0.01))}
 assert(!center.busy)
}
center.reload();settle();assert(center.grants.titleOfSelectedItem=="owned-login")
center.providerName.stringValue="local";center.registerProvider();settle()
center.secret.stringValue="owned-ui-secret";center.itemName.stringValue="owned-item";center.storeSecret();assert(center.secret.stringValue.isEmpty);settle()
center.review(URL(fileURLWithPath:"/owned-fixture.json"));settle();assert(center.savePlan.isEnabled)
center.grantName.stringValue="owned-login";center.level.selectItem(at:1);center.registerGrant();settle()
assert(recorder.calls.contains{ $0.contains("--review-digest") && $0.contains("owned-review-digest") && $0.contains("--fill-only") })
center.revoke();settle();assert(recorder.calls.contains(["revoke","owned-login"]))
let content=center.window!.contentView!;content.layoutSubtreeIfNeeded()
let tabs=content.subviews.compactMap{$0 as? NSTabView}.first!
let output=URL(fileURLWithPath:CommandLine.arguments[1]);try! FileManager.default.createDirectory(at:output,withIntermediateDirectories:true)
for i in 0..<tabs.numberOfTabViewItems {
 tabs.selectTabViewItem(at:i);content.layoutSubtreeIfNeeded()
 let pane=tabs.selectedTabViewItem!.view!;pane.layoutSubtreeIfNeeded()
 func check(_ view:NSView) {
  if view.isHidden {return}
  if view is NSControl {
   let r=view.convert(view.bounds,to:content)
   assert(r.minX>=0 && r.maxX<=content.bounds.width+1 && r.minY>=0 && r.maxY<=content.bounds.height+1,"control outside content: \(r)")
  }
  for v in view.subviews {check(v)}
 }
 check(pane)
 func redraw(_ view:NSView) {view.needsDisplay=true;view.displayIfNeeded();for v in view.subviews {redraw(v)}}
 redraw(content)
 CATransaction.flush();RunLoop.current.run(until:Date().addingTimeInterval(0.05));redraw(content)
 let image=content.bitmapImageRepForCachingDisplay(in:content.bounds)!
 content.cacheDisplay(in:content.bounds,to:image)
 try! image.representation(using:.png,properties:[:])!.write(to:output.appendingPathComponent("vault-tab-\(i).png"))
}
assert(center.window!.contentMinSize.height>=600)
print("native credential management, stdin-only secret, immutable review and three tab layouts passed")
''')
            binary=directory/'fixture'
            result=subprocess.run(['swiftc','ActivityCenter.swift','main.swift','-o',str(binary)],cwd=directory,capture_output=True,text=True,timeout=90)
            self.assertEqual(0,result.returncode,result.stderr)
            artifacts=Path(os.environ.get('TACTO_CREDENTIAL_UI_ARTIFACTS',str(directory/'render'))).absolute()
            result=subprocess.run([str(binary),str(artifacts)],capture_output=True,text=True,timeout=30)
            self.assertEqual(0,result.returncode,result.stderr)
            self.assertEqual(3,len(list(artifacts.glob('vault-tab-*.png'))))
