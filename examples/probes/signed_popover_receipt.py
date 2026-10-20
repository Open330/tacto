"""Owned content receipt after one signed click; explicit preparation, no retries."""
import hashlib
import itertools
import json
import os
from pathlib import Path
import plistlib
import secrets
import subprocess
import tempfile
import time

TARGET='ai.callabo.tacto.popover-receipt-fixture'
MODES=('clear','transient','semitransient')


def callback(data,nonce,mode,first_mouse=True):
    if len(data)>131072:raise ValueError('callback_budget')
    lines=data[:data.rfind(b'\n')+1].splitlines()
    if not lines:return None
    value=json.loads(lines[-1])
    if (type(value) is not dict or set(value)!={'nonce','seq','mode','popover_shown',
        'popover_closes','activations','receivers','window_events','window_keys','popover_window_visible','accepts_first_mouse'} or value['nonce']!=nonce
        or type(value['accepts_first_mouse']) is not bool or value['accepts_first_mouse'] is not first_mouse
        or mode not in MODES or value['mode']!=mode or any(type(value[k]) is not bool for k in ('popover_shown','popover_window_visible'))):
        raise ValueError('callback_identity')
    for key in ('seq','popover_closes','activations'):
        if type(value[key]) is not int or not 0<=value[key]<=10000:raise ValueError('callback_counter')
    if value['seq']<1:raise ValueError('callback_sequence')
    if type(value['window_keys']) is not list or len(value['window_keys'])!=2 or any(type(v) is not bool for v in value['window_keys']):
        raise ValueError('callback_window_keys')
    if type(value['receivers']) is not list or len(value['receivers'])!=2:raise ValueError('callback_receivers')
    for item in value['receivers']:
        if type(item) is not dict or set(item)!={'downs','ups'} or any(type(v) is not int or not 0<=v<=10000 for v in item.values()):
            raise ValueError('callback_receiver_counter')
    if type(value['window_events']) is not list or len(value['window_events'])!=2:raise ValueError('callback_events')
    for events in value['window_events']:
        if type(events) is not list or len(events)>64:raise ValueError('callback_event_budget')
        for event in events:
            if (type(event) is not dict or set(event)!={'phase','window_matches','popover_shown','receiver_hit'}
                or event['phase'] not in ('down','up') or type(event['window_matches']) is not bool
                or type(event['popover_shown']) is not bool or type(event['receiver_hit']) is not bool):raise ValueError('callback_event_shape')
    return value


def received_once(before,after,index):
    if before is None or after is None or after['seq']<=before['seq']:return False
    other=1-index
    return (all(after['receivers'][index][k]-before['receivers'][index][k]==1 for k in ('downs','ups'))
        and after['receivers'][other]==before['receivers'][other]
        and all(e['window_matches'] for events in after['window_events'] for e in events))


def routing_refusal_verified(response,before,after):
    from tacto.preparation_diagnostic import preparation_diagnostic
    if type(response) is not dict or before is None or after is None:return False
    diagnostic=preparation_diagnostic(response.get('preparation_diagnostic'))
    return (response.get('type')=='error' and response.get('effect')=='not_started'
        and type(response.get('posted_or_attempted_events')) is int and response['posted_or_attempted_events']==0
        and diagnostic is not None and diagnostic['reason']=='target_window_not_focused'
        and after['seq']>before['seq'] and after['receivers']==before['receivers']
        and after['window_events']==before['window_events'])


def main(driver,output,*,prepared=False,routing_refusal=False):
    fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    report={'passed':False,'model_requests':0,'all_dependencies_frozen':False,
        'scope':'owned acceptsFirstMouse true/false receivers; target window constructed first at bootstrap; AXFocusedWindow independently checked; one signed window-point action per child',
        'prepared_before_content':prepared,'routing_refusal_measurement':routing_refusal,'cases':[]}
    error=None
    with os.fdopen(fd,'w') as stream:
        try:_run(driver,report,prepared=prepared,routing_refusal=routing_refusal)
        except BaseException as exc:report.update(passed=False,error_class=type(exc).__name__);error=exc
        stream.write(json.dumps(report,indent=2));stream.flush();os.fsync(stream.fileno())
    if error is not None:raise error
    return report


