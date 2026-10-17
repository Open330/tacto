"""Signed range export boundaries; no UI or file decoding/import."""
from pathlib import Path
import hashlib
import base64
import json
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from nlc.helper import HelperDriver, InheritedHelperDriver
from nlc.trace import Trace, TracingDriver, TraceDriver

ROOT=Path(__file__).resolve().parents[1]

class ExportChannelTests(unittest.TestCase):
    def test_export_bytes_hash_flags_and_native_identity_are_validated(self):
        raw=b'{\\rtf1 own}'
        reply={'type':'document_rtf_export','document_token':'token','document_path':None,
            'utf16_length':3,'rtf_base64':base64.b64encode(raw).decode(),
            'rtf_sha256':hashlib.sha256(raw).hexdigest(),'text_sha256':hashlib.sha256(b'own').hexdigest(),
            'evidence_kind':'ax_range_rtf_export','source_format_verified':False,
            'attachment_completeness_verified':False,'authorizes_save':False}
        driver=SimpleNamespace(capabilities={'evidence.document_rtf_export'},_send=lambda _:reply)
        self.assertIs(HelperDriver.document_rtf_export(driver,'com.apple.TextEdit','token'),reply)
        for patch in ({'document_token':'foreign'},{'utf16_length':True},{'utf16_length':1000001},
                      {'rtf_base64':'bad'},{'rtf_sha256':'0'*64},{'document_path':'relative'},
                      {'source_format_verified':True},{'attachment_completeness_verified':None},
                      {'authorizes_save':True},{'text_sha256':None}):
            driver._send=lambda _,patch=patch:{**reply,**patch}
            self.assertEqual(HelperDriver.document_rtf_export(driver,'com.apple.TextEdit','token')['effect'],'not_started')

    def test_invalid_requests_are_not_sent_and_valid_request_is_pinned(self):
        calls=[]
        driver=SimpleNamespace(capabilities={'evidence.document_rtf_export'},
            _send=lambda request:calls.append(request) or {'type':'error'})
        for args in ({'document_token':''},{'document_token':True},{'document_token':'x'*129},
                     {'max_utf16_units':True},{'max_utf16_units':1.0},{'max_utf16_units':1000001},
                     {'timeout_seconds':True},{'timeout_seconds':0},{'timeout_seconds':float('nan')},
                     {'timeout_seconds':7},{'target':'owned'}):
            kw={'target':'com.apple.TextEdit','document_token':'token',**args}
            self.assertEqual(HelperDriver.document_rtf_export(driver,**kw)['effect'],'not_started')
        self.assertEqual(calls,[])
        for cls in (HelperDriver,InheritedHelperDriver):
            cls.document_rtf_export(driver,'com.apple.TextEdit','token',max_utf16_units=0,timeout_seconds=.2)
            self.assertEqual(calls[-1],{'type':'document_rtf_export','target':'com.apple.TextEdit',
                'document_token':'token','max_utf16_units':0,'timeout_seconds':.2})
        driver.capabilities.clear()
        before=len(calls)
        self.assertEqual(HelperDriver.document_rtf_export(driver,'com.apple.TextEdit','token')['effect'],'not_started')
        self.assertEqual(len(calls),before)

    def test_export_is_live_only_and_trace_cannot_claim_it(self):
        from unittest.mock import Mock
        live=Mock();live.capabilities={'evidence.document_rtf_export'}
        trace=Trace();wrapped=TracingDriver(live,trace)
        wrapped.document_rtf_export('com.apple.TextEdit','token',max_utf16_units=10,timeout_seconds=.5)
        live.document_rtf_export.assert_called_once_with('com.apple.TextEdit','token',max_utf16_units=10,timeout_seconds=.5)
        self.assertEqual(trace.events,[])
        replay=TraceDriver(Trace(capabilities=list(live.capabilities)))
        self.assertEqual(replay.capabilities,frozenset())
        self.assertEqual(replay.document_rtf_export('com.apple.TextEdit','token')['effect'],'not_started')

