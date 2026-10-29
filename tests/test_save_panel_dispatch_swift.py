"""Run actual single-use guard and click branch against injected AX boundaries."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('swift'), 'macOS Swift required')
class SavePanelDispatchSwiftTests(unittest.TestCase):
    def test_actual_guard_bindings_single_use_and_uncertain_dispatch(self):
        source = (ROOT / 'helper/TactoHelper.swift').read_text()
        guard = source[source.index('func validateSavePanelDispatch('):source.index('func readSavePanel(')]
        click = source[source.index('    case "click":', source.index('func act(')):
                       source.index('    case "set_value":', source.index('func act('))]
        prelude = r'''
import Foundation
import ApplicationServices
struct StopState { var isStopped = false }
var abortState = StopState()
let window = AXUIElementCreateApplication(10001)
let button = AXUIElementCreateApplication(10002)
let other = AXUIElementCreateApplication(10003)
struct Witness { let commit: AXUIElement }
struct Pin { let pid: pid_t; let window: AXUIElement; let documentToken: String?; let witness: Witness }
var savePanelTokens: [(token:String, panel:Pin)] = []
var latestElements = [window,button]
var latestObsID: String? = "obs"
var latestTargetPID: pid_t = 42
var readerCalls = 0
var posts = 0
var mode = "good"
var actionError = AXError.success
func documentIsWholeNumber(_ raw: Any) -> NSNumber? {
    guard let n=raw as? NSNumber, CFGetTypeID(n) != CFBooleanGetTypeID(),
          n.doubleValue.isFinite, n.doubleValue == Double(n.intValue) else { return nil }
    return n
}
func readSavePanel(_ request:[String:Any], operationDeadline:Double?=nil) -> [String:Any] {
    readerCalls += 1
    precondition(request["require_format"] as? Bool == true)
    precondition(request["document_token"] as? String == "document")
    precondition(request["window_element_index"] as? Int == 0)
    precondition(operationDeadline != nil)
    if mode == "stale" {return ["type":"error"]}
    if mode == "stop" {abortState.isStopped=true}
    if mode == "late" {Thread.sleep(forTimeInterval:0.03)}
    return ["type":"save_panel", "panel_token":"panel",
        "commit_control":["obs_id":mode == "obs" ? "other" : "obs", "enabled":mode != "disabled",
                          "action":["name":"click","element_index":mode == "wrong" ? 0 : 1]]]
}
var advertisePress = true, pointerCalls = 0
func AXUIElementCopyActionNames(_ el:AXUIElement, _ names:UnsafeMutablePointer<CFArray?>) -> AXError {
    names.pointee = (advertisePress ? ["AXPress"] : ["AXShowMenu"]) as CFArray; return .success
}
func prepareBackgroundWindow(_ obsID:String, _ target:String, _ element:AXUIElement, _ pid:pid_t,
                             deadline:Double, expectation:[String:Any]?, elementClicks:Int) -> [String:Any] {
    pointerCalls += 1; return ["type":"acted", "input_route":"window_bound_element_click"]
}
// The list-selection route never applies to this button (no parent list).
func selectListItem(_ el:AXUIElement, deadline:Double) -> [String:Any]? { nil }
func selectTableRow(_ el:AXUIElement, deadline:Double) -> [String:Any]? { nil }
func AXUIElementPerformAction(_ el:AXUIElement, _ action:CFString) -> AXError {
    precondition(CFEqual(el,button)); precondition(savePanelTokens.isEmpty)
    posts += 1; return actionError
}
func axActionFailureEffect(_ error:AXError) -> String {"unknown"}
func reset() {
    abortState.isStopped=false; mode="good"; posts=0; readerCalls=0; actionError = .success
    advertisePress=true; pointerCalls=0
    latestElements=[window,button]; latestObsID="obs"; latestTargetPID=42
    savePanelTokens=[("panel",Pin(pid:42,window:window,documentToken:"document",witness:Witness(commit:button)))]
}
'''
        checked = r'''
func checked(_ token:String?="panel", _ duration:Double=1) -> [String:Any] {
    let obsID="obs", target="owned", action:[String:Any]=["name":"click","element_index":1]
    let name="click", savePanelToken:String?=token, deadline=ProcessInfo.processInfo.systemUptime+duration
    let p:pid_t=42, expect:[String:Any]?=nil
    func element() -> AXUIElement? {button}
    switch name {
''' + click + r'''
    default: break
    }
    return ["type":"acted"]
}
reset();precondition(checked()["type"] as? String == "acted");precondition(posts==1 && readerCalls==1)
precondition(checked()["effect"] as? String == "not_started");precondition(posts==1)
for bad in ["stale","obs","disabled","wrong","stop","late"] {
    reset(); mode=bad
    let result=checked("panel",bad == "late" ? 0.01 : 1)
    precondition(result["effect"] as? String == "not_started");precondition(posts==0)
}
reset();precondition(checked("foreign")["effect"] as? String == "not_started");precondition(readerCalls==0)
reset();latestObsID="other";precondition(checked()["effect"] as? String == "not_started");precondition(readerCalls==0)
reset();latestTargetPID=43;precondition(checked()["effect"] as? String == "not_started");precondition(readerCalls==0)
reset();latestElements=[window,other];precondition(checked()["effect"] as? String == "not_started");precondition(readerCalls==0)
reset();latestElements.append(window);precondition(checked()["effect"] as? String == "not_started");precondition(readerCalls==0)
reset();actionError=AXError(rawValue:-25204)!
precondition(checked()["unverified"] as? String == "cannot_complete");precondition(posts==1 && savePanelTokens.isEmpty)
precondition(checked()["effect"] as? String == "not_started");precondition(posts==1)
// No AXPress: never a pointer click inside a save panel; outside one, a
// window-bound element click instead of a refusal.
reset();advertisePress=false
precondition(checked()["needs_pointer"] as? Bool == true);precondition(pointerCalls==0 && posts==0)
reset();advertisePress=false
precondition(checked(nil)["input_route"] as? String == "window_bound_element_click");precondition(pointerCalls==1 && posts==0)
print("SAVE_PANEL_DISPATCH_PASS")
'''
        with tempfile.TemporaryDirectory(prefix='tacto-save-dispatch-') as directory:
            script = Path(directory) / 'main.swift'
            script.write_text((ROOT / 'HelperModeProtocol.swift').read_text() + "\nfunc windowScopePointerRefusal() -> [String:Any]? { nil }\nfunc waitForResource(_ lease:TactoScopeLease,_ scope:String,deadline:Double? = nil) -> Bool { lease.take(scope) }\n" + prelude + guard + checked)
            result = subprocess.run(['swift', str(script)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'SAVE_PANEL_DISPATCH_PASS')
