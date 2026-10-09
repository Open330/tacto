"""Menu observation preserves uncertainty and uses the signed L2 channel."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock
from nlc.helper import HelperDriver, InheritedHelperDriver

ROOT = Path(__file__).resolve().parents[1]

class MenuTransportTests(unittest.TestCase):
    def test_all_transports_forward_read_without_acting(self):
        for cls in (HelperDriver, InheritedHelperDriver):
            d = object.__new__(cls)
            d._send = Mock(return_value={'type':'menu_state','enabled':False})
            result = d.menu_state('fixture', [['Probe','검사'], 'Command'])
            self.assertIs(result['enabled'],False)
            d._send.assert_called_once_with({'type':'menu_state','target':'fixture',
                'path':[['Probe','검사'],'Command']})

    @unittest.skipUnless(shutil.which('xcrun'), 'Swift unavailable')
    def test_actual_swift_reader_rejects_unknown_state_stop_and_replacement(self):
        source = (ROOT/'helper/NLCHelper.swift').read_text()
        reader = source[source.index('func readMenuState('):source.index('func handle(')]
        traversal = source[source.index('@Sendable func menuChildren('):source.index('/// Bring an app forward')]
        harness = '''import Foundation
import CoreFoundation
typealias AXUIElement = Int
func AXUIElementGetTypeID() -> CFTypeID { CFNumberGetTypeID() }
let kAXChildrenAttribute = "AXChildren"
let kAXRoleAttribute = "AXRole"
let kAXTitleAttribute = "AXTitle"
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
func attr(_ item: Int, _ key: String) -> Any? {
    if stopDuringRead { stopped = true }
    if key == "AXMenuBar" { return malformedBar ? "bad" : 1 }
    if key == "AXChildren" { return duplicate ? [2, 2] : huge > 0 ? Array(2..<(huge + 2)) : [item + 1] }
    if key == "AXRole" { return "AXMenuItem" }
    if key == "AXTitle" { return item == 2 ? "Probe" : "Command" }
    return key == "AXEnabled" ? enabled : key == "AXFrontmost" ? false : nil
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
    out = changingCount && countCalls % 2 == 0 ? 2 : duplicate ? 2 : huge > 0 ? huge : 1
    return .success
}
func AXUIElementCopyAttributeValues(_ el: Int, _ key: CFString, _ index: Int, _ count: Int, _ out: inout CFArray?) -> AXError {
    rangeCounts.append(count)
    out = (duplicate ? [2, 2] : huge > 0 ? Array(2..<(count + 2)) : [el + 1]) as CFArray
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
''' + traversal + reader + ''' 
let request: [String: Any] = ["type":"menu_state", "target":"fixture", "path":["Probe","Command"]]
var results: [[String: Any]] = []
for value: Any? in [true, false, nil, 1, "true"] {
    enabled = value; resolutions = 0; results.append(readMenuState(request))
}
enabled = true; stopped = true; results.append(readMenuState(request))
stopped = false; changed = true; resolutions = 0; results.append(readMenuState(request))
var bad = request; bad["path"] = ["Probe", []] as [Any]; results.append(readMenuState(bad))
bad = request; bad["action"] = "click"; results.append(readMenuState(bad))
changed = false; stopDuringRead = true; resolutions = 0; results.append(readMenuState(request))
stopDuringRead = false; stopped = false; huge = 5001; results.append(readMenuState(request))
huge = 5000; results.append(readMenuState(request))
huge = 0; clockStep = 6; results.append(readMenuState(request))
clockStep = 0; axError = true; results.append(readMenuState(request))
axError = false; malformedBar = true; results.append(readMenuState(request))
malformedBar = false; timeoutFailure = true; results.append(readMenuState(request))
timeoutFailure = false; resetFailure = true; results.append(readMenuState(request))
resetFailure = false; changingCount = true; countCalls = 0; results.append(readMenuState(request))
changingCount = false; countDisappears = true; countCalls = 0; results.append(readMenuState(request))
countDisappears = false; duplicate = true; results.append(readMenuState(request))
assert(rangeCounts.allSatisfy { $0 > 0 && $0 <= 5000 })
assert(configuredTimeouts.allSatisfy { $0 >= 0 && $0 <= 1 })
assert(configuredTimeouts.contains(0))
let data = try! JSONSerialization.data(withJSONObject: results)
print(String(data:data,encoding:.utf8)!)
'''
        with tempfile.TemporaryDirectory() as tmp:
            src=Path(tmp)/'main.swift'; binary=Path(tmp)/'probe';src.write_text(harness)
            build=subprocess.run(['xcrun','swiftc',str(src),'-o',str(binary)],capture_output=True,text=True)
            self.assertEqual(build.returncode,0,build.stderr)
            run=subprocess.run([str(binary)],capture_output=True,text=True)
            self.assertEqual(run.returncode,0,run.stderr)
            rows=json.loads(run.stdout)
        self.assertIs(rows[0]['enabled'],True)
        self.assertIs(rows[1]['enabled'],False)
        self.assertIs(rows[1]['target_ax_frontmost'],False)
        self.assertTrue(all(r['error']=='menu_state_unavailable' for r in rows[2:5]))
        self.assertEqual([r['error'] for r in rows[5:]],
            ['aborted_by_user','target_changed','invalid_menu_path','invalid_menu_path',
             'aborted_by_user','menu_read_budget_exceeded','menu_read_budget_exceeded','menu_read_timeout','menu_read_failed','menu_read_failed',
             'menu_timeout_unavailable','menu_timeout_reset_failed','menu_state_changed','menu_state_changed','menu_not_found'])

    @unittest.skipUnless(shutil.which('xcrun'), 'Swift unavailable')
    def test_actual_swift_action_guard_rejects_nonboolean_enabled(self):
        source=(ROOT/'helper/NLCHelper.swift').read_text()
        start=source.index('        guard let enabled = attr(item',source.index('    case "menu":'))
        end=source.index('        let menuError =',start)
        guard=source[start:end]
        # The focused_document check (2026-10-09) sits in this span too; with
        # no focused_document in the action it is not taken.
        script='import Foundation\nimport CoreFoundation\nlet item = 0\nlet kAXEnabledAttribute = "AXEnabled"\nlet wasFront = false\nlet walked = ["Probe", "Command"]\nlet action: [String: Any] = [:]\nlet p: pid_t = 0\nfunc focusedDocumentMismatch(pid: pid_t, want: String) -> String { "" }\nvar current: Any? = nil\nfunc attr(_ item: Int, _ key: String) -> Any? { current }\nfunc run() -> [String: Any] {\n'+guard+'\nreturn ["type":"dispatch"]\n}\nlet values: [Any?] = [nil, 0, 1, "true", false, true]\nlet rows = values.map { current = $0; return run() }\nprint(String(data:try! JSONSerialization.data(withJSONObject:rows),encoding:.utf8)!)\n'
        with tempfile.TemporaryDirectory() as tmp:
            src=Path(tmp)/'main.swift';binary=Path(tmp)/'probe';src.write_text(script)
            build=subprocess.run(['xcrun','swiftc',str(src),'-o',str(binary)],capture_output=True,text=True)
            self.assertEqual(build.returncode,0,build.stderr)
            run=subprocess.run([str(binary)],capture_output=True,text=True)
            self.assertEqual(run.returncode,0,run.stderr)
            rows=json.loads(run.stdout)
        self.assertTrue(all(row.get('effect')=='not_started' for row in rows[:5]))
        self.assertEqual(rows[5]['type'],'dispatch')

    @unittest.skipUnless(shutil.which('xcrun'), 'Swift unavailable')
    def test_launcher_schema_does_not_widen_other_requests(self):
        source=(ROOT/'launcher/nlc.swift').read_text()
        schema=source[source.index('let allowedTypes:'):source.index('// MARK: - Socket plumbing')]
        script='import Foundation\nimport CoreFoundation\nfunc modelTimeout(_ raw: Any?) -> Double? { 1 }\n'+schema+'''
let valid: [String:Any] = ["type":"menu_state","target":"fixture","path":["Probe","Command"]]
var cases = [valid]
var bad = valid; bad["path"] = []; cases.append(bad)
bad = valid; bad["path"] = ["Probe", ["x", "\\n"]] as [Any]; cases.append(bad)
bad = valid; bad["obs_id"] = "o1"; cases.append(bad)
bad = valid; bad["type"] = "observe"; cases.append(bad)
print(cases.map { vetted($0) == nil ? "ok" : "refused" }.joined(separator:","))
'''
        with tempfile.TemporaryDirectory() as tmp:
            src=Path(tmp)/'main.swift';binary=Path(tmp)/'probe';src.write_text(script)
            build=subprocess.run(['xcrun','swiftc',str(src),'-o',str(binary)],capture_output=True,text=True)
            self.assertEqual(build.returncode,0,build.stderr)
            run=subprocess.run([str(binary)],capture_output=True,text=True)
            self.assertEqual(run.returncode,0,run.stderr)
            self.assertEqual(run.stdout.strip(),'ok,refused,refused,refused,refused')
