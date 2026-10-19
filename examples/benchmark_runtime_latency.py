"""Paired signed-runtime latency check in a disposable native app; no models.

Example: python3 examples/benchmark_runtime_latency.py --baseline /path/to/nlc
  --candidate /path/to/new/nlc --output /absolute/new/private-directory
Both launchers must already have permission. This never installs or grants it.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import statistics
import subprocess
import time
import uuid

if __package__ in (None, ''):
    from release_identity import python_source_hashes
else:
    from .release_identity import python_source_hashes

ROOT = Path(__file__).resolve().parents[1]


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    path.chmod(0o600)


def code_hash(path):
    result = subprocess.run(['codesign', '-dv', '--verbose=4', str(path)], capture_output=True, text=True, check=True)
    return next(line.split('=', 1)[1] for line in result.stderr.splitlines() if line.startswith('CDHash='))


def manifest(launcher):
    runtime = launcher.parent.parent
    return {'python_sources': python_source_hashes(runtime, bundled_only=True),
            'bytecode': {str(p.relative_to(runtime/'.venv')): hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in sorted((runtime/'.venv').rglob('*.pyc'))},
            'launcher_cdhash': code_hash(launcher),
            'helper_cdhash': code_hash(runtime/'helper/Tacto Helper.app'),
            'python_cdhash': code_hash(runtime/'.venv/bin/python')}


def seal_status(launcher):
    app = launcher.parent.parent.parents[2]
    result = subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], capture_output=True)
    return result.returncode == 0


def template(target, amount, factor, bonus, *, fail=None):
    def check(identifier, value):
        return {'field': {'identifier': identifier}, 'attribute': 'Value', 'equals': value}
    def step(action, identifier, value=None, post=None):
        result = {'action': action, 'selector': {'identifier': identifier}, 'postcondition': post}
        if value is not None:
            result['params'] = {'value': value}
        return result
    result = str(int(amount)*int(factor)+(5 if bonus else 0))
    wanted = '1' if bonus else '0'
    steps = [step('set_value', 'bench-amount', amount, check('bench-amount', amount)),
             step('set_value', 'bench-factor', factor, check('bench-factor', factor)),
             step('click', 'bench-bonus', post=check('bench-bonus', wanted)),
             step('click', 'bench-review', post=check('bench-stage', 'impossible' if fail == 'review' else 'review')),
             step('click', 'bench-compute', post=check('bench-preview', result)),
             step('click', 'bench-finish', post=check('bench-stage', 'impossible' if fail == 'finish' else 'done'))]
    steps[2]['skip_if'] = check('bench-bonus', wanted)
    contract = {'effects': ['write_ephemeral'], 'reversibility': 'reversible',
                'resource': {'object_type': 'entry', 'scope': 'entry'}}
    # Expanded contracts work in the published baseline as well as development.
    for item in steps:
        item['safety'] = contract.copy()
    return {'schema': 2, 'name': 'owned-runtime-latency', 'target': target, 'steps': steps}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--pairs', type=int, default=10)
    parser.add_argument('--launcher-optimized', action='store_true',
                        help='candidate also includes the separately reviewed helper cleanup change')
    parser.add_argument('--release-comparison', action='store_true',
                        help='Compare complete release artifacts; record all differences without single-patch attribution')
    args = parser.parse_args()
    if not 1 <= args.pairs <= 30:
        parser.error('pairs must be 1..30')
    base = args.output.resolve(); base.mkdir(mode=0o700)
    launchers = {key: getattr(args, key).resolve(strict=True) for key in ('baseline', 'candidate')}
    before = {key: manifest(value) for key, value in launchers.items()}
    seals_before = {key: seal_status(value) for key, value in launchers.items()}
    if args.launcher_optimized and not seals_before['candidate']:
        raise ValueError('candidate bundle must be sealed before measurement')
    differences = [name for name in set(before['baseline']['python_sources']) | set(before['candidate']['python_sources'])
                   if before['baseline']['python_sources'].get(name) != before['candidate']['python_sources'].get(name)]
    native_equal = all(before['baseline'][key] == before['candidate'][key]
                       for key in ('helper_cdhash', 'python_cdhash'))
    launcher_changed = before['baseline']['launcher_cdhash'] != before['candidate']['launcher_cdhash']
    # Refuse attributing a comparison to this patch when other runtime code differs.
    expected = ['driver.py', 'flow.py', 'general.py', 'helper.py', 'overlay.py'] if args.launcher_optimized else ['driver.py', 'flow.py', 'general.py', 'helper.py']
    bytecode_equal = set(before['baseline']['bytecode']) == set(before['candidate']['bytecode'])
    for name, digest in before['baseline']['bytecode'].items():
        changed_module = ('/site-packages/tacto/__pycache__/' in '/'+name
                          and Path(name).name.split('.', 1)[0]+'.py' in expected)
        if not changed_module and before['candidate']['bytecode'].get(name) != digest:
            bytecode_equal = False
    if not args.release_comparison and (sorted(differences) != expected or not native_equal or not bytecode_equal
            or launcher_changed != args.launcher_optimized):
        write(base/'mismatch.json', {'manifests': before, 'differences': differences, 'native_equal': native_equal})
        raise ValueError('runtimes must match the declared Python changes and optional launcher change')
    target = 'ai.callabo.nlc.latency.' + uuid.uuid4().hex[:8]
    app = base/'Latency Benchmark.app'; contents = app/'Contents'
    (contents/'MacOS').mkdir(parents=True)
    (contents/'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': target,
        'CFBundleExecutable': 'RepeatBenchmark', 'CFBundleName': 'Tacto Latency Benchmark',
        'CFBundlePackageType': 'APPL', 'LSUIElement': True}))
    fixture_source = ROOT/'tests/fixtures/NativeRepeatBenchmark.swift'
    subprocess.run(['swiftc', '-parse-as-library', str(fixture_source), '-o', str(contents/'MacOS/RepeatBenchmark')], check=True, capture_output=True)
    subprocess.run(['codesign', '--force', '--sign', '-', str(app)], check=True, capture_output=True)
    control = base/'fixture'; control.mkdir(mode=0o700)
    child = subprocess.Popen([str(contents/'MacOS/RepeatBenchmark'), str(control)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rows = []; startup = []; sequence = 0
    def state():
        return json.loads((control/'oracle.json').read_text())
    def reset(job):
        nonlocal sequence
        sequence += 1
        write(control/'control.json', {'sequence': sequence, 'job': job, 'changed_control': False})
        deadline = time.monotonic()+10
        while time.monotonic() < deadline:
            try:
                value = state()
                if value['sequence'] == sequence and value['phase'] == 'entry':
                    if value['active']:
                        raise ValueError('fixture must remain background')
                    return value
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            if child.poll() is not None:
                raise RuntimeError('fixture exited')
            time.sleep(.025)
        raise TimeoutError('fixture reset')
    def run(arm, argv, directory):
        directory.mkdir(mode=0o700)
        began = time.monotonic()
        result = subprocess.run([str(launchers[arm]), *argv], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=40)
        elapsed = time.monotonic()-began
        helper = str(launchers[arm].parent.parent/'helper/Tacto Helper.app/Contents/MacOS/TactoHelper')
        processes = subprocess.check_output(['ps', '-axo', 'comm='], text=True).splitlines()
        if any(line.strip() == helper for line in processes):
            raise ValueError('owned helper remains after command cleanup')
        for name, text in [('stdout.txt', result.stdout), ('stderr.txt', result.stderr)]:
            path = directory/name; path.write_text(text); path.chmod(0o600)
        return result.returncode, elapsed
    try:
        reset('bootstrap')
        # CLI startup, helper startup and observe are measured separately from flows.
        for index in range(5):
            for arm in (('baseline', 'candidate') if index % 2 == 0 else ('candidate', 'baseline')):
                for kind, argv in [('usage', []), ('apps', ['apps', '--helper']),
                                   ('observe', ['observe', target, '--helper'])]:
                    code, seconds = run(arm, argv, base/f'start-{index}-{arm}-{kind}')
                    if kind != 'usage' and code != 0:
                        raise ValueError('read-only startup probe failed')
                    startup.append({'arm': arm, 'kind': kind, 'exit': code, 'seconds': seconds})
                    write(base/'startup-progress.json', {'rows': startup})
        cases = [(str(11+i), str(2+i%4), i%2 == 0, None) for i in range(args.pairs)]
        cases += [('19', '3', True, 'review'), ('19', '3', True, 'finish')]
        for pair, (amount, factor, bonus, fail) in enumerate(cases):
            initials = []
            for arm in (('baseline', 'candidate') if pair % 2 == 0 else ('candidate', 'baseline')):
                job = f'{arm}-{pair}'; initial = reset(job)
                initials.append({k: initial[k] for k in ('amount', 'factor', 'bonus', 'phase', 'control_version', 'active')})
                flow = base/f'flow-{pair}-{arm}.json'; write(flow, template(target, amount, factor, bonus, fail=fail))
                trace = base/f'trace-{pair}-{arm}.json'
                code, seconds = run(arm, ['run', str(flow), '--helper', '--think', '0', '--no-indicator', '--trace', str(trace)], base/f'run-{pair}-{arm}')
                observed = state(); commits = [c for c in observed['commits'] if c['job'] == job]
                expected = {'job': job, 'amount': amount, 'factor': factor, 'bonus': bonus,
                            'result': int(amount)*int(factor)+(5 if bonus else 0)}
                if fail == 'review':
                    passed = code == 1 and observed['phase'] == 'review' and commits == []
                elif fail == 'finish':
                    passed = code == 1 and observed['phase'] == 'done' and commits == [expected]
                else:
                    passed = code == 0 and observed['phase'] == 'done' and commits == [expected]
                passed = passed and not observed['active']
                rows.append({'pair': pair, 'arm': arm, 'negative': fail, 'seconds': seconds,
                             'exit': code, 'passed': passed, 'commits': len(commits), 'trace': str(trace)})
                write(base/'progress.json', {'rows': rows})
                print(json.dumps(rows[-1]), flush=True)
                if not passed:
                    raise ValueError('task/negative oracle failed')
            if initials[0] != initials[1]:
                raise ValueError('paired initial predicates differ')
        after = {key: manifest(value) for key, value in launchers.items()}
        seals_after = {key: seal_status(value) for key, value in launchers.items()}
        summary = {arm: {'runs': args.pairs, 'median_seconds': statistics.median(r['seconds'] for r in rows if r['arm'] == arm and not r['negative']),
                         'total_seconds': sum(r['seconds'] for r in rows if r['arm'] == arm and not r['negative'])}
                   for arm in launchers}
        savings = 100*(1-summary['candidate']['total_seconds']/summary['baseline']['total_seconds'])
        write(base/'report.json', {'passed': before == after and all(r['passed'] for r in rows)
                                  and ((all(seals_before.values()) and all(seals_after.values())) if args.release_comparison else (not args.launcher_optimized or seals_after['candidate'])),
            'rows': rows, 'startup': startup, 'summary': summary, 'saving_percent': savings,
            'source_stable': before == after, 'source_before': before, 'source_after': after,
            'fixture_sha256': hashlib.sha256(fixture_source.read_bytes()).hexdigest(),
            'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'model_requests': 0, 'scope': 'same six-step flow; alternating arm order; independent logical commit oracle; fresh CLI per task',
            'launcher_optimized': args.launcher_optimized,
            'release_comparison': args.release_comparison,
            'python_module_differences': differences,
            'native_code_hashes_equal': native_equal,
            'single_patch_attribution': not args.release_comparison,
            'common_bytecode_identical': bytecode_equal,
            'bundle_signature_before': seals_before, 'bundle_signature_after': seals_after,
            'limits': ['owned fixture, one Mac, warmed filesystem; not Sky or agent authoring/token comparison',
                       'foreground witness is fixture active state, not continuous input/pointer monitoring',
                       'logical commit count does not prove exactly one physical input',
                       'ordinary observations retain settling; blocking AX reads are not bounded by polling deadline']})
        print(str(base/'report.json'), flush=True)
    finally:
        if child.poll() is None:
            child.terminate()
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            child.kill(); child.wait(timeout=3)


if __name__ == '__main__':
    main()
