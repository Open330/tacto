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
from contextlib import ExitStack

TARGET = 'ai.callabo.nlc.opaque-input-fixture'
TITLE = 'NLC Opaque Input Fixture '


def main(output, *, public_driver=None, visual_flow=False, vision_provider="fixed", require_selection=False, journal_barrier=False, general_visual=False):
    if type(visual_flow) is not bool or vision_provider not in ('fixed','codex','claude'):
        raise ValueError('explicit visual Flow and supported provider required')
    if type(require_selection) is not bool:
        raise ValueError('require_selection must be boolean')
    if type(journal_barrier) is not bool or (journal_barrier and (not visual_flow or vision_provider!='fixed')):
        raise ValueError('journal probe requires fixed visual flow')
    if type(general_visual) is not bool or (general_visual and (not visual_flow or journal_barrier)):
        raise ValueError('general visual input requires explicit visual mode without a journal')
    # Reserve private evidence before any fixture launch or UI input.
    descriptor=os.open(output,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(descriptor,'w') as stream:
        try:
            return _main(stream,public_driver=public_driver,visual_flow=visual_flow,
                         vision_provider=vision_provider,require_selection=require_selection,journal_barrier=journal_barrier,general_visual=general_visual)
        except BaseException as error:
            if stream.tell()==0:
                json.dump({'passed':False,'error_class':type(error).__name__},stream)
            raise


def _main(stream, *, public_driver, visual_flow, vision_provider, require_selection, journal_barrier, general_visual):
    from tacto.driver import Driver, _attr
    from tacto.flow import Selector
    from tacto.input_audit_process import ProcessInputAudit
    from tacto.native_window_binding import capture_binding
    from tacto.native_window_payload import WINDOW_INPUT_CAPABILITIES
    from tacto.protocol import window_input_reobservation
    root = Path(__file__).resolve().parents[2]
    sources = ['tests/fixtures/NativeOpaqueInputFixture.swift','examples/probes/native-opaque-input.py',
        'tacto/driver.py','tacto/helper.py','tacto/native_window_payload.py','tacto/native_key_events.py',
        'tacto/native_window_binding.py','tacto/native_window_dispatch.py','tacto/native_window_ready.py','tacto/native_frontmost.py',
        'tacto/native_process_identity.py','tacto/input_audit.py','tacto/input_audit_process.py',
        'helper/TactoHelper.swift','helper/WindowInput.swift','helper/WindowDispatch.swift',
        'helper/FrontmostProcess.swift','launcher/tacto.swift','tacto/protocol.py',
        'tacto/native_visual.py','tacto/native_visual_actions.py','tacto/general_visual.py','tacto/general.py',
        'tacto/flow.py','tacto/visual_replay.py','tacto/visual.py','tacto/provider_observation.py',
        'tacto/provider_images.py','tacto/provider_result.py','tacto/model_budget.py',
        'tacto/claude_result.py','tacto/codex_result.py','tacto/ask_provider.py',
        'tacto/authoring.py','tacto/provider_io.py','tacto/dispatch_journal.py','launcher/tacto','helper/tacto-helper',
        'helper/Tacto Helper.app/Contents/MacOS/TactoHelper']
    hashes = lambda:{name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in sources}
    native = Driver(); public_driver = public_driver or native
    report = {'passed':False,'transport':type(public_driver).__name__,'scope':'owned opaque first responder only',
        'comparison_eligible':False,'journal_barrier':journal_barrier,
        'source_hashes_before':hashes(),'visual_flow':visual_flow and not general_visual,'general_visual':general_visual,'vision_provider':vision_provider,'dispatches':[],'user_document_read':False,'foreground_activation':False,'require_selection':require_selection}
    audit = ProcessInputAudit(interval=.005,startup_timeout=10,stop_timeout=3,max_samples=10000,require_selection=require_selection)
    child = None
    try:
        assert WINDOW_INPUT_CAPABILITIES <= set(public_driver.capabilities),'opaque input capability absent'
        assert native._resolve(TARGET,launch=False) is None,'owned fixture already running'
        with tempfile.TemporaryDirectory(prefix='nlc-opaque-input-') as directory, ExitStack() as resources:
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
                        from tacto.general_visual import NativeVisualScope
                        from tacto.flow import Flow, Step, replay
                        from tacto.contract import Envelope
                        from tacto.visual import plan_native_input
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
                        flow_driver=public_driver
                        if journal_barrier:
                            from tacto.dispatch_journal import DurableDispatchDriver
                            journal=Path(directory)/'journal'
                            flow_driver=resources.enter_context(DurableDispatchDriver(public_driver,journal))
                        if general_visual:
                            from tacto.general import run
                            def no_ax(*args):raise AssertionError('unexpected AX planner fallback')
                            result=run(public_driver,TARGET,goal,no_ax,visual_scope=scope,
                                envelope=Envelope(quote=goal,allows=frozenset({'read','write_ephemeral'})),
                                max_screens=3,timeout_seconds=120)
                            report['general_result']={'ok':result.ok,'verified':result.verified,
                                'certainty':result.certainty,'asked':result.asked,'events':result.events,
                                'visual_history':result.visual_history,'provider_calls':result.provider_calls}
                            assert result.verified and result.certainty=='confirmed' and result.asked==2,report['general_result']
                        else:
                            result=replay(flow_driver,Flow.from_json(Flow('opaque-input',TARGET,[step],schema=2).to_json()),
                                visual_scopes={'input':scope})
                            report['flow_result']={'ok':result.ok,'log':result.log,'events':result.events,
                                'visual_history':result.visual_history,'provider_calls':result.provider_calls,'visual_used':result.visual_used}
                        if journal_barrier:
                            assert not result.ok and result.visual_used,report['flow_result']
                            assert len(result.events)==1 and len(result.visual_history)==2,report['flow_result']
                            assert result.visual_history[-1]['reason']=='dispatch_journal_unavailable',report['flow_result']
                            current=state()
                            assert 'keyDowns=1 keyUps=1 rights=1 ' in current['opaque-A-state'],current
                            assert current['opaque-A-state'].endswith('textB64=') and current['opaque-B-state']==before['opaque-B-state'],current
                            flow_driver.close_journal()
                            class NoInput:
                                calls=0
                                def act(self,*args,**kwargs):
                                    self.calls+=1;raise AssertionError('restart must refuse before underlying input')
                            sentinel=NoInput()
                            with DurableDispatchDriver(sentinel,journal) as restarted:
                                refusal=restarted.act('fresh',TARGET,{'name':'window_input','element_index':index,'key':'Right'})
                            assert refusal.get('error')=='dispatch_journal_requires_reconciliation' and sentinel.calls==0,refusal
                            assert state()==current,'restart changed receiver'
                            records=[json.loads(line) for line in (journal/'00000.jsonl').read_text().splitlines()]
                            assert len(records)==2 and records[1]['category']=='unknown',records
                            report['journal_evidence']={'records':records,'restart_reply':refusal,
                                'underlying_restart_input_calls':sentinel.calls,'worker_process_restarted':False}
                        else:
                            summary=report['general_result'] if general_visual else report['flow_result']
                            assert result.ok and (general_visual or result.visual_used),summary
                            assert len(result.events)==2 and all(e['state']=='confirmed' for e in result.events),summary
                            assert len(result.visual_history)==2,summary
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
    report['input_audit']=audit.report();report['source_hashes_after']=hashes()
    report['source_stable_during_run']=report['source_hashes_before']==report['source_hashes_after']
    if not report['source_stable_during_run'] or report['input_audit']['status']!='UNCHANGED_SAMPLED':
        report['passed']=False;report.setdefault('error','source/input state changed or inconclusive; cause not attributed')
    json.dump(report,stream,ensure_ascii=False,indent=2)
    return report
