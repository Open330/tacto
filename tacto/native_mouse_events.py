"""Prepare, but NEVER dispatch, a left-click pair for a known native window.

Coordinates are CoreGraphics screen *points*, with a top-left origin. This
module does not establish PID/window ownership, observation freshness, geometry
freshness, activation, or permission to click; callers must establish those.

The private CGEvent{Set,Get}WindowLocation declarations are published in WebKit:
https://github.com/WebKit/WebKit/blob/7596ac02075f631c107be8d4e0b056d236288433/Tools/TestRunnerShared/spi/CoreGraphicsTestSPI.h
Their availability/behavior is OS-dependent. Missing symbols or failed readback
are errors, never a reason to fall back to foreground/global input. Setting the
global location resets the private window location, so its setter comes last.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import threading
import time
from typing import Any


class NativeMouseEventError(RuntimeError):
    """The complete, verified event pair could not be prepared."""


class NativeMouseEventsUnavailable(NativeMouseEventError):
    """The required macOS bindings or private symbols are unavailable."""


_number_lock = threading.Lock()
_last_number = 0


def _next_event_number():
    global _last_number
    with _number_lock:
        _last_number = _last_number % 0x7FFFFFFF + 1
        return _last_number


@dataclass(frozen=True)
class PreparedWindowClick:
    """Immutable metadata; CG references themselves must not be mutated.

    Retaining the NSEvents also preserves ownership of their borrowed CG refs.
    This object is not an authorization token or evidence of an executed click.
    """
    window_id: int
    bounds: tuple[float, float, float, float]
    point: tuple[float, float]
    window_point: tuple[float, float]
    event_number: int
    timestamp: float
    down: Any
    up: Any
    _owners: tuple[Any, Any]


def _coordinates(value: Any, count: int, name: str) -> tuple[float, ...]:
    if not isinstance(value, (tuple, list)) or len(value) != count:
        raise ValueError(f"{name} must contain {count} finite numbers")
    if any(type(v) not in (int, float) for v in value):
        raise ValueError(f"{name} must contain finite non-boolean numbers")
    try:
        result = tuple(float(v) for v in value)
    except (OverflowError, ValueError):
        raise ValueError(f"{name} must contain finite numbers") from None
    if not all(math.isfinite(v) for v in result):
        raise ValueError(f"{name} must contain finite numbers")
    return result


class _MacBackend:
    def __init__(self):
        # Deliberately lazy: input validation also works on non-macOS hosts.
        try:
            import ctypes
            import AppKit
            import Quartz
            import objc
            class Point(ctypes.Structure):
                _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]
            library = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
            setter = library.CGEventSetWindowLocation
            getter = library.CGEventGetWindowLocation
            setter.argtypes = [ctypes.c_void_p, Point]
            setter.restype = None
            getter.argtypes = [ctypes.c_void_p]
            getter.restype = Point
        except (ImportError, OSError, AttributeError) as exc:
            raise NativeMouseEventsUnavailable("native mouse event dependencies unavailable") from exc
        self.appkit, self.q, self.objc = AppKit, Quartz, objc
        self.point_type, self.library = Point, library
        self.setter, self.getter = setter, getter

    def create(self, kind, window_id, local, number, timestamp, pressure):
        owner = self.appkit.NSEvent.mouseEventWithType_location_modifierFlags_timestamp_windowNumber_context_eventNumber_clickCount_pressure_(
            kind, local, 0, timestamp, window_id, None, number, 1, pressure)
        event = owner.CGEvent() if owner is not None else None
        if event is None:
            raise NativeMouseEventError("mouse event construction failed")
        return owner, event

    def configure(self, event, window_id, point, relative):
        q = self.q
        q.CGEventSetFlags(event, 0)
        q.CGEventSetLocation(event, point)
        q.CGEventSetIntegerValueField(event, q.kCGMouseEventWindowUnderMousePointer, window_id)
        q.CGEventSetIntegerValueField(event, q.kCGMouseEventWindowUnderMousePointerThatCanHandleThisEvent, window_id)
        self.setter(self.objc.pyobjc_id(event), self.point_type(*relative))

    def read(self, event):
        q = self.q
        global_point = q.CGEventGetLocation(event)
        local = self.getter(self.objc.pyobjc_id(event))
        return {
            "kind": int(q.CGEventGetType(event)),
            "point": (global_point.x, global_point.y),
            "window_point": (local.x, local.y),
            "flags": int(q.CGEventGetFlags(event)),
            "window_id": int(q.CGEventGetIntegerValueField(event, q.kCGMouseEventWindowUnderMousePointer)),
            "handling_window": int(q.CGEventGetIntegerValueField(event, q.kCGMouseEventWindowUnderMousePointerThatCanHandleThisEvent)),
            "event_number": int(q.CGEventGetIntegerValueField(event, q.kCGMouseEventNumber)),
            "click_count": int(q.CGEventGetIntegerValueField(event, q.kCGMouseEventClickState)),
            "pressure": float(q.CGEventGetDoubleValueField(event, q.kCGMouseEventPressure)),
        }


def _load_backend():
    return _MacBackend()


def build_window_click(window_id, bounds, point, event_number=None) -> PreparedWindowClick:
    """Build a verified pair without sending any input.

    Bounds are half-open: the right/bottom edge belongs outside this window.
    A single timestamp and event number identify this immediate down/up pair.
    """
    if type(window_id) is not int or not 0 < window_id <= 0xFFFFFFFF:
        raise ValueError("window_id must be a positive CGWindowID")
    bounds = _coordinates(bounds, 4, "bounds")
    point = _coordinates(point, 2, "point")
    x, y, width, height = bounds
    if width <= 0 or height <= 0 or not math.isfinite(x + width) or not math.isfinite(y + height):
        raise ValueError("bounds must have positive finite extent")
    if not (x <= point[0] < x + width and y <= point[1] < y + height):
        raise ValueError("point must be inside bounds")
    number = _next_event_number() if event_number is None else event_number
    if type(number) is not int or not 0 <= number <= 0x7FFFFFFF:
        raise ValueError("event_number must be a nonnegative signed 32-bit integer")
    relative = (point[0] - x, point[1] - y)
    appkit_local = (relative[0], height - relative[1])
    try:
        backend = _load_backend()  # resolves BOTH SPI symbols before construction
        timestamp = time.monotonic()
        owners, events = [], []
        for kind, pressure in ((1, 1.0), (2, 0.0)):
            owner, event = backend.create(kind, window_id, appkit_local, number, timestamp, pressure)
            backend.configure(event, window_id, point, relative)
            owners.append(owner)
            events.append(event)
        # Validate the complete pair after all mutations, including shared-ref bugs.
        for kind, pressure, event in ((1, 1.0, events[0]), (2, 0.0, events[1])):
            expected = dict(kind=kind, point=point, window_point=relative, flags=0,
                            window_id=window_id, handling_window=window_id,
                            event_number=number, click_count=1, pressure=pressure)
            if backend.read(event) != expected:
                raise NativeMouseEventError("mouse event readback mismatch")
        return PreparedWindowClick(window_id, bounds, point, relative, number,
                                   timestamp, events[0], events[1], tuple(owners))
    except NativeMouseEventError:
        raise
    except Exception as exc:
        raise NativeMouseEventError("mouse event preparation failed") from exc
