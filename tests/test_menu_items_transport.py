"""Execute the production Swift menu discovery and launcher schema with AX shims."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

SHIM = r'''import Foundation
import CoreFoundation
typealias AXUIElement = Int
func AXUIElementGetTypeID() -> CFTypeID { CFNumberGetTypeID() }
let kAXChildrenAttribute = "AXChildren"
let kAXRoleAttribute = "AXRole"
let kAXTitleAttribute = "AXTitle"
var malformedTitle = false
var unknownChildren = false
var clock = 0.0
var clockStep = 0.0
struct ProcessInfo {
    static let processInfo = ProcessInfo()
    var systemUptime: Double { clock += clockStep; return clock }
}
var malformedBar = false
var axError = false
var huge = 0
var duplicate = false
var stopDuringRead = false
let kAXEnabledAttribute = "AXEnabled"
var enabled: Any? = true
var changed = false
var stopped = false
var resolutions = 0
struct Abort { var isStopped: Bool { stopped } }
let abortState = Abort()
func pid(forBundle: String) -> Int32? { resolutions += 1; return changed && resolutions > 1 ? 43 : 42 }
func nativeProcessStartIdentity(_ p: Int32) -> Double? { 123 }
func AXUIElementCreateApplication(_ p: Int32) -> Int { 0 }
func children(_ item: Int) -> [Int] {
    if item == 1 { return duplicate ? [2, 2] : [2, 4] }
    if item == 2 { return [3] }
    if item == 3 { return [5, 6] }
    return []
}
func attr(_ item: Int, _ key: String) -> Any? {
    if stopDuringRead { stopped = true }
    if key == "AXMenuBar" { return malformedBar ? "bad" : 1 }
    if key == "AXRole" { return item == 3 ? "AXMenu" : "AXMenuItem" }
    if key == "AXTitle" {
        if malformedTitle && item == 2 { return 7 }
        return [2:"Probe", 4:"", 5:"Command", 6:"Other"][item]
    }
    return key == "AXEnabled" ? enabled : nil
}
enum AXError: Int32 { case success = 0, failure = -25204, noValue = -25212 }
var timeoutFailure = false
var resetFailure = false
var configuredTimeouts: [Float] = []
var rangeCounts: [Int] = []
var countCalls = 0
var changingCount = false
var countDisappears = false
func AXUIElementGetAttributeValueCount(_ el: Int, _ key: CFString, _ out: inout CFIndex) -> AXError {
    countCalls += 1
    if countDisappears && countCalls % 2 == 0 { return .noValue }
    if unknownChildren { return .noValue }
    out = huge > 0 ? huge : children(el).count
    return .success
}
func AXUIElementCopyAttributeValues(_ el: Int, _ key: CFString, _ index: Int, _ count: Int, _ out: inout CFArray?) -> AXError {
    rangeCounts.append(count)
    out = children(el) as CFArray
    return .success
}
func AXUIElementSetMessagingTimeout(_ el: Int, _ seconds: Float) -> AXError {
    configuredTimeouts.append(seconds)
    if seconds == 0 { return resetFailure ? .failure : .success }
    return timeoutFailure ? .failure : .success
}
func AXUIElementCopyAttributeValue(_ el: Int, _ key: CFString, _ out: inout CFTypeRef?) -> AXError {
    assert(key as String != "AXChildren")
    if axError { return .failure }
    out = attr(el, key as String) as CFTypeRef?
    return .success
}
'''

@unittest.skipUnless(shutil.which('xcrun'), 'Swift unavailable')
class MenuItemsTransportTests(unittest.TestCase):
    def run_swift(self, script):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp)/'main.swift'; binary = Path(tmp)/'probe'
            src.write_text(script)
            build = subprocess.run(['xcrun', 'swiftc', str(src), '-o', str(binary)], capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            return json.loads(run.stdout)

    def test_listing_actual_reader_complete_unknown_duplicate_and_bounds(self):
        source = (ROOT/'helper/NLCHelper.swift').read_text()
        reader = source[source.index('func readMenuState('):source.index('func handle(')]
        traversal = source[source.index('@Sendable func menuChildren('):source.index('/// Bring an app forward')]
        rows = self.run_swift(SHIM + traversal + reader + r'''
let root: [String: Any] = ["type":"menu_items", "target":"fixture", "path":[]]
var rows = [readMenuState(root)]
var nested = root; nested["path"] = [["Missing", "Probe"]]; rows.append(readMenuState(nested))
enabled = nil; rows.append(readMenuState(root))
enabled = 1; rows.append(readMenuState(root))
duplicate = true; rows.append(readMenuState(root)); duplicate = false
malformedTitle = true; rows.append(readMenuState(root)); malformedTitle = false
unknownChildren = true; rows.append(readMenuState(root)); unknownChildren = false
axError = true; rows.append(readMenuState(root)); axError = false
stopped = true; rows.append(readMenuState(root)); stopped = false
changed = true; resolutions = 0; rows.append(readMenuState(root)); changed = false
huge = 5001; rows.append(readMenuState(root)); huge = 0
var bad = root; bad["path"] = Array(repeating:"Probe",count:7); rows.append(readMenuState(bad))
bad = root; bad["action"] = "click"; rows.append(readMenuState(bad))
print(String(data:try! JSONSerialization.data(withJSONObject:rows),encoding:.utf8)!)
''')
        self.assertEqual(rows[0], {'type':'menu_items', 'path':[], 'complete':True,
            'items':[{'title':'Probe','path':['Probe'],'enabled':True,'has_submenu':True}]})
        self.assertEqual(rows[1]['path'], ['Probe'])
        self.assertEqual([x['title'] for x in rows[1]['items']], ['Command','Other'])
        self.assertTrue(all(x['has_submenu'] is False for x in rows[1]['items']))
        self.assertIsNone(rows[2]['items'][0]['enabled'])
        self.assertIsNone(rows[3]['items'][0]['enabled'])
        self.assertEqual([r['error'] for r in rows[4:]], ['menu_ambiguous',
            'invalid_menu_title','menu_state_unavailable','menu_read_failed',
            'aborted_by_user','target_changed','menu_read_budget_exceeded',
            'invalid_menu_path','invalid_menu_path'])

    def test_launcher_root_listing_only(self):
        source = (ROOT/'launcher/nlc.swift').read_text()
        schema = source[source.index('let allowedTypes:'):source.index('// MARK: - Socket plumbing')]
        rows = self.run_swift('import Foundation\nimport CoreFoundation\nfunc modelTimeout(_ raw: Any?) -> Double? { 1 }\n' + schema + r'''
let root: [String: Any] = ["type":"menu_items", "target":"fixture", "path":[]]
var cases = [root]
var req = root; req["path"] = ["Probe"]; cases.append(req)
req = root; req["type"] = "menu_state"; cases.append(req)
req = root; req["action"] = "click"; cases.append(req)
req = root; req["path"] = [["x", "\n"]]; cases.append(req)
req = root; req["path"] = Array(repeating:"x",count:7); cases.append(req)
print(String(data:try! JSONSerialization.data(withJSONObject:cases.map { vetted($0) == nil }),encoding:.utf8)!)
''')
        self.assertEqual(rows, [True,True,False,False,False,False])
