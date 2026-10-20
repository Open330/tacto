"""Run actual popup capture guard with injected read-only AX/CG metadata."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('swift'),'Swift required')
class PopupBindingTests(unittest.TestCase):
    def test_physical_popup_and_logical_parent_must_both_remain_bound(self):
        source=(ROOT/'helper/WindowInput.swift').read_text()
        function=source[source.index('func captureNativePopupWindowBinding('):]
        function=function[:function.index('\n}\n')+3]
        support=r'''
import Foundation
import AppKit
import ApplicationServices
let element=AXUIElementCreateApplication(10),popup=AXUIElementCreateApplication(11),parent=AXUIElementCreateApplication(12),other=AXUIElementCreateApplication(13)
var mode="valid"
enum NativeWindowInputError:Error {case refused(String)}
struct NativeWindowBinding {
 let element:AXUIElement,window:AXUIElement
 let pid:pid_t,processStart:Double,windowID:CGWindowID
 let windowBounds:CGRect,elementBounds:CGRect,point:CGPoint
}
class NativeWindowReadScope {
 func ready() throws {if mode=="deadline" {throw NativeWindowInputError.refused("expired")}}
 func withTimeout<T>(_ node:AXUIElement,_ block:()->T) throws -> T {try ready();return block()}
}
func nativeProcessStartIdentity(_ pid:pid_t)->Double? {mode=="process_changed" ? 11:10}
func windowInputPID(_ node:AXUIElement) throws -> pid_t {mode=="cross_owner" && CFEqual(node,element) ? 99:42}
func windowInputRole(_ node:AXUIElement,scope:NativeWindowReadScope?) throws -> String {
 if CFEqual(node,popup) {return mode=="wrong_popup_role" ? "AXGroup":"AXScrollArea"}
 return CFEqual(node,parent) ? "AXWindow":"AXTextField"
}
func windowInputAncestor(_ node:AXUIElement,_ pid:pid_t,scope:NativeWindowReadScope?) throws -> AXUIElement {
 mode=="different_parent" && CFEqual(node,popup) ? other:parent
}
enum WindowInputSPI {
 static func windowFunction() throws -> (AXUIElement,UnsafeMutablePointer<CGWindowID>)->Int32 {
  return {node,out in
   if mode=="spi_failed" {return -1}
   out.pointee=CFEqual(node,parent) ? 17:18
   if mode=="no_popup_id" && CFEqual(node,popup) {out.pointee=0}
   if mode=="same_physical_window" {out.pointee=17}
   if mode=="element_other_window" && CFEqual(node,element) {out.pointee=19}
   return 0
  }
 }
}
func windowInputFrame(_ node:AXUIElement,scope:NativeWindowReadScope?) throws -> CGRect {
 if CFEqual(node,parent) {return CGRect(x:100,y:200,width:500,height:400)}
 if CFEqual(node,popup) {return CGRect(x:110,y:230,width:100,height:40)}
 return CGRect(x:mode=="outside_popup" ? 0:120,y:240,width:20,height:20)
}
func windowInputCGFrame(_ id:CGWindowID,_ pid:pid_t,scope:NativeWindowReadScope?) throws -> CGRect {
 if mode=="cg_unavailable" {throw NativeWindowInputError.refused("unknown CG owner/visibility")}
 if id==17 {return CGRect(x:mode=="parent_geometry_changed" ? 101:100,y:200,width:500,height:400)}
 return CGRect(x:mode=="popup_geometry_changed" ? 111:110,y:230,width:100,height:40)
}
func windowInputHit(_ point:CGPoint,_ pid:pid_t,scope:NativeWindowReadScope?) throws -> AXUIElement {
 mode=="wrong_hit" ? parent:mode=="hit_cycle" ? other:element
}
func windowInputAttribute(_ node:AXUIElement,_ key:String,scope:NativeWindowReadScope?) throws -> CFTypeRef {other}
func windowInputElement(_ value:CFTypeRef) throws -> AXUIElement {value as! AXUIElement}
'''
        body=r'''
var result:[String:Bool]=[:]
for value in ["valid","deadline","process_changed","cross_owner","wrong_popup_role","different_parent","spi_failed","no_popup_id","same_physical_window","element_other_window","outside_popup","cg_unavailable","parent_geometry_changed","popup_geometry_changed","wrong_hit","hit_cycle"] {
 mode=value
 do {
  let binding=try captureNativePopupWindowBinding(element,popup,42,expectedStart:10,scope:NativeWindowReadScope())
  precondition(binding.windowID==18 && CFEqual(binding.window,parent) && binding.point==CGPoint(x:130,y:250))
  result[value]=true
 } catch {result[value]=false}
}
print(String(data:try! JSONSerialization.data(withJSONObject:result),encoding:.utf8)!)
'''
        with tempfile.TemporaryDirectory(prefix='tacto-popup-capture-') as directory:
            path=Path(directory)/'main.swift';path.write_text(support+function+body)
            result=subprocess.run(['swift',str(path)],capture_output=True,text=True,timeout=30)
        self.assertEqual(0,result.returncode,result.stderr)
        values=json.loads(result.stdout);self.assertTrue(values.pop('valid'))
        self.assertTrue(all(value is False for value in values.values()),values)
