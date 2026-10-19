import base64
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tacto.helper import HelperDriver, InheritedHelperDriver
from tacto.trace import Trace, TraceDriver, TracingDriver

ROOT = Path(__file__).resolve().parents[1]


def reply():
    return {'type':'document_attachment_metadata','document_token':'token','document_path':None,
            'utf16_length':4,'text_sha256':hashlib.sha256('한😀\ufffc'.encode()).hexdigest(),
            'occurrences':[{'utf16_index':3,'ax_attachment_present':True,'ax_element_reference':True}],
            'evidence_kind':'ax_attributed_attachment_metadata','sampled_stable':True,
            'proves_no_intermediate_change':False,'source_format_verified':False,
            'attachment_completeness_verified':False,'attachment_payload_verified':False,'authorizes_save':False}


class AttachmentChannelTests(unittest.TestCase):
    def test_validated_envelope_and_inherited_channel(self):
        response = reply()
        driver = SimpleNamespace(capabilities={'evidence.document_attachment_metadata'}, _send=Mock(return_value=response))
        for cls in (HelperDriver, InheritedHelperDriver):
            self.assertEqual(cls.document_attachment_metadata(driver,'com.apple.TextEdit','token'),response)
        self.assertEqual(driver._send.call_count,2)
        invalid = [{'authorizes_save':True},{'attachment_payload_verified':True},{'sampled_stable':False},
                   {'utf16_length':True},{'document_token':'other'},{'text_sha256':'bad'},
                   {'document_path':'relative'},{'extra':'payload'},{'occurrences':reply()['occurrences']*257}]
        for row in [{'utf16_index':True,'ax_attachment_present':True,'ax_element_reference':True},
                    {'utf16_index':4,'ax_attachment_present':True,'ax_element_reference':True},
                    {'utf16_index':3,'ax_attachment_present':False,'ax_element_reference':True},
                    {'utf16_index':3,'ax_attachment_present':True,'ax_element_reference':1},
                    {'utf16_index':3,'ax_attachment_present':True,'ax_element_reference':True,'payload':'bad'}]:
            invalid.append({'occurrences':[row]})
        invalid.append({'occurrences':reply()['occurrences']*2})
        for change in invalid:
            driver._send.return_value={**reply(),**change}
            with self.subTest(change=change):
                self.assertEqual(HelperDriver.document_attachment_metadata(driver,'com.apple.TextEdit','token')['effect'],'not_started')
        for response in [None,{'type':'error','detail':'private text'},[]]:
            driver._send.return_value=response
            self.assertEqual(HelperDriver.document_attachment_metadata(driver,'com.apple.TextEdit','token'),
                             {'type':'error','error':'document_attachment_metadata_unavailable','effect':'not_started'})

    def test_bad_requests_never_send(self):
        driver=SimpleNamespace(capabilities={'evidence.document_attachment_metadata'},_send=Mock())
        for change in [{'target':'other'},{'document_token':''},{'document_token':True},
                       {'max_utf16_units':100001},{'max_utf16_units':True},{'max_utf16_units':1.0},
                       {'timeout_seconds':0},{'timeout_seconds':float('nan')},{'timeout_seconds':True}]:
            kwargs={'target':'com.apple.TextEdit','document_token':'token',**change}
            self.assertEqual(HelperDriver.document_attachment_metadata(driver,**kwargs)['effect'],'not_started')
        driver.capabilities.clear()
        HelperDriver.document_attachment_metadata(driver,'com.apple.TextEdit','token')
        driver._send.assert_not_called()

    def test_trace_cannot_replay_live_metadata(self):
        inner=Mock();inner.capabilities={'evidence.document_attachment_metadata'}
        trace=Trace();wrapped=TracingDriver(inner,trace)
        wrapped.document_attachment_metadata('com.apple.TextEdit','token')
        inner.document_attachment_metadata.assert_called_once_with('com.apple.TextEdit','token',max_utf16_units=100000,timeout_seconds=3)
        self.assertEqual([],trace.events)
        replay=TraceDriver(Trace(capabilities=list(inner.capabilities)))
        self.assertEqual(replay.capabilities,frozenset())
        self.assertEqual(replay.document_attachment_metadata('com.apple.TextEdit','token')['effect'],'not_started')

    def test_cli_requires_signed_route_and_checks_source_position_binding(self):
        from tacto.cli import _dispatch
        driver=Mock();path='/private/tmp/owned.rtfd'
        text={'type':'document_text','document_token':'token','document_path':path,'text':'한😀\ufffc'}
        driver.document_text.return_value=text
        driver.document_attachment_metadata.return_value={**reply(),'document_path':path}
        with patch('tacto.cli._make_driver',return_value=driver),contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(2,_dispatch('document-attachments',[path]))
            driver.document_text.assert_not_called()
            self.assertEqual(0,_dispatch('document-attachments',[path,'--helper']))
            data=json.loads(output.getvalue().splitlines()[-1])
            self.assertFalse(data['attachment_payload_verified'])
            driver.document_attachment_metadata.return_value={**reply(),'document_path':path,'occurrences':[]}
            self.assertEqual(1,_dispatch('document-attachments',[path,'--helper']))
            self.assertEqual('document_attachment_metadata_unavailable',json.loads(output.getvalue().splitlines()[-1])['error'])
            driver.document_text.return_value={**text,'document_path':'/private/tmp/other.rtfd'}
            calls=driver.document_attachment_metadata.call_count
            self.assertEqual(1,_dispatch('document-attachments',[path,'--helper']))
            self.assertEqual(calls,driver.document_attachment_metadata.call_count)


