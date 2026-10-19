"""Read-only prerequisite audit of historical NLC/Sky rows, without GUI access.

Matching recorded prerequisites do not prove complete initial-state/model parity.
Never repair old rows, pair duplicates selectively, or turn missing data into a
comparison score. Writes a new owner-only report, refusing existing outputs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path


def manifest_valid(value):
    if (not isinstance(value, dict) or type(value.get('version')) is not int
            or value['version'] != 2):
        return False
    files = value.get('files')
    if not isinstance(files, dict) or not files:
        return False
    if any(not isinstance(k, str) or not isinstance(v, str) or len(v) != 64
           or any(c not in '0123456789abcdef' for c in v) for k, v in files.items()):
        return False
    digest = hashlib.sha256(json.dumps(files, sort_keys=True,
                                      separators=(',', ':')).encode()).hexdigest()
    return value.get('sha256') == digest


def instruction_profile_valid(value):
    if (type(value) is not dict or set(value) != {'schema', 'nlc_template_sha256',
            'sky_template_sha256', 'sky_guide_sha256', 'sky_guide_provided'}
            or value['schema'] != 'comparison-instructions-v1'
            or type(value['sky_guide_provided']) is not bool):
        return False
    def digest(v):
        return type(v) is str and len(v) == 64 and all(c in '0123456789abcdef' for c in v)
    return (digest(value['nlc_template_sha256']) and digest(value['sky_template_sha256'])
            and (digest(value['sky_guide_sha256']) if value['sky_guide_provided']
                 else value['sky_guide_sha256'] is None))


def audit(rows):
    groups = {}
    invalid_rows = []
    for index, row in enumerate(rows):
        if (not isinstance(row, dict) or not isinstance(row.get('task'), str)
                or not row['task'] or type(row.get('rep')) is not int or row['rep'] < 0
                or row.get('arm') not in ('nlc', 'sky')):
            invalid_rows.append(index)
            continue
        groups.setdefault((row['task'], row['rep']), []).append((index, row))
    pairs = []
    for (task, rep), group in sorted(groups.items()):
        issues = set()
        nlcs = [r for i, r in group if r['arm'] == 'nlc']
        skies = [r for i, r in group if r['arm'] == 'sky']
        if len(nlcs) != 1 or len(skies) != 1:
            issues.add('missing_or_duplicate_arm')
        for index, row in group:
            if row.get('comparison_eligible') is not True:
                issues.add('run_not_eligible')
            if row.get('status') not in ('passed', 'failed', 'timeout'):
                issues.add('run_not_attempted_or_invalid')
            if type(row.get('oracle_passed')) is not bool:
                issues.add('oracle_outcome_not_recorded')
            if row.get('status') == 'passed' and (row.get('oracle_passed') is not True
                    or type(row.get('process_exit')) is not int or row['process_exit'] != 0):
                issues.add('success_not_supported_by_oracle_and_process')
            if row.get('layer_violation') is not False or row.get('shortcut') is not False:
                issues.add('layer_compliance_not_observed')
            locked = row.get('locked')
            if not isinstance(locked, list) or len(locked) != 2 or any(v is not False for v in locked):
                issues.add('session_not_observed_unlocked')
            if row.get('source_changed') is not False:
                issues.add('source_not_observed_stable')
            before, after = row.get('source_manifest_before'), row.get('source_manifest_after')
            if not manifest_valid(before) or not manifest_valid(after):
                issues.add('source_manifest_missing_or_invalid')
            elif before != after:
                issues.add('source_changed')
            if not isinstance(row.get('initial_frontmost_bundle'), str) or not row['initial_frontmost_bundle']:
                issues.add('initial_frontmost_not_recorded')
            if not isinstance(row.get('oracle_contract'), str) or not row['oracle_contract']:
                issues.add('oracle_contract_not_recorded')
            if not isinstance(row.get('runtime_policy'), str) or not row['runtime_policy']:
                issues.add('runtime_policy_not_recorded')
            profile = row.get('comparison_instruction_profile')
            if not instruction_profile_valid(profile):
                issues.add('instruction_profile_missing_or_invalid')
            else:
                if row.get('agent_prompt_template_sha256') != profile[row['arm'] + '_template_sha256']:
                    issues.add('prompt_template_not_bound_to_profile')
            if row.get('instruction_profile_stable') is not True:
                issues.add('instruction_profile_not_observed_stable')
            constraint = row.get('background_constraint')
            if not isinstance(constraint, str) or not 0 < len(constraint) <= 4096:
                issues.add('runtime_constraint_not_recorded')
            if type(row.get('timeout_seconds')) is not int or row['timeout_seconds'] <= 0:
                issues.add('timeout_not_recorded')
        if len(nlcs) == len(skies) == 1:
            tacto, sky = nlcs[0], skies[0]
            for key, reason in (
                    ('initial_frontmost_bundle', 'initial_frontmost_differs'),
                    ('oracle_contract', 'oracle_contract_differs'),
                    ('source_manifest_before', 'source_cohort_differs'),
                    ('runtime_policy', 'runtime_policy_differs'),
                    ('background_constraint', 'runtime_constraint_differs'),
                    ('timeout_seconds', 'timeout_differs'),
                    ('comparison_instruction_profile', 'instruction_profile_differs')):
                if tacto.get(key) != sky.get(key):
                    issues.add(reason)
        pairs.append({'task': task, 'rep': rep, 'row_indices': [i for i, r in group],
                      'observed_outcomes': [{'arm': r['arm'], 'status': r.get('status'),
                                             'oracle_passed': r.get('oracle_passed')} for i, r in group],
                      'recorded_prerequisites_match': not issues,
                      'issues': sorted(issues), 'same_conditions_verified': False,
                      'additional_evidence_required': ['independent_initial_task_state',
                                                       'serving_model_identity_and_configuration']})
    return {'schema': 'nlc-sky-recorded-prerequisites-v2', 'rows': len(rows),
            'invalid_row_indices': invalid_rows, 'pairs': pairs,
            'comparison_rates_computed': False,
            'scope': 'recorded_prerequisites_only_not_complete_condition_parity'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inputs', type=Path, nargs='+')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    cohorts = []
    for path in args.inputs:
        raw = path.read_bytes()
        if len(raw) > 8 * 1024 * 1024:
            parser.error('input exceeds the 8 MiB audit budget')
        rows = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
        cohorts.append({'input': str(path), 'sha256': hashlib.sha256(raw).hexdigest(),
                        'audit': audit(rows)})
    fd = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump({'cohorts': cohorts}, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(f'Audited {len(cohorts)} cohorts; no comparison rates computed.')


if __name__ == '__main__':
    main()
