"""Disposable local fixtures for model-driven general-loop benchmarks.

Known target and host-provided save witness mapping are intentional constraints;
this does not benchmark arbitrary-app routing or witness discovery.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
import uuid
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CONTENT = 'NLC-local-benchmark'
SELECT = '''<label>Choice<select aria-label="Choice" onchange="document.querySelector('[aria-label=Result]').value=this.value==='beta'?'Beta-confirmed':'pending'">
<option value="alpha">Alpha</option><option value="beta">Beta</option></select></label>
<input aria-label="Result" value="pending" readonly>'''
SCROLL = '''<div id="panel" style="height:100px;overflow:auto" onscroll="if(this.scrollTop>=this.scrollHeight-this.clientHeight-1){window.reached=true;document.querySelector('[aria-label=Marker]').value='Bottom-confirmed'}">
<button>Scroll anchor</button><div style="height:300px"></div></div>
<input aria-label="Marker" value="pending" readonly>'''
SAVE = '''<input aria-label="Content" value=""><input aria-label="location" readonly>
<input aria-label="filename" value="note.txt" readonly><input aria-label="overwrite" value="" readonly>
<input aria-label="readback" value="" readonly><output>Draft</output>
<button onclick="saveLocal(document.querySelector('[aria-label=location]').value,
 document.querySelector('[aria-label=filename]').value,
 document.querySelector('[aria-label=Content]').value,
 (!document.querySelector('select') || document.querySelector('select').value==='beta') &&
 (!document.querySelector('#panel') || window.reached===true)).then(value=>{
 document.querySelector('[aria-label=readback]').value=value;
 document.querySelector('output').textContent='Saved'})">Save</button>'''
FIXTURES = {'select-read': SELECT, 'scroll-read': SCROLL,
            'save-readback': SAVE, 'combined': SELECT + SCROLL + SAVE}
GOALS = {
 'select-read': 'Choose Beta in Choice, then read the Result field aloud.',
 'scroll-read': 'Scroll the panel using Scroll anchor to the bottom, then read Marker aloud.',
 'save-readback': 'Enter NLC-local-benchmark in Content. Save as note.txt in the prefilled location, then read readback aloud.',
 'combined': 'Enter NLC-local-benchmark in Content, choose Beta in Choice, scroll the panel using Scroll anchor to the bottom, then Save as note.txt in the prefilled location and read readback aloud.'}
ORACLES = {
 'select-read': ['loop_done', 'selected_value', 'read_answer'],
 'scroll-read': ['loop_done', 'scroll_position', 'read_answer'],
 'save-readback': ['loop_done', 'destination', 'file_readback', 'read_answer', 'receipt'],
 'combined': ['loop_done', 'form_value', 'selected_value', 'scroll_position', 'destination', 'file_readback', 'read_answer', 'receipt']}


def execute(task_id, provider='codex', timeout=45, planner=None):
    from nlc.ask import what_to_do_here
    from nlc.authoring import _ask_model, _extract_json
    from nlc.browser import BrowserDriver
    from nlc.general import run
    from nlc.contract import Envelope
    from nlc.outcome import spec_for, verify
    from nlc.commit_evidence import ArtifactEvidence, CommitEvidence
    started = time.monotonic()
    calls = 0
    plans = []
    def record(answer):
        plans.append({
            "actions": [{"do": a.get("do"), "label": a.get("candidate", {}).get("label")}
                        for a in (answer or {}).get("actions", [])],
            "done": (answer or {}).get("done") is True, "accepted": answer is not None})
        return answer
    def plan(goal, candidates, done):
        nonlocal calls
        calls += 1
        if planner is not None:
            return record(planner(goal, candidates, done))
        question = what_to_do_here(goal, candidates, done)
        try:
            answer = _extract_json(_ask_model(question.prompt, provider, timeout=timeout, expect_json=True))
            return record(question.check(answer))
        except Exception:
            return record(None)
    with tempfile.TemporaryDirectory(prefix='nlc-benchmark-') as folder:
        driver = BrowserDriver()
        writes = []
        active_commit = None
        try:
            def save(location, name, content, ready):
                # Host enforces the fixture's sole authorized destination.
                if active_commit is None:
                    raise ValueError('write has no host-prepared commit event')
                if location != folder or name != 'note.txt' or ready is not True:
                    raise ValueError('destination or prerequisite mismatch')
                path = Path(folder) / 'note.txt'
                if path.exists():
                    raise ValueError('duplicate write')
                path.write_text(content)
                writes.append(path)
                # Receipt is created by the actual host write, never by the model.
                active_commit['receipt'] = {
                    'receipt_id': uuid.uuid4().hex, 'event_id': active_commit['event_id'],
                    'action_digest': active_commit['action_digest'], 'path': str(path),
                    'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                return path.read_text()
            driver._page.expose_function('saveLocal', save)
            driver.goto('data:text/html,' + quote(FIXTURES[task_id]))
            kwargs = {}
            spec = None
            if task_id in ('save-readback', 'combined'):
                driver._page.locator('[aria-label=location]').evaluate('(el,value)=>el.value=value', folder)
                env = Envelope(quote=GOALS[task_id], allows=frozenset({'read', 'write_ephemeral', 'write_persistent'}), scope='item')
                spec = spec_for('save_file', env,
                    {'저장 위치': folder, '저장한 이름': 'note.txt', '다시 읽은 내용': CONTENT},
                    {'저장 위치': 'request', '저장한 이름': 'request', '다시 읽은 내용': 'request'})
                def where(name, candidates):
                    label = {'저장 위치': 'location', '저장한 이름': 'filename',
                             '요청하지 않은 덮어쓰기 없음': 'overwrite', '다시 읽은 내용': 'readback'}[name]
                    c = next(c for c in candidates if c['label'] == label)
                    return {'candidate': c, 'pick': candidates.index(c)}
                def prepare_commit(event):
                    nonlocal active_commit
                    path = Path(folder) / 'note.txt'
                    if path.exists() or active_commit is not None:
                        raise ValueError('save must have one fresh commit event')
                    active_commit = {'event_id': event['event_id'],
                                     'action_digest': event['action_digest'], 'receipt': None}
                    return active_commit
                def after_commit(event, driver_result, context):
                    if context is not active_commit:
                        return None
                    # Pump pending JS->host callback before inspecting actual disk.
                    try:
                        driver._page.wait_for_function("document.querySelector('output').textContent==='Saved'", timeout=1500)
                    except Exception:
                        return None
                    receipt = context['receipt']
                    path = Path(folder) / 'note.txt'
                    if (not receipt or len(writes) != 1 or writes[0] != path
                            or receipt['event_id'] != event['event_id']
                            or receipt['action_digest'] != event['action_digest']
                            or receipt['path'] != str(path) or not path.is_file()):
                        return None
                    digest = hashlib.sha256(path.read_bytes()).hexdigest()
                    if digest != receipt['sha256'] or digest != hashlib.sha256(CONTENT.encode()).hexdigest():
                        return None
                    return CommitEvidence(event['event_id'], event['action_digest'],
                        ArtifactEvidence(receipt['receipt_id'], str(path), digest))
                kwargs = dict(envelope=env, spec=spec, ask_where=where,
                              approve=lambda _: True, prepare_commit=prepare_commit,
                              after_commit=after_commit)
            result = run(driver, 'page', GOALS[task_id], plan, max_screens=12, **kwargs)
            evidence = []
            def check(oracle, passed, observed):
                evidence.append(dict(oracle=oracle, passed=bool(passed), observed=observed))
            check('loop_done', result.ok, result.ok)
            if task_id in ('select-read', 'combined'):
                value = driver._page.locator('select').input_value()
                check('selected_value', value == 'beta', value)
            if task_id in ('scroll-read', 'combined'):
                pos = driver._page.locator('#panel').evaluate('(el)=>({top:el.scrollTop,max:el.scrollHeight-el.clientHeight})')
                check('scroll_position', pos['max'] > 0 and pos['top'] >= pos['max'] - 1, pos)
            if task_id == 'combined':
                value = driver._page.locator('[aria-label=Content]').input_value()
                check('form_value', value == CONTENT, value)
            if spec:
                path = Path(folder) / 'note.txt'
                check('destination', len(writes) == 1 and writes[0] == path,
                      {'name': 'note.txt', 'writes': len(writes), 'temporary_directory': True})
                content = path.read_text() if path.exists() else None
                check('file_readback', content == CONTENT, content)
                receipt = verify(spec, result, result.after_tree)
                check('receipt', receipt.ok, receipt.ok)
            expected = CONTENT if spec else ('Beta-confirmed' if task_id == 'select-read' else 'Bottom-confirmed')
            check('read_answer', expected in result.said, result.said)
            return dict(event='benchmark_result', task_id=task_id,
                        passed=all(e['passed'] for e in evidence), evidence=evidence,
                        model_calls=calls, elapsed_seconds=time.monotonic()-started,
                        why=result.why, plans=plans, planner_mode='fixed' if planner else 'real-model')
        finally:
            driver.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', choices=FIXTURES, required=True)
    parser.add_argument('--provider', default='codex')
    parser.add_argument('--timeout', type=int, default=45)
    args = parser.parse_args()
    result = execute(args.task, args.provider, args.timeout)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
