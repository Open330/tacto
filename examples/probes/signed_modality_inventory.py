"""Compare full signed traversal and bounded owned-app modality evidence.

Synthetic deep AXSheet is a representation counterexample, not proof of input
interception. No key, click, setter, menu or global activation is sent.
"""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import secrets
import subprocess
import tempfile
import time

TARGET='ai.callabo.nlc.modality-fixture'
MODES=('clear','window_sheet','deep_sheet','app_modal','panel','popover_transient','popover_semitransient','nested_sheet')
STATE_FLAGS=('appkit_sheet_attached','appkit_modal_window','custom_sheet_is_synthetic',
             'panel_visible','popover_shown','nested_sheet_attached')

def requested_state(state, mode):
    flag={'window_sheet':'appkit_sheet_attached','deep_sheet':'custom_sheet_is_synthetic',
          'app_modal':'appkit_modal_window','panel':'panel_visible',
          'popover_transient':'popover_shown','popover_semitransient':'popover_shown',
          'nested_sheet':'nested_sheet_attached'}.get(mode)
    return mode in MODES and state is not None and (flag is None or state[flag] is True)


def callback(data, nonce, mode):
    if len(data)>131072:raise ValueError('callback_budget')
    lines=data[:data.rfind(b'\n')+1].splitlines()
    if not lines:return None
    value=json.loads(lines[-1])
    if (type(value) is not dict or set(value)!=set(('nonce','seq','mode')+STATE_FLAGS) or value['nonce']!=nonce
            or mode not in MODES or value['mode']!=mode or type(value['seq']) is not int or not 1<=value['seq']<=10000
            or any(type(value[k]) is not bool for k in STATE_FLAGS)):
        raise ValueError('callback_identity_or_shape')
    return value


def main(driver, output):
    fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    report={'passed':False,'kind':'owned read-only modality comparison','model_requests':0,
            'all_dependencies_frozen':False,'input_interception_verified':False,'cases':[]}
    interrupted=None
    with os.fdopen(fd,'w') as stream:
        try:_run(driver,report)
        except BaseException as error:
            report.update(passed=False,error_class=type(error).__name__);interrupted=error
        stream.write(json.dumps(report,ensure_ascii=False,indent=2));stream.flush();os.fsync(stream.fileno())
    if interrupted is not None:raise interrupted
    return report


