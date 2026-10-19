"""Live Codex paging of owned registered files, followed by an HTTP receipt."""
import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import runpy
import secrets
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--registrations', type=int, default=64)
    parser.add_argument('--budget', type=int, default=2500)
    parser.add_argument('--max-pages', type=int, default=8)
    args = parser.parse_args()
    if not 2 <= args.registrations <= 128 or args.budget <= 0 or not 1 <= args.max_pages <= 16:
        parser.error('registrations 2..128, positive budget, max-pages 1..16 required')
    import tiktoken
    import tacto.ask
    from tacto.outcome import verify
    from tacto.provider_observation import summarize_provider_calls
    from tests.test_delivery_receipt_integration import DeliveryReceiptIntegrationTests
    meter = tiktoken.get_encoding('o200k_base')
    paths = list((ROOT / 'tacto').glob('*.py')) + [Path(__file__).resolve(),
        ROOT / 'tests/test_delivery_receipt_integration.py',
        ROOT / 'examples/probes/token-budget-planning.py']
    def snapshot():
        return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    executable = runpy.run_path(str(ROOT / 'examples/probes/token-budget-planning.py'))['executable_snapshot']
    before = snapshot()
    executable_before = executable('codex')
    descriptor = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    case = DeliveryReceiptIntegrationTests('test_general_registered_upload_chooses_host_scope_and_receives_exact_bytes_once')
    started = time.monotonic()
    calls = []
    report = {'passed': False, 'registrations': args.registrations,
              'task_prompt_budget': args.budget, 'encoding': 'o200k_base',
              'serving_tokenizer_verified': False, 'comparison_eligible': False,
              'all_dependencies_frozen': False, 'external_destination': False,
              'scope': 'live provider planning; owned HTTP receipt and trusted completion controller'}
    wanted_scope = 'upload_' + secrets.token_hex(8)
    report['wanted_scope'] = wanted_scope
    original = tacto.ask.reach_model
    def bounded(prompt, *positional, **kwargs):
        count = len(meter.encode_ordinary(prompt))
        calls.append({'task_prompt_tokens': count, 'reference_chunk_in_prompt': '# 참조 내용' in prompt,
                      'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest()})
        if count > args.budget or len(calls) > args.max_pages:
            raise AssertionError('observed_prompt_budget_violation')
        return original(prompt, *positional, **kwargs)
    try:
        case.setUp()
        with patch('tacto.ask.reach_model', side_effect=bounded):
            result = case.run_registered_upload(general=True, live_provider='codex',
                paged=True, registration_count=args.registrations,
                paging_budget=args.budget, max_pages=args.max_pages, wanted_scope=wanted_scope,
                planner_goal='Upload only the file with the exact filename 검토.txt to fixture-recipient from fixture-sender. Find its registered scope from the candidate metadata. Do not upload any other file.')
        outcome = verify(case.general_upload_spec, result, result.after_tree)
        upload_events = [e for e in result.events if e['action']['name'] == 'upload']
        report.update(general_ok=result.ok, general_verified=result.verified, outcome=asdict(outcome),
            general_stop_reason=result.stop_reason, paged_plan_records=case.paged_plan_records,
            posts=case.posts, message_count=len(case.messages),
            actions=[e['action'] for e in result.events], event_states=[e['state'] for e in result.events],
            provider_calls=result.provider_calls, provider_summary=summarize_provider_calls(result.provider_calls),
            receiving_manifests=[asdict(p.evidence) for p in result.commit_evidence.values()])
        report['passed'] = (outcome.state == 'done' and case.posts == 1 and len(case.messages) == 1
            and len(upload_events) == 1 and upload_events[0]['action']['scope'] == wanted_scope
            and upload_events[0]['state'] == 'confirmed')
    except Exception as error:
        report['error_class'] = type(error).__name__
    finally:
        case.doCleanups()
        after = snapshot()
        executable_after = executable('codex')
        stable = before == after and executable_before == executable_after
        report.update(calls=calls, elapsed_seconds=time.monotonic()-started,
            source_before=before, source_after=after, source_subset_stable=before == after,
            executable_before=executable_before, executable_after=executable_after,
            executable_stable=executable_before == executable_after)
        report['passed'] = report['passed'] and stable
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({k: report.get(k) for k in ('passed', 'posts', 'elapsed_seconds', 'error_class')}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
