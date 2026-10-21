"""UI Automation calls for WindowsDriver. Windows only; imported lazily.

Only element patterns are used. Nothing here posts mouse or keyboard input,
activates a window or moves the pointer.
"""

from __future__ import annotations

import ctypes
import os
import sys

if sys.platform != "win32":
    raise ImportError("tacto.uia_backend requires Windows")

import comtypes
import comtypes.client

comtypes.client.GetModule("UIAutomationCore.dll")
from comtypes.gen import UIAutomationClient as UIA  # noqa: E402

# UIA identifiers (UIAutomationClient.h); stable across Windows versions.
CONTROL_TYPES = {
    50000: "Button", 50001: "Calendar", 50002: "CheckBox", 50003: "ComboBox",
    50004: "Edit", 50005: "Hyperlink", 50006: "Image", 50007: "ListItem",
    50008: "List", 50009: "Menu", 50010: "MenuBar", 50011: "MenuItem",
    50012: "ProgressBar", 50013: "RadioButton", 50014: "ScrollBar", 50015: "Slider",
    50016: "Spinner", 50017: "StatusBar", 50018: "Tab", 50019: "TabItem",
    50020: "Text", 50021: "ToolBar", 50022: "ToolTip", 50023: "Tree",
    50024: "TreeItem", 50025: "Custom", 50026: "Group", 50027: "Thumb",
    50028: "DataGrid", 50029: "DataItem", 50030: "Document", 50031: "SplitButton",
    50032: "Window", 50033: "Pane", 50034: "Header", 50035: "HeaderItem",
    50036: "Table", 50037: "TitleBar", 50038: "Separator",
}
PATTERNS = {
    "invoke": (10000, UIA.IUIAutomationInvokePattern),
    "value": (10002, UIA.IUIAutomationValuePattern),
    "scroll": (10004, UIA.IUIAutomationScrollPattern),
    "expand_collapse": (10005, UIA.IUIAutomationExpandCollapsePattern),
    "selection_item": (10010, UIA.IUIAutomationSelectionItemPattern),
    "toggle": (10015, UIA.IUIAutomationTogglePattern),
}
RUNTIME_ID = 30000
PROCESS_ID, CONTROL_TYPE, NAME, IS_ENABLED, AUTOMATION_ID = 30002, 30003, 30005, 30010, 30011
HELP_TEXT, NATIVE_WINDOW_HANDLE = 30013, 30020
IS_OFFSCREEN, VALUE, VALUE_READONLY, IS_SELECTED = 30022, 30045, 30046, 30079
WINDOW_INTERACTION_STATE, WINDOW_IS_MODAL = 30076, 30077
BLOCKED_BY_MODAL_WINDOW = 3
WINDOW_CONTROL = 50032
TREE_SCOPE_SUBTREE = 7
# Packaged (UWP) apps: the top window belongs to the frame host and the app's
# content is a CoreWindow child owned by the app's own process.
FRAME_WINDOW, CORE_WINDOW = "ApplicationFrameWindow", "Windows.UI.Core.CoreWindow"


class PatternUnavailable(LookupError):
    """The element stopped exposing the pattern; nothing was sent."""
# Is<Pattern>PatternAvailable: read as properties, so one cached round trip
# answers every pattern instead of one cross-process call per pattern.
PATTERN_AVAILABLE = {"invoke": 30031, "value": 30043, "scroll": 30034,
                     "expand_collapse": 30028, "selection_item": 30036, "toggle": 30041}
CACHED = (RUNTIME_ID, PROCESS_ID, CONTROL_TYPE, NAME, IS_ENABLED, AUTOMATION_ID, HELP_TEXT, NATIVE_WINDOW_HANDLE,
          IS_OFFSCREEN, VALUE, VALUE_READONLY, IS_SELECTED, *PATTERN_AVAILABLE.values())
