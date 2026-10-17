"""Read-only AX/CG identity and geometry binding; no activation or input.

Private ABI: AXError _AXUIElementGetWindow(AXUIElementRef, CGWindowID *).
Primary declaration: https://github.com/asmvik/yabai/blob/master/src/misc/extern.h
Local creation precedent: reviews/2026-09-19/g0-keywindow-separation.py.
Missing SPI is an explicit refusal. Validation is a fresh read, not an atomic
OS transaction or protection against changes after validation. Observation IDs,
Esc, deadlines, and dispatch authorization remain the caller's responsibility.
"""
from dataclasses import dataclass, field
import math


class NativeWindowBindingError(RuntimeError):
    pass


class NativeWindowBindingUnavailable(NativeWindowBindingError):
    pass


@dataclass(frozen=True)
class WindowBinding:
    element: object
    window: object
    pid: int
    launch_identity: float
    window_id: int
    window_bounds: tuple
    element_bounds: tuple
    point: tuple
    _backend: object = field(repr=False, compare=False)
    point_kind: str = "element_center"
    relative_point: tuple | None = None


class _MacBackend:
    scope = None
    def __init__(self):
        try:
            import ctypes
            import ctypes.util
            import objc
            import ApplicationServices as ax
            import Quartz
            from CoreFoundation import CFEqual
            library = ctypes.CDLL('/System/Library/Frameworks/ApplicationServices.framework/Frameworks/HIServices.framework/HIServices')
            get_window = library._AXUIElementGetWindow
            get_window.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
            get_window.restype = ctypes.c_int32
        except (ImportError, OSError, AttributeError) as exc:
            raise NativeWindowBindingUnavailable('native window binding dependencies unavailable') from exc
        self.c, self.objc, self.ax, self.q = ctypes, objc, ax, Quartz
        self.equal = CFEqual
        self.library, self.get_window = library, get_window
        self.scope = None

    def _call(self, element, operation):
        return self.scope.call(self.ax, element, operation) if self.scope else operation()

    def attr(self, element, name):
        error, value = self._call(element, lambda: self.ax.AXUIElementCopyAttributeValue(element, name, None))
        if error != 0:
            raise NativeWindowBindingError('required AX attribute unavailable: ' + name)
        return value

    def pid(self, element):
        error, pid = self.ax.AXUIElementGetPid(element, None)
        if error != 0:
            raise NativeWindowBindingError('AX process unavailable')
        return int(pid)

    def launch(self, pid):
        from .native_process_identity import process_start_identity
        return process_start_identity(pid)

    def require_key_target(self, binding, *, element=False):
        """Read the app's current routing; never set focus or select a view."""
        from CoreFoundation import CFGetTypeID
        self.require_ready(binding.window, binding.pid)
        app = self.ax.AXUIElementCreateApplication(binding.pid)
        window = self.attr(app, 'AXFocusedWindow')
        if (CFGetTypeID(window) != self.ax.AXUIElementGetTypeID()
                or not self.equal(window, binding.window)):
            raise NativeWindowBindingError('target window is not the app focused window')
        # AXFocused on a background window can be false while this app's
        # AXFocusedWindow identifies it. Use the app's current routing identity;
        # never equate that window flag with AppKit isKeyWindow or foreground.
        focused = self.attr(app, 'AXFocusedUIElement')
        if CFGetTypeID(focused) != self.ax.AXUIElementGetTypeID():
            raise NativeWindowBindingError('target input element unavailable')
        if not self.equal(_ancestor_window(self, focused, binding.pid), binding.window):
            raise NativeWindowBindingError('target input belongs to another window')
        if element and not self.equal(focused, binding.element):
            raise NativeWindowBindingError('observed element is not the current target input')

    def require_window_target(self, binding, *, element=False):
        """Read window routing without assuming an AX input element exists."""
        from CoreFoundation import CFGetTypeID
        self.require_ready(binding.window, binding.pid)
        app = self.ax.AXUIElementCreateApplication(binding.pid)
        window = self.attr(app, 'AXFocusedWindow')
        if (CFGetTypeID(window) != self.ax.AXUIElementGetTypeID()
                or not self.equal(window, binding.window)):
            raise NativeWindowBindingError('target window is not the app focused window')

    def frame(self, element):
        ax = self.ax
        p_ok, p = ax.AXValueGetValue(self.attr(element, 'AXPosition'), ax.kAXValueCGPointType, None)
        s_ok, s = ax.AXValueGetValue(self.attr(element, 'AXSize'), ax.kAXValueCGSizeType, None)
        if not p_ok or not s_ok:
            raise NativeWindowBindingError('AX geometry unavailable')
        return (p.x, p.y, s.width, s.height)

    def window_id(self, window):
        number = self.c.c_uint32()
        error = self._call(window, lambda: self.get_window(self.objc.pyobjc_id(window), self.c.byref(number)))
        if error != 0 or not number.value:
            raise NativeWindowBindingError('AX window ID unavailable')
        return number.value

    def windows(self):
        if self.scope: self.scope.ready()
        q = self.q
        entries = q.CGWindowListCopyWindowInfo(q.kCGWindowListOptionAll, 0)
        if self.scope: self.scope.ready()
        if entries is None:
            raise NativeWindowBindingError('CG windows unavailable')
        # PyObjC may return OC_PythonLong/Float proxies, not exact Python
        # builtins. Normalize OS metadata before the strict host validation.
        return [dict(id=int(e[q.kCGWindowNumber]), pid=int(e[q.kCGWindowOwnerPID]),
                     onscreen=e.get(q.kCGWindowIsOnscreen), layer=e.get(q.kCGWindowLayer),
                     bounds=tuple(float(e[q.kCGWindowBounds][k])
                                  if k in e.get(q.kCGWindowBounds, {}) else None
                                  for k in ('X','Y','Width','Height')))
                for e in entries]

    def require_ready(self, window, pid):
        from Foundation import NSArray
        from .native_window_ready import require_window_ready
        app = self.ax.AXUIElementCreateApplication(pid)
        return require_window_ready(window, lambda: self.attr(app, 'AXWindows'),
            lambda element, name: self._call(element, lambda: self.ax.AXUIElementCopyAttributeValue(element, name, None)),
            self.equal, array_types=(list, tuple, NSArray))

    def hit(self, pid, point):
        app = self.ax.AXUIElementCreateApplication(pid)
        error, element = self._call(app, lambda: self.ax.AXUIElementCopyElementAtPosition(app, *point, None))
        if error != 0 or element is None:
            raise NativeWindowBindingError('application hit-test unavailable')
        return element


