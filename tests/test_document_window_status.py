"""Closed is successful same-process absence, never a general token/read error."""
from pathlib import Path
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from tacto.helper import HelperDriver, InheritedHelperDriver

ROOT=Path(__file__).resolve().parents[1]

class StatusChannelTests(unittest.TestCase):
    def driver(self,reply):
        driver=InheritedHelperDriver.__new__(InheritedHelperDriver)
        driver.capabilities={'evidence.document_window_status'}
        driver.sent=[]
        driver._send=lambda message: driver.sent.append(message) or reply
        return driver

    def test_success_requires_exact_token_same_process_and_state(self):
        good={'type':'document_window_status','document_token':'own','state':'closed','same_process_verified':True}
        driver=self.driver(good)
        self.assertEqual(good,driver.document_window_status('com.apple.TextEdit','own'))
        self.assertEqual([{'type':'document_window_status','target':'com.apple.TextEdit','document_token':'own'}],driver.sent)
        for patch in ({'state':'unknown'},{'document_token':'other'},{'same_process_verified':1},{'extra':0}):
            self.assertEqual('error',self.driver({**good,**patch}).document_window_status('com.apple.TextEdit','own')['type'])
        for reply in (None,[],{'type':'error','error':'PRIVATE'},{}):
            result=self.driver(reply).document_window_status('com.apple.TextEdit','own')
            self.assertEqual('document_window_status_unavailable',result['error'])
            self.assertNotIn('PRIVATE',str(result))

    def test_only_closed_diagnostic_codes_survive_error_transport(self):
        for reason in ('process_changed','window_snapshot_changed','request_invalid_or_pin_missing'):
            reply={'type':'error','error':'document_window_status_unavailable','effect':'not_started','reason_code':reason}
            self.assertEqual(reply,self.driver(reply).document_window_status('com.apple.TextEdit','own'))
        for reason in ('PRIVATE',True,[],None):
            reply={'type':'error','error':'document_window_status_unavailable','effect':'not_started','reason_code':reason}
            result=self.driver(reply).document_window_status('com.apple.TextEdit','own')
            self.assertNotIn('reason_code',result)
            self.assertNotIn('PRIVATE',str(result))

    def test_status_local_failure_strings_keep_their_closed_reason_mapping(self):
        source=(ROOT/'helper/TactoHelper.swift').read_text()
        handler=source[source.index('func documentWindowStatus('):source.index('func documentRTFExport(')]
        thrown=set(re.findall(r'DocumentError\.fail\("([^"\n]+)"\)',handler))
        mapping=handler[handler.index('let codes = '):]
        keys=set(re.findall(r'"([^"\n]+)"\s*:',mapping))
        self.assertFalse(thrown-keys,thrown-keys)
        self.assertIn('document read deadline elapsed',keys)
        self.assertIn('사용자가 중단해 문서 읽기를 멈췄습니다',keys)

    def test_old_capability_bad_target_or_token_never_sends(self):
        for target,token in [('foreign','own'),('com.apple.TextEdit',''),('com.apple.TextEdit',True),('com.apple.TextEdit','x'*129)]:
            driver=self.driver({})
            self.assertEqual('not_started',driver.document_window_status(target,token)['effect'])
            self.assertEqual([],driver.sent)
        driver=self.driver({});driver.capabilities=set()
        self.assertEqual('not_started',driver.document_window_status('com.apple.TextEdit','own')['effect'])
        self.assertEqual([],driver.sent)
        self.assertTrue(hasattr(HelperDriver,'document_window_status'))

class StatusTraceTests(unittest.TestCase):
    def test_live_wrapper_never_persists_absence_and_replay_refuses(self):
        from unittest.mock import Mock
        from types import SimpleNamespace
        from tacto.trace import Trace, TracingDriver, TraceDriver
        reply={'type':'document_window_status','document_token':'own','state':'closed','same_process_verified':True}
        inner=SimpleNamespace(capabilities={'evidence.document_window_status'},
                              document_window_status=Mock(return_value=reply))
        trace=Trace()
        driver=TracingDriver(inner,trace)
        self.assertEqual(reply,driver.document_window_status('com.apple.TextEdit','own'))
        self.assertEqual([],trace.events)
        inner.document_window_status.assert_called_once_with('com.apple.TextEdit','own')
        replay=TraceDriver(Trace(capabilities=['evidence.document_window_status']))
        self.assertNotIn('evidence.document_window_status',replay.capabilities)
        self.assertEqual('not_started',replay.document_window_status('com.apple.TextEdit','own')['effect'])

