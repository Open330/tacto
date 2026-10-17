"""Execute helper preparation with injected native boundaries, no GUI input."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('swift'), 'Swift required')
class WindowPreparationDiagnosticsTests(unittest.TestCase):
    def test_actual_background_handler_phase_reason_and_effect(self):
        source = (ROOT / 'helper/TactoHelper.swift').read_text()
        fn = source[source.index('func prepareBackgroundWindow('):]
        fn = fn[:fn.index('\n}\n') + 3]
        harness = '''import Foundation
import CoreFoundation
typealias AXUIElement = NSNumber
enum NativeWindowInputError: Error { case refused(String), unavailable(String), needsForeground(String) }
struct NativeWindowDispatchFailure: Error { let stopped: Bool; let attempted: Int; let detail: String }
struct NativeOpaqueWindowInput {}
struct NativeWindowBinding {}
enum PointKind { case windowInput, windowPoint, titlebar, elementCenter }
struct DispatchResult { let posted = 2; let windowID = 12 }
final class Abort { var isStopped = false }
let abortState = Abort()
let element = NSNumber(value: 1)
let latestElements = [element]
let latestProcessStart: Double? = 1
let latestObsID = "o"
let latestTargetPID: pid_t = 7
let latestTargetName = "fixture"
var scenario = "success"
var dispatched = 0
func nativeProcessStartIdentity(_ pid: pid_t) -> Double? { scenario == "identity" ? 2 : 1 }
func processForWindowTarget(_ target: String) -> pid_t? { 7 }
func validateDocumentObservation(_ target: String) throws {}
func mismatch(_ expected: [String:Any], element: AXUIElement, pid: pid_t) -> String? { nil }
final class NativeWindowReadScope {
 var operationCount = 0
 let check: () throws -> Void
 init(deadline: Double, check: @escaping () throws -> Void) { self.check = check }
 func ready() throws { try check(); if scenario == "context" { throw NativeWindowInputError.refused("window observation deadline expired") } }
}
func captureNativeWindowBinding(_ el: AXUIElement, _ pid: pid_t, expectedStart: Double,
 pointKind: PointKind, scope: NativeWindowReadScope, relativePoint: CGPoint?) throws -> NativeWindowBinding {
 scope.operationCount += 7
 let why: String? = scenario == "nodes" ? "readiness budget exceeded: nodes" :
  scenario == "deadline" ? "readiness budget exceeded: deadline" :
  scenario == "titlebar" ? "no blank native titlebar point" :
  scenario == "private" ? "PRIVATE_DOCUMENT_SENTINEL" : nil
 if let why { throw NativeWindowInputError.refused(why) }
 return NativeWindowBinding()
}
func requireNativeWindowTarget(_ b: NativeWindowBinding, scope: NativeWindowReadScope) throws {}
func requireNativeKeyTarget(_ b: NativeWindowBinding, element: Bool, scope: NativeWindowReadScope) throws {}
func requireNativeSubmitTarget(_ b: NativeWindowBinding, scope: NativeWindowReadScope) throws {}
func validateNativeWindowBinding(_ b: NativeWindowBinding, scope: NativeWindowReadScope) throws {}
func dispatchNativeWindowPreparation(_ b: NativeWindowBinding, deadline: Double, stopped: () -> Bool,
 context: () throws -> Void, validate: (NativeWindowBinding) throws -> Void, key: String?,
 routing: ((NativeWindowBinding, Bool) throws -> Void)?, preflight: (() throws -> Void)?,
 pointClick: Bool, windowInput: NativeOpaqueWindowInput?, elementClicks: Int, submitKey: Bool) throws -> DispatchResult {
 dispatched += 1
 if scenario == "dispatch" { throw NativeWindowDispatchFailure(stopped:false, attempted:1, detail:"injected dispatch failure") }
 return DispatchResult()
}
''' + fn + '''
var rows: [[String:Any]] = []
for mode in ["success", "identity", "context", "nodes", "deadline", "titlebar", "private", "dispatch"] {
 scenario = mode; dispatched = 0
 let result = prepareBackgroundWindow("o", "fixture", element, 7, deadline: Double.infinity)
 rows.append(["case": mode, "type": result["type"]!, "effect": result["effect"] ?? "unverified",
              "diagnostic": result["preparation_diagnostic"]!, "dispatch_calls": dispatched])
}
print(String(data:try! JSONSerialization.data(withJSONObject:rows),encoding:.utf8)!)
'''
        with tempfile.TemporaryDirectory(prefix='nlc-preparation-diagnostic-') as directory:
            script = Path(directory) / 'main.swift'
            script.write_text(harness)
            run = subprocess.run(['swift', str(script)], capture_output=True, text=True, timeout=60)
        self.assertEqual(0, run.returncode, run.stderr)
        rows = {r['case']: r for r in json.loads(run.stdout)}
        expected = {'success': ('dispatch', 'dispatched_unverified'),
                    'identity': ('identity', 'identity_changed'),
                    'context': ('context', 'observation_deadline'),
                    'nodes': ('binding', 'readiness_nodes'),
                    'deadline': ('binding', 'readiness_deadline'),
                    'titlebar': ('binding', 'titlebar_point_unavailable'),
                    'private': ('binding', 'unclassified_refusal'),
                    'dispatch': ('dispatch', 'unclassified_refusal')}
        for name, pair in expected.items():
            with self.subTest(name=name):
                row, detail = rows[name], rows[name]['diagnostic']
                self.assertEqual(pair, (detail['phase'], detail['reason']))
                self.assertGreaterEqual(detail['elapsed_ms'], 0)
                self.assertLessEqual(detail['elapsed_ms'], 600000)
                self.assertEqual(0 if name in ('identity', 'context') else 7, detail['scoped_ax_operations'])
                self.assertEqual(1 if name in ('success', 'dispatch') else 0, row['dispatch_calls'])
                if name != 'success':
                    self.assertEqual('unknown' if name == 'dispatch' else 'not_started', row['effect'])
        self.assertNotIn('PRIVATE_DOCUMENT_SENTINEL', run.stdout)

    def test_actual_scope_counts_only_started_ax_operations(self):
        source = (ROOT / 'helper/WindowInput.swift').read_text()
        scope = source[source.index('final class NativeWindowReadScope {'):]
        scope = scope[:scope.index('\n}\n') + 3]
        script = '''import Foundation
import CoreFoundation
typealias AXUIElement = Int
enum AXError { case success, failure }
enum NativeWindowInputError: Error { case refused(String), unavailable(String) }
var failSet = false
var failReset = false
var copies = 0
func AXUIElementSetMessagingTimeout(_ element: Int, _ timeout: Float) -> AXError {
 return (timeout > 0 && failSet) || (timeout == 0 && failReset) ? .failure : .success
}
func AXUIElementCopyAttributeValue(_ element: Int, _ name: CFString, _ out: inout CFTypeRef?) -> AXError {
 copies += 1; out = NSNumber(value:1); return .success
}
''' + scope + '''
var rows: [[String:Any]] = []
for mode in ["normal", "expired", "set_failure", "reset_failure"] {
 copies = 0; failSet = mode == "set_failure"; failReset = mode == "reset_failure"
 let scope = NativeWindowReadScope(deadline: ProcessInfo.processInfo.systemUptime + (mode == "expired" ? -1 : 30), check: {})
 var refused = false
 do { _ = try scope.read(1, "AXRole") } catch { refused = true }
 rows.append(["case":mode, "count":scope.operationCount, "copies":copies, "refused":refused])
}
print(String(data:try! JSONSerialization.data(withJSONObject:rows),encoding:.utf8)!)
'''
        with tempfile.TemporaryDirectory(prefix='nlc-read-scope-counter-') as directory:
            path = Path(directory) / 'main.swift'; path.write_text(script)
            run = subprocess.run(['swift', str(path)], capture_output=True, text=True, timeout=60)
        self.assertEqual(0, run.returncode, run.stderr)
        rows = json.loads(run.stdout)
        for row in rows:
            self.assertEqual(row['copies'], row['count'])
            self.assertEqual(row['case'] != 'normal', row['refused'])
            self.assertEqual(0 if row['case'] in ('expired', 'set_failure') else 1, row['count'])


if __name__ == '__main__':
    unittest.main()
