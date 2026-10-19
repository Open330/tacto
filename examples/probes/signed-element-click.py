"""Owned fixture only: pointer clicks on elements that offer no AXPress.

A messenger's room list (KakaoTalk, 2026-10-10) answers neither AXPress nor
AXOpen: its rows open on a double click and its profile button only takes a
mouse down. The signed helper reaches both with window-bound clicks at the
element's hit-tested centre, the app left in the background. Effects are read
from the fixture's own label, which only its receiving views write, and a
double click is also replayed through a Flow whose postcondition is the opened
room's window — the result, not the posting, is what passes.
"""
from pathlib import Path
import json
import hashlib
import os
import plistlib
import subprocess
import tempfile
import time

TARGET = 'ai.callabo.nlc.list-fixture'


def main(driver, output):
    from tacto.driver import Driver, _attr
    from tacto.flow import Flow, Step, Selector, Postcondition, replay, pin_params
    from tacto.contract import Envelope, ContextBinding
    from ApplicationServices import AXUIElementCreateApplication
    from tacto.input_audit_process import ProcessInputAudit
    root = Path(__file__).resolve().parents[2]
    source_paths = list((root / 'tacto').glob('*.py')) + list((root / 'nlc').glob('*.py')) + [Path(__file__).resolve(),
        root / 'tests/fixtures/NativeListFixture.swift', root / 'helper/tacto-helper',
        root / 'helper/Tacto Helper.app/Contents/MacOS/TactoHelper']
    def source_subset():
        return {str(path.relative_to(root)):hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(source_paths)}
    source_before = source_subset()
    local = Driver()
    audit = ProcessInputAudit(interval=.005, startup_timeout=10, stop_timeout=3)
    report = {'passed': False, 'scope': 'owned background AppKit list; element pointer clicks',
              'transport': type(driver).__name__}
    process = None
    interrupted = None
    report_fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        assert local._resolve(TARGET, launch=False) is None, 'fixture already running'
        assert {'action.double_click', 'click.window_bound_element'} <= set(driver.capabilities)
        with tempfile.TemporaryDirectory(prefix='nlc-signed-element-click-') as work:
            app = Path(work) / 'NLC List Fixture.app'
            binary = app / 'Contents/MacOS/NativeListFixture'
            binary.parent.mkdir(parents=True)
            (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({
                'CFBundleIdentifier': TARGET, 'CFBundleExecutable': binary.name,
                'CFBundleName': app.stem, 'CFBundlePackageType': 'APPL', 'LSUIElement': True}))
            subprocess.run(['xcrun', 'swiftc', '-parse-as-library',
                str(root / 'tests/fixtures/NativeListFixture.swift'), '-o', str(binary)],
                check=True, capture_output=True, timeout=60)
            with audit:
                try:
                    close_signal = Path(work) / 'close-owned-window'
                    process = subprocess.Popen([str(binary), '--close-owned-window', str(close_signal)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    deadline = time.monotonic() + 15
                    while local._resolve(TARGET, launch=False) != process.pid:
                        assert time.monotonic() < deadline and process.poll() is None
                        time.sleep(.1)
                    time.sleep(.5)

                    def host():
                        stack = [AXUIElementCreateApplication(process.pid)]
                        while stack:
                            node = stack.pop()
                            if _attr(node, 'AXIdentifier') == 'list-state':
                                return dict(part.split('=', 1) for part in str(_attr(node, 'AXValue')).split())
                            stack.extend(_attr(node, 'AXChildren') or [])
                        raise AssertionError('state label absent')

                    def observe():
                        view = driver.observe(TARGET, 'tree_full')
                        assert view.get('type') == 'observation', view
                        return view

                    report['initial'] = host()
                    assert report['initial']['active'] == 'false'
                    view = observe()
                    report['tree'] = view['tree']
                    index, _ = Selector(role='Window', title='NLC List Fixture').resolve(view['tree'])
                    prepared = driver.act(view['obs_id'], TARGET, {'name': 'prepare_window', 'element_index': index})
                    report['preparation'] = prepared
                    assert prepared.get('type') == 'acted', prepared
                    time.sleep(.3)
                    assert host()['key'] == 'true', host()

                    # 1. One click on a control with no AXPress.
                    view = observe()
                    index, _ = Selector(role='Group', desc='Profile').resolve(view['tree'])
                    before = host()
                    clicked = driver.act(view['obs_id'], TARGET, {'name': 'click', 'element_index': index})
                    time.sleep(.3)
                    report['profile'] = {'response': clicked, 'before': before, 'after': host()}
                    assert clicked.get('input_route') == 'window_bound_element_click', clicked
                    assert clicked.get('posted_events') == 2, clicked
                    assert report['profile']['after']['profileDowns'] == '1', report['profile']
                    assert report['profile']['after']['routeErrors'] == '0', report['profile']

                    # 2. A double click on a row, directly.
                    view = observe()
                    row = Selector(value='Room B', click_ancestor=['Row', 'Cell'])
                    index, _ = row.resolve(view['tree'])
                    opened = driver.act(view['obs_id'], TARGET, {'name': 'double_click', 'element_index': index})
                    time.sleep(.4)
                    report['double_click'] = {'response': opened, 'after': host()}
                    assert opened.get('type') == 'acted', opened
                    assert report['double_click']['after']['opens'] == '1', report['double_click']
                    assert report['double_click']['after']['opened'] == 'Room_B', report['double_click']
                    # Window labels are JSON-quoted now; match the field, not the text.
                    assert Selector(role='Window', title='Room B').resolve_all(observe()['tree'])

                    # 3. The same through replay: the postcondition is the window.
                    env = Envelope.minimal()
                    step = Step('double_click', Selector(value='Room C', click_ancestor=['Row', 'Cell']),
                        safety=env.contract_for({'write_ephemeral'}, 'open room'),
                        postcondition=Postcondition(field=Selector(role='Window', title='Room C'),
                                                    attribute='Title', equals='Room C'))
                    result = replay(driver, Flow('open_room', TARGET, [step], schema=2))
                    report['replay'] = {'ok': result.ok, 'log': result.log, 'events': result.events,
                                        'after': host()}
                    assert result.ok, result.log
                    assert report['replay']['after']['opens'] == '2', report['replay']
                    # 4. Return on a selected row opens it (bound submit).
                    assert 'press_key.return_bound' in driver.capabilities
                    view = observe()
                    index, _ = Selector(value='Room A', click_ancestor=['Row']).resolve(view['tree'])
                    selected = driver.act(view['obs_id'], TARGET, {'name': 'click', 'element_index': index})
                    time.sleep(.3)
                    assert selected.get('input_route') == 'window_bound_element_click', selected
                    assert host()['selected'] == '0', host()

                    def submit(selector, postcondition, binding_field):
                        params = {'key': 'Return'}
                        return Step('press_key', selector, params=params, is_submit=True,
                            safety=env.contract_for({'write_ephemeral'}, 'submit'),
                            binding=ContextBinding(bundle_id=TARGET, operation='press_key',
                                                   params_digest=pin_params(params), **binding_field),
                            postcondition=postcondition)
                    result = replay(driver, Flow('open_by_return', TARGET, [submit(
                        Selector(value='Room A', click_ancestor=['Row']),
                        Postcondition(field=Selector(role='Window', title='Room A'),
                                      attribute='Title', equals='Room A'),
                        {'value': 'Room A'})], schema=2))
                    report['row_return'] = {'ok': result.ok, 'log': result.log, 'events': result.events,
                                            'after': host()}
                    assert result.ok, result.log
                    assert report['row_return']['after']['opened'] == 'Room_A', report['row_return']

                    # 5. Return in the message field SENDS: declared as a mere
                    #    UI change it is refused before any key (Codex review
                    #    of f339633); step 6 sends it as transmit.
                    view = observe()
                    index, _ = Selector(identifier='message-field').resolve(view['tree'])
                    focused = driver.act(view['obs_id'], TARGET, {'name': 'click', 'element_index': index})
                    time.sleep(.3)
                    view = observe()
                    index, _ = Selector(identifier='message-field').resolve(view['tree'])
                    typed = driver.act(view['obs_id'], TARGET,
                                       {'name': 'set_value', 'element_index': index, 'value': 'hello there'})
                    assert typed.get('type') == 'acted', typed
                    result = replay(driver, Flow('send_by_return', TARGET, [submit(
                        Selector(identifier='message-field'),
                        Postcondition(field=Selector(identifier='list-state'), attribute='Value',
                                      contains='sent=1 lastSent=hello_there'),
                        {'identifier': 'message-field'})], schema=2))
                    report['field_return_as_ephemeral'] = {'focus': focused, 'ok': result.ok, 'log': result.log,
                                              'events': result.events, 'after': host()}
                    assert not result.ok and result.events == [], result.log
                    assert 'contract_effect_mismatch' in str(result.log), result.log
                    assert report['field_return_as_ephemeral']['after']['sent'] == '0', report['field_return_as_ephemeral']

                    # 6. A transmit send: asked first, then witnessed in the
                    #    sender's own window by an independent AX reader.
                    from tacto.contract import issue_approval
                    from tacto.transcript_receipt import TranscriptReceipt
                    view = observe()
                    index, _ = Selector(identifier='message-field').resolve(view['tree'])
                    typed = driver.act(view['obs_id'], TARGET,
                                       {'name': 'set_value', 'element_index': index, 'value': 'second'})
                    assert typed.get('type') == 'acted', typed
                    params = {'key': 'Return'}
                    send = Step('press_key', Selector(identifier='message-field'), params=params,
                        is_submit=True, safety=env.contract_for({'transmit'}, 'message'),
                        binding=ContextBinding(bundle_id=TARGET, identifier='message-field',
                                               operation='press_key', params_digest=pin_params(params)),
                        postcondition=Postcondition(field=Selector(identifier='list-state'),
                                                    attribute='Value', contains='sent=1 lastSent=second'))
                    def reader(target):
                        seen = local.observe(target, 'tree_full')
                        assert seen.get('type') == 'observation', seen
                        return seen['tree']
                    receipt = TranscriptReceipt(reader, target=TARGET, recipient='NLC List Fixture',
                        body='second', recipient_field=Selector(role='Window', title='NLC List Fixture'),
                        messages=Selector(role='StaticText', ancestor='transcript'), timeout_seconds=3)
                    flow = Flow('send_message', TARGET, [send], schema=2)
                    asked = replay(driver, flow, prepare_commit=receipt.prepare_commit,
                                   after_commit=receipt.after_commit, commit_expected=receipt.commit_expected)
                    gated = [e for e in asked.log if e.get('status') == 'gated']
                    assert not asked.ok and gated, asked.log
                    assert '받는 사람: NLC List Fixture · 보낼 문장: second' in gated[0]['detail']['what'], gated
                    assert host()['sent'] == '0', host()
                    # One receipt per run: the asking run already used its prepare.
                    receipt = TranscriptReceipt(reader, target=TARGET, recipient='NLC List Fixture',
                        body='second', recipient_field=Selector(role='Window', title='NLC List Fixture'),
                        messages=Selector(role='StaticText', ancestor='transcript'), timeout_seconds=3)
                    result = replay(driver, flow, {0: issue_approval(send.binding, send.safety)},
                                    prepare_commit=receipt.prepare_commit, after_commit=receipt.after_commit,
                                    commit_expected=receipt.commit_expected)
                    proof = next(iter(result.commit_evidence.values()), None)
                    report['transmit'] = {'asked_first': asked.log, 'ok': result.ok, 'log': result.log,
                                          'events': result.events, 'after': host(),
                                          'evidence': repr(proof)}
                    assert result.ok and result.events[0]['state'] == 'confirmed', result.log
                    assert report['transmit']['after']['sent'] == '1', report['transmit']

                    # 7. A context menu left open is closed with AXCancel, not
                    #    Escape, and nothing in it is chosen.
                    assert 'action.close_menu' in driver.capabilities
                    view = observe()
                    index, _ = Selector(role='Group', desc='Profile').resolve(view['tree'])
                    shown = driver.act(view['obs_id'], TARGET,
                                       {'name': 'secondary_action', 'element_index': index, 'action': 'AXShowMenu'})
                    time.sleep(.5)
                    opened_menu = host()

                    def ax_shape():
                        # Where the open menu sits in AX (roles only, no text).
                        app_el = AXUIElementCreateApplication(process.pid)
                        def roles(node, depth):
                            kids = (_attr(node, 'AXChildren') or [])[:30] if depth else []
                            return [_attr(node, 'AXRole'), [roles(k, depth - 1) for k in kids]]
                        chain, node = [], _attr(app_el, 'AXFocusedUIElement')
                        for _ in range(10):
                            if node is None: break
                            chain.append(_attr(node, 'AXRole')); node = _attr(node, 'AXParent')
                        return {'app': roles(app_el, 3), 'focused_chain': chain}
                    report['menu_ax_shape'] = ax_shape()
                    view = driver.observe(TARGET, 'tree_full')
                    closed = driver.act(view['obs_id'], TARGET, {'name': 'close_menu'})
                    time.sleep(.3)
                    report['close_menu'] = {'show': shown, 'while_open': opened_menu,
                                            'response': closed, 'after': host()}
                    assert opened_menu['menuOpen'] == 'true', report['close_menu']
                    assert closed.get('type') == 'acted' and closed.get('menus_open') == 0, closed
                    assert report['close_menu']['after']['menuOpen'] == 'false', report['close_menu']
                    assert report['close_menu']['after']['menuPicks'] == '0', report['close_menu']
                    view = driver.observe(TARGET, 'tree_full')
                    again = driver.act(view['obs_id'], TARGET, {'name': 'close_menu'})
                    assert again.get('effect') == 'not_started', again

                    # 8. The fixture closes only its own window as setup;
                    #    signed reopen_window brings it back without activation.
                    assert 'action.reopen_window' in driver.capabilities
                    signal_fd = os.open(close_signal, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(signal_fd, 'w') as signal:
                        signal.write('close-owned-main')
                    until_closed = time.monotonic() + 3
                    app_element = AXUIElementCreateApplication(process.pid)
                    while any(_attr(window, 'AXTitle') == 'NLC List Fixture'
                              for window in (_attr(app_element, 'AXWindows') or [])):
                        assert time.monotonic() < until_closed, 'owned main window did not close'
                        time.sleep(.05)
                    view = driver.observe(TARGET, 'tree_full', require_window=False)
                    assert view.get('type') == 'observation', view
                    assert not Selector(role='Window', title='NLC List Fixture').resolve_all(view['tree']), view['tree']
                    reopened = driver.act(view['obs_id'], TARGET, {'name': 'reopen_window'})
                    time.sleep(.4)
                    after_reopen = observe()
                    report['reopen'] = {'response': reopened, 'tree_has_window': bool(
                                        Selector(role='Window', title='NLC List Fixture').resolve_all(after_reopen['tree'])),
                                        'after': host()}
                    assert reopened.get('type') == 'acted', reopened
                    assert reopened.get('activated_target') is not True, reopened
                    assert report['reopen']['tree_has_window'], report['reopen']
                    assert report['reopen']['after']['reopens'] == '1', report['reopen']

                    # 9. The model loop, with a scripted planner: a row that has
                    #    no name of its own is offered by the text inside it,
                    #    selected, then opened with a declared Return submit.
                    from tacto.general import run as general_run
                    before = host()
                    seen = []
                    def planner(goal, cands, history):
                        row = next((c for c in cands if c.get('role') == 'Row'
                                    and c.get('label') == 'Room C'), None)
                        seen.append({'row': None if row is None else
                                     {k: row.get(k) for k in ('label', 'can', 'via_child')}})
                        if row is None or len(seen) > 3:
                            return {'done': True}
                        if len(seen) == 1:
                            # Room windows opened above hold the app's focus;
                            # a pointer click needs its own window prepared.
                            window = next(c for c in cands if c.get('role') == 'Window'
                                          and c.get('label') == 'NLC List Fixture')
                            return {'actions': [{'do': 'prepare_window', 'candidate': window}],
                                    'expect': None}
                        if len(seen) == 2:
                            return {'actions': [{'do': 'click', 'candidate': row}], 'expect': None}
                        return {'actions': [{'do': 'submit', 'candidate': row}],
                                'expect': f"opens={int(before['opens']) + 1}"}
                    walked = general_run(driver, TARGET, 'Room C 방 열어줘', planner, max_screens=4)
                    report['general_row_submit'] = {'ok': walked.ok, 'verified': walked.verified,
                        'why': walked.why, 'seen': seen, 'events': walked.events, 'after': host()}
                    assert seen[0]['row'] and seen[0]['row']['via_child'], seen
                    assert 'submit' in seen[0]['row']['can'], seen
                    assert walked.ok and walked.verified, report['general_row_submit']
                    after = report['general_row_submit']['after']
                    assert after['opens'] == str(int(before['opens']) + 1), report['general_row_submit']
                    assert after['opened'] == 'Room_C', report['general_row_submit']
                    assert after['sent'] == before['sent'], report['general_row_submit']

                    report['final'] = host()
                    # NSApp.isActive of this accessory fixture turns true after
                    # prepare_window (measured 2026-10-10) while the frontmost
                    # process, pointer and focus stay unchanged; the input
                    # audit below is what decides "not brought forward".
                    report['fixture_reported_active'] = report['final']['active']
                    report['passed'] = True
                finally:
                    if process is not None:
                        if process.poll() is None: process.terminate()
                        process.wait(timeout=5)
                        report['owned_child_reaped'] = True
    except BaseException as exc:
        report.update(passed=False, error=type(exc).__name__ + ': ' + str(exc))
        if not isinstance(exc, Exception):
            interrupted = exc
            report['interrupted'] = True
    report['input_audit'] = audit.report()
    if report['input_audit']['status'] != 'UNCHANGED_SAMPLED': report['passed'] = False
    source_after = source_subset()
    report.update(source_subset_before=source_before, source_subset_after=source_after,
                  source_subset_stable=source_before == source_after, all_dependencies_frozen=False)
    if source_before != source_after:
        report['passed'] = False
    with os.fdopen(report_fd, 'w') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, default=str)
    if interrupted is not None:
        raise interrupted
    return report
