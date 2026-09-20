"""Signed transport: a picture of one owned window, taken by the grant holder.

The screen-recording permission belongs to `ai.callabo.nlc.helper` rather than
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

TARGET = 'ai.callabo.nlc.menu-fixture'


def _standard_window_index(driver, target):
    """Which of the app's windows is the ordinary one, by subrole.

    Counted over the same list the helper indexes — real windows only, in
    order — so the number means the same thing on both sides.
    """
    from ApplicationServices import AXUIElementCreateApplication
    from nlc.driver import Driver, _attr
    pid = Driver()._resolve(target, launch=False)
    if pid is None:
        return 0
    windows = [w for w in (_attr(AXUIElementCreateApplication(pid), 'AXWindows') or [])
               if _attr(w, 'AXRole') == 'AXWindow']
    for i, w in enumerate(windows):
        if _attr(w, 'AXSubrole') == 'AXStandardWindow':
            return i
    return 0


def main(driver, output: Path) -> dict:
    from nlc.driver import Driver

    root = Path(__file__).resolve().parents[2]
    source = (root / 'tests/fixtures/NativeMenuFixture.swift').read_bytes()
    local = Driver()
    assert local._resolve(TARGET, launch=False) is None, 'fixture already running'

    report: dict = {'transport': type(driver).__name__, 'target': TARGET,
                    'grant_holder': 'ai.callabo.nlc.helper',
                    'scope': 'one owned window; never the desktop or a region',
                    'steps': []}
    process = None
    try:
        with tempfile.TemporaryDirectory() as work:
            app = Path(work) / 'NLC Menu Fixture.app'
            binary = app / 'Contents/MacOS/NativeMenuFixture'
            binary.parent.mkdir(parents=True)
            (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({
                'CFBundleIdentifier': TARGET, 'CFBundleExecutable': binary.name,
                'CFBundleName': 'NLC Menu Fixture', 'CFBundlePackageType': 'APPL',
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
            # Name the window. Reading the fixture's accessibility tree makes
            # AppKit materialise a second one — an AXDialog titled "Window",
            # the menu carrier — so by capture time the app has two and the
            # helper refuses to guess between them. That refusal is right: the
            # caller is the only one who knows which window it meant, and an
            # index picked by position would silently follow whichever came
            # first that run.
            index = _standard_window_index(driver, TARGET)
            report['window_index'] = index
            shot = driver.capture(TARGET, obs_id=observation['obs_id'],
                                  window_index=index, path=str(destination))
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
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                          encoding='utf-8')
    return report
