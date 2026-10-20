"""One fixed, model-free first task; input verification is not a Save receipt."""
import time
import math
from .app_launch import launch_app, present_app
from .document_label import window_attributes, window_flag
from .flow import Flow, Step, Selector, Postcondition, replay, pin_params, _replay_observation
from .contract import SafetyContract, Resource, ContextBinding
from .agent import Agent
from .library import textedit_new_document

TARGET = 'com.apple.TextEdit'
TEXT = 'Hello, World!\nTacto로 작성했습니다.'


def _windows(observation):
    """Observation-local tree indices mapped to stable process/window IDs."""
    identity = observation.get('window_identity')
    if not isinstance(identity, dict):
        raise ValueError('window_identity_unavailable')
    pid, start = identity.get('pid'), identity.get('process_start')
    if type(pid) is not int or pid <= 0 or type(start) not in (int, float) or not math.isfinite(start) or start <= 0:
        raise ValueError('window_identity_unavailable')
    mapping = {}
    rows = identity.get('windows')
    if not isinstance(rows, list):
        raise ValueError('window_identity_unavailable')
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('window_identity_unavailable')
        index, wid = row.get('element_index'), row.get('window_id')
        if type(index) is not int or index < 0 or type(wid) is not int or not 0 < wid <= 0xffffffff or index in mapping:
            raise ValueError('window_identity_unavailable')
        mapping[index] = wid
    documents = {}
    current = None
    body = []
    def finish():
        if current is None or not any(Selector(role='TextArea', identifier='First Text View').matches(line) for line in body):
            return
        attrs, focused, index, header = current
        wid = mapping.get(index)
        if wid is None or wid in documents:
            raise ValueError('window_identity_unavailable')
        documents[wid] = dict(attrs=attrs, focused=focused, tree='\n'.join([header, *body]))
    for line in observation['tree'].splitlines():
        attrs = window_attributes(line)
        if attrs is not None:
            finish()
            current = (attrs, window_flag(line, 'focused'), int(line.strip().split()[0]), line)
            body = []
        else:
            body.append(line)
    finish()
    return (pid, start), documents, set(mapping.values())


