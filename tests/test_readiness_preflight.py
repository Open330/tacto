"""Read-only preflight is not a reusable input permit; subsequent checks are fresh."""
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock
from examples.probes.signed_finder_preparation import StageDriver
ROOT=Path(__file__).resolve().parents[1]

class PreflightStageTests(unittest.TestCase):
    def test_titlebar_diagnostic_retains_only_closed_roles_and_booleans(self):
        from examples.probes.signed_finder_read_cost import preflight_summary
        hit={'status':'read_complete','role':'AXToolbar','same_owner':True,'is_window':False,
             'same_window':True,'actions_readable':True,'has_actions':False,'clear_margin':False}
        reply={'type':'readiness_preflight','reason':'read_complete',
               'read_only':True,'authorizes_input':False,'elapsed_ms':0,
               'scoped_ax_operations':0,'ax_operation_elapsed_ms':0,'context_elapsed_ms':0,
               'titlebar_hits':[dict(hit) for _ in range(3)]}
        self.assertEqual(preflight_summary(reply)['titlebar_hits'],reply['titlebar_hits'])
        for change in ({'title':'PRIVATE'}, {'role':'PRIVATE'}, {'same_owner':1}):
            altered={**reply,'titlebar_hits':[{**hit,**change},dict(hit),dict(hit)]}
            self.assertIsNone(preflight_summary(altered))

    def test_journal_keeps_missing_authority_flags_unknown(self):
        driver=Mock()
        with tempfile.TemporaryFile(mode='w+') as journal:
            wrapped=StageDriver(driver,journal,preflight=True)
            wrapped.call('readiness_preflight',lambda:{'type':'error'})
            journal.seek(0);rows=[json.loads(line) for line in journal]
            self.assertIsNone(rows[-1]['read_only']);self.assertIsNone(rows[-1]['authorizes_input'])

    def test_failed_or_unauthorizing_reply_cannot_forward_prepare(self):
        for reply in ({'type':'error'}, {'type':'readiness_preflight'},
                      {'type':'readiness_preflight','read_only':True,'authorizes_input':True}):
            driver=Mock(capabilities={'diagnostic.readiness_preflight'})
            driver._send.return_value=reply
            wrapped=StageDriver(driver,io.StringIO(),preflight=True)
            wrapped.record=Mock()  # No real journal descriptor in this isolated unit.
            result=wrapped.act('obs','com.apple.finder',{'name':'prepare_window','element_index':1})
            self.assertEqual(result['effect'],'not_started');driver.act.assert_not_called()

    def test_success_still_invokes_original_action_without_a_permit(self):
        driver=Mock(capabilities={'diagnostic.readiness_preflight'})
        driver._send.return_value={'type':'readiness_preflight','read_only':True,'authorizes_input':False}
        driver.act.return_value={'type':'error','effect':'not_started','error':'changed_since_preflight'}
        wrapped=StageDriver(driver,io.StringIO(),preflight=True);wrapped.record=Mock()
        action={'name':'prepare_window','element_index':1}
        result=wrapped.act('obs','com.apple.finder',action,expect={'role':'Window'})
        driver.act.assert_called_once_with('obs','com.apple.finder',action,expect={'role':'Window'})
        self.assertEqual(result['error'],'changed_since_preflight')

