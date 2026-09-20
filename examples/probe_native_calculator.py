"""Opt-in real-model Calculator loop; run only with exclusive native UI access.

Resets Calculator to zero, then asks the model to calculate 17 * 23 through
observed candidates. This is a native-driver screen-loop probe, not CLI task
routing or a signed-helper transport benchmark. It changes Calculator's state.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider', choices=['codex', 'claude'], required=True)
    args = parser.parse_args()
    from nlc.ask import what_to_do_here
    from nlc.authoring import _ask_model, _extract_json
    from nlc.driver import Driver
    from nlc.flow import Postcondition, Selector, replay
    from nlc.general import run
    from nlc.library import calc_arith

    def snapshot():
        paths = sorted((ROOT / 'nlc').glob('*.py')) + [Path(__file__)]
        return hashlib.sha256(b''.join(str(p.relative_to(ROOT)).encode() +
            hashlib.sha256(p.read_bytes()).digest() for p in paths)).hexdigest()
    before = snapshot()
    subprocess.run(['open', '-a', 'Calculator'], check=True, timeout=10)
    driver = Driver()
    setup = replay(driver, calc_arith().bind(a='0', b='0', op='+'))
    if not setup.ok:
        print(json.dumps({'passed': False, 'stage': 'setup', 'log': setup.log}, ensure_ascii=False))
        return 1
    plans = []
    def planner(goal, candidates, done):
        question = what_to_do_here(goal, candidates, done)
        raw = _extract_json(_ask_model(question.prompt, args.provider, timeout=45, expect_json=True))
        answer = question.check(raw) if isinstance(raw, dict) else None
        plans.append({'accepted': answer is not None,
                      'actions': [dict(do=a['do'], label=a['candidate']['label'])
                                  for a in (answer or {}).get('actions', [])],
                      'done': (answer or {}).get('done', False)})
        return answer
    start = time.monotonic()
    result = run(driver, 'com.apple.calculator',
                 '계산기에서 17 곱하기 23을 계산하고 화면에 표시된 결과를 읽어 알려주세요.',
                 planner, max_screens=12, timeout_seconds=150)
    # 'tree_full', not the default 'tree_diff': the loop has already observed
    # this screen, so a diff of an unchanged screen is the literal string
    # '(no change)' and the postcondition below would read that instead of
    # the display. It fails precisely when the model got it right.
    observation = driver.observe('com.apple.calculator', 'tree_full')
    correct = observation.get('type') == 'observation' and Postcondition(
        field=Selector(role='ScrollArea', identifier='StandardInputView'),
        equals='391', attribute='Value').check(observation['tree'])
    after = snapshot()
    passed = result.ok and correct and any('391' in s for s in result.said) and before == after
    print(json.dumps(dict(passed=passed, provider=args.provider, loop_ok=result.ok,
        display_verified=correct, said=result.said, why=result.why, plans=plans,
        seconds=round(time.monotonic()-start, 2), source_before=before,
        source_after=after, source_stable=before == after), ensure_ascii=False))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
