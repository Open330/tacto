"""Close this signed client's channel after an owned send, before reading reply.

The helper reply may already be in the kernel buffer. This is client-side
reply loss, not proof of a network outage or of helper-side EPIPE.
"""
from pathlib import Path
import hashlib
import json
import os
import plistlib
import socket
import subprocess
import tempfile
import time

TARGET = 'ai.callabo.nlc.list-fixture'
BODY = 'lost_reply_once'


class DisconnectAfterCommit:
    """Delegate real IO; terminate the owned socket before its commit read."""
    def __init__(self, stream, channel, received):
        self.stream, self.channel, self.received = stream, channel, received
        self.armed = self.closed = False
        self.writes = self.commit_writes = self.reads_after_close = 0

    def write(self, payload):
        message = json.loads(payload)
        self.writes += 1
        action = message.get('action') or {}
        if (message.get('type') == 'act' and message.get('target') == TARGET
                and action.get('name') == 'press_key' and action.get('key') == 'Return'):
            self.armed = True
            self.commit_writes += 1
        return self.stream.write(payload)

    def flush(self):
        self.stream.flush()
        if self.armed:
            self.armed = False
            try:
                deadline = time.monotonic() + 3
                while not self.received():
                    if time.monotonic() >= deadline:
                        raise TimeoutError('owned commit was not observed')
                    time.sleep(.01)
            finally:
                try:
                    self.channel.shutdown(socket.SHUT_RDWR)
                finally:
                    self.stream.close()
                    self.closed = True

    def readline(self):
        if self.closed:
            self.reads_after_close += 1
        return self.stream.readline()  # closed real file raises ValueError

    def close(self):
        self.stream.close()


