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
HELP_TEXT, IS_OFFSCREEN, VALUE, VALUE_READONLY, IS_SELECTED = 30013, 30022, 30045, 30046, 30079
WINDOW_INTERACTION_STATE, WINDOW_IS_MODAL = 30076, 30077
BLOCKED_BY_MODAL_WINDOW = 3
WINDOW_CONTROL = 50032
TREE_SCOPE_SUBTREE = 7


class PatternUnavailable(LookupError):
    """The element stopped exposing the pattern; nothing was sent."""
# Is<Pattern>PatternAvailable: read as properties, so one cached round trip
# answers every pattern instead of one cross-process call per pattern.
PATTERN_AVAILABLE = {"invoke": 30031, "value": 30043, "scroll": 30034,
                     "expand_collapse": 30028, "selection_item": 30036, "toggle": 30041}
CACHED = (RUNTIME_ID, PROCESS_ID, CONTROL_TYPE, NAME, IS_ENABLED, AUTOMATION_ID, HELP_TEXT,
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

    def _root(self):
        return self._uia.GetRootElement()

    def processes(self):
        seen, out = set(), []
        windows = self._root().FindAll(TREE_SCOPE_CHILDREN, self._uia.CreateTrueCondition())
        for i in range(windows.Length):
            try:
                pid = windows.GetElement(i).CurrentProcessId
            except (comtypes.COMError, ValueError):
                continue  # a window that closed while being listed
            if pid in seen:
                continue
            seen.add(pid)
            exe = _executable(pid)
            if exe:
                out.append({"pid": pid, "exe": exe, "name": os.path.splitext(exe)[0]})
        return out

    def top_windows(self, pid):
        condition = self._uia.CreatePropertyCondition(PROCESS_ID, pid)
        found = self._root().FindAll(TREE_SCOPE_CHILDREN, condition)
        return [found.GetElement(i) for i in range(found.Length)]

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

    def props(self, element):
        """Current properties in one cross-process round trip."""
        try:
            cached = element.BuildUpdatedCache(self._cache)
        except (comtypes.COMError, ValueError):
            return {"control_type": None, "runtime_id": None, "pid": None, "patterns": set()}
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
        }

    def modal_present(self, pid):
        """True/False for this process's windows, None if it cannot be read.

        Owned dialogs (Save As, "save changes?") sit under their owner window,
        not at the desktop root, so each top window is asked whether it is
        blocked and its subtree is searched for a modal window.
        """
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
