"""Host-authorized native image proposals; pixels never prove completion.

Capture and input cannot be atomic. A fresh exact image comparison narrows the
gap but does not guarantee that pixels remain unchanged during dispatch.
"""
from copy import deepcopy
import hashlib
import math
from pathlib import Path
import struct
import tempfile
import time
import weakref

from .native_visual import NativeVisualCapture, NativeVisualError, _frame_valid


_SPENT = weakref.WeakValueDictionary()


def capture_window(driver, obs_id, target, *, window_index, timeout_seconds=5):
    """Read either public transport into an immutable, digest-checked image.

    The signed helper retains its original private artifact under its existing
    capture contract. Our requested copy is removed with the temporary folder.
    Old helpers without explicit image mapping are refused.
    """
    if (not isinstance(obs_id, str) or not obs_id or not isinstance(target, str) or not target
            or type(window_index) is not int or window_index < 0):
        raise ValueError('an explicit observed window index and target are required')
    with tempfile.TemporaryDirectory(prefix='tacto-native-image-') as directory:
        path = Path(directory) / 'window.png'
        reply = driver.capture(target, obs_id=obs_id, window_index=window_index,
                               path=path, timeout_seconds=timeout_seconds)
        if not isinstance(reply, dict) or reply.get('type') != 'capture':
            raise NativeVisualError('native_capture_failed:' + str(reply.get('error', 'invalid_reply')) if isinstance(reply, dict) else 'native_capture_failed:invalid_reply')
        if (reply.get('obs_id') != obs_id or reply.get('target') != target
                or reply.get('window_index') != window_index
                or reply.get('image_mapping') != 'full_window_no_shadow'
                or not _frame_valid(reply.get('window_frame'))
                or any(type(reply.get(k)) is not int or reply[k] <= 0
                       for k in ('pid', 'window_id', 'width', 'height'))
                or not isinstance(reply.get('capture_id'), str) or not reply['capture_id']):
            raise NativeVisualError('invalid_native_image_mapping')
        with path.open('rb') as stream:
            png = stream.read(5 * 1024 * 1024 + 1)
        if (len(png) > 5 * 1024 * 1024 or len(png) < 24
                or png[:8] != b'\x89PNG\r\n\x1a\n' or png[12:16] != b'IHDR'
                or struct.unpack('>II', png[16:24]) != (reply['width'], reply['height'])
                or hashlib.sha256(png).hexdigest() != reply.get('sha256')):
            raise NativeVisualError('invalid_native_image')
        return NativeVisualCapture(reply['capture_id'], obs_id, target, reply['pid'],
            reply['window_id'], window_index, reply['sha256'],
            {'width': reply['width'], 'height': reply['height'],
             'coordinate_space': 'window_pixels'}, time.time(),
            reply.get('binding', 'helper_native_window_id'), png, tuple(reply['window_frame']))


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def native_visual_capabilities(input_actions):
    """Validate host-selected input kinds and their required backend guards."""
    if type(input_actions) is not frozenset or not input_actions or not input_actions <= {'click', 'key', 'text'}:
        raise ValueError('native visual input kinds must be a nonempty frozenset of click/key/text')
    required = set()
    if 'click' in input_actions:
        required |= {'action.click_point', 'click_point.window_bound', 'click_point.host_authorization'}
    if input_actions & {'key', 'text'}:
        from .native_window_payload import WINDOW_INPUT_CAPABILITIES
        required |= set(WINDOW_INPUT_CAPABILITIES)
    return required


