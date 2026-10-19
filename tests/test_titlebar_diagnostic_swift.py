"""Run the actual titlebar diagnostic against bounded, read-only AX doubles."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('swift'), 'Swift required')
class TitlebarDiagnosticTests(unittest.TestCase):
    def test_foreign_hits_margin_failures_and_budget_are_distinct(self):
        source = (ROOT/'helper/WindowInput.swift').read_text()
        def function(name):
            text = source[source.index('func '+name+'('):]
            return text[:text.index('\n}\n')+3]
        harness = r'''
import Foundation
import AppKit
import ApplicationServices
enum NativeWindowInputError: Error { case refused(String) }
let window = AXUIElementCreateApplication(1), close = AXUIElementCreateApplication(2)
let foreign = AXUIElementCreateApplication(3)
var mode = "ok", hits = 0, ancestry = 0, exhausted = false
final class NativeWindowReadScope {
 func ready(forceFull:Bool=false)throws { if exhausted {throw NativeWindowInputError.refused("budget")} }
 func withTimeout<T>(_ node:AXUIElement,_ action:()throws->T)throws->T {try ready();return try action()}
}
func windowInputRectIsValid(_ rect:CGRect)->Bool {rect.width>0 && rect.height>0}
func windowInputFrame(_ node:AXUIElement,scope:NativeWindowReadScope?)throws->CGRect {
 CFEqual(node,close) ? CGRect(x:8,y:6,width:14,height:14):CGRect(x:0,y:0,width:400,height:200)
}
func windowInputAttribute(_ node:AXUIElement,_ name:String,scope:NativeWindowReadScope?)throws->CFTypeRef {
 name=="AXCloseButton" ? close : "AXCloseButton" as CFString
}
func windowInputElement(_ raw:CFTypeRef)throws->AXUIElement {raw as! AXUIElement}
func windowInputPID(_ node:AXUIElement)throws->pid_t {CFEqual(node,foreign) ? 99:42}
func windowInputAncestor(_ node:AXUIElement,_ pid:pid_t,scope:NativeWindowReadScope?)throws->AXUIElement {
 ancestry+=1
 if CFEqual(node,foreign) {throw NativeWindowInputError.refused("cross process")}
 return window
}
func windowInputRole(_ node:AXUIElement,scope:NativeWindowReadScope?)throws->String {
 mode=="private" ? "PRIVATE_ROLE":"AXWindow"
}
func windowInputHit(_ point:CGPoint,_ pid:pid_t,scope:NativeWindowReadScope?)throws->AXUIElement {
 hits+=1
 if mode=="budget" {exhausted=true;throw NativeWindowInputError.refused("budget")}
 if mode=="margin" && hits==2 {throw NativeWindowInputError.refused("unavailable")}
 return mode=="foreign" ? foreign:window
}
func AXUIElementCopyActionNames(_ node:AXUIElement,_ out:UnsafeMutablePointer<CFArray?>)->AXError {
 out.pointee=[] as CFArray;return .success
}
'''
        checks = r'''
var results:[[String:Any]]=[]
for name in ["ok","foreign","margin","private","budget"] {
 mode=name;hits=0;ancestry=0;exhausted=false
 do {
  let rows=try nativeTitlebarHitSummary(window,42,scope:NativeWindowReadScope())
  results.append(["mode":name,"rows":rows,"ancestry":ancestry,"threw":false])
 } catch {results.append(["mode":name,"threw":true])}
}
print(String(data:try! JSONSerialization.data(withJSONObject:results),encoding:.utf8)!)
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'main.swift'
            path.write_text(harness + ''.join(function(name) for name in (
                'nativeTitlebarPoints', 'nativeTitlebarPointIsClear', 'nativeTitlebarHitSummary')) + checks)
            result = subprocess.run(['swift', str(path)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('PRIVATE', result.stdout)
        rows = {item['mode']: item for item in json.loads(result.stdout)}
        self.assertTrue(all(row['clear_margin'] for row in rows['ok']['rows']))
        self.assertEqual(len(rows['ok']['rows']), 15)
        self.assertTrue(all(row['status']=='cross_process' for row in rows['foreign']['rows']))
        self.assertEqual(rows['foreign']['ancestry'], 1)  # Close anchor only.
        self.assertFalse(rows['margin']['threw'])
        self.assertEqual(rows['margin']['rows'][0]['status'], 'margin_unavailable')
        self.assertFalse(rows['margin']['rows'][0]['clear_margin'])
        self.assertTrue(all(row['role']=='other' for row in rows['private']['rows']))
        self.assertTrue(rows['budget']['threw'])
