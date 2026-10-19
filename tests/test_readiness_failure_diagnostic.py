"""The first unusable readiness read is named by closed codes, without changing the verdict."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from tacto.preparation_diagnostic import preparation_diagnostic, readiness_failure

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
import Foundation
import AppKit
import ApplicationServices
let app = AXUIElementCreateApplication(42), window = AXUIElementCreateApplication(1)
let child = AXUIElementCreateApplication(3), leaf = AXUIElementCreateApplication(4)
func axError(_ code: AXError) -> CFTypeRef { var e = code; return AXValueCreate(.axError, &e)! }
var override: [String: (AXError, CFTypeRef?)] = [:]
func key(_ node: AXUIElement, _ name: String) -> String {
    var pid: pid_t = 0; AXUIElementGetPid(node, &pid); return "\(pid).\(name)"
}
let read: AXRead = { node, name in
    if let forced = override[key(node, name)] { return forced }
    if CFEqual(node, app) && name == "AXWindows" { return (.success, [window] as CFArray) }
    if name == "AXRole" { return (.success, (CFEqual(node, window) ? "AXWindow" : "AXGroup") as CFString) }
    if name == "AXMinimized" || name == "AXModal" { return (.success, kCFBooleanFalse) }
    if name == "AXSheets" { return (.success, [] as CFArray) }
    if name == "AXChildren" {
        if CFEqual(node, window) { return (.success, [child] as CFArray) }
        if CFEqual(node, child) { return (.success, [leaf] as CFArray) }
        return (.success, [] as CFArray)
    }
    return (.attributeUnsupported, nil)
}
var batchCall: [String: (AXError, CFArray?)] = [:]
let readBatch: AXBatchRead = { node, names in
    if let forced = batchCall[key(node, "batch")] { return forced }
    let values: [CFTypeRef] = try names.map { name in
        let (status, value) = try read(node, name)
        if status == .success, let value { return value }
        return axError(status == .success ? .failure : status)
    }
    return (.success, values as CFArray)
}
func attempt(_ name: String, batch: Bool = false, scoped: Bool = true) -> [String: Any] {
    let scope = scoped ? NativeWindowReadScope(deadline: ProcessInfo.processInfo.systemUptime + 30, check: {}) : nil
    var reason = "accepted"
    do {
        try nativeRequireWindowReady(window, 42, scope: scope, read: read,
                                     readBatch: batch ? readBatch : nil, isFinder: false)
    } catch NativeWindowInputError.refused(let why) { reason = why }
    catch { reason = "other" }
    var row: [String: Any] = ["reason": reason]
    if let failure = scope?.readinessFailure { row["failure"] = failure }
    if let error = scope?.readinessAXError { row["ax_error"] = error }
    return row
}
var rows: [String: Any] = [:]
func scenario(_ name: String, batch: Bool = false, _ setup: () -> Void) {
    override = [:]; batchCall = [:]; setup()
    var row = attempt(name, batch: batch)
    // The same tree without a scope must refuse identically.
    row["unscoped_reason"] = attempt(name, batch: batch, scoped: false)["reason"]
    rows[name] = row
}
scenario("accepted") {}
scenario("scalar_invalid") { override["3.AXRole"] = (.invalidUIElement, nil) }
scenario("scalar_absent") { override["3.AXRole"] = (.noValue, nil) }
scenario("scalar_unsupported") { override["3.AXRole"] = (.attributeUnsupported, nil) }
scenario("scalar_numeric") { override["3.AXRole"] = (.success, NSNumber(value: 7)) }
scenario("scalar_malformed") { override["3.AXRole"] = (.success, [] as CFArray) }
scenario("scalar_null") { override["3.AXRole"] = (.success, nil) }
scenario("scalar_empty") { override["3.AXRole"] = (.success, "" as CFString) }
scenario("leaf_children") { override["4.AXChildren"] = (.success, "rows" as CFString) }
scenario("leaf_modal") { override["4.AXModal"] = (.success, "no" as CFString) }
scenario("leaf_modal_true") { override["4.AXModal"] = (.success, kCFBooleanTrue) }
scenario("windows") { override["42.AXWindows"] = (.cannotComplete, nil) }
scenario("top_sheets_malformed") { override["1.AXSheets"] = (.success, "x" as CFString) }
scenario("top_sheet_open") { override["1.AXSheets"] = (.success, [leaf] as CFArray) }
scenario("batch_field", batch: true) { override["3.AXRole"] = (.invalidUIElement, nil) }
scenario("batch_call", batch: true) { batchCall["3.batch"] = (.cannotComplete, nil) }
scenario("batch_count", batch: true) { batchCall["3.batch"] = (.success, ["AXGroup" as CFString] as CFArray) }
// The first failure is kept even if a later one is recorded on the same scope.
let kept = NativeWindowReadScope(deadline: ProcessInfo.processInfo.systemUptime + 30, check: {})
kept.recordReadinessFailure(attribute: "AXRole", source: "scalar", kind: "ax_error", error: .invalidUIElement, depth: 2)
kept.recordReadinessFailure(attribute: "AXChildren", source: "batch_field", kind: "malformed", error: nil, depth: 9)
kept.recordReadinessFailure(attribute: "AXTitle", source: "scalar", kind: "ax_error", error: .success, depth: 99)
rows["kept"] = ["failure": kept.readinessFailure!]
let bounded = NativeWindowReadScope(deadline: ProcessInfo.processInfo.systemUptime + 30, check: {})
bounded.recordReadinessFailure(attribute: "AXTitle", source: "scalar", kind: "ax_error", error: .success, depth: 99)
rows["bounded"] = ["failure": bounded.readinessFailure!]
print(String(data: try! JSONSerialization.data(withJSONObject: rows, options: [.sortedKeys]), encoding: .utf8)!)
'''


@unittest.skipUnless(shutil.which('xcrun'), 'macOS Swift required')
class ReadinessFailureDiagnosticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory(prefix='tacto-readiness-failure-') as directory:
            src = Path(directory)/'main.swift'
            binary = Path(directory)/'probe'
            src.write_text(HARNESS)
            built = subprocess.run(['xcrun', 'swiftc', '-warnings-as-errors', str(src),
                    str(ROOT/'helper/WindowInput.swift'), str(ROOT/'helper/WindowDispatch.swift'),
                    str(ROOT/'helper/FrontmostProcess.swift'), '-o', str(binary)],
                    capture_output=True, text=True, timeout=120)
            assert built.returncode == 0, built.stderr
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            assert run.returncode == 0, run.stderr
        cls.rows = json.loads(run.stdout)

    def failure(self, name):
        return self.rows[name].get('failure')

    def test_scope_never_changes_the_verdict(self):
        for name, row in self.rows.items():
            if 'unscoped_reason' in row:
                with self.subTest(name):
                    self.assertEqual(row['unscoped_reason'], row['reason'])
        self.assertEqual('accepted', self.rows['accepted']['reason'])
        self.assertNotIn('failure', self.rows['accepted'])

    def test_scalar_failures_are_told_apart(self):
        expected = {
            'scalar_invalid': ('window state unavailable: AXRole', 'ax_error', -25202),
            'scalar_absent': ('required AX attribute unavailable: AXRole', 'required_absent', -25212),
            'scalar_unsupported': ('required AX attribute unavailable: AXRole', 'required_absent', -25205),
            'scalar_numeric': ('invalid AX role', 'numeric', None),
            'scalar_malformed': ('invalid AX role', 'malformed', None),
            'scalar_null': ('window state unavailable: AXRole', 'null_value', None),
            'scalar_empty': ('attached sheet or malformed role', 'empty', None),
        }
        for name, (reason, kind, error) in expected.items():
            with self.subTest(name):
                self.assertEqual(reason, self.rows[name]['reason'])
                failure = self.failure(name)
                self.assertEqual({'stage': 'tree', 'attribute': 'AXRole', 'source': 'scalar',
                                  'kind': kind, 'depth': 1, 'visit': 2,
                                  **({'ax_error': error} if error else {})}, failure)

    def test_attribute_stage_and_depth_follow_the_failing_read(self):
        self.assertEqual({'stage': 'tree', 'attribute': 'AXChildren', 'source': 'scalar',
                          'kind': 'malformed', 'depth': 2, 'visit': 3}, self.failure('leaf_children'))
        self.assertEqual('window children malformed', self.rows['leaf_children']['reason'])
        self.assertEqual({'stage': 'tree', 'attribute': 'AXModal', 'source': 'scalar',
                          'kind': 'malformed', 'depth': 2, 'visit': 3}, self.failure('leaf_modal'))
        self.assertEqual({'stage': 'windows', 'attribute': 'AXWindows', 'source': 'scalar',
                          'kind': 'ax_error', 'depth': 0, 'visit': 0, 'ax_error': -25204},
                         self.failure('windows'))
        self.assertEqual({'stage': 'top', 'attribute': 'AXSheets', 'source': 'scalar',
                          'kind': 'malformed', 'depth': 0, 'visit': 0}, self.failure('top_sheets_malformed'))

    def test_positive_findings_are_not_read_failures(self):
        self.assertEqual('modal or unknown element', self.rows['leaf_modal_true']['reason'])
        self.assertIsNone(self.failure('leaf_modal_true'))
        self.assertEqual('sheet or malformed sheet list', self.rows['top_sheet_open']['reason'])
        self.assertIsNone(self.failure('top_sheet_open'))

    def test_batch_sources_are_exact(self):
        self.assertEqual({'stage': 'tree', 'attribute': 'AXRole', 'source': 'batch_field',
                          'kind': 'ax_error', 'depth': 1, 'visit': 2, 'ax_error': -25202},
                         self.failure('batch_field'))
        self.assertEqual('readiness batch failed', self.rows['batch_call']['reason'])
        self.assertEqual({'stage': 'tree', 'attribute': 'batch', 'source': 'batch_call',
                          'kind': 'batch_failed', 'depth': 1, 'visit': 2, 'ax_error': -25204},
                         self.failure('batch_call'))
        self.assertEqual('readiness batch malformed', self.rows['batch_count']['reason'])
        self.assertEqual({'stage': 'tree', 'attribute': 'batch', 'source': 'batch_call',
                          'kind': 'batch_malformed', 'depth': 1, 'visit': 2}, self.failure('batch_count'))

    def test_first_failure_is_kept_and_fields_are_bounded(self):
        self.assertEqual({'stage': 'entry', 'attribute': 'AXRole', 'source': 'scalar',
                          'kind': 'ax_error', 'depth': 2, 'visit': 0, 'ax_error': -25202},
                         self.failure('kept'))
        self.assertEqual({'stage': 'entry', 'attribute': 'other', 'source': 'scalar',
                          'kind': 'ax_error', 'depth': 64, 'visit': 0}, self.failure('bounded'))

    def test_every_emitted_failure_passes_the_closed_python_validator(self):
        for name, row in self.rows.items():
            if 'failure' in row:
                with self.subTest(name):
                    self.assertEqual(row['failure'], readiness_failure(row['failure']))
                    checked = preparation_diagnostic({'phase': 'context', 'reason': 'readiness_attribute_failed',
                                                      'elapsed_ms': 1, 'readiness_failure': row['failure']})
                    self.assertEqual(row['failure'], checked['readiness_failure'])


class ReadinessFailureValidatorTests(unittest.TestCase):
    base = {'stage': 'tree', 'attribute': 'AXRole', 'source': 'batch_field', 'kind': 'ax_error',
            'depth': 1, 'visit': 2, 'ax_error': -25202}

    def test_unknown_codes_extra_keys_and_unbounded_numbers_are_rejected(self):
        self.assertEqual(self.base, readiness_failure(self.base))
        for change in ({'stage': 'walk'}, {'attribute': 'AXTitle'}, {'source': 'cache'},
                       {'kind': 'timeout'}, {'depth': 65}, {'depth': -1}, {'visit': 20001},
                       {'depth': True}, {'ax_error': 0}, {'ax_error': 70000}, {'ax_error': '-25202'},
                       {'title': 'Documents'}, {'attribute': 'AXRole' * 50}):
            with self.subTest(change):
                self.assertIsNone(readiness_failure({**self.base, **change}))
        for missing in ('stage', 'attribute', 'source', 'kind', 'depth', 'visit'):
            with self.subTest(missing=missing):
                self.assertIsNone(readiness_failure({k: v for k, v in self.base.items() if k != missing}))
        self.assertIsNone(preparation_diagnostic({'phase': 'context', 'reason': 'readiness_attribute_failed',
                                                  'elapsed_ms': 1, 'readiness_failure': {**self.base, 'kind': 'x'}}))

    def test_preflight_summary_keeps_only_the_validated_failure(self):
        from examples.probes.signed_finder_read_cost import preflight_summary
        reply = {'type': 'error', 'reason': 'preflight_unavailable', 'read_only': True,
                 'authorizes_input': False, 'elapsed_ms': 22, 'scoped_ax_operations': 43,
                 'ax_operation_elapsed_ms': 14, 'context_elapsed_ms': 0,
                 'readiness_failure': self.base, 'readiness_ax_error': -25202}
        summary = preflight_summary(reply)
        self.assertEqual(self.base, summary['readiness_failure'])
        self.assertEqual(-25202, summary['readiness_ax_error'])
        self.assertIsNone(preflight_summary({**reply, 'readiness_failure': {**self.base, 'title': 'x'}}))
        self.assertIsNone(preflight_summary({**reply, 'readiness_ax_error': 0}))

    def test_helper_exposes_the_same_metadata_on_both_paths(self):
        source = (ROOT/'helper/TactoHelper.swift').read_text()
        preflight = source[source.index('func readinessPreflight('):]
        preflight = preflight[:preflight.index('let fields = Set(')]
        self.assertIn('out["readiness_failure"] = failure', preflight)
        self.assertIn('out["readiness_ax_error"] = error', preflight)
        self.assertIn('diagnostic["readiness_failure"] = failure', source)


if __name__ == '__main__':
    unittest.main()
