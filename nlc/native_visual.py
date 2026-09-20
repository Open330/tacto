"""Optional ScreenCaptureKit target-window reads; no input or permission prompts.

AX-to-SCWindow matching is best-effort unique PID/title/frame correlation, not an
identity proof. Ambiguity or changes refuse capture. Only the chosen window is
captured; desktop/region capture is never used. Native pixels are not browser
CSS coordinates. Requires macOS 14+ and optional PyObjC framework bindings.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import math
import sys
import threading
import time
import uuid

from .visual import VisualCapture, VisualError


class NativeVisualError(VisualError):
    pass


@dataclass(frozen=True)
class NativeWindowBinding:
    obs_id: str
    target: str
    pid: int
    window_id: int


@dataclass(frozen=True)
class WindowRecord:
    window_id: int
    pid: int
    title: str | None
    frame: tuple
    on_screen: bool
    handle: object = field(repr=False, compare=False)


@dataclass(frozen=True)
class NativeVisualCapture:
    capture_id: str
    obs_id: str
    target: str
    pid: int
    window_id: int
    window_index: int
    sha256: str
    viewport: dict
    captured_at: float
    binding: str
    png: bytes = field(repr=False)

    def to_dict(self):
        return {"capture_id": self.capture_id, "obs_id": self.obs_id,
                "target": self.target, "pid": self.pid, "window_id": self.window_id,
                "window_index": self.window_index, "sha256": self.sha256,
                "viewport": dict(self.viewport), "captured_at": self.captured_at,
                "capture_kind": "native_window", "coordinate_space": "window_pixels",
                "binding": self.binding, "consistency": "best_effort_window_metadata",
                "evidence_kind": "visual_read", "authorizes_action": False,
                "commit_witness": False}

    def save(self, path):
        return VisualCapture.save(self, path)


def _await_callback(start, timeout, pump):
    """Wait while pumping the caller's runloop; discard late callback results."""
    lock = threading.Lock()
    done = threading.Event()
    state = {"closed": False}
    def callback(value, error):
        with lock:
            if state["closed"] or done.is_set():
                return
            state.update(value=value, error=error)
            done.set()
    deadline = time.monotonic() + timeout
    try:
        start(callback)
        while not done.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise NativeVisualError("capture_timeout")
            pump(min(0.01, remaining))
        with lock:
            value, error = state.get("value"), state.get("error")
        if time.monotonic() > deadline:
            raise NativeVisualError("capture_timeout")
        if error is not None or value is None:
            raise NativeVisualError("screen_capture_failed")
        return value
    finally:
        with lock:
            state["closed"] = True
            state.pop("value", None)
            state.pop("error", None)


class ScreenCaptureBackend:
    """Lazy native bindings; construction does not capture or request access."""
    def __init__(self):
        if sys.platform != "darwin":
            raise NativeVisualError("native_capture_unsupported_platform")
        try:
            import Quartz
            import Foundation
        except ImportError as exc:
            raise NativeVisualError("native_capture_dependencies_missing") from exc
        self.q, self.f = Quartz, Foundation
        self.sc = None

    def permission(self):
        preflight = getattr(self.q, "CGPreflightScreenCaptureAccess", None)
        return preflight is not None and bool(preflight())

    def _framework(self):
        if self.sc is None:
            try:
                import ScreenCaptureKit
            except ImportError as exc:
                raise NativeVisualError("screencapturekit_dependency_missing") from exc
            version = self.f.NSProcessInfo.processInfo().operatingSystemVersion()
            # Some PyObjC builds expose NSOperatingSystemVersion as a tuple.
            major = version.majorVersion if hasattr(version, "majorVersion") else version[0]
            manager = getattr(ScreenCaptureKit, "SCScreenshotManager", None)
            if major < 14 or manager is None or not hasattr(
                    manager, "captureImageWithFilter_configuration_completionHandler_"):
                raise NativeVisualError("screenshot_manager_unavailable")
            self.sc = ScreenCaptureKit
        return self.sc

    def _pump(self, seconds):
        self.f.NSRunLoop.currentRunLoop().runUntilDate_(
            self.f.NSDate.dateWithTimeIntervalSinceNow_(seconds))

    def windows(self, timeout):
        sc = self._framework()
        content = _await_callback(
            lambda cb: sc.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(True, True, cb),
            timeout, self._pump)
        result = []
        for window in content.windows():
            app = window.owningApplication()
            if app is None:
                continue
            frame = window.frame()
            result.append(WindowRecord(int(window.windowID()), int(app.processID()),
                window.title(), (frame.origin.x, frame.origin.y, frame.size.width, frame.size.height),
                bool(window.isOnScreen()), window))
        return result

    def ax_state(self, element):
        # Import only after an explicitly requested native operation.
        from .driver import _attr, _elem_frame
        role = _attr(element, "AXRole")
        if role != "AXWindow":
            return (role, None, None, None)
        from ApplicationServices import AXUIElementGetPid
        error, pid = AXUIElementGetPid(element, None)
        return (_attr(element, "AXRole"), _attr(element, "AXTitle"),
                _elem_frame(element), int(pid) if error == 0 else None)

    def screenshot(self, window, timeout):
        sc = self._framework()
        content_filter = sc.SCContentFilter.alloc().initWithDesktopIndependentWindow_(window.handle)
        config = sc.SCStreamConfiguration.alloc().init()
        width, height = math.ceil(window.frame[2]), math.ceil(window.frame[3])
        config.setWidth_(width)
        config.setHeight_(height)
        config.setShowsCursor_(False)
        config.setIgnoreShadowsSingleWindow_(True)
        config.setScalesToFit_(False)
        if hasattr(config, "setIncludeChildWindows_"):
            config.setIncludeChildWindows_(False)
        image = _await_callback(lambda cb:
            sc.SCScreenshotManager.captureImageWithFilter_configuration_completionHandler_(
                content_filter, config, cb), timeout, self._pump)
        width, height = self.q.CGImageGetWidth(image), self.q.CGImageGetHeight(image)
        if width <= 0 or height <= 0:
            raise NativeVisualError("empty_capture")
        import AppKit
        bitmap = AppKit.NSBitmapImageRep.alloc().initWithCGImage_(image)
        data = bitmap.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, {})
        if data is None:
            raise NativeVisualError("png_encoding_failed")
        return bytes(data), (int(width), int(height))


