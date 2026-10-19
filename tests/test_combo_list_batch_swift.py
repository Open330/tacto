"""Production list reader plus batch decoder; AX metadata stubs, no GUI input."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]

@unittest.skipUnless(shutil.which('swift'),'Swift required')
class ComboListBatchSwiftTests(unittest.TestCase):
    def test_complete_131_rows_fresh_reads_strict_batch_errors_and_scalar_fallback(self):
        source=(ROOT/'helper/TactoHelper.swift').read_text()
        window=(ROOT/'helper/WindowInput.swift').read_text()
        def extract(text,name):
            part=text[text.index('func '+name+'('):]
            return part[:part.index('\n}\n')+3]
        support=r'''
import Foundation
import ApplicationServices
let maxMenuOptions=200
let nodes=(10000..<10263).map {AXUIElementCreateApplication(pid_t($0))}
var mode="valid",batches=0,scalars=0,generation=0
func index(_ node:AXUIElement)->Int {nodes.firstIndex {CFEqual($0,node)}!}
enum NativeWindowInputError:Error {case refused(String),unavailable(String)}
typealias AXBatchRead=(AXUIElement,[String]) throws -> (AXError,CFArray?)
class NativeWindowReadScope {
 func withTimeout<T>(_ node:AXUIElement,_ body:()->T) throws -> T {body()}
 func recordReadinessAXFailure(_ error:AXError) {}
 func read(_ node:AXUIElement,_ name:String) throws -> (AXError,CFTypeRef?) {let value=attr(node,name);return(value == nil ? .noValue:.success,value)}
}
func AXUIElementGetPid(_ node:AXUIElement,_ out:UnsafeMutablePointer<pid_t>)->AXError {
 let i=index(node)
 out.pointee=mode=="cross_owner" && i==262 ? 99:42
 return mode=="pid_error" && i==262 ? .cannotComplete:.success
}
func value(_ node:AXUIElement,_ name:String)->CFTypeRef? {
 let i=index(node),row=i>0 && i<=131
 if name=="AXRole" {
  if mode=="numeric_role" && i==262 {return NSNumber(value:1)}
  if mode=="wrong_role" && i==262 {return "AXButton" as CFString}
  return (i==0 ? "AXList":row ? "AXGroup":"AXTextField") as CFString
 }
 if name=="AXChildren" {
  if i==0 {
   if mode=="too_many" {return Array(repeating:nodes[1],count:201) as CFArray}
   if mode=="malformed_child" {return [NSNumber(value:1)] as CFArray}
   if mode=="empty" {return [] as CFArray}
   return Array(nodes[1...131]) as CFArray
  }
  return (mode=="multiple_cells" && i==131 ? [nodes[262],nodes[262]]:[nodes[i+131]]) as CFArray
 }
 if name=="AXParent" {return mode=="wrong_parent" && i==262 ? nodes[0]:nodes[row ? 0:i-131]}
 if name=="AXValue" {
  if mode=="missing_value" && i==262 {return nil}
  if mode=="numeric_value" && i==262 {return NSNumber(value:1)}
  if mode=="empty_value" && i==262 {return "" as CFString}
  return "Choice\(i-132)-\(generation)" as CFString
 }
 if name=="AXEnabled" {
  if mode=="numeric_enabled" && i==262 {return NSNumber(value:1)}
  return (mode != "disabled" || i != 262) as CFBoolean
 }
 return nil
}
func attr(_ node:AXUIElement,_ name:String)->CFTypeRef? {scalars+=1;return value(node,name)}
func AXUIElementCopyMultipleAttributeValues(_ node:AXUIElement,_ names:CFArray,_ options:AXCopyMultipleAttributeOptions,_ out:UnsafeMutablePointer<CFArray?>)->AXError {
 batches+=1
 if mode=="unsupported" {return .attributeUnsupported}
 if mode=="not_implemented" {return .notImplemented}
 if mode=="timeout" {return .cannotComplete}
 let keys=names as! [String]
 var fields:[CFTypeRef]=keys.map {
  if mode=="field_error" && index(node)==262 && $0=="AXValue" {
   var error=AXError.cannotComplete;return AXValueCreate(.axError,&error)!
  }
  if let got=value(node,$0) {return got}
  var error=AXError.noValue;return AXValueCreate(.axError,&error)!
 }
 if mode=="malformed_batch" {fields.removeLast()}
 out.pointee=fields as CFArray
 return .success
}
'''
        body=r'''
var result:[[String:Any]]=[]
for name in ["valid","unsupported","not_implemented","timeout","field_error","malformed_batch","missing_value","numeric_value","empty_value","numeric_enabled","numeric_role","wrong_role","cross_owner","pid_error","wrong_parent","multiple_cells","too_many","malformed_child","empty","disabled"] {
 mode=name;batches=0;scalars=0;generation=0
 let first=comboBoxListRows(nodes[0])
 let firstBatches=batches,firstScalars=scalars
 generation=1
 let second=comboBoxListRows(nodes[0])
 let fresh=first != nil && second != nil && first!.last!.label != second!.last!.label
 result.append(["mode":name,"accepted":first != nil,"count":first?.count ?? 0,
  "batches":firstBatches,"scalars":firstScalars,"fresh":fresh,"disabled":first?.last?.disabled ?? false])
}
print(String(data:try! JSONSerialization.data(withJSONObject:result),encoding:.utf8)!)
'''
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'main.swift'
            path.write_text(support+extract(window,'nativeReadinessBatch')+extract(source,'comboBoxListAttributes')+extract(source,'comboBoxListRows')+body)
            result=subprocess.run(['swift',str(path)],capture_output=True,text=True,timeout=60)
        self.assertEqual(0,result.returncode,result.stderr)
        rows={row['mode']:row for row in json.loads(result.stdout)}
        for name in ('valid','unsupported','not_implemented','disabled'):
            self.assertTrue(rows[name]['accepted'],name)
            self.assertEqual(131,rows[name]['count'])
            self.assertTrue(rows[name]['fresh'])
            self.assertEqual(263,rows[name]['batches'])
            self.assertEqual(919 if name in ('unsupported','not_implemented') else 0,rows[name]['scalars'])
        self.assertTrue(rows['disabled']['disabled'])
        for name,row in rows.items():
            if name not in ('valid','unsupported','not_implemented','disabled'):
                self.assertFalse(row['accepted'],name)
                self.assertEqual(0,row['scalars'],name)
        self.assertEqual(1,rows['timeout']['batches'])
        self.assertEqual(1,rows['malformed_batch']['batches'])
