"""Run the production popup capture and scope; fake AX performs no GUI input."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

@unittest.skipUnless(shutil.which('swift'), 'Swift required')
class PopupBindingContextTests(unittest.TestCase):
    def test_popup_capture_cadence_and_late_refusals(self):
        source = (ROOT / 'helper/WindowInput.swift').read_text()
        scope = source[source.index('final class NativeWindowReadScope {'):]
        scope = scope[:scope.index('\n}\n') + 3]
        source = (ROOT / 'helper/TactoHelper.swift').read_text()
        start = source.index('        func capture() throws -> NativeWindowBinding {')
        capture = source[start:source.index('        let binding=try capture()', start)]
        support = r'''
import Foundation
import CoreFoundation
typealias AXUIElement=Int
func CFEqual(_ left:Int,_ right:Int)->Bool {left==right}
enum AXError:Int32 {case success=0,failure = -25200}
enum NativeWindowInputError:Error {case refused(String),unavailable(String)}
func AXUIElementSetMessagingTimeout(_ node:Int,_ seconds:Float)->AXError {.success}
func AXUIElementCopyAttributeValue(_ node:Int,_ name:CFString,_ out:inout CFTypeRef?)->AXError {.success}
struct NativeWindowBinding {}
enum PointKind {case windowInput,windowPoint,titlebar,elementCenter}
var anchor=true,reads=0
func nativePopupSelectionAnchorMatches(_ control:Int,_ element:Int,_ popup:Int,_ pid:Int,scope:NativeWindowReadScope?=nil)->Bool {anchor}
func captureNativePopupWindowBinding(_ element:Int,_ popup:Int,_ pid:Int,expectedStart:Int,scope:NativeWindowReadScope) throws->NativeWindowBinding {
 try scope.withReadinessContext {
  for _ in 0..<272 {_=try scope.withTimeout(1) {reads+=1}}
 }
 return NativeWindowBinding()
}
func captureNativeWindowBinding(_ element:Int,_ pid:Int,expectedStart:Int,pointKind:PointKind,scope:NativeWindowReadScope,relativePoint:Int?) throws->NativeWindowBinding {NativeWindowBinding()}
var rows:[[String:Any]]=[]
for mode in ["exact","alias","identity","metadata","anchor","options","body"] {
 var full=0,fast=0,identity=true,metadata=true,emitted=0
 reads=0;anchor=mode != "anchor"
 let scope=NativeWindowReadScope(deadline:ProcessInfo.processInfo.systemUptime+30,
  incrementalCheck:mode=="alias" ? nil : {fast+=1;if !identity {throw NativeWindowInputError.refused("identity")}},
  check:{full+=1;if !identity || !metadata {throw NativeWindowInputError.refused("context")}})
 let popup:Int?=2,observedControl:Int?=3,key:String?=nil,point:Int?=nil,windowInput:Int?=nil
 let element=1,pid=42,start=10,elementClicks=1,submitKey=false
 let finalTransition:(() throws->Void)?={}
 let selectionCheck:((NativeWindowReadScope) throws->Void)?={_ in
  for _ in 0..<262 {
   // Existing option helpers use raw AX reads, outside scoped accounting.
   reads+=1
   if reads==3 && mode=="identity" {identity=false}
   if reads==3 && mode=="metadata" {metadata=false}
   if reads==3 && mode=="body" {throw NativeWindowInputError.refused("body")}
  }
  if mode=="options" {throw NativeWindowInputError.refused("options changed")}
 }
'''
        checks = r'''
 do {_=try capture();emitted+=1} catch {}
 let beforeFull=full,beforeFast=fast
 identity=true;metadata=true;try! scope.ready()
 rows.append(["mode":mode,"reads":reads,"full":beforeFull,"fast":beforeFast,
  "emitted":emitted,"outside_full":full-beforeFull,"outside_fast":fast-beforeFast])
}
print(String(data:try! JSONSerialization.data(withJSONObject:rows),encoding:.utf8)!)
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'main.swift'
            prefix, cases = support.split('var rows:[[String:Any]]=[]', 1)
            path.write_text(prefix + scope + 'var rows:[[String:Any]]=[]' + cases + capture + checks)
            result = subprocess.run(['swift', str(path)], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = {row['mode']: row for row in json.loads(result.stdout)}
        self.assertEqual(rows['exact']['reads'], 534)
        self.assertEqual(rows['alias']['reads'], 534)
        self.assertLess(rows['exact']['full'], 25)
        self.assertGreater(rows['alias']['full'], 540)
        self.assertEqual(rows['alias']['fast'], 0)
        self.assertEqual(rows['identity']['reads'], 262)
        self.assertEqual(rows['metadata']['reads'], 262)
        self.assertEqual(rows['anchor']['reads'], 262)
        self.assertEqual(rows['options']['reads'], 262)
        self.assertEqual(rows['body']['reads'], 3)
        for name, row in rows.items():
            self.assertEqual(row['emitted'], int(name in ('exact', 'alias')))
            self.assertEqual(row['outside_full'], 1)
            self.assertEqual(row['outside_fast'], 0)
