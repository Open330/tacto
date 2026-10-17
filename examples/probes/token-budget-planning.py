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
    paths = list((root / 'tacto').glob('*.py')) + [Path(__file__).resolve()]
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(paths)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--provider', choices=('codex', 'claude'), default='codex')
    parser.add_argument('--encoding', default='o200k_base')
    parser.add_argument('--budget', type=int, default=2500)
    parser.add_argument('--max-requests', type=int, default=12)
    args = parser.parse_args()
    enc = tiktoken.get_encoding(args.encoding)
    counter = lambda text: len(enc.encode_ordinary(text))
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    before = source_hashes()
    reports = []
    driver = BrowserDriver(headless=True)
    try:
        for case, (count, wanted) in enumerate(((36, 30), (48, 47))):
            nonce = secrets.token_hex(4)
            names = [f'Shipping region {nonce} {i:03}' for i in range(count)]
            driver._page.set_content('<title>Owned selection ledger</title>' + ''.join(
                f'<select aria-label="{name}"><option value="standard">Standard</option>'
                '<option value="priority">Priority</option>'
                '<option disabled value="retired">Retired</option></select>' for name in names))
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
                if args.provider == 'codex':
                    from tacto.codex_result import codex_result
                    response = codex_result(prompt, timeout=kwargs['timeout_seconds'])
                else:
                    from tacto.claude_result import claude_result
                    response = claude_result(prompt, timeout=kwargs['timeout_seconds'])
                record['provider_result'] = response.to_dict()
                if response.status != 'ok':
                    raise RuntimeError('live_provider_failed:' + response.error.code)
                return response.text

            result, action_result, error = None, None, None
            try:
                with patch('tacto.ask.reach_model', side_effect=live_request):
                    result = ask_here_paged('Set only ' + names[wanted] + ' to Priority. '
                        'Return exactly one select_option action for that control.', rows,
                        provider=args.provider, budget=args.budget, token_counter=counter,
                        max_pages=args.max_requests, timeout_seconds=180)
                actions = result.get('actions', []) if isinstance(result, dict) else []
                # Host independently binds the only allowed ephemeral action.
                if (len(actions) == 1 and actions[0]['do'] == 'select_option'
                        and actions[0]['value'] == 'priority'
                        and actions[0]['candidate']['match'] == names[wanted]):
                    action_result = driver.act(obs['obs_id'], 'page', {
                        'name': 'select_option', 'value': 'priority',
                        'element_index': actions[0]['candidate']['element_index']})
            except Exception as exc:
                error = type(exc).__name__
            actual = driver._page.locator('select').evaluate_all('xs => xs.map(x => x.value)')
            expected = ['standard'] * count
            expected[wanted] = 'priority'
            report = {'case': case, 'provider': args.provider, 'candidate_count': len(rows),
                      'expected_control': names[wanted], 'planner_result': result,
                      'driver_result': action_result, 'error_code': error,
                      'actual_values': actual, 'expected_values': expected,
                      'calls': calls, 'passed': actual == expected,
                      'scope': 'owned_headless_html_select',
                      'native_ui_test': False, 'tool_isolation_proven': False}
            private_json(args.output / f'case-{case}.json', report)
            reports.append(report)
            print(json.dumps({'case': case, 'passed': report['passed']}), flush=True)
    finally:
        driver.close()
    after = source_hashes()
    summary = {'passed': sum(r['passed'] for r in reports), 'total': len(reports),
               'provider': args.provider, 'transport_override': 'direct_cli_json',
               'encoding': args.encoding, 'tiktoken_version': importlib.metadata.version('tiktoken'),
               'budget': args.budget, 'budget_scope': 'complete_task_prompt_only',
               'serving_model_tokenizer_verified': False, 'max_requests': args.max_requests,
               'source_checkpoint': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
               'source_before': before, 'source_after': after,
               'measured_source_subset_stable': before == after, 'all_dependencies_frozen': False}
    private_json(args.output / 'summary.json', summary)
    return 0 if all(r['passed'] for r in reports) else 1


if __name__ == '__main__':
    raise SystemExit(main())
