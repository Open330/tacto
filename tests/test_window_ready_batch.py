"""The same adversarial modal trees must refuse with batched attributes."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from tests.test_window_ready_swift import HARNESS

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('xcrun'), 'macOS Swift required')
class WindowReadyBatchTests(unittest.TestCase):
    def test_actual_traversal_progress_identifies_other_window_and_resets_each_pass(self):
        harness = '''import Foundation
import AppKit
import ApplicationServices
let app=AXUIElementCreateApplication(42), other=AXUIElementCreateApplication(1)
let target=AXUIElementCreateApplication(2), child=AXUIElementCreateApplication(3)
var fail=true, failWindows=false, failRecheck=false
var modalReads=0
let scope=NativeWindowReadScope(deadline:ProcessInfo.processInfo.systemUptime+30,check:{})
let read:AXRead={ node,name in
 if CFEqual(node,app) && name=="AXWindows" {
  return failWindows ? (.cannotComplete,nil):(.success,[other,target] as CFArray)
 }
 if name=="AXRole" {
  if fail && CFEqual(node,child) { throw NativeWindowInputError.refused("window observation deadline expired") }
  return (.success,(CFEqual(node,child) ? "AXButton":"AXWindow") as CFString)
 }
 if name=="AXModal" && CFEqual(node,other) {
  modalReads += 1
  return (.success,failRecheck && modalReads==2 ? kCFBooleanTrue:kCFBooleanFalse)
 }
 if name=="AXMinimized" || name=="AXModal" { return (.success,kCFBooleanFalse) }
 if name=="AXChildren" { return (.success,(CFEqual(node,other) ? [child]:[]) as CFArray) }
 return (.attributeUnsupported,nil)
}
var reason="accepted"
do { try nativeRequireWindowReady(target,42,scope:scope,read:read,isFinder:false) }
catch NativeWindowInputError.refused(let why) { reason=why }
let failed=scope.readinessProgress!
fail=false
try! nativeRequireWindowReady(target,42,scope:scope,read:read,isFinder:false)
let complete=scope.readinessProgress!
failWindows=true
do { try nativeRequireWindowReady(target,42,scope:scope,read:read,isFinder:false) } catch {}
let early=scope.readinessProgress!
failWindows=false;failRecheck=true;modalReads=0
do { try nativeRequireWindowReady(target,42,scope:scope,read:read,isFinder:false) } catch {}
print(String(data:try! JSONSerialization.data(withJSONObject:["reason":reason,"failed":failed,
 "complete":complete,"early":early,"recheck":scope.readinessProgress!]),encoding:.utf8)!)
'''
        report=self.run_swift(harness)
        self.assertEqual(report['reason'],'readiness budget exceeded: deadline')
        self.assertEqual(report['failed'],{'pass':1,'windows':2,'completed':0,'index':0,
            'target':False,'nodes':2,'total_nodes':2,'stage':'tree'})
        self.assertEqual(report['complete'],{'pass':2,'windows':2,'completed':2,'index':1,
            'target':True,'nodes':0,'total_nodes':3,'stage':'complete'})
        self.assertEqual(report['early'],{'pass':3,'windows':0,'completed':0,'index':-1,
            'target':False,'nodes':0,'total_nodes':0,'stage':'windows'})
        self.assertEqual(report['recheck'],{'pass':4,'windows':2,'completed':2,'index':0,
            'target':False,'nodes':0,'total_nodes':3,'stage':'recheck'})

    def test_opt_in_identity_counter_does_not_skip_repeated_nodes_and_resets(self):
        harness=r'''
import Foundation
import AppKit
import ApplicationServices
let app=AXUIElementCreateApplication(42),target=AXUIElementCreateApplication(1)
let other=AXUIElementCreateApplication(2),shared=AXUIElementCreateApplication(3)
var sharedRoleReads=0,lateModal=false
let read:AXRead={ node,name in
 if CFEqual(node,app) && name=="AXWindows" {return (.success,[other,target] as CFArray)}
 if name=="AXRole" {
  if CFEqual(node,shared) {sharedRoleReads+=1}
  return (.success,(CFEqual(node,shared) ? "AXButton":"AXWindow") as CFString)
 }
 if name=="AXModal" {return (.success,lateModal && CFEqual(node,shared) && sharedRoleReads==2 ? kCFBooleanTrue:kCFBooleanFalse)}
 if name=="AXMinimized" {return (.success,kCFBooleanFalse)}
 if name=="AXChildren" {return (.success,(CFEqual(node,shared) ? []:[shared]) as CFArray)}
 return (.attributeUnsupported,nil)
}
let scope=NativeWindowReadScope(deadline:ProcessInfo.processInfo.systemUptime+30,collectReadinessRoleCosts:true,check:{})
try! nativeRequireWindowReady(target,42,scope:scope,read:read,isFinder:false)
let first=scope.readinessIdentityCounts!,firstReads=sharedRoleReads
sharedRoleReads=0;lateModal=true
var refused=false
do {try nativeRequireWindowReady(target,42,scope:scope,read:read,isFinder:false)} catch {refused=true}
let second=scope.readinessIdentityCounts!
let plain=NativeWindowReadScope(deadline:ProcessInfo.processInfo.systemUptime+30,check:{})
plain.recordReadinessIdentity(shared)
print(String(data:try! JSONSerialization.data(withJSONObject:["first":first,"second":second,
 "first_reads":firstReads,"second_reads":sharedRoleReads,"refused":refused,
 "default_off":plain.readinessIdentityCounts == nil]),encoding:.utf8)!)
'''
        result=self.run_swift(harness)
        self.assertEqual({'visits':4,'unique_nodes':3,'repeated_nodes':1},result['first'])
        self.assertEqual(result['first'],result['second'])
        self.assertEqual(2,result['first_reads']);self.assertEqual(2,result['second_reads'])
        self.assertTrue(result['refused']);self.assertTrue(result['default_off'])

    def run_swift(self, text):
        with tempfile.TemporaryDirectory(prefix='tacto-ready-batch-') as directory:
            src = Path(directory) / 'main.swift'; binary = Path(directory) / 'probe'
            src.write_text(text)
            built = subprocess.run(['xcrun', 'swiftc', '-warnings-as-errors', str(src),
                    str(ROOT / 'helper/WindowInput.swift'), str(ROOT / 'helper/WindowDispatch.swift'),
                    str(ROOT / 'helper/FrontmostProcess.swift'), '-o', str(binary)],
                    capture_output=True, text=True, timeout=60)
            self.assertEqual(0, built.returncode, built.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
        self.assertEqual(0, run.returncode, run.stderr)
        return json.loads(run.stdout)

    def test_existing_adversarial_cases_agree_in_batch_mode(self):
        original = self.run_swift(HARNESS)
        # Package exactly the fake provider's per-attribute AX replies as the
        # real CopyMultiple API does; execute the actual batch decoder and DFS.
        replacement = '''read: read, readBatch: { node, names in
            let values: [CFTypeRef] = try names.map { name in
                let (status, value) = try read(node, name)
                if status == .success { return value! }
                var error = status
                return AXValueCreate(.axError, &error)!
            }
            return (.success, values as CFArray)
        }, isFinder: finder)'''
        self.assertIn('read: read, isFinder: finder)', HARNESS)
        batched = self.run_swift(HARNESS.replace('read: read, isFinder: finder)', replacement))
        self.assertEqual(original, batched)

    def test_transport_failures_and_malformed_packets_refuse(self):
        harness = '''import Foundation
import AppKit
import ApplicationServices
let node = AXUIElementCreateApplication(4242)
let names = ["AXRole", "AXModal", "AXChildren"]
var rows: [String:String] = [:]
func run(_ error: AXError, _ values: CFArray?) -> String {
 do {
  let result = try nativeReadinessBatch(node, names:names, read:{ _, _ in (error, values) })
  return result == nil ? "fallback" : "accepted"
 } catch NativeWindowInputError.refused(let detail) { return detail }
 catch { return "refused" }
}
rows["unsupported"] = run(.attributeUnsupported, nil)
rows["not implemented"] = run(.notImplemented, nil)
rows["timeout"] = run(.cannotComplete, nil)
rows["failure"] = run(.failure, nil)
rows["nil packet"] = run(.success, nil)
rows["short packet"] = run(.success, ["AXWindow", false] as CFArray)
rows["extra packet"] = run(.success, ["AXWindow", false, [], "extra"] as CFArray)
var zero = AXError.success
rows["wrapped success is invalid"] = run(.success, [AXValueCreate(.axError,&zero)!, false, []] as CFArray)
print(String(data:try! JSONSerialization.data(withJSONObject:rows),encoding:.utf8)!)
'''
        rows = self.run_swift(harness)
        self.assertEqual({'unsupported':'fallback', 'not implemented':'fallback',
                          'timeout':'readiness batch failed', 'failure':'readiness batch failed',
                          'nil packet':'readiness batch malformed',
                          'short packet':'readiness batch malformed', 'extra packet':'readiness batch malformed',
                          'wrapped success is invalid':'readiness batch error malformed'}, rows)

    def test_leaf_top_modal_is_read_again_after_batch(self):
        harness = '''import Foundation
import AppKit
import ApplicationServices
let app = AXUIElementCreateApplication(42)
let window = AXUIElementCreateApplication(1)
var modalReads = 0
let read: AXRead = { element, name in
 if CFEqual(element, app) && name == "AXWindows" { return (.success, [window] as CFArray) }
 if name == "AXRole" { return (.success, "AXWindow" as CFString) }
 if name == "AXMinimized" { return (.success, kCFBooleanFalse) }
 if name == "AXChildren" { return (.success, [] as CFArray) }
 if name == "AXModal" {
  modalReads += 1
  return (.success, modalReads >= 3 ? kCFBooleanTrue : kCFBooleanFalse)
 }
 return (.attributeUnsupported, nil)
}
var refused = false
do {
 try nativeRequireWindowReady(window,42,read:read,readBatch:{ node, names in
  let values: [CFTypeRef] = try names.map { name in let (_, value) = try read(node,name); return value! }
  return (.success,values as CFArray)
 },isFinder:false)
} catch { refused = true }
print(String(data:try! JSONSerialization.data(withJSONObject:["refused":refused,"modal_reads":modalReads]),encoding:.utf8)!)
'''
        self.assertEqual({'refused':True, 'modal_reads':3}, self.run_swift(harness))

    def test_finder_desktop_exception_cannot_survive_role_change(self):
        harness = '''import Foundation
import AppKit
import ApplicationServices
let app = AXUIElementCreateApplication(42)
let window = AXUIElementCreateApplication(1)
let desktop = AXUIElementCreateApplication(2)
var roleReads = 0
let read: AXRead = { node, name in
 if CFEqual(node, app) && name == "AXWindows" { return (.success, [desktop,window] as CFArray) }
 if name == "AXRole" {
  if CFEqual(node,desktop) { roleReads += 1; return (.success,(roleReads >= 3 ? "AXDialog" : "AXScrollArea") as CFString) }
  return (.success,"AXWindow" as CFString)
 }
 if name == "AXParent" { return (.success,app) }
 if name == "AXMinimized" { return (.success,kCFBooleanFalse) }
 if name == "AXModal" && CFEqual(node,window) { return (.success,kCFBooleanFalse) }
 if name == "AXChildren" { return (.success,[] as CFArray) }
 return (.attributeUnsupported,nil)
}
var reason = "accepted"
do {
 try nativeRequireWindowReady(window,42,read:read,readBatch:{ node, names in
  let values: [CFTypeRef] = try names.map { name in
   let (status,value) = try read(node,name)
   if status == .success { return value! }
   var error = status; return AXValueCreate(.axError,&error)!
  }
  return (.success,values as CFArray)
 },isFinder:true)
} catch NativeWindowInputError.refused(let why) { reason = why }
print(String(data:try! JSONSerialization.data(withJSONObject:["reason":reason,"role_reads":roleReads]),encoding:.utf8)!)
'''
        self.assertEqual({'reason':'window role changed during validation','role_reads':3},
                         self.run_swift(harness))


if __name__ == '__main__':
    unittest.main()
