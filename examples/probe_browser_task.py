"""Opt-in real-model probe on a disposable local page, with a DOM oracle.

This measures the general screen loop with a known target, not CLI task routing
or a broad computer-use benchmark. It spends model calls but performs no upload,
message sending, or persistent browser operation.
"""
import argparse
import json
from pathlib import Path
import sys
import time
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PAGE = '''<label>Name<input aria-label="name"></label>
<button onclick="if(document.querySelector('input').value==='TACTO-2026'){
document.body.innerHTML='<label>Result<input aria-label=result readonly value=TACTO-2026-confirmed></label>'}">Next</button>'''
GOAL = "Name 입력칸에 TACTO-2026을 입력하고 Next를 누른 다음, 새 화면의 Result 값을 읽어서 알려주세요."


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider', default='codex')
    parser.add_argument('--timeout', type=int, default=45, help='seconds per model call')
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error('--timeout must be positive')
    from tacto.ask import ask, what_to_do_here
    from tacto.browser import BrowserDriver
    from tacto.general import run

    def emit(event, **fields):
        print(json.dumps(dict(event=event, **fields), ensure_ascii=False), flush=True)

    calls = 0
    def plan(goal, candidates, done):
        nonlocal calls
        calls += 1
        emit('model_call', number=calls, candidates=len(candidates))
        question = what_to_do_here(goal, candidates, done)
        try:
            answer = ask(question, provider=args.provider, timeout_seconds=args.timeout,
                         validation_retries=1)
        except Exception as exc:
            emit('model_error', detail=str(exc))
            answer = None
        emit('plan', actions=[a['do'] for a in (answer or {}).get('actions', [])],
             done=(answer or {}).get('done', False), accepted=answer is not None)
        return answer

    driver = BrowserDriver()
    started = time.monotonic()
    try:
        driver.goto('data:text/html,' + quote(PAGE))
        result = run(driver, 'page', GOAL, plan, max_screens=4)
        field = driver._page.locator('[aria-label=result]')
        actual = field.input_value() if field.count() else None
        passed = result.ok and actual == 'TACTO-2026-confirmed' and actual in result.said
        emit('result', passed=passed, loop_ok=result.ok, why=result.why,
             said=result.said, dom_result=actual, model_calls=calls,
             seconds=round(time.monotonic() - started, 2), provider_calls=result.provider_calls,
             requested_provider=args.provider)
        return 0 if passed else 1
    finally:
        driver.close()


if __name__ == '__main__':
    raise SystemExit(main())
