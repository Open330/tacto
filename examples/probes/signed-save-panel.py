"""Owned background panel, signed read transport, independent host values."""
from pathlib import Path
import json
import hashlib
import plistlib
import subprocess
import tempfile
import time

TARGET = 'ai.callabo.tacto.save-settings-fixture'


def main(driver, output, *, guarded_commit=False):
    from tacto.driver import Driver
    from tacto.input_audit_process import ProcessInputAudit
    root = Path(__file__).resolve().parents[2]
    audit = ProcessInputAudit(interval=.005, startup_timeout=10, stop_timeout=3)
    report = {'passed': False, 'transport': type(driver).__name__,
              'scope': ('owned NSSavePanel callback; guarded AXPress; no file writer'
                        if guarded_commit else 'owned NSSavePanel; read only; never Save'), 'steps': []}
    process = None
    sources = ('helper/TactoHelper.swift', 'helper/SavePanelObservation.swift', 'tacto/helper.py',
               'launcher/tacto.swift', 'examples/probes/signed-save-panel.py',
               'tests/fixtures/NativeSaveSettingsFixture.swift', 'tacto/input_audit_process.py')
    def hashes():
        return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in sources}
    report['source_sha256'] = hashes()
    destination = root / 'reviews/2026-10-01' / f'owned-signed-directory-{time.time_ns()}'
    changed = destination.with_name(destination.name + '-changed')
    try:
        assert Driver()._resolve(TARGET, launch=False) is None, 'fixture already running'
        with tempfile.TemporaryDirectory(prefix='tacto-signed-save-panel-') as work:
            app = Path(work) / 'TACTO Owned Save Directory.app'
            binary = app / 'Contents/MacOS/NativeSaveSettingsFixture'
            binary.parent.mkdir(parents=True)
            (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({
                'CFBundleIdentifier': TARGET, 'CFBundleExecutable': binary.name,
                'CFBundlePackageType': 'APPL', 'CFBundleName': app.stem, 'LSUIElement': True}))
            subprocess.run(['xcrun', 'swiftc', '-warnings-as-errors', '-parse-as-library',
                            str(root / 'tests/fixtures/NativeSaveSettingsFixture.swift'),
                            '-o', str(binary)], check=True, timeout=60, capture_output=True)
            destination.mkdir(); changed.mkdir()
            trigger = Path(work) / 'change-settings'
            host_path = output.with_suffix('.host.jsonl')
            with audit, host_path.open('w') as host:
                try:
                    process = subprocess.Popen([str(binary), str(destination), str(changed), str(trigger)],
                                               stdout=host, stderr=subprocess.DEVNULL)
                    report['fixture_pid'] = process.pid
                    deadline = time.monotonic() + 15
                    while Driver()._resolve(TARGET, launch=False) != process.pid:
                        assert time.monotonic() < deadline and process.poll() is None, 'fixture unavailable'
                        time.sleep(.1)
                    time.sleep(4)

                    def observe():
                        observation = driver.observe(TARGET, 'tree_full')
                        assert observation.get('type') == 'observation', observation
                        windows = [int(p[0]) for line in observation['tree'].splitlines()
                                   if len(p := line.strip().split()) > 1 and p[1] == 'Window']
                        assert windows, 'window missing in signed observation'
                        return observation, windows[0]

                    def read(observation, window, **kwargs):
                        return driver.save_panel(observation['obs_id'], TARGET, window, **kwargs)

                    def refused(answer):
                        assert answer.get('error') == 'save_panel_unavailable' and answer.get('effect') == 'not_started', answer

                    observation, window = observe()
                    report['steps'].append({'step': 'stale observation', 'response':
                        driver.save_panel('foreign-observation', TARGET, window)})
                    refused(report['steps'][-1]['response'])
                    first = read(observation, window, require_format=True)
                    report['initial'] = first
                    assert first.get('type') == 'save_panel', first
                    assert (first['directory'], first['filename'], first['format'], first['original_changes_setting']) == (
                        str(destination), 'never-saved.rtf', 'rtf', 0), first
                    assert first['authorizes_save'] is False
                    commit = first['commit_control']['action']['element_index']
                    line = next(line for line in observation['tree'].splitlines()
                                if line.strip().split()[0] == str(commit))
                    assert 'Button' in line and 'OKButton' in line, line
                    report['commit_observation_row'] = line.strip()
                    same = read(observation, window, panel_token=first['panel_token'], require_format=True)
                    assert same == first, same
                    if guarded_commit:
                        action = first['commit_control']['action']
                        denied = driver.act(observation['obs_id'], TARGET, action,
                            save_panel_token=first['panel_token'], risk_class='preapproved', approved=False)
                        unknown = driver.act(observation['obs_id'], TARGET, action,
                            save_panel_token='foreign-panel', risk_class='preapproved', approved=True)
                        report['guard_refusals'] = [denied, unknown]
                        assert all(r.get('effect') == 'not_started' for r in (denied, unknown)), report['guard_refusals']
                        response = driver.act(observation['obs_id'], TARGET, action,
                            save_panel_token=first['panel_token'], risk_class='preapproved', approved=True)
                        report['guarded_click'] = response
                        deadline = time.monotonic() + 5
                        while process.poll() is None and time.monotonic() < deadline:
                            time.sleep(.05)
                        callbacks = [row for line in host_path.read_text().splitlines()
                                     if (row := json.loads(line)).get('callback') == 'sheet_completed']
                        report['owned_callbacks'] = callbacks
                        assert callbacks == [{'callback':'sheet_completed', 'response':1}], callbacks
                        assert response.get('type') == 'acted', response
                        assert process.poll() == 0, 'owned panel did not exit after callback'
                        assert not list(destination.iterdir()) and not list(changed.iterdir()), 'unexpected saved artifact'
                    else:
                        refused(read(observation, window, panel_token='foreign-panel'))
                        trigger.write_text('owned host changes same panel settings')
                        time.sleep(2)
                        observation, window = observe()
                        stale = read(observation, window, panel_token=first['panel_token'])
                        report['changed_settings_rejected'] = stale
                        refused(stale)
                        stale_click = driver.act(observation['obs_id'], TARGET,
                            first['commit_control']['action'], save_panel_token=first['panel_token'],
                            risk_class='preapproved', approved=True)
                        report['changed_settings_click_rejected'] = stale_click
                        assert stale_click.get('effect') == 'not_started', stale_click
                        fresh = read(observation, window)
                        report['fresh'] = fresh
                        assert fresh.get('type') == 'save_panel', fresh
                        host_values = json.loads(host_path.read_text().splitlines()[-1])
                        report['host_values'] = host_values
                        assert (fresh['filename'], fresh['format_value'], fresh['original_changes_setting'], fresh['directory']) == (
                            host_values['filename'], host_values['format'], host_values['keep_changes'], host_values['directory'])
                        assert fresh['format'] is None and fresh['format_value'] == 'Plain Text'
                        assert fresh['original_changes_setting'] == 1 and not host_values['active']
                        refused(read(observation, window, require_format=True))
                        assert not list(destination.iterdir()) and not list(changed.iterdir()), 'unexpected saved artifact'
                    report['passed'] = True
                finally:
                    if process is not None:
                        if process.poll() is None:
                            process.terminate()
                        process.wait(timeout=5)
                        report['owned_child_reaped'] = True
    except Exception as exc:
        report.update(passed=False, error=type(exc).__name__ + ': ' + str(exc))
    finally:
        for directory in (changed, destination):
            if directory.exists():
                directory.rmdir()
    report['input_audit'] = audit.report()
    report['after_source_sha256'] = hashes()
    report['source_stable'] = report['source_sha256'] == report['after_source_sha256']
    if not report['source_stable']:
        report['passed'] = False
    if report['input_audit']['status'] != 'UNCHANGED_SAMPLED':
        report['passed'] = False
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return report
