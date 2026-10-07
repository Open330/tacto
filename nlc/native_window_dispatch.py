"""PID-addressed background preparation; no global input or focus restoration.

Snapshots and system calls are sequential, not an atomic input transaction.
Mac imports are lazy; injected boundaries can execute without UI input.
"""
from dataclasses import dataclass
import math
import time


class WindowDispatchFailure(RuntimeError):
    def __init__(self, detail, *, attempted=0, stopped=False):
        super().__init__(detail)
        self.attempted = attempted
        self.stopped = stopped


@dataclass(frozen=True)
class UserInputState:
    foreground: int
    pointer: tuple
    focused: object


class WindowReadScope:
    def __init__(self, deadline, check, *, clock=time.monotonic):
        self.deadline, self.check, self.clock = deadline, check, clock

    def ready(self):
        self.check()
        if type(self.deadline) not in (int, float) or not math.isfinite(self.deadline) or self.clock() >= self.deadline:
            raise RuntimeError('window observation deadline expired')

    def call(self, ax, element, operation, *, maximum=.5):
        self.ready()
        remaining = self.deadline - self.clock()
        if remaining <= 0 or ax.AXUIElementSetMessagingTimeout(element, min(maximum, remaining)) != 0:
            raise RuntimeError('window AX timeout unavailable')
        try:
            result = operation()
        finally:
            if ax.AXUIElementSetMessagingTimeout(element, 0) != 0:
                raise RuntimeError('window AX timeout reset failed')
        self.ready()
        return result


class _MacBackend:
    def __init__(self):
        import AppKit
        import ApplicationServices
        import Quartz
        from CoreFoundation import CFEqual, CFGetTypeID
        self.appkit, self.ax, self.q = AppKit, ApplicationServices, Quartz
        self.equal, self.type_id = CFEqual, CFGetTypeID

    def read_input(self, scope):
        from .native_frontmost import frontmost_pid
        scope.ready()
        foreground = frontmost_pid()
        if type(foreground) is not int or foreground <= 0:
            raise RuntimeError('user foreground unavailable')
        event = self.q.CGEventCreate(None)
        if event is None:
            raise RuntimeError('user pointer unavailable')
        point = self.q.CGEventGetLocation(event)
        pointer = (float(point.x), float(point.y))
        if not all(math.isfinite(x) for x in pointer):
            raise RuntimeError('user pointer malformed')
        app = self.ax.AXUIElementCreateApplication(foreground)
        error, focused = scope.call(self.ax, app,
            lambda: self.ax.AXUIElementCopyAttributeValue(app, 'AXFocusedUIElement', None), maximum=.25)
        if (error != 0 or focused is None
                or self.type_id(focused) != self.ax.AXUIElementGetTypeID()
                or frontmost_pid() != foreground):
            raise RuntimeError('user focus unavailable or foreground changed')
        return UserInputState(foreground, pointer, focused)

    def activation(self, binding, timestamp):
        owner = self.appkit.NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
            13, (0, 0), 0xC0000, timestamp, binding.window_id, None, 1, 0, 0)
        event = owner.CGEvent() if owner is not None else None
        reflected = self.appkit.NSEvent.eventWithCGEvent_(event) if event is not None else None
        if (event is None or self.q.CGEventGetType(event) != 13
                or self.q.CGEventGetFlags(event) != 0xC0000 or reflected is None
                or reflected.type() != 13 or reflected.subtype() != 1
                or reflected.windowNumber() != binding.window_id
                or reflected.data1() != 0 or reflected.data2() != 0):
            raise RuntimeError('window activation event readback failed')
        return owner, event

    def post(self, event, pid):
        self.q.CGEventPostToPid(pid, event)


def dispatch_preparation(binding, *, deadline, stopped, context, backend=None,
                         clock=time.monotonic, validate=None, build_pair=None):
    return _dispatch(binding, deadline=deadline, stopped=stopped, context=context,
                     backend=backend, clock=clock, validate=validate, build_pair=build_pair)


def dispatch_navigation(binding, key, *, deadline, stopped, context, backend=None,
                        clock=time.monotonic, validate=None, build_pair=None, routing=None,
                        preflight=None):
    from .native_key_events import NAVIGATION
    if type(key) is not str or key not in NAVIGATION:
        raise WindowDispatchFailure('unsupported navigation key')
    return _dispatch(binding, deadline=deadline, stopped=stopped, context=context,
                     backend=backend, clock=clock, validate=validate, build_pair=build_pair,
                     key=key, routing=routing, preflight=preflight)


def dispatch_point(binding, *, deadline, stopped, context, backend=None,
                   clock=time.monotonic, validate=None, build_pair=None, routing=None,
                   preflight=None):
    """Internal window-point pair; caller must authorize its semantic effect."""
    return _dispatch(binding, deadline=deadline, stopped=stopped, context=context,
                     backend=backend, clock=clock, validate=validate, build_pair=build_pair,
                     point_click=True, routing=routing, preflight=preflight)


