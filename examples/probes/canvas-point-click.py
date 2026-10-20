"""Capture, pick a coordinate off the picture, click it — end to end.

This is the loop `point` exists for, on a surface where accessibility genuinely
cannot help. The fixture's canvas is one opaque AXGroup with no children, so an
observation can see that something is there and nothing about the three targets
painted on it. The only way to reach them is to look.

Every step is checked against something the driver does not write:

  * the observation must NOT contain the targets — otherwise the fixture is not
    testing what it claims and an index would have done;
  * the target is located in the CAPTURED IMAGE, by its colour, not from any
    knowledge of where it was drawn;
  * the hit is read back from a label the view's own mouseDown handler writes,
    together with a counter, so "the value looks right" cannot pass for
    "the click happened".

Nothing is synthesised: the point is resolved to the real element under it and
that element is pressed.
"""
import json
import subprocess
import time
from pathlib import Path

TARGET = 'ai.callabo.tacto.canvas-fixture'
ROOT = Path(__file__).resolve().parents[2]


def _fixture_app(work):
    """Build the fixture into its own bundle and return the executable."""
    import plistlib
    app = Path(work) / 'TACTO Canvas Fixture.app'
    binary = app / 'Contents/MacOS/NativeCanvasFixture'
    binary.parent.mkdir(parents=True, exist_ok=True)
    (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({
        'CFBundleIdentifier': TARGET,
        'CFBundleExecutable': binary.name,
        'CFBundleName': 'TACTO Canvas Fixture',
        'CFBundlePackageType': 'APPL',
        'CFBundleVersion': '1',
        'LSUIElement': True,
    }))
    sdks = sorted(Path('/Library/Developer/CommandLineTools/SDKs').glob('MacOSX*.sdk'))
    if not sdks:
        raise RuntimeError('no_macos_sdk')
    build = subprocess.run(
        ['/Library/Developer/CommandLineTools/usr/bin/swiftc', '-parse-as-library',
         '-sdk', str(sdks[-1]),
         str(ROOT / 'tests/fixtures/NativeCanvasFixture.swift'), '-o', str(binary)],
        capture_output=True, text=True)
    if build.returncode != 0:
        raise RuntimeError('fixture_compile_failed: ' + build.stderr.strip()[:200])
    return binary


def _label(pid, identifier):
    """Read a witness label straight from AX, never from the driver's reply."""
    from ApplicationServices import AXUIElementCreateApplication
    from tacto.driver import _attr
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


