"""Drag payload, real native construction, timed dispatch and cleanup contracts."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from tacto.native_drag import drag_problem
from tacto.protocol import drag_reobservation
ROOT=Path(__file__).resolve().parents[1]

class DragPayloadTests(unittest.TestCase):
    def test_bounded_path_and_unambiguous_receiving_contract(self):
        action={'name':'drag','element_index':0,'from':{'x':20,'y':40},'to':{'x':220,'y':80},'duration':0.6}
        self.assertIsNone(drag_problem(action))
        for changes in ({'duration':True},{'duration':float('nan')},{'duration':3},{'from':{'x':True,'y':2}},
                        {'via':[{'x':1,'y':2}]*13},{'to':action['from']},{'point':{'x':1,'y':2}}):
            self.assertIsNotNone(drag_problem({**action,**changes}))
        response={'type':'acted','input_route':'window_bound_drag','unverified':'observe_effect',
                  'reobserve_required':True,'posted_events':20}
        caps={'action.drag','drag.window_bound'}
        self.assertTrue(drag_reobservation(action,response,caps))
        for changes in ({'posted_events':2},{'posted_events':True},{'reobserve_required':False},{'input_route':'other'}):
            self.assertFalse(drag_reobservation(action,{**response,**changes},caps))

@unittest.skipUnless(sys.platform=='darwin' and shutil.which('xcrun'),'macOS Swift required')
class NativeDragTests(unittest.TestCase):
    def test_real_events_timing_cancellation_cleanup_and_window_drift(self):
        harness=r'''
import Foundation
import AppKit
import ApplicationServices
import Darwin
let pid=getpid(),element=AXUIElementCreateApplication(getpid())
let start=nativeProcessStartIdentity(pid)!
let binding=NativeWindowBinding(element:element,window:element,pid:pid,processStart:start,
 windowID:17,windowBounds:CGRect(x:-300,y:200,width:300,height:200),
 elementBounds:CGRect(x:-300,y:200,width:300,height:200),point:CGPoint(x:-280,y:240),pointKind:.windowPoint)
let drag=NativeWindowDrag(path:[CGPoint(x:20,y:40),CGPoint(x:70,y:90),CGPoint(x:220,y:80)],duration:0.2)
let stroke=try buildNativeWindowDrag(binding,drag:drag)
precondition(stroke.events.count==9)
let (_,getLocation)=try WindowInputSPI.locationFunctions()
precondition(stroke.events.contains {getLocation($0)==CGPoint(x:70,y:90)})
let stamps=stroke.events.map {NSEvent(cgEvent:$0)!.timestamp}
precondition(abs(stamps.last!-stamps.first!-0.2)<0.00001)
precondition(zip(stamps,stamps.dropFirst()).allSatisfy {$0 <= $1})
precondition(stroke.events.first!.type == .leftMouseDown && stroke.events.last!.type == .leftMouseUp)
precondition(getLocation(stroke.events.last!) == CGPoint(x:220,y:80))
precondition(stroke.events.last!.location == CGPoint(x:-80,y:280))
precondition(Set(stroke.events.map {$0.getIntegerValueField(.mouseEventNumber)}).count==1)
for event in stroke.events.dropFirst().dropLast() {
 precondition(event.type == .leftMouseDragged && event.getDoubleValueField(.mouseEventPressure)==1)
}
for path in [[CGPoint(x:20,y:40),CGPoint(x:300,y:80)],[CGPoint(x:20,y:40),CGPoint(x:CGFloat.nan,y:80)]] {
 do {_=try buildNativeWindowDrag(binding,drag:NativeWindowDrag(path:path,duration:0.2));fatalError("invalid admitted")}
 catch NativeWindowInputError.refused {}
}
for mode in ["success","cancel","post_error","drift","dead_process","late"] {
 var clock=10.0,stop=false,posts:[CGEvent]=[]
 do {
  _=try dispatchNativeWindowDrag(binding,drag:drag,deadline:mode=="late" ? 10.1:20,
   stopped:{stop},context:{},validate:{},now:{clock},
   readInput:{_ in NativeUserInputState(foreground:99,pointer:.zero,focused:nil)},
   pointer:{.zero},foreground:{99},post:{event,_ in
    posts.append(event)
    if mode=="cancel" || mode=="dead_process" {stop=true}
    if mode=="post_error" && posts.count==2 {throw NativeWindowInputError.refused("post uncertain")}
   },sameProcess:{mode != "dead_process"},windowUnchanged:{
    if mode=="drift" && posts.count==1 {throw NativeWindowInputError.refused("window moved")}
   },sleep:{clock += max(0.0001,$0)})
  precondition(mode=="success" && posts.count==9 && clock>=10.2)
 } catch let failure as NativeWindowDispatchFailure {
  precondition(mode != "success")
  precondition(failure.attempted==posts.count)
  if mode=="late" {precondition(posts.isEmpty)}
  else if mode=="dead_process" {precondition(posts.count==1)}
  else {
   precondition(posts.last!.type == .leftMouseUp)
   if mode=="cancel" || mode=="drift" {precondition(posts.last!.location==binding.point)}
   if mode=="post_error" {precondition(posts.last!.location==posts[1].location)}
  }
 }
}
print("DRAG_PASS")
'''
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'main.swift';binary=Path(directory)/'check'
            source.write_text(harness)
            build=subprocess.run(['xcrun','swiftc',str(source),str(ROOT/'helper/WindowInput.swift'),
                str(ROOT/'helper/WindowDispatch.swift'),str(ROOT/'helper/FrontmostProcess.swift'),'-o',str(binary)],
                capture_output=True,text=True,timeout=90)
            self.assertEqual(0,build.returncode,build.stderr)
            result=subprocess.run([str(binary)],capture_output=True,text=True,timeout=15)
            self.assertEqual(0,result.returncode,result.stderr)
            self.assertEqual('DRAG_PASS',result.stdout.strip())

    def test_launcher_accepts_generic_drag_without_vroid_title(self):
        source=(ROOT/'launcher/tacto.swift').read_text()
        schema=source[source.index('let allowedTypes:'):source.index('// MARK: - Socket plumbing')]
        checks=r'''
let action:[String:Any] = ["name":"drag","element_index":0,"from":["x":20,"y":40],"to":["x":220,"y":80],"duration":0.6]
let base:[String:Any] = ["type":"act","obs_id":"owned","target":"com.apple.TextEdit","coordinate_authorized":true,"action":action]
precondition(vetted(base)==nil)
for changes:[String:Any] in [["duration":true],["duration":0],["duration":3],["from":["x":-1,"y":0]],
 ["to":["x":true,"y":0]],["via":Array(repeating:["x":1,"y":2],count:13)],["title":"unsupported"]] {
 var a=action;for(k,v) in changes {a[k]=v};var r=base;r["action"]=a;precondition(vetted(r) != nil)
}
var missing=base;missing.removeValue(forKey:"coordinate_authorized");precondition(vetted(missing) != nil)
print("LAUNCHER_DRAG_PASS")
'''
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'main.swift'
            path.write_text('import Foundation\nimport CoreFoundation\nfunc modelTimeout(_ raw:Any?)->Double? {1}\n'+schema+checks)
            result=subprocess.run(['swift',str(path)],capture_output=True,text=True,timeout=45)
        self.assertEqual(0,result.returncode,result.stderr)
        self.assertEqual('LAUNCHER_DRAG_PASS',result.stdout.strip())

class DragReplayTests(unittest.TestCase):
    def test_replay_requires_receiving_effect_and_never_retries(self):
        from tacto.contract import Envelope,ContextBinding
        from tacto.flow import Flow,Step,Selector,Postcondition,replay,pin_params
        params={'from':{'x':20,'y':40},'to':{'x':220,'y':80},'duration':0.6}
        env=Envelope.minimal()
        step=Step('drag',Selector(role='Window',title='Fixture'),params=params,
            safety=env.contract_for({'write_ephemeral'},'drag'),
            binding=ContextBinding(bundle_id='fixture',role='Window',title='Fixture',operation='drag',params_digest=pin_params(params)),
            postcondition=Postcondition(field=Selector(identifier='state'),equals='moved',attribute='Value'))
        class App:
            capabilities={'action.drag','drag.window_bound'}
            def __init__(self,moves):self.moves=moves;self.calls=[];self.state='waiting'
            def observe(self,*a,**k):return {'type':'observation','obs_id':'obs','tree':'0 Window Title: Fixture\n  1 StaticText Identifier: state, Value: '+self.state}
            def act(self,obs,target,action,**kwargs):
                self.calls.append((action,kwargs))
                if self.moves:self.state='moved'
                return {'type':'acted','input_route':'window_bound_drag','unverified':'observe_effect',
                        'reobserve_required':True,'posted_events':20}
            def wait_until(self,target,predicate,**kwargs):
                obs=self.observe();return obs if predicate(obs['tree']) else {'type':'timeout','last_obs':obs}
        for moves in (True,False):
            app=App(moves);result=replay(app,Flow('drag','fixture',[step],schema=2))
            self.assertEqual(result.ok,moves,result.log)
            self.assertEqual(len(app.calls),1)
            self.assertEqual(app.calls[0][1]['risk_class'],'preapproved')
            self.assertIs(app.calls[0][1]['approved'],True)

class DragCLITests(unittest.TestCase):
    def test_single_window_compact_output_and_no_retry_on_partial_send(self):
        import contextlib,io
        from tacto.native_drag import main
        class App:
            capabilities={'action.drag'}
            def __init__(self,partial=False):self.calls=[];self.partial=partial
            def observe(self,*a,**k):return {'type':'observation','obs_id':'obs','tree':'0 Window Title: "Fixture" [focused]\n  1 Slider Value: 20'}
            def act(self,*a,**k):
                self.calls.append((a,k))
                if self.partial:return {'type':'error','error':'action_failed','effect':'unknown'}
                return {'type':'acted','action':'drag','input_route':'window_bound_drag',
                        'posted_events':20,'unverified':'observe_effect','reobserve_required':True,
                        'preparation_diagnostic':{'private':'omit by default'}}
        for partial in (False,True):
            app=App(partial);output=io.StringIO()
            with contextlib.redirect_stdout(output):
                code=main(['fixture','--helper','--from','20','40','--to','220','80'],app)
            self.assertEqual(code,int(partial))
            self.assertEqual(len(app.calls),1)
            value=json.loads(output.getvalue())
            self.assertNotIn('preparation_diagnostic',value)
            self.assertNotIn('observation',value)
            self.assertTrue(value['observed'])

    def test_prepare_reobserves_and_never_reuses_old_element_index(self):
        import contextlib,io
        from tacto.native_drag import main
        class App:
            capabilities={'action.drag'}
            def __init__(self):self.calls=[]
            def observe(self,*a,**k):return {'type':'observation','obs_id':str(len(self.calls)),
                'tree':('0' if not self.calls else '8')+' Window Title: "Fixture" [focused]'}
            def act(self,obs,target,action,**k):
                self.calls.append(action)
                return {'type':'acted','action':action['name']}
        app=App()
        with contextlib.redirect_stdout(io.StringIO()):
            code=main(['fixture','--helper','--prepare','--from','20','40','--to','220','80'],app)
        self.assertEqual(code,0)
        self.assertEqual([a['name'] for a in app.calls],['prepare_window','drag'])
        self.assertEqual([a['element_index'] for a in app.calls],[0,8])

class DragCaptureTests(unittest.TestCase):
    def test_capture_reports_retina_scale_and_refuses_overwriting(self):
        import contextlib,io
        from types import SimpleNamespace
        from unittest.mock import Mock,patch
        from tacto.native_drag import capture_main
        driver=Mock()
        driver.observe.return_value={'type':'observation','obs_id':'obs','tree':'0 Window Title: "Fixture"'}
        capture=SimpleNamespace(png=b'private png',viewport={'width':960,'height':640},window_frame=(20,30,480,320))
        with tempfile.TemporaryDirectory() as directory,patch('tacto.native_visual_actions.capture_window',return_value=capture):
            path=Path(directory)/'window.png'
            argv=['fixture','--helper','--output',str(path)]
            output=io.StringIO()
            with contextlib.redirect_stdout(output):code=capture_main(argv,driver)
            self.assertEqual(code,0)
            self.assertEqual(json.loads(output.getvalue())['pixel_to_point'],{'x':0.5,'y':0.5})
            self.assertEqual(path.stat().st_mode&0o777,0o600)
            self.assertEqual(path.read_bytes(),b'private png')
            with contextlib.redirect_stdout(io.StringIO()):self.assertEqual(capture_main(argv,driver),1)
            self.assertEqual(path.read_bytes(),b'private png')
