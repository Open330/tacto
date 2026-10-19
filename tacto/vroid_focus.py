"""Explicit VRoid foreground preparation; no mouse/key or model edits."""
import argparse
import json
from pathlib import Path
from .flow import Selector
from .native_visual_actions import capture_window

TARGET = 'net.pixiv.vroid.macosx'


def main(argv, driver):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--helper', action='store_true', required=True)
    parser.add_argument('--yes', action='store_true', required=True)
    parser.add_argument('--title', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if 'action.vroid_focus' not in driver.capabilities:
        print(json.dumps({'error': 'vroid_focus_capability_missing', 'effect': 'not_started'}))
        return 1
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    obs = driver.observe(TARGET, 'tree_full')
    if obs.get('type') != 'observation':
        print(json.dumps(obs))
        return 1
    indices = Selector(role='Window', title=args.title).resolve_all(obs['tree'])
    if len(indices) != 1:
        print(json.dumps({'error': 'exact_window_missing_or_ambiguous', 'effect': 'not_started'}))
        return 1
    capture_window(driver, obs['obs_id'], TARGET, window_index=indices[0]).save(args.output / 'before.png')
    reply = driver.act(obs['obs_id'], TARGET,
                       dict(name='vroid_focus', element_index=indices[0], title=args.title),
                       risk_class='preapproved', approved=True,
                       intent_text='User-authorized VRoid foreground recovery')
    (args.output / 'reply.json').write_text(json.dumps(reply, indent=2))
    fresh = driver.observe(TARGET, 'tree_full')
    if fresh.get('type') == 'observation':
        found = Selector(role='Window', title=args.title).resolve_all(fresh['tree'])
        if len(found) == 1:
            capture_window(driver, fresh['obs_id'], TARGET, window_index=found[0]).save(args.output / 'after.png')
    print(json.dumps(reply))
    return 0 if reply.get('type') == 'acted' else 1
