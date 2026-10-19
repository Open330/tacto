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
        element_state = source[source.index('var latestElementGeneration = UUID()'):]
        element_state = element_state[:element_state.index('\n}\n') + 3]
        harness = '''import Foundation
import CoreFoundation
typealias AXUIElement = NSNumber
enum NativeWindowInputError: Error { case refused(String), unavailable(String), needsForeground(String) }
struct NativeWindowDispatchFailure: Error { let stopped: Bool; let attempted: Int; let detail: String }
struct NativeOpaqueWindowInput {}
struct NativeWindowDrag {}
typealias NativeWindowPreparation = DispatchResult
struct NativeWindowBinding {
 let window=NSNumber(value:2),windowID:UInt32=12
 let windowBounds=1,elementBounds=2,point=3
}
enum PointKind { case windowInput, windowPoint, titlebar, elementCenter }
struct DispatchResult { let posted = 2; let windowID = 12 }
final class Abort { var isStopped = false }
let abortState = Abort()
let element = NSNumber(value: 1)
ELEMENT_STATE
let latestProcessStart: Double? = 1
let latestObsID = "o"
let latestTargetPID: pid_t = 7
let latestTargetName = "fixture"
var latestDocumentToken:String? = nil
struct PinnedDocument {
 let pid:pid_t,stamp:Double,window:AXUIElement,body:AXUIElement
 var selectedPath:String? = "/owned/doc.txt",lifecycleWindowID:UInt32? = 12
}
var documentTokens:[(token:String,document:PinnedDocument)]=[]

var scenario = "success"
var dispatched = 0
var lastIncrementalSupplied = false
var startChanged=false, metadataChanged=false,documentLiveChanged=false
func nativeProcessStartIdentity(_ pid: pid_t) -> Double? { scenario == "identity" || startChanged ? 2 : 1 }
func nativeObservedBundleMatches(_ pid: pid_t, _ target: String) -> Bool {
 scenario.hasPrefix("bound_") && !metadataChanged
}
func processForWindowTarget(_ target: String) -> pid_t? { scenario == "bound_process" ? 99 : 7 }
func validateDocumentObservation(_ target: String) throws {
 if documentLiveChanged {throw NativeWindowInputError.refused("window observation or process changed")}
}
func mismatch(_ expected: [String:Any], element: AXUIElement, pid: pid_t) -> String? { nil }
func nativePopupSelectionAnchorMatches(_ control:AXUIElement,_ element:AXUIElement,
 _ popup:AXUIElement,_ pid:pid_t,scope:NativeWindowReadScope?=nil)->Bool {scenario != "popup_bad_path"}
final class NativeWindowReadScope {
 var contextElapsedMS = 0
 var operationElapsedMS = 0
 var operationCount = 0
 var fullContextChecks = 0, incrementalContextChecks = 0
 var readinessAXError:Int? = nil
 var readinessProgress:[String:Any]? = nil
 var readinessFailure:[String:Any]? = nil
 let check: () throws -> Void
 init(deadline: Double, incrementalCheck: (() throws -> Void)? = nil, check: @escaping () throws -> Void) { self.check = check;lastIncrementalSupplied = incrementalCheck != nil }
 func withBindingContext<T>(_ body:() throws->T) throws->T {try ready();let result=try body();try ready();return result}
 func ready() throws { try check(); if scenario == "context" { throw NativeWindowInputError.refused("window observation deadline expired") } }
}
func captureNativeWindowBinding(_ el: AXUIElement, _ pid: pid_t, expectedStart: Double,
 pointKind: PointKind, scope: NativeWindowReadScope, relativePoint: CGPoint?) throws -> NativeWindowBinding {
 scope.operationCount += 7
 if scenario == "bound_restart" { startChanged=true }
 if scenario == "bound_metadata_changed" { metadataChanged=true }
 if scenario == "batch_failed" || scenario == "attribute_failed" { scope.readinessAXError = -25204 }
 if scenario == "batch_failed" { scope.readinessFailure = ["stage":"tree","attribute":"batch","source":"batch_call",
  "kind":"batch_failed","depth":1,"visit":2,"ax_error":-25204] }

 if scenario == "bound_document_live_changed" { documentLiveChanged=true }
 if scenario == "bound_document_replaced" { documentTokens[0].document.selectedPath="/other/doc.txt" }
 if scenario == "bound_document_removed" { documentTokens=[] }
 if scenario == "replace_map" { latestElements = [NSNumber(value: 2)] }
 if scenario == "mutate_map" { latestElements[0] = NSNumber(value: 2) }
 if scenario == "equal_map" { latestElements = [element] }
 if scenario == "restore_map" {
  latestElements.append(NSNumber(value: 2)); latestElements.removeLast()
 }
 try scope.ready()
 let why: String? = scenario == "nodes" ? "readiness budget exceeded: nodes" :
  scenario == "deadline" ? "readiness budget exceeded: deadline" :
  scenario == "titlebar" ? "no blank native titlebar point" :
  scenario == "private" ? "PRIVATE_DOCUMENT_SENTINEL" :
  scenario == "batch_failed" ? "readiness batch failed" :
  scenario == "batch_malformed" ? "readiness batch malformed" :
  scenario == "attribute_failed" ? "window state unavailable: AXRole" : nil
 if let why { throw NativeWindowInputError.refused(why) }
 return NativeWindowBinding()
}
func captureNativePopupWindowBinding(_ el:AXUIElement,_ popup:AXUIElement,_ pid:pid_t,
 expectedStart:Double,scope:NativeWindowReadScope) throws -> NativeWindowBinding {
 return NativeWindowBinding()
}
func requireNativeWindowTarget(_ b: NativeWindowBinding, scope: NativeWindowReadScope) throws {}
func requireNativeKeyTarget(_ b: NativeWindowBinding, element: Bool, scope: NativeWindowReadScope) throws {}
func requireNativeSubmitTarget(_ b: NativeWindowBinding, scope: NativeWindowReadScope) throws {}
func validateNativeWindowBinding(_ b: NativeWindowBinding, scope: NativeWindowReadScope) throws {}
func windowInputCGFrame(_ id:UInt32,_ pid:pid_t) throws -> Int {1}
func dispatchNativeWindowDrag(_ b:NativeWindowBinding,drag:NativeWindowDrag,deadline:Double,
 stopped:()->Bool,context:()throws->Void,validate:()throws->Void,
 sameProcess:()->Bool,windowUnchanged:()throws->Void) throws -> DispatchResult {
 fatalError("drag is tested separately; preparation cases must not dispatch it")
}
func dispatchNativeWindowPreparation(_ b: NativeWindowBinding, deadline: Double, stopped: () -> Bool,
 context: () throws -> Void, validate: (NativeWindowBinding) throws -> Void, key: String?,
 routing: ((NativeWindowBinding, Bool) throws -> Void)?, preflight: (() throws -> Void)?,
 pointClick: Bool, windowInput: NativeOpaqueWindowInput?, elementClicks: Int, submitKey: Bool,
 finalTransition: ((_ checkpoint: () throws -> Void) throws -> Void)?,
 headroomDiagnostic: (([String:Any])->Void)? = nil) throws -> DispatchResult {
 dispatched += 1
 let focusReasons = ["focus_missing":"user focus unavailable",
                     "foreground_changed":"user foreground changed",
                     "focus_deadline":"user focus deadline expired",
                     "target_window":"target window is not the app focused window",
                     "target_input":"target input element or window changed",
                     "target_outside":"focus is outside the bound window",
                     "target_not_focused":"the bound input is not the keyboard focus",
                     "target_row_not_focused":"the selected row is not in the focused list",
                     "target_window_after_input":"target window is not the app focused window"]
 if let reason = focusReasons[scenario] {
  throw NativeWindowDispatchFailure(stopped:false, attempted:scenario == "target_window_after_input" ? 1:0, detail:reason)
 }
 if scenario == "headroom" {
  throw NativeWindowDispatchFailure(stopped:false, attempted:0, detail:"window validation headroom insufficient")
 }
 if scenario == "dispatch" { throw NativeWindowDispatchFailure(stopped:false, attempted:1, detail:"injected dispatch failure") }
 return DispatchResult()
}
'''.replace('ELEMENT_STATE', element_state) + fn + '''
var rows: [[String:Any]] = []
for mode in ["success", "identity", "context", "nodes", "deadline", "titlebar", "private", "dispatch",
             "replace_map", "mutate_map", "equal_map", "restore_map",
             "focus_missing", "foreground_changed", "focus_deadline", "headroom",
             "target_window", "target_input", "target_outside", "target_window_after_input",
             "target_not_focused", "target_row_not_focused",
             "bound_process", "bound_restart", "bound_metadata_changed", "batch_failed", "batch_malformed", "attribute_failed", "bound_document", "bound_document_replaced", "bound_document_removed", "bound_document_live_changed",
             "popup_anchor", "popup_bad_path", "popup_missing_anchor", "popup_duplicate_anchor", "anchor_without_popup"] {
 scenario = mode; dispatched = 0;startChanged=false;metadataChanged=false;documentLiveChanged=false
 latestElements = [element]
 latestDocumentToken = mode.hasPrefix("bound_document") ? "owned-doc" : nil
 documentTokens = latestDocumentToken == nil ? []:[(token:"owned-doc",document:PinnedDocument(pid:7,stamp:1,window:NSNumber(value:2),body:element))]
 lastIncrementalSupplied = false
 if mode=="popup_missing_anchor" {latestElements=[]}
 if mode=="popup_duplicate_anchor" {latestElements=[element,element]}
 let anchored=mode.hasPrefix("popup_") || mode=="anchor_without_popup"
 let result = anchored ? prepareBackgroundWindow("o", "fixture",NSNumber(value:3),7,deadline:Double.infinity,
  elementClicks:1,popup:mode=="anchor_without_popup" ? nil:NSNumber(value:2),observedControl:element,
  selectionCheck:{_ in},finalTransition:{_,_ in}) : prepareBackgroundWindow("o", "fixture", element, 7, deadline: Double.infinity)
 rows.append(["case": mode, "type": result["type"]!, "effect": result["effect"] ?? "unverified",
              "diagnostic": result["preparation_diagnostic"]!, "dispatch_calls": dispatched,
              "incremental_supplied":lastIncrementalSupplied])
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
                    'dispatch': ('dispatch', 'unclassified_refusal'),
                    'replace_map': ('binding', 'identity_changed'),
                    'mutate_map': ('binding', 'identity_changed'),
                    'equal_map': ('binding', 'identity_changed'),
                    'restore_map': ('binding', 'identity_changed'),
                    'focus_missing': ('dispatch', 'user_focus_unavailable'),
                    'foreground_changed': ('dispatch', 'user_foreground_changed'),
                    'focus_deadline': ('dispatch', 'user_focus_deadline'),
                    'headroom': ('dispatch', 'validation_headroom'),
                    'target_window': ('dispatch','target_window_not_focused'),
                    'target_input': ('dispatch','target_input_changed'),
                    'target_outside': ('dispatch','target_focus_outside_window'),
                    'target_not_focused': ('dispatch','target_input_not_focused'),
                    'target_row_not_focused': ('dispatch','target_row_not_in_focused_list'),
                    'target_window_after_input': ('dispatch','target_window_not_focused'),
                    'bound_process': ('dispatch','dispatched_unverified'),
                    'bound_restart': ('binding','identity_changed'),
                    'bound_metadata_changed': ('binding','identity_changed'),
                    'batch_failed': ('binding','readiness_batch_failed'),
                    'batch_malformed': ('binding','readiness_batch_malformed'),
                    'attribute_failed': ('binding','readiness_attribute_failed'),
                    'bound_document': ('dispatch','dispatched_unverified'),
                    'bound_document_replaced': ('binding','identity_changed'),
                    'bound_document_removed': ('binding','identity_changed'),
                    'bound_document_live_changed': ('binding','identity_changed')}
        for name, pair in expected.items():
            with self.subTest(name=name):
                row, detail = rows[name], rows[name]['diagnostic']
                self.assertEqual(pair, (detail['phase'], detail['reason']))
                self.assertGreaterEqual(detail['elapsed_ms'], 0)
                self.assertLessEqual(detail['elapsed_ms'], 600000)
                self.assertEqual(0 if name in ('identity', 'context') else 7, detail['scoped_ax_operations'])
                self.assertEqual(1 if name in ('success', 'bound_process','bound_document','dispatch', 'focus_missing',
                                              'foreground_changed', 'focus_deadline', 'headroom',
                                              'target_window','target_input','target_outside','target_window_after_input',
                                              'target_not_focused','target_row_not_focused') else 0,
                                 row['dispatch_calls'])
                if name not in ('success','bound_process','bound_document'):
                    self.assertEqual('unknown' if name in ('dispatch','target_window_after_input') else 'not_started', row['effect'])
        for name,cause in {'identity':'initial_binding','replace_map':'element_map',
                           'mutate_map':'element_map','equal_map':'element_map','restore_map':'element_map',
                           'bound_restart':'process_start','bound_metadata_changed':'target_resolution',
                           'bound_document_replaced':'document_pin','bound_document_removed':'document_pin',
                           'bound_document_live_changed':'document_live'}.items():
            self.assertEqual(cause,rows[name]['diagnostic'].get('identity_cause'),name)
        self.assertNotIn('identity_cause',rows['success']['diagnostic'])
        self.assertEqual('acted',rows['popup_anchor']['type'])
        self.assertEqual(1,rows['popup_anchor']['dispatch_calls'])
        for name in ('popup_bad_path','popup_missing_anchor','popup_duplicate_anchor','anchor_without_popup'):
            self.assertEqual('not_started',rows[name]['effect'],name)
            self.assertEqual(0,rows[name]['dispatch_calls'],name)
        self.assertFalse(rows['success']['incremental_supplied'])
        self.assertTrue(rows['bound_document']['incremental_supplied'])
        self.assertTrue(rows['bound_process']['incremental_supplied'])
        self.assertEqual(rows['batch_failed']['diagnostic']['readiness_ax_error'],-25204)
        self.assertNotIn('readiness_ax_error',rows['batch_malformed']['diagnostic'])
        self.assertEqual(rows['batch_failed']['diagnostic']['readiness_failure'],
            {'stage':'tree','attribute':'batch','source':'batch_call','kind':'batch_failed',
             'depth':1,'visit':2,'ax_error':-25204})
        self.assertNotIn('readiness_failure',rows['batch_malformed']['diagnostic'])
        self.assertNotIn('PRIVATE_DOCUMENT_SENTINEL', run.stdout)

    def test_actual_scope_counts_only_started_ax_operations(self):
        source = (ROOT / 'helper/WindowInput.swift').read_text()
        scope = source[source.index('final class NativeWindowReadScope {'):]
        scope = scope[:scope.index('\n}\n') + 3]
        script = '''import Foundation
