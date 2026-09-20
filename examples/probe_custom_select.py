"""Real-model custom ARIA select probe on a disposable local page."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PAGE = '''<section aria-label="Billing">
<button role="combobox" aria-label="Choice" aria-controls="choices" aria-expanded="false"
 onclick="this.setAttribute('aria-expanded','true');choices.hidden=false">Choose</button>
<div id="choices" role="listbox" hidden>
<div role="option" aria-selected="false" onclick="choose(this)">Alpha</div>
<div role="option" aria-selected="false" onclick="choose(this)">Beta</div></div></section>
<input aria-label="Result" value="pending" readonly><script>
window.writes=0;function choose(el){window.writes++;
document.querySelectorAll('[role=option]').forEach(option=>option.setAttribute('aria-selected',String(option===el)));
document.querySelector('[aria-label=Result]').value=el.textContent;
choices.hidden=true;document.querySelector('[role=combobox]').setAttribute('aria-expanded','false')}
</script>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider', choices=['codex', 'claude'], default='codex')
    args = parser.parse_args()
    from nlc.ask import ask_here_paged
    from nlc.browser import BrowserDriver
    from nlc.general import run

    def snapshot():
        paths = sorted((ROOT / 'nlc').glob('*.py')) + [Path(__file__)]
        return hashlib.sha256(b''.join(str(p.relative_to(ROOT)).encode() +
            hashlib.sha256(p.read_bytes()).digest() for p in paths)).hexdigest()
    before = snapshot()
    plans = []
    def planner(goal, candidates, done):
        result = ask_here_paged(goal, candidates, done, provider=args.provider)
        plans.append(result)
        return result
    driver = BrowserDriver()
    start = time.monotonic()
    try:
        driver.goto('data:text/html,' + quote(PAGE))
        result = run(driver, 'page', 'Choice 메뉴에서 Beta를 선택하고 Result 값을 읽어 알려주세요.',
                     planner, max_screens=8, timeout_seconds=150)
        value = driver._page.locator('[aria-label=Result]').input_value()
        writes = driver._page.evaluate('window.writes')
        after = snapshot()
        passed = result.ok and value == 'Beta' and writes == 1 and 'Beta' in result.said and before == after
        print(json.dumps(dict(passed=passed, provider=args.provider, value=value,
            writes=writes, said=result.said, loop_ok=result.ok, why=result.why,
            plans=plans, seconds=round(time.monotonic()-start, 2),
            source_before=before, source_after=after, source_stable=before == after), ensure_ascii=False))
        return 0 if passed else 1
    finally:
        driver.close()


if __name__ == '__main__':
    raise SystemExit(main())