def _run(driver,report,*,prepared=False,routing_refusal=False):
    if prepared and routing_refusal:raise ValueError("incompatible_probe_modes")
    if routing_refusal:
        report['scope']='owned A app-focused, request B; diagnostic validation only; no task success'
    from tacto.helper import InheritedHelperDriver
    if not isinstance(driver,InheritedHelperDriver) or not InheritedHelperDriver.available():raise RuntimeError('signed_helper_required')
    if 'action.click_point' not in driver.capabilities:raise RuntimeError('signed_point_capability_required')
    from tacto.driver import Driver
    from tacto.flow import Selector
    from tacto.native_process_identity import process_start_identity
    from tacto.input_audit_process import ProcessInputAudit
    from ApplicationServices import (AXUIElementCreateApplication,AXUIElementCopyAttributeValue,
        AXUIElementSetMessagingTimeout,AXUIElementGetPid,AXUIElementGetTypeID)
    from CoreFoundation import CFGetTypeID,CFStringGetTypeID
    root=Path(__file__).resolve().parents[2]
    paths=list((root/'tacto').glob('*.py'))+list((root/'helper').glob('*.swift'))+[
        Path(__file__).resolve(),root/'examples/eval.py',root/'tests/fixtures/NativePopoverReceiptFixture.swift',
        root/'launcher/tacto',root/'launcher/tacto.swift',root/'helper/tacto-helper',
        root/'helper/Tacto Helper.app/Contents/MacOS/TactoHelper',root/'helper/Tacto Helper.app/Contents/Info.plist']
    def manifest():return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
    report['source_subset_before']=manifest();host=Driver();host._pump(.05)
    if host._resolve(TARGET,launch=False) is not None:raise RuntimeError('owned_fixture_already_running')
    try:
        with tempfile.TemporaryDirectory(prefix='tacto-popover-receipt-') as directory:
            work=Path(directory);binary=work/'Receipt.app/Contents/MacOS/Receipt';binary.parent.mkdir(parents=True)
            info=binary.parent.parent/'Info.plist';info.write_bytes(plistlib.dumps({'CFBundleIdentifier':TARGET,
                'CFBundleExecutable':'Receipt','CFBundleName':'TACTO Popover Receipt','CFBundlePackageType':'APPL','LSUIElement':True}))
            subprocess.run(['xcrun','swiftc','-O','-warnings-as-errors','-parse-as-library',
                str(root/'tests/fixtures/NativePopoverReceiptFixture.swift'),'-o',str(binary)],capture_output=True,check=True,timeout=60)
            report['fixture_artifact']={'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
                'info_plist_sha256':hashlib.sha256(info.read_bytes()).hexdigest()}
            for mode,index,first_mouse in itertools.product(MODES,(1,) if routing_refusal else (0,1),(True,False)):
                case={'mode':mode,'target_window':'AB'[index],'accepts_first_mouse':first_mouse,'passed':False};report['cases'].append(case)
                nonce=secrets.token_hex(16);fd=os.open(work/f'{mode}-{index}-{first_mouse}.jsonl',os.O_RDWR|os.O_CREAT|os.O_EXCL,0o600)
                child=None;audit=ProcessInputAudit(interval=.005,startup_timeout=10,stop_timeout=3)
                try:
                    with audit:
                        try:
                            child=subprocess.Popen([str(binary),mode,'A' if routing_refusal else 'AB'[index],'yes' if first_mouse else 'no',nonce],stdout=fd,stderr=subprocess.DEVNULL,close_fds=True)
                            start=process_start_identity(child.pid);case['fixture_pid']=child.pid
                            def identity():
                                if child.poll() is not None or process_start_identity(child.pid)!=start or host._resolve(TARGET,launch=False)!=child.pid:
                                    raise RuntimeError('owned_fixture_identity_changed')
                            def state():
                                identity()
                                return callback(os.pread(fd,131073,0),nonce,mode,first_mouse)
                            deadline=time.monotonic()+10;before=None
                            while time.monotonic()<deadline:
                                if child.poll() is not None:raise RuntimeError('owned_fixture_exited')
                                if host._resolve(TARGET,launch=False)==child.pid:
                                    before=state()
                                    if before is not None and (mode=='clear' or (before['popover_shown'] and before['popover_window_visible'])):break
                                time.sleep(.05)
                            case['before']=before
                            if before is None or (mode!='clear' and not (before['popover_shown'] and before['popover_window_visible'])):raise RuntimeError('owned_fixture_not_ready')
                            if any(v for r in before['receivers'] for v in r.values()) or any(before['window_events']):raise RuntimeError('initial_input_present')
                            def attribute(node,name):
                                identity()
                                if AXUIElementSetMessagingTimeout(node,.2)!=0:raise RuntimeError('focus_timeout_set')
                                try:error,value=AXUIElementCopyAttributeValue(node,name,None)
                                finally:
                                    if AXUIElementSetMessagingTimeout(node,0)!=0:raise RuntimeError('focus_timeout_reset')
                                identity()
                                if error!=0 or value is None:raise RuntimeError('focus_attribute_unavailable')
                                return value
                            focused=attribute(AXUIElementCreateApplication(child.pid),'AXFocusedWindow')
                            if CFGetTypeID(focused)!=AXUIElementGetTypeID():raise RuntimeError('focus_window_malformed')
                            owner_error,owner=AXUIElementGetPid(focused,None)
                            if owner_error!=0 or owner!=child.pid:raise RuntimeError('focus_window_owner')
                            title=attribute(focused,'AXTitle')
                            case['owned_app_focused_target']=(CFGetTypeID(title)==CFStringGetTypeID()
                                and str(title)=='TACTO Popover Receipt '+'AB'[index])
                            if case['owned_app_focused_target']==routing_refusal:raise RuntimeError('owned_focus_condition_not_met')
                            observation=driver.observe(TARGET,'tree_full')
                            if observation.get('type')!='observation':raise RuntimeError('observation_unavailable')
                            window,_=Selector(role='Window',title='TACTO Popover Receipt '+'AB'[index]).resolve(observation['tree'])
                            if prepared:
                                case['before_preparation']=state()
                                case['preparation_response']=driver.act(observation['obs_id'],TARGET,
                                    {'name':'prepare_window','element_index':window})
                                if case['preparation_response'].get('type')!='acted':raise RuntimeError('preparation_refused')
                                deadline=time.monotonic()+2
                                while True:
                                    ready=state()
                                    if ready['seq']>case['before_preparation']['seq'] and ready['window_keys'][index]:break
                                    if time.monotonic()>=deadline:raise RuntimeError('preparation_not_received')
                                    time.sleep(.05)
                                case['after_preparation']=ready
                                if any(v for r in ready['receivers'] for v in r.values()):raise RuntimeError('preparation_touched_content')
                                observation=driver.observe(TARGET,'tree_full')
                                if observation.get('type')!='observation':raise RuntimeError('post_preparation_observation_unavailable')
                                window,_=Selector(role='Window',title='TACTO Popover Receipt '+'AB'[index]).resolve(observation['tree'])
                            case['before']=before=state()
                            if not prepared and mode!='clear' and not before['popover_shown']:raise RuntimeError('popover_closed_before_input')
                            # Window-relative top-left coordinates, inside the owned receiver rectangle.
                            case['response']=driver.act(observation['obs_id'],TARGET,
                                {'name':'click_point','element_index':window,'point':{'x':120,'y':150}},
                                risk_class='preapproved',approved=True)
                            deadline=time.monotonic()+2
                            while True:
                                after=state();case['after']=after
                                if (received_once(before,after,index) or (routing_refusal and after['seq']>before['seq'])
                                    or time.monotonic()>=deadline):break
                                time.sleep(.05)
                            events=after['window_events'][index][len(before['window_events'][index]):]
                            case['content_received_once']=received_once(before,after,index)
                            case['direct_content_events']=(len(events)==2 and [e['phase'] for e in events]==['down','up']
                                and all(e['receiver_hit'] for e in events)
                                and after['window_events'][1-index]==before['window_events'][1-index])
                            case['popover_open_at_content_dispatch']=(events[0]['popover_shown'] if events else None)
                            case['popover_consumption_measurement_valid']=(case['direct_content_events']
                                and (mode=='clear' or case['popover_open_at_content_dispatch'] is True))
                            case['passed']=(case['response'].get('type')=='acted' and case['content_received_once']
                                and case['direct_content_events'] and case['response'].get('posted_events')==2)
                            case['task_succeeded']=case['passed']
                            if routing_refusal:
                                case['passed']=routing_refusal_verified(case['response'],before,after)
                        except Exception as exc:case.update(passed=False,error_class=type(exc).__name__)
                        finally:
                            if child is not None:
                                if child.poll() is None:
                                    child.terminate()
                                    try:child.wait(timeout=3)
                                    except subprocess.TimeoutExpired:child.kill();child.wait(timeout=3)
                                case['owned_child_reaped']=child.poll() is not None
                finally:
                    os.close(fd);case['input_audit']=audit.report()
                    if case['input_audit'].get('status')!='UNCHANGED_SAMPLED':case['passed']=False
                deadline=time.monotonic()+3
                while True:
                    host._pump(.05);remaining=host._resolve(TARGET,launch=False)
                    if remaining is None:break
                    if child is None or remaining!=child.pid or time.monotonic()>=deadline:raise RuntimeError('owned_cleanup_unavailable')
    finally:
        report['source_subset_after']=manifest();report['source_subset_stable']=report['source_subset_before']==report['source_subset_after']
    report['passed']=report['source_subset_stable'] and len(report['cases'])==(6 if routing_refusal else 12) and all(c['passed'] for c in report['cases'])