def main(driver, output):
    from tacto.driver import Driver, _attr
    from tacto.flow import Flow, Step, Selector, Postcondition, replay, pin_params
    from tacto.contract import Envelope, ContextBinding, issue_approval
    from tacto.helper import HelperUnavailable, InheritedHelperDriver
    from tacto.helper_identity import helper_instance_status
    from tacto.transcript_receipt import TranscriptReceipt
    from tacto.input_audit_process import ProcessInputAudit
    from ApplicationServices import AXUIElementCreateApplication
    root = Path(__file__).resolve().parents[2]
    report_fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    report = {'passed': False, 'model_calls': 0,
              'fault_boundary': 'client_shutdown_after_owned_commit_before_reply_read',
              'reply_may_already_be_buffered': True, 'helper_write_failure': 'not_observed',
              'receipt_kind': 'sender_transcript_independent_owned_AX_reader',
              'external_delivery_verified': False, 'all_dependencies_frozen': False}
    process = audit = interrupted = None
    source_paths = list((root / 'tacto').glob('*.py')) + list((root / 'nlc').glob('*.py')) + list((root / 'helper').glob('*.swift')) + [
        Path(__file__).resolve(), root / 'examples/eval.py', root / 'tests/fixtures/NativeListFixture.swift',
        root / 'launcher/tacto.swift', root / 'launcher/tacto', root / 'helper/tacto-helper',
        root / 'helper/Tacto Helper.app/Contents/MacOS/TactoHelper']
    def source_subset():
        return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(source_paths)}
    source_before = None
    try:
        source_before = source_subset()
        local = Driver()
        audit = ProcessInputAudit(interval=.005, startup_timeout=10, stop_timeout=3)
        assert local._resolve(TARGET, launch=False) is None, 'fixture already running'
        assert isinstance(driver, InheritedHelperDriver), 'signed inherited channel required'
        identity = driver.helper_process_identity
        assert identity is not None and identity.peer_pid_version is not None, 'helper identity unsupported'
        report['helper_identity'] = {'pid':identity.pid,'start_time':identity.start_time,
                                     'peer_pid_version':identity.peer_pid_version}
        from AppKit import NSRunningApplication
        helper = NSRunningApplication.runningApplicationWithProcessIdentifier_(identity.pid)
        assert (helper is not None and helper.bundleIdentifier() == 'ai.callabo.nlc.helper'
                and Path(str(helper.executableURL().path())).resolve() ==
                    (root / 'helper/Tacto Helper.app/Contents/MacOS/TactoHelper').resolve())
        report['helper_before'] = helper_instance_status(identity)
        assert report['helper_before'] == 'same_instance'
        with tempfile.TemporaryDirectory(prefix='nlc-commit-disconnect-') as work:
            app = Path(work) / 'NLC List Fixture.app'
            binary = app / 'Contents/MacOS/NativeListFixture'
            binary.parent.mkdir(parents=True)
            (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({
                'CFBundleIdentifier': TARGET, 'CFBundleExecutable': binary.name,
                'CFBundleName': app.stem, 'CFBundlePackageType': 'APPL', 'LSUIElement': True}))
            subprocess.run(['xcrun', 'swiftc', '-parse-as-library',
                str(root / 'tests/fixtures/NativeListFixture.swift'), '-o', str(binary)],
                check=True, capture_output=True, timeout=60)
            with audit:
                try:
                    process = subprocess.Popen([str(binary)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    deadline = time.monotonic() + 15
                    while local._resolve(TARGET, launch=False) != process.pid:
                        assert time.monotonic() < deadline and process.poll() is None
                        time.sleep(.1)
                    time.sleep(.5)
                    def host():
                        assert process.poll() is None
                        stack = [AXUIElementCreateApplication(process.pid)]
                        for _ in range(512):
                            if not stack:
                                break
                            node = stack.pop()
                            if _attr(node, 'AXIdentifier') == 'list-state':
                                return dict(part.split('=', 1) for part in str(_attr(node, 'AXValue')).split())
                            stack.extend(_attr(node, 'AXChildren') or [])
                        raise AssertionError('owned state unavailable')
                    def reader(target):
                        assert target == TARGET and local._resolve(TARGET, launch=False) == process.pid
                        seen = local.observe(target, 'tree_full')
                        assert seen.get('type') == 'observation'
                        return seen['tree']
                    report['initial'] = host()
                    assert report['initial']['sent'] == '0' and report['initial']['active'] == 'false'
                    view = driver.observe(TARGET, 'tree_full')
                    index, _ = Selector(role='Window', title='NLC List Fixture').resolve(view['tree'])
                    response = driver.act(view['obs_id'], TARGET, {'name':'prepare_window', 'element_index':index})
                    report['preparation'] = response
                    assert response.get('type') == 'acted'
                    time.sleep(.3)
                    view = driver.observe(TARGET, 'tree_full')
                    index, _ = Selector(identifier='message-field').resolve(view['tree'])
                    focused = driver.act(view['obs_id'], TARGET, {'name':'click', 'element_index':index})
                    assert focused.get('type') == 'acted'
                    time.sleep(.3)
                    view = driver.observe(TARGET, 'tree_full')
                    index, _ = Selector(identifier='message-field').resolve(view['tree'])
                    typed = driver.act(view['obs_id'], TARGET, {'name':'set_value', 'element_index':index, 'value':BODY})
                    assert typed.get('type') == 'acted' and host()['sent'] == '0'
                    env, params = Envelope.minimal(), {'key':'Return'}
                    send = Step('press_key', Selector(identifier='message-field'), params=params, is_submit=True,
                        safety=env.contract_for({'transmit'}, 'owned message'),
                        binding=ContextBinding(bundle_id=TARGET, identifier='message-field',
                                               operation='press_key', params_digest=pin_params(params)),
                        postcondition=Postcondition(field=Selector(identifier='list-state'),
                                                    attribute='Value', contains='sent=1 lastSent=' + BODY))
                    rec = TranscriptReceipt(reader, target=TARGET, recipient='NLC List Fixture', body=BODY,
                        recipient_field=Selector(role='Window', title='NLC List Fixture'),
                        messages=Selector(role='StaticText', ancestor='transcript'), timeout_seconds=3)
                    def received():
                        state = host()
                        return state['sent'] == '1' and state['lastSent'] == BODY
                    disconnected = DisconnectAfterCommit(driver._f, driver._sock, received)
                    driver._f = disconnected
                    flow = Flow('commit_then_read', TARGET, [send,
                        Step('read', safety=env.contract_for({'read'}, 'entry'))], schema=2)
                    result = replay(driver, flow, {0: issue_approval(send.binding, send.safety)},
                        prepare_commit=rec.prepare_commit, after_commit=rec.after_commit,
                        commit_expected=rec.commit_expected)
                    report['replay'] = {'ok':result.ok, 'log':result.log, 'events':result.events,
                                        'receipt_count':len(result.commit_evidence)}
                    assert not result.ok and result.log[-1].get('error') == 'observation_unavailable'
                    assert len(result.events) == 1 and result.events[0]['state'] == 'confirmed'
                    assert result.events[0]['commit_evidence_kind'] == 'sender_transcript'
                    assert result.events[0]['dispatch_response'] == {'type':'error', 'effect':'unknown'}
                    assert len(result.commit_evidence) == 1 and disconnected.closed
                    writes = disconnected.writes
                    refusals = 0
                    for following in ({'type':'ping'}, {'type':'act', 'target':TARGET,
                            'action':{'name':'press_key', 'key':'Return', 'element_index':index}}):
                        try: driver._send(following)
                        except HelperUnavailable: refusals += 1
                        else: raise AssertionError('closed channel reused')
                    time.sleep(.25)
                    report['final'] = host()
                    report['channel'] = {'poisoned':driver._poisoned, 'socket_closed':driver._sock.fileno() == -1,
                        'file_closed':disconnected.stream.closed, 'commit_writes':disconnected.commit_writes,
                        'reads_after_close':disconnected.reads_after_close, 'following_refusals':refusals,
                        'following_writes':disconnected.writes - writes}
                    assert report['final']['sent'] == '1' and report['final']['lastSent'] == BODY
                    assert disconnected.commit_writes == 1 and disconnected.writes == writes
                    assert driver._poisoned and report['channel']['socket_closed']
                    deadline = time.monotonic() + 5
                    while True:
                        status = helper_instance_status(identity)
                        if status in ('exited','zombie','replaced') or time.monotonic() >= deadline:
                            break
                        time.sleep(.05)
                    report['helper_after_channel_close'] = status
                    report['helper_original_execution_ended'] = status in ('exited','zombie','replaced')
                    assert report['helper_original_execution_ended']
                    report['passed'] = True
                finally:
                    if process is not None:
                        if process.poll() is None: process.terminate()
                        process.wait(timeout=5)
                        report['owned_child_reaped'] = True
    except BaseException as exc:
        report.update(passed=False, error_class=type(exc).__name__)
        if not isinstance(exc, Exception):
            interrupted = exc
            report['interrupted'] = True
    finally:
        report['input_audit'] = audit.report() if audit is not None else {'status':'NOT_STARTED'}
        if report['input_audit']['status'] != 'UNCHANGED_SAMPLED': report['passed'] = False
        source_after = source_subset()
        report.update(source_subset_before=source_before, source_subset_after=source_after,
                      source_subset_stable=source_before == source_after)
        if source_before != source_after: report['passed'] = False
        with os.fdopen(report_fd, 'w') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
    if interrupted is not None: raise interrupted
    return report
