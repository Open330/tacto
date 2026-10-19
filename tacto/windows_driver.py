"""Windows backend: UI Automation through the same L2 protocol as macOS.

Every action uses an element's own UI Automation pattern (Invoke, Value,
Toggle, SelectionItem, ExpandCollapse, Scroll). Nothing here synthesizes
mouse or keyboard input, activates a window or moves the pointer, so the
person keeps their foreground app. An element that only answers to real
input is refused before anything is sent (`needs_pointer` /
`needs_foreground`, effect `not_started`).

The UIA calls live behind a small backend (`tacto.uia_backend` on Windows);
this module only formats observations and decides what may be dispatched, so
it imports and is tested on any platform.
"""

from __future__ import annotations

import time
import uuid

MAX_TREE_NODES = 5000
MAX_TREE_DEPTH = 64
MAX_CHILDREN_PER_NODE = 400

# UIA control type -> the role names the engine already understands. Edit
# and Document are the text inputs; ListItem/DataItem are rows and cells.
ROLES = {
    "Button": "Button", "SplitButton": "Button", "CheckBox": "CheckBox",
    "RadioButton": "RadioButton", "ComboBox": "PopUpButton",
    "Hyperlink": "Link", "MenuItem": "MenuItem", "ListItem": "Row",
    "TreeItem": "Row", "DataItem": "Cell", "TabItem": "RadioButton",
    "Edit": "TextField", "Document": "TextArea", "Text": "StaticText",
    "Image": "Image", "Window": "Window", "Pane": "Group", "Group": "Group",
    "List": "List", "Tree": "Outline", "DataGrid": "Table", "Table": "Table",
    "Tab": "TabGroup", "ToolBar": "Toolbar", "MenuBar": "MenuBar",
    "Menu": "Menu", "ScrollBar": "ScrollBar", "Slider": "Slider",
    "Spinner": "Incrementor", "ProgressBar": "ProgressIndicator",
    "StatusBar": "Group", "TitleBar": "Group", "Header": "Group",
    "HeaderItem": "Button", "Calendar": "Group", "Custom": "Group",
}
# Shown even without a name: what a person could act on.
INTERACTABLE = frozenset({"Button", "CheckBox", "RadioButton", "PopUpButton", "Link",
                          "MenuItem", "Row", "Cell", "TextField", "TextArea", "Window"})
SCROLL_DIRECTIONS = frozenset({"up", "down", "left", "right"})
MAX_SCROLL_PAGES = 10


class keep_awake:
    """Keep the display on while one command runs (Windows only, else a no-op).

    The same reason as macOS: an agent drives one command at a time, and a
    display that sleeps mid-run turns a task into a locked-screen failure.
    """
    _CONTINUOUS, _SYSTEM, _DISPLAY = 0x80000000, 0x00000001, 0x00000002

    def __enter__(self):
        import sys
        self._kernel32 = None
        if sys.platform == "win32":
            import ctypes
            kernel32 = ctypes.WinDLL("kernel32")
            # EXECUTION_STATE is an unsigned DWORD; 0x80000000 overflows a C int.
            kernel32.SetThreadExecutionState.argtypes = (ctypes.c_uint32,)
            kernel32.SetThreadExecutionState.restype = ctypes.c_uint32
            self._kernel32 = kernel32
            kernel32.SetThreadExecutionState(self._CONTINUOUS | self._SYSTEM | self._DISPLAY)
        return self

    def __exit__(self, *exc):
        if self._kernel32 is not None:
            self._kernel32.SetThreadExecutionState(self._CONTINUOUS)
        return False


class NotStarted(RuntimeError):
    """Refused before anything was sent to the application."""


class _NeedsPointer(NotStarted):
    """Only real pointer input would reach this element; nothing was sent."""


class _NeedsForeground(NotStarted):
    """Only keyboard input to the foreground window would do this; nothing was sent."""


def _one_line(text):
    return " ".join(str(text).split())


