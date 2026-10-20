"""Actual owned AppKit twelve-stage workflow interrupted by a real sheet.

GUI input uses only the signed inherited helper. The independent AX reader
reads the owned receiving counters, not the person's foreground document.
"""
from pathlib import Path
import hashlib
import json
import os
import plistlib
import subprocess
import tempfile
import time
import sys
import socket

TARGET = 'ai.callabo.tacto.recovery-fixture'


def main(driver, output, *, repeated=False, exhaust_budget=False, interleave_textedit=False,
         interleave_disconnect=False, interleave_parent_change=False):
    from tacto.driver import Driver, _attr
    from tacto.helper import InheritedHelperDriver
    from tacto.flow import Flow, Step, Selector, Postcondition, Resolution, replay
    from tacto.contract import Envelope
    from tacto.input_audit_process import ProcessInputAudit
    from ApplicationServices import AXUIElementCreateApplication
    if type(repeated) is not bool or type(exhaust_budget) is not bool or (exhaust_budget and not repeated):
        raise ValueError('exhaustion requires the repeated recovery fixture')
    if type(interleave_textedit) is not bool or (interleave_textedit and (not repeated or exhaust_budget)):
        raise ValueError('TextEdit interleave requires successful repeated recovery')
    if type(interleave_disconnect) is not bool or (interleave_disconnect and not interleave_textedit):
        raise ValueError('disconnect requires the TextEdit interleave')
    if type(interleave_parent_change) is not bool or (interleave_parent_change and (not interleave_textedit or interleave_disconnect)):
        raise ValueError('parent change requires TextEdit interleave without disconnect')
    stages=24 if repeated else 12
    interrupt_requests=(6,12,18) if repeated else (6,)
    recovery_budget=1 if exhaust_budget or not repeated else 3
    root = Path(__file__).resolve().parents[2]
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    report = {'passed':False, 'model_calls':0, 'target':TARGET,
              'recovery_selection':'scripted_escalate_named_recovery',
              'recovery_budget':recovery_budget,'stages':stages,'expected_budget_exhaustion':exhaust_budget,
              'scope':'owned AppKit custom AXValue workflow and native sheets',
              'all_dependencies_frozen':False}
    report['interleave_textedit_requested'] = interleave_textedit
    report['expected_interleave_disconnect'] = interleave_disconnect
    report['expected_interleave_parent_change'] = interleave_parent_change
    paths = list((root/'tacto').glob('*.py')) + list((root/'helper').glob('*.swift')) + [
        Path(__file__).resolve(), root/'examples/eval.py',
        root/'tests/fixtures/NativeRecoveryFixture.swift', root/'launcher/tacto.swift',
        root/'launcher/tacto', root/'helper/tacto-helper',
        root/'helper/Tacto Helper.app/Contents/MacOS/TactoHelper']
    if interleave_textedit:
        paths += [root/'examples/probes'/name for name in (
            'native_recovery_interleave.py','signed_textedit_save.py','signed_owned_textedit.py','focus_witness.py')]
        paths += [root/'examples/product_comparison.py']
    def manifest():
        return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
    before = None
    audit = process = interrupted = None
    try:
        before = manifest()
        local = Driver()
        audit = ProcessInputAudit(interval=.005, startup_timeout=10, stop_timeout=3)
        assert local._resolve(TARGET, launch=False) is None, 'owned fixture already running'
        assert isinstance(driver,InheritedHelperDriver), 'signed inherited helper required'
        with tempfile.TemporaryDirectory(prefix='tacto-native-recovery-') as work:
            app = Path(work)/'TACTO Recovery Fixture.app'
            binary = app/'Contents/MacOS/NativeRecoveryFixture'
            binary.parent.mkdir(parents=True)
            (app/'Contents/Info.plist').write_bytes(plistlib.dumps({
                'CFBundleIdentifier':TARGET, 'CFBundleExecutable':binary.name,
                'CFBundleName':app.stem, 'CFBundlePackageType':'APPL', 'LSUIElement':True}))
            subprocess.run(['xcrun','swiftc','-parse-as-library',
                            str(root/'tests/fixtures/NativeRecoveryFixture.swift'),'-o',str(binary)],
                           check=True,capture_output=True,timeout=60)
            with audit:
                try:
                    process = subprocess.Popen([str(binary)]+(['--repeated'] if repeated else []),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                    report['owned_pid'] = process.pid
                    deadline = time.monotonic()+15
                    while local._resolve(TARGET,launch=False) != process.pid:
                        assert process.poll() is None and time.monotonic()<deadline
                        time.sleep(.05)
                    time.sleep(.3)
                    def receiving():
                        assert process.poll() is None and local._resolve(TARGET,launch=False) == process.pid
                        stack = [AXUIElementCreateApplication(process.pid)]
                        for _ in range(512):
                            if not stack: break
                            node = stack.pop()
                            if _attr(node,'AXIdentifier') == 'recovery-state':
                                return dict(part.split('=',1) for part in str(_attr(node,'AXValue')).split())
                            stack.extend(_attr(node,'AXChildren') or [])
                        raise AssertionError('owned receiving state absent')
                    def sheet_count():
                        assert process.poll() is None and local._resolve(TARGET,launch=False) == process.pid
                        windows = _attr(AXUIElementCreateApplication(process.pid),'AXWindows') or []
                        main = [w for w in windows if _attr(w,'AXTitle') == 'TACTO Recovery Fixture']
                        assert len(main)==1
                        sheets = []
                        pending = list(_attr(main[0],'AXChildren') or [])
                        for _ in range(128):
                            if not pending: break
                            child = pending.pop()
                            if _attr(child,'AXRole')=='AXSheet':
                                sheets.append(child)
                            else:
                                pending.extend(_attr(child,'AXChildren') or [])
                        report['sheet_observation'] = {
                            'main_role':_attr(main[0],'AXRole'),
                            'main_AXSheets_count':len(_attr(main[0],'AXSheets') or []),
                            'main_descendant_sheet_count':len(sheets)}
                        if sheets:
                            assert len(sheets)==1 and _attr(sheets[0],'AXRole')=='AXSheet'
                            children = list(_attr(sheets[0],'AXChildren') or [])
                            found = False
                            for _ in range(64):
                                if not children: break
                                node = children.pop()
                                if _attr(node,'AXIdentifier')=='owned-sheet-cancel':
                                    found = _attr(node,'AXTitle')=='Cancel'
                                    break
                                children.extend(_attr(node,'AXChildren') or [])
                            assert found, 'owned sheet Cancel identity absent'
                        return len(sheets)
                    report['initial'] = receiving()
                    assert report['initial']['stage']=='0' and report['initial']['active']=='false'
                    env = Envelope.minimal()
                    steps = [Step('set_value',Selector(identifier='workflow-stage'),params={'value':str(n)},
                        safety=env.contract_for({'write_ephemeral'},'owned stage'),
                        postcondition=Postcondition(field=Selector(identifier='workflow-stage'),
                                                    attribute='Value',equals=str(n)),
                        on_uncertain=['postcondition_timeout']) for n in range(1,stages+1)]
                    escalation = []
                    stage_six = []
                    interleave = None
                    if interleave_textedit:
                        sys.path.insert(0,str(root/'examples'))
                        sys.path.insert(0,str(root/'examples/probes'))
                        from native_recovery_interleave import OwnedTextEditInterleave
                        from signed_textedit_save import run as save_owned_textedit
                        from product_comparison import _locked
                        from tacto.native_frontmost import frontmost_pid
                        def can_cleanup():
                            try:
                                return (process.poll() is None and _locked() is False
                                    and frontmost_pid()!=local._resolve('com.apple.TextEdit',launch=False))
                            except Exception:return False
                        def run_child():
                            if not can_cleanup():raise RuntimeError('owned_interleave_environment_changed')
                            child = save_owned_textedit(driver,can_cleanup=can_cleanup,scoped_save=True)
                            if interleave_disconnect:
                                # Fault only this invocation's channel, after the
                                # verified child Save AND owned document cleanup.
                                if (child.get('passed') is not True or child.get('cleanup_status')!='closed'
                                        or child.get('artifact_removed') is not True):
                                    raise RuntimeError('disconnect_child_boundary_unverified')
                                driver._sock.shutdown(socket.SHUT_RDWR)
                                report['helper_channel_shutdown'] = True
                            if interleave_parent_change:
                                if (child.get('passed') is not True or child.get('cleanup_status')!='closed'
                                        or child.get('artifact_removed') is not True):
                                    raise RuntimeError('parent_change_child_boundary_unverified')
                                before_change=receiving()
                                view=driver.observe(TARGET,'tree_full')
                                index,_=Selector(role='Button',title='Other').resolve(view['tree'])
                                response=driver.act(view['obs_id'],TARGET,{'name':'click','element_index':index},
                                    expect={'role':'Button','attribute':'Title','label':'Other','enabled':True},
                                    risk_class='preapproved',approved=True,intent_text='Owned evaluator parent-change fault only')
                                after_change=receiving()
                                report['parent_change_injection']={'before':before_change,'after':after_change,'dispatch':response}
                                assert response.get('type')=='acted' and before_change['otherClicks']=='0' and after_change['otherClicks']=='1'
                                report['parent_change_injected']=True
                            return child
                        interleave = OwnedTextEditInterleave(target=TARGET,driver=driver,
                            receiving=receiving,run_child=run_child)
                        report['interleaved_textedit'] = interleave.rows
                    class WitnessDriver:
                        def __getattr__(self,name): return getattr(driver,name)
                        def observe(self,*args,**kwargs):
                            response = driver.observe(*args,**kwargs)
                            if 'owned-sheet-cancel' in response.get('tree',''):
                                report['interrupt_observation'] = response
                            return response
                        def wait_until(self,*args,**kwargs):
                            response = driver.wait_until(*args,**kwargs)
                            if 'owned-sheet-cancel' in response.get('tree',''):
                                report['interrupt_observation'] = response
                            if interleave is not None:
                                response = interleave.after_wait(args[0],args[1],response)
                            return response
                        def act(self,obs,target,action,**kwargs):
                            requested=int(action['value']) if action.get('name')=='set_value' else None
                            monitored = requested in interrupt_requests
                            if monitored:
                                deadline = time.monotonic()+3
                                prior=[attempt for attempt in stage_six if attempt['requested']==requested]
                                expected = 1 if not prior else 0
                                while sheet_count()!=expected:
                                    assert time.monotonic()<deadline, 'owned sheet readiness timed out'
                                    time.sleep(.05)
                                before = {'state':receiving(),'sheet_count':sheet_count()}
                                if not prior:
                                    refusal = driver.act(obs,target,action,expect={'no_modal':True})
                                    report['modal_guard'] = {'response':refusal,
                                        'before':before['state'],'after':receiving()}
                                    assert refusal.get('type')=='error' and refusal.get('effect')=='not_started'
                                    assert report['modal_guard']['after']==before['state']
                                else:
                                    kwargs['expect'] = {**(kwargs.get('expect') or {}),'no_modal':True}
                            response = driver.act(obs,target,action,**kwargs)
                            if monitored:
                                if prior:
                                    report['modal_guard_clear_response'] = response
                                stage_six.append({'requested':requested,'before':before,'after':receiving()})
                            return response
                    def resolve(why):
                        escalation.append({'kind':why.kind,'step':why.step,'effect':why.effect})
                        return Resolution(kind='recover',recovery='dismiss_dialog')
                    result = replay(WitnessDriver(),Flow('owned_twelve_stage_recovery',TARGET,steps,schema=2),
                                    escalate=resolve,budget=recovery_budget)
                    report['replay'] = {'ok':result.ok,'log':result.log,'events':result.events,
                                        'receipt_count':len(result.commit_evidence)}
                    report['escalations'] = escalation
                    report['stage_six_attempts'] = stage_six
                    report['final'] = receiving()
                    if interleave_disconnect:
                        assert not result.ok and report.get('helper_channel_shutdown') is True
                        assert getattr(driver,'_poisoned',False) is True
                        report['helper_channel_poisoned'] = True
                        assert len(interleave.rows)==1 and interleave.rows[0]['parent_stage']==8
                        assert interleave.rows[0]['phase']=='failed'
                        assert {k:report['final'][k] for k in ('stage','accepted','blockedWrites','cancels','sheet','otherClicks')} == {
                            'stage':'8','accepted':'8','blockedWrites':'1','cancels':'1','sheet':'false','otherClicks':'0'}
                        assert len(result.events)==10 and len({e['event_id'] for e in result.events})==10
                        assert escalation==[{'kind':'postcondition_timeout','step':5,'effect':'unknown'}]
                        time.sleep(.2)
                        report['after_stop']=receiving()
                        assert report['after_stop']==report['final']
                        report['expected_stop_verified'] = True
                    elif interleave_parent_change:
                        assert not result.ok and report.get('parent_change_injected') is True
                        assert len(interleave.rows)==1 and interleave.rows[0]['parent_stage']==8 and interleave.rows[0]['phase']=='failed'
                        assert interleave.rows[0]['after']==report['final']
                        assert {k:report['final'][k] for k in ('stage','accepted','blockedWrites','cancels','sheet','otherClicks')} == {
                            'stage':'8','accepted':'8','blockedWrites':'1','cancels':'1','sheet':'false','otherClicks':'1'}
                        assert len(result.events)==10 and len({e['event_id'] for e in result.events})==10
                        assert not getattr(driver,'_poisoned',False)
                        report['helper_channel_poisoned']=False
                        time.sleep(.2);report['after_stop']=receiving()
                        assert report['after_stop']==report['final']
                        report['expected_stop_verified']=True
                    elif exhaust_budget:
                        assert not result.ok
                        terminal=result.log[-1]
                        assert {k:terminal.get(k) for k in ('status','step','action','error','effect')} == {
                            'status':'error','step':11,'action':'set_value',
                            'error':'postcondition_timeout','effect':'unknown'}
                        assert len(result.events)==14 and len({e['event_id'] for e in result.events})==14
                        assert report['final']['stage']=='11' and report['final']['accepted']=='11'
                        assert report['final']['blockedWrites']=='2' and report['final']['cancels']=='1'
                        assert report['final']['sheet']=='true' and report['final']['otherClicks']=='0'
                        assert len(stage_six)==3 and [a['requested'] for a in stage_six]==[6,6,12]
                        assert len(escalation)==1 and escalation[0]['step']==5
                        assert sheet_count()==1
                        report['expected_stop_verified']=True
                    else:
                        rounds=len(interrupt_requests)
                        assert result.ok
                        assert escalation == [{'kind':'postcondition_timeout','step':n-1,'effect':'unknown'}
                            for n in interrupt_requests]
                        assert {k:report['final'][k] for k in ('stage','accepted','blockedWrites','cancels','sheet','stageSixRequests','otherClicks')} == {
                            'stage':str(stages),'accepted':str(stages),'blockedWrites':str(rounds),
                            'cancels':str(rounds),'sheet':'false','stageSixRequests':'2','otherClicks':'0'}
                        assert len(stage_six)==2*rounds
                        for i,n in enumerate(interrupt_requests):
                            refused,accepted=stage_six[2*i:2*i+2]
                            assert refused['requested']==accepted['requested']==n
                            assert refused['after']['stage']==str(n-1)
                            assert refused['after']['blockedWrites']==str(i+1)
                            assert accepted['after']['stage']==str(n) and accepted['after']['accepted']==str(n)
                        assert sheet_count()==0
                        assert report['modal_guard_clear_response'].get('type')=='acted'
                        recovery = [e for e in result.events if e.get('recovery')=='dismiss_dialog']
                        assert len(recovery)==rounds and [e['parent_step'] for e in recovery]==[n-1 for n in interrupt_requests]
                        assert len(result.events)==stages+2*rounds
                        assert len({e['event_id'] for e in result.events})==len(result.events)
                    report['parent_task_oracle_passed'] = not exhaust_budget and not interleave_disconnect and not interleave_parent_change
                    if interleave is not None and not interleave_disconnect and not interleave_parent_change:
                        assert [row['parent_stage'] for row in interleave.rows] == [8,14,20]
                        assert all(row['phase']=='completed' for row in interleave.rows)
                        report['multi_app_saved_state_verified'] = True
                    report['task_oracle_passed'] = not exhaust_budget and not interleave_disconnect and not interleave_parent_change
                    report['passed'] = True
                finally:
                    if process is not None:
                        if process.poll() is None: process.terminate()
                        process.wait(timeout=5)
                        report['owned_child_reaped'] = True
    except BaseException as exc:
        report.update(passed=False,error_class=type(exc).__name__)
        if not isinstance(exc,Exception):
            interrupted=exc
            report['interrupted']=True
    finally:
        report['input_audit'] = audit.report() if audit is not None else {'status':'NOT_STARTED'}
        if report['input_audit']['status'] != 'UNCHANGED_SAMPLED': report['passed']=False
        report['classification'] = ('task_completed_input_state_unverified'
            if report.get('task_oracle_passed') and not report['passed'] else
            'passed' if report['passed'] else 'failed')
        after=manifest()
        report.update(source_subset_before=before,source_subset_after=after,
                      source_subset_stable=before==after)
        if before!=after: report['passed']=False
        with os.fdopen(fd,'w') as stream: json.dump(report,stream,ensure_ascii=False,indent=2)
    if interrupted is not None: raise interrupted
    return report
