"""Opaque input construction, authorization and uncertain delivery boundaries."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest

from tacto.native_window_payload import valid_window_text, window_input_problem, WINDOW_INPUT_CAPABILITIES
from tacto.native_key_events import build_window_text
from tacto.native_window_dispatch import dispatch_window_input, WindowDispatchFailure, UserInputState

ROOT = Path(__file__).resolve().parents[1]


class OpaquePayloadTests(unittest.TestCase):
    def test_unicode_bounds_and_navigation_separation(self):
        for text in ('a', '한글', 'A😀한글', 'e\u0301', '👩\u200d💻', '😀'*32, 'a'*64):
            self.assertTrue(valid_window_text(text), repr(text))
            self.assertIsNone(window_input_problem({'name':'window_input','element_index':0,'text':text}))
        for text in ('', 'a'*65, '😀'*33, '\n', '\t', '\r', '\x1b', '\x7f', '\x85', '\u2028', '\u2029', '\uf700', '\ud800', None, True):
            self.assertFalse(valid_window_text(text), repr(text))
        for key in ('Tab','shift+Tab','Up','Down','Left','Right'):
            self.assertIsNone(window_input_problem({'name':'window_input','element_index':0,'key':key}))
        for action in ({'name':'window_input','element_index':0,'key':'Escape'},
                {'name':'window_input','element_index':0,'key':'cmd+s'},
                {'name':'window_input','element_index':0,'text':'a','key':'Tab'},
                {'name':'window_input','element_index':True,'text':'a'},
                {'name':'window_input','element_index':1.0,'text':'a'},
                {'name':'window_input','element_index':0,'text':'a','point':{'x':1,'y':2}}):
            self.assertIsNotNone(window_input_problem(action))

    def test_reflected_pair_must_keep_every_window_character_and_flag_field(self):
        def create(kind, window, code, chars, plain, flags, timestamp):
            return object(), {'kind':kind, 'window':window, 'text':None}
        def backend(corrupt=None):
            def read(event):
                chars = event['text'] if event['kind']==10 else 'a'
                row=[event['kind'],event['kind'],event['window'],0,0,chars,chars,0,0,False,0]
                if corrupt is not None: row[corrupt]='changed'
                return tuple(row)
            return SimpleNamespace(create=create, unicode=lambda e,t:e.update(text=t),
                unicode_text=lambda e:(len(e['text'].encode('utf-16-le'))//2,e['text']), read=read)
        self.assertEqual(17,build_window_text(17,'A😀한글',backend=backend()).window_id)
        for field in range(11):
            with self.subTest(field=field), self.assertRaisesRegex(RuntimeError,'readback'):
                build_window_text(17,'A😀한글',backend=backend(field))

    @unittest.skipUnless(importlib.util.find_spec('AppKit'),'macOS AppKit required')
    def test_actual_python_unicode_pair_construction_never_posts(self):
        for text in ('한글','A😀한글','e\u0301','👩\u200d💻','😀'*32,'a'*64):
            pair=build_window_text(17,text)
            self.assertEqual(17,pair.window_id)
            self.assertEqual(2,len(pair._owners))


class OpaqueDispatchTests(unittest.TestCase):
    def run_mode(self, mode, payload=None):
        posts=[];state={'stop':mode=='stop_before','clock':1001 if mode=='expired_before' else 10}
        binding=SimpleNamespace(point_kind='window_input',element='window',window='window',pid=42,
            window_id=17,_backend=SimpleNamespace(equal=lambda a,b:a==b))
        if mode=='bad_binding':binding.point_kind='titlebar'
        if mode=='bad_element':binding.element='input'
        def route(*args,**kwargs):
            if mode=='route_before' or (mode=='route_after' and posts):raise RuntimeError('window changed')
        def read(scope):
            scope.ready()
            return UserInputState(42 if mode=='foreground_before' else 77,
                (1,1) if mode=='pointer_after' and posts else (0,0),
                'changed' if mode=='focus_after' and posts else 'focus')
        def post(event,pid):
            self.assertEqual(42,pid);posts.append(event)
            if mode=='stop_after':state['stop']=True
            if mode=='expired_after':state['clock']=1001
            if mode=='post_error':raise RuntimeError('uncertain post')
        backend=SimpleNamespace(read_input=read,equal=lambda a,b:a==b,post=post)
        try:
            result=dispatch_window_input(binding,payload or {'text':'A😀한글'},deadline=1000,
                stopped=lambda:state['stop'],context=lambda:None,clock=lambda:state['clock'],backend=backend,
                validate=lambda b:None,routing=route,
                build_pair=lambda *args:SimpleNamespace(down=10,up=11,_owners=(object(),object())))
            return posts,result,None
        except WindowDispatchFailure as error:return posts,None,error

    def test_complete_pair_is_unverified_and_requires_receiving_evidence(self):
        for payload in ({'text':'A😀한글'},{'key':'Right'}):
            posts,result,error=self.run_mode('success',payload)
            self.assertIsNone(error)
            self.assertEqual([10,11],posts)
            self.assertEqual('window_input',result['input_route'])
            self.assertEqual('observe_effect',result['unverified'])
            self.assertIs(True,result['reobserve_required'])

    def test_partial_pair_never_restores_focus_or_sends_next_event(self):
        for mode,attempts in [(m,0) for m in ('bad_binding','bad_element','stop_before','expired_before','foreground_before','route_before')]+[(m,1) for m in ('stop_after','expired_after','post_error','pointer_after','focus_after','route_after')]:
            posts,result,error=self.run_mode(mode)
            self.assertIsNone(result,mode)
            self.assertEqual((attempts,attempts),(len(posts),error.attempted),mode)

    def test_no_extra_internal_payload_fields(self):
        for payload in ({'text':'a','element_index':0},{'key':'Right','text':'a'},{'key':'Escape'}):
            posts,result,error=self.run_mode('success',payload)
            self.assertFalse(posts)
            self.assertEqual(0,error.attempted)


class OpaqueHelperTests(unittest.TestCase):
    def driver(self):
        from tacto.helper import HelperDriver
        driver=HelperDriver.__new__(HelperDriver)
        driver.capabilities=set(WINDOW_INPUT_CAPABILITIES)
        driver._send=Mock(return_value={'type':'acted','input_route':'window_input'})
        return driver

    def test_only_literal_host_approval_reaches_channel_and_has_deadline(self):
        driver=self.driver();action={'name':'window_input','element_index':0,'text':'A😀한글'}
        for risk,approved in [('none',True),('preapproved',1),('confirm','yes'),('preapproved',False)]:
            self.assertEqual('not_started',driver.act('obs','owned',action,risk_class=risk,approved=approved)['effect'])
        driver._send.assert_not_called()
        driver.act('obs','owned',action,risk_class='preapproved',approved=True)
        msg=driver._send.call_args.args[0]
        self.assertIs(True,msg['window_input_authorized'])
        self.assertGreater(msg['click_deadline_uptime'],0)
        self.assertEqual(action,msg['action'])

    def test_capability_and_parent_deadline_refuse_before_channel(self):
        from tacto.model_budget import model_deadline
        import time
        driver=self.driver();action={'name':'window_input','element_index':0,'key':'Right'}
        with model_deadline(time.monotonic()-1):
            self.assertEqual('not_started',driver.act('obs','owned',action,risk_class='preapproved',approved=True)['effect'])
        driver.capabilities=set()
        self.assertEqual('not_started',driver.act('obs','owned',action,risk_class='preapproved',approved=True)['effect'])
        driver._send.assert_not_called()


@unittest.skipUnless(sys.platform=='darwin' and shutil.which('xcrun'),'macOS Swift required')
class OpaqueSwiftTests(unittest.TestCase):
    def test_actual_builders_and_dispatch_boundary(self):
        harness=r'''
import Foundation
import AppKit
import ApplicationServices
for text in ["한글", "A😀한글", "e\u{301}", "👩\u{200D}💻", String(repeating:"😀",count:32), String(repeating:"a",count:64)] {
    precondition(nativeWindowTextValid(text))
    let pair=try buildNativeWindowText(17,text:text)
    precondition(pair.owners.count==2)
}
for text in ["", "\n", "\t", "\r", "\u{1B}", "\u{85}", "\u{2028}", "\u{2029}", "\u{F700}", String(repeating:"😀",count:33)] {
    precondition(!nativeWindowTextValid(text))
}
let window=AXUIElementCreateApplication(10001), focus=AXUIElementCreateApplication(10002)
let binding=NativeWindowBinding(element:window,window:window,pid:42,processStart:10,windowID:17,
    windowBounds:CGRect(x:100,y:200,width:300,height:200),elementBounds:CGRect(x:100,y:200,width:300,height:200),
    point:CGPoint(x:250,y:300),pointKind:.windowInput)
for mode in ["success","stop","deadline","post","pointer","routing"] {
    var posted:[UInt32]=[], stop=false,clock=10.0
    do {
        let result=try dispatchNativeWindowPreparation(binding,deadline:1000,stopped:{stop},context:{},now:{clock},
            readInput:{_ in NativeUserInputState(foreground:77,pointer:mode=="pointer" && !posted.isEmpty ? CGPoint(x:1,y:1):.zero,focused:focus)},
            validate:{_ in},post:{event,pid in
                precondition(pid==42);posted.append(event.type.rawValue)
                if mode=="stop" {stop=true};if mode=="deadline" {clock=1001}
                if mode=="post" {throw NativeWindowInputError.unavailable("uncertain")}
            },routing:{_,_ in if mode=="routing" && !posted.isEmpty {throw NativeWindowInputError.refused("window changed")}},
            windowInput:.text("A😀한글"))
        precondition(mode=="success" && result.posted==2 && posted==[10,11])
    } catch let failure as NativeWindowDispatchFailure {
        precondition(mode != "success" && failure.attempted==1 && posted==[10])
    }
}
print("OPAQUE_SWIFT_PASS")
'''
        with tempfile.TemporaryDirectory(prefix='tacto-opaque-swift-') as directory:
            source=Path(directory)/'main.swift';binary=Path(directory)/'check';source.write_text(harness)
            built=subprocess.run(['xcrun','swiftc','-warnings-as-errors',str(source),
                str(ROOT/'helper/WindowInput.swift'),str(ROOT/'helper/WindowDispatch.swift'),
                str(ROOT/'helper/FrontmostProcess.swift'),'-o',str(binary)],capture_output=True,text=True,timeout=60)
            self.assertEqual(0,built.returncode,built.stdout+built.stderr)
            ran=subprocess.run([str(binary)],capture_output=True,text=True,timeout=10)
            self.assertEqual(0,ran.returncode,ran.stdout+ran.stderr)
            self.assertIn('OPAQUE_SWIFT_PASS',ran.stdout)

    def test_actual_launcher_exact_payload_and_authorization(self):
        code=(ROOT/'launcher/tacto.swift').read_text()
        schema=code[code.index('let allowedTypes:'):code.index('// MARK: - Socket plumbing')]
        harness=r'''
let root:[String:Any]=["type":"act","obs_id":"owned","target":"fixture","window_input_authorized":true,
    "click_deadline_uptime":10,"action":["name":"window_input","element_index":0,"text":"A😀한글"]]
precondition(vetted(root)==nil)
for flag:Any in [false,1,"true",NSNull()] {var r=root;r["window_input_authorized"]=flag;precondition(vetted(r) != nil)}
var missing=root;missing.removeValue(forKey:"window_input_authorized");precondition(vetted(missing) != nil)
for value:Any in [true,0.0,-1] {var r=root;r["action"]=["name":"window_input","element_index":value,"text":"a"];precondition(vetted(r) != nil)}
for text in ["","\n","\u{85}","\u{2028}","\u{F700}",String(repeating:"a",count:65)] {var r=root;r["action"]=["name":"window_input","element_index":0,"text":text];precondition(vetted(r) != nil)}
for key in ["Tab","shift+Tab","Up","Down","Left","Right"] {var r=root;r["action"]=["name":"window_input","element_index":0,"key":key];precondition(vetted(r)==nil)}
for key in ["Escape","Return","cmd+s"] {var r=root;r["action"]=["name":"window_input","element_index":0,"key":key];precondition(vetted(r) != nil)}
var both=root;both["action"]=["name":"window_input","element_index":0,"key":"Tab","text":"a"];precondition(vetted(both) != nil)
var foreign=root;foreign["action"]=["name":"read","element_index":0];precondition(vetted(foreign) != nil)
print("OPAQUE_LAUNCHER_PASS")
'''
        with tempfile.TemporaryDirectory(prefix='tacto-opaque-schema-') as directory:
            source=Path(directory)/'main.swift';source.write_text('import Foundation\nimport CoreFoundation\nfunc modelTimeout(_ raw: Any?) -> Double? { 1 }\n'+schema+harness)
            ran=subprocess.run(['swift',str(source)],capture_output=True,text=True,timeout=60)
            self.assertEqual(0,ran.returncode,ran.stdout+ran.stderr)
            self.assertIn('OPAQUE_LAUNCHER_PASS',ran.stdout)

@unittest.skipUnless(importlib.util.find_spec('ApplicationServices'), 'native driver required')
class OpaqueDriverTests(unittest.TestCase):
    def test_native_window_parameters_and_partial_effect_are_preserved(self):
        from tacto.driver import Driver
        driver=Driver.__new__(Driver);window=object()
        obs=SimpleNamespace(target='fixture',pid=42,obs_id='owned',elements=[window])
        driver._latest={'fixture':obs};driver._last_action_at=0;driver._showing_control=Mock()
        action={'name':'window_input','element_index':0,'text':'A😀한글'}
        driver._perform=Mock()
        for risk,approved in [('none',True),('preapproved',1),('confirm','yes')]:
            self.assertEqual('not_started',driver.act('owned','fixture',action,risk_class=risk,approved=approved)['effect'])
        driver._perform.assert_not_called()
        del driver._perform
        with patch('tacto.driver._attr',return_value='AXWindow'),patch.object(driver,'_window_action_binding',return_value=('binding',123,lambda:None)) as capture, \
             patch('tacto.native_window_dispatch.dispatch_window_input') as dispatch:
            dispatch.return_value={'input_route':'window_input','posted_events':2,'unverified':'observe_effect','reobserve_required':True}
            result=driver.act('owned','fixture',action,risk_class='preapproved',approved=True)
            self.assertEqual('acted',result['type'])
            self.assertEqual((obs,window,'window_input'),capture.call_args.args[:3])
            self.assertEqual(('binding',{'text':'A😀한글'}),dispatch.call_args.args[:2])
            dispatch.side_effect=WindowDispatchFailure('after one event',attempted=1)
            result=driver.act('owned','fixture',action,risk_class='preapproved',approved=True)
            self.assertEqual(('unknown',1),(result['effect'],result['posted_or_attempted_events']))


class OpaqueTraceTests(unittest.TestCase):
    def test_recorded_input_cannot_become_live_window_authority(self):
        from tacto.trace import Trace, TraceDriver
        trace=Trace();trace.capabilities=list(WINDOW_INPUT_CAPABILITIES)
        trace.events=[{'kind':'act','request':{},'result':{'type':'acted'}}]
        driver=TraceDriver(trace)
        self.assertFalse(WINDOW_INPUT_CAPABILITIES & driver.capabilities)
        result=driver.act('old','owned',{'name':'window_input','element_index':0,'text':'a'},risk_class='preapproved',approved=True)
        self.assertEqual('not_started',result['effect'])
        self.assertEqual(0,driver._i)

class OpaqueBindingTests(unittest.TestCase):
    def test_window_identity_and_geometry_are_rechecked_without_fictitious_ax_input(self):
        import tacto.native_window_binding as binding
        from tests.test_native_window_binding import Backend
        backend=Backend();backend.nodes['window']['AXSubrole']='AXStandardWindow'
        backend.require_ready=lambda *args:None
        backend.hit=lambda *args:self.fail('opaque keyboard binding must not fabricate an AX input element')
        with patch.object(binding,'_load_backend',return_value=backend):
            observed=binding.capture_binding('window',4,point_kind='window_input')
        self.assertEqual('window_input',observed.point_kind)
        self.assertIs(binding.validate_binding(observed),observed)
        backend.nodes['window']['frame']=(11,20,300,200);backend.cg[0]['bounds']=(11,20,300,200)
        with self.assertRaises(binding.NativeWindowBindingError):binding.validate_binding(observed)
        with patch.object(binding,'_load_backend',return_value=backend),self.assertRaises(binding.NativeWindowBindingError):
            binding.capture_binding('button',4,point_kind='window_input')

    def test_text_raw_payload_mismatch_is_rejected_even_if_appkit_reflection_matches(self):
        from tacto.native_key_events import _MacBackend
        if not importlib.util.find_spec('AppKit'):self.skipTest('AppKit unavailable')
        backend=_MacBackend()
        with patch.object(backend,'unicode_text',return_value=(1,'x')),self.assertRaisesRegex(RuntimeError,'readback'):
            build_window_text(17,'한글',backend=backend)


class OpaqueReceiptTests(unittest.TestCase):
    def test_partial_and_forged_receipts_are_not_receiving_evidence(self):
        from tacto.protocol import window_input_reobservation
        action={'name':'window_input','element_index':0,'text':'A😀한글'}
        response={'type':'acted','input_route':'window_input','mechanism':'pid_window_events',
            'unverified':'observe_effect','reobserve_required':True,'posted_events':2,'window_id':17,'obs_id':'owned'}
        check=lambda r,a=action,c=WINDOW_INPUT_CAPABILITIES:window_input_reobservation(a,r,c,obs_id='owned',window_id=17)
        self.assertTrue(check(response))
        for changes in ({'posted_events':1},{'posted_events':True},{'window_id':18},{'obs_id':'stale'},
                {'mechanism':'global'},{'input_route':'window_key'},{'reobserve_required':False},
                {'type':'error','effect':'unknown'},{'unverified':False}):
            self.assertFalse(check({**response,**changes}),changes)
        self.assertFalse(check(response,{**action,'text':'\n'}))
        self.assertFalse(check(response,c=set()))
