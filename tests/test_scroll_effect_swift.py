"""Execute the actual helper scroll branch with a setter that changes then errs."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform=='darwin' and shutil.which('swift'),'Swift macOS required')
class ScrollEffectSwiftTests(unittest.TestCase):
    def test_timeout_after_change_is_unknown_and_preflight_refusal_sends_nothing(self):
        source=(ROOT/'helper/TactoHelper.swift').read_text()
        start=source.index('    case "scroll":')
        branch=source[start:source.index('    case "secondary_action":',start)]
        support=r'''
import Foundation
import ApplicationServices
let node=AXUIElementCreateApplication(42)
var mode="timeout",writes=0,position=0.5
var requested:[String:Any]=["direction":"down","pages":1]
var rawPosition:CFTypeRef=NSNumber(value:0.5)
struct Abort {var isStopped=false}
let abortState=Abort()
func scrollBar(near el:AXUIElement,vertical:Bool,expectedPID:pid_t)->AXUIElement? {node}
func attr(_ el:AXUIElement,_ key:String)->CFTypeRef? {rawPosition}
func AXUIElementIsAttributeSettable(_ el:AXUIElement,_ key:CFString,_ value:UnsafeMutablePointer<DarwinBoolean>)->AXError {
 value.pointee=DarwinBoolean(mode != "not_settable");return .success
}
func AXUIElementSetAttributeValue(_ el:AXUIElement,_ key:CFString,_ value:CFTypeRef)->AXError {
 writes+=1;position=value as! Double
 return mode=="success" ? .success:.cannotComplete
}
func perform()->[String:Any] {
 let p:pid_t=42
 let action=requested
 func element()->AXUIElement? {node}
 switch "scroll" {
'''
        tail=r'''
 default:return ["type":"error"]
 }
 return ["type":"acted"]
}
var rows:[[String:Any]]=[]
for value in ["timeout","not_settable","success"] {
 mode=value;writes=0;position=0.5
 rows.append(["mode":value,"receipt":perform(),"writes":writes,"position":position])
}
for value:Any in [true,false,0,11,1.0,1.5,"1",NSNull()] {
 mode="success";writes=0;position=0.5;requested=["direction":"down","pages":value]
 let result=perform();precondition(writes==0 && result["effect"] as? String=="not_started")
}
for value:Any in ["wrong",true,NSNull()] {
 writes=0;requested=["direction":value,"pages":1]
 let result=perform();precondition(writes==0 && result["effect"] as? String=="not_started")
}
for value in [NSNumber(value:true),NSNumber(value:Double.nan),NSNumber(value:Double.infinity),NSNumber(value:-0.1),NSNumber(value:1.1)] {
 writes=0;requested=["direction":"down","pages":1];rawPosition=value
 let result=perform();precondition(writes==0 && result["effect"] as? String=="not_started")
}
print(String(data:try! JSONSerialization.data(withJSONObject:rows),encoding:.utf8)!)
'''
        with tempfile.TemporaryDirectory(prefix='nlc-scroll-effect-') as directory:
            path=Path(directory)/'main.swift';path.write_text(support+branch+tail)
            result=subprocess.run(['swift',str(path)],capture_output=True,text=True,timeout=30)
        self.assertEqual(0,result.returncode,result.stderr)
        timeout,refused,success=json.loads(result.stdout)
        self.assertEqual('unknown',timeout['receipt']['effect'])
        self.assertEqual(1,timeout['writes']);self.assertEqual(.6,timeout['position'])
        self.assertEqual('not_started',refused['receipt']['effect'])
        self.assertEqual(0,refused['writes']);self.assertEqual(.5,refused['position'])
        self.assertEqual('acted',success['receipt']['type'])
        self.assertEqual(1,success['writes']);self.assertEqual(.6,success['position'])