import CoreFoundation
typealias AXUIElement = Int
func CFEqual(_ left:Int,_ right:Int)->Bool {left==right}
enum AXError:Int32 { case success=0, failure = -25200 }
enum NativeWindowInputError: Error { case refused(String), unavailable(String) }
var failSet = false
var failReset = false
var copies = 0
func AXUIElementSetMessagingTimeout(_ element: Int, _ timeout: Float) -> AXError {
 return (timeout > 0 && failSet) || (timeout == 0 && failReset) ? .failure : .success
}
func AXUIElementCopyAttributeValue(_ element: Int, _ name: CFString, _ out: inout CFTypeRef?) -> AXError {
 copies += 1; Thread.sleep(forTimeInterval:0.01); out = NSNumber(value:1); return .success
}
''' + scope + '''
var rows: [[String:Any]] = []
for mode in ["normal", "expired", "set_failure", "reset_failure"] {
 copies = 0; failSet = mode == "set_failure"; failReset = mode == "reset_failure"
 let scope = NativeWindowReadScope(deadline: ProcessInfo.processInfo.systemUptime + (mode == "expired" ? -1 : 30), check: { Thread.sleep(forTimeInterval:0.005) })
 var refused = false
 do { _ = try scope.read(1, "AXRole") } catch { refused = true }
 rows.append(["case":mode, "count":scope.operationCount, "copies":copies, "refused":refused, "context_ms":scope.contextElapsedMS, "operation_ms":scope.operationElapsedMS])
}
print(String(data:try! JSONSerialization.data(withJSONObject:rows),encoding:.utf8)!)
'''
        with tempfile.TemporaryDirectory(prefix='nlc-read-scope-counter-') as directory:
            path = Path(directory) / 'main.swift'; path.write_text(script)
            run = subprocess.run(['swift', str(path)], capture_output=True, text=True, timeout=60)
        self.assertEqual(0, run.returncode, run.stderr)
        rows = json.loads(run.stdout)
        for row in rows:
            self.assertGreater(row['context_ms'],0)
            self.assertEqual(row['copies']>0,row['operation_ms']>0)
            self.assertEqual(row['copies'], row['count'])
            self.assertEqual(row['case'] != 'normal', row['refused'])
            self.assertEqual(0 if row['case'] in ('expired', 'set_failure') else 1, row['count'])


if __name__ == '__main__':
    unittest.main()