def _load_backend():
    return _MacBackend()


def _rect(value):
    if not isinstance(value, (tuple, list)) or len(value) != 4:
        raise NativeWindowBindingError('invalid geometry')
    try:
        if any(type(v) not in (int, float) for v in value):
            raise ValueError()
        r = tuple(float(v) for v in value)
        x, y, w, h = r
        if not all(math.isfinite(v) for v in (*r, x+w, y+h)) or w <= 0 or h <= 0:
            raise ValueError()
        return r
    except (ValueError, OverflowError):
        raise NativeWindowBindingError('invalid geometry') from None


def _launch(value):
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and value > 0
    except OverflowError:
        valid = False
    if not valid:
        raise NativeWindowBindingError('invalid launch identity')
    return float(value)


def _ancestor_window(backend, element, pid):
    current, seen = element, []
    for _ in range(21):
        if current is None or any(backend.equal(current, old) for old in seen):
            break
        if backend.pid(current) != pid:
            raise NativeWindowBindingError('cross-process ancestor')
        if backend.attr(current, 'AXRole') == 'AXWindow':
            return current
        seen.append(current)
        current = backend.attr(current, 'AXParent')
    raise NativeWindowBindingError('bounded window ancestry unavailable')


def _capture(element, pid, requested_launch, backend, point_kind="element_center", relative_point=None):
    launch = _launch(backend.launch(pid))
    if requested_launch is not None and launch != requested_launch:
        raise NativeWindowBindingError('process identity changed')
    window = _ancestor_window(backend, element, pid)
    number = backend.window_id(window)
    if type(number) is not int or not 0 < number <= 0xFFFFFFFF:
        raise NativeWindowBindingError('invalid window ID')
    bounds, rect = _rect(backend.frame(window)), _rect(backend.frame(element))
    matches = [e for e in backend.windows() if e['id'] == number]
    if len(matches) != 1:
        raise NativeWindowBindingError('CG window not unique')
    info = matches[0]
    if info['pid'] != pid or info['onscreen'] != True or info['layer'] != 0 or _rect(info['bounds']) != bounds:
        raise NativeWindowBindingError('CG window ownership or geometry mismatch')
    x, y, w, h = bounds
    ex, ey, ew, eh = rect
    if not (x <= ex and y <= ey and ex+ew <= x+w and ey+eh <= y+h):
        raise NativeWindowBindingError('element outside window')
    if point_kind == 'window_input':
        backend.require_ready(window, pid)
        if not backend.equal(element, window) or backend.attr(window, 'AXSubrole') != 'AXStandardWindow':
            raise NativeWindowBindingError('opaque input requires an observed standard window')
        if _launch(backend.launch(pid)) != launch:
            raise NativeWindowBindingError('process changed during capture')
        return WindowBinding(element, window, pid, launch, number, bounds, rect,
                             (x+w/2, y+h/2), backend, point_kind)
    if point_kind == 'window_point':
        backend.require_ready(window, pid)
        if not backend.equal(element, window) or backend.attr(window, 'AXSubrole') != 'AXStandardWindow':
            raise NativeWindowBindingError('coordinate input requires an observed standard window')
        px, py = _relative_point(relative_point)
        if not (0 <= px < w and 0 <= py < h):
            raise NativeWindowBindingError('coordinate outside bound window')
        if _launch(backend.launch(pid)) != launch:
            raise NativeWindowBindingError('process changed during capture')
        return WindowBinding(element, window, pid, launch, number, bounds, rect,
                             (x+px, y+py), backend, point_kind, (px, py))
    if point_kind == "titlebar":
        backend.require_ready(window, pid)
        if not backend.equal(element, window) or backend.attr(window, 'AXSubrole') != 'AXStandardWindow':
            raise NativeWindowBindingError('titlebar requires a standard window')
        close = backend.attr(window, 'AXCloseButton')
        if close is None or backend.pid(close) != pid or backend.attr(close, 'AXSubrole') != 'AXCloseButton':
            raise NativeWindowBindingError('native titlebar anchor unavailable')
        if not backend.equal(_ancestor_window(backend, close, pid), window):
            raise NativeWindowBindingError('titlebar anchor belongs to another window')
        cx, cy, cw, ch = _rect(backend.frame(close))
        if not (x <= cx and cx+cw <= x+w and y <= cy and cy+ch <= y+min(h/4, 50)):
            raise NativeWindowBindingError('titlebar anchor outside upper window band')
        points = [(x+w*fraction, cy+ch/2) for fraction in (.5, .75, .25)]
        for point in points:
            hit = backend.hit(pid, point)
            if hit is not None and backend.pid(hit) == pid and backend.equal(hit, window):
                if _launch(backend.launch(pid)) != launch:
                    raise NativeWindowBindingError('process changed during capture')
                return WindowBinding(element, window, pid, launch, number, bounds, rect,
                                     point, backend, point_kind)
        raise NativeWindowBindingError('no blank native titlebar point')
    if point_kind != "element_center":
        raise NativeWindowBindingError('unknown binding point kind')
    point = (ex+ew/2, ey+eh/2)
    hit, seen = backend.hit(pid, point), []
    for _ in range(21):
        if hit is None or any(backend.equal(hit, old) for old in seen):
            break
        if backend.pid(hit) != pid:
            raise NativeWindowBindingError('cross-process hit-test')
        if backend.equal(hit, element):
            # Recheck process after the series of AX reads.
            if _launch(backend.launch(pid)) != launch:
                raise NativeWindowBindingError('process changed during capture')
            return WindowBinding(element, window, pid, launch, number, bounds, rect, point, backend)
        if backend.attr(hit, 'AXRole') == 'AXWindow':
            break
        seen.append(hit)
        hit = backend.attr(hit, 'AXParent')
    raise NativeWindowBindingError('hit-test does not identify element or descendant')