@unittest.skipUnless(shutil.which('swift'),'macOS Swift required')
class NativePreflightTests(unittest.TestCase):
    def test_actual_preflight_then_new_sheet_is_refused_by_fresh_traversal(self):
        helper=(ROOT/'helper/TactoHelper.swift').read_text();window=(ROOT/'helper/WindowInput.swift').read_text()
        def fn(source,name):
            value=source[source.index(name):];return value[:value.index('\n}\n')+3]
        scope=fn(window,'final class NativeWindowReadScope {')
        batch=fn(window,'func nativeReadinessBatch(')
        traversal=window[window.index('let nativeWindowReadinessNodeLimit ='):window.index('func nativeTitlebarPoints(')]
        harness=r'''
import Foundation
import AppKit
import ApplicationServices
enum NativeWindowInputError:Error { case refused(String),unavailable(String) }
typealias AXRead=(AXUIElement,String)throws->(AXError,CFTypeRef?)
typealias AXBatchRead=(AXUIElement,[String])throws->(AXError,CFArray?)
final class Abort {var isStopped=false};let abortState=Abort()
let app=AXUIElementCreateApplication(42),window=AXUIElementCreateApplication(1),leaf=AXUIElementCreateApplication(2)
let latestObsID="owned",latestTargetName="com.apple.finder",latestTargetPID:pid_t=42
let latestProcessStart:Double?=1,latestElementGeneration=UUID(),latestElements=[window]
var latestDocumentToken:String?=nil,sheet=false,privateRole=false
func pid(forBundle name:String)->pid_t? {42}
func nativeProcessStartIdentity(_ pid:pid_t)->Double? {1}
func nativeObservedBundleMatches(_ pid:pid_t,_ target:String)->Bool {true}
func nativeTitlebarHitSummary(_ window:AXUIElement,_ pid:pid_t,scope:NativeWindowReadScope)throws->[[String:Any]] {[]}
func AXUIElementGetPid(_ node:AXUIElement,_ out:UnsafeMutablePointer<pid_t>)->AXError {out.pointee=42;return .success}
func AXUIElementSetMessagingTimeout(_ node:AXUIElement,_ seconds:Float)->AXError {.success}
func read(_ node:AXUIElement,_ name:String)->(AXError,CFTypeRef?) {
 if CFEqual(node,app) && name=="AXWindows" {return (.success,[window] as CFArray)}
 if name=="AXRole" {return (.success,(CFEqual(node,window) ? "AXWindow":privateRole ? "PRIVATE_ROLE":sheet ? "AXSheet":"AXGroup") as CFString)}
 if name=="AXModal" || name=="AXMinimized" {return (.success,kCFBooleanFalse)}
 if name=="AXChildren" {return (.success,(CFEqual(node,window) ? [leaf]:[]) as CFArray)}
 return (.attributeUnsupported,nil)
}
func AXUIElementCopyAttributeValue(_ node:AXUIElement,_ name:CFString,_ out:UnsafeMutablePointer<CFTypeRef?>)->AXError {
 let (error,value)=read(node,name as String);out.pointee=value;return error
}
func AXUIElementCopyMultipleAttributeValues(_ node:AXUIElement,_ names:CFArray,
 _ options:AXCopyMultipleAttributeOptions,_ out:UnsafeMutablePointer<CFArray?>)->AXError {
 if !CFEqual(node,window) {Thread.sleep(forTimeInterval:0.003)}
 let values:[CFTypeRef]=(names as! [String]).map {name in
  let (status,value)=read(node,name);if let value {return value}
  var error=status;return AXValueCreate(.axError,&error)!
 }
 out.pointee=values as CFArray;return .success
}
'''
        checks=r'''
let request:[String:Any]=["type":"readiness_preflight","target":"com.apple.finder","obs_id":"owned","element_index":0]
let reply=readinessPreflight(request)
var single=request;single["read_strategy"]="single"
let singleReply=readinessPreflight(single)
var invalidStrategy=request;invalidStrategy["read_strategy"]="PRIVATE_STRATEGY"
let invalidReply=readinessPreflight(invalidStrategy)
privateRole=true
let privateReply=readinessPreflight(request)
privateRole=false
sheet=true
var fresh="accepted"
do {try nativeRequireWindowReady(window,42,scope:NativeWindowReadScope(deadline:ProcessInfo.processInfo.systemUptime+5,check:{}),isFinder:true)}
catch NativeWindowInputError.refused(let why) {fresh=why}
var malformed=request;malformed["element_index"]=true
print(String(data:try! JSONSerialization.data(withJSONObject:["preflight":reply,"single":singleReply,"invalid_strategy":invalidReply,"private_role":privateReply,"fresh":fresh,
 "malformed":readinessPreflight(malformed),"sheet_preflight":readinessPreflight(request)]),encoding:.utf8)!)
'''
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'main.swift'
            path.write_text(harness+fn(helper,'func documentIsWholeNumber(')+scope+batch+traversal+fn(helper,'func readinessPreflight(')+checks)
            run=subprocess.run(['swift',str(path)],capture_output=True,text=True,timeout=60)
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertNotIn('PRIVATE_ROLE',run.stdout)
        self.assertNotIn('PRIVATE_STRATEGY',run.stdout)
        report=json.loads(run.stdout)
        self.assertEqual(report['preflight']['type'],'readiness_preflight')
        self.assertTrue(report['preflight']['read_only']);self.assertFalse(report['preflight']['authorizes_input'])
        self.assertEqual('batch',report['preflight']['read_strategy'])
        self.assertEqual('single',report['single']['read_strategy'])
        self.assertEqual('readiness_preflight',report['single']['type'])
        self.assertTrue(report['single']['read_only']);self.assertFalse(report['single']['authorizes_input'])
        self.assertEqual({'AXWindow':2,'AXGroup':3},
                         {r['role']:r['scoped_ax_operations'] for r in report['single']['readiness_role_costs']})
        self.assertEqual('invalid_or_stale_preflight',report['invalid_strategy']['reason'])
        attributes={r['attribute']:r['scoped_ax_operations'] for r in report['single']['readiness_attribute_costs']}
        self.assertEqual(5,attributes['AXRole'])
        self.assertEqual(3,attributes['AXModal'])
        self.assertEqual(2,attributes['AXChildren'])
        self.assertEqual(report['single']['scoped_ax_operations'],sum(attributes.values()))
        for key,expected in [('preflight',{'AXWindow':1,'AXGroup':1}),
                             ('private_role',{'AXWindow':1,'other':1}),
                             ('sheet_preflight',{'AXWindow':1,'AXSheet':1})]:
            rows=report[key]['readiness_role_costs']
            self.assertEqual(expected,{row['role']:row['nodes'] for row in rows})
            self.assertEqual(2,sum(row['scoped_ax_operations'] for row in rows))
            self.assertEqual(report[key]['readiness_progress']['total_nodes'],sum(row['nodes'] for row in rows))
            for row in rows:
                for metric in ('ax_operation_us','context_us','wall_us'):
                    self.assertIs(type(row[metric]),int);self.assertGreaterEqual(row[metric],0)
                if row['role']!='AXWindow':self.assertGreaterEqual(row['ax_operation_us'],2000)
        self.assertEqual(report['fresh'],'attached sheet or malformed role')
        self.assertEqual(report['malformed']['type'],'error')
        self.assertEqual(report['sheet_preflight']['refusal_cause'],'sheet_or_malformed_role')