def _frame_valid(frame):
    return (isinstance(frame, (tuple, list)) and len(frame) == 4 and
            all(type(v) in (float, int) and math.isfinite(v) for v in frame) and
            frame[2] > 0 and frame[3] > 0 and frame[2] * frame[3] <= 32_000_000)


def capture_native(driver, obs_id, target, *, window_index=None,
                   timeout_seconds=5, backend=None):
    """Capture a unique observed AXWindow via its correlated SCWindow only.

    Multiple AX windows require explicit window_index. Optional typed host
    native_window_binding(obs_id,target) pins the SCWindow ID additionally.
    Timeout cannot cancel an already submitted SCK screenshot; late images are
    discarded. This function never activates apps or sends native input.
    """
    if type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be finite and positive")
    if not isinstance(target, str) or not target:
        raise ValueError("target must be nonempty")
    deadline = time.monotonic() + timeout_seconds
    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise NativeVisualError("capture_timeout")
        return value
    backend = backend if backend is not None else ScreenCaptureBackend()
    if backend.permission() is not True:
        raise NativeVisualError("screen_capture_permission_missing")
    def observation():
        obs = getattr(driver, "_latest", {}).get(target.lower())
        if obs is None or getattr(obs, "obs_id", None) != obs_id or getattr(obs, "target", "").lower() != target.lower():
            raise NativeVisualError("stale_observation")
        if type(obs.pid) is not int or obs.pid <= 0:
            raise NativeVisualError("invalid_observation_pid")
        return obs
    obs = observation()
    pid = obs.pid
    candidates = []
    for index, element in enumerate(obs.elements):
        state = backend.ax_state(element)
        if state[0] == "AXWindow":
            candidates.append((index, element, state))
    if window_index is not None:
        if type(window_index) is not int:
            raise NativeVisualError("invalid_window_index")
        candidates = [item for item in candidates if item[0] == window_index]
    if len(candidates) != 1:
        raise NativeVisualError("ambiguous_or_missing_ax_window")
    index, element, state = candidates[0]
    role, title, frame, ax_pid = state
    if ax_pid != pid:
        raise NativeVisualError("ax_window_pid_mismatch")
    if not isinstance(title, str) or not title or not _frame_valid(frame):
        raise NativeVisualError("insufficient_ax_window_identity")
    pinned = None
    binding_hook = getattr(driver, "native_window_binding", None)
    if binding_hook is not None:
        pinned = binding_hook(obs_id, target)
        if (type(pinned) is not NativeWindowBinding or pinned.obs_id != obs_id
                or pinned.target.lower() != target.lower() or pinned.pid != pid
                or type(pinned.window_id) is not int or pinned.window_id <= 0):
            raise NativeVisualError("invalid_host_window_binding")
    def select(records):
        matches = [w for w in records if w.pid == pid and w.on_screen is True
                   and w.title == title and tuple(w.frame) == tuple(frame)
                   and (pinned is None or w.window_id == pinned.window_id)]
        if len(matches) != 1 or type(matches[0].window_id) is not int or matches[0].window_id <= 0:
            raise NativeVisualError("ambiguous_or_missing_sc_window")
        return matches[0]
    window = select(backend.windows(remaining()))
    def check_ax():
        current = observation()
        if (current is not obs or current.pid != pid or index >= len(current.elements)
                or current.elements[index] is not element or backend.ax_state(element) != state):
            raise NativeVisualError("native_window_changed")
    check_ax()
    png, size = backend.screenshot(window, remaining())
    check_ax()
    after = select(backend.windows(remaining()))
    if after != window:
        raise NativeVisualError("native_window_changed")
    if pinned is not None and (getattr(driver, "native_window_binding", None) != binding_hook
                               or binding_hook(obs_id, target) != pinned):
        raise NativeVisualError("native_window_binding_changed")
    check_ax()
    remaining()
    if not isinstance(png, bytes) or not png.startswith(b'\x89PNG\r\n\x1a\n') or len(size) != 2 or any(type(n) is not int or n <= 0 for n in size):
        raise NativeVisualError("invalid_native_image")
    return NativeVisualCapture('nv' + uuid.uuid4().hex, obs_id, target, pid,
        window.window_id, index, hashlib.sha256(png).hexdigest(),
        {"width": size[0], "height": size[1], "coordinate_space": "window_pixels"},
        time.time(), 'host_window_id' if pinned else 'best_effort_unique_window_correlation', png)