@unittest.skipUnless(sys.platform=='darwin' and shutil.which('swift'),'macOS Swift required')
class AttachmentSwiftTests(unittest.TestCase):
    def test_actual_launcher_schema_accepts_only_pinned_read_request(self):
        source=(ROOT/'launcher/tacto.swift').read_text()
        schema=source[source.index('let allowedTypes:'):source.index('// MARK: - Socket plumbing')]
        checks=r'''
let root:[String:Any]=["type":"document_attachment_metadata","target":"com.apple.TextEdit","document_token":"token"]
precondition(vetted(root)==nil)
for patch:[String:Any] in [["target":"foreign"],["document_token":true],["document_token":""],
 ["max_utf16_units":true],["max_utf16_units":1.0],["max_utf16_units":100001],
 ["timeout_seconds":true],["timeout_seconds":0],["timeout_seconds":Double.nan],["timeout_seconds":7],
 ["document_path":"/private/tmp/owned.rtfd"],["action":["name":"read","element_index":0]]] {
 precondition(vetted(root.merging(patch){_,new in new}) != nil)
}
print("SCHEMA_PASS")
'''
        self.run_swift('import Foundation\nimport CoreFoundation\nfunc modelTimeout(_ raw:Any?)->Double? {1}\n'+schema+checks,'SCHEMA_PASS')

    def run_swift(self,source,expected):
        with tempfile.TemporaryDirectory() as directory:
            script=Path(directory)/'main.swift';script.write_text(source)
            result=subprocess.run(['swift',str(script)],capture_output=True,text=True,timeout=40)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip(),expected)

    def test_actual_handler_identity_metadata_stability_limits_and_timeout(self):
        source=(ROOT/'helper/TactoHelper.swift').read_text()
        handler=source[source.index('func documentAttachmentMetadata('):source.index('func documentScopedSaveAction(')]
        harness=r'''
import Foundation
import CryptoKit
import ApplicationServices
var mode="good",path="/private/tmp/owned.rtfd",text="한😀\u{FFFC}",textReads=0,metadataReads=0
let window=AXUIElementCreateApplication(10001),body=AXUIElementCreateApplication(10002),other=AXUIElementCreateApplication(10003)
struct Pinned {let pid:pid_t;let stamp:Double;let window:AXUIElement;let body:AXUIElement}
var documentTokens:[(token:String,document:Pinned)]=[]
var documentOperationDeadline:Double?=nil
enum DocumentError:Error {case fail(String)}
func sha256Hex(_ data:Data)->String {SHA256.hash(data:data).map{String(format:"%02x",$0)}.joined()}
func documentIsWholeNumber(_ raw:Any?)->NSNumber? {
 guard let n=raw as? NSNumber,CFGetTypeID(n) != CFBooleanGetTypeID(),!CFNumberIsFloatType(n as CFNumber) else{return nil};return n
}
func documentCheckAbort()throws {
 if mode=="abort" || ProcessInfo.processInfo.systemUptime >= (documentOperationDeadline ?? .infinity){throw DocumentError.fail("stop")}
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
 precondition(CFEqual(el,body) && name as String == "AXAttributedStringForRange")
 metadataReads += 1
 if mode=="path" {path="/private/tmp/other.rtfd"}
 if mode=="body" {documentTokens=[("token",Pinned(pid:42,stamp:10,window:window,body:other))]}
 if mode=="expired" {documentTokens=[]}
 if mode=="late" {Thread.sleep(forTimeInterval:0.03)}
 if mode=="wrongtype" {result.pointee="wrong" as NSString;return .success}
 let attributed=NSMutableAttributedString(string:text)
 if !text.isEmpty && !(mode=="metadata" && metadataReads>1) {
  attributed.addAttribute(NSAttributedString.Key("AXAttachment"),value:other,range:NSRange(location:text.utf16.count-1,length:1))
 }
 result.pointee=attributed;return .success
}
func reset(_ kind:String="good") {
 mode=kind;path="/private/tmp/owned.rtfd";text="한😀\u{FFFC}";textReads=0;metadataReads=0
 documentOperationDeadline=nil;documentTokens=[("token",Pinned(pid:42,stamp:10,window:window,body:body))]
}
'''
        checks=r'''
let request:[String:Any]=["type":"document_attachment_metadata","target":"com.apple.TextEdit","document_token":"token"]
reset();let good=documentAttachmentMetadata(request)
precondition(good["type"] as? String == "document_attachment_metadata")
let rows=good["occurrences"] as! [[String:Any]]
precondition(rows.count==1 && rows[0]["utf16_index"] as? Int==3 && rows[0]["ax_element_reference"] as? Bool==true)
precondition(good["attachment_payload_verified"] as? Bool==false && good["authorizes_save"] as? Bool==false)
precondition(textReads==3 && metadataReads==2 && documentOperationDeadline==nil)
for kind in ["text","metadata","path","body","expired","abort","wrongtype","late","reset"] {
 reset(kind);var r=request;if kind=="late" {r["timeout_seconds"]=0.01}
 precondition(documentAttachmentMetadata(r)["effect"] as? String=="not_started",kind)
 precondition(documentOperationDeadline==nil)
}
reset();text=String(repeating:"\u{FFFC}",count:257)
precondition(documentAttachmentMetadata(request)["effect"] as? String=="not_started" && metadataReads==0)
for patch:[String:Any] in [["document_token":"unknown"],["document_token":true],["target":"foreign"],["max_utf16_units":1.0],["timeout_seconds":true],["timeout_seconds":0],["extra":0]] {
 reset();precondition(documentAttachmentMetadata(request.merging(patch){_,new in new})["effect"] as? String=="not_started")
 precondition(textReads==0 && metadataReads==0)
}
print("METADATA_PASS")
'''
        self.run_swift(harness+handler+checks,'METADATA_PASS')