def _label(props, *, quote=False):
    """'Key: value' pairs; window values are JSON-quoted as on macOS so a
    title cannot spoof another key."""
    from .document_label import quote_value
    parts = []
    for key, field in (("Title", "name"), ("Value", "value"), ("Help", "help"),
                       ("Identifier", "automation_id")):
        value = props.get(field)
        if isinstance(value, str) and value.strip():
            text = _one_line(value)[:200]
            parts.append(f"{key}: {quote_value(text) if quote else text}")
    return ", ".join(parts)


def _fingerprint(props):
    """What an observed index must still be: same UIA element, same kind, same name.

    A Win32 proxy's runtime id is positional (window handle, child id), so a
    row inserted above would otherwise pass as the observed one.
    """
    return (props.get("runtime_id"), props.get("pid"), props.get("control_type"),
            props.get("name"), props.get("automation_id"))


class Observation:
    def __init__(self, pid, target):
        self.obs_id = "w" + uuid.uuid4().hex[:8]
        self.pid = pid
        self.target = target
        self.elements = []
        self.identities = []
        self.lines = []
        self.visited = 0
        self.truncated = False
        self.seen = set()


class WindowsDriver:
    """In-process UI Automation driver; no signed helper exists on Windows yet."""

    capabilities = frozenset({
        "observe", "act", "probe", "effect", "expect.role", "expect.label",
        "expect.attribute", "expect.enabled", "expect.no_modal",
        "action.click", "action.set_value", "action.read", "action.scroll",
        "action.select_option", "select_option.label_only",
    })

    def __init__(self, backend=None, *, clock=time.monotonic, sleep=time.sleep):
        if backend is None:
            from .uia_backend import UIABackend
            backend = UIABackend()
        self._backend = backend
        self._clock = clock
        self._sleep = sleep
        self._latest = {}

    # ------------------------------------------------------------ targets
    def list_targets(self):
        apps = [{"id": app["exe"], "displayName": app.get("name") or app["exe"],
                 "pid": app["pid"], "isRunning": True}
                for app in self._backend.processes() if self._backend.top_windows(app["pid"])]
        return {"apps": apps}

    def _resolve(self, target, launch=False):
        """A process id: 'pid:N', or an executable name with or without .exe."""
        if not isinstance(target, str) or not target.strip():
            return None
        if target.startswith("pid:"):
            try:
                pid = int(target[4:])
            except ValueError:
                return None
            return pid if any(app["pid"] == pid for app in self._backend.processes()) else None
        want = target.lower()
        want = want if want.endswith(".exe") else want + ".exe"
        matches = [app["pid"] for app in self._backend.processes()
                   if app["exe"].lower() == want and self._backend.top_windows(app["pid"])]
        # Two running copies cannot be told apart by name; ask for 'pid:N'.
        return matches[0] if len(matches) == 1 else None

    def identity(self, target):
        if isinstance(target, str) and target.startswith("pid:"):
            pid = self._resolve(target)
            exe = next((app["exe"] for app in self._backend.processes() if app["pid"] == pid), None)
            return "win:" + (exe or target).lower()
        name = target.lower()
        return "win:" + (name if name.endswith(".exe") else name + ".exe")

    def close(self):
        self._latest.clear()

    # --------------------------------------------------------- observation
    def _walk(self, obs, element, depth, *, front=False, read=None):
        if depth > MAX_TREE_DEPTH or obs.visited >= MAX_TREE_NODES:
            obs.truncated = True
            return
        obs.visited += 1
        try:
            props = read[0](element)
        except Exception:  # an element can vanish mid-walk; never guess its content
            obs.truncated = True
            return
        # A popup (menu, flyout) is both a top-level window and, in UIA, a
        # child of its owner; show it once so its items stay unique.
        runtime_id = props.get("runtime_id")
        if runtime_id is not None:
            if runtime_id in obs.seen:
                return
            obs.seen.add(runtime_id)
        role = ROLES.get(props.get("control_type"), "Group")
        if props.get("offscreen") is True and role != "Window":
            return
        label = _label(props, quote=role == "Window")
        if role in INTERACTABLE or label:
            index = len(obs.elements)
            obs.elements.append(element)
            obs.identities.append(_fingerprint(props))
            readonly = " [readonly]" if role in ("TextField", "TextArea") and props.get("readonly") is True else ""
            disabled = "" if props.get("enabled") is not False else " [disabled]"
            selected = " [selected]" if props.get("selected") is True else ""
            # As on macOS: the app's front window, so a recipe can say "the
            # one the person is looking at" when two documents look alike.
            focused = " [focused]" if front and role == "Window" else ""
            obs.lines.append(f"{'  ' * depth}{index} {role}{readonly}"
                             + (f"  {label}" if label else "") + disabled + selected + focused)
        try:
            children = read[1](element)
        except Exception:
            obs.truncated = True
            return
        for child in children[:MAX_CHILDREN_PER_NODE]:
            self._walk(obs, child, depth + 1, read=read)
        if len(children) > MAX_CHILDREN_PER_NODE:
            obs.truncated = True

    def observe(self, target, mode="tree_full", require_window=True, **_ignored):
        if mode not in ("tree_full", "tree_diff"):
            return {"type": "error", "error": "not_implemented", "detail": f"mode {mode!r}"}
        try:
            pid = self._resolve(target)
            windows = self._backend.top_windows(pid) if pid is not None else []
        except Exception:
            # UI Automation can fail transiently (an app's provider is busy);
            # report it rather than raise, and never guess what is on screen.
            return {"type": "error", "error": "observation_unavailable", "target": target,
                    "reason": "uia_error"}
        if pid is None:
            return {"type": "error", "error": "target_not_found", "target": target,
                    "detail": "No single running app with a window matches; use the "
                              "executable name (notepad.exe) or 'pid:N'."}
        obs = Observation(pid, target)
        kinds = self._kinds(windows)
        front = self._front(windows, kinds)
        # Real windows first: a popup host listed on its own is then already
        # shown inside its owner, where it belongs.
        for position in sorted(range(len(windows)), key=lambda i: kinds[i] != "Window"):
            try:
                root, props, children = self._backend.walker(windows[position])
            except Exception:
                root, props, children = windows[position], self._backend.props, self._backend.children
            self._walk(obs, root, 0, front=position == front, read=(props, children))
        if obs.truncated:
            obs.lines.append("… (트리 일부만 표시)")
        if not windows:
            if require_window:
                return {"type": "error", "error": "no_window_content", "target": target}
            obs.lines.append("(창 없음)")
        self._latest[target.lower()] = obs
        return {"type": "observation", "obs_id": obs.obs_id, "target": target, "mode": mode,
                "tree": "\n".join(obs.lines), "element_count": len(obs.elements),
                "custom_selects": {}, "truncated": obs.truncated, "waited_ms": 0}

    def _kinds(self, windows):
        kinds = []
        for window in windows:
            try:
                kinds.append(self._backend.props(window).get("control_type"))
            except Exception:
                kinds.append(None)
        return kinds

    def _front(self, windows, kinds):
        """Position of the app's front window, or None when it cannot be told.

        Only a real window counts: a menu popup in front belongs to it.
        """
        try:
            hwnds = [self._backend.props(window).get("hwnd") if kind == "Window" else None
                     for window, kind in zip(windows, kinds)]
            front = self._backend.front_window([h for h in hwnds if h])
        except Exception:
            return None  # unmarked: an in_focused_window selector then fails closed
        return hwnds.index(front) if front is not None and hwnds.count(front) == 1 else None

    def wait_until(self, target, predicate, timeout_ms=4000, poll_ms=250):
        from .windows_control import stopped
        deadline = self._clock() + max(0, timeout_ms) / 1000
        last = None
        while True:
            if stopped():
                return {"type": "error", "error": "aborted_by_user", "target": target,
                        "detail": "사용자가 Esc로 중단했습니다"}
            last = self.observe(target, "tree_full", require_window=False)
            if last.get("type") == "observation" and predicate(last["tree"]):
                return last
            if self._clock() >= deadline:
                return {"type": "timeout", "target": target,
                        "waited_ms": int(timeout_ms), "last_obs": last}
            self._sleep(max(0.01, poll_ms / 1000))

    # -------------------------------------------------------------- actions
    def act(self, obs_id, target, action, expect=None, risk_class="none", intent_text="",
            approved=False, **_dispatch):
        from .windows_control import stopped
        if stopped():
            return {"type": "error", "error": "aborted_by_user", "effect": "not_started",
                    "detail": "사용자가 Esc로 중단했습니다"}
        if risk_class not in ("none", "preapproved", "confirm", "handoff"):
            return {"type": "error", "error": "unverifiable_action", "effect": "not_started"}
        if risk_class == "handoff":
            return {"type": "handoff_request", "reason": intent_text or "user_action_required"}
        if risk_class in ("confirm", "preapproved") and not approved:
            return {"type": "confirm_request", "what": intent_text, "risk_class": risk_class,
                    "detail": "resend with approved=true after user consent"}
        obs = self._latest.get(target.lower()) if isinstance(target, str) else None
        if obs is None or obs.obs_id != obs_id:
            return {"type": "error", "error": "stale_observation", "effect": "not_started"}
        if not isinstance(action, dict):
            return {"type": "error", "error": "invalid_action", "effect": "not_started"}
        name = action.get("name")
        if expect:
            why = self._mismatch(obs, action, expect)
            if why:
                return {"type": "error", "error": "target_changed", "effect": "not_started",
                        "detail": why}
        try:
            result = self._perform(obs, action, name)
        except (NotStarted, LookupError) as exc:
            # LookupError: the backend found the pattern gone before calling it.
            out = {"type": "error", "error": "action_failed", "action": name,
                   "effect": "not_started", "detail": str(exc)}
            if isinstance(exc, _NeedsPointer):
                out["needs_pointer"] = True
            if isinstance(exc, _NeedsForeground):
                out["needs_foreground"] = True
            return out
        except Exception as exc:  # the pattern call may have reached the app
            return {"type": "error", "error": "action_failed", "action": name,
                    "effect": "unknown", "detail": str(exc)[:200] if isinstance(exc, RuntimeError)
                    and not isinstance(exc, OSError) else type(exc).__name__}
        # A pattern call is the app's own synchronous action, like AXPress on
        # macOS: the engine verifies its outcome with postconditions.
        return {"type": "acted", "action": name, "obs_id": obs_id,
                "note": "not verified — observe again and check the result (D7)",
                **(result or {})}

    def _mismatch(self, obs, action, expect):
        """Why this is no longer what the caller meant, or "" if it still is.

        The same contract as the macOS driver: an expectation that cannot be
        checked is refused, never silently treated as met.
        """
        if expect.get("selection") is not None:
            return "selection expectations are not supported on Windows"
        if expect.get("no_modal") and self._backend.modal_present(obs.pid) is not False:
            return "a modal window is open or its presence could not be read"
        if action.get("element_index") is None:
            named = [k for k in ("role", "label", "attribute", "enabled") if expect.get(k) is not None]
            return (f"no target element to check {', '.join(named)} against" if named else "")
        try:
            _, props = self._element(obs, action)
        except NotStarted as exc:
            return str(exc)
        if expect.get("enabled") and props.get("enabled") is not True:
            return "target is disabled or its state could not be read"
        want_role = expect.get("role")
        role = ROLES.get(props.get("control_type"), "Group")
        if want_role and role != want_role:
            return f"target changed (expected {want_role}, now {role})"
        want = expect.get("label")
        if want is not None:
            from .flow import Selector, _norm
            seen = _label(props)
            key = expect.get("attribute")
            attrs = (key,) if key else Selector._ATTR_KEYS
            def normalized(value, attr):
                value = _norm(value)
                return Selector._stable_id(value) if attr == "Identifier" else value
            if not any((got := Selector._attr(seen, attr)) is not None
                       and normalized(got, attr) == normalized(want, attr) for attr in attrs):
                return f"target changed (expected {want!r})"
        return ""

    def probe(self, obs_id, target, element_index):
        """One element's current attributes without walking the tree."""
        obs = self._latest.get(target.lower()) if isinstance(target, str) else None
        if obs is None or obs.obs_id != obs_id:
            return {"type": "error", "error": "stale_observation"}
        try:
            _, props = self._element(obs, {"element_index": element_index})
        except NotStarted as exc:
            return {"type": "error", "error": "target_missing", "detail": str(exc)}
        return {"type": "probe", "line": _label(props)}

    def _element(self, obs, action):
        index = action.get("element_index")
        if type(index) is not int or not 0 <= index < len(obs.elements):
            raise NotStarted("element_index out of range")
        element = obs.elements[index]
        if obs.identities[index][0] is None:
            raise NotStarted("element identity unavailable")
        props = self._backend.props(element)
        # The same element, still in the same process: a reused index after the
        # window changed must not address whatever now sits in its place.
        if _fingerprint(props) != obs.identities[index] or props.get("pid") != obs.pid:
            raise NotStarted("target changed since observation")
        return element, props

    def _perform(self, obs, action, name):
        backend = self._backend
        if name == "read":
            if "element_index" in action:
                self._element(obs, action)
            return None
        if name == "press_key" or name == "type_text":
            raise _NeedsForeground("keyboard input would take the person's foreground window")
        element, props = self._element(obs, action)
        patterns = props.get("patterns") or set()
        if props.get("enabled") is False:
            raise NotStarted("element is disabled")
        if name == "click":
            for pattern, call in (("invoke", backend.invoke), ("toggle", backend.toggle),
                                  ("selection_item", backend.select),
                                  ("expand_collapse", backend.expand)):
                if pattern in patterns:
                    call(element)
                    return {"input_route": "uia_" + pattern}
            raise _NeedsPointer("element exposes no invoke/toggle/select/expand pattern")
        if name == "set_value":
            value = action.get("value")
            if not isinstance(value, str):
                raise NotStarted("set_value requires a text value")
            if "value" not in patterns or props.get("readonly") is True:
                raise NotStarted("element has no writable value")
            backend.set_value(element, value)
            return {"input_route": "uia_value"}
        if name == "scroll":
            direction, pages = action.get("direction"), action.get("pages", 1)
            if direction not in SCROLL_DIRECTIONS or type(pages) is not int or not 1 <= pages <= MAX_SCROLL_PAGES:
                raise NotStarted("invalid scroll")
            container = backend.scroll_container(element)
            if container is None:
                raise NotStarted("no scrollable container")
            backend.scroll(container, direction, pages)
            return {"input_route": "uia_scroll"}
        if name == "select_option":
            label = action.get("option_label") or action.get("value")
            if not isinstance(label, str) or not label:
                raise NotStarted("option_label_required")
            if "value" in action and action.get("value") != label:
                raise NotStarted("native_option_value_mismatch")
            if "expand_collapse" not in patterns:
                raise NotStarted("not a selectable list")
            # Opening the list is already input to the app, so from here a
            # refusal is reported with an unknown effect, never not_started.
            backend.expand(element)
            try:
                # One cached read per option: each is a cross-process call.
                options = [(option, props) for option in backend.descendants(element)
                           for props in (backend.props(option),)
                           if props.get("control_type") == "ListItem" and props.get("name") == label]
                if len(options) != 1:
                    raise RuntimeError("option absent or ambiguous")
                if "selection_item" not in (options[0][1].get("patterns") or set()):
                    raise RuntimeError("option cannot be selected")
                try:
                    backend.select(options[0][0])
                except LookupError as exc:
                    raise RuntimeError("option cannot be selected") from exc
            finally:
                backend.collapse(element)
            return {"input_route": "uia_selection_item"}
        raise NotStarted(f"unsupported action {name!r} on Windows")
