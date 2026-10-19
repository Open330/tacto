"""Compile the helper's actual ComboBox opening target guard with injected AX."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('swift'),'Swift required')
class ComboOpenButtonTests(unittest.TestCase):
    def test_combo_container_and_rows_refuse_cross_owner_parent_shape_and_scalar_errors(self):
        source=(ROOT/'helper/TactoHelper.swift').read_text()
        def extract(name):
            part=source[source.index('func '+name+'('):]
            return part[:part.index('\n}\n')+3]
        support=r'''
import Foundation
import ApplicationServices
let nodes=(10..<16).map { AXUIElementCreateApplication(pid_t($0)) }
let maxMenuOptions=200
var mode="valid"
func index(_ node:AXUIElement)->Int { nodes.firstIndex { CFEqual($0,node) }! }
func str(_ node:AXUIElement,_ key:String)->String? {
 let i=index(node)
 if mode=="bad_role" && i==3 { return "AXButton" }
 return ["AXComboBox","AXScrollArea","AXList","AXGroup","AXTextField","AXScrollBar"][i]
}
func AXUIElementGetPid(_ node:AXUIElement,_ output:UnsafeMutablePointer<pid_t>)->AXError {
 output.pointee=(mode=="cross_owner" && index(node)==4) || (mode=="bar_cross_owner" && index(node)==5) ? 99:41
 return .success
}
class NativeWindowReadScope {
 func read(_ node:AXUIElement,_ name:String) throws -> (AXError,CFTypeRef?) {let value=attr(node,name);return(value == nil ? .noValue:.success,value)}
}
func nativeReadinessBatch(_ node:AXUIElement,names:[String],scope:NativeWindowReadScope?=nil) throws -> [(AXError,CFTypeRef?)]? {
 names.map { let value=attr(node,$0);return (value == nil ? .noValue:.success,value) }
}
func attr(_ node:AXUIElement,_ key:String)->CFTypeRef? {
 let i=index(node)
 if key=="AXRole" {return str(node,key)! as CFString}
 if key=="AXChildren" {
  if i==1 && mode.hasPrefix("bar_") {return mode=="bar_duplicate" ? [nodes[2],nodes[5],nodes[5]] as CFArray:[nodes[2],nodes[5]] as CFArray}
  if mode=="missing_children" && i==0 { return nil }
  if mode=="duplicate_scroll" && i==0 { return [nodes[1],nodes[1]] as CFArray }
  if mode=="duplicate_rows" && i==2 {return [nodes[3],nodes[3]] as CFArray}
  if mode=="too_many_rows" && i==2 { return Array(repeating:nodes[3],count:201) as CFArray }
  if mode=="malformed_rows" && i==2 { return [NSNumber(value:1)] as CFArray }
  if mode=="multiple_cells" && i==3 { return [nodes[4],nodes[4]] as CFArray }
  return i<4 ? [nodes[i+1]] as CFArray:[] as CFArray
 }
 if key=="AXParent" {
  if i==5 {return mode=="bar_wrong_parent" ? nodes[0]:nodes[1]}
  if mode=="wrong_list_parent" && i==2 { return nodes[0] }
  if mode=="wrong_row_parent" && i==3 { return nodes[0] }
  return i>0 ? nodes[i-1]:nil
 }
 if key=="AXValue" { return mode=="numeric_value" ? NSNumber(value:1):"Beta" as CFString }
 if key=="AXEnabled" {
  if mode=="disabled_row" {return false as CFBoolean}
  if mode=="missing_enabled" { return nil }
  if mode=="numeric_enabled" { return NSNumber(value:1) }
  return true as CFBoolean
 }
 return nil
}
'''
        body=r'''
var result:[String:Bool]=[:]
for value in ["valid","cross_owner","bad_role","missing_children","duplicate_scroll","too_many_rows","malformed_rows","multiple_cells","wrong_list_parent","wrong_row_parent","numeric_value","missing_enabled","numeric_enabled","bar_valid","bar_cross_owner","bar_wrong_parent","bar_duplicate","wrong_popup","wrong_chosen","duplicate_rows","disabled_row"] {
 mode=value
 result[value]=comboBoxOwnedList(nodes[0],41) != nil && comboBoxListRows(nodes[2]) != nil && nativePopupSelectionAnchorMatches(nodes[0],mode=="wrong_chosen" ? nodes[4]:nodes[3],mode=="wrong_popup" ? nodes[0]:nodes[1],41)
}
print(String(data:try! JSONSerialization.data(withJSONObject:result),encoding:.utf8)!)
'''
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'main.swift'
            path.write_text(support+extract('comboBoxAttribute')+extract('comboBoxString')+extract('comboBoxChildren')+extract('comboBoxOwnedList')+extract('comboBoxListAttributes')+extract('comboBoxListRows')+extract('nativePopupSelectionAnchorMatches')+body)
            result=subprocess.run(['swift',str(path)],capture_output=True,text=True,timeout=30)
        self.assertEqual(0,result.returncode,result.stderr)
        values=json.loads(result.stdout);self.assertTrue(values.pop('valid'));self.assertTrue(values.pop('bar_valid'))
        self.assertTrue(all(value is False for value in values.values()),values)

    def test_unique_direct_owned_enabled_press_button_and_closed_failures(self):
        source=(ROOT/'helper/TactoHelper.swift').read_text()
        function=source[source.index('func comboBoxOpenButton('):]
        function=function[:function.index('\n}\n')+3]
        children=source[source.index('func comboBoxChildren('):]
        children=children[:children.index('\n}\n')+3]
        support=r'''
import Foundation
import ApplicationServices
class NativeWindowReadScope {
 func read(_ node:AXUIElement,_ name:String) throws -> (AXError,CFTypeRef?) {let value=attr(node,name);return(value == nil ? .noValue:.success,value)}
}
let control=AXUIElementCreateApplication(41),button=AXUIElementCreateApplication(42),other=AXUIElementCreateApplication(43)
var mode="valid"
func str(_ node:AXUIElement,_ key:String)->String? {
 if CFEqual(node,control) { return mode=="not_combo" ? "AXPopUpButton":"AXComboBox" }
 if mode=="unreadable_role" { return nil }
 return "AXButton"
}
func attr(_ node:AXUIElement,_ key:String)->CFTypeRef? {
 if key=="AXChildren" {
  if mode=="missing_children" { return nil }
  if mode=="duplicate_buttons" { return [button,other] as CFArray }
  if mode=="too_many" { return Array(repeating:button,count:9) as CFArray }
  return [button] as CFArray
 }
 if key=="AXParent" { return mode=="wrong_parent" ? other:control }
 if key=="AXEnabled" {
  if mode=="missing_enabled" { return nil }
  if mode=="numeric_enabled" { return NSNumber(value:1) }
  return (mode != "disabled") as CFBoolean
 }
 return nil
}
func AXUIElementGetPid(_ node:AXUIElement,_ output:UnsafeMutablePointer<pid_t>)->AXError {
 output.pointee=mode=="cross_pid" && CFEqual(node,button) ? 99:41
 return mode=="pid_error" ? .cannotComplete:.success
}
func AXUIElementCopyActionNames(_ node:AXUIElement,_ output:UnsafeMutablePointer<CFArray?>)->AXError {
 output.pointee=(mode=="duplicate_action" ? ["AXPress","AXPress"]:mode=="unsupported" ? ["AXShowMenu"]:["AXPress"]) as CFArray
 return mode=="action_error" ? .cannotComplete:.success
}
'''
        body=r'''
var results:[String:Bool]=[:]
for test in ["valid","not_combo","unreadable_role","missing_children","duplicate_buttons","too_many","wrong_parent","missing_enabled","numeric_enabled","disabled","cross_pid","pid_error","duplicate_action","unsupported","action_error"] {
 mode=test;results[test]=comboBoxOpenButton(control,41) != nil
}
print(String(data:try! JSONSerialization.data(withJSONObject:results),encoding:.utf8)!)
'''
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'main.swift';path.write_text(support+source[source.index('func comboBoxAttribute('):source.index('func comboBoxListVisibility(')]+children+function+body)
            result=subprocess.run(['swift',str(path)],capture_output=True,text=True,timeout=30)
        self.assertEqual(0,result.returncode,result.stderr)
        values=json.loads(result.stdout)
        self.assertTrue(values.pop('valid'))
        self.assertTrue(all(value is False for value in values.values()),values)
