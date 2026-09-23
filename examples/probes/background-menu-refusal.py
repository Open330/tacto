"""What a background app can be told to do through its menus, and what it cannot.

The measurement that settled the suite's focus-stealing, kept as a check.

macOS validates a menu item dispatched through the RESPONDER CHAIN against the
key window. A background app has no key window and cannot be given one —
`makeKey()` was called on this very fixture, its counter moved, `isKeyWindow`
stayed false. So those commands are unreachable from the background, by design,
and the driver is right to refuse them with `needs_foreground` and hand them to
a person.

An item with a DIRECT target has no such dependency and works from the
background, which is why "a background app's menus are disabled" was the wrong
rule and sent the suite reaching for focus it did not need.

This takes no focus. The fixture opens behind everything and is never
activated; both facts are checked, not assumed.
"""
import plistlib
import subprocess
import tempfile
import time
from pathlib import Path

TARGET = 'ai.callabo.nlc.menu-fixture'
ROOT = Path(__file__).resolve().parents[2]


def _build(work):
    app = Path(work) / 'NLC Menu Fixture.app'
    binary = app / 'Contents/MacOS/NativeMenuFixture'
    binary.parent.mkdir(parents=True, exist_ok=True)
    (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({
        'CFBundleIdentifier': TARGET, 'CFBundleExecutable': binary.name,
        'CFBundleName': 'NLC Menu Fixture', 'CFBundlePackageType': 'APPL',
        'CFBundleVersion': '1', 'LSUIElement': True,
    }))
    sdks = sorted(Path('/Library/Developer/CommandLineTools/SDKs').glob('MacOSX*.sdk'))
    if not sdks:
        raise RuntimeError('no_macos_sdk')
    build = subprocess.run(
        ['/Library/Developer/CommandLineTools/usr/bin/swiftc', '-parse-as-library',
         '-sdk', str(sdks[-1]),
         str(ROOT / 'tests/fixtures/NativeMenuFixture.swift'), '-o', str(binary)],
        capture_output=True, text=True)
    if build.returncode != 0:
        raise RuntimeError('fixture_compile_failed: ' + build.stderr.strip()[:200])
    return binary


def _witness(pid, identifier):
    from ApplicationServices import AXUIElementCreateApplication
    from nlc.driver import _attr
    found = []

    def walk(node, depth=0):
        if depth > 8 or found:
            return
        if _attr(node, 'AXIdentifier') == identifier:
            found.append(node)
            return
        for child in (_attr(node, 'AXChildren') or [])[:60]:
            walk(child, depth + 1)

    app = AXUIElementCreateApplication(pid)
    for window in (_attr(app, 'AXWindows') or []):
        walk(window)
    return _attr(found[0], 'AXValue') if found else None


def _frontmost():
    from AppKit import NSWorkspace
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    return app.bundleIdentifier() if app is not None else None


def main(driver, output: Path):
    from nlc.driver import Driver

    report = {'target': TARGET, 'steps': [], 'passed': False,
              'what_this_proves': 'a direct-target menu command works from the '
                                  'background; a responder-chain one is refused, '
                                  'not forced'}
    process = None
    work = tempfile.TemporaryDirectory(prefix='nlc-menu-')
    try:
        binary = _build(work.name)
        process = subprocess.Popen([str(binary), '--timeout', '90'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        probe = Driver()
        pid = None
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            probe._pump()
            pid = probe._resolve(TARGET, launch=False)
            if pid is not None and _witness(pid, 'nlc-fixture-state') is not None:
                break
            time.sleep(0.2)
        if pid is None or _witness(pid, 'nlc-fixture-state') is None:
            raise RuntimeError('fixture_did_not_start')
        report['fixture_pid'] = pid

        # The premise, checked rather than assumed: this must be a BACKGROUND
        # app throughout, or the whole measurement means nothing.
        front_before = _frontmost()
        if front_before is None:
            raise RuntimeError('frontmost application is not observable')
        if front_before == TARGET:
            raise RuntimeError('fixture_is_frontmost_before_we_started')
        state = _witness(pid, 'nlc-fixture-state') or ''
        report['steps'].append({'step': 'fixture is in the background',
                                'frontmost': front_before, 'state': state})
        if 'iskeywindow=true' in state.lower().replace(' ', ''):
            raise RuntimeError(f'fixture holds the key window: {state}')

        # What the two menu items report before anything is asked of them.
        direct_state = driver.menu_state(TARGET, ['Probe', 'Direct Command'])
        responder_state = driver.menu_state(TARGET, ['Probe', 'Responder Command'])
        report['steps'].append({'step': 'what the menus say',
                                'direct_enabled': direct_state.get('enabled'),
                                'responder_enabled': responder_state.get('enabled')})
        if direct_state.get('enabled') is not True:
            raise RuntimeError(f'direct target should be enabled: {direct_state}')
        if responder_state.get('enabled') is not False:
            raise RuntimeError(f'responder target should be disabled: {responder_state}')

        # The direct one works, from the background, and its own counter says so.
        before = _witness(pid, 'nlc-fixture-direct-count')
        if before is None:
            raise RuntimeError('direct counter is missing')
        observation = driver.observe(TARGET, 'tree_full')
        acted = driver.act(observation['obs_id'], TARGET,
                           {'name': 'menu', 'path': ['Probe', 'Direct Command']})
        time.sleep(0.5)
        after = _witness(pid, 'nlc-fixture-direct-count')
        report['steps'].append({'step': 'direct command from the background',
                                'response': acted, 'count': [before, after]})
        if acted.get('type') != 'acted':
            raise RuntimeError(f'direct command refused: {acted}')
        if after is None or int(after) != int(before) + 1:
            raise RuntimeError(f'direct command must increment exactly once: {before}->{after}')

        # The responder one is REFUSED — and nothing happens.
        before = _witness(pid, 'nlc-fixture-responder-count')
        if before is None:
            raise RuntimeError('responder counter is missing')
        observation = driver.observe(TARGET, 'tree_full')
        refused = driver.act(observation['obs_id'], TARGET,
                             {'name': 'menu', 'path': ['Probe', 'Responder Command']})
        time.sleep(0.5)
        after = _witness(pid, 'nlc-fixture-responder-count')
        report['steps'].append({'step': 'responder command from the background',
                                'response': refused, 'count': [before, after]})
        if refused.get('type') != 'error':
            raise RuntimeError(f'a command needing the key window went through: {refused}')
        if after != before:
            raise RuntimeError(f'refused and ran anyway: {before}->{after}')

        # Refusing is not enough: it has to be refused for the RIGHT reason, or
        # a caller cannot know to hand it to a person.
        report['refusal'] = {'error': refused.get('error'),
                             'effect': refused.get('effect'),
                             'needs_foreground': refused.get('needs_foreground'),
                             'detail': str(refused.get('detail', ''))[:160]}
        if refused.get('effect') != 'not_started':
            raise RuntimeError(f'a refusal must say nothing started: {refused}')
        if refused.get('needs_foreground') is not True:
            raise RuntimeError(f'a refusal must explain the required handoff: {refused}')

        # And none of it cost anyone their screen.
        front_after = _frontmost()
        report['frontmost'] = [front_before, front_after]
        if front_after != front_before:
            raise RuntimeError(f'focus moved: {front_before} -> {front_after}')
        report['passed'] = True
        return report
    except Exception as e:
        report['error'] = f'{type(e).__name__}: {e}'
        return report
    finally:
        try:
            from nlc.evaluation import write_report
            write_report(output, report)
        finally:
            if process is not None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            work.cleanup()
