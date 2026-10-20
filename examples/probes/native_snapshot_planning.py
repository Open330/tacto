"""Real-provider planning over a recorded owned native snapshot; no GUI input."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def snapshot(path, index):
    raw = Path(path).read_bytes()
    if len(raw) > 32 * 1024 * 1024:
        raise ValueError('snapshot_report_too_large')
    report = json.loads(raw)
    if (report.get('evaluation_kind') != 'real_model_owned_appkit'
            or report.get('target') != 'ai.callabo.tacto.select-fixture'
            or type(index) is not int or index < 0):
        raise ValueError('owned_native_snapshot_required')
    candidates = report['plans'][index]['candidates']
    if not isinstance(candidates, list) or not candidates or not all(isinstance(c, dict) for c in candidates):
        raise ValueError('snapshot_candidates_unavailable')
    option = report['requested_option']
    if not isinstance(option, str) or not option.startswith('Choice') or not option[6:].isascii() or not option[6:].isdecimal():
        raise ValueError('owned_option_required')
    return candidates, option, hashlib.sha256(raw).hexdigest()


def run(path, index, provider, output):
    candidates, option, input_hash = snapshot(path, index)
    if provider not in ('codex', 'claude'):
        raise ValueError('unsupported_provider')
    import tiktoken
    import tacto.ask as ask
    from tacto.provider_observation import capture_provider_calls, summarize_provider_calls
    counter = tiktoken.get_encoding('o200k_base')
    files = sorted((ROOT/'tacto').glob('*.py')) + [Path(__file__).resolve()]
    def sources():
        return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    result = {'scope':'planning only on recorded owned native snapshot; no UI execution',
              'provider':provider, 'input_sha256':input_hash, 'snapshot_index':index,
              'host_ui_actions_executed':0, 'provider_side_effects_verified':False,
              'task_completion_verified':False,
              'serving_tokenizer_verified':False, 'comparison_eligible':False,
              'prompt_budget':2500, 'prompt_counter':'o200k_base', 'prompt_metrics':[],
              'planner_queries':[]}
    before = None
    calls = []
    began = time.monotonic()
    try:
        before = sources()
        original = ask.ask
        def measured(question, *args, **kwargs):
            guard = kwargs['_request_guard']
            def submit(prompt, attempt):
                guard(prompt, attempt)
                result['prompt_metrics'].append({'tokens':len(counter.encode(prompt,disallowed_special=())),
                    'validation_attempt':attempt, 'sha256':hashlib.sha256(prompt.encode()).hexdigest()})
            kwargs['_request_guard'] = submit
            answer = original(question, *args, **kwargs)
            if isinstance(answer, dict):
                kind = ('find_context' if isinstance(answer.get('read_context'),dict)
                        and 'search' in answer['read_context'] else
                        'read_context' if 'read_context' in answer else
                        'next_page' if answer.get('next_page') else 'plan')
                result['planner_queries'].append(kind)
            return answer
        with capture_provider_calls() as calls, patch.object(ask,'ask',side_effect=measured):
            answer = ask.ask_here_paged(f'선택 메뉴에서 {option}를 한 번 선택하고, 결과 표시의 값을 읽어 알려주세요.',
                candidates, provider=provider, budget=2500, max_pages=8,
                token_counter=lambda text:len(counter.encode(text,disallowed_special=())), timeout_seconds=90)
        result['answer'] = answer
        actions = answer.get('actions',[]) if isinstance(answer,dict) else []
        result['valid_scroll_plan'] = bool(actions) and all(
            a.get('do') == 'scroll' and a.get('direction') == 'down'
            and type(a.get('pages',1)) is int and 1 <= a.get('pages',1) <= 10
            and a.get('candidate',{}).get('container') == 'tacto-fixture-choice' for a in actions)
    except BaseException as error:
        result['error_class'] = type(error).__name__
        if not isinstance(error, Exception):
            raise
    finally:
        result.update(elapsed_seconds=time.monotonic()-began, provider_calls=calls)
        try:result['usage'] = summarize_provider_calls(calls)
        except Exception as error:result['usage_error_class']=type(error).__name__
        try:after = sources()
        except Exception:after = None
        result.update(source_before=before,source_after=after,
                      source_subset_stable=before is not None and before==after)
        with os.fdopen(descriptor,'w') as stream:
            json.dump(result,stream,ensure_ascii=False,indent=2)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--index',type=int,default=1)
    parser.add_argument('--provider',choices=('codex','claude'),required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    result = run(args.input,args.index,args.provider,args.output)
    print(json.dumps({k:result.get(k) for k in ('provider','valid_scroll_plan','elapsed_seconds',
          'error_class','source_subset_stable')},ensure_ascii=False))
