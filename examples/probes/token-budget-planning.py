"""Real tokenizer + live planner + independently verified owned web selection.

Requires the optional test tools Playwright/Chromium and tiktoken. Run with
PYTHONPATH=. .venv/bin/python examples/probes/token-budget-planning.py OUTPUT.
Pins the direct CLI adapter for repeatable provider telemetry; the model is
real, not a mocked answer. No native/user browser or real account is involved.
The encoding is caller-selected, not an observed serving-model tokenizer.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
from unittest.mock import patch

import tiktoken
from tacto.ask import ask_here_paged
from tacto.browser import BrowserDriver
from tacto.general import candidates


def private_json(path, data):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def source_hashes():
    root = Path(__file__).resolve().parents[2]
    paths = list((root / 'tacto').glob('*.py')) + list((root / 'nlc').glob('*.py')) + [Path(__file__).resolve()]
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(paths)}


def selection_case(value):
    try:
        count, wanted = (int(part) for part in value.split(':'))
    except (ValueError, TypeError):
        raise argparse.ArgumentTypeError('case must be COUNT:INDEX') from None
    if not 1 <= count <= 256 or not 0 <= wanted < count:
        raise argparse.ArgumentTypeError('COUNT must be 1..256 and INDEX must be in that range')
    return count, wanted


def executable_snapshot(provider):
    from tacto.authoring import _provider_env
    environment = _provider_env()
    path = shutil.which(provider, path=environment.get('PATH', os.defpath))
    if path is None:
        raise RuntimeError('provider_executable_unavailable')
    resolved = Path(path).resolve(strict=True)
    try:
        version = subprocess.run([path, '--version'], capture_output=True, text=True,
                                 timeout=10, env=environment)
    except subprocess.TimeoutExpired:
        raise RuntimeError('provider_version_preflight_failed') from None
    if version.returncode != 0:
        raise RuntimeError('provider_version_preflight_failed')
    return {'path':path, 'resolved_path':str(resolved),
            'sha256':hashlib.sha256(resolved.read_bytes()).hexdigest(),
            'version':version.stdout.strip(), 'scope':'entrypoint_only',
            'dependency_closure_verified':False, 'lookup_environment':'provider_env'}


def case_outcome(report):
    """A provider gate without an answer/action is not a task failure rate."""
    from tacto.provider_result import UNAVAILABLE_REASONS
    calls = report['calls']
    errors = [call.get('provider_result', {}).get('error') for call in calls]
    codes = [error.get('code') if isinstance(error, dict) else None for error in errors]
    unavailable = (bool(calls) and report['planner_result'] is None
                   and report['driver_result'] is None
                   and all(call.get('provider_result', {}).get('status') == 'error'
                           and code in UNAVAILABLE_REASONS for call, code in zip(calls, codes)))
    return {'status':'passed' if report['passed'] else 'provider_unavailable' if unavailable else 'failed',
            'task_execution':'attempted' if report['driver_result'] is not None else 'not_attempted',
            'provider_error_codes':sorted({code for code in codes if isinstance(code, str)}),
            'comparison_eligible':False}


def live_cli_result(provider, prompt, timeout):
    """Use each provider's structured adapter, never raw CLI text fallback."""
    if provider == 'codex':
        from tacto.codex_result import codex_result
        return codex_result(prompt, timeout=timeout)
    if provider == 'claude':
        from tacto.claude_result import claude_result
        return claude_result(prompt, timeout=timeout)
    if provider == 'opencode':
        from tacto.opencode_result import opencode_result
        return opencode_result(prompt, timeout=timeout)
    raise ValueError('unsupported structured probe provider')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--provider', choices=('codex', 'claude', 'opencode'), default='codex')
    parser.add_argument('--encoding', default='o200k_base')
    parser.add_argument('--budget', type=int, default=2500)
    parser.add_argument('--max-requests', type=int, default=12)
    parser.add_argument('--case', type=selection_case, action='append', dest='cases',
                        help='repeatable COUNT:zero-based-index; defaults to 36:30 and 48:47')
    parser.add_argument('--locale', choices=('en', 'ko'), default='en')
    parser.add_argument('--option-locale', choices=('en', 'ko'), default='en',
                        help='option labels and DOM values; independent of control-label locale')
    args = parser.parse_args()
    if args.budget < 1 or args.max_requests < 1:
        parser.error('budget and max-requests must be positive')
    enc = tiktoken.get_encoding(args.encoding)
    counter = lambda text: len(enc.encode_ordinary(text))
    executable_before = executable_snapshot(args.provider)
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    before = source_hashes()
    reports = []
    if args.option_locale == 'ko':
        standard_value,priority_value='일반_배송','우선_배송'
        standard_label,priority_label,retired_label='일반 배송','우선 배송','종료된 배송 (비활성)'
    else:
        standard_value,priority_value='standard','priority'
        standard_label,priority_label,retired_label='Standard','Priority','Retired'
    driver = BrowserDriver(headless=True)
    try:
        for case, (count, wanted) in enumerate(args.cases or ((36, 30), (48, 47))):
            nonce = secrets.token_hex(4)
            prefix = '배송 지역' if args.locale == 'ko' else 'Shipping region'
            names = [f'{prefix} {nonce} {i:03}' for i in range(count)]
            driver._page.set_content('<title>Owned selection ledger</title>' + ''.join(
                f'<select aria-label="{name}"><option value="{standard_value}">{standard_label}</option>'
                f'<option value="{priority_value}">{priority_label}</option>'
                f'<option disabled value="retired">{retired_label}</option></select>' for name in names))
            obs = driver.observe()
            rows = candidates(obs['tree'], capabilities=driver.capabilities)
            # Same browser option enrichment as the general planner.
            for row in rows:
                options = obs['select_options'].get(str(row['element_index']))
                if options is not None:
                    row['options'] = options
                    row['can'] = [a for a in row['can'] if a != 'set_value'] + ['select_option']
            calls = []

            def live_request(prompt, provider, **kwargs):
                measured = counter(prompt)
                record = {'task_prompt_tokens': measured,
                          'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
                          'prompt': prompt}
                calls.append(record)
                if measured > args.budget or len(calls) > args.max_requests:
                    raise AssertionError('probe_observed_request_budget_violation')
                print(json.dumps({'case': case, 'request': len(calls),
                                  'task_prompt_tokens': measured}), flush=True)
                response = live_cli_result(args.provider, prompt, kwargs['timeout_seconds'])
                record['provider_result'] = response.to_dict()
                if response.status != 'ok':
                    raise RuntimeError('live_provider_failed:' + response.error.code)
                return response.text

            result, action_result, error = None, None, None
            try:
                with patch('tacto.ask.reach_model', side_effect=live_request):
                    result = ask_here_paged('Set only ' + names[wanted] + ' to ' + priority_label + '. '
                        'Return exactly one select_option action for that control.', rows,
                        provider=args.provider, budget=args.budget, token_counter=counter,
                        max_pages=args.max_requests, timeout_seconds=180)
                actions = result.get('actions', []) if isinstance(result, dict) else []
                # Host independently binds the only allowed ephemeral action.
                if (len(actions) == 1 and actions[0]['do'] == 'select_option'
                        and actions[0]['value'] == priority_value
                        and actions[0]['candidate']['match'] == names[wanted]):
                    action_result = driver.act(obs['obs_id'], 'page', {
                        'name': 'select_option', 'value': priority_value,
                        'element_index': actions[0]['candidate']['element_index']})
            except Exception as exc:
                error = type(exc).__name__
            actual = driver._page.locator('select').evaluate_all('xs => xs.map(x => x.value)')
            expected = [standard_value] * count
            expected[wanted] = priority_value
            report = {'case': case, 'provider': args.provider, 'candidate_count': len(rows),
                      'expected_control': names[wanted], 'planner_result': result,
                      'driver_result': action_result, 'error_code': error,
                      'actual_values': actual, 'expected_values': expected,
                      'calls': calls, 'passed': actual == expected,
                      'scope': 'owned_headless_html_select',
                      'native_ui_test': False, 'tool_isolation_proven': False}
            report['option_locale']=args.option_locale
            report['outcome'] = case_outcome(report)
            private_json(args.output / f'case-{case}.json', report)
            reports.append(report)
            print(json.dumps({'case': case, 'passed': report['passed']}), flush=True)
    finally:
        driver.close()
    after = source_hashes()
    try:
        executable_after = executable_snapshot(args.provider)
    except Exception as exc:
        executable_after = {'status':'unavailable', 'error_class':type(exc).__name__}
    summary = {'passed': sum(r['passed'] for r in reports), 'total': len(reports),
               'outcomes':[r['outcome'] for r in reports],
               'comparison_rates_computed':False,
               'provider': args.provider, 'transport_override': 'direct_cli_json',
               'locale': args.locale, 'cases': args.cases or [[36, 30], [48, 47]],
               'option_locale':args.option_locale,
               'executable_before':executable_before, 'executable_after':executable_after,
               'executable_stable':executable_before == executable_after,
               'encoding': args.encoding, 'tiktoken_version': importlib.metadata.version('tiktoken'),
               'budget': args.budget, 'budget_scope': 'complete_task_prompt_only',
               'serving_model_tokenizer_verified': False, 'max_requests': args.max_requests,
               'source_checkpoint': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
               'source_before': before, 'source_after': after,
               'measured_source_subset_stable': before == after, 'all_dependencies_frozen': False}
    private_json(args.output / 'summary.json', summary)
    return 0 if (all(r['passed'] for r in reports) and before == after
                 and executable_before == executable_after) else 1


if __name__ == '__main__':
    raise SystemExit(main())
