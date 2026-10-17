"""Live image read of randomized, owned canvas scoreboards, outside AX text.

Run with PYTHONPATH=. .venv/bin/python examples/probes/browser-visual-score.py
REPORT_DIRECTORY. Uses headless Chromium, never the user's browser or desktop.
This is an ordinary game score display, not a CAPTCHA or security challenge.
No click, save/send action, or completion witness is inferred from model text.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import time

from tacto.browser import BrowserDriver
from tacto.provider_observation import capture_provider_calls
from tacto.visual import capture_browser, query_visual, VisualError


def source_hashes():
    root = Path(__file__).resolve().parents[2]
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in ('tacto/codex_result.py', 'tacto/browser.py', 'tacto/visual.py',
                         'tacto/provider_images.py', 'tacto/provider_observation.py',
                         'examples/probes/browser-visual-score.py')}


def private_json(path, value):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--provider', choices=('codex', 'claude'), default='codex')
    parser.add_argument('--model')
    parser.add_argument('--timeout', type=float, default=60)
    args = parser.parse_args()
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    checkpoint = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    before_hashes = source_hashes()
    reports = []
    driver = BrowserDriver(headless=True)
    try:
        # Position, contrast, and font change independently of the random score.
        for index, (background, foreground, font, x, y) in enumerate((
                ('#fff', '#111', 'bold 64px sans-serif', 60, 120),
                ('#152536', '#fff', 'bold 48px monospace', 160, 230),
                ('#eee', '#123', '32px sans-serif', 420, 350))):
            score = 1000 + secrets.randbelow(9000)
            driver._page.set_viewport_size({'width': 800, 'height': 480})
            driver._page.set_content('<title>Owned scoreboard fixture</title>'
                '<canvas width="800" height="480" aria-label="Game scoreboard"></canvas>')
            driver._page.evaluate('''([score, bg, fg, font, x, y]) => {
                const c = document.querySelector('canvas').getContext('2d');
                c.fillStyle = bg; c.fillRect(0, 0, 800, 480);
                c.fillStyle = fg; c.font = font;
                c.fillText('Score: ' + score, x, y);
            }''', [score, background, foreground, font, x, y])
            observation = driver.observe()
            dom = driver._page.content()
            opaque = (str(score) not in observation['tree'] and str(score) not in dom)
            report = {'case': index, 'expected_score': str(score),
                      'score_absent_from_ax_and_dom': opaque, 'passed': False,
                      'source_checkpoint': checkpoint, 'provider': args.provider,
                      'requested_model': args.model}
            if opaque:
                capture = capture_browser(driver, observation['obs_id'])
                capture.save(args.output / f'case-{index}.png')
                report['capture'] = capture.to_dict()
                with capture_provider_calls() as calls:
                    try:
                        reply = query_visual(capture,
                            'What is the game score? Return only its decimal digits '
                            'inside the answer string.', provider=args.provider,
                            model=args.model, timeout=args.timeout)
                        report['answer'] = reply['answer']
                        report['passed'] = reply['answer'].strip() == str(score)
                    except VisualError as error:
                        report['error_code'] = str(error)
                    finally:
                        report['provider_calls'] = calls
            else:
                report['error_code'] = 'fixture_not_opaque'
            private_json(args.output / f'case-{index}.json', report)
            reports.append(report)
            print(json.dumps({'case': index, 'passed': report['passed'],
                              'error_code': report.get('error_code') }), flush=True)
    finally:
        driver.close()
    after_hashes = source_hashes()
    summary = {'scope': 'owned_headless_canvas_visual_read_only',
               'timestamp': time.time(), 'passed': sum(r['passed'] for r in reports),
               'total': len(reports), 'source_checkpoint': checkpoint,
               'authorizes_action': False, 'commit_witness': False,
               'native_ui_test': False, 'tool_isolation_proven': False}
    summary.update(source_before=before_hashes, source_after=after_hashes,
                   measured_source_subset_stable=before_hashes == after_hashes,
                   full_dependency_freeze=False)
    private_json(args.output / 'summary.json', summary)
    return 0 if all(r['passed'] for r in reports) else 1


if __name__ == '__main__':
    raise SystemExit(main())