class NativeVisualClickExecutor:
    """Spend one capture per proposal, recheck it, then use public click_point."""
    def __init__(self, driver, target):
        self.driver, self.target = driver, target

    def click(self, capture, proposal, authorize, *, cancelled=None, timeout_seconds=5):
        return self._execute(capture, proposal, authorize, cancelled=cancelled,
                             timeout_seconds=timeout_seconds, opaque=False)

    def input(self, capture, proposal, authorize, *, cancelled=None, timeout_seconds=5):
        """Spend an image on bounded key/text; authorize and verify separately."""
        return self._execute(capture, proposal, authorize, cancelled=cancelled,
                             timeout_seconds=timeout_seconds, opaque=True)

    def _execute(self, capture, proposal, authorize, *, cancelled, timeout_seconds, opaque):
        if not _number(timeout_seconds) or not 0 < timeout_seconds <= 10:
            raise ValueError('timeout must be finite, positive and at most 10')
        event = {'action': 'window_input' if opaque else 'click_point', 'effect': 'not_started'}
        def denied(reason):
            return {**event, 'reason': reason}
        if type(capture) is not NativeVisualCapture:
            return denied('invalid_native_capture')
        if not isinstance(capture.capture_id, str) or not capture.capture_id:
            return denied('invalid_native_capture')
        event['capture_id'] = capture.capture_id
        if capture.capture_id in _SPENT:
            return denied('capture_already_used')
        _SPENT[capture.capture_id] = capture
        if capture.target != self.target:
            return denied('target_mismatch')
        capabilities = getattr(self.driver, 'capabilities', ())
        required = native_visual_capabilities(frozenset({'key', 'text'} if opaque else {'click'}))
        if (not isinstance(capabilities, (set, frozenset, tuple, list))
                or not required <= set(capabilities)):
            return denied('native_input_capability_missing' if opaque else 'native_point_capability_missing')
        if (not _frame_valid(capture.window_frame)
                or capture.viewport.get('coordinate_space') != 'window_pixels'
                or type(capture.window_index) is not int or capture.window_index < 0):
            return denied('invalid_native_image_mapping')
        proposal = deepcopy(proposal)
        if opaque:
            from .native_window_payload import window_input_problem
            field = proposal.get('action') if isinstance(proposal, dict) else None
            if field not in ('key', 'text') or set(proposal) != {'action', field}:
                return denied('invalid_proposal')
            action = {'name':'window_input', 'element_index':capture.window_index, field:proposal[field]}
            if window_input_problem(action):
                return denied('invalid_proposal')
        else:
            if (not isinstance(proposal, dict) or set(proposal) != {'action', 'x', 'y'}
                    or proposal.get('action') != 'click'):
                return denied('invalid_proposal')
            x, y = proposal['x'], proposal['y']
            width, height = capture.viewport.get('width'), capture.viewport.get('height')
            if (not all(_number(v) for v in (x, y, width, height))
                    or not 0 <= x < width or not 0 <= y < height):
                return denied('invalid_coordinates')
        if hashlib.sha256(capture.png).hexdigest() != capture.sha256:
            return denied('invalid_capture')
        if authorize is None:
            return denied('authorization_required')
        deadline = time.monotonic() + timeout_seconds
        def stopped():
            return time.monotonic() >= deadline or (cancelled is not None and cancelled())
        try:
            if stopped():
                return denied('cancelled_or_expired')
            if authorize(deepcopy(proposal), capture) is not True:
                return denied('authorization_denied')
            if stopped():
                return denied('cancelled_or_expired')
            fresh = capture_window(self.driver, capture.obs_id, self.target,
                window_index=capture.window_index, timeout_seconds=deadline-time.monotonic())
            if any(getattr(fresh, key) != getattr(capture, key) for key in
                   ('obs_id', 'target', 'pid', 'window_id', 'window_index', 'window_frame', 'viewport', 'sha256')):
                return denied('stale_capture')
            if stopped():
                return denied('cancelled_or_expired')
        except Exception as error:
            return denied('preflight_failed:' + type(error).__name__)
        if opaque:
            event.update(window_input_payload={field:proposal[field]}, effect='unknown')
        else:
            point = {'x': x * capture.window_frame[2] / width,
                     'y': y * capture.window_frame[3] / height}
            action = {'name':'click_point', 'element_index':capture.window_index, 'point':point}
            event.update(image_point={'x': x, 'y': y}, window_point=point, effect='unknown')
        event['dispatch_action'] = deepcopy(action)
        try:
            from .model_budget import model_deadline
            with model_deadline(deadline):
                reply = self.driver.act(capture.obs_id, self.target,
                    action,
                    risk_class='preapproved', approved=True,
                    intent_text='host-authorized native visual proposal')
        except Exception as error:
            return {**event, 'reason': 'dispatch_failed:' + type(error).__name__}
        event['dispatch'] = reply
        from .protocol import window_point_reobservation, window_input_reobservation
        reobservation = window_input_reobservation if opaque else window_point_reobservation
        if reobservation(action, reply, capabilities,
                                      obs_id=capture.obs_id, window_id=capture.window_id):
            return {**event, 'effect': 'dispatched', 'reobserve_required': True}
        if isinstance(reply, dict) and reply.get('effect') == 'not_started':
            return {**event, 'effect': 'not_started', 'reason': reply.get('error', 'dispatch_refused')}
        return {**event, 'reason': 'dispatch_unverified', 'reobserve_required': True}


