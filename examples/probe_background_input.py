"""Opt-in Calculator background-input probe, with independent process sampling.

Requires an already running, background Calculator at 0 in Basic mode. It never
launches/activates apps. Changes Calculator to 7 then 0 and Scientific then Basic.
A failed step stops immediately; no hidden cleanup or foreground restoration.
Run the signed path with: ./launcher/nlc eval background_input_preserves_focus
"""
from pathlib import Path
import argparse
import hashlib
import json
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TARGET = 'com.apple.calculator'


def run_probe(driver, *, transport, require_selection=False):
    from nlc.driver import Driver, _attr, AXUIElementCreateApplication
    from nlc.flow import Selector, Postcondition
    from nlc.input_audit_process import ProcessInputAudit

    if type(require_selection) is not bool:
        raise ValueError('require_selection must be bool')
    report = dict(passed=False, transport=transport, target=TARGET, steps=[],
                  selection_observation_required=require_selection,
                  measurement='sampled preservation AND independent target readback',
                  proves_no_intermediate_change=False)
    paths = ['examples/probe_background_input.py', 'nlc/driver.py', 'helper/NLCHelper.swift', 'helper/nlc-helper',
             'nlc/input_audit.py', 'nlc/input_audit_process.py']
    report['source_hashes'] = {p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest()
                               for p in paths if (ROOT/p).is_file()}
    try:
        # Resolve without launch, even before the first observation.
        pid = Driver()._resolve(TARGET, launch=False)
        if pid is None:
            raise RuntimeError('fixture_not_running')
        app = AXUIElementCreateApplication(pid)
        def background():
            if _attr(app, 'AXFrontmost') is not False:
                raise RuntimeError('target_not_observably_background')
        def observe():
            background()
            o = driver.observe(TARGET, 'tree_full')
            if o.get('type') != 'observation':
                raise RuntimeError('observation_failed:' + str(o.get('error')))
            return o
        def mode(tree):
            matches = re.findall(r'Identifier: Mode: ([^;\n]+);', tree)
            return matches[0] if len(matches) == 1 else None
        def display(tree, value):
            return Postcondition(field=Selector(role='ScrollArea',identifier='StandardInputView'),
                                 attribute='Value',equals=value).check(tree)
        before = observe()
        if mode(before['tree']) != 'basic' or not display(before['tree'], '0'):
            raise RuntimeError('fixture_requires_zero_and_basic_mode')
        report['initial'] = {'display':'0','mode':'basic','target_background':True}
        specifications = [
            ('digit_seven', {'name':'click'}, 'Seven', lambda tree: display(tree,'7')),
            ('clear_own_digit', {'name':'click'}, ['Clear','AllClear'], lambda tree: display(tree,'0')),
            ('scientific_mode', {'name':'menu','path':['View','Scientific']}, None,
             lambda tree: mode(tree)=='scientific'),
            ('restore_basic_mode', {'name':'menu','path':['View','Basic']}, None,
             lambda tree: mode(tree)=='basic' and display(tree,'0')),
        ]
        for label, action, identifier, verify in specifications:
            observation = observe()
            action = dict(action)
            if identifier:
                index, _ = Selector(role='Button',identifier=identifier).resolve(observation['tree'])
                action['element_index'] = index
            step = {'label':label,'action':action,'effect_verified':False}
            report['steps'].append(step)
            audit = ProcessInputAudit(strategy='application_frontmost',interval=.005,
                                      ready_samples=3,startup_timeout=10,stop_timeout=3,
                                      require_selection=require_selection)
            try:
                with audit:
                    background()
                    result = driver.act(observation['obs_id'],TARGET,action)
                    step['result'] = result
                    # Observe the app's asynchronous reaction too. This delay is
                    # measured, not treated as proof that an action succeeded.
                    time.sleep(.15)
                    after = observe()
                    step['effect_verified'] = bool(verify(after['tree']))
                    step['after'] = {'mode':mode(after['tree']),
                                     'expected_effect_verified':step['effect_verified']}
            finally:
                step['audit'] = audit.report()
            if step['audit']['status'] != 'UNCHANGED_SAMPLED':
                raise RuntimeError('input_preservation_not_verified:' + label)
            if result.get('type') != 'acted' or result.get('unverified') or not step['effect_verified']:
                raise RuntimeError('target_effect_not_verified:' + label)
        report['passed'] = True
    except Exception as exc:
        report['error'] = type(exc).__name__ + ':' + str(exc)
        report['cleanup'] = 'stopped; no further UI events or foreground restoration'
    return report


def run_and_save(driver, *, transport, output, require_selection=False):
    output = Path(output)
    # Reserve the report before any UI action; never erase an earlier attempt.
    with output.open('x') as stream:
        report = run_probe(driver,transport=transport,require_selection=require_selection)
        json.dump(report,stream,ensure_ascii=False,indent=2)
        stream.write('\n')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--require-selection',action='store_true',
                        help='Require observable foreground caret/selection; unavailable evidence prevents actions.')
    args=parser.parse_args()
    from nlc.driver import Driver
    result=run_and_save(Driver(),transport='in-process',output=args.output,
                        require_selection=args.require_selection)
    print(json.dumps({'passed':result['passed'],'error':result.get('error'),
                      'report':str(args.output)},ensure_ascii=False))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
