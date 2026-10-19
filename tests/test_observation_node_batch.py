"""Differential real Swift walker: packet data, boundaries and read cost."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform=='darwin' and shutil.which('xcrun'),'macOS Swift required')
class ObservationPacketTests(unittest.TestCase):
    def test_batch_and_scalar_walkers_render_same_tree_with_fewer_reads(self):
        current=(ROOT/'helper/TactoHelper.swift').read_text()
        def segment(source,begin,end):return source[source.index(begin):source.index(end,source.index(begin))]
        common=segment(current,'let interactableRoles:','// MARK: - Session state')
        # Keep the scalar reference available in shallow/source exports and
        # after history rewrites. It is not a historical performance claim.
        old=(ROOT/'tests/fixtures/ScalarObservationWalker.swift').read_text()
        support=r'''
import Foundation
import ApplicationServices
var singles=0,batches=0,actions=0
var mode="batch"
var data:[pid_t:[String:CFTypeRef]] = [:]
func id(_ element:AXUIElement)->pid_t { var pid:pid_t=0; _=AXUIElementGetPid(element,&pid);return pid }
func element(_ pid:pid_t)->AXUIElement { AXUIElementCreateApplication(pid) }
func AXUIElementCopyAttributeValue(_ node:AXUIElement,_ key:CFString,_ out:UnsafeMutablePointer<CFTypeRef?>)->AXError {
 singles += 1
 guard let value=data[id(node)]?[key as String] else { return .attributeUnsupported }
 out.pointee=value;return .success
}
func AXUIElementCopyMultipleAttributeValues(_ node:AXUIElement,_ names:CFArray,
 _ options:AXCopyMultipleAttributeOptions,_ out:UnsafeMutablePointer<CFArray?>)->AXError {
 batches += 1
 let keys=names as! [String]
 if keys.count>7 {
  if mode=="unsupported" { return .attributeUnsupported }
  if mode=="short" { out.pointee=["AXWindow"] as CFArray;return .success }
  if mode=="nil" { return .success }
 }
 let entries:[CFTypeRef]=keys.map { key in
  if let value=data[id(node)]?[key] { return value }
  var error=AXError.attributeUnsupported
  return AXValueCreate(.axError,&error)!
 }
 out.pointee=entries as CFArray;return .success
}
func AXUIElementCopyActionNames(_ node:AXUIElement,_ out:UnsafeMutablePointer<CFArray?>)->AXError {
 actions += 1;out.pointee=(id(node)==4 ? ["AXShowMenu"] : []) as CFArray;return .success
}
func documentURLSyntaxPath(_ text:String)->String? {
 // This oracle tests rendering, not the separately tested URL decoder.
 URL(string:text)?.path
}
'''
        checks=r'''
func render(_ previous:Bool)->([String],Int,Int,Int,[String:Int]) {
 singles=0;batches=0;actions=0
 let metrics=ObservationReadMetrics()
 var lines:[String]=[],elements:[AXUIElement]=[];var visited=0,truncated=false
 if previous { priorWalk(element(1),depth:0,out:&lines,elements:&elements,visited:&visited,truncated:&truncated) }
 else { walk(element(1),depth:0,out:&lines,elements:&elements,visited:&visited,truncated:&truncated,metrics:metrics) }
 return (lines,singles,batches,actions,metrics.metadata)
}
data=[
 1:["AXRole":"AXWindow" as CFString,"AXTitle":"A, \"B\"\n한글" as CFString,
    "AXDocument":"file:///owned/e%CC%81.txt" as CFString,"AXEnabled":kCFBooleanTrue,
    "AXChildren":[element(2),element(4),element(5)] as CFArray],
 2:["AXRole":"AXSheet" as CFString,"AXEnabled":kCFBooleanTrue,"AXChildren":[element(3)] as CFArray,
    "AXDocument":"file:///owned/sheet-must-not-render.txt" as CFString],
 3:["AXRole":"AXButton" as CFString,"AXTitle":"Cancel\nNow" as CFString,"AXEnabled":kCFBooleanFalse,
    "AXSelected":kCFBooleanTrue,"AXValue":NSNumber(value:42),
    "AXDocument":"file:///owned/button-must-not-render.txt" as CFString],
 4:["AXRole":"AXButton" as CFString,"AXSubrole":"AXCloseButton" as CFString,"AXEnabled":kCFBooleanTrue],
 5:["AXRole":"AXStaticText" as CFString,"AXValue":kCFBooleanTrue]
]
let baseline=render(true)
var results:[[String:Any]]=[]
for strategy in ["batch","unsupported","short","nil"] {
 mode=strategy
 let result=render(false)
 precondition(result.0==baseline.0,"rendered tree changed")
 precondition(result.3==baseline.3,"secondary action enumeration lost")
 results.append(["mode":strategy,"singles":result.1,"batches":result.2,"actions":result.3,"metrics":result.4])
}
// No cross-observation reuse: changed attributes must be fetched again.
mode="batch";data[3]!["AXTitle"]="Changed" as CFString
let changed=render(false)
precondition(changed.0.contains(where:{$0.contains("Changed")}))
precondition(changed.0 != baseline.0)
let output:[String:Any]=["rows":results,"prior_singles":baseline.1,"prior_batches":baseline.2,
                        "tree":baseline.0]
print(String(data:try! JSONSerialization.data(withJSONObject:output),encoding:.utf8)!)
'''
        with tempfile.TemporaryDirectory() as directory:
            src=Path(directory)/'main.swift';exe=Path(directory)/'check'
            src.write_text(support+common+old+checks)
            built=subprocess.run(['xcrun','swiftc','-warnings-as-errors',str(src),'-o',str(exe)],capture_output=True,text=True,timeout=60)
            self.assertEqual(built.returncode,0,built.stderr)
            result=subprocess.run([str(exe)],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
        report=json.loads(result.stdout)
        batch=report['rows'][0]
        self.assertEqual(batch['singles'],0)
        self.assertEqual(batch['batches'],5)
        self.assertLess(batch['batches'],report['prior_singles']+report['prior_batches'])
        for row in report['rows']:
            self.assertEqual(row['metrics'], {
                'packet_attempts':5,
                'packet_successes':5 if row['mode']=='batch' else 0,
                'packet_fallbacks':0 if row['mode']=='batch' else 5,
                'secondary_action_reads':row['actions']})
        tree='\n'.join(report['tree'])
        self.assertIn('Sheet',tree);self.assertIn('[disabled] [selected]',tree)
        self.assertIn('Subrole: AXCloseButton',tree)
        self.assertIn('Secondary Actions: ShowMenu',tree)
        self.assertIn('Document: "/owned/é.txt"',tree)
        self.assertEqual(tree.count('Document:'),1)
        self.assertNotIn('must-not-render',tree)
