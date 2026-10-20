"""Owned fixture only: signed preparation and independent menu counters."""
from pathlib import Path
import json
import plistlib
import subprocess
import tempfile
import time

TARGET = 'ai.callabo.tacto.menu-fixture'


def main(driver, output):
    from tacto.driver import Driver, _attr
    from ApplicationServices import AXUIElementCreateApplication
    from tacto.input_audit_process import ProcessInputAudit
    root = Path(__file__).resolve().parents[2]
    local = Driver()
    audit = ProcessInputAudit(interval=.005, startup_timeout=10, stop_timeout=3)
    report = {'passed': False, 'scope': 'owned background AppKit fixture; window preparation',
              'transport': type(driver).__name__, 'steps': []}
    process = None
    try:
        assert local._resolve(TARGET, launch=False) is None, 'fixture already running'
        assert 'action.prepare_window.background' in driver.capabilities
        with tempfile.TemporaryDirectory(prefix='tacto-signed-background-window-') as work:
            app = Path(work) / 'TACTO Menu Fixture.app'
            binary = app / 'Contents/MacOS/NativeMenuFixture'
            binary.parent.mkdir(parents=True)
            (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({
                'CFBundleIdentifier': TARGET, 'CFBundleExecutable': binary.name,
                'CFBundleName': app.stem, 'CFBundlePackageType': 'APPL', 'LSUIElement': True}))
            subprocess.run(['xcrun', 'swiftc', '-parse-as-library',
                str(root / 'tests/fixtures/NativeMenuFixture.swift'), '-o', str(binary)],
                check=True, capture_output=True, timeout=60)
            with audit:
                try:
                    process = subprocess.Popen([str(binary), '--timeout', '120'],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    report['fixture_pid'] = process.pid
                    deadline = time.monotonic() + 15
                    while local._resolve(TARGET, launch=False) != process.pid:
                        assert time.monotonic() < deadline and process.poll() is None
                        time.sleep(.1)
                    time.sleep(.5)

                    def host():
                        found = {}
                        stack = [AXUIElementCreateApplication(process.pid)]
                        while stack:
                            node = stack.pop()
                            identifier = _attr(node, 'AXIdentifier')
                            if identifier in ('tacto-fixture-direct-count', 'tacto-fixture-responder-count',
                                              'tacto-fixture-make-key-count', 'tacto-fixture-state'):
                                assert identifier not in found
                                found[identifier] = _attr(node, 'AXValue')
                            stack.extend(_attr(node, 'AXChildren') or [])
                        assert len(found) == 4, found
                        return found

                    initial = host()
                    report['initial'] = initial
                    assert all(initial[k] == '0' for k in initial if k.endswith('-count'))
                    assert 'isActive=false' in initial['tacto-fixture-state']
                    observation = driver.observe(TARGET, 'tree_full')
                    assert observation.get('type') == 'observation', observation
                    windows = [int(p[0]) for line in observation['tree'].splitlines()
                               if len(p := line.strip().split()) > 1 and p[1] == 'Window']
                    assert len(windows) == 1, windows
                    action = {'name': 'prepare_window', 'element_index': windows[0]}
                    stale = driver.act('foreign-observation', TARGET, action)
                    assert stale.get('type') == 'error' and stale.get('effect') == 'not_started', stale
                    if isinstance(driver, Driver):
                        from tacto.model_budget import model_deadline
                        with model_deadline(time.monotonic()-1):
                            expired = driver.act(observation['obs_id'], TARGET, action)
                    else:
                        expired = driver._send({'type':'act', 'target':TARGET, 'obs_id':observation['obs_id'],
                            'action':action, 'click_deadline_uptime':time.monotonic()-1})
                    assert expired.get('type') == 'error' and expired.get('effect') == 'not_started', expired
                    report['refusals'] = [stale, expired]
                    assert host() == initial
                    prepared = driver.act(observation['obs_id'], TARGET, action)
                    report['preparation'] = prepared
                    assert prepared.get('type') == 'acted', prepared
                    assert prepared.get('mechanism') == 'pid_window_events' and prepared.get('posted_events') == 3
                    assert prepared.get('reobserve_required') is True and prepared.get('unverified') == 'observe_effect'
                    time.sleep(.3)
                    report['after_preparation'] = host()
                    assert 'isKeyWindow=true' in report['after_preparation']['tacto-fixture-state']
                    for title, identifier in [('Direct Command', 'tacto-fixture-direct-count'),
                                              ('Responder Command', 'tacto-fixture-responder-count')]:
                        observation = driver.observe(TARGET, 'tree_full')
                        before = host()
                        menu = driver.menu_state(TARGET, ['Probe', title])
                        assert menu.get('enabled') is True, menu
                        response = driver.act(observation['obs_id'], TARGET, {'name':'menu', 'path':['Probe',title]})
                        time.sleep(.2)
                        after = host()
                        report['steps'].append({'title':title, 'before':before, 'response':response, 'after':after})
                        assert response.get('type') == 'acted', response
                        assert before[identifier] == '0' and after[identifier] == '1'
                    report['final'] = host()
                    assert report['final']['tacto-fixture-make-key-count'] == '0'
                    report['passed'] = True
                finally:
                    if process is not None:
                        if process.poll() is None and 'host' in locals():
                            try: report['before_cleanup'] = host()
                            except Exception as exc: report['cleanup_observation_error'] = type(exc).__name__
                        if process.poll() is None: process.terminate()
                        process.wait(timeout=5)
                        report['owned_child_reaped'] = True
    except Exception as exc:
        report.update(passed=False, error=type(exc).__name__ + ': ' + str(exc))
    report['input_audit'] = audit.report()
    if report['input_audit']['status'] != 'UNCHANGED_SAMPLED': report['passed'] = False
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return report
