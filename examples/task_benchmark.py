"""Opt-in local-task benchmark; missing baselines never establish equivalence.

Run: python examples/task_benchmark.py run --task input-next-read --provider codex
Summarize JSONL: python examples/task_benchmark.py summarize results.jsonl
Five local fixtures have real-model adapters. The unavailable Codex computer-use
baseline stays explicit. Fixed-planner fixture tests are not model-run results.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import importlib.metadata
import json
import math
import os
import platform
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
# Tracked with the code: under reviews/ (gitignored) a fresh clone had no
# manifest and 16 tests failed on june-mba (2026-10-09).
DEFAULT_MANIFEST = ROOT / 'examples/benchmark-tasks.json'
STATUSES = {'passed', 'failed', 'timeout', 'unavailable', 'skipped'}


def source_snapshot(manifest, manifest_path=None):
    """Hash allowlisted actual source bytes, never outputs or virtualenv files.

    The effective manifest is included as well as its file, because API callers
    can supply an in-memory manifest. Endpoints detect changes between snapshots,
    not a change-and-revert during execution; this is provenance, not attestation.
    """
    paths = list((ROOT / 'nlc').glob('*.py'))
    paths += [ROOT / 'examples' / name for name in (
        'task_benchmark.py', 'benchmark_web_runner.py', 'probe_browser_task.py')]
    manifest_file = Path(manifest_path) if manifest_path is not None else DEFAULT_MANIFEST
    entries = []
    for path in paths:
        entries.append((path.relative_to(ROOT).as_posix(), path.read_bytes()))
    if manifest_file.is_file():
        entries.append(('manifest-file', manifest_file.read_bytes()))
    entries.append(('effective-manifest', json.dumps(
        manifest, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()))
    digest = hashlib.sha256()
    for name, data in sorted(entries):
        encoded = name.encode()
        digest.update(len(encoded).to_bytes(8, 'big'))
        digest.update(encoded)
        digest.update(len(data).to_bytes(8, 'big'))
        digest.update(data)
    def git(*args):
        try:
            out = subprocess.run(['git', '-C', str(ROOT), *args],
                                 capture_output=True, text=True, timeout=5)
            return out.stdout.strip() if out.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            return None
    status = git('status', '--porcelain', '--untracked-files=normal')
    return {'git_head': git('rev-parse', 'HEAD'), 'source_sha256': digest.hexdigest(),
            'dirty': None if status is None else bool(status),
            'hashed_files': [name for name, _ in sorted(entries)]}


def environment_versions():
    try:
        playwright = importlib.metadata.version('playwright')
    except importlib.metadata.PackageNotFoundError:
        playwright = None
    return {'python': platform.python_version(), 'playwright': playwright}


def _source_key(result):
    source = result.get('source') or {}
    return (source.get('source_sha256'), source.get('end', {}).get('source_sha256'),
            source.get('stable_during_run'))


def _cohorts(results):
    grouped = {}
    for result in results:
        key = (result['system'], result.get('provider'), result.get('model'),
               result.get('requested_provider'), result.get('measurement_mode', 'unknown'), *_source_key(result))
        grouped.setdefault(key, []).append(result)
    cohorts = []
    for key, runs in grouped.items():
        system, provider, model, requested_provider, measurement_mode, source_hash, end_hash, stable = key
        measured = [r for r in runs if r['status'] in ('passed', 'failed', 'timeout')]
        passed = sum(r['status'] == 'passed' for r in measured)
        cohorts.append({'system': system, 'provider': provider, 'model': model,
                        'requested_provider': requested_provider, 'measurement_mode': measurement_mode,
                        'source_sha256': source_hash, 'end_source_sha256': end_hash,
                        'source_status': 'unknown' if source_hash is None else
                                         'stable' if stable is True else 'changed' if stable is False else 'unknown',
                        'runs': len(runs), 'measured_runs': len(measured),
                        'passed_runs': passed,
                        'measured_run_pass_rate': passed / len(measured) if measured else None,
                        'run_ids': [r['run_id'] for r in runs]})
    return cohorts


def condition_id(task):
    """Goal, fixture, budget and acceptance checks are all comparison inputs."""
    data = {k: task[k] for k in ('id', 'goal', 'initial_conditions',
                                 'time_budget_seconds', 'oracles')}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def validate_manifest(manifest):
    if manifest.get('schema') != 1 or not manifest.get('tasks'):
        raise ValueError('unsupported or empty manifest')
    systems = manifest.get('systems', [])
    if not systems or len(set(systems)) != len(systems):
        raise ValueError('systems must be distinct')
    seen = set()
    for task in manifest['tasks']:
        if not isinstance(task.get('id'), str) or not task['id'] or task['id'] in seen:
            raise ValueError('task IDs must be nonempty and distinct')
        seen.add(task['id'])
        budget = task.get('time_budget_seconds')
        if type(budget) not in (int, float) or not math.isfinite(budget) or budget <= 0:
            raise ValueError('invalid task budget')
        if not task.get('goal') or not isinstance(task.get('initial_conditions'), dict):
            raise ValueError('task requires goal and initial conditions')
        checks = task.get('oracles')
        if not isinstance(checks, list) or not checks or len(set(checks)) != len(checks):
            raise ValueError('task requires distinct outcome oracles')
    return manifest


def validate_result(result, manifest):
    task = next((t for t in manifest['tasks'] if t['id'] == result.get('task_id')), None)
    if task is None or result.get('system') not in manifest['systems']:
        raise ValueError('unknown task or system')
    if result.get('condition_id') != condition_id(task):
        raise ValueError('result belongs to different benchmark conditions')
    if not isinstance(result.get('run_id'), str) or not result['run_id']:
        raise ValueError('missing run ID')
    if result.get('status') not in STATUSES:
        raise ValueError('unknown status')
    elapsed = result.get('elapsed_seconds')
    if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0:
        raise ValueError('invalid elapsed time')
    if not isinstance(result.get('evidence'), list):
        raise ValueError('evidence must be a list')
    if result['status'] == 'passed':
        if elapsed > task['time_budget_seconds']:
            raise ValueError('passed result exceeded task budget')
        evidence = result['evidence']
        if len(evidence) != len(task['oracles']):
            raise ValueError('incomplete outcome evidence')
        if {e.get('oracle') for e in evidence} != set(task['oracles']):
            raise ValueError('outcome oracle mismatch')
        if any(e.get('passed') is not True or 'observed' not in e for e in evidence):
            raise ValueError('success requires positive observed evidence')
    elif not result.get('reason'):
        raise ValueError('non-success requires reason, including skips')
    # These fields are mandatory even when the true model is not observable.
    if 'provider' not in result or 'model' not in result:
        raise ValueError('provider/model must be recorded, null when unknown')
    return result


def summarize(manifest, results):
    validate_manifest(manifest)
    results = list(results)
    by_cell = {}
    ids = set()
    for result in results:
        validate_result(result, manifest)
        if result['run_id'] in ids:
            raise ValueError('duplicate run ID')
        ids.add(result['run_id'])
        by_cell.setdefault((result['task_id'], result['system']), []).append(result)
    rows = []
    totals = {s: {'planned_tasks': len(manifest['tasks']), 'fully_passed_tasks': 0,
                  'missing_tasks': 0, 'runs': 0, 'passed_runs': 0,
                  'measured_tasks': 0, 'measured_runs': 0} for s in manifest['systems']}
    for task in manifest['tasks']:
        for system in manifest['systems']:
            runs = by_cell.get((task['id'], system), [])
            counts = {status: sum(r['status'] == status for r in runs) for status in sorted(STATUSES)}
            rows.append({'task_id': task['id'], 'system': system,
                         'condition_id': condition_id(task), 'runs': len(runs),
                         'statuses': counts, 'missing': not runs})
            tally = totals[system]
            measured = [r for r in runs if r['status'] in ('passed', 'failed', 'timeout')]
            tally['measured_tasks'] += int(bool(measured))
            tally['measured_runs'] += len(measured)
            tally['runs'] += len(runs)
            tally['passed_runs'] += counts['passed']
            tally['missing_tasks'] += int(not runs)
            tally['fully_passed_tasks'] += int(bool(runs) and counts['passed'] == len(runs))
    for tally in totals.values():
        tally['planned_task_completion_rate'] = tally['fully_passed_tasks'] / tally['planned_tasks']
        tally['measured_run_pass_rate'] = (tally['passed_runs'] / tally['measured_runs']
                                           if tally['measured_runs'] else None)
        tally['measurement_coverage'] = tally['measured_tasks'] / tally['planned_tasks']
    cohorts = _cohorts(results)
    return {'rows': rows, 'totals': totals, 'cohorts': cohorts,
            'mixed_cohorts': any(sum(c['system'] == system for c in cohorts) > 1
                                 for system in manifest['systems']),
            'equivalence_established': False,
            'limitation': 'Local fixture coverage only; missing, skipped and unavailable tasks are not successes. Multiple runs retain all failures; no best-run selection. Aggregate totals may mix source/provider cohorts; inspect cohorts before comparison. Missing source remains unknown. planned_task_completion_rate is planned coverage, not measured failure rate; measured_run_pass_rate is null when nothing was executed.'}


def attach_provider_metadata(result, event):
    """Use observed route identities; a requested provider is not a measurement."""
    from nlc.provider_observation import summarize_provider_calls
    records = event.get('provider_calls', [])
    if type(records) is not list or any(type(r) is not dict or 'text' in r for r in records):
        raise ValueError('invalid benchmark provider metadata')
    summary = summarize_provider_calls(records)
    providers = {r.get('provider') for r in records if r.get('provider') is not None}
    models = {r.get('model') for r in records if r.get('model') is not None}
    result.update(provider=next(iter(providers)) if len(providers) == 1 else None,
                  model=next(iter(models)) if len(models) == 1 else None,
                  provider_calls=records, model_usage=summary,
                  measurement_mode=event.get('planner_mode', 'real-model'))
    return result


def import_external_result(manifest, record, artifact_root):
    """Validate a manually recorded trusted-tool run and bind raw evidence bytes.

    No UI is invoked. Supplied hashes prove file integrity, not outcome truth.
    Independent oracle evidence must be inspected by the benchmark operator.
    """
    import copy
    result = copy.deepcopy(record)
    validate_result(result, manifest)
    if result['system'] != 'codex-computer-use':
        raise ValueError('external adapter is for the Codex computer-use baseline')
    provenance = result.get('external_observation')
    if type(provenance) is not dict or provenance.get('transport') != 'trusted node_repl sky RPC':
        raise ValueError('trusted baseline transport provenance required')
    if provenance.get('execution_mode') not in ('fixed-actions', 'autonomous-model'):
        raise ValueError('baseline execution mode required')
    if type(result.get('environment')) is not dict:
        raise ValueError('baseline environment metadata required')
    artifacts = provenance.get('artifacts')
    if type(artifacts) is not list or not artifacts:
        raise ValueError('raw baseline artifacts required')
    root = Path(artifact_root).resolve(strict=True)
    for artifact in artifacts:
        if type(artifact) is not dict or set(artifact) != {'path', 'sha256'}:
            raise ValueError('invalid baseline artifact reference')
        relative = Path(artifact['path'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('baseline artifact must be confined to artifact root')
        resolved = (root / relative).resolve(strict=True)
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise ValueError('baseline artifact is outside artifact root')
        if hashlib.sha256(resolved.read_bytes()).hexdigest() != artifact['sha256']:
            raise ValueError('baseline artifact hash mismatch')
    if result['status'] == 'passed':
        paths = {a['path'] for a in artifacts}
        if any(e.get('artifact') not in paths for e in result['evidence']):
            raise ValueError('every baseline oracle must reference raw evidence')
    result['measurement_mode'] = provenance['execution_mode']
    result['artifact_validation'] = 'hashes-verified-outcomes-not-rejudged'
    return result


def _run_bounded(argv, timeout):
    with subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, start_new_session=(os.name == 'posix')) as proc:
        try:
            stdout, _ = proc.communicate(timeout=timeout)
            return proc.returncode, stdout
        except subprocess.TimeoutExpired:
            if os.name == 'posix':
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                proc.kill()
            proc.wait()
            raise


def run_task(manifest, task_id, system='nlc', provider='codex', *, manifest_path=None):
    validate_manifest(manifest)
    task = next((t for t in manifest['tasks'] if t['id'] == task_id), None)
    if task is None or system not in manifest['systems']:
        raise ValueError('unknown task or system')
    start_source = source_snapshot(manifest, manifest_path)
    environment = environment_versions()
    result = dict(task_id=task_id, condition_id=condition_id(task), system=system,
                  provider=None,
                  requested_provider=provider if system == 'nlc' else None, model=None,
                  run_id=str(uuid.uuid4()), elapsed_seconds=0.0, status='unavailable',
                  reason=None, evidence=[])
    if system != 'nlc':
        result['reason'] = 'No Codex computer-use baseline adapter is available; not executed.'
    elif task.get('runner') == 'benchmark_web_runner':
        spec = importlib.util.spec_from_file_location('benchmark_web_runner', ROOT / 'examples/benchmark_web_runner.py')
        fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixture)
        if (hashlib.sha256(fixture.FIXTURES[task_id].encode()).hexdigest() != task['initial_conditions']['fixture_sha256']
                or fixture.GOALS[task_id] != task['goal']
                or fixture.ORACLES[task_id] != task['oracles']):
            raise ValueError('fixture changed; update benchmark conditions explicitly')
        started = time.monotonic()
        try:
            code, stdout = _run_bounded([sys.executable, str(ROOT / 'examples/benchmark_web_runner.py'),
                                        '--task', task_id, '--provider', provider,
                                        '--timeout', str(max(1, int(task['time_budget_seconds'] / 4)))],
                                       task['time_budget_seconds'])
            events = []
            for line in stdout.splitlines():
                try:
                    event = json.loads(line)
                    if isinstance(event, dict) and event.get('event') == 'benchmark_result':
                        events.append(event)
                except ValueError:
                    pass
            if len(events) != 1 or events[0].get('task_id') != task_id:
                result.update(status='failed', reason='Missing or ambiguous fixture outcome.')
            else:
                end = events[0]
                attach_provider_metadata(result, end)
                result['evidence'] = end.get('evidence', [])
                result['diagnostics'] = {k: end.get(k) for k in ('why', 'model_calls', 'planner_mode', 'plans')}
                passed = code == 0 and end.get('passed') is True and end.get('planner_mode') == 'real-model'
                result.update(status='passed' if passed else 'failed',
                              reason=None if passed else 'Fixture outcome checks failed.')
        except subprocess.TimeoutExpired:
            result.update(status='timeout', reason='Whole-task budget exceeded; local process group stopped, remote cancellation not guaranteed.')
        except OSError as exc:
            result.update(status='unavailable', reason='Runner could not start: ' + type(exc).__name__)
        result['elapsed_seconds'] = time.monotonic() - started
        if result['status'] == 'passed' and result['elapsed_seconds'] > task['time_budget_seconds']:
            result.update(status='timeout', reason='Outcome arrived after the whole-task time budget.')
    elif task.get('runner') != 'probe_browser_task':
        result['reason'] = 'Fixture/runner not implemented; not executed.'
    else:
        spec = importlib.util.spec_from_file_location('benchmark_probe', ROOT / 'examples/probe_browser_task.py')
        probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(probe)
        if (hashlib.sha256(probe.PAGE.encode()).hexdigest() != task['initial_conditions']['fixture_sha256']
                or probe.GOAL != task['goal']):
            raise ValueError('probe fixture changed; update benchmark conditions explicitly')
        started = time.monotonic()
        try:
            code, stdout = _run_bounded([sys.executable, str(ROOT / 'examples/probe_browser_task.py'),
                                        '--provider', provider, '--timeout',
                                        str(max(1, int(task['time_budget_seconds'] / 4)))],
                                       task['time_budget_seconds'])
            events = []
            for line in stdout.splitlines():
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue
            finals = [e for e in events if isinstance(e, dict) and e.get('event') == 'result']
            if len(finals) != 1:
                result.update(status='failed', reason='Runner did not produce exactly one outcome record.')
            else:
                end = finals[0]
                attach_provider_metadata(result, end)
                result['diagnostics'] = {
                    'why': end.get('why'), 'model_calls': end.get('model_calls'),
                    'planner_mode': 'real-model',
                    'plans': [{key: event.get(key) for key in ('actions', 'done', 'accepted')}
                              for event in events if isinstance(event, dict)
                              and event.get('event') == 'plan']}
                expected = 'NLC-2026-confirmed'
                result['evidence'] = [
                    {'oracle': 'loop_done', 'passed': end.get('loop_ok') is True, 'observed': end.get('loop_ok')},
                    {'oracle': 'dom_result', 'passed': end.get('dom_result') == expected, 'observed': end.get('dom_result')},
                    {'oracle': 'read_answer', 'passed': expected in end.get('said', ''), 'observed': end.get('said')}]
                passed = code == 0 and end.get('passed') is True and all(e['passed'] for e in result['evidence'])
                result.update(status='passed' if passed else 'failed',
                              reason=None if passed else 'Runner or independent DOM/readback checks failed.')
        except subprocess.TimeoutExpired:
            result.update(status='timeout', reason='Whole-task time budget exceeded; local process group stopped, remote cancellation not guaranteed.')
        except OSError as exc:
            result.update(status='unavailable', reason='Runner could not start: ' + type(exc).__name__)
        result['elapsed_seconds'] = time.monotonic() - started
        if result['status'] == 'passed' and result['elapsed_seconds'] > task['time_budget_seconds']:
            result.update(status='timeout', reason='Outcome arrived after the whole-task time budget.')
    end_source = source_snapshot(manifest, manifest_path)
    result['source'] = {**start_source, 'start': start_source, 'end': end_source,
                        'stable_during_run': all(start_source[key] == end_source[key]
                            for key in ('source_sha256', 'git_head', 'dirty'))}
    result['environment'] = environment
    return validate_result(result, manifest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=DEFAULT_MANIFEST)
    commands = parser.add_subparsers(dest='command', required=True)
    run = commands.add_parser('run')
    run.add_argument('--task', default='input-next-read')
    run.add_argument('--system', choices=['nlc', 'codex-computer-use'], default='nlc')
    run.add_argument('--provider', default='codex')
    external = commands.add_parser('import-baseline')
    external.add_argument('record', type=Path)
    external.add_argument('--artifact-root', type=Path, required=True)
    summary = commands.add_parser('summarize')
    summary.add_argument('results', type=Path)
    args = parser.parse_args()
    manifest = validate_manifest(json.loads(args.manifest.read_text()))
    if args.command == 'run':
        output = run_task(manifest, args.task, args.system, args.provider, manifest_path=args.manifest)
    elif args.command == 'import-baseline':
        output = import_external_result(manifest, json.loads(args.record.read_text()), args.artifact_root)
    else:
        output = summarize(manifest, [json.loads(line) for line in args.results.read_text().splitlines() if line.strip()])
    print(json.dumps(output, ensure_ascii=False, indent=None if args.command == 'run' else 2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
