"""Internal point dispatch on an owned non-accessibility canvas, with host counters."""
from pathlib import Path
import hashlib
import json
import os
import plistlib
import subprocess
import tempfile
import time

TARGET = 'ai.callabo.nlc.opaque-point-fixture'


def main(output, *, public_driver=None, vision_provider=None, general_visual=False, flow_visual=False):
    from tacto.driver import Driver, _attr
    from tacto.flow import Selector
    from tacto.native_window_binding import capture_binding
    from tacto.native_window_dispatch import dispatch_point, WindowReadScope, WindowDispatchFailure
    from tacto.native_process_identity import process_start_identity
    from tacto.input_audit_process import ProcessInputAudit
    from ApplicationServices import AXUIElementCopyActionNames, AXUIElementCopyElementAtPosition, AXUIElementCreateApplication
    root = Path(__file__).resolve().parents[2]
    sources = ['tests/fixtures/NativeOpaquePointFixture.swift', 'examples/probes/native-opaque-point.py',
               'tacto/driver.py', 'tacto/native_window_binding.py', 'tacto/native_window_dispatch.py',
               'tacto/native_mouse_events.py', 'tacto/native_process_identity.py', 'tacto/native_frontmost.py',
               'tacto/input_audit_process.py', 'tacto/input_audit.py', 'tacto/helper.py',
               'helper/WindowInput.swift', 'helper/WindowDispatch.swift', 'helper/TactoHelper.swift', 'launcher/tacto.swift',
               'tacto/native_visual.py', 'tacto/native_visual_actions.py', 'tacto/visual.py', 'tacto/model_budget.py',
               'tacto/codex_result.py', 'tacto/claude_result.py', 'tacto/provider_observation.py',
               'tacto/ask_provider.py', 'tacto/provider_images.py', 'tacto/provider_result.py',
               'tacto/general.py', 'tacto/general_visual.py', 'tacto/protocol.py', 'tacto/promote.py',
               'tacto/contract.py', 'tacto/flow.py', 'tacto/safety.py', 'tacto/visual_replay.py']
    hashes = lambda: {name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in sources}
    report = {'passed':False, 'scope':'public authorized point' if public_driver else 'internal Python point pair',
              'source_hashes_before':hashes(), 'public_coordinate_capability_enabled':public_driver is not None,
              'transport':type(public_driver).__name__ if public_driver else 'internal'}
    if vision_provider not in (None, 'fixed', 'codex', 'claude') or (vision_provider and public_driver is None):
        raise ValueError('visual mode requires an explicit public driver and supported planner')
    if type(general_visual) is not bool or (general_visual and vision_provider is None):
        raise ValueError('general visual mode requires an explicit image planner')
    report['vision_provider'] = vision_provider
    if type(flow_visual) is not bool or (flow_visual and (vision_provider is None or general_visual)):
        raise ValueError('flow visual mode requires its own explicit image planner')
    report['flow_visual'] = flow_visual
    report['general_visual'] = general_visual
    driver = Driver(); child = None
    audit = ProcessInputAudit(interval=.005, startup_timeout=10, stop_timeout=3, max_samples=10000)
    try:
        assert driver._resolve(TARGET, launch=False) is None, 'owned fixture already running'
        with tempfile.TemporaryDirectory(prefix='tacto-opaque-point-') as directory:
            bundle = Path(directory)/'Opaque.app'; binaries = bundle/'Contents/MacOS'
            binaries.mkdir(parents=True); binary = binaries/'Opaque'
            subprocess.run(['xcrun','swiftc','-parse-as-library','-warnings-as-errors', str(root/sources[0]), '-o', str(binary)],
                           check=True, capture_output=True, timeout=45)
            (bundle/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleExecutable':'Opaque',
                'CFBundleIdentifier':TARGET,'CFBundleName':'NLC Opaque Point Fixture','CFBundlePackageType':'APPL'}))
            subprocess.run(['codesign','--force','--sign','-',str(bundle)],check=True,capture_output=True,timeout=15)
            with audit:
                child = subprocess.Popen([str(binary)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                try:
                    until = time.monotonic()+10
                    while driver._resolve(TARGET,launch=False) is None:
                        assert child.poll() is None and time.monotonic()<until, 'owned fixture did not start'
                        time.sleep(.05)
                    time.sleep(.3)
                    def observe():
                        reply=driver.observe(TARGET,'tree_full')
                        assert reply.get('type')=='observation',reply
                        return driver._latest[TARGET.lower()]
                    def state():
                        obs=observe()
                        return {str(_attr(element,'AXIdentifier')):str(_attr(element,'AXValue'))
                            for element in obs.elements if _attr(element,'AXIdentifier') in ('opaque-A-state','opaque-B-state')}
                    def window(obs,suffix):
                        found=[(index,element) for index,element in enumerate(obs.elements)
                            if _attr(element,'AXRole')=='AXWindow'
                            and _attr(element,'AXTitle')=='NLC Opaque Point Fixture '+suffix]
                        assert len(found)==1,'owned window absent or ambiguous'
                        return found[0]
                    report['initial']=state()
                    obs=observe(); index,element=window(obs,'A')
                    if public_driver:
                        view=public_driver.observe(TARGET,'tree_full')
                        public_index=Selector(role='Window',title='NLC Opaque Point Fixture A').resolve(view['tree'])[0]
                        prepared=public_driver.act(view['obs_id'],TARGET,{'name':'prepare_window','element_index':public_index})
                    else:
                        prepared=driver.act(obs.obs_id,TARGET,{'name':'prepare_window','element_index':index})
                    report['preparation']=prepared
                    assert prepared.get('type')=='acted' and prepared.get('posted_events')==3,prepared
                    time.sleep(.12)
                    report['after_preparation']=state()
                    assert 'downs=0 ups=0 greens=0' in report['after_preparation']['opaque-A-state']
                    def binding_for(obs,element):
                        start=obs.process_launch_identity
                        deadline=time.monotonic()+5
                        elements=tuple(obs.elements)
                        def context():
                            if (driver._latest.get(TARGET.lower()) is not obs
                                    or driver._resolve(TARGET,launch=False)!=obs.pid
                                    or process_start_identity(obs.pid)!=start
                                    or tuple(obs.elements)!=elements):
                                raise RuntimeError('owned observation changed')
                        scope=WindowReadScope(deadline,context)
                        binding=capture_binding(element,obs.pid,start,point_kind='window_point',
                                                relative_point=(280,120),read_scope=scope)
                        return binding,deadline,context
                    if public_driver:
                        view=public_driver.observe(TARGET,'tree_full')
                        public_index=Selector(role='Window',title='NLC Opaque Point Fixture B').resolve(view['tree'])[0]
                        action={'name':'click_point','element_index':public_index,'point':{'x':280,'y':120}}
                        denied=public_driver.act(view['obs_id'],TARGET,action)
                        assert denied.get('effect')=='not_started' and denied.get('error')=='coordinate_authorization_required',denied
                        report['authorization_refusal']=denied
                        refused=public_driver.act(view['obs_id'],TARGET,action,risk_class='preapproved',approved=True)
                        assert refused.get('effect')=='not_started' and refused.get('posted_or_attempted_events')==0,refused
                        report['wrong_window_refusal']=refused
                    else:
                        obs=observe(); _,foreign=window(obs,'B')
                        binding,deadline,context=binding_for(obs,foreign)
                        try:
                            dispatch_point(binding,deadline=deadline,stopped=lambda:False,context=context)
                            raise AssertionError('unfocused target window was dispatched')
                        except WindowDispatchFailure as error:
                            assert error.attempted==0
                            report['wrong_window_refusal']={'attempted':error.attempted,'detail':str(error)}
                    obs=observe(); _,element=window(obs,'A')
                    binding,deadline,context=binding_for(obs,element)
                    # A read-only hit test cannot find an AXPress target here;
                    # the subsequent pair uses only the pinned native window.
                    error,hit=AXUIElementCopyElementAtPosition(AXUIElementCreateApplication(obs.pid),*binding.point,None)
                    assert error==0 and hit is not None,'cannot inspect owned canvas hit'
                    listed,names=AXUIElementCopyActionNames(hit,None)
                    assert listed==0 and 'AXPress' not in tuple(names or ()),'canvas exposes AXPress'
                    report['hit']={'role':_attr(hit,'AXRole'),'actions':list(names or ())}
                    if public_driver:
                        view=public_driver.observe(TARGET,'tree_full')
                        public_index=Selector(role='Window',title='NLC Opaque Point Fixture A').resolve(view['tree'])[0]
                        if vision_provider:
                            from tacto.native_visual_actions import run_native_visual
                            from tacto.visual import plan_visual
                            from tacto.provider_observation import capture_provider_calls
                            captures=[]
                            def verify(capture, history):
                                artifact=Path(str(output)+f'.capture-{len(captures)}.png')
                                capture.save(artifact)
                                captures.append({**capture.to_dict(), 'path':str(artifact)})
                                values=state()
                                return ('downs=1 ups=1 greens=1 last=green routeErrors=0' in values['opaque-A-state']
                                    and 'downs=0 ups=0 greens=0 last=none routeErrors=0' in values['opaque-B-state'])
                            def planner(capture, goal, history):
                                if vision_provider != 'fixed':
                                    return plan_visual(capture,goal,history,provider=vision_provider,timeout=60)
                                import AppKit
                                import Foundation
                                image=AppKit.NSBitmapImageRep.imageRepWithData_(Foundation.NSData.dataWithBytes_length_(capture.png,len(capture.png)))
                                assert image is not None, 'cannot decode own capture'
                                pixels=[]
                                for y in range(0,image.pixelsHigh(),4):
                                    for x in range(0,image.pixelsWide(),4):
                                        color=image.colorAtX_y_(x,y).colorUsingColorSpace_(AppKit.NSColorSpace.deviceRGBColorSpace())
                                        if color.greenComponent()>.47 and color.greenComponent()>color.redComponent()*1.6 and color.greenComponent()>color.blueComponent()*1.6:
                                            pixels.append((x,y))
                                assert pixels,'no visible green pixels'
                                return {'action':'click','x':sum(p[0] for p in pixels)/len(pixels),
                                        'y':sum(p[1] for p in pixels)/len(pixels)}
                            def authorize(proposal,capture):
                                # Effects are known from OUR fixture source. Pixel
                                # coordinates only locate the approved canvas; they
                                # never decide whether a generic app operation is safe.
                                if capture.pid!=child.pid or capture.window_id!=binding.window_id or tuple(capture.window_frame)!=tuple(binding.window_bounds):
                                    return False
                                rx=proposal['x']*capture.window_frame[2]/capture.viewport['width']
                                ry=proposal['y']*capture.window_frame[3]/capture.viewport['height']
                                return 210<=rx<420 and capture.window_frame[3]-230<ry<capture.window_frame[3]-55
                            report['visual_captures']=captures
                            with capture_provider_calls() as calls:
                                report['provider_calls']=calls
                                if flow_visual:
                                    from tacto.flow import Flow, Step, replay
                                    from tacto.general_visual import NativeVisualScope
                                    from tacto.contract import Envelope
                                    goal='Click once in the center of the green painted area.'
                                    scope=NativeVisualScope(Selector(role='Window',title='NLC Opaque Point Fixture A'),
                                        planner,authorize,verify,frozenset({'write_ephemeral'}),label='owned green opaque canvas')
                                    step=Step('visual',params={'scope':'green','max_screens':2,'timeout_seconds':90},
                                        intent_text=goal,safety=Envelope.minimal().contract_for({'write_ephemeral'},goal))
                                    flow=Flow.from_json(Flow('owned-green',TARGET,[step],schema=2).to_json())
                                    result=replay(public_driver,flow,visual_scopes={'green':scope})
                                    report['flow_result']={'ok':result.ok,'log':result.log,'events':result.events,
                                        'visual_used':result.visual_used,'visual_history':result.visual_history,
                                        'provider_calls':result.provider_calls}
                                    assert result.ok and result.visual_used,report['flow_result']
                                    assert len(result.visual_history)==1 and len(result.events)==1,report['flow_result']
                                    assert result.events[0]['state']=='confirmed' and result.events[0]['visual_host_verified'] is True,report['flow_result']
                                    report['dispatch']=result.visual_history[0]['dispatch']
                                elif general_visual:
                                    from dataclasses import asdict
                                    from tacto.general import run
                                    from tacto.general_visual import NativeVisualScope
                                    from tacto.contract import Envelope
                                    goal='Click once in the center of the green painted area.'
                                    scope=NativeVisualScope(Selector(role='Window',title='NLC Opaque Point Fixture A'),
                                        planner,authorize,verify,frozenset({'write_ephemeral'}),label='owned green opaque canvas')
                                    def no_ax(*args):raise AssertionError('unexpected AX planner fallback')
                                    result=run(public_driver,TARGET,goal,no_ax,visual_scope=scope,
                                        envelope=Envelope(quote=goal,allows=frozenset({'read','write_ephemeral'})),
                                        max_screens=2,timeout_seconds=90)
                                    serialized=asdict(result);serialized['effects']=sorted(result.effects)
                                    report['general_result']=serialized
                                    assert result.ok and result.verified and result.certainty=='confirmed',serialized
                                    assert result.asked==1 and len(result.visual_history)==1,serialized
                                    assert len(result.events)==1 and result.events[0]['visual_host_verified'] is True,serialized
                                    report['dispatch']=result.visual_history[0]['dispatch']
                                else:
                                    result=run_native_visual(public_driver,TARGET,'Click once in the center of the green painted area.',
                                        Selector(role='Window',title='NLC Opaque Point Fixture A'),planner,
                                        authorize,verify,max_steps=1,timeout_seconds=90)
                                    report['visual_result']=result.to_dict()
                                    assert result.state=='DONE',result.to_dict()
                                    assert len(result.history)==1,result.to_dict()
                                    report['dispatch']=result.history[0]['dispatch']
                            report['visual_captures']=captures
                            report['provider_calls']=calls
                        else:
                            report['dispatch']=public_driver.act(view['obs_id'],TARGET,
                                {'name':'click_point','element_index':public_index,'point':{'x':280,'y':120}},
                                risk_class='preapproved',approved=True,intent_text='owned ephemeral green canvas click',
                                expect={'role':'Window','label':'NLC Opaque Point Fixture A','attribute':'Title'})
                    else:
                        report['dispatch']=dispatch_point(binding,deadline=deadline,stopped=lambda:False,context=context)
                    assert report['dispatch']['posted_events']==2
                    time.sleep(.15); report['final']=state()
                    assert 'downs=1 ups=1 greens=1 last=green routeErrors=0' in report['final']['opaque-A-state'],report['final']
                    assert 'downs=0 ups=0 greens=0 last=none routeErrors=0' in report['final']['opaque-B-state'],report['final']
                    report['passed']=True
                finally:
                    if child is not None and child.poll() is None and 'state' in locals():
                        try:report['last_owned_state']=state()
                        except Exception as final_error:report['last_owned_state_error']=type(final_error).__name__
                    if child is not None:
                        if child.poll() is None:
                            child.terminate()
                            try:child.wait(timeout=3)
                            except subprocess.TimeoutExpired:child.kill();child.wait(timeout=3)
                        report['owned_child_reaped']=child.poll() is not None
    except Exception as error:
        report['passed']=False;report['error']=type(error).__name__+':'+str(error)
    report['input_audit']=audit.report()
    report['source_hashes_after']=hashes()
    report['source_stable_during_run']=report['source_hashes_before']==report['source_hashes_after']
    if not report['source_stable_during_run'] or report['input_audit']['status']!='UNCHANGED_SAMPLED':
        report['passed']=False;report.setdefault('error','source/input state changed or inconclusive; cause not attributed')
    with Path(output).open('x') as stream:
        os.chmod(output,0o600);json.dump(report,stream,ensure_ascii=False,indent=2)
    return report
