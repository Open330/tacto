"""Opt-in real-model canvas click on a disposable browser fixture.

The model sees pixels only. The host authorizes clicks only within this local
fixture and independently verifies the canvas click counter; no external effect
or generic application completion is inferred from that counter.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PAGE = '''<!doctype html><style>body{margin:0}</style>
<canvas id="board" width="800" height="450"></canvas><script>
window.fixtureClicks=[]; const c=board.getContext('2d');
c.fillStyle='#f5f6f8';c.fillRect(0,0,800,450);
c.fillStyle='#151515';c.font='26px sans-serif';
c.fillText('Click the blue rectangle once',60,70);
c.fillStyle='#186ade';c.fillRect(430,230,200,100);
c.fillStyle='#d74b37';c.fillRect(100,230,200,100);
c.fillStyle='white';c.font='24px sans-serif';c.fillText('BLUE',495,289);c.fillText('RED',173,289);
board.addEventListener('click',e=>{const r=board.getBoundingClientRect();
const x=e.clientX-r.left,y=e.clientY-r.top;
window.fixtureClicks.push({x,y,correct:x>=430&&x<630&&y>=230&&y<330});
c.fillStyle='#151515';c.fillText('Click received',60,400);});
</script>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout', type=int, default=45)
    parser.add_argument('--model')
    parser.add_argument('--provider', choices=['codex', 'claude'], default='codex')
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error('--timeout must be positive')
    from nlc.browser import BrowserDriver
    from nlc.visual import plan_visual
    from nlc.visual_actions import run_visual

    root = Path(__file__).resolve().parents[1]
    files = ['nlc/visual.py', 'nlc/visual_actions.py', 'nlc/browser.py',
             'nlc/claude_result.py', 'nlc/provider_result.py',
             'examples/probe_visual_task.py']
    def hashes():
        return {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in files}
    before = hashes()
    driver = BrowserDriver()
    calls = 0
    start = time.monotonic()
    url = 'data:text/html,' + quote(PAGE)
    def emit(event, **values):
        print(json.dumps(dict(event=event, **values), ensure_ascii=False), flush=True)
    def plan(capture, goal, history):
        nonlocal calls
        calls += 1
        try:
            proposal = plan_visual(capture, goal, history, provider=args.provider,
                                   model=args.model, timeout=args.timeout)
        except Exception as exc:
            emit('planner_error', code=type(exc).__name__, detail=str(exc))
            raise
        emit('plan', number=calls, proposal=proposal, capture=capture.to_dict())
        return proposal
    def authorize(proposal, capture):
        return (driver._page.url == url and proposal.get('action') == 'click'
                and len(driver._page.evaluate('window.fixtureClicks')) == 0)
    def verify(capture, history):
        clicks = driver._page.evaluate('window.fixtureClicks')
        return len(clicks) == 1 and clicks[0]['correct'] is True
    try:
        driver.goto(url)
        result = run_visual(driver, 'page', 'Click the blue rectangle exactly once.',
                            plan, authorize, verify, max_steps=2,
                            timeout_seconds=args.timeout + 20)
        clicks = driver._page.evaluate('window.fixtureClicks')
        after = hashes()
        passed = (result.state == 'DONE' and len(clicks) == 1
                  and clicks[0]['correct'] and before == after)
        emit('result', passed=bool(passed), state=result.state, reason=result.reason,
             clicks=clicks, model_calls=calls, provider=args.provider, requested_model=args.model,
             seconds=round(time.monotonic()-start, 2), source_before=before,
             source_after=after, source_stable=before == after)
        return 0 if passed else 1
    finally:
        driver.close()


if __name__ == '__main__':
    raise SystemExit(main())