def _run(driver,report):
    from tacto.helper import InheritedHelperDriver
    if not isinstance(driver,InheritedHelperDriver) or not InheritedHelperDriver.available():
        raise RuntimeError('signed_helper_required')
    if 'diagnostic.readiness_preflight' not in driver.capabilities:
        raise RuntimeError('preflight_capability_unavailable')
    from tacto.driver import Driver
    from tacto.flow import Selector
    from tacto.modal_presence import modal_presence
    from tacto.input_audit_process import ProcessInputAudit
    from tacto.native_process_identity import process_start_identity
    from examples.probes.signed_readiness_scale import native_elements
    from ApplicationServices import (AXUIElementCreateApplication,AXUIElementCopyAttributeValue,
        AXUIElementSetMessagingTimeout,AXUIElementGetTypeID)
    from CoreFoundation import CFGetTypeID,CFArrayGetTypeID,CFStringGetTypeID,CFBooleanGetTypeID,CFEqual
    root=Path(__file__).resolve().parents[2]
    paths=list((root/'tacto').glob('*.py'))+list((root/'nlc').glob('*.py'))+list((root/'helper').glob('*.swift'))+[
        Path(__file__).resolve(),root/'examples/probes/signed_readiness_scale.py',root/'examples/eval.py',
        root/'tests/fixtures/NativeModalityFixture.swift',root/'launcher/tacto',root/'launcher/tacto.swift',
        root/'helper/tacto-helper',root/'helper/Tacto Helper.app/Contents/MacOS/TactoHelper',
        root/'helper/Tacto Helper.app/Contents/Info.plist',root/'helper/build.sh',root/'helper/bundle.sh',root/'signing.sh']
    def manifest():return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
    report['source_subset_before']=manifest();host=Driver()
    host._pump(.05)
    if host._resolve(TARGET,launch=False) is not None:raise RuntimeError('owned_fixture_already_running')
    try:
        with tempfile.TemporaryDirectory(prefix='nlc-modality-') as directory:
            work=Path(directory);binary=work/'Modality.app/Contents/MacOS/Modality';binary.parent.mkdir(parents=True)
            info=binary.parent.parent/'Info.plist'
            info.write_bytes(plistlib.dumps({'CFBundleIdentifier':TARGET,'CFBundleExecutable':'Modality',
                'CFBundlePackageType':'APPL','CFBundleName':'NLC Modality','LSUIElement':True}))
            subprocess.run(['xcrun','swiftc','-O','-warnings-as-errors','-parse-as-library',
                str(root/'tests/fixtures/NativeModalityFixture.swift'),'-o',str(binary)],capture_output=True,check=True,timeout=60)
            report['fixture_artifact']={'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
                'info_plist_sha256':hashlib.sha256(info.read_bytes()).hexdigest()}
            for mode in MODES:
                case={'mode':mode,'passed':False};report['cases'].append(case)
                nonce=secrets.token_hex(16);channel=os.open(work/(mode+'.jsonl'),os.O_RDWR|os.O_CREAT|os.O_EXCL,0o600)
                child=None;audit=ProcessInputAudit(interval=.005,startup_timeout=10,stop_timeout=3)
                try:
                    with audit:
                        try:
                            child=subprocess.Popen([str(binary),mode,nonce],stdout=channel,stderr=subprocess.DEVNULL,close_fds=True)
                            start=process_start_identity(child.pid);case.update(fixture_pid=child.pid,process_start=start)
                            def identity():
                                if child.poll() is not None or process_start_identity(child.pid)!=start or host._resolve(TARGET,launch=False)!=child.pid:
                                    raise RuntimeError('owned_fixture_identity_changed')
                            state=None;deadline=time.monotonic()+10
                            while time.monotonic()<deadline:
                                if child.poll() is not None:raise RuntimeError('owned_fixture_exited')
                                if host._resolve(TARGET,launch=False)==child.pid:
                                    identity();state=callback(os.pread(channel,131073,0),nonce,mode)
                                    if requested_state(state,mode):break
                                time.sleep(.05)
                            if not requested_state(state,mode):
                                case['fixture_state_before']=state
                                raise RuntimeError('owned_fixture_requested_state_not_ready')
                            case['fixture_state_before']=state
                            observation=driver.observe(TARGET,'tree_full')
                            if observation.get('type')!='observation':raise RuntimeError('owned_observation_unavailable')
                            case['deep_sheet_role_seen']=any(len(line.split())>1 and line.split()[1]=='Sheet' for line in observation['tree'].splitlines())
                            index,_=Selector(role='Window',title='NLC Modality A').resolve(observation['tree'])
                            deadline=time.monotonic()+3;read_count=0
                            def read(node,name):
                                nonlocal read_count
                                identity();read_count+=1
                                if read_count>1000 or time.monotonic()>=deadline:raise RuntimeError('boundary_read_budget')
                                if AXUIElementSetMessagingTimeout(node,.2)!=0:raise RuntimeError('boundary_timeout_set')
                                try:error,value=AXUIElementCopyAttributeValue(node,name,None)
                                finally:
                                    if AXUIElementSetMessagingTimeout(node,0)!=0:raise RuntimeError('boundary_timeout_reset')
                                if value is not None:
                                    kind=CFGetTypeID(value)
                                    if kind==CFArrayGetTypeID():value=native_elements(value)
                                    elif kind==CFStringGetTypeID():value=str(value)
                                    elif kind==CFBooleanGetTypeID():value=bool(value)
                                return int(error),value
                            boundary=modal_presence(AXUIElementCreateApplication(child.pid),read,
                                is_element=lambda v:v is not None and CFGetTypeID(v)==AXUIElementGetTypeID(),same_element=CFEqual)
                            case['boundary']=boundary;case['boundary_ax_reads']=read_count
                            full=driver._send({'type':'readiness_preflight','target':TARGET,'obs_id':observation['obs_id'],'element_index':index})
                            case['full']=full
                            identity();after=callback(os.pread(channel,131073,0),nonce,mode);case['fixture_state_after']=after
                            case['verdicts_differ']=boundary=='clear' and full.get('type')!='readiness_preflight'
                            # Comparison is of guard predicates, not proof of positively observed modality.
                            expected_cause={'window_sheet':{'sheet_or_malformed_role','sheet_or_malformed_list'},
                                'deep_sheet':{'sheet_or_malformed_role'},
                                'app_modal':{'modal_or_unknown_top','modal_or_unknown_element'},
                                'nested_sheet':{'sheet_or_malformed_role','sheet_or_malformed_list'}}.get(mode)
                            case['passed']=(full.get('read_only') is True and full.get('authorizes_input') is False
                                and ((full.get('type')=='readiness_preflight' and full.get('reason')=='read_complete')
                                     or (full.get('type')=='error' and full.get('reason')=='preflight_unavailable'))
                                and boundary in ('clear','present') and after is not None
                                and (mode!='clear' or (boundary=='clear' and full.get('type')=='readiness_preflight'))
                                and (mode not in ('window_sheet','deep_sheet','app_modal','nested_sheet') or full.get('type')=='error')
                                and (mode!='window_sheet' or boundary=='present')
                                and (mode!='deep_sheet' or case['deep_sheet_role_seen'])
                                and requested_state(after,mode) and after['seq']>=state['seq']
                                and (expected_cause is None or full.get('refusal_cause') in expected_cause))
                        except Exception as error:case.update(passed=False,error_class=type(error).__name__)
                        finally:
                            if child is not None:
                                if child.poll() is None:
                                    child.terminate()
                                    try:child.wait(timeout=3)
                                    except subprocess.TimeoutExpired:child.kill();child.wait(timeout=3)
                                case['owned_child_reaped']=child.poll() is not None
                finally:
                    os.close(channel);case['input_audit']=audit.report()
                    if case['input_audit'].get('status')!='UNCHANGED_SAMPLED':case['passed']=False
                deadline=time.monotonic()+3
                while True:
                    host._pump(.05);pid=host._resolve(TARGET,launch=False)
                    if pid is None:break
                    if child is None or pid!=child.pid or time.monotonic()>=deadline:raise RuntimeError('owned_cleanup_unavailable')
    finally:
        report['source_subset_after']=manifest();report['source_subset_stable']=report['source_subset_before']==report['source_subset_after']
    report['passed']=report['source_subset_stable'] and len(report['cases'])==len(MODES) and all(c['passed'] for c in report['cases'])
