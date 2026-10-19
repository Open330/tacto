"""Actual receiving guard and dispatcher refuse late positives after two posts."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]

def extract(text,prefix):
    part=text[text.index(prefix):]
    return part[:part.index('\n}\n')+3]

@unittest.skipUnless(sys.platform=='darwin' and shutil.which('xcrun'),'macOS Swift required')
class ComboReceivingDeadlineSwiftTests(unittest.TestCase):
    def test_nested_deadlines_never_extend_and_restore_after_all_failures(self):
        scope=extract((ROOT/'helper/WindowInput.swift').read_text(),'final class NativeWindowReadScope {')
        support=r'''
import Foundation
import CoreFoundation
typealias AXUIElement=Int
func CFEqual(_ left:Int,_ right:Int)->Bool {left==right}
enum AXError:Int32 {case success=0,failure = -25200}
enum NativeWindowInputError:Error {case refused(String),unavailable(String)}
var lastTimeout:Float=0
func AXUIElementSetMessagingTimeout(_ node:Int,_ value:Float)->AXError {if value>0 {lastTimeout=value};return .success}
func AXUIElementCopyAttributeValue(_ node:Int,_ name:CFString,_ out:inout CFTypeRef?)->AXError {.success}
'''
        body=r'''
var rows:[[String:Any]]=[]
for mode in ["normal","late_body","late_read","nested_larger","throw_body","invalid_nan","invalid_inf","extends_parent","expired"] {
 let scope=NativeWindowReadScope(deadline:ProcessInfo.processInfo.systemUptime+30,check:{})
 let until=mode=="invalid_nan" ? Double.nan:mode=="invalid_inf" ? Double.infinity:mode=="extends_parent" ? scope.deadline+1:ProcessInfo.processInfo.systemUptime+(mode=="expired" ? -1:0.07)
 var emitted=0,bodies=0
 do {
  _=try scope.withReadDeadline(until) {
   bodies+=1
   if mode=="throw_body" {throw NativeWindowInputError.refused("body")}
   if mode=="late_body" {Thread.sleep(forTimeInterval:0.12)}
   if mode=="late_read" {_=try scope.withTimeout(1) {Thread.sleep(forTimeInterval:0.12);return true}}
   if mode=="nested_larger" {
    _=try scope.withReadDeadline(ProcessInfo.processInfo.systemUptime+0.5) {
     Thread.sleep(forTimeInterval:0.12)
     return try scope.withTimeout(1) {true}
    }
   }
   return true
  }
  emitted+=1
 } catch {}
 let before=scope.operationCount
 _=try! scope.withTimeout(1) {true}
 rows.append(["mode":mode,"emitted":emitted,"bodies":bodies,"operations_before_restore":before,
  "outside_operations":scope.operationCount-before,"outside_timeout":lastTimeout])
}
print(String(data:try! JSONSerialization.data(withJSONObject:rows),encoding:.utf8)!)
'''
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'main.swift';path.write_text(support+scope+body)
            result=subprocess.run(['swift',str(path)],capture_output=True,text=True,timeout=60)
        self.assertEqual(0,result.returncode,result.stderr)
        rows={row['mode']:row for row in json.loads(result.stdout)}
        for name,row in rows.items():
            self.assertEqual(int(name=='normal'),row['emitted'],name)
            self.assertEqual(1,row['outside_operations'])
            self.assertEqual(0.5,row['outside_timeout'])
            self.assertEqual(int(name not in ('invalid_nan','invalid_inf','extends_parent','expired')),row['bodies'])
        self.assertEqual(1,rows['late_read']['operations_before_restore'])
        self.assertEqual(0,rows['nested_larger']['operations_before_restore'])

    def test_actual_dispatcher_late_value_checkpoint_abort_and_user_change_keep_attempted_two(self):
        confirm=extract((ROOT/'helper/TactoHelper.swift').read_text(),'func confirmComboBoxSelectionTransition(')
        support=r'''
import Foundation
import AppKit
import ApplicationServices
let control=AXUIElementCreateApplication(10001),menu=AXUIElementCreateApplication(10002),focus=AXUIElementCreateApplication(10003)
let binding=NativeWindowBinding(element:control,window:menu,pid:42,processStart:10,
 windowID:17,windowBounds:CGRect(x:100,y:200,width:300,height:200),
 elementBounds:CGRect(x:110,y:230,width:100,height:40),point:CGPoint(x:160,y:250))
var mode="valid",stopped=false,valueCalls=0,maxTimeout:Float=0
func AXUIElementSetMessagingTimeout(_ node:AXUIElement,_ seconds:Float)->AXError {if seconds>0 {maxTimeout=max(maxTimeout,seconds)};return .success}
func comboBoxListVisibility(_ node:AXUIElement,_ pid:pid_t,scope:NativeWindowReadScope?=nil)->Bool? {
 guard let scope else {fatalError("unscoped visibility")}
 return try? scope.withTimeout(node) {(mode=="not_closed" || mode=="nested_short")}
}
func rereadSelection(_ pid:pid_t,role:String,identity:String?,control:AXUIElement?=nil,scope:NativeWindowReadScope?=nil)->String? {
 guard let scope,let control else {fatalError("unscoped value")}
 return try? scope.withTimeout(control) {
  valueCalls+=1
  if mode=="late_value" {Thread.sleep(forTimeInterval:0.45)}
  if mode=="abort_value" {stopped=true}
  return mode=="wrong_value" ? "Alpha":"Beta"
 }
}
'''
        body=r'''
var rows:[[String:Any]]=[]
for name in ["valid","not_closed","wrong_value","late_value","late_checkpoint","abort_value","user_changed","nested_short"] {
 mode=name;stopped=false;valueCalls=0;maxTimeout=0
 var posts=0,receiving=0,checkpoints=0,accepted=false,attempted=0
 let scope=NativeWindowReadScope(deadline:ProcessInfo.processInfo.systemUptime+30,
  check:{if stopped {throw NativeWindowInputError.refused("user stopped input")}})
 let started=ProcessInfo.processInfo.systemUptime
 do {
  let result=try dispatchNativeWindowPreparation(binding,deadline:scope.deadline,stopped:{stopped},context:{},
   readInput:{_ in NativeUserInputState(foreground:77,pointer:name=="user_changed" && posts==2 ? CGPoint(x:1,y:1):.zero,focused:focus)},
   validate:{_ in},post:{_,_ in posts+=1},routing:{_,_ in},elementClicks:1,
   finalTransition:{checkpoint in
    receiving+=1
    if name=="nested_short" {
     try scope.withReadDeadline(ProcessInfo.processInfo.systemUptime+0.07) {
    try confirmComboBoxSelectionTransition(control,menu,42,role:"AXComboBox",identity:"known",want:"Beta",scope:scope,
     checkpoint:{checkpoints+=1;try checkpoint();if name=="late_checkpoint" && checkpoints==2 {Thread.sleep(forTimeInterval:0.45)}})
    }
    } else {
     try confirmComboBoxSelectionTransition(control,menu,42,role:"AXComboBox",identity:"known",want:"Beta",scope:scope,
      checkpoint:{checkpoints+=1;try checkpoint();if name=="late_checkpoint" && checkpoints==2 {Thread.sleep(forTimeInterval:0.45)}})
    }
   })
  accepted=true;attempted=result.posted
 } catch let failure as NativeWindowDispatchFailure {attempted=failure.attempted}
 catch {fatalError("unexpected dispatch error")}
 let before=scope.operationCount
 stopped=false;try! scope.ready()
 rows.append(["mode":name,"accepted":accepted,"posts":posts,"attempted":attempted,"receiving":receiving,
  "value_calls":valueCalls,"operations":before,"max_timeout":maxTimeout,
  "elapsed":ProcessInfo.processInfo.systemUptime-started])
}
print(String(data:try! JSONSerialization.data(withJSONObject:rows),encoding:.utf8)!)
'''
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'main.swift';binary=Path(directory)/'check';path.write_text(support+confirm+body)
            build=subprocess.run(['xcrun','swiftc','-warnings-as-errors',str(path),str(ROOT/'helper/WindowInput.swift'),str(ROOT/'helper/WindowDispatch.swift'),str(ROOT/'helper/FrontmostProcess.swift'),'-o',str(binary)],capture_output=True,text=True,timeout=90)
            self.assertEqual(0,build.returncode,build.stderr)
            result=subprocess.run([str(binary)],capture_output=True,text=True,timeout=15)
        self.assertEqual(0,result.returncode,result.stderr)
        rows={row['mode']:row for row in json.loads(result.stdout)}
        for name,row in rows.items():
            self.assertEqual(name=='valid',row['accepted'],name)
            self.assertEqual(2,row['posts'],name)
            self.assertEqual(2,row['attempted'],name)
            self.assertLessEqual(row['max_timeout'],0.401)
        self.assertEqual(1,rows['valid']['value_calls'])
        self.assertEqual(0,rows['not_closed']['value_calls'])
        self.assertEqual(1,rows['late_value']['value_calls'])
        self.assertEqual(1,rows['abort_value']['value_calls'])
        self.assertEqual(1,rows['late_checkpoint']['value_calls'])
        self.assertEqual(0,rows['user_changed']['receiving'])

        self.assertEqual(0,rows["nested_short"]["value_calls"])
        self.assertLess(rows["nested_short"]["elapsed"],0.2)
        self.assertLessEqual(rows["nested_short"]["max_timeout"],0.071)
