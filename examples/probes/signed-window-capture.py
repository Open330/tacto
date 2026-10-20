"""Signed transport: a picture of one owned window, taken by the grant holder.

The screen-recording permission belongs to `ai.callabo.tacto.helper` rather than
to whatever Python happens to be running, for the same reason the
accessibility one does — on a shared interpreter it would be a permission
every script on that interpreter holds, and a screenshot reaches further than
a click.

Captures only a fixture this script launched and terminates. Never the
desktop, never a region, never an app of the person's.
"""
from __future__ import annotations

import json
import os
import plistlib
import subprocess
import tempfile
import time
from pathlib import Path

TARGET = 'ai.callabo.tacto.menu-fixture'


def main(driver, output: Path) -> dict:
    from tacto.input_audit_process import ProcessInputAudit

    audit = ProcessInputAudit(interval=.005, startup_timeout=10, stop_timeout=3)
    try:
        with audit:
            report = _capture_owned_window(driver, output)
    except Exception as exc:
        report = {'passed': False, 'error': type(exc).__name__ + ': ' + str(exc)}
    report['input_audit'] = audit.report()
    if report['input_audit']['status'] != 'UNCHANGED_SAMPLED':
        report['passed'] = False
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                      encoding='utf-8')
    return report


def _capture_owned_window(driver, output: Path) -> dict:
    from tacto.driver import Driver

    root = Path(__file__).resolve().parents[2]
    source = (root / 'tests/fixtures/NativeMenuFixture.swift').read_bytes()
    local = Driver()
    assert local._resolve(TARGET, launch=False) is None, 'fixture already running'

    report: dict = {'transport': type(driver).__name__, 'target': TARGET,
                    'grant_holder': 'ai.callabo.tacto.helper',
                    'scope': 'one owned window; never the desktop or a region',
                    'steps': []}
    process = None
    try:
        with tempfile.TemporaryDirectory() as work:
            app = Path(work) / 'TACTO Menu Fixture.app'
            binary = app / 'Contents/MacOS/NativeMenuFixture'
            binary.parent.mkdir(parents=True)
            (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({
                'CFBundleIdentifier': TARGET, 'CFBundleExecutable': binary.name,
                'CFBundleName': 'TACTO Menu Fixture', 'CFBundlePackageType': 'APPL',
                'CFBundleVersion': '1', 'LSUIElement': True}))
            captured = Path(work) / 'NativeMenuFixture.swift'
            captured.write_bytes(source)
            subprocess.run(['xcrun', 'swiftc', '-warnings-as-errors',
                            '-parse-as-library', str(captured), '-o', str(binary)],
                           check=True, timeout=120)
            process = subprocess.Popen([str(binary), '--timeout', '120'],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            report['fixture_pid'] = process.pid
            deadline = time.monotonic() + 20
            while local._resolve(TARGET, launch=False) != process.pid:
                assert time.monotonic() < deadline and process.poll() is None, 'did not register'
                time.sleep(.05)
            time.sleep(.8)

            # `capture` reaching this transport at all is the wiring under
            # test; the capability string is checked in the unit suite, and
            # this driver has no `ping` to ask.
            report['capture_reachable'] = callable(getattr(driver, 'capture', None))
            assert report['capture_reachable'], 'capture is not on this transport'
            observation = driver.observe(TARGET, 'tree_full')
            assert observation.get('type') == 'observation', observation
            report['steps'].append({'step': 'observe',
                                    'element_count': observation.get('element_count')})

            # A capture names the observation that described the window.
            stale = driver.capture(TARGET, obs_id='not-the-one')
            report['steps'].append({'step': 'stale obs_id', 'response': stale})
            assert stale.get('error') == 'stale_observation', stale

            destination = output.parent / f'{output.stem}.png'
            # No window_index. There was a second window here for a while and
            # it was ours: macOS attaches its capture badge TO the window being
            # captured, and that badge turns up in the app's own AXWindows
            # list. The helper drops attachments now — something sitting wholly
            # inside another of the app's windows with no way to close it is
            # not a window — so the fixture has one again and naming an index
            # would only re-introduce a number to get wrong.
            shot = driver.capture(TARGET, obs_id=observation['obs_id'],
                                  path=str(destination))
            report['steps'].append({'step': 'capture', 'response': shot})
            if shot.get('type') != 'capture':
                report['passed'] = False
                report['why'] = shot.get('detail') or shot.get('error')
                return report

            assert shot['authorizes_action'] is False, shot
            assert shot['commit_witness'] is False, shot
            assert destination.exists() and destination.stat().st_size > 0, 'no file'
            header = destination.read_bytes()[:8]
            assert header == b'\x89PNG\r\n\x1a\n', f'not a PNG: {header!r}'
            report['png_bytes'] = destination.stat().st_size
            report['png_path'] = str(destination)
            report['passed'] = True
    except Exception as exc:
        report['steps'].append({'error': repr(exc)})
        report['passed'] = False
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        elif process is not None:
            process.wait(timeout=5)
    return report
