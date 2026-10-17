"""Own inaccessible first responder receives public window input; no user documents."""
from pathlib import Path
import base64
import hashlib
import json
import os
import plistlib
import subprocess
import tempfile
import time

TARGET = 'ai.callabo.nlc.opaque-input-fixture'
TITLE = 'NLC Opaque Input Fixture '


def main(output, *, public_driver=None, visual_flow=False, vision_provider="fixed", require_selection=False):
    from nlc.driver import Driver, _attr
    from nlc.flow import Selector
    from nlc.input_audit_process import ProcessInputAudit
    from nlc.native_window_binding import capture_binding
    from nlc.native_window_payload import WINDOW_INPUT_CAPABILITIES
    from nlc.protocol import window_input_reobservation
    root = Path(__file__).resolve().parents[2]
    sources = ['tests/fixtures/NativeOpaqueInputFixture.swift','examples/probes/native-opaque-input.py',
        'nlc/driver.py','nlc/helper.py','nlc/native_window_payload.py','nlc/native_key_events.py',
        'nlc/native_window_binding.py','nlc/native_window_dispatch.py','nlc/native_window_ready.py','nlc/native_frontmost.py',
        'nlc/native_process_identity.py','nlc/input_audit.py','nlc/input_audit_process.py',
        'helper/TactoHelper.swift','helper/WindowInput.swift','helper/WindowDispatch.swift',
        'helper/FrontmostProcess.swift','launcher/tacto.swift','nlc/protocol.py',
        'nlc/native_visual.py','nlc/native_visual_actions.py','nlc/general_visual.py','nlc/general.py',
        'nlc/flow.py','nlc/visual_replay.py','nlc/visual.py','nlc/provider_observation.py',
        'nlc/provider_images.py','nlc/provider_result.py','nlc/model_budget.py',
        'nlc/claude_result.py','nlc/codex_result.py','nlc/ask_provider.py',
        'nlc/authoring.py']
    hashes = lambda:{name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in sources}
    if type(visual_flow) is not bool or vision_provider not in ('fixed','codex','claude'):
        raise ValueError('explicit visual Flow and supported provider required')
    if type(require_selection) is not bool:
        raise ValueError('require_selection must be boolean')
    native = Driver(); public_driver = public_driver or native
    report = {'passed':False,'transport':type(public_driver).__name__,'scope':'owned opaque first responder only',
        'source_hashes_before':hashes(),'visual_flow':visual_flow,'vision_provider':vision_provider,'dispatches':[],'user_document_read':False,'foreground_activation':False,'require_selection':require_selection}
    audit = ProcessInputAudit(interval=.005,startup_timeout=10,stop_timeout=3,max_samples=10000,require_selection=require_selection)
    child = None
    try:
        assert WINDOW_INPUT_CAPABILITIES <= set(public_driver.capabilities),'opaque input capability absent'
        assert native._resolve(TARGET,launch=False) is None,'owned fixture already running'
        with tempfile.TemporaryDirectory(prefix='nlc-opaque-input-') as directory:
            bundle=Path(directory)/'OpaqueInput.app';binaries=bundle/'Contents/MacOS';binaries.mkdir(parents=True)
            binary=binaries/'OpaqueInput'
            subprocess.run(['xcrun','swiftc','-parse-as-library','-warnings-as-errors',str(root/sources[0]),'-o',str(binary)],
                check=True,capture_output=True,timeout=45)
            (bundle/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleExecutable':'OpaqueInput',
                'CFBundleIdentifier':TARGET,'CFBundleName':'NLC Opaque Input Fixture','CFBundlePackageType':'APPL'}))
            subprocess.run(['codesign','--force','--sign','-',str(bundle)],check=True,capture_output=True,timeout=15)
            with audit:
                child=subprocess.Popen([str(binary)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                report['fixture_pid']=child.pid
                try:
                    deadline=time.monotonic()+10
                    while native._resolve(TARGET,launch=False) != child.pid:
                        assert child.poll() is None and time.monotonic()<deadline,'fixture did not start'
                        time.sleep(.05)
                    time.sleep(.3)
                    def state():
                        assert native._resolve(TARGET,launch=False)==child.pid,'owned fixture identity changed'
                        view=native.observe(TARGET,'tree_full');assert view.get('type')=='observation',view
                        obs=native._latest[TARGET.lower()]
                        return {str(_attr(el,'AXIdentifier')):str(_attr(el,'AXValue')) for el in obs.elements
                            if _attr(el,'AXIdentifier') in ('opaque-A-state','opaque-B-state')}
                    def view_for(suffix):
                        assert native._resolve(TARGET,launch=False)==child.pid,'owned fixture identity changed'
                        view=public_driver.observe(TARGET,'tree_full');assert view.get('type')=='observation',view
                        index=Selector(role='Window',title=TITLE+suffix).resolve(view['tree'])[0]
                        return view,index
                    report['initial']=state()
                    view,index=view_for('A')
                    prepared=public_driver.act(view['obs_id'],TARGET,{'name':'prepare_window','element_index':index})
                    report['preparation']=prepared
                    assert prepared.get('type')=='acted' and prepared.get('posted_events')==3,prepared
                    view,index=view_for('A')
                    clicked=public_driver.act(view['obs_id'],TARGET,{'name':'click_point','element_index':index,'point':{'x':280,'y':120}},
                        risk_class='preapproved',approved=True,intent_text='owned canvas focus and ephemeral counter')
                    report['focus_click']=clicked
                    assert clicked.get('type')=='acted' and clicked.get('posted_events')==2,clicked
                    time.sleep(.15);before=state();report['before_input']=before
                    assert 'inputReady=true' in before['opaque-A-state'],before
                    assert 'greens=1' in before['opaque-A-state'],before
                    obs=native._latest[TARGET.lower()]
                    own=[el for el in obs.elements if _attr(el,'AXRole')=='AXWindow' and _attr(el,'AXTitle')==TITLE+'A']
                    assert len(own)==1
                    binding=capture_binding(own[0],child.pid,obs.process_launch_identity,point_kind='window_input')
                    report['window_id']=binding.window_id
                    view,index=view_for('A')
                    denied=public_driver.act(view['obs_id'],TARGET,{'name':'window_input','element_index':index,'key':'Right'},
                        risk_class='none',approved=True)
                    report['authorization_refusal']=denied
                    assert denied.get('effect')=='not_started',denied
                    view,index=view_for('B')
                    wrong=public_driver.act(view['obs_id'],TARGET,{'name':'window_input','element_index':index,'key':'Right'},
                        risk_class='preapproved',approved=True,intent_text='owned wrong-window refusal')
                    report['wrong_window_refusal']=wrong
                    assert wrong.get('effect')=='not_started' and wrong.get('posted_or_attempted_events',0)==0,wrong
                    assert state()==before,'a refused operation changed own canvas'
                    if visual_flow:
                        from nlc.general_visual import NativeVisualScope
                        from nlc.flow import Flow, Step, replay
                        from nlc.contract import Envelope
                        from nlc.visual import plan_native_input
                        expected_text='A😀한글'
                        def receiving(capture,history):
                            current=state()
                            count=len(history)
                            expected_text_now=expected_text if count==2 else ''
                            encoded=base64.b64encode(expected_text_now.encode()).decode()
                            return (capture.pid==child.pid and capture.window_id==binding.window_id
                                and f'keyDowns={count} keyUps={count} rights={1 if count else 0} ' in current['opaque-A-state']
                                and 'routeErrors=0' in current['opaque-A-state']
                                and current['opaque-A-state'].endswith('textB64='+encoded)
                                and current['opaque-B-state']==before['opaque-B-state'])
                        def planner(capture,goal,history):
                            if vision_provider=='fixed':
                                return {'action':'key','key':'Right'} if not history else {'action':'text','text':expected_text}
                            return plan_native_input(capture,goal,history,provider=vision_provider,timeout=55)
                        def authorize(proposal,capture):
                            return (capture.pid==child.pid and capture.window_id==binding.window_id
                                and tuple(capture.window_frame)==tuple(binding.window_bounds)
                                and proposal in ({'action':'key','key':'Right'},{'action':'text','text':expected_text})
                                and 'inputReady=true' in state()['opaque-A-state'])
                        goal='Press Right once, then type exactly A😀한글 once into the already prepared opaque canvas. Stop after those two inputs.'
                        scope=NativeVisualScope(Selector(role='Window',title=TITLE+'A'),planner,authorize,
                            lambda c,h:len(h)==2 and receiving(c,h),frozenset({'write_ephemeral'}),
                            verify_step=receiving,input_actions=frozenset({'key','text'}),label='owned opaque keyboard entry')
                        step=Step('visual',params={'scope':'input','max_screens':3,'timeout_seconds':120},
                            intent_text=goal,safety=Envelope.minimal().contract_for({'write_ephemeral'},goal))
                        result=replay(public_driver,Flow.from_json(Flow('opaque-input',TARGET,[step],schema=2).to_json()),
                            visual_scopes={'input':scope})
                        report['flow_result']={'ok':result.ok,'log':result.log,'events':result.events,
                            'visual_history':result.visual_history,'provider_calls':result.provider_calls,'visual_used':result.visual_used}
                        assert result.ok and result.visual_used,report['flow_result']
                        assert len(result.events)==2 and all(e['state']=='confirmed' for e in result.events),report['flow_result']
                        assert len(result.visual_history)==2,report['flow_result']
                        report['final']=state();report['passed']=True
                    else:
                        expected=''
                        payloads=[{'key':'Right'},{'text':'A😀한글'},{'text':'e\u0301👩\u200d💻'},{'text':'😀'*32}]
                        for number,payload in enumerate(payloads,1):
                            view,index=view_for('A')
                            action={'name':'window_input','element_index':index,**payload}
                            reply=public_driver.act(view['obs_id'],TARGET,action,risk_class='preapproved',approved=True,
                                intent_text='owned reversible opaque navigation or Unicode append',
                                expect={'role':'Window','label':TITLE+'A','attribute':'Title'})
                            report['dispatches'].append({'kind':'key' if 'key' in payload else 'text',
                                'text_sha256':hashlib.sha256(payload.get('text','').encode()).hexdigest() if 'text' in payload else None,
                                'reply':reply})
                            assert reply.get('type')=='acted' and reply.get('input_route')=='window_input',reply
                            assert reply.get('mechanism')=='pid_window_events' and reply.get('obs_id')==view['obs_id'],reply
                            assert type(reply.get('posted_events')) is int and reply['posted_events']==2,reply
                            assert reply.get('window_id')==binding.window_id and reply.get('reobserve_required') is True,reply
                            assert window_input_reobservation(action,reply,public_driver.capabilities,
                                obs_id=view['obs_id'],window_id=binding.window_id),reply
                            expected+=payload.get('text','')
                            time.sleep(.15);receiving=state();encoded=base64.b64encode(expected.encode()).decode()
                            assert f'keyDowns={number} keyUps={number} rights=1 ' in receiving['opaque-A-state'],receiving
                            assert 'routeErrors=0' in receiving['opaque-A-state'] and 'textB64='+encoded in receiving['opaque-A-state'],receiving
                            assert receiving['opaque-B-state']==before['opaque-B-state'],receiving
                            report['dispatches'][-1]['receiving_host_verified']=True
                        report['final']=receiving;report['passed']=True
                finally:
                    if child is not None:
                        if child.poll() is None:
                            child.terminate()
                            try:child.wait(timeout=3)
                            except subprocess.TimeoutExpired:child.kill();child.wait(timeout=3)
                        report['owned_child_reaped']=child.poll() is not None
    except Exception as error:
        report['passed']=False;report['error']=type(error).__name__+':'+str(error)
    report['input_audit']=audit.report();report['source_hashes_after']=hashes()
    report['source_stable_during_run']=report['source_hashes_before']==report['source_hashes_after']
    if not report['source_stable_during_run'] or report['input_audit']['status']!='UNCHANGED_SAMPLED':
        report['passed']=False;report.setdefault('error','source/input state changed or inconclusive; cause not attributed')
    with Path(output).open('x') as stream:
        os.chmod(output,0o600);json.dump(report,stream,ensure_ascii=False,indent=2)
    return report