def _relative_point(point):
    if (not isinstance(point, (tuple, list)) or len(point) != 2
            or any(type(value) not in (int, float) for value in point)):
        raise NativeWindowBindingError('coordinate requires two finite non-boolean numbers')
    try:
        result = tuple(float(value) for value in point)
        if not all(math.isfinite(value) and value >= 0 for value in result):
            raise ValueError()
    except (ValueError, OverflowError):
        raise NativeWindowBindingError('coordinate requires two finite nonnegative numbers') from None
    return result


def capture_binding(element, pid, launch_identity=None, *, point_kind="element_center", read_scope=None,
                    relative_point=None):
    if point_kind not in ("element_center", "titlebar", "window_point", "window_input"):
        raise NativeWindowBindingError("unknown binding point kind")
    if point_kind == 'window_point':
        relative_point = _relative_point(relative_point)
    elif relative_point is not None:
        raise NativeWindowBindingError('coordinate is only valid for a window-point binding')
    if element is None or type(pid) is not int or not 0 < pid <= 0x7FFFFFFF:
        raise NativeWindowBindingError('invalid element or PID')
    if launch_identity is not None:
        launch_identity = _launch(launch_identity)
    try:
        backend = _load_backend()
        if read_scope is not None:
            read_scope.ready()
            backend.scope = read_scope
        return _capture(element, pid, launch_identity, backend, point_kind, relative_point)
    except NativeWindowBindingError:
        raise
    except Exception as exc:
        raise NativeWindowBindingError('window binding read failed') from exc


def validate_binding(binding):
    """Return the same binding only after a complete fresh read matches it."""
    if not isinstance(binding, WindowBinding):
        raise NativeWindowBindingError('invalid binding')
    try:
        fresh = _capture(binding.element, binding.pid, binding.launch_identity, binding._backend,
                         binding.point_kind, binding.relative_point)
        if not binding._backend.equal(fresh.window, binding.window):
            raise NativeWindowBindingError('window identity changed')
        for name in ('window_id', 'window_bounds', 'element_bounds', 'point'):
            if getattr(fresh, name) != getattr(binding, name):
                raise NativeWindowBindingError('window binding changed: ' + name)
        return binding
    except NativeWindowBindingError:
        raise
    except Exception as exc:
        raise NativeWindowBindingError('window binding validation failed') from exc
