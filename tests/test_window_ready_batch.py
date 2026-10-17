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
 } catch { return "refused" }
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
                          'timeout':'refused', 'failure':'refused', 'nil packet':'refused',
                          'short packet':'refused', 'extra packet':'refused',
                          'wrapped success is invalid':'refused'}, rows)

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
