"""Production pinned metadata chain and real read scope, no GUI input."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]

@unittest.skipUnless(shutil.which('swift'),'Swift required')
class ComboContainerScopeSwiftTests(unittest.TestCase):
    def test_container_anchor_popup_identity_and_original_value_share_deadline_and_abort(self):
        helper=(ROOT/'helper/TactoHelper.swift').read_text()
        window=(ROOT/'helper/WindowInput.swift').read_text()
        def extract(text,prefix):
            part=text[text.index(prefix):]
            return part[:part.index('\n}\n')+3]
        support=r'''
import Foundation
import ApplicationServices
import CoreGraphics
let nodes=(20000..<20007).map {ApplicationServices.AXUIElementCreateApplication(pid_t($0))}
let maxMenuOptions=200
var mode="normal",copies=0,batches=0,spis=0,cgs=0,sets=0,resets=0,stopped=false,identity=true,metadata=true
var maxTimeout:Float=0
func index(_ node:AXUIElement)->Int {nodes.firstIndex {CFEqual($0,node)}!}
func AXUIElementCreateApplication(_ pid:pid_t)->AXUIElement {nodes[0]}
func AXUIElementGetPid(_ node:AXUIElement,_ out:UnsafeMutablePointer<pid_t>)->AXError {out.pointee=42;return .success}
enum NativeWindowInputError:Error {case refused(String),unavailable(String)}
typealias AXBatchRead=(AXUIElement,[String]) throws -> (AXError,CFArray?)
enum ComboBoxContainerState {case closed,open(AXUIElement),unknown}
func change() {
 let operations=copies+batches+spis
 if operations==3 && mode=="abort" {stopped=true}
 if operations==5 && mode=="identity" {identity=false}
 if operations==5 && mode=="metadata" {metadata=false}
}
func AXUIElementSetMessagingTimeout(_ node:AXUIElement,_ seconds:Float)->AXError {
 if seconds>0 {sets+=1;maxTimeout=max(maxTimeout,seconds);if mode=="set_failure" {return .cannotComplete}}
 else {resets+=1;if mode=="reset_failure" {return .cannotComplete}}
 return .success
}
func value(_ node:AXUIElement,_ key:String)->CFTypeRef? {
 let i=index(node)
 switch key {
 case "AXRole":return ["AXApplication","AXComboBox","AXButton","AXScrollArea","AXList","AXGroup","AXTextField"][i] as CFString
 case "AXChildren":return [0:[nodes[1]],1:[nodes[2],nodes[3]],3:[nodes[4]],4:[nodes[5]],5:[nodes[6]]][i] as CFArray?
 case "AXParent":return nodes[[0,0,1,1,3,4,5][i]]
 case "AXIdentifier":return "known" as CFString
 case "AXValue":return (i==1 ? "Alpha":"Beta") as CFString
 case "AXEnabled":return true as CFBoolean
 default:return nil
 }
}
func attr(_ node:AXUIElement,_ key:String)->CFTypeRef? {fatalError("pinned metadata escaped to raw attr")}
func str(_ node:AXUIElement,_ key:String)->String? {fatalError("pinned metadata escaped to raw str")}
func AXUIElementCopyAttributeValue(_ node:AXUIElement,_ key:CFString,_ out:UnsafeMutablePointer<CFTypeRef?>)->AXError {
 copies+=1;change()
 if mode=="deadline" {Thread.sleep(forTimeInterval:0.08)}
 out.pointee=value(node,key as String);return out.pointee == nil ? .noValue:.success
}
func AXUIElementCopyMultipleAttributeValues(_ node:AXUIElement,_ names:CFArray,_ options:AXCopyMultipleAttributeOptions,_ out:UnsafeMutablePointer<CFArray?>)->AXError {
 batches+=1;change()
 let fields=(names as! [String]).map {key -> CFTypeRef in
  if let v=value(node,key) {return v}
  var error=AXError.noValue;return AXValueCreate(.axError,&error)!
 }
 out.pointee=fields as CFArray;return .success
}
enum WindowInputSPI {
 static func windowFunction() throws -> ((AXUIElement,UnsafeMutablePointer<CGWindowID>)->Int32) {
  {_,out in spis+=1;change();if mode=="spi_abort" {stopped=true};out.pointee=17;return 0}
 }
}
func CGWindowListCopyWindowInfo(_ options:CGWindowListOption,_ id:CGWindowID)->CFArray? {
 cgs+=1;if mode=="cg_abort" {stopped=true}
 return [[kCGWindowNumber as String:17,kCGWindowOwnerPID as String:42,kCGWindowLayer as String:0,kCGWindowIsOnscreen as String:true]] as CFArray
}
'''
        body=r'''
var result:[[String:Any]]=[]
for name in ["normal","abort_before","abort","identity","metadata","expired","deadline","set_failure","reset_failure","spi_abort","cg_abort","menu_abort"] {
 mode=name;copies=0;batches=0;spis=0;cgs=0;sets=0;resets=0;maxTimeout=0
 stopped=name=="abort_before";identity=true;metadata=true
 var emitted=0
 let scope=NativeWindowReadScope(deadline:ProcessInfo.processInfo.systemUptime+(name=="expired" ? -1:name=="deadline" ? 0.05:30),
  incrementalCheck:{if stopped || !identity {throw NativeWindowInputError.refused("identity/abort")}},
  check:{if stopped || !identity || !metadata {throw NativeWindowInputError.refused("context")}})
 do {
  let valid=try scope.withBindingContext {()->Bool in
   guard case .open(let list)=comboBoxContainerState(nodes[1],42,scope:scope),
     comboBoxPopupWindowID(list,scope:scope)==17 else {return false}
   if mode=="menu_abort" {stopped=true}
   guard let options=menuOptions(list,scope:scope),options.count==1,options[0]["label"] as? String=="Beta",
     let rows=comboBoxListRows(list,scope:scope),rows.count==1,
     nativePopupSelectionAnchorMatches(nodes[1],rows[0].node,nodes[3],42,scope:scope),
     rereadSelection(42,role:"AXComboBox",identity:"known",control:nodes[1],scope:scope)=="Alpha" else {return false}
   return true
  }
  if valid {emitted+=1}
 } catch {}
 result.append(["mode":name,"emitted":emitted,"copies":copies,"batches":batches,"spis":spis,"cgs":cgs,
  "operations":scope.operationCount,"sets":sets,"resets":resets,"max_timeout":maxTimeout])
}
print(String(data:try! JSONSerialization.data(withJSONObject:result),encoding:.utf8)!)
'''
        functions=extract(window,'final class NativeWindowReadScope {')+extract(window,'func nativeReadinessBatch(')+extract(window,'func nativeWindowID(')+extract(window,'private func windowInputNumber(')+extract(window,'func nativePopupVisibility(')
        for name in ['comboBoxAttribute','comboBoxString','comboBoxChildren','comboBoxOwnedList','comboBoxPopupWindowID','comboBoxListVisibility','comboBoxContainerState','comboBoxListAttributes','comboBoxListRows','menuOptions','nativePopupSelectionAnchorMatches','rereadSelection']:
            functions+=extract(helper,'func '+name+'(')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'main.swift';path.write_text(support+functions+body)
            result=subprocess.run(['swift',str(path)],capture_output=True,text=True,timeout=60)
        self.assertEqual(0,result.returncode,result.stderr)
        rows={row['mode']:row for row in json.loads(result.stdout)}
        self.assertEqual(1,rows['normal']['emitted'])
        self.assertGreater(rows['normal']['operations'],50)
        self.assertEqual(9,rows['normal']['batches'])
        self.assertGreaterEqual(rows['normal']['spis'],2)
        self.assertGreaterEqual(rows['normal']['cgs'],1)
        for name,row in rows.items():
            self.assertEqual(int(name=='normal'),row['emitted'],name)
            self.assertEqual(row['copies']+row['batches']+row['spis'],row['operations'],name)
            self.assertLessEqual(row['max_timeout'],0.5)
        for name in ['abort_before','expired','set_failure']:
            self.assertEqual(0,rows[name]['operations'],name)
        self.assertEqual(3,rows['abort']['operations'])
        self.assertEqual(5,rows['identity']['operations'])
        self.assertEqual(16,rows['metadata']['operations'])
        self.assertEqual(1,rows['deadline']['operations'])
        self.assertLessEqual(rows['deadline']['max_timeout'],0.05)
        self.assertEqual(1,rows['reset_failure']['operations'])
        self.assertEqual(1,rows['spi_abort']['spis'])
        self.assertEqual(1,rows['cg_abort']['cgs'])
        self.assertEqual(0,rows['menu_abort']['batches'])
        self.assertGreater(rows['menu_abort']['operations'],10)
