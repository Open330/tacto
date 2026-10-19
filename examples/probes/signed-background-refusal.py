"""Signed transport: the two halves of the background contract.

Runs under the signed launcher so the helper accepts the connection. Launches
one owned accessory fixture (LSUIElement, orders its window back) and checks
both halves against it:

  press_key       general navigation is unsupported, so it is REFUSED with
                  not_started. This test does
                  not request the separate background window preparation path.

  click           an accessibility press does NOT need the front, so it still
                  WORKS on the background app — measured by the fixture's own
                  counter — while isActive stays false.

The second half is why the first is affordable: refusing window-bound input
does not cost the ordinary path.

Terminates only the child it launched.
"""
from __future__ import annotations

import json
import plistlib
import subprocess
import tempfile
import time
from pathlib import Path

TARGET = 'ai.callabo.nlc.menu-fixture'


def main(driver, output: Path) -> dict:
    from tacto.driver import Driver, _attr
    from ApplicationServices import AXUIElementCreateApplication

    root = Path(__file__).resolve().parents[2]
    source = (root / 'tests/fixtures/NativeMenuFixture.swift').read_bytes()
    local = Driver()
    assert local._resolve(TARGET, launch=False) is None, 'fixture already running'

    report: dict = {'transport': type(driver).__name__, 'target': TARGET,
                    'expect': 'window-bound refused; accessibility press still works',
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
            process = subprocess.Popen([str(binary), '--timeout', '90'],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            report['fixture_pid'] = process.pid
            deadline = time.monotonic() + 20
            while local._resolve(TARGET, launch=False) != process.pid:
                assert time.monotonic() < deadline and process.poll() is None, 'fixture did not register'
                time.sleep(.05)
            time.sleep(.6)

            element = AXUIElementCreateApplication(process.pid)

            def state():
                def find(node, ident, depth=0):
                    if depth > 20:
                        return None
                    if _attr(node, 'AXIdentifier') == ident:
                        return node
                    for child in (_attr(node, 'AXChildren') or [])[:200]:
                        hit = find(child, ident, depth + 1)
                        if hit is not None:
                            return hit
                    return None
                node = find(element, 'nlc-fixture-state')
                counter = find(element, 'nlc-fixture-make-key-count')
                return {'state': _attr(node, 'AXValue') if node else None,
                        'make_key_count': _attr(counter, 'AXValue') if counter else None,
                        'app_ax_frontmost': _attr(element, 'AXFrontmost')}

            report['before'] = state()
            observation = driver.observe(TARGET, 'tree_full')
            assert observation.get('type') == 'observation', observation
            window = button = None
            for line in observation['tree'].splitlines():
                parts = line.strip().split()
                if len(parts) < 2:
                    continue
                if parts[1] == 'Window' and window is None:
                    window = int(parts[0])
                if 'nlc-fixture-make-key' in line and 'count' not in line and button is None:
                    button = int(parts[0])
            assert window is not None and button is not None, (window, button)

            # General navigation remains unsupported and sends no input.
            answer = driver.act(observation['obs_id'], TARGET,
                                {'name': 'press_key', 'key': 'Tab'})
            after = state()
            report['steps'].append({'action': 'press_key', 'response': answer,
                                    'after': after, 'expected': 'refused'})
            assert answer.get('type') == 'error', answer
            assert answer.get('effect') == 'not_started', answer
            assert 'isActive=false' in (after['state'] or ''), after
            assert after['app_ax_frontmost'] is not True, after
            assert after['make_key_count'] == report['before']['make_key_count'], after

            # Half two: the accessibility press still reaches a background app.
            answer = driver.act(observation['obs_id'], TARGET,
                                {'name': 'click', 'element_index': button})
            time.sleep(.5)
            after = state()
            report['steps'].append({'action': 'click', 'response': answer,
                                    'after': after, 'expected': 'acted via AXPress'})
            assert answer.get('type') == 'acted', answer
            assert after['make_key_count'] != report['before']['make_key_count'], after
            # Pressed, ran, and the app is still not active: no activation was
            # needed and none was taken.
            assert 'isActive=false' in (after['state'] or ''), after
            assert after['app_ax_frontmost'] is not True, after
            report['passed'] = True
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        report['fixture_exit'] = None if process is None else process.poll()
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report