TREE_SCOPE_ELEMENT = 1
TREE_SCOPE_CHILDREN, TREE_SCOPE_DESCENDANTS = 2, 4
# ScrollAmount: LargeDecrement 0, SmallDecrement 1, NoAmount 2, LargeIncrement 3.
LARGE_DECREMENT, NO_AMOUNT, LARGE_INCREMENT = 0, 2, 3
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


from ctypes import wintypes  # noqa: E402

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
# Explicit signatures: a 64-bit HANDLE must not be truncated to a C int.
_kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD,
                                                 wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
_kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
_kernel32.CloseHandle.restype = wintypes.BOOL
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_ENUM_WINDOWS = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_user32.EnumWindows.argtypes = (_ENUM_WINDOWS, wintypes.LPARAM)
_user32.EnumWindows.restype = wintypes.BOOL
_user32.IsWindowVisible.argtypes = (wintypes.HWND,)
_user32.IsWindowVisible.restype = wintypes.BOOL
_user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
_user32.GetClassNameW.restype = ctypes.c_int
_user32.FindWindowExW.argtypes = (wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR)
_user32.FindWindowExW.restype = wintypes.HWND
_user32.GetWindow.argtypes = (wintypes.HWND, wintypes.UINT)
_user32.GetWindow.restype = wintypes.HWND
_user32.IsWindow.argtypes = (wintypes.HWND,)
_user32.IsWindow.restype = wintypes.BOOL
_user32.IsWindowEnabled.argtypes = (wintypes.HWND,)
_user32.IsWindowEnabled.restype = wintypes.BOOL
GW_OWNER = 4
_dwmapi = ctypes.WinDLL("dwmapi")
_dwmapi.DwmGetWindowAttribute.argtypes = (wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
_dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long
DWMWA_CLOAKED = 14


def _owner(hwnd):
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _class(hwnd):
    buffer = ctypes.create_unicode_buffer(256)
    return buffer.value if _user32.GetClassNameW(hwnd, buffer, 256) else ""


def _shown(hwnd):
    """Visible and not cloaked: suspended packaged apps keep hidden frames."""
    if not _user32.IsWindowVisible(hwnd):
        return False
    cloaked = wintypes.DWORD()
    failed = _dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
    return bool(failed) or not cloaked.value


def _owner_window(hwnd):
    return _user32.GetWindow(hwnd, GW_OWNER)


def _disables_its_owner(hwnd, pid, *, owner_of=_owner_window, pid_of=_owner,
                        is_window=_user32.IsWindow, enabled=_user32.IsWindowEnabled):
    """Win32 evidence that a top window is modal for its process.

    DialogBox, MessageBox and the common file dialogs disable the window that
    owns them until they close; a modeless owned window (Find, a palette, a
    notice) leaves its owner enabled. UI Automation can list such a dialog at
    the top level with IsModal false and the owner not yet marked blocked
    (Windows CI run 39564444225), so the owner's state is read directly.

    Only an owner in the same process counts. No owner, an owner that cannot be
    read, or one that disappears while being read is no evidence: False.
    """
    owner = owner_of(hwnd)
    if not owner or owner == hwnd or not is_window(owner) or pid_of(owner) != pid:
        return False
    disabled = not enabled(owner)
    # Re-read: a closed owner reads as disabled, and its handle may be reused.
    return disabled and bool(is_window(owner)) and pid_of(owner) == pid and owner_of(hwnd) == owner


def _executable(pid):
    kernel32 = _kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return None
        return os.path.basename(buffer.value)
    finally:
        kernel32.CloseHandle(handle)


class UIABackend:
    def __init__(self):
        self._uia = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)
        self._walker = self._uia.ControlViewWalker
        self._cache = self._uia.CreateCacheRequest()
        for prop in CACHED:
            self._cache.AddProperty(prop)
        self._cache.TreeScope = TREE_SCOPE_ELEMENT
        # A whole window in one call. Win32 controls answer each property
        # call through a proxy, so walking them one by one took ~9 ms a node.
        self._tree_cache = self._uia.CreateCacheRequest()
        for prop in CACHED:
            self._tree_cache.AddProperty(prop)
        self._tree_cache.TreeScope = TREE_SCOPE_SUBTREE

    def _windows_by_pid(self):
        """{pid: [top window handles]}, front first, from one Win32 pass.

        Asking UI Automation for the desktop's children queries every app's
        provider, which a slow app turns into most of a second; EnumWindows
        reads the window list itself. A packaged (UWP) app's frame belongs to
        the frame host while its content is a CoreWindow child owned by the
        app, so the frame is listed under the app's pid as well.
        """
        out = {}

        def visit(hwnd, _):
            if hwnd and _shown(hwnd):
                out.setdefault(_owner(hwnd), []).append(hwnd)
                if _class(hwnd) == FRAME_WINDOW:
                    content = _user32.FindWindowExW(hwnd, None, CORE_WINDOW, None)
                    if content:
                        out.setdefault(_owner(content), []).append(hwnd)
            return True
        _user32.EnumWindows(_ENUM_WINDOWS(visit), 0)
        return out

    def processes(self):
        out = []
        for pid in self._windows_by_pid():
            exe = _executable(pid)
            if exe:
                out.append({"pid": pid, "exe": exe, "name": os.path.splitext(exe)[0]})
        return out

    def top_windows(self, pid):
        # A packaged app's frame title bar stays in the frame host's process,
        # so the driver's same-process check still refuses it; only the app's
        # own content can be acted on.
        windows = []
        for hwnd in self._windows_by_pid().get(pid, []):
            try:
                windows.append(self._uia.ElementFromHandle(hwnd))
            except (comtypes.COMError, ValueError):
                continue  # closed since it was listed
        return windows

    def children(self, element):
        out = []
        # comtypes returns a falsy NULL pointer, not None, at the end.
        child = self._walker.GetFirstChildElement(element)
        while child and len(out) < 10000:
            out.append(child)
            try:
                child = self._walker.GetNextSiblingElement(child)
            except comtypes.COMError:
                break
        return out

    def descendants(self, element):
        found = element.FindAll(TREE_SCOPE_DESCENDANTS, self._uia.CreateTrueCondition())
        return [found.GetElement(i) for i in range(min(found.Length, 5000))]

    def _pattern(self, element, name):
        pattern_id, interface = PATTERNS[name]
        raw = element.GetCurrentPattern(pattern_id)
        if not raw:
            raise PatternUnavailable(name)
        return raw.QueryInterface(interface)

    def walker(self, window):
        """(root, props, children) for one window, read in a single call.

        The cached elements stay live references, so an observed element can
        still be acted on. If the subtree cannot be cached, walk it live.
        """
        try:
            root = window.BuildUpdatedCache(self._tree_cache)
        except (comtypes.COMError, ValueError):
            return window, self.props, self.children
        return root, self._read, self._cached_children

    def _cached_children(self, element):
        found = element.GetCachedChildren()
        # NULL, not an empty array, when the element has no children.
        return [found.GetElement(i) for i in range(found.Length)] if found else []

    def props(self, element):
        """Current properties in one cross-process round trip."""
        try:
            cached = element.BuildUpdatedCache(self._cache)
        except (comtypes.COMError, ValueError):
            return {"control_type": None, "runtime_id": None, "pid": None, "patterns": set()}
        return self._read(cached)

    def _read(self, cached):
        def value(prop):
            try:
                return cached.GetCachedPropertyValue(prop)
            except comtypes.COMError:
                return None
        patterns = {name for name, prop in PATTERN_AVAILABLE.items() if value(prop) is True}
        raw_id = value(RUNTIME_ID)
        try:
            runtime_id = tuple(raw_id) if raw_id is not None else None
        except TypeError:
            runtime_id = None
        return {
            "control_type": CONTROL_TYPES.get(value(CONTROL_TYPE)),
            "name": value(NAME), "automation_id": value(AUTOMATION_ID),
            "help": value(HELP_TEXT), "enabled": value(IS_ENABLED),
            "offscreen": value(IS_OFFSCREEN), "pid": value(PROCESS_ID),
            "value": value(VALUE) if "value" in patterns else None,
            "readonly": value(VALUE_READONLY) if "value" in patterns else None,
            "selected": value(IS_SELECTED) if "selection_item" in patterns else None,
            "runtime_id": runtime_id, "patterns": patterns,
            "hwnd": value(NATIVE_WINDOW_HANDLE) or None,
        }

    def front_window(self, hwnds):
        """The highest of these windows in z-order: the app's own front window.

        EnumWindows lists top-level windows from the top down, so the first
        visible match is the one the app last brought forward, even while the
        person works in another app. Read-only; nothing is activated.
        """
        wanted = {int(h) for h in hwnds if h}
        found = []

        def visit(hwnd, _):
            if hwnd and int(hwnd) in wanted and _user32.IsWindowVisible(hwnd):
                found.append(int(hwnd))
                return False
            return True
        _user32.EnumWindows(_ENUM_WINDOWS(visit), 0)
        return found[0] if found else None

    def modal_present(self, pid):
        """True/False for this process's windows, None if it cannot be read.

        Owned dialogs (Save As, "save changes?") sit under their owner window,
        not at the desktop root, so each top window is asked whether it is
        blocked and its subtree is searched for a modal window. A dialog that
        is listed at the top level itself is found by the owner it disables.
        """
        try:
            if any(_disables_its_owner(hwnd, pid) for hwnd in self._windows_by_pid().get(pid, [])):
                return True
        except (OSError, ValueError):
            pass  # no Win32 evidence; UI Automation still answers below
        modal = self._uia.CreateAndCondition(
            self._uia.CreatePropertyCondition(CONTROL_TYPE, WINDOW_CONTROL),
            self._uia.CreatePropertyCondition(WINDOW_IS_MODAL, True))
        try:
            for window in self.top_windows(pid):
                if window.GetCurrentPropertyValue(WINDOW_IS_MODAL) is True:
                    return True
                state = window.GetCurrentPropertyValue(WINDOW_INTERACTION_STATE)
                if state == BLOCKED_BY_MODAL_WINDOW:
                    return True
                if window.FindFirst(TREE_SCOPE_SUBTREE, modal):
                    return True
            return False
        except (comtypes.COMError, ValueError):
            return None

    def invoke(self, element):
        self._pattern(element, "invoke").Invoke()

    def toggle(self, element):
        self._pattern(element, "toggle").Toggle()

    def select(self, element):
        self._pattern(element, "selection_item").Select()

    def expand(self, element):
        self._pattern(element, "expand_collapse").Expand()

    def collapse(self, element):
        try:
            self._pattern(element, "expand_collapse").Collapse()
        except PatternUnavailable:
            pass

    def set_value(self, element, text):
        self._pattern(element, "value").SetValue(text)

    def scroll_container(self, element):
        current = element
        for _ in range(64):
            if not current:
                return None
            available = current.GetCurrentPropertyValue(PATTERN_AVAILABLE["scroll"]) is True
            pattern = self._pattern(current, "scroll") if available else None
            if pattern is not None and (pattern.CurrentVerticallyScrollable or pattern.CurrentHorizontallyScrollable):
                return current
            current = self._walker.GetParentElement(current)
        return None

    def scroll(self, element, direction, pages):
        pattern = self._pattern(element, "scroll")
        vertical = {"down": LARGE_INCREMENT, "up": LARGE_DECREMENT}.get(direction, NO_AMOUNT)
        horizontal = {"right": LARGE_INCREMENT, "left": LARGE_DECREMENT}.get(direction, NO_AMOUNT)
        for _ in range(pages):
            pattern.Scroll(horizontal, vertical)
