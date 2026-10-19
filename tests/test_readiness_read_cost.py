"""Execute the actual signed diagnostic adapter with read-only injected AX."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

@unittest.skipUnless(shutil.which('swift'),'macOS Swift required')
class ReadinessReadCostTests(unittest.TestCase):
    def test_bound_requests_privacy_order_abort_and_partial_failure(self):
        helper=(ROOT/'helper/TactoHelper.swift').read_text()
        window=(ROOT/'helper/WindowInput.swift').read_text()
        def fn(source,name):
            value=source[source.index(name):]
            return value[:value.index('\n}\n')+3]
        adapter=fn(helper,'func readinessReadCost(')
        number=fn(helper,'func documentIsWholeNumber(')
        scope=fn(window,'final class NativeWindowReadScope {')
        batch=fn(window,'func nativeReadinessBatch(')
        support=r'''
import Foundation
import ApplicationServices
enum NativeWindowInputError:Error { case refused(String),unavailable(String) }
typealias AXBatchRead=(AXUIElement,[String])throws->(AXError,CFArray?)
final class Abort { var isStopped=false }
let abortState=Abort()
var latestObsID="owned",latestTargetName="com.apple.finder"
let latestTargetPID:pid_t=42,latestProcessStart:Double?=1
var latestDocumentToken:String?=nil,latestElementGeneration=UUID()
let latestElements=[AXUIElementCreateApplication(1)]
var identity=true,mode="ok",reads=0
func pid(forBundle target:String)->pid_t? { 42 }
func nativeProcessStartIdentity(_ pid:pid_t)->Double? { identity ? 1:2 }
func nativeObservedBundleMatches(_ pid:pid_t,_ bundle:String)->Bool { identity }
func AXUIElementSetMessagingTimeout(_ node:AXUIElement,_ seconds:Float)->AXError { .success }
func value(_ name:String)->(AXError,CFTypeRef?) {
 if name=="AXRole" { return (.success,"PRIVATE_NAME" as CFString) }
 if name=="AXChildren" { return (.success,[latestElements[0]] as CFArray) }
 return (.attributeUnsupported,nil)
}
func AXUIElementCopyAttributeValue(_ node:AXUIElement,_ name:CFString,
 _ out:UnsafeMutablePointer<CFTypeRef?>)->AXError {
 reads+=1;let (error,v)=value(name as String);out.pointee=v;return error
}
func AXUIElementCopyMultipleAttributeValues(_ node:AXUIElement,_ names:CFArray,
 _ options:AXCopyMultipleAttributeOptions,_ out:UnsafeMutablePointer<CFArray?>)->AXError {
 reads+=1
 if mode=="identity" { identity=false }
 if mode=="abort" { abortState.isStopped=true }
 if mode=="unsupported" { return .attributeUnsupported }
 if mode=="transport" { return .cannotComplete }
 let values:[CFTypeRef]=(names as! [String]).map { name in
  let (status,v)=value(name)
  if let v { return v }
  var error=status;return AXValueCreate(.axError,&error)!
 }
 out.pointee=values as CFArray;return .success
}
'''
        checks=r'''
let base:[String:Any]=["type":"readiness_read_cost","target":"com.apple.finder","obs_id":"owned","element_indices":[0]]
var result:[[String:Any]]=[]
for kind in ["ok","unsupported","transport","identity","abort","boolean","duplicate","empty","too_many","stale","document","wrong_type","unknown_field"] {
 reads=0;identity=true;abortState.isStopped=false;latestDocumentToken=nil;mode=kind
 var request=base
 switch kind {
 case "boolean":request["element_indices"]=[true]
 case "duplicate":request["element_indices"]=[0,0]
 case "empty":request["element_indices"]=[] as [Int]
 case "too_many":request["element_indices"]=Array(repeating:0,count:17)
 case "stale":request["obs_id"]="foreign"
 case "document":latestDocumentToken="pinned"
 case "wrong_type":request["type"]="act"
 case "unknown_field":request["filename"]="PRIVATE_PATH"
 default:break
 }
 let reply=readinessReadCost(request)
 result.append(["case":kind,"reads":reads,"reply":reply])
}
print(String(data:try! JSONSerialization.data(withJSONObject:result),encoding:.utf8)!)
'''
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'main.swift';source.write_text(support+number+scope+batch+adapter+checks)
            run=subprocess.run(['swift',str(source)],capture_output=True,text=True,timeout=60)
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertNotIn('PRIVATE',run.stdout)
        rows={row['case']:row for row in json.loads(run.stdout)}
        self.assertEqual(rows['ok']['reads'],8)
        self.assertEqual(rows['ok']['reply']['scoped_ax_operations'],8)
        self.assertEqual([r['batch_first'] for r in rows['ok']['reply']['rows']],[True,False])
        self.assertEqual(rows['ok']['reply']['rows'][0]['single_attributes'],
                         rows['ok']['reply']['rows'][0]['batch_attributes'])
        self.assertEqual(rows['unsupported']['reply']['rows'][0]['batch_transport'],'unsupported')
        self.assertIsNone(rows['unsupported']['reply']['rows'][0]['batch_attributes'])
        for name in ('identity','abort','transport'):
            self.assertEqual(rows[name]['reads'],1)
            self.assertEqual(rows[name]['reply']['type'],'error')
            self.assertEqual(len(rows[name]['reply']['rows']),1)
            self.assertNotIn('single_ax_us',rows[name]['reply']['rows'][0])
        for name in ('boolean','duplicate','empty','too_many','stale','document','wrong_type','unknown_field'):
            self.assertEqual(rows[name]['reads'],0)
            self.assertEqual(rows[name]['reply']['type'],'error')