def run_first_task(driver, *, launch=launch_app, execute=replay, present=None):
    started = time.monotonic()
    def result(status, stage, **extra):
        if status == 'failed':
            extra.setdefault('repeat_write', False)
        return dict(type='first_run', status=status, stage=stage,
                    seconds=round(time.monotonic() - started, 3),
                    model_calls=0, save_command_sent=False, **extra)
    launched = launch(TARGET)
    if launched.get('type') != 'app_launch':
        return result('failed', 'launch', error=launched.get('error', 'launch_failed'),
                      effect=launched.get('effect', 'unknown'), repeat_write=False)
    # Share replay's transport/malformed-read handling; never retry an input
    # because a later observation failed.
    before = _replay_observation(driver.observe, TARGET, 'tree_full', require_window=False, window_identity=True)
    if before.get('type') != 'observation' or before.get('truncated'):
        return result('failed', 'observe', error=before.get('error', 'incomplete_observation'))
    try:
        owner, old, old_ids = _windows(before)
    except ValueError as exc:
        return result('failed', 'observe', error=str(exc))
    # No store lookup: a corrupt/modified user recipe cannot redefine the
    # onboarding task. The canonical library flow uses the normal policy.
    created = execute(driver, textedit_new_document())
    if not created.ok:
        return result('failed', 'new_document', error='new_document_not_verified')
    # The New menu's generic postcondition can see an EXISTING text area
    # before TextEdit publishes its new window. Settle by reading only; never
    # resend New or a write in response to delayed AX window creation.
    for identity_read in range(8):
        after = _replay_observation(driver.observe, TARGET, 'tree_full', window_identity=True)
        if after.get('type') != 'observation' or after.get('truncated'):
            return result('failed', 'new_document_identity',
                          error=after.get('error', 'incomplete_observation'), repeat_write=False)
        try:
            after_owner, windows, _ = _windows(after)
        except ValueError as exc:
            return result('failed', 'new_document_identity', error=str(exc))
        new_ids = set(windows) - set(old)
        if after_owner != owner:
            return result('failed', 'new_document_identity', error='application_identity_changed')
        if len(new_ids) == 1 and set(old).issubset(windows):
            break
        if len(new_ids) > 1 or identity_read == 7:
            return result('failed', 'new_document_identity', error='new_window_not_unique',
                          identity_reads=identity_read + 1,
                          before_document_count=len(old), after_document_count=len(windows),
                          new_document_count=len(new_ids),
                          missing_existing_document_count=len(set(old) - set(windows)))
        time.sleep(0.1)
    new_id = next(iter(new_ids))
    if new_id in old_ids:
        return result('failed', 'new_document_identity', error='new_window_not_unique')
    window = windows[new_id]
    attrs, focused = window['attrs'], window['focused']
    title = attrs.get('Title')
    if not title or attrs.get('Document') or not focused:
        return result('failed', 'new_document_identity', error='new_document_not_blank_and_focused')
    field = Selector(role='TextArea', identifier='First Text View', ancestor=title,
                     in_focused_window=True)
    # An empty AXValue may be omitted from the textual tree. Match exactly
    # one text area, and refuse any nonempty body before our only write.
    try:
        index, _ = field.resolve(after['tree'])
        line = next(line for line in after['tree'].splitlines() if line.strip() and line.strip().split(None, 1)[0] == str(index))
    except LookupError:
        return result('failed', 'new_document_identity', error='new_text_area_not_unique')
    if Selector._attr(line, 'Value') not in (None, ''):
        return result('failed', 'new_document_identity', error='new_document_not_empty')
    params = {'value': TEXT}
    # AX tree values encode line breaks as two visible characters. Match the
    # fixed welcome text in that representation; the AX write remains raw.
    condition = Postcondition(field=field, attribute='Value', equals=TEXT.replace('\n', '\\n'))
    step = Step('set_value', field, params=params, postcondition=condition,
                safety=SafetyContract(effects=['write_persistent'], reversibility='recoverable',
                    resource=Resource(object_type='new first-run document', object_identity=title, scope='item')),
                binding=ContextBinding(bundle_id=TARGET, role='TextArea', identifier='First Text View',
                    ancestor=title, operation='set_value', params_digest=pin_params(params),
                    resource_identity=title, resource_identity_from=dict(role='Window', title=title,
                        attribute='Title', in_focused_window=True)))
    flow = Flow(name='tacto_first_run', target=TARGET, steps=[step])
    approvals, blocked = Agent(driver=driver).authorize(flow, ask=lambda _: True)
    if blocked:
        return result('failed', 'authorize', error='first_run_contract_refused')
    written = execute(driver, flow, approvals=approvals)
    # Never retry a dispatched write, even when the normal persistent-write
    # engine lacks a commit receipt. A separate read proves only visible text.
    failures = [row for row in written.log if row.get('status') in ('error', 'gated', 'unverified')]
    allowed = (not written.ok and len(failures) == 1
               and failures[0].get('error') == 'commit_unverified'
               and failures[0].get('detail') == 'missing_typed_host_commit_evidence')
    if not written.ok and not allowed:
        return result('failed', 'write', error=failures[0].get('error', 'write_failed') if failures else 'write_failed',
                      repeat_write=False)
    fresh = _replay_observation(driver.observe, TARGET, 'tree_full', window_identity=True)
    try:
        fresh_owner, fresh_windows, _ = _windows(fresh)
        verified_window = fresh_windows.get(new_id)
    except (ValueError, KeyError, TypeError):
        verified_window = None
        fresh_owner = None
    visible_text = Postcondition(field=Selector(role='TextArea', identifier='First Text View'),
                                 attribute='Value', equals=TEXT.replace('\n', '\\n'))
    if (fresh.get('type') != 'observation' or fresh.get('truncated') or fresh_owner != owner
            or verified_window is None or not visible_text.check(verified_window['tree'])):
        return result('failed', 'verify', error=fresh.get('error', 'text_not_verified'), repeat_write=False)
    title = verified_window['attrs'].get('Title')
    # This fixed welcome task explicitly presents its result. Ordinary flows
    # and launch_app retain their background behavior. Presentation failure
    # cannot turn verified text into a request to repeat the write.
    presentation = {'foreground_verified': False, 'error': 'result_document_not_focused'}
    if verified_window['focused']:
        try:
            presentation = (present or present_app)(TARGET, owner[0])
        except Exception:
            presentation = {'foreground_verified': False, 'error': 'presentation_unavailable'}
    if not isinstance(presentation, dict):
        presentation = {'foreground_verified': False, 'error': 'presentation_unavailable'}
    foreground = presentation.get('foreground_verified') is True
    if foreground:
        shown = _replay_observation(driver.observe, TARGET, 'tree_full', window_identity=True)
        try:
            shown_owner, shown_windows, _ = _windows(shown)
            shown_window = shown_windows.get(new_id)
            foreground = (shown.get('type') == 'observation' and not shown.get('truncated')
                          and shown_owner == owner and shown_window is not None
                          and shown_window['focused'] and visible_text.check(shown_window['tree']))
        except (ValueError, KeyError, TypeError):
            foreground = False
        if not foreground:
            presentation = {'error': 'result_window_not_verified'}
    return result('text_verified', 'complete', target=TARGET, window_title=title, window_id=new_id,
                  text=TEXT, persistent_commit_verified=False,
                  foreground_verified=foreground,
                  **({'presentation_error': presentation.get('error', 'presentation_unavailable')} if not foreground else {}),
                  write_receipt='verified' if written.ok else 'commit_unverified',
                  scope='new document visible text only')