@unittest.skipUnless(sys.platform=='darwin' and shutil.which('swift'),'macOS Swift required')
class StatusSwiftTests(unittest.TestCase):
    def execute(self,code):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'main.swift';path.write_text(code)
            result=subprocess.run(['swift',str(path)],capture_output=True,text=True,timeout=40)
        self.assertEqual(0,result.returncode,result.stderr)
        return result.stdout.strip()

    def test_launcher_exact_readonly_status_fields(self):
        source=(ROOT/'launcher/tacto.swift').read_text()
        schema=source[source.index('let allowedTypes:'):source.index('// MARK: - Socket plumbing')]
        checks=r'''
let request:[String:Any]=["type":"document_window_status","target":"com.apple.TextEdit","document_token":"own"]
precondition(vetted(request)==nil)
for patch:[String:Any] in [["target":"foreign"],["document_token":true],["document_token":""],["document_token":String(repeating:"x",count:129)],["action":["name":"click","element_index":0]],["document_path":"/private/tmp/own.txt"],["max_utf16_units":0],["timeout_seconds":1]] {
 precondition(vetted(request.merging(patch){_,new in new}) != nil)
}
var missing=request;missing.removeValue(forKey:"document_token");precondition(vetted(missing) != nil)
print("VET_PASS")
'''
        self.assertEqual('VET_PASS',self.execute('import Foundation\nimport CoreFoundation\nfunc modelTimeout(_ raw:Any?)->Double? {1}\n'+schema+checks))

    def test_actual_status_handler_absence_drift_error_and_no_body_access(self):
        source=(ROOT/'helper/TactoHelper.swift').read_text()
        handler=source[source.index('func documentWindowStatus('):source.index('func documentRTFExport(')]
        harness=r'''
import Foundation
import ApplicationServices
struct PinnedDocument {let pid:pid_t;let stamp:Double;let window:AXUIElement;let selectedPath:String?;let lifecycleWindowID:CGWindowID?}
enum DocumentError:Error {case fail(String)}
let own=AXUIElementCreateApplication(10001),other=AXUIElementCreateApplication(10002)
var documentTokens=[(token:"own",document:PinnedDocument(pid:42,stamp:10,window:own,selectedPath:"/private/tmp/own.txt",lifecycleWindowID:10001))]
var mode="open", reads=0
var documentOperationDeadline:Double?=nil
func CGPreflightScreenCaptureAccess()->Bool {fatalError("metadata must not require capture authorization")}
func CGWindowListCopyWindowInfo(_ option:CGWindowListOption,_ relative:CGWindowID)->CFArray? {
 precondition(option == .optionAll)
 if mode=="cg-error" {return nil}
 if mode=="cg-malformed" {return [[kCGWindowNumber as String:true]] as CFArray}
 return ["offspace","no-capture-present"].contains(mode) ? [[kCGWindowOwnerPID as String:42,kCGWindowNumber as String:10001]] as CFArray:[] as CFArray
}
func documentIsWholeNumber(_ raw:Any?)->NSNumber? {
 guard let n=raw as? NSNumber,CFGetTypeID(n) != CFBooleanGetTypeID(),!CFNumberIsFloatType(n as CFNumber) else {return nil};return n
}
func documentCheckAbort()throws {
 if mode=="abort" || ProcessInfo.processInfo.systemUptime >= (documentOperationDeadline ?? .infinity){throw DocumentError.fail("PRIVATE")}
}
func documentProcessIdentity(_ target:String)throws->(pid_t,Double) {
 if mode=="gone" {throw DocumentError.fail("PRIVATE")};return (42,mode=="restart" ? 11:10)
}
func documentRequired(_ el:AXUIElement,_ name:String)throws->Any {
 try documentCheckAbort()
 if name=="AXWindows" {
  reads += 1
  if mode=="readerror" {throw DocumentError.fail("PRIVATE")}
  if mode=="overflow" {return Array(repeating:own,count:129)}
  if mode=="drift" {return reads==1 ? [own]:[AXUIElement]()}
  if mode=="late" {Thread.sleep(forTimeInterval:0.01)}
  return ["closed","offspace","no-capture","no-capture-present","cg-error","cg-malformed","no-id"].contains(mode) ? [AXUIElement]() : mode=="reopened" ? [other] : mode=="duplicate" ? [own,other] : [own]
 }
 precondition(name=="AXSubrole","body or unexpected attribute was read")
 return mode=="rolechanged" ? "AXDialog":"AXStandardWindow"
}
func documentPath(_ window:AXUIElement)throws->String? {
 if mode=="patherror" {throw DocumentError.fail("PRIVATE")}
 return mode=="pathchanged" ? "/private/tmp/else.txt":"/private/tmp/own.txt"
}
'''
        checks=r'''
let request:[String:Any]=["type":"document_window_status","target":"com.apple.TextEdit","document_token":"own"]
let opened=documentWindowStatus(request);precondition(opened["state"] as? String == "open")
mode="closed";reads=0;let closed=documentWindowStatus(request);precondition(closed["state"] as? String == "closed")
precondition(closed["same_process_verified"] as? Bool == true && documentTokens.count==1)
mode="no-capture";reads=0
precondition(documentWindowStatus(request)["state"] as? String == "closed")
for kind in ["gone","restart","readerror","overflow","drift","reopened","duplicate","patherror","pathchanged","abort","rolechanged","late","offspace","no-capture-present","cg-error","cg-malformed"] {
 mode=kind;reads=0;documentOperationDeadline=kind=="late" ? ProcessInfo.processInfo.systemUptime+0.005:nil
 let old=documentOperationDeadline
 let result=documentWindowStatus(request)
 precondition(result["type"] as? String == "error","\(kind) \(result)")
 precondition(result["state"]==nil && documentTokens.count==1 && documentOperationDeadline==old)
 if kind=="offspace" {precondition(result["reason_code"] as? String == "global_window_still_present")}
 if kind=="restart" {precondition(result["reason_code"] as? String == "process_changed")}
 if kind=="readerror" {precondition(result["reason_code"] as? String == "native_read_failed")}
}
mode="no-id";documentOperationDeadline=nil
let held=documentTokens
 documentTokens=[("own",PinnedDocument(pid:42,stamp:10,window:own,selectedPath:"/private/tmp/own.txt",lifecycleWindowID:nil))]
precondition(documentWindowStatus(request)["type"] as? String == "error")
documentTokens=held
mode="closed";documentOperationDeadline=nil
for patch:[String:Any] in [["document_token":"unknown"],["target":"foreign"],["document_token":true],["action":["name":"click"]]] {
 precondition(documentWindowStatus(request.merging(patch){_,new in new})["type"] as? String == "error")
}
print("STATUS_PASS")
'''
        self.assertEqual('STATUS_PASS',self.execute(harness+handler+checks))

if __name__=='__main__': unittest.main()