def run_native_visual(driver, target, goal, window_selector, planner, authorize, verify,
                      *, max_steps=8, timeout_seconds=60, cancelled=None):
    """Observe and replan one native click at a time; only host verify says DONE.

    window_selector resolves an AXWindow in each new tree. Its native PID/window
    ID is pinned on the first capture; a replacement window is never followed.
    Prepare the intended background window explicitly before calling this loop.
    No automatic recovery or retry follows a possibly delivered input.
    """
    from .visual_actions import VisualRunResult
    from .model_budget import model_deadline
    if type(max_steps) is not int or max_steps < 0:
        raise ValueError('max_steps must be a nonnegative integer')
    if not _number(timeout_seconds) or timeout_seconds < 0:
        raise ValueError('timeout must be finite and nonnegative')
    deadline = time.monotonic() + timeout_seconds
    history, latest, identity = [], None, None
    executor = NativeVisualClickExecutor(driver, target)
    def finish(state, reason):
        return VisualRunResult(state, reason, deepcopy(history), latest.to_dict() if latest else None)
    def stopped():
        if time.monotonic() >= deadline:
            return 'deadline_exceeded'
        try:
            return 'cancelled' if cancelled is not None and cancelled() else None
        except Exception:
            return 'cancellation_check_failed'
    for step in range(max_steps + 1):
        if reason := stopped():
            return finish('STOPPED', reason)
        try:
            obs = driver.observe(target, 'tree_full')
            if obs.get('type') != 'observation':
                return finish('UNVERIFIED', 'observation_failed')
            index, _ = window_selector.resolve(obs['tree'])
            if type(index) is not int:
                return finish('REFUSED', 'window_selector_unresolved')
            if reason := stopped():
                return finish('STOPPED', reason)
            latest = capture_window(driver, obs['obs_id'], target, window_index=index,
                                    timeout_seconds=min(5, deadline-time.monotonic()))
            current = (latest.pid, latest.window_id)
            if identity is not None and identity != current:
                return finish('REFUSED', 'native_window_replaced')
            identity = current
            if reason := stopped():
                return finish('STOPPED', reason)
            if verify(latest, deepcopy(history)) is True:
                return finish('DONE', 'host_verified')
            if step == max_steps:
                return finish('STOPPED', 'step_limit')
            with model_deadline(deadline):
                proposal = planner(latest, goal, deepcopy(history))
        except Exception as error:
            return finish('UNVERIFIED', 'observe_or_plan_failed:' + type(error).__name__ + ':' + str(error)[:160])
        if reason := stopped():
            return finish('STOPPED', reason)
        if isinstance(proposal, dict) and proposal == {'action': 'done'}:
            return finish('UNVERIFIED', 'model_done_without_host_evidence')
        if isinstance(proposal, dict) and proposal == {'action': 'stop'}:
            return finish('STOPPED', 'planner_stopped')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return finish('STOPPED', 'deadline_exceeded')
        try:
            event = executor.click(latest, proposal, authorize, cancelled=cancelled,
                                   timeout_seconds=min(5, remaining))
        except Exception as error:
            return finish('UNVERIFIED', 'executor_failed:' + type(error).__name__)
        history.append(event)
        if event['effect'] == 'not_started':
            return finish('REFUSED', event['reason'])
        if event['effect'] == 'unknown':
            return finish('UNVERIFIED', event['reason'])
    raise AssertionError('unreachable')
