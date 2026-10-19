"""Real scope/list/batch code: interrupted metadata never authorizes input."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]

@unittest.skipUnless(shutil.which('swift'),'Swift required')
class ComboListScopeSwiftTests(unittest.TestCase):
    def test_deadline_abort_identity_metadata_and_timeout_failures_stop_complete_list_reads(self):
        source=(ROOT/'helper/TactoHelper.swift').read_text()
        window=(ROOT/'helper/WindowInput.swift').read_text()
        def extract(text,prefix):
            part=text[text.index(prefix):]
            return part[:part.index('\n}\n')+3]
        support=r'''
import Foundation
import ApplicationServices
let maxMenuOptions=200
let nodes=(10000..<10263).map {AXUIElementCreateApplication(pid_t($0))}
var mode="normal",batches=0,scalars=0,sets=0,resets=0,stopped=false,identity=true,metadata=true
var longestTimeout:Float=0
func index(_ node:AXUIElement)->Int {nodes.firstIndex {CFEqual($0,node)}!}
enum NativeWindowInputError:Error {case refused(String),unavailable(String)}
typealias AXBatchRead=(AXUIElement,[String]) throws -> (AXError,CFArray?)
func AXUIElementGetPid(_ node:AXUIElement,_ out:UnsafeMutablePointer<pid_t>)->AXError {out.pointee=42;return .success}
func AXUIElementSetMessagingTimeout(_ node:AXUIElement,_ timeout:Float)->AXError {
 if timeout>0 {sets+=1;longestTimeout=max(longestTimeout,timeout);if mode=="set_failure" {return .cannotComplete}}
 else {resets+=1;if mode=="reset_failure" {return .cannotComplete}}
 return .success
}
func value(_ node:AXUIElement,_ name:String)->CFTypeRef {
 let i=index(node),row=i>0 && i<=131
 switch name {
 case "AXRole":return (i==0 ? "AXList":row ? "AXGroup":"AXTextField") as CFString
 case "AXChildren":return (i==0 ? Array(nodes[1...131]):[nodes[i+131]]) as CFArray
 case "AXParent":return nodes[row ? 0:i-131]
 case "AXValue":return "Choice\(i-132)" as CFString
 default:return true as CFBoolean
 }
}
func change(_ calls:Int) {
 if calls==3 && mode=="abort" {stopped=true}
 if calls==3 && mode=="identity" {identity=false}
 if calls==3 && (mode=="metadata" || mode=="alias_metadata") {metadata=false}
}
func AXUIElementCopyAttributeValue(_ node:AXUIElement,_ key:CFString,_ out:UnsafeMutablePointer<CFTypeRef?>)->AXError {
 scalars+=1
 if scalars==3 && mode=="unsupported_abort" {stopped=true}
 out.pointee=value(node,key as String);return .success
}
func attr(_ node:AXUIElement,_ key:String)->CFTypeRef? {fatalError("scoped fallback escaped to raw attr")}
func AXUIElementCopyMultipleAttributeValues(_ node:AXUIElement,_ names:CFArray,_ options:AXCopyMultipleAttributeOptions,_ out:UnsafeMutablePointer<CFArray?>)->AXError {
 batches+=1;change(batches)
 if mode=="deadline" {Thread.sleep(forTimeInterval:0.08)}
 if mode.hasPrefix("unsupported") {return .attributeUnsupported}
 out.pointee=(names as! [String]).map {value(node,$0)} as CFArray;return .success
}
'''
        body=r'''
var result:[[String:Any]]=[]
for name in ["normal","unsupported","alias","abort_before","abort","identity","metadata","alias_metadata","expired","deadline","set_failure","reset_failure","unsupported_abort"] {
 mode=name;batches=0;scalars=0;sets=0;resets=0;longestTimeout=0
 stopped=name=="abort_before";identity=true;metadata=true
 var full=0,fast=0,emitted=0,count=0
 func checkIdentity() throws {if stopped || !identity {throw NativeWindowInputError.refused("identity/abort")}}
 let scope=NativeWindowReadScope(deadline:ProcessInfo.processInfo.systemUptime+(name=="expired" ? -1:name=="deadline" ? 0.05:30),
  incrementalCheck:name.hasPrefix("alias") ? nil:{fast+=1;try checkIdentity()},
  check:{full+=1;try checkIdentity();if !metadata {throw NativeWindowInputError.refused("metadata")}})
 do {
  let rows=try scope.withBindingContext {comboBoxListRows(nodes[0],scope:scope)}
  if let rows {count=rows.count;emitted+=1}
 } catch {}
 let fullBefore=full,fastBefore=fast
 stopped=false;identity=true;metadata=true
 if name != "expired" && name != "deadline" {try! scope.ready()}
 result.append(["mode":name,"batches":batches,"scalars":scalars,"sets":sets,"resets":resets,
  "operations":scope.operationCount,"emitted":emitted,"count":count,"max_timeout":longestTimeout,
  "full":fullBefore,"fast":fastBefore,"outside_fast":fast-fastBefore,"outside_full":full-fullBefore])
}
print(String(data:try! JSONSerialization.data(withJSONObject:result),encoding:.utf8)!)
'''
        functions=(extract(window,'final class NativeWindowReadScope {')+extract(window,'func nativeReadinessBatch(')
                   +extract(source,'func comboBoxListAttributes(')+extract(source,'func comboBoxListRows('))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'main.swift';path.write_text(support+functions+body)
            result=subprocess.run(['swift',str(path)],capture_output=True,text=True,timeout=60)
        self.assertEqual(0,result.returncode,result.stderr)
        rows={row['mode']:row for row in json.loads(result.stdout)}
        for name in ('normal','unsupported','alias'):
            self.assertEqual(131,rows[name]['count'])
            self.assertEqual(263,rows[name]['batches'])
            self.assertEqual(919 if name=='unsupported' else 0,rows[name]['scalars'])
        for name,row in rows.items():
            with self.subTest(case=name):
                self.assertEqual(int(name in ('normal','unsupported','alias')),row['emitted'])
                self.assertLessEqual(row['max_timeout'],0.5)
                self.assertEqual(0,row['outside_fast'])
                self.assertEqual(int(name not in ('expired','deadline')),row['outside_full'])
                self.assertEqual(row['operations'],row['batches']+row['scalars'])
        for name in ('abort_before','expired','set_failure'):
            self.assertEqual(0,rows[name]['batches'])
        for name in ('abort','identity','alias_metadata'):
            self.assertEqual(3,rows[name]['batches'])
        self.assertEqual(16,rows['metadata']['batches'])
        self.assertEqual(1,rows['deadline']['batches'])
        self.assertLessEqual(rows['deadline']['max_timeout'],0.05)
        self.assertEqual(1,rows['reset_failure']['batches'])
        self.assertEqual(2,rows['unsupported_abort']['batches'])
        self.assertEqual(3,rows['unsupported_abort']['scalars'])
        self.assertGreater(rows['alias']['full'],500)
        self.assertEqual(0,rows['alias']['fast'])
        self.assertLess(rows['normal']['full'],25)
        self.assertEqual(rows['normal']['operations'],rows['normal']['resets'])