def _find_colour(png_path, wanted, tolerance=40):
    """Where in the image is that colour? Returns the blob's centre in PIXELS.

    Read out of the picture, with no knowledge of where the fixture drew it.
    """
    import Quartz
    import CoreFoundation
    raw = str(png_path).encode()
    url = CoreFoundation.CFURLCreateFromFileSystemRepresentation(None, raw, len(raw), False)
    source = Quartz.CGImageSourceCreateWithURL(url, None)
    image = Quartz.CGImageSourceCreateImageAtIndex(source, 0, None)
    width, height = Quartz.CGImageGetWidth(image), Quartz.CGImageGetHeight(image)
    provider = Quartz.CGImageGetDataProvider(image)
    data = Quartz.CGDataProviderCopyData(provider)
    pixels = bytes(data)
    stride = Quartz.CGImageGetBytesPerRow(image)
    per_pixel = Quartz.CGImageGetBitsPerPixel(image) // 8

    # Byte order from the image itself. The capture path once produced BGRA
    # and now produces RGBA (2026-10-07); assuming either read red as blue.
    little = (Quartz.CGImageGetBitmapInfo(image) & Quartz.kCGBitmapByteOrderMask) \
        == Quartz.kCGBitmapByteOrder32Little
    # Which channel DOMINATES, not exact values: colour-space conversion moved
    # systemGreen (52,199,89) to (101,196,102) in the same capture.
    channel = max(range(3), key=lambda i: wanted[i])
    xs, ys = [], []
    step = max(1, min(width, height) // 240)
    for y in range(0, height, step):
        row = y * stride
        for x in range(0, width, step):
            offset = row + x * per_pixel
            if offset + 3 > len(pixels):
                continue
            if little:
                b, g, r = pixels[offset], pixels[offset + 1], pixels[offset + 2]
            else:
                r, g, b = pixels[offset], pixels[offset + 1], pixels[offset + 2]
            rgb = (r, g, b)
            others = [rgb[i] for i in range(3) if i != channel]
            if (abs(rgb[channel] - wanted[channel]) <= tolerance
                    and all(rgb[channel] - o >= 60 for o in others)):
                xs.append(x)
                ys.append(y)
    if not xs:
        return None
    return (sum(xs) / len(xs), sum(ys) / len(ys)), (width, height)


def main(driver, output: Path):
    from tacto.input_audit_process import ProcessInputAudit
    audit = ProcessInputAudit(interval=.005, startup_timeout=10, stop_timeout=3)
    try:
        with audit:
            report = _capture_and_click(driver, output)
    except Exception as exc:
        report = {'passed': False, 'error': type(exc).__name__ + ': ' + str(exc)}
    report['input_audit'] = audit.report()
    if report['input_audit']['status'] != 'UNCHANGED_SAMPLED':
        report['passed'] = False
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def _capture_and_click(driver, output: Path):
    import tempfile
    import shutil
    from tacto.driver import Driver, _attr, _elem_frame
    from ApplicationServices import AXUIElementCreateApplication

    report = {'target': TARGET, 'steps': [], 'passed': False,
              'what_this_proves': 'a coordinate read off a capture reaches a '
                                  'control accessibility cannot describe'}
    process = None
    witness = None
    work = tempfile.mkdtemp(prefix='tacto-canvas-')
    try:
        if Driver()._resolve(TARGET, launch=False) is not None:
            raise RuntimeError('fixture already running; cannot claim ownership')
        binary = _fixture_app(work)
        process = subprocess.Popen([str(binary), '--timeout', '120'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        probe = Driver()
        deadline = time.monotonic() + 15
        pid = None
        while time.monotonic() < deadline:
            probe._pump()
            pid = probe._resolve(TARGET, launch=False)
            if pid is not None and _label(pid, 'tacto-canvas-hit') is not None:
                break
            time.sleep(0.2)
        if pid != process.pid:
            raise RuntimeError('fixture_did_not_start')
        report['fixture_pid'] = pid
        # Armed before observe, capture and the click: the frontmost app at
        # the end says nothing about a moment in the middle.
        witness = _witness_focus()

        observation = driver.observe(TARGET, 'tree_full')
        if observation.get('type') != 'observation':
            raise RuntimeError('observe_failed: ' + str(observation.get('error')))
        tree = observation['tree']
        report['steps'].append({'step': 'observe',
                                'element_count': observation.get('element_count')})

        # The premise, checked rather than assumed. If accessibility could see
        # the targets, an element_index would reach them and this proves nothing.
        for name in ('red', 'green', 'blue'):
            if name in tree.lower():
                raise RuntimeError(f'fixture_is_not_opaque: tree mentions {name}')
        report['steps'].append({'step': 'targets are invisible to accessibility',
                                'checked': ['red', 'green', 'blue']})

        shot_path = output.parent / f'{output.stem}.png'
        shot = driver.capture(TARGET, obs_id=observation['obs_id'],
                              path=str(shot_path))
        report['steps'].append({'step': 'capture', 'response': shot})
        if shot.get('type') != 'capture':
            raise RuntimeError('capture_failed: ' + str(shot.get('detail') or shot.get('error')))

        found = _find_colour(shot_path, (52, 199, 89))      # systemGreen
        if found is None:
            raise RuntimeError('green_target_not_found_in_image')
        (px, py), (image_w, image_h) = found
        viewport = shot['viewport']
        # Image pixels -> window points, using the scale the capture reports.
        scale_x = image_w / viewport['width']
        scale_y = image_h / viewport['height']
        wx, wy = px / scale_x, py / scale_y
        report['steps'].append({'step': 'located in the picture',
                                'image_px': [round(px, 1), round(py, 1)],
                                'scale': [round(scale_x, 2), round(scale_y, 2)],
                                'window_point': [round(wx, 1), round(wy, 1)]})

        before_hit = _label(pid, 'tacto-canvas-hit')
        before_count = _label(pid, 'tacto-canvas-count')
        frontmost_before = _frontmost_bundle()

        observation = driver.observe(TARGET, 'tree_full')
        clicked = driver.act(observation['obs_id'], TARGET,
                             {'name': 'click', 'point': {'x': wx, 'y': wy}})
        report['steps'].append({'step': 'click by point', 'response': clicked})
        if clicked.get('type') != 'acted':
            raise RuntimeError('click_failed: ' + str(clicked.get('detail') or clicked.get('error')))

        after_hit = _label(pid, 'tacto-canvas-hit')
        after_count = _label(pid, 'tacto-canvas-count')
        report['witness'] = {'hit': [before_hit, after_hit],
                             'count': [before_count, after_count]}
        if after_hit != 'green':
            raise RuntimeError(f'wrong_target_hit: {after_hit!r}')
        if after_count == before_count:
            raise RuntimeError('the label moved but the handler never ran')

        # And it cost nobody their focus — at the end or for a moment between.
        report['frontmost'] = [frontmost_before, _frontmost_bundle()]
        witness.__exit__(None, None, None)
        report['activations'] = witness.summary()['activations']
        witness = None
        if _frontmost_bundle() == TARGET:
            raise RuntimeError('the fixture came to the front')
        if report['activations']:
            raise RuntimeError(f'focus moved during the run: {report["activations"]}')
        report['passed'] = True
        return report
    except Exception as e:                              # reported, never hidden
        report['error'] = f'{type(e).__name__}: {e}'
        return report
    finally:
        if witness is not None:
            witness.__exit__(None, None, None)
        if process is not None:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        shutil.rmtree(work)


def _witness_focus():
    """Start a focus witness from the probes directory, wherever we run from."""
    import sys
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    from focus_witness import FocusWitness
    return FocusWitness().__enter__()


def _frontmost_bundle():
    from AppKit import NSWorkspace
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    return app.bundleIdentifier() if app is not None else None