@unittest.skipUnless(sys.platform=='darwin' and shutil.which('swift'),'macOS Swift required')
class ExportSwiftTests(unittest.TestCase):
    def test_launcher_requires_token_and_exact_read_only_fields(self):
        source=(ROOT/'launcher/tacto.swift').read_text()
        schema=source[source.index('let allowedTypes:'):source.index('// MARK: - Socket plumbing')]
        checks=r'''
let root:[String:Any]=["type":"document_rtf_export","target":"com.apple.TextEdit","document_token":"token"]
var requests=[root]
for patch:[String:Any] in [["target":"foreign"],["document_token":true],["document_token":""],
 ["max_utf16_units":true],["max_utf16_units":1.0],["max_utf16_units":1000001],
 ["timeout_seconds":true],["timeout_seconds":0],["timeout_seconds":Double.nan],["timeout_seconds":7],
 ["document_path":"/private/tmp/owned.rtf"],["action":["name":"read","element_index":0]]] {
 requests.append(root.merging(patch){_,new in new})
}
var absent=root;absent.removeValue(forKey:"document_token");requests.append(absent)
print(String(data:try! JSONSerialization.data(withJSONObject:requests.map{vetted($0)==nil}),encoding:.utf8)!)
'''
        with tempfile.TemporaryDirectory(prefix='nlc-rtf-vet-') as directory:
            script=Path(directory)/'main.swift'
            script.write_text('import Foundation\nimport CoreFoundation\nfunc modelTimeout(_ raw:Any?)->Double? {1}\n'+schema+checks)
            result=subprocess.run(['swift',str(script)],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),[True]+[False]*13)

    def test_actual_handler_export_stability_identity_budgets_and_timeout(self):
        source=(ROOT/'helper/TactoHelper.swift').read_text()
        handler=source[source.index('func documentRTFExport('):source.index('func documentWindowForToken(')]
        harness=r'''
import Foundation
import CryptoKit
import ApplicationServices
struct StopState {var isStopped=false}
var abortState=StopState()
enum DocumentError:Error {case fail(String)}
struct PinnedDocument {let pid:pid_t;let stamp:Double;let window:AXUIElement;let body:AXUIElement}
let window=AXUIElementCreateApplication(10001),body=AXUIElementCreateApplication(10002),other=AXUIElementCreateApplication(10003)
var documentTokens:[(token:String,document:PinnedDocument)]=[]
var documentOperationDeadline:Double?=nil
var mode="good",path="/private/tmp/own.rtf",text="한😀",textReads=0,rtfReads=0
func sha256Hex(_ data:Data)->String {SHA256.hash(data:data).map{String(format:"%02x",$0)}.joined()}
func documentIsWholeNumber(_ raw:Any?)->NSNumber? {
 guard let n=raw as? NSNumber,CFGetTypeID(n) != CFBooleanGetTypeID(),!CFNumberIsFloatType(n as CFNumber) else{return nil};return n
}
func documentCheckAbort()throws {
 if abortState.isStopped || ProcessInfo.processInfo.systemUptime >= (documentOperationDeadline ?? .infinity){throw DocumentError.fail("stop")}
}
func documentPath(_ window:AXUIElement)throws->String? {path}
func documentWindowForToken(_ target:String,_ token:String)throws->AXUIElement {
 guard documentTokens.contains(where:{$0.token==token}) else{throw DocumentError.fail("expired")};return window
}
func documentCompleteText(_ body:AXUIElement,budget:Int)throws->String {
 textReads += 1;if text.utf16.count>budget {throw DocumentError.fail("budget")}
 return mode=="text" && textReads>1 ? "changed" : text
}
func AXUIElementSetMessagingTimeout(_ el:AXUIElement,_ timeout:Float)->AXError {
 precondition(CFEqual(el,body));return mode=="reset" && timeout==0 ? AXError(rawValue:-25204)! : .success
}
func AXUIElementCopyParameterizedAttributeValue(_ el:AXUIElement,_ name:CFString,_ param:CFTypeRef,
 _ result:UnsafeMutablePointer<AnyObject?>)->AXError {
 precondition(CFEqual(el,body) && name as String == "AXRTFForRange")
 var range=CFRange();precondition(AXValueGetValue(param as! AXValue,.cfRange,&range))
 precondition(range.location==0 && range.length==text.utf16.count);rtfReads += 1
 if mode=="path" {path="/private/tmp/other.rtf"}
 if mode=="body" {documentTokens=[("token",PinnedDocument(pid:42,stamp:10,window:window,body:other))]}
 if mode=="expired" {documentTokens=[]}
 if mode=="abort" {abortState.isStopped=true}
 if mode=="late" {Thread.sleep(forTimeInterval:0.03)}
 if mode=="wrongtype" {result.pointee="not data" as NSString;return .success}
 let raw = mode=="overflow" ? Data(repeating:120,count:2097153) :
   Data((mode=="header" ? "no rtf" : mode=="rtf" && rtfReads>1 ? "{\\rtf1 changed}" : "{\\rtf1 own}").utf8)
 result.pointee=raw as NSData;return .success
}
func reset(_ kind:String="good") {
 mode=kind;path="/private/tmp/own.rtf";text="한😀";textReads=0;rtfReads=0;abortState.isStopped=false
 documentOperationDeadline=nil;documentTokens=[("token",PinnedDocument(pid:42,stamp:10,window:window,body:body))]
}
'''
        checks=r'''
let request:[String:Any]=["type":"document_rtf_export","target":"com.apple.TextEdit","document_token":"token"]
reset();let good=documentRTFExport(request)
precondition(good["type"] as? String == "document_rtf_export")
precondition(good["text_sha256"] as? String == sha256Hex(Data(text.utf8)))
precondition(good["rtf_sha256"] as? String == sha256Hex(Data("{\\rtf1 own}".utf8)))
precondition(good["utf16_length"] as? Int == 3 && textReads==3 && rtfReads==2)
precondition(good["source_format_verified"] as? Bool == false && good["attachment_completeness_verified"] as? Bool == false)
precondition(good["authorizes_save"] as? Bool == false && documentOperationDeadline==nil)
for kind in ["text","rtf","path","body","expired","abort","wrongtype","overflow","header","late","reset"] {
 reset(kind);var r=request;if kind=="late" {r["timeout_seconds"]=0.01}
 let failed=documentRTFExport(r);precondition(failed["effect"] as? String == "not_started","\(kind) \(failed)")
 precondition(failed["rtf_base64"]==nil && documentOperationDeadline==nil)
}
reset();text="";var r=request;r["max_utf16_units"]=0;precondition(documentRTFExport(r)["type"] as? String == "document_rtf_export")
reset();r["max_utf16_units"]=0;precondition(documentRTFExport(r)["effect"] as? String == "not_started" && rtfReads==0)
for patch:[String:Any] in [["document_token":"unknown"],["document_token":true],["target":"foreign"],["max_utf16_units":1.0],["timeout_seconds":true],["timeout_seconds":0],["extra":0]] {
 reset();let failed=documentRTFExport(request.merging(patch){_,new in new});precondition(failed["effect"] as? String == "not_started" && textReads==0 && rtfReads==0)
}
print("EXPORT_PASS")
'''
        with tempfile.TemporaryDirectory(prefix='nlc-rtf-export-') as directory:
            script=Path(directory)/'main.swift';script.write_text(harness+handler+checks)
            result=subprocess.run(['swift',str(script)],capture_output=True,text=True,timeout=40)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip(),'EXPORT_PASS')
