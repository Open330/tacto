"""Explicit one-shot foreground Unity diagnostic, never an AX fallback."""
import argparse
import json
import math
from pathlib import Path
from .flow import Selector
from .native_visual_actions import capture_window

TARGET = 'net.pixiv.vroid.macosx'


def main(argv, driver):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--helper', action='store_true', required=True)
    parser.add_argument('--yes', action='store_true')
    parser.add_argument('--title', required=True)
    parser.add_argument('--from-pixels', nargs=2, type=float, required=True)
    parser.add_argument('--to-pixels', nargs=2, type=float, required=True)
    parser.add_argument('--via-pixels', nargs=2, type=float, action='append', default=[],
                        help='Intermediate canvas point; repeat up to 12 times in stroke order')
    parser.add_argument('--graph-bounds-pixels', nargs=4, type=float,
                        help='Observed curve graph rectangle x y width height; confines drag to this right-panel graph')
    parser.add_argument('--duration', type=float, default=1.5)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if (not args.yes or not all(math.isfinite(v) for v in
            [*args.from_pixels, *args.to_pixels, args.duration,
             *(v for pair in args.via_pixels for v in pair)])
            or len(args.via_pixels) > 12
            or not 0.5 <= args.duration <= 2 or '.vroid' not in args.title):
        parser.error('explicit --yes, native document title and bounded finite drag required')
    if 'action.vroid_drag' not in driver.capabilities:
        print(json.dumps({'error':'vroid_drag_capability_missing','effect':'not_started'}))
        return 1
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    obs = driver.observe(TARGET, 'tree_full')
    if obs.get('type') != 'observation':
        print(json.dumps(obs)); return 1
    indices = Selector(role='Window', title=args.title).resolve_all(obs['tree'])
    if len(indices) != 1:
        print(json.dumps({'error':'exact_window_missing_or_ambiguous','effect':'not_started'})); return 1
    index = indices[0]
    before = capture_window(driver, obs['obs_id'], TARGET, window_index=index)
    before.save(args.output / 'before.png')
    width, height = before.viewport['width'], before.viewport['height']
    frame = before.window_frame
    def point(values):
        x, y = values
        if not 0 <= x < width or not 0 <= y < height:
            raise ValueError('pixel point outside exact capture')
        return {'x':x * frame[2] / width, 'y':y * frame[3] / height}
    action = dict(name='vroid_drag', element_index=index, title=args.title,
                  duration=args.duration, **{'from':point(args.from_pixels), 'to':point(args.to_pixels)})
    if args.graph_bounds_pixels:
        gx, gy, gw, gh = args.graph_bounds_pixels
        if not all(math.isfinite(v) for v in (gx, gy, gw, gh)) or gw <= 0 or gh <= 0:
            parser.error('finite positive graph bounds required')
        origin = point((gx, gy))
        point((gx + gw, gy + gh))
        action['graph_bounds'] = dict(**origin, width=gw * frame[2] / width,
                                     height=gh * frame[3] / height)
    if args.via_pixels:
        action['via'] = [point(pair) for pair in args.via_pixels]
    # Record intent before input; never retry a partial or unknown dispatch.
    (args.output / 'request.json').write_text(json.dumps(action, indent=2))
    reply = driver.act(obs['obs_id'], TARGET, action, risk_class='preapproved',
                       intent_text='User-authorized VRoid curve graph drag' if args.graph_bounds_pixels else 'User-authorized VRoid hair canvas drag', approved=True)
    (args.output / 'reply.json').write_text(json.dumps(reply, indent=2))
    fresh = driver.observe(TARGET, 'tree_full')
    if fresh.get('type') == 'observation':
        found = Selector(role='Window', title=args.title).resolve_all(fresh['tree'])
        if not found and not args.title.endswith('*'):
            found = Selector(role='Window', title=args.title + '*').resolve_all(fresh['tree'])
        if len(found) == 1:
            after = capture_window(driver, fresh['obs_id'], TARGET, window_index=found[0])
            after.save(args.output / 'after.png')
    print(json.dumps(reply))
    return 0 if reply.get('type') == 'acted' else 1
