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
import plistlib
import subprocess
import tempfile
import time

TARGET = 'ai.callabo.nlc.list-fixture'


def main(driver, output):
    from nlc.driver import Driver, _attr
    from nlc.flow import Flow, Step, Selector, Postcondition, replay, pin_params
    from nlc.contract import Envelope, ContextBinding
    from ApplicationServices import AXUIElementCreateApplication
    from nlc.input_audit_process import ProcessInputAudit
    root = Path(__file__).resolve().parents[2]
    local = Driver()
    audit = ProcessInputAudit(interval=.005, startup_timeout=10, stop_timeout=3)
    report = {'passed': False, 'scope': 'owned background AppKit list; element pointer clicks',
              'transport': type(driver).__name__}
    process = None
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
                    process = subprocess.Popen([str(binary)],
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
                    assert 'Window  Title: Room B' in observe()['tree']

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
                    from nlc.contract import issue_approval
                    from nlc.transcript_receipt import TranscriptReceipt
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
    except Exception as exc:
        report.update(passed=False, error=type(exc).__name__ + ': ' + str(exc))
    report['input_audit'] = audit.report()
    if report['input_audit']['status'] != 'UNCHANGED_SAMPLED': report['passed'] = False
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return report
