"""Production first-lookup scope refuses changed observations before input."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]

@unittest.skipUnless(shutil.which('swift'),'Swift required')
class InitialSelectionScopeSwiftTests(unittest.TestCase):
    def test_first_read_tracks_observation_process_generation_abort_and_deadline(self):
        def extract(text,prefix):
            part=text[text.index(prefix):]
            return part[:part.index('\n}\n')+3]
        scope=extract((ROOT/'helper/WindowInput.swift').read_text(),'final class NativeWindowReadScope {')
        factory=extract((ROOT/'helper/TactoHelper.swift').read_text(),'func initialSelectionReadScope(')
        support=r'''
import Foundation
import CoreFoundation
typealias AXUIElement=Int
enum AXError:Int32 {case success=0,failure = -25200}
enum NativeWindowInputError:Error {case refused(String),unavailable(String)}
class Abort {var isStopped=false};let abortState=Abort()
var latestElements=[1,2],latestElementGeneration=1,latestProcessStart:Double?=1,latestDocumentToken:String?=nil
var latestObsID="o",latestTargetPID:pid_t=42,latestTargetName="fixture"
var resolved:pid_t?=42,liveStart:Double?=1,docError=false,mode="normal",copies=0,checks=0
func CFEqual(_ left:Int,_ right:Int)->Bool {left==right}
func nativeProcessStartIdentity(_ pid:pid_t)->Double? {liveStart}
func pid(forBundle target:String)->pid_t? {resolved}
func validateDocumentObservation(_ target:String) throws {checks+=1;if docError {throw NativeWindowInputError.refused("document")}}
func AXUIElementSetMessagingTimeout(_ node:Int,_ seconds:Float)->AXError {.success}
func AXUIElementCopyAttributeValue(_ node:Int,_ name:CFString,_ out:inout CFTypeRef?)->AXError {
 copies+=1;out="AXComboBox" as CFString
 switch mode {
 case "abort":abortState.isStopped=true
 case "obs":latestObsID="new"
 case "pid":latestTargetPID=99
 case "name":latestTargetName="other"
 case "generation":latestElementGeneration=2
 case "count":latestElements.append(3)
 case "start":liveStart=2
 case "resolver":resolved=99
 case "document_token":latestDocumentToken="new"
 case "document_live":docError=true
 case "deadline":Thread.sleep(forTimeInterval:0.08)
 default:break
 }
 return .success
}
'''
        body=r'''
var rows:[[String:Any]]=[]
for name in ["normal","alias_normal","abort_before","abort","obs","pid","name","generation","count","start","resolver","document_token","document_live","deadline","expired","missing_start","duplicate_control","document_scope"] {
 mode=name;copies=0;checks=0;abortState.isStopped=name=="abort_before"
 latestElements=name=="duplicate_control" ? [1,1]:[1,2];latestElementGeneration=1
 latestProcessStart=name=="missing_start" ? nil:1;liveStart=1;latestDocumentToken=name=="document_scope" ? "pinned":nil
 latestObsID="o";latestTargetPID=42;latestTargetName="fixture";resolved=42;docError=false
 var emitted=0,full=0,fast=0
 do {
  let scope=try initialSelectionReadScope("o",name=="alias_normal" ? "other-alias":"fixture",1,42,deadline:ProcessInfo.processInfo.systemUptime+(name=="expired" ? -1:name=="deadline" ? 0.05:30))
  _=try scope.read(1,"AXRole");emitted+=1
  full=scope.fullContextChecks;fast=scope.incrementalContextChecks
 } catch {}
 rows.append(["mode":name,"emitted":emitted,"copies":copies,"full":full,"fast":fast])
}
print(String(data:try! JSONSerialization.data(withJSONObject:rows),encoding:.utf8)!)
'''
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'main.swift';path.write_text(support+scope+factory+body)
            result=subprocess.run(['swift',str(path)],capture_output=True,text=True,timeout=60)
        self.assertEqual(0,result.returncode,result.stderr)
        rows={row['mode']:row for row in json.loads(result.stdout)}
        for name,row in rows.items():
            self.assertEqual(int(name in ('normal','alias_normal')),row['emitted'],name)
            self.assertEqual(0 if name in ('abort_before','expired','missing_start','duplicate_control','document_scope') else 1,row['copies'],name)
            self.assertEqual(0,row['fast'])
        self.assertEqual(3,rows['normal']['full'])
        self.assertEqual(3,rows['alias_normal']['full'])
