"""The actual state classifier must distinguish malformed AX from closed."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]

@unittest.skipUnless(shutil.which('swift'),'Swift required')
class ComboContainerStateTests(unittest.TestCase):
    def test_invalid_control_frees_slot_only_when_pinned_popup_is_confirmed_absent(self):
        source=(ROOT/'helper/TactoHelper.swift').read_text()
        part=source[source.index('func selectionContainerIsOurs('):]
        function=part[:part.index('\n}\n')+3]
        support=r'''import Foundation
import ApplicationServices
let control=AXUIElementCreateApplication(20),container=AXUIElementCreateApplication(21),other=AXUIElementCreateApplication(22)
var mode="unknown"
enum ComboBoxContainerState {case closed,open(AXUIElement),unknown}
func str(_ node:AXUIElement,_ key:String)->String? {mode.hasPrefix("invalid_role_") ? nil:"AXList"}
func comboBoxContainerState(_ node:AXUIElement,_ pid:pid_t)->ComboBoxContainerState {
 switch mode {
 case "known_closed","closed_visible","closed_unknown":return .closed
 case "known_open":return .open(container)
 case "replaced":return .open(other)
 default:return .unknown
 }
}
func AXUIElementCopyAttributeValue(_ node:AXUIElement,_ key:CFString,_ value:UnsafeMutablePointer<CFTypeRef?>)->AXError {
 mode.hasPrefix("invalid_") ? .invalidUIElement:.cannotComplete
}
func nativePopupVisibility(_ id:CGWindowID,_ pid:pid_t) throws -> Bool {
 precondition(id==17 && pid==42)
 if mode=="invalid_unknown" || mode=="closed_unknown" || mode=="ended_unknown" {throw NSError(domain:"unreadable",code:1)}
 return mode != "invalid_absent" && mode != "timeout_absent" && mode != "known_closed" && mode != "invalid_role_absent" && mode != "ended_absent"
}
func appMenus(_ pid:pid_t)->[AXUIElement] {[]}
func nativeProcessInstanceEnded(_ pid:pid_t,expectedStart:Double?)->Bool {precondition(expectedStart==10);return mode.hasPrefix("ended_")}
'''
        body=r'''var result:[String:Bool]=[:]
for value in ["known_closed","closed_visible","closed_unknown","known_open","replaced","unknown","timeout_absent","invalid_present","invalid_unknown","invalid_absent","invalid_no_id","invalid_role_absent","invalid_role_visible","ended_absent","ended_visible","ended_unknown","ended_no_id"] {
 mode=value
 result[value]=selectionContainerIsOurs(control,container,42,knownPopupWindowID:value.hasSuffix("no_id") ? nil:17,knownProcessStart:10)
}
print(String(data:try! JSONSerialization.data(withJSONObject:result),encoding:.utf8)!)
'''
        with tempfile.TemporaryDirectory(prefix='tacto-combo-slot-') as directory:
            path=Path(directory)/'main.swift';path.write_text(support+function+body)
            result=subprocess.run(['swift',str(path)],capture_output=True,text=True,timeout=30)
        self.assertEqual(0,result.returncode,result.stderr)
        values=json.loads(result.stdout)
        for mode in ('known_closed','replaced','invalid_absent','invalid_role_absent','ended_absent'):self.assertFalse(values.pop(mode))
        self.assertTrue(all(values.values()),values)

    def test_unknown_reads_never_classify_as_closed_or_open(self):
        source=(ROOT/'helper/TactoHelper.swift').read_text()
        def extract(name):
            part=source[source.index('func '+name+'('):]
            return part[:part.index('\n}\n')+3]
        support=r'''
import Foundation
import ApplicationServices
class NativeWindowReadScope {
 func read(_ node:AXUIElement,_ name:String) throws -> (AXError,CFTypeRef?) {let value=attr(node,name);return(value == nil ? .noValue:.success,value)}
}
let nodes=(20..<25).map {AXUIElementCreateApplication(pid_t($0))}
var mode="closed"
func index(_ node:AXUIElement)->Int {nodes.firstIndex {CFEqual($0,node)}!}
func AXUIElementGetPid(_ node:AXUIElement,_ value:UnsafeMutablePointer<pid_t>)->AXError {
 if mode=="pid_error" {return .cannotComplete}
 value.pointee=mode=="cross_child_owner" && index(node)==1 ? 99:42
 return .success
}
func attr(_ node:AXUIElement,_ key:String)->CFTypeRef? {
 let i=index(node)
 if key=="AXRole" {
  if mode=="missing_role" {return nil}
  if mode=="numeric_role" {return NSNumber(value:1)}
  if mode=="unknown_child_role" && i==1 {return "AXGroup" as CFString}
  return ["AXComboBox","AXButton","AXScrollArea","AXList","AXTextField"][i] as CFString
 }
 if key=="AXParent" {
  if mode=="wrong_parent" {return nodes[4]}
  if mode=="numeric_parent" {return NSNumber(value:1)}
  return nodes[0]
 }
 if key=="AXChildren" {
  if mode=="missing_children" {return nil}
  if mode=="invalid_member" {return [NSNumber(value:1)] as CFArray}
  if mode=="empty_children" {return [] as CFArray}
  if mode=="duplicate_button" {return [nodes[1],nodes[1]] as CFArray}
  if mode=="duplicate_scroll" {return [nodes[1],nodes[2],nodes[2]] as CFArray}
  if mode=="editable_closed" {return [nodes[1],nodes[4]] as CFArray}
  return mode=="closed" ? [nodes[1]] as CFArray:[nodes[1],nodes[2]] as CFArray
 }
 return nil
}
func comboBoxOwnedList(_ node:AXUIElement,_ pid:pid_t,scope:NativeWindowReadScope?=nil)->AXUIElement? {mode=="unreadable_list" ? nil:nodes[3]}
func comboBoxListVisibility(_ node:AXUIElement,_ pid:pid_t,scope:NativeWindowReadScope?=nil)->Bool? {mode=="unknown_visibility" ? nil:mode != "cached_closed"}
enum ComboBoxContainerState {case closed,open(AXUIElement),unknown}
'''
        body=r'''
var results:[String:String]=[:]
for value in ["closed","editable_closed","open","cached_closed","missing_children","invalid_member","empty_children","duplicate_button","duplicate_scroll","missing_role","numeric_role","unknown_child_role","wrong_parent","numeric_parent","pid_error","cross_child_owner","unreadable_list","unknown_visibility"] {
 mode=value
 switch comboBoxContainerState(nodes[0],42) {
 case .closed:results[value]="closed"
 case .open(let list):precondition(CFEqual(list,nodes[3]));results[value]="open"
 case .unknown:results[value]="unknown"
 }
}
print(String(data:try! JSONSerialization.data(withJSONObject:results),encoding:.utf8)!)
'''
        with tempfile.TemporaryDirectory(prefix='tacto-combo-state-') as directory:
            path=Path(directory)/'main.swift'
            path.write_text(support+extract('comboBoxAttribute')+extract('comboBoxString')+extract('comboBoxChildren')+extract('comboBoxContainerState')+body)
            result=subprocess.run(['swift',str(path)],capture_output=True,text=True,timeout=30)
        self.assertEqual(0,result.returncode,result.stderr)
        values=json.loads(result.stdout)
        for mode in ('closed','editable_closed','cached_closed'):self.assertEqual('closed',values.pop(mode))
        self.assertEqual('open',values.pop('open'))
        self.assertTrue(all(v=='unknown' for v in values.values()),values)