def dispatch_window_input(binding, payload, *, deadline, stopped, context, backend=None,
                          clock=time.monotonic, validate=None, build_pair=None, routing=None,
                          preflight=None):
    from .native_window_payload import window_input_problem
    action = ({'name':'window_input', 'element_index':0, **payload}
              if isinstance(payload, dict) and set(payload) in ({'key'}, {'text'}) else {})
    problem = window_input_problem(action)
    if problem:
        raise WindowDispatchFailure(problem)
    return _dispatch(binding, deadline=deadline, stopped=stopped, context=context,
                     backend=backend, clock=clock, validate=validate, build_pair=build_pair,
                     routing=routing, preflight=preflight, opaque_payload=dict(payload))


def _dispatch(binding, *, deadline, stopped, context, backend=None,
              clock=time.monotonic, validate=None, build_pair=None, key=None, routing=None,
              preflight=None, point_click=False, opaque_payload=None):
    from .native_mouse_events import build_window_click
    from .native_window_binding import validate_binding
    attempted = 0

    def ready():
        if stopped():
            raise RuntimeError('user stopped input')
        if type(deadline) not in (int, float) or not math.isfinite(deadline) or clock() >= deadline:
            raise RuntimeError('window input deadline expired')
        context()

    try:
        scope = WindowReadScope(deadline, ready, clock=clock)
        ready()
        if opaque_payload is not None and (key is not None or point_click
                or binding.point_kind != 'window_input' or not binding._backend.equal(binding.element, binding.window)):
            raise RuntimeError('opaque input requires an observed window binding')
        if point_click and (key is not None or binding.point_kind != 'window_point'
                or not binding._backend.equal(binding.element, binding.window)):
            raise RuntimeError('coordinate input requires an observed window-point binding')
        if key is None and not point_click and opaque_payload is None and (binding.point_kind != 'titlebar' or not binding._backend.equal(binding.element, binding.window)):
            raise RuntimeError('window preparation requires a titlebar binding')
        if key is not None and (binding.point_kind != 'element_center'
                or binding._backend.equal(binding.element, binding.window)):
            raise RuntimeError('navigation requires an observed input element')
        validate = validate or validate_binding
        if key is None and opaque_payload is None:
            build_pair = build_pair or build_window_click
        validate(binding); ready()
        backend = backend or _MacBackend()
        initial = backend.read_input(scope)
        if initial.foreground == binding.pid:
            raise RuntimeError('background preparation requires a background process')
        if opaque_payload is not None:
            from .native_key_events import build_window_key, build_window_text
            routing = routing or binding._backend.require_window_target
            routing(binding, element=False); ready()
            constructor = build_window_key if 'key' in opaque_payload else build_window_text
            value = opaque_payload.get('key', opaque_payload.get('text'))
            pair = (build_pair or constructor)(binding.window_id, value)
            owners, events = pair._owners, (pair.down, pair.up)
        elif point_click:
            routing = routing or binding._backend.require_window_target
            routing(binding, element=False); ready()
            pair = build_pair(binding.window_id, binding.window_bounds, binding.point)
            owners, events = pair._owners, (pair.down, pair.up)
        elif key is None:
            pair = build_pair(binding.window_id, binding.window_bounds, binding.point)
            activation_owner, activation = backend.activation(binding, clock())
            owners, events = (activation_owner, pair._owners), (activation, pair.down, pair.up)
        else:
            from .native_key_events import build_window_key
            routing = routing or binding._backend.require_key_target
            routing(binding, element=True); ready()
            pair = (build_pair or build_window_key)(binding.window_id, key)
            owners, events = pair._owners, (pair.down, pair.up)
        # Keep borrowed CG event owners alive until the final post completes.
        for number, event in enumerate(events):
            ready(); validate(binding); ready()
            if number == 0 and preflight:
                preflight(); ready()
            if routing:
                routing(binding, element=number == 0); ready()
            before = backend.read_input(scope)
            fields = _differences(initial, before, backend.equal)
            if fields:
                raise RuntimeError('user input state changed before input: ' + ','.join(fields))
            ready()
            attempted += 1
            backend.post(event, binding.pid)
            ready()
            after = backend.read_input(scope)
            fields = _differences(before, after, backend.equal)
            if fields:
                raise RuntimeError('user input state changed during input: ' + ','.join(fields))
            validate(binding); ready()
            if routing:
                routing(binding, element=False); ready()
            confirmed = backend.read_input(scope)
            fields = _differences(before, confirmed, backend.equal)
            if fields:
                raise RuntimeError('user input state changed during validation: ' + ','.join(fields))
            ready()
        _ = owners
        return {'input_route':'window_input' if opaque_payload is not None else 'window_bound_mouse' if point_click else 'window_prepare' if key is None else 'window_key', 'mechanism':'pid_window_events',
                'posted_events':attempted, 'window_id':binding.window_id,
                'unverified':'observe_effect', 'reobserve_required':True}
    except Exception as exc:
        raise WindowDispatchFailure(str(exc), attempted=attempted, stopped=bool(stopped())) from exc


def _differences(before, after, equal):
    return [name for name, changed in (
        ('foreground', before.foreground != after.foreground),
        ('pointer', before.pointer != after.pointer),
        ('focus', not equal(before.focused, after.focused))) if changed]
