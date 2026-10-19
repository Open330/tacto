"""Actual process-end proof distinguishes ESRCH/reuse from unreadable metadata."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]

@unittest.skipUnless(shutil.which('swift'),'Swift required')
class ProcessInstanceEndedTests(unittest.TestCase):
    def test_pending_slot_requires_both_ended_process_and_absent_popup(self):
        source=(ROOT/'helper/TactoHelper.swift').read_text()
        part=source[source.index('func menuSlotBusy('):];function=part[:part.index('\n}\n')+3]
        harness=r'''import Foundation
import ApplicationServices
struct Pin {let pid:pid_t=42;let popupWindowID:CGWindowID?;let processStart:Double?=10}
struct Spent {let pid:pid_t;let control:AXUIElement;let menu:AXUIElement;let popupWindowID:CGWindowID?;let processStart:Double?}
var pendingSelect:Pin?,spentMenu:Spent?,mode="alive"
func nativeProcessInstanceEnded(_ pid:pid_t,expectedStart:Double?)->Bool {mode.hasPrefix("ended")}
func nativePopupVisibility(_ id:CGWindowID,_ pid:pid_t) throws -> Bool {
 if mode=="ended_unknown" {throw NSError(domain:"unknown",code:1)}
 return mode != "ended_absent" && mode != "alive_absent"
}
func selectionContainerIsOurs(_ control:AXUIElement,_ menu:AXUIElement,_ pid:pid_t,knownPopupWindowID:CGWindowID?,knownProcessStart:Double?)->Bool {true}
'''+function+r'''var results:[String:Bool]=[:]
for value in ["alive_absent","ended_absent","ended_visible","ended_unknown","ended_no_id"] {
 mode=value;pendingSelect=Pin(popupWindowID:value=="ended_no_id" ? nil:17)
 results[value]=menuSlotBusy()==nil
 precondition((pendingSelect==nil)==results[value]!)
}
print(String(data:try! JSONSerialization.data(withJSONObject:results),encoding:.utf8)!)
'''
        with tempfile.TemporaryDirectory(prefix='nlc-pending-slot-') as directory:
            path=Path(directory)/'main.swift';path.write_text(harness)
            result=subprocess.run(['swift',str(path)],capture_output=True,text=True,timeout=30)
        self.assertEqual(0,result.returncode,result.stderr)
        values=json.loads(result.stdout);self.assertTrue(values.pop('ended_absent'))
        self.assertFalse(any(values.values()),values)

    def test_missing_reused_alive_and_unknown_process_states(self):
        source=(ROOT/'helper/WindowInput.swift').read_text()
        part=source[source.index('func nativeProcessInstanceEnded('):]
        function=part[:part.index('\n}\n')+3]
        harness=r'''
import Foundation
import Darwin
func nativeProcessStartIdentity(_ pid:pid_t)->Double? {nil}
'''+function+r'''
var results:[String:Bool]=[:]
results["missing"]=nativeProcessInstanceEnded(42,expectedStart:10,probe:{_ in (-1,ESRCH)})
results["permission"]=nativeProcessInstanceEnded(42,expectedStart:10,probe:{_ in (-1,EPERM)})
results["unknown_error"]=nativeProcessInstanceEnded(42,expectedStart:10,probe:{_ in (-1,EIO)})
results["alive"]=nativeProcessInstanceEnded(42,expectedStart:10,probe:{_ in (0,0)},readStart:{_ in 10})
results["reused"]=nativeProcessInstanceEnded(42,expectedStart:10,probe:{_ in (0,0)},readStart:{_ in 11})
results["unreadable_start"]=nativeProcessInstanceEnded(42,expectedStart:10,probe:{_ in (0,0)},readStart:{_ in nil})
results["unknown_original"]=nativeProcessInstanceEnded(42,expectedStart:nil,probe:{_ in (0,0)},readStart:{_ in 11})
results["invalid_original"]=nativeProcessInstanceEnded(42,expectedStart:Double.nan,probe:{_ in (0,0)},readStart:{_ in 11})
results["invalid_current"]=nativeProcessInstanceEnded(42,expectedStart:10,probe:{_ in (0,0)},readStart:{_ in Double.nan})
results["invalid_pid"]=nativeProcessInstanceEnded(-1,expectedStart:10,probe:{_ in fatalError("invalid PID reached probe")})
print(String(data:try! JSONSerialization.data(withJSONObject:results),encoding:.utf8)!)
'''
        with tempfile.TemporaryDirectory(prefix='nlc-process-proof-') as directory:
            path=Path(directory)/'main.swift';path.write_text(harness)
            result=subprocess.run(['swift',str(path)],capture_output=True,text=True,timeout=30)
        self.assertEqual(0,result.returncode,result.stderr)
        values=json.loads(result.stdout)
        self.assertTrue(values.pop('missing'));self.assertTrue(values.pop('reused'))
        self.assertFalse(any(values.values()),values)
