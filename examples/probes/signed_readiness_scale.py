"""Actual signed preparation over owned 0/512/6000-leaf AX trees.

A has a small tree; only B grows. Refusals stay failed tasks. No user windows
are closed, no model is called, and callback state independently records receipt.
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

TARGET = 'ai.callabo.nlc.readiness-scale-fixture'
COUNTS = (0, 512, 6000)


def native_elements(value):
    from CoreFoundation import CFGetTypeID, CFArrayGetTypeID
    from ApplicationServices import AXUIElementGetTypeID
    if value is None or CFGetTypeID(value) != CFArrayGetTypeID() or len(value)>6000:
        raise ValueError('host_array_malformed')
    result=list(value)
    if any(CFGetTypeID(node)!=AXUIElementGetTypeID() for node in result):
        raise ValueError('host_array_child_malformed')
    return result


def receiving_state(data, nonce, count):
    if len(data) > 131072:
        raise ValueError('receiving_channel_budget')
    lines = data[:data.rfind(b'\n')+1].splitlines()
    if not lines:
        return None
    value = json.loads(lines[-1])
    keys = {'nonce','seq','leaf_count','activations','A_key','B_key','A_mouse_downs',
            'B_mouse_downs','route_errors'}
    if type(value) is not dict or set(value) != keys or value['nonce'] != nonce:
        raise ValueError('receiving_channel_identity')
    for key in ('seq','leaf_count','activations','A_mouse_downs','B_mouse_downs','route_errors'):
        if type(value[key]) is not int or not 0 <= value[key] <= 10000:
            raise ValueError('receiving_channel_shape')
    if value['seq']<1 or value['leaf_count'] != count or any(type(value[k]) is not bool for k in ('A_key','B_key')):
        raise ValueError('receiving_channel_shape')
    return value


def _source_manifest(root):
    paths=list((root/'tacto').glob('*.py'))+list((root/'nlc').glob('*.py'))+list((root/'helper').glob('*.swift'))+[
        Path(__file__).resolve(),root/'tests/fixtures/NativeReadinessScaleFixture.swift',
        root/'examples/eval.py',root/'launcher/tacto',root/'launcher/tacto.swift',
        root/'helper/tacto-helper',root/'helper/Tacto Helper.app/Contents/MacOS/TactoHelper',
        root/'helper/Tacto Helper.app/Contents/Info.plist',root/'helper/build.sh',
        root/'helper/bundle.sh',root/'signing.sh']
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def preparation_received(current, prior):
    return (type(current) is dict and type(prior) is dict and current['seq']>prior['seq']
            and current['A_key'] and not current['B_key'] and current['route_errors']==0
            and current['B_mouse_downs']==prior['B_mouse_downs'])


def wait_preparation_state(read, prior, *, timeout=2, clock=time.monotonic, sleep=time.sleep):
    """Read the original callback only; activation alone is not the key receipt.

    Contradictory routing is terminal even if a later callback might look right.
    This never reissues preparation or hides a sequence regression.
    """
    deadline=clock()+timeout
    latest=None
    while True:
        latest=read()
        if latest is not None:
            if latest['seq']<prior['seq']:raise ValueError('receiving_sequence_regressed')
            if (latest['route_errors'] or latest['B_key']
                    or latest['B_mouse_downs']>prior['B_mouse_downs']):return latest
            if latest['seq']>prior['seq'] and latest['A_key']:return latest
        remaining=deadline-clock()
        if remaining<=0:return latest
        sleep(min(.02,remaining))


def main(driver, output, *, audit_strategy='application_frontmost'):
    if type(audit_strategy) is not str or audit_strategy not in ('application_frontmost','systemwide'):
        raise ValueError('invalid audit strategy')
    descriptor=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    report={'passed':False,'model_requests':0,'all_dependencies_frozen':False,
            'scope':'owned custom AX hierarchy; only B grows; sampled input audit',
            'requested_audit_strategy':audit_strategy,'input_preservation_verified':False,'cases':[]}
    interrupted=None
    with os.fdopen(descriptor,'w') as stream:
        try:
            _run(driver,report,audit_strategy=audit_strategy)
        except BaseException as error:
            report.update(passed=False,error_class=type(error).__name__,effect='unknown')
            interrupted=error
        stream.write(json.dumps(report,ensure_ascii=False,indent=2));stream.flush();os.fsync(stream.fileno())
    if interrupted is not None:
        raise interrupted
    return report


def _run(driver, report, *, audit_strategy='application_frontmost'):
    from tacto.helper import InheritedHelperDriver
    if not isinstance(driver,InheritedHelperDriver) or not InheritedHelperDriver.available():
        raise RuntimeError('signed_helper_required')
    from tacto.driver import Driver
    from tacto.flow import Selector
    from tacto.input_audit_process import ProcessInputAudit
    from tacto.preparation_diagnostic import preparation_diagnostic
    from tacto.native_process_identity import process_start_identity
    from ApplicationServices import (AXUIElementCreateApplication, AXUIElementCopyAttributeValue,
                                     AXUIElementSetMessagingTimeout)
    root=Path(__file__).resolve().parents[2]
    source_before=_source_manifest(root);report['source_subset_before']=source_before
    report['manifest_scope']='listed checkout sources/runtime/fixture; excludes dependencies/compiler/OS'
    host=Driver()
    if host._resolve(TARGET,launch=False) is not None:
        raise RuntimeError('owned_fixture_already_running')
    try:
        with tempfile.TemporaryDirectory(prefix='nlc-readiness-scale-') as directory:
            work=Path(directory);bundle=work/'Scale.app';binary=bundle/'Contents/MacOS/Scale'
            binary.parent.mkdir(parents=True)
            info=bundle/'Contents/Info.plist'
            info.write_bytes(plistlib.dumps({'CFBundleIdentifier':TARGET,'CFBundleExecutable':'Scale',
                'CFBundleName':'NLC Readiness Scale','CFBundlePackageType':'APPL','LSUIElement':True}))
            subprocess.run(['xcrun','swiftc','-O','-warnings-as-errors','-parse-as-library',
                str(root/'tests/fixtures/NativeReadinessScaleFixture.swift'),'-o',str(binary)],
                capture_output=True,check=True,timeout=60)
            report['fixture_artifact']={'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
                                        'info_plist_sha256':hashlib.sha256(info.read_bytes()).hexdigest()}
            for count in COUNTS:
                case={'requested_B_leaves':count,'passed':False};report['cases'].append(case)
                nonce=secrets.token_hex(16)
                log_path=work/f'callback-{count}.jsonl'
                fd=os.open(log_path,os.O_RDWR|os.O_CREAT|os.O_EXCL,0o600)
                child=None
                audit=ProcessInputAudit(strategy=audit_strategy,interval=.005,startup_timeout=10,stop_timeout=3)
                try:
                    with audit:
                        try:
                            child=subprocess.Popen([str(binary),str(count),nonce],stdout=fd,
                                stderr=subprocess.DEVNULL,close_fds=True)
                            start=process_start_identity(child.pid);case['fixture_pid']=child.pid
                            def identity():
                                if (child.poll() is not None or process_start_identity(child.pid)!=start
                                        or host._resolve(TARGET,launch=False)!=child.pid):
                                    raise RuntimeError('owned_fixture_identity_changed')
                            def state():
                                identity()
                                return receiving_state(os.pread(fd,131073,0),nonce,count)
                            deadline=time.monotonic()+10
                            initial=None
                            while time.monotonic()<deadline:
                                if child.poll() is not None:raise RuntimeError('owned_fixture_exited')
                                if host._resolve(TARGET,launch=False)==child.pid:
                                    initial=state()
                                    if initial is not None:break
                                time.sleep(.05)
                            if initial is None:raise RuntimeError('owned_fixture_not_ready')
                            case['initial']=initial
                            if initial['A_key'] or initial['B_key'] or initial['A_mouse_downs'] or initial['B_mouse_downs']:
                                raise RuntimeError('owned_fixture_initial_input')
                            # Independent read of the complete group arrays, not the
                            # helper's 2500-node/prefix60 descriptive observation.
                            read_deadline=time.monotonic()+15
                            def attr(node,name,optional=False):
                                identity()
                                if time.monotonic()>=read_deadline:raise RuntimeError('host_read_deadline')
                                if AXUIElementSetMessagingTimeout(node,.2)!=0:raise RuntimeError('host_timeout_set')
                                try:
                                    error,value=AXUIElementCopyAttributeValue(node,name,None)
                                    if optional and error in (-25205,-25212):return None
                                    if error!=0:raise RuntimeError('host_attribute_unavailable')
                                    return value
                                finally:
                                    if AXUIElementSetMessagingTimeout(node,0)!=0:raise RuntimeError('host_timeout_reset')
                            windows=native_elements(attr(AXUIElementCreateApplication(child.pid),'AXWindows'))
                            if len(windows)!=2:raise RuntimeError('owned_window_count')
                            stack=list(windows);scale_root=None;seen=0
                            while stack:
                                node=stack.pop();seen+=1
                                if seen>50:raise RuntimeError('host_root_search_budget')
                                # AXIdentifier is optional on standard window chrome.
                                if attr(node,'AXIdentifier',optional=True)=='scale-root':scale_root=node;break
                                children=attr(node,'AXChildren',optional=True)
                                if children is not None:stack.extend(native_elements(children))
                            if scale_root is None:raise RuntimeError('owned_scale_root_missing')
                            groups_value=attr(scale_root,'AXChildren',optional=True)
                            groups=[] if groups_value is None else native_elements(groups_value)
                            if len(groups)>60:raise RuntimeError('owned_group_budget')
                            actual=sum(len(native_elements(attr(group,'AXChildren'))) for group in groups)
                            case['observed_B_leaves']=actual
                            if actual!=count:raise RuntimeError('owned_leaf_count_mismatch')
                            observation=driver.observe(TARGET,'tree_full')
                            if observation.get('type')!='observation':raise RuntimeError('signed_observation_failed')
                            window,_=Selector(role='Window',title='NLC Readiness Scale A').resolve(observation['tree'])
                            case['before_preparation']=state()
                            started=time.monotonic()
                            reply=driver.act(observation['obs_id'],TARGET,{'name':'prepare_window','element_index':window})
                            case['elapsed_ms']=round((time.monotonic()-started)*1000)
                            case['response_type']=reply.get('type')
                            case['effect']=reply.get('effect')
                            case['diagnostic']=preparation_diagnostic(reply.get('preparation_diagnostic'))
                            case['final']=wait_preparation_state(state,case['before_preparation'])
                            case['receipt_fresh']=(case['final'] is not None
                                and case['final']['seq']>case['before_preparation']['seq'])
                            case['passed']=(reply.get('type')=='acted'
                                and preparation_received(case['final'],case['before_preparation']))
                        except Exception as error:
                            case.update(passed=False,error_class=type(error).__name__,effect='unknown')
                        finally:
                            if child is not None:
                                if child.poll() is None:
                                    try:
                                        case['final']=state()
                                        case['passed']=(case['passed'] and preparation_received(
                                            case['final'],case.get('before_preparation')))
                                    except Exception as error:
                                        case['passed']=False
                                        case['final_read_error_class']=type(error).__name__
                                if child.poll() is None:
                                    child.terminate()
                                    try:child.wait(timeout=3)
                                    except subprocess.TimeoutExpired:child.kill();child.wait(timeout=3)
                                case['owned_child_reaped']=child.poll() is not None
                finally:
                    os.close(fd);case['input_audit']=audit.report()
                    if case['input_audit'].get('status')!='UNCHANGED_SAMPLED':case['passed']=False
                    prior_receiving,latest_receiving=case.get('before_preparation'),case.get('final')
                    case['activation_event_delta']=(latest_receiving['activations']-prior_receiving['activations']
                        if type(prior_receiving) is dict and type(latest_receiving) is dict else None)
                    case['input_preservation_verified']=False
                # NSRunningApplication can briefly retain the reaped child.
                # Wait only for its own metadata removal; never terminate a
                # newly appearing/foreign process or repeat the preparation.
                cleanup_deadline=time.monotonic()+3
                while True:
                    remaining_pid=host._resolve(TARGET,launch=False)
                    if remaining_pid is None:break
                    if (child is None or remaining_pid!=child.pid
                            or time.monotonic()>=cleanup_deadline):
                        raise RuntimeError('owned_fixture_cleanup_unverified')
                    host._pump(.05)
    finally:
        source_after=_source_manifest(root);report['source_subset_after']=source_after
        report['source_subset_stable']=source_before==source_after
    report['passed']=(report['source_subset_stable'] and len(report['cases'])==len(COUNTS)
                      and all(c['passed'] for c in report['cases']))
