"""Hover: move the global pointer into an observed window, no buttons, and back.

Window-bound clicks and drags leave the person's cursor alone. An app that
reacts to where the real cursor is (a hover effect, a character that avoids the
cursor) needs the cursor itself. This moves it along the requested points,
holds it, optionally captures the window while it is still there, and puts it
back where it was. Moving the mouse during the hover ends it and leaves the
cursor where the person put it.
"""
import argparse
import json
import math
import os
import re
import shutil
from pathlib import Path

# The helper allows five seconds for one input action; restoring and an
# optional capture need part of that.
_BUDGET = 4.2
_CAPTURE_SECONDS = 1.3


def _finite_number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def hover_problem(action):
    required = {'to', 'duration', 'hold'}
    if not required <= action.keys() or action.keys() - (required | {'name', 'element_index', 'via', 'capture'}):
        return 'hover requires to, duration, hold; optional via, capture'
    for key in ('duration', 'hold'):
        if not _finite_number(action[key]) or not 0 <= action[key] <= 2:
            return f'{key} must be 0 to 2 seconds'
    if 'capture' in action and type(action['capture']) is not bool:
        return 'capture must be true or false'
    if action['duration'] + action['hold'] + (_CAPTURE_SECONDS if action.get('capture') else 0) > _BUDGET:
        return f'duration + hold must fit in {_BUDGET - _CAPTURE_SECONDS:g} seconds with --capture, {_BUDGET:g} without'
    via = action.get('via', [])
    if not isinstance(via, list) or len(via) > 12:
        return 'via must contain at most 12 points'
    for point in [*via, action['to']]:
        if (not isinstance(point, dict) or set(point) != {'x', 'y'} or any(
                not _finite_number(v) or v < 0 for v in point.values())):
            return 'each point must be finite nonnegative {x,y} window points'
    return None


def _copy_capture(capture, output):
    """Copy the helper-owned PNG to a new private file; never overwrite."""
    source = capture.get('path') if isinstance(capture, dict) else None
    if capture.get('type') != 'capture' or not isinstance(source, str):
        return {'saved': False, 'error': capture.get('error', 'capture_failed') if isinstance(capture, dict) else 'capture_failed'}
    with open(source, 'rb') as stream, output.open('xb') as target:
        os.fchmod(target.fileno(), 0o600)
        shutil.copyfileobj(stream, target)
    frame = capture.get('window_frame')
    result = {'saved': True, 'path': str(output)}
    if isinstance(frame, list) and len(frame) == 4 and capture.get('width'):
        result['pixel_to_point'] = {'x': frame[2] / capture['width'], 'y': frame[3] / capture['height']}
    return result


def _main(argv, driver):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('target', help='application bundle ID')
    parser.add_argument('--helper', action='store_true', required=True)
    parser.add_argument('--window', help='exact observed window title; required when multiple windows exist')
    parser.add_argument('--to', nargs=2, type=float, required=True, metavar=('X', 'Y'),
                        help='window point (top-left origin, points) where the cursor stops')
    parser.add_argument('--via', nargs=2, type=float, action='append', default=[], metavar=('X', 'Y'),
                        help='window points passed through first, in order (up to 12)')
    parser.add_argument('--duration', type=float, default=0.3, help='seconds to travel the path (0 jumps)')
    parser.add_argument('--hold', type=float, default=1.0, help='seconds to keep the cursor at --to')
    parser.add_argument('--capture', type=Path, help='new PNG path: the window, captured before the cursor goes back')
    parser.add_argument('--json', action='store_true', help='include the fresh observation after the hover')
    args = parser.parse_args(argv)
    action = {'name': 'hover', 'to': dict(zip(('x', 'y'), args.to)),
              'duration': args.duration, 'hold': args.hold}
    if args.via:
        action['via'] = [dict(zip(('x', 'y'), point)) for point in args.via]
    if args.capture:
        if args.capture.exists() or args.capture.is_symlink():
            parser.error('--capture must name a new file')
        action['capture'] = True
    problem = hover_problem(action)
    if problem:
        parser.error(problem)
    if 'action.hover' not in getattr(driver, 'capabilities', ()):
        print(json.dumps({'error': 'hover_unavailable', 'effect': 'not_started', 'detail': 'update the signed helper'}))
        return 1
    from .flow import Selector
    obs = driver.observe(args.target, 'tree_full')
    if obs.get('type') != 'observation':
        print(json.dumps(obs, ensure_ascii=False)); return 1
    indices = (Selector(role='Window', title=args.window).resolve_all(obs['tree']) if args.window else
               [int(match.group(1)) for line in obs['tree'].splitlines()
                if (match := re.match(r'^(\d+) Window(?:\s|$)', line))])
    if len(indices) != 1:
        print(json.dumps({'error': 'window_missing_or_ambiguous', 'effect': 'not_started',
                          'detail': 'specify --window with an exact observed title'}))
        return 1
    action['element_index'] = indices[0]
    reply = driver.act(obs['obs_id'], args.target, action, risk_class='preapproved', approved=True,
                       intent_text='Requested pointer hover')
    output = {k: v for k, v in reply.items() if k != 'hold_capture'}
    if args.capture and isinstance(reply.get('hold_capture'), dict):
        try:
            output['capture'] = _copy_capture(reply['hold_capture'], args.capture)
        except OSError as error:
            output['capture'] = {'saved': False, 'error': type(error).__name__}
    # Moving the cursor proves nothing about what the app did with it.
    if reply.get('type') == 'acted' or reply.get('effect') == 'unknown':
        fresh = driver.observe(args.target, 'tree_full')
        if args.json:
            output['observation'] = fresh
        else:
            output['observed'] = fresh.get('type') == 'observation'
    output['verification'] = 'compare window_frames or the capture; moving the cursor alone proves no effect'
    print(json.dumps(output, ensure_ascii=False))
    return 0 if reply.get('type') == 'acted' else 1


def main(argv, driver):
    try:
        return _main(argv, driver)
    except (ConnectionError, OSError, TimeoutError, RuntimeError) as error:
        # The cursor may have moved before the connection went; say so.
        print(json.dumps({'error': 'hover_connection_failed', 'effect': 'unknown',
                          'exception': type(error).__name__, 'retry': False}))
        return 1
