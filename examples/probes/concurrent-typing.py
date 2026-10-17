"""Prepare/run a bounded user typing trial against an owned background window.

Never generates foreground typing, activates an app, restores input, or retries
an uncertain dispatch. Reports sampled evidence, not hardware input attestation.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
WITNESS = 'ai.callabo.nlc.user-typing-witness'
TARGET = 'ai.callabo.nlc.navigation-fixture'
EXPECTED = 'tacto typing 12345'
STATE = re.compile(r'downs=(\d+) ups=(\d+) wrongWindow=(\d+) utf16=(\d+) '
                   r'selection=(\d+),(\d+) key=(true|false) focused=(true|false) sha256=([0-9a-f]{64})')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_new(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, indent=2); stream.write('\n')


def prepare(directory):
    directory.mkdir(mode=0o700, exist_ok=False)
    bundle = directory / 'Background Navigation.app'
    binary = bundle / 'Contents/MacOS/Navigation'
    binary.parent.mkdir(parents=True)
    source = directory / 'NativeNavigationFixture.swift'
    source.write_bytes((ROOT / 'tests/fixtures/NativeNavigationFixture.swift').read_bytes())
    subprocess.run(['xcrun', 'swiftc', '-parse-as-library', '-warnings-as-errors',
                    str(source), '-o', str(binary)], check=True, capture_output=True, timeout=45)
    (bundle / 'Contents/Info.plist').write_bytes(plistlib.dumps({
        'CFBundleIdentifier': TARGET, 'CFBundleExecutable': 'Navigation',
        'CFBundleName': 'NLC Background Navigation', 'CFBundlePackageType': 'APPL'}))
    subprocess.run(['codesign', '--force', '--sign', '-', str(bundle)],
                   check=True, capture_output=True, timeout=15)
    subprocess.run(['codesign', '--verify', '--strict', str(bundle)],
                   check=True, capture_output=True, timeout=15)
    manifest = {'binary': str(binary.resolve()), 'binary_sha256': digest(binary),
                'source_sha256': digest(source), 'built_not_launched': True,
                'foreground_text': EXPECTED, 'background_right_count': 16,
                'typing_wait_seconds': 60, 'hardware_origin_attested': False}
    write_new(directory / 'build-report.json', manifest)
    print(json.dumps(manifest), flush=True)


def run(directory, output, witness_build_report):
    from AppKit import NSRunningApplication
    from ApplicationServices import AXUIElementCreateApplication, AXUIElementSetMessagingTimeout
    from tacto.driver import Driver, _attr
    from tacto.flow import Selector
    from tacto.input_audit import MacInputReader
    manifest = json.loads((directory / 'build-report.json').read_text())
    binary = Path(manifest['binary'])
    report = {'passed': False, 'scope': 'owned foreground test text / owned background navigation',
              'hardware_origin_attested': False, 'actions': [], 'samples': [],
              'classification': 'not_run', 'expected_text_sha256': hashlib.sha256(EXPECTED.encode()).hexdigest()}
    selected = ['examples/probes/concurrent-typing.py', 'tacto/driver.py', 'tacto/input_audit.py',
                'tacto/native_frontmost.py', 'tacto/native_window_ready.py',
                'tacto/native_window_binding.py', 'tacto/native_window_dispatch.py', 'tacto/native_key_events.py']
    report['selected_sources_before'] = {name: digest(ROOT / name) for name in selected}
    child = None
    try:
        assert not output.exists(), 'fresh report path required'
        assert digest(binary) == manifest['binary_sha256'], 'prepared binary changed'
        apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_(WITNESS)
        assert len(apps) == 1, 'exactly one user typing witness required'
        witness_build = json.loads(witness_build_report.read_text())
        assert str(Path(apps[0].bundleURL().path()).resolve()) == witness_build['app'], 'prepared witness bundle required'
        assert digest(Path(witness_build['app']) / 'Contents/MacOS/TypingWitness') == witness_build['binary_sha256'], 'witness binary changed'
        report['witness_build_report_sha256'] = digest(witness_build_report)
        report['background_binary_sha256'] = digest(binary)
        witness_pid = apps[0].processIdentifier()
        report['witness_pid'] = witness_pid
        reader = MacInputReader(strategy='application_frontmost', require_selection=True)
        initial = reader()
        assert not initial.errors and initial.frontmost_pid == witness_pid and initial.selected_text_range == (0, 0), 'own empty focused field required'
        assert initial.pointer is not None and initial.focus_identity not in (None, 'no-focus')

        def labels(pid, prefix):
            app = AXUIElementCreateApplication(pid)
            assert AXUIElementSetMessagingTimeout(app, .25) == 0
            stack, found, count = [app], {}, 0
            deadline = time.monotonic() + 1
            while stack:
                count += 1
                assert count <= 150 and time.monotonic() < deadline, 'AX label read budget exceeded'
                node = stack.pop(); identifier = _attr(node, 'AXIdentifier')
                if isinstance(identifier, str) and identifier.startswith(prefix):
                    assert identifier not in found, 'ambiguous own label'
                    # Read only own counters/control state; never editor bodies.
                    if identifier != 'typing-witness-editor': found[identifier] = _attr(node, 'AXValue')
                stack.extend(_attr(node, 'AXChildren') or [])
            return found

        def sample():
            before = reader()
            assert not before.errors and before.frontmost_pid == witness_pid, 'foreground changed or unavailable; no restore'
            assert before.pointer == initial.pointer and before.focus_identity == initial.focus_identity, 'pointer/focus changed; cause not attributed'
            value = labels(witness_pid, 'typing-witness-').get('typing-witness-state')
            match = STATE.fullmatch(value or '')
            assert match, 'own witness state unavailable'
            parts = match.groups()
            state = dict(zip(('downs', 'ups', 'wrong_window', 'utf16', 'location', 'length'), map(int, parts[:6])))
            state.update(key=parts[6] == 'true', focused=parts[7] == 'true', sha256=parts[8])
            assert state['key'] and state['focused'] and state['wrong_window'] == 0, 'own witness routing/focus changed'
            assert len(report['samples']) < 1500, 'sample budget exceeded'
            row = {'time': time.monotonic(), 'input': asdict(before), 'witness': state}
            report['samples'].append(row)
            return state

        baseline = sample()
        assert baseline['utf16'] == baseline['downs'] == baseline['ups'] == 0, 'fresh empty trial required'
        driver = Driver()
        assert driver._resolve(TARGET, launch=False) is None, 'background fixture already running'
        child = subprocess.Popen([str(binary)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        report['fixture_pid'] = child.pid
        deadline = time.monotonic() + 10
        while driver._resolve(TARGET, launch=False) != child.pid:
            assert child.poll() is None and time.monotonic() < deadline, 'owned child unavailable'
            sample(); time.sleep(.1)
        def observe():
            assert driver._resolve(TARGET, launch=False) == child.pid, 'owned child identity changed'
            value = driver.observe(TARGET, 'tree_full')
            assert value.get('type') == 'observation', 'background observation failed'
            return value
        view = observe()
        index, _ = Selector(role='Window', title='NLC Navigation Fixture A').resolve(view['tree'])
        sample()
        prepared = driver.act(view['obs_id'], TARGET, {'name': 'prepare_window', 'element_index': index})
        report['preparation'] = prepared
        assert prepared.get('type') == 'acted', 'background preparation refused'
        sample()
        state = labels(child.pid, 'nav-')
        assert state['nav-A-first'] == 'position=0'
        assert all('downs=0 ups=0 routeErrors=0 key=false' in state[f'nav-{suffix}-state'] for suffix in ('A', 'B')), 'fresh background counters required'
        assert sample()['downs'] == 0, 'typing started before arming; fresh trial required'
        print('ARMED: type "tacto typing 12345" slowly in own witness; first key starts background trial', flush=True)
        report['classification'] = 'waiting_for_user_typing'
        deadline = time.monotonic() + 60
        while sample()['downs'] == 0:
            assert time.monotonic() < deadline, 'no user typing observed before deadline'
            time.sleep(.1)
        report['classification'] = 'running'
        for number in range(16):
            sample(); view = observe()
            index, _ = Selector(identifier='nav-A-first').resolve(view['tree'])
            sample()
            started = time.monotonic()
            result = driver.act(view['obs_id'], TARGET, {'name': 'press_key', 'element_index': index, 'key': 'Right'})
            report['actions'].append({'started': started, 'ended': time.monotonic(), 'result': result})
            assert result.get('type') == 'acted' and result.get('posted_events') == 2, 'background input refused/uncertain; no retry'
            until = time.monotonic() + 1
            while time.monotonic() < until:
                sample(); time.sleep(.1)
            state = labels(child.pid, 'nav-')
            assert state['nav-A-first'] == f'position={number + 1}', 'receiving position mismatch'
            assert f'downs={number + 1} ups={number + 1} routeErrors=0 key=false' in state['nav-A-state'], 'background A receiving mismatch'
            assert 'downs=0 ups=0 routeErrors=0 key=false' in state['nav-B-state'], 'background B received input'
        until = time.monotonic() + 3
        while time.monotonic() < until: sample(); time.sleep(.1)
        final = sample(); report['final'] = final
        assert final['sha256'] == report['expected_text_sha256'] and final['utf16'] == len(EXPECTED), 'foreground test text mismatch'
        assert final['location'] == len(EXPECTED) and final['length'] == 0, 'foreground final caret mismatch'
        assert final['downs'] == final['ups'] == len(EXPECTED), 'expected direct per-character typing counters mismatch'
        start, end = report['actions'][0]['started'], report['actions'][-1]['ended']
        overlap = [r['witness']['downs'] for r in report['samples'] if start <= r['time'] <= end]
        assert overlap and max(overlap) - min(overlap) >= 2, 'insufficient sampled typing overlap'
        report['classification'] = 'passed_sampled_owned_concurrency'
        report['passed'] = True
    except Exception as exc:
        report['error'] = str(exc)
        report['classification'] = 'incomplete_or_failed_no_cause_attribution'
    finally:
        if child is not None:
            if child.poll() is None:
                child.terminate()
                try: child.wait(timeout=3)
                except subprocess.TimeoutExpired: child.kill(); child.wait(timeout=3)
            report['owned_child_reaped'] = child.poll() is not None
        report['selected_sources_after'] = {name: digest(ROOT / name) for name in selected}
        report['selected_sources_stable'] = report['selected_sources_before'] == report['selected_sources_after']
        times = [s['time'] for s in report['samples']]
        report['largest_sample_gap_seconds'] = max((b-a for a,b in zip(times, times[1:])), default=None)
        if not report['selected_sources_stable']: report['passed'] = False
        write_new(output, report)
    print(json.dumps({'passed': report['passed'], 'classification': report['classification'], 'report': str(output)}), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('prepare', 'run'))
    parser.add_argument('directory', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--witness-build-report', type=Path)
    args = parser.parse_args()
    if args.mode == 'prepare': prepare(args.directory)
    else:
        if args.output is None or args.witness_build_report is None:
            parser.error('run requires --output (fresh path) and --witness-build-report')
        raise SystemExit(run(args.directory, args.output, args.witness_build_report))
