"""NLC Protocol P0 driver — macOS accessibility (AX) backend.

L1 (OS driver) + L2 (action protocol) in one module.
Implements the PROTOCOL.md v0.1 decisions:
  D1 observe = AX-tree first, diff by default
  D3 element_index bound to obs_id; stale indices rejected at protocol level
  D5 auto-wait built into observation (no sleep primitive exposed)
  D6 risk_class gate (P0: structural stub — confirm-class acts need approved=True)
  D7 caller must verify via a fresh observation; act never claims task success
"""

from __future__ import annotations

import difflib
import subprocess
import threading
import time
import uuid

# CGEvent input synthesis (typing, key combos, coordinate clicks/scroll) targets
# the globally focused app, so concurrent flows must serialize it. AX actions
# (AXPress, AXValue) are per-element and need no focus — they run truly parallel.
_INPUT_LOCK = threading.Lock()

# Synthetic input is tagged so a cooperating app (or an audit tool) can tell our
# events apart from a human's. macOS has no public "this window is being
# automated" flag, but CGEvent carries user data on its source, which is the one
# public, cooperative signal available (advisor-confirmed).
NLC_EVENT_MARKER = 0x4E4C4331        # 'NLC1'
_EVENT_SOURCE = None


def _event_source():
    """A private-state event source stamped with our marker."""
    global _EVENT_SOURCE
    if _EVENT_SOURCE is None:
        try:
            import Quartz as _Q
            src = _Q.CGEventSourceCreate(_Q.kCGEventSourceStatePrivate)
            if src is not None:
                _Q.CGEventSourceSetUserData(src, NLC_EVENT_MARKER)
            _EVENT_SOURCE = src
        except Exception:
            _EVENT_SOURCE = False      # unavailable; fall back to None source
    return _EVENT_SOURCE or None

from AppKit import NSRunningApplication, NSWorkspace
from ApplicationServices import (
    AXIsProcessTrusted,
    AXUIElementCreateApplication,
    AXUIElementCopyAttributeValue,
    AXUIElementPerformAction,
    AXUIElementSetAttributeValue,
)
import Quartz

# ---------------------------------------------------------------- AX helpers

AX_INTERACTABLE_ROLES = {
    "AXButton", "AXTextField", "AXTextArea", "AXCheckBox", "AXRadioButton",
    "AXPopUpButton", "AXMenuButton", "AXMenuItem", "AXLink", "AXComboBox",
    "AXSlider", "AXIncrementor", "AXDisclosureTriangle", "AXTabGroup",
    "AXStaticText", "AXCell", "AXRow", "AXScrollArea", "AXGroup", "AXImage",
}

AUTO_WAIT_BASE_S = 1.0     # D5: settle time after an action before capture
MAX_TREE_DEPTH = 25
# Every attribute read is an interprocess round trip, so the cost of observing
# scales with the size of the app's tree — and some apps are enormous. Mail was
# still walking after 40 seconds and 11,000 elements, which is not "slow", it is
# a hang: the app people most want help with was the one that never answered.
# A node budget makes the worst case bounded and, unlike a timeout, identical on
# every machine — so a recorded trace still replays the same way.
MAX_TREE_NODES = 2500
# One enormous, homogeneous list is what actually makes an app expensive: a
# mailbox holds thousands of rows that all look alike, and walking them all buys
# nothing a person asked for. Capping children per container keeps the SHAPE of
# the window — sidebar, toolbar, the first rows — where a global budget alone
# would spend everything inside the first big list and never reach the rest.
MAX_CHILDREN_PER_NODE = 60
TRUNCATION_MARK = "… (트리가 커서 여기서 잘렸습니다 — 더 좁은 창/앱을 대상으로 하세요)"


def _attr(el, name):
    err, value = AXUIElementCopyAttributeValue(el, name, None)
    return value if err == 0 else None


_LABEL_ATTRS = ("AXTitle", "AXDescription", "AXValue", "AXHelp",
                "AXIdentifier", "AXPlaceholderValue")


def _elem_label(el):
    """Compact one-line description of an AX element.

    Fetched in ONE round trip rather than six. Some apps compute these lazily
    per element — a Mail message row costs 20ms read one attribute at a time and
    3ms read together, and Mail has thousands of rows, which is the difference
    between "slow" and an observation that never returns.

    An attribute the element does not have comes back as an AXValue carrying an
    error rather than nil, so only real strings and numbers are kept.
    """
    parts = []
    values = None
    try:
        from ApplicationServices import AXUIElementCopyMultipleAttributeValues
        err, values = AXUIElementCopyMultipleAttributeValues(
            el, _LABEL_ATTRS, 0, None)
        if err != 0:
            values = None
    except Exception:
        values = None
    if values is None:                     # older bindings: one call each
        values = [_attr(el, name) for name in _LABEL_ATTRS]
    for name, v in zip(_LABEL_ATTRS, values):
        if not isinstance(v, (str, int, float)) or v == "":
            continue
        parts.append(f"{name[2:]}: {v}")   # strip the AX prefix
    return ", ".join(parts)


def _elem_frame(el):
    """(x, y, w, h) in screen coords, or None."""
    pos, size = _attr(el, "AXPosition"), _attr(el, "AXSize")
    if pos is None or size is None:
        return None
    # AXValueGetValue lives in ApplicationServices; Quartz does not export it,
    # so the coordinate fallback used to raise AttributeError the moment an
    # AXPress failed and it was actually needed.
    from ApplicationServices import (AXValueGetValue, kAXValueCGPointType,
                                     kAXValueCGSizeType)
    err_p, p = AXValueGetValue(pos, kAXValueCGPointType, None)
    err_s, s = AXValueGetValue(size, kAXValueCGSizeType, None)
    if not (err_p and err_s):
        return None
    return (p.x, p.y, s.width, s.height)


class Observation:
    def __init__(self, target: str, pid: int):
        self.obs_id = f"o{uuid.uuid4().hex[:8]}"
        self.target = target
        self.pid = pid
        self.elements: list = []   # index -> AXUIElement
        self.lines: list[str] = []
        self.created_at = time.time()
        self.visited = 0           # nodes touched, emitted or not
        self.truncated = False

    def walk(self, el, depth=0):
        if depth > MAX_TREE_DEPTH:
            return
        if self.visited >= MAX_TREE_NODES:
            self.truncated = True
            return
        self.visited += 1
        role = _attr(el, "AXRole") or "?"
        label = _elem_label(el)
        if role in AX_INTERACTABLE_ROLES or label:
            idx = len(self.elements)
            self.elements.append(el)
            indent = "  " * depth
            # Surface enabled state in the tree text so a replay precondition can
            # check it after re-resolving a selector (elements default enabled).
            disabled = "" if _attr(el, "AXEnabled") in (None, True) else " [disabled]"
            self.lines.append(f"{indent}{idx} {role[2:] if role.startswith('AX') else role}"
                              + (f"  {label}" if label else "") + disabled)
        children = _attr(el, "AXChildren") or []
        for child in children[:MAX_CHILDREN_PER_NODE]:
            self.walk(child, depth + 1)
        if len(children) > MAX_CHILDREN_PER_NODE:
            self.truncated = True
            self.lines.append("  " * (depth + 1)
                              + f"… ({len(children) - MAX_CHILDREN_PER_NODE}개 더 있음)")


class Driver:
    """Persistent L2 endpoint. One instance per session (obs registry lives here)."""

    def __init__(self):
        if not AXIsProcessTrusted():
            raise RuntimeError(
                "accessibility_permission_missing: grant this process "
                "Accessibility permission in System Settings")
        self._latest: dict[str, Observation] = {}   # target -> newest obs
        self._last_action_at = 0.0

    # ------------------------------------------------------------ targets

    def list_targets(self):
        apps = []
        for app in NSWorkspace.sharedWorkspace().runningApplications():
            if app.activationPolicy() != 0:   # regular apps only
                continue
            apps.append({
                "id": str(app.bundleIdentifier() or ""),
                "displayName": str(app.localizedName() or ""),
                "pid": int(app.processIdentifier()),
                "isRunning": True,
            })
        return {"type": "targets", "apps": apps}

    @staticmethod
    def _pump(seconds: float = 0.25):
        """Let NSWorkspace refresh its app list.

        `runningApplications()` is a cached snapshot updated by notifications
        delivered on the run loop. A plain script never runs one, so an app
        launched a moment ago stays invisible forever — which looked exactly
        like "the app isn't running" even while `pgrep` could see it (real user
        report). Draining the run loop briefly is what makes the list current.
        """
        try:
            from Foundation import NSRunLoop, NSDate
            NSRunLoop.currentRunLoop().runUntilDate_(
                NSDate.dateWithTimeIntervalSinceNow_(seconds))
        except Exception:
            pass

    def _resolve(self, target: str, launch=True, _pumped=False):
        want = target.lower()
        # An app's everyday name is not unique: "Messages" also names
        # com.apple.messages.AssistantExtension, a background agent with no
        # windows and no menu bar, and whichever came first in the list won.
        # A bundle id is an exact answer; a name means the app a person can see,
        # so regular apps (activation policy 0) beat agents and extensions.
        by_bundle, by_name = [], []
        for app in NSWorkspace.sharedWorkspace().runningApplications():
            bid = (app.bundleIdentifier() or "").lower()
            name = (app.localizedName() or "").lower()
            if want == bid:
                by_bundle.append(app)
            elif want == name:
                by_name.append(app)
        # A bundle id is explicit, so it may name anything. A NAME never means a
        # background agent: with the real Messages quit, falling back to
        # com.apple.messages.AssistantExtension would hand back a process with
        # no windows and no menus instead of reporting the app as absent.
        by_name = [a for a in by_name if a.activationPolicy() == 0]
        for group in (by_bundle, by_name):
            regular = [a for a in group if a.activationPolicy() == 0]
            for app in regular or group:
                return int(app.processIdentifier())
        if not _pumped:
            # The snapshot may simply be stale — refresh before concluding the
            # app is absent, and before paying to launch it.
            self._pump()
            pid = self._resolve(target, launch=False, _pumped=True)
            if pid is not None:
                return pid
        if launch:
            # Like Codex's get_app_state: launch in background transparently.
            flag = "-b" if "." in target else "-a"
            subprocess.run(["open", "-g", flag, target], check=True)
            deadline = time.time() + 10
            while time.time() < deadline:
                self._pump(0.3)        # pump, don't just sleep, or we never see it
                pid = self._resolve(target, launch=False, _pumped=True)
                if pid is not None:
                    time.sleep(0.7)   # let the UI build before first capture
                    return pid
        return None

    # ------------------------------------------------------------ observe

    def _walk_windows(self, obs: "Observation") -> bool:
        """Walk an app's real window content into `obs`. Returns False when the
        app exposes no genuine AXWindow (locked screen echoes back the app
        element, role AXApplication, which would recurse into the whole menu
        bar). Walking windows, not the whole app, avoids thousands of menu items.
        """
        app_el = AXUIElementCreateApplication(obs.pid)
        windows = [w for w in (_attr(app_el, "AXWindows") or [])
                   if _attr(w, "AXRole") == "AXWindow"]
        for w in windows:
            start = len(obs.lines)
            obs.walk(w)
            # Mark the frontmost window so a selector can mean "the one the
            # person is looking at". Two open documents expose two identical
            # text areas, and without this every such recipe reads as ambiguous.
            if len(obs.lines) > start and (_attr(w, "AXMain") is True
                                           or _attr(w, "AXFocused") is True):
                obs.lines[start] += " [focused]"
        return bool(windows)

    # ------------------------------------------------------------ menu commands
    #
    # A large share of everyday commands (save, copy, new tab, undo) live in the
    # menu bar and nowhere else, so a driver that only walks windows cannot
    # express them. Menus are NOT walked into the observation tree: an app's full
    # menu bar is thousands of items, and paying that on every observe would make
    # the cheap path expensive. Instead a menu command is addressed by its
    # user-visible title path, resolved against the live menu bar at act time.
    #
    # Titles are the right key here — an AXIdentifier on a menu item is usually
    # absent, whereas the title is the same string the user reads aloud. Because
    # titles are localized, each path element may be a list of alternatives.

    @staticmethod
    def _menu_children(el):
        """Children of a menu node, descending through the AXMenu wrapper.

        A menu bar item holds an AXMenu child which holds the actual items, and
        a submenu repeats that nesting; flattening it here keeps the caller's
        path free of structural noise the user never sees.
        """
        kids = _attr(el, "AXChildren") or []
        menus = [c for c in kids if _attr(c, "AXRole") == "AXMenu"]
        if menus:
            return _attr(menus[0], "AXChildren") or []
        return kids

    def _menu_item(self, pid: int, path):
        """Resolve a menu command by title path, e.g. ["Format", "Font", "Bold"].

        Each element may be a string or a list of acceptable titles, so one
        recipe survives a localized menu ("Format" / "서식").
        """
        if not path:
            raise ValueError("menu path is empty")
        app_el = AXUIElementCreateApplication(pid)
        node = _attr(app_el, "AXMenuBar")
        if node is None:
            raise RuntimeError("앱이 메뉴 막대를 노출하지 않습니다")
        walked: list[str] = []
        for want in path:
            wants = [want] if isinstance(want, str) else list(want)
            children = self._menu_children(node)
            hit = None
            for c in children:
                title = _attr(c, "AXTitle")
                if title in wants:
                    hit = c
                    break
            if hit is None:
                have = [t for t in (_attr(c, "AXTitle") for c in children) if t]
                where = " > ".join(walked) or "메뉴 막대"
                raise RuntimeError(
                    f"{where} 에 {wants!r} 메뉴가 없습니다. 있는 항목: {have[:25]}")
            walked.append(_attr(hit, "AXTitle") or wants[0])
            node = hit
        return node, walked

    def _activate(self, pid: int, timeout_s: float = 2.0) -> bool:
        """Bring an app forward, and wait until it really is forward.

        Menu commands are validated against the ACTIVE application: with TextEdit
        in the background, `Edit > Select All` reports itself disabled even
        though nothing is wrong with it. Element presses need no such thing,
        which is why only menu steps pay this cost — activation takes the user's
        focus, so it is not something to do on every action.

        Raised through AXFrontmost rather than NSRunningApplication.activate:
        macOS may decline to let a background process change which app is
        active, and this runs from one. Setting frontmost is the
        assistive-technology path, and asking the same API whether it worked
        keeps the wait from disagreeing with the request.
        """
        app_el = AXUIElementCreateApplication(pid)
        if _attr(app_el, "AXFrontmost") is True:
            return True
        AXUIElementSetAttributeValue(app_el, "AXFrontmost", True)
        running = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if running is not None:
            running.activateWithOptions_(1 << 1)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if _attr(app_el, "AXFrontmost") is True:
                return True
            self._pump(0.05)
        return False

    @staticmethod
    def _modal_sheet(pid: int) -> bool:
        """Is a sheet or modal dialog waiting for an answer in this app?"""
        app_el = AXUIElementCreateApplication(pid)
        for w in (_attr(app_el, "AXWindows") or []):
            if _attr(w, "AXSheets") or _attr(w, "AXModal") is True:
                return True
        return False

    def menu_state(self, target: str, path) -> dict:
        """Report a menu command's live state without invoking it.

        Lets a recipe check "is Bold already on?" (AXMenuItemMarkChar) or "is
        Save available?" (AXEnabled) before deciding to act.
        """
        pid = self._resolve(target)
        if pid is None:
            return {"type": "error", "error": "target_not_found", "target": target}
        try:
            item, walked = self._menu_item(pid, path)
        except Exception as e:
            return {"type": "error", "error": "menu_not_found", "detail": str(e)}
        return {"type": "menu_state", "path": walked,
                "enabled": _attr(item, "AXEnabled") in (None, True),
                "mark": _attr(item, "AXMenuItemMarkChar"),
                "shortcut": _attr(item, "AXMenuItemCmdChar")}

    def identity(self, target: str) -> str:
        """Stable identity namespace for contract bindings: the bundle id."""
        return target

    def observe(self, target: str, mode: str = "tree_diff",
                require_window: bool = True):
        if mode not in ("tree_diff", "tree_full"):
            return {"type": "error", "error": "not_implemented_p0",
                    "detail": f"mode {mode!r} (screenshot lands in P1)"}
        # D5: auto-wait — settle after the most recent action.
        settle = self._last_action_at + AUTO_WAIT_BASE_S - time.time()
        waited_ms = 0
        if settle > 0:
            time.sleep(settle)
            waited_ms = int(settle * 1000)

        pid = self._resolve(target)
        if pid is None:
            # Distinguish "not running" from "this process cannot see the GUI
            # session at all". NSWorkspace needs no Accessibility permission, but
            # it DOES need a connection to the window server: a tmux/screen server
            # started outside the Aqua login session (or an ssh/launchd context)
            # returns an empty app list, so a running app looks absent. Reporting
            # "app not running" there is misleading (real user report).
            gui = [a for a in NSWorkspace.sharedWorkspace().runningApplications()
                   if a.activationPolicy() == 0]
            if not gui:
                return {"type": "error", "error": "gui_session_unavailable",
                        "target": target,
                        "detail": "이 프로세스가 화면(GUI) 세션에 연결돼 있지 않아요. "
                                  "tmux/스크린 안에서 실행 중이라면 tmux 밖에서 실행하거나 "
                                  "`reattach-to-user-namespace uv run nlc ...` 로 실행해 주세요."}
            return {"type": "error", "error": "target_not_found", "target": target,
                    "detail": f"'{target}' 앱을 찾지 못했어요. 실행 중인 앱: "
                              + ", ".join(sorted(a.bundleIdentifier() or a.localizedName()
                                                 for a in gui)[:12])}

        obs = Observation(target, pid)
        has_windows = self._walk_windows(obs)
        if not has_windows and require_window:
            # No window content. On a locked screen only the menu bar is
            # reachable, so this is the graceful analog of Codex's
            # cgWindowNotFound — report it clearly instead of dumping menus.
            # Keep no_window_content independent of lock (codex point D), but
            # attach the live lock state so callers can decide hand-off vs retry.
            from .session import lock_state
            return {"type": "error", "error": "no_window_content", "target": target,
                    "lock": lock_state(),
                    "detail": "app exposes no AXWindows (locked screen, hidden, "
                              "or minimized). Unlock/raise the app and re-observe."}
        if not has_windows:
            # Menus belong to the application, not to a window, so a recipe whose
            # first move is "File > New" must be able to start from an app with
            # nothing open — which is exactly how Finder and Mail usually sit.
            # Registering the empty observation gives that step a valid obs_id to
            # cite without pretending window content exists.
            self._latest[target.lower()] = obs
            return {"type": "observation", "obs_id": obs.obs_id, "target": target,
                    "mode": mode, "tree": "(창 없음 — 메뉴만 사용 가능)",
                    "element_count": 0, "waited_ms": waited_ms}
        if not obs.elements:
            return {"type": "error", "error": "empty_tree", "target": target,
                    "detail": "window present but no AX elements (AX-opaque app?)"}

        prev = self._latest.get(target.lower())
        self._latest[target.lower()] = obs

        if obs.truncated:
            # Say so in the tree itself: a selector that finds nothing here has
            # a different meaning from one that searched the whole window, and
            # the caller has to be able to tell those apart.
            obs.lines.append(TRUNCATION_MARK)
        tree = "\n".join(obs.lines)
        if mode == "tree_diff" and prev is not None:
            diff = list(difflib.unified_diff(prev.lines, obs.lines,
                                             lineterm="", n=1))
            if not diff:
                tree = "(no change)"
            elif len(diff) < len(obs.lines):
                tree = "\n".join(diff[3:])  # drop ---/+++/@@ header noise
        return {"type": "observation", "obs_id": obs.obs_id, "target": target,
                "mode": mode, "tree": tree, "element_count": len(obs.elements),
                "truncated": obs.truncated, "waited_ms": waited_ms}

    def wait_until(self, target: str, predicate, timeout_ms=4000, poll_ms=250):
        """Re-observe on a monotonic deadline until predicate(full_tree) is true.

        Replaces blind fixed sleeps: the dominant CU failure mode is the action
        loop waiting a wrong fixed time. `predicate` receives the full tree text
        and returns bool. Returns the final observation dict (observation on
        success, or {'type':'timeout', ...} with the last tree).
        """
        deadline = time.monotonic() + timeout_ms / 1000
        last = None
        while True:
            obs = self.observe(target, "tree_full")
            last = obs
            if obs["type"] == "observation" and predicate(obs["tree"]):
                return obs
            if time.monotonic() >= deadline:
                return {"type": "timeout", "target": target, "waited_ms": timeout_ms,
                        "last_obs": last}
            time.sleep(poll_ms / 1000)

    # ------------------------------------------------------------ act

    def act(self, obs_id: str, target: str, action: dict,
            risk_class: str = "none", intent_text: str = "",
            approved: bool = False):
        # D6 gate. handoff -> user does it. confirm AND preapproved both require
        # approval at the driver: the driver cannot see the user's original
        # request, so it cannot itself decide a "preapproved" action was in scope
        # — the caller that HAS that context passes approved=True. Failing closed
        # here (not silently running preapproved) was a review finding.
        if risk_class == "handoff":
            return {"type": "handoff_request", "reason": intent_text or "user_action_required"}
        if risk_class in ("confirm", "preapproved") and not approved:
            return {"type": "confirm_request", "what": intent_text, "risk_class": risk_class,
                    "detail": "resend with approved=true after user consent "
                              "(preapproved needs the original request to have named it)"}

        # D3: index freshness — action must cite the newest observation.
        obs = self._latest.get(target.lower())
        if obs is None or obs.obs_id != obs_id:
            return {"type": "error", "error": "stale_observation",
                    "detail": "re-observe and re-derive element_index "
                              f"(latest is {obs.obs_id if obs else 'none'})"}

        name = action.get("name")
        try:
            result = self._perform(obs, action)
        except Exception as e:  # surface, never swallow
            return {"type": "error", "error": "action_failed",
                    "action": name, "detail": str(e)}
        self._last_action_at = time.time()
        return {"type": "acted", "action": name, "obs_id": obs_id,
                "note": "not verified — observe again and check the result (D7)",
                **(result or {})}

    def _element(self, obs, action):
        idx = action.get("element_index")
        if idx is None or not (0 <= idx < len(obs.elements)):
            raise ValueError(f"element_index {idx} out of range 0..{len(obs.elements)-1}")
        return obs.elements[idx]

    def _perform(self, obs, action):
        name = action["name"]
        if name == "click":
            el = self._element(obs, action)
            err = AXUIElementPerformAction(el, "AXPress")
            if err != 0:  # coordinate fallback per D3
                frame = _elem_frame(el)
                if frame is None:
                    raise RuntimeError(f"AXPress failed ({err}) and no frame for fallback")
                self._mouse_click(frame[0] + frame[2] / 2, frame[1] + frame[3] / 2, obs.pid)
                return {"fallback": "coordinate_click"}
        elif name == "set_value":
            el = self._element(obs, action)
            err = AXUIElementSetAttributeValue(el, "AXValue", action["value"])
            if err != 0:
                raise RuntimeError(f"AXValue set failed ({err})")
        elif name == "type_text":
            self._type_text(action["text"], obs.pid)
        elif name == "press_key":
            self._press_key(action["key"], obs.pid)
        elif name == "scroll":
            el = self._element(obs, action)
            frame = _elem_frame(el)
            if frame is None:
                raise RuntimeError("no frame for scroll target")
            self._scroll_at(frame[0] + frame[2] / 2, frame[1] + frame[3] / 2,
                            action.get("direction", "down"), action.get("pages", 1),
                            obs.pid)
        elif name == "read":
            # Look, change nothing. A recipe that must carry a value from one
            # part of the screen to another had no way to say "now read this":
            # every step had to act, so reading was only ever a side effect of
            # doing something else.
            return None
        elif name == "menu":
            # An app validates its menus only while it is the active one, so
            # this is a precondition for reading `enabled` correctly, not a
            # cosmetic raise-to-front.
            if not self._activate(obs.pid):
                raise RuntimeError("앱을 앞으로 가져오지 못해 메뉴를 쓸 수 없습니다")
            item, walked = self._menu_item(obs.pid, action["path"])
            # Menu items are validated lazily by the app, so a disabled item is
            # a real precondition failure worth reporting by name rather than a
            # silent no-op press.
            if _attr(item, "AXEnabled") is False:
                # A modal sheet disables the whole menu bar, and "this menu is
                # disabled" is a dead end when the real answer is "a dialog is
                # waiting". An earlier run that stopped mid-flow leaves exactly
                # that state behind, so this is the common case, not an exotic one.
                if self._modal_sheet(obs.pid):
                    raise RuntimeError(
                        f"앱에 열린 대화상자가 있어 '{' > '.join(walked)}' 메뉴를 "
                        f"쓸 수 없습니다. 대화상자를 닫고 다시 시도해 주세요")
                raise RuntimeError(f"메뉴 '{' > '.join(walked)}' 가 지금은 비활성 상태입니다")
            err = AXUIElementPerformAction(item, "AXPress")
            if err != 0:
                raise RuntimeError(f"메뉴 '{' > '.join(walked)}' 실행 실패 ({err})")
            # Observed on this machine: pressing Format > Font > Bold with no
            # selection returns 0 and changes nothing. Success here means the
            # press was delivered, never that the command took effect — which is
            # why replay requires a postcondition on menu steps.
            return {"menu_path": walked}
        elif name == "secondary_action":
            el = self._element(obs, action)
            err = AXUIElementPerformAction(el, action["action"])
            if err != 0:
                raise RuntimeError(f"secondary action {action['action']!r} failed ({err})")
        else:
            raise ValueError(f"unknown action {name!r}")

    # ------------------------------------------------------------ input synthesis
    #
    # CGEvent input is delivered to the TARGET pid via CGEventPostToPid, not the
    # global HID tap — otherwise a background target would receive keystrokes
    # into whatever app currently holds focus (a correctness + safety bug caught
    # in review). NOTE: posting to a pid routes to the process, not necessarily
    # to a specific window/first-responder; it does not guarantee activation.
    # Callers should verify the effect with a postcondition rather than assume it.

    def _post(self, ev, pid):
        if pid is None:
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
        elif hasattr(Quartz, "CGEventPostToPid"):
            Quartz.CGEventPostToPid(pid, ev)
        else:
            # Fail closed: do NOT silently fall back to the global tap, which is
            # exactly the wrong-app delivery bug this method exists to prevent.
            raise RuntimeError("CGEventPostToPid unavailable; refusing global "
                               "input post to avoid wrong-app delivery")

    def _mouse_click(self, x, y, pid=None):
        """Click at a screen point, for controls that expose no AX action.

        Delivered through the system event tap rather than to a pid, because a
        mouse event is routed by WHERE it is: posted to a process, it arrives
        with nothing to tell the app which view was hit, and a sidebar row
        simply never selects. The location is the targeting, which is why this
        does not reintroduce the wrong-app hazard that pid-posting exists to
        prevent for keystrokes — those carry no location at all.

        The app is raised first so the point really belongs to the window the
        frame was read from, and the pointer is put back where the person left
        it.
        """
        if pid is not None:
            self._activate(pid)
        with _INPUT_LOCK:
            where = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
            for etype in (Quartz.kCGEventMouseMoved,
                          Quartz.kCGEventLeftMouseDown,
                          Quartz.kCGEventLeftMouseUp):
                ev = Quartz.CGEventCreateMouseEvent(_event_source(), etype, (x, y),
                                                    Quartz.kCGMouseButtonLeft)
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
                time.sleep(0.03)
            back = Quartz.CGEventCreateMouseEvent(
                _event_source(), Quartz.kCGEventMouseMoved, where,
                Quartz.kCGMouseButtonLeft)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, back)

    def _type_text(self, text, pid=None):
        with _INPUT_LOCK:
            for ch in text:
                for down in (True, False):
                    ev = Quartz.CGEventCreateKeyboardEvent(_event_source(), 0, down)
                    Quartz.CGEventKeyboardSetUnicodeString(ev, len(ch), ch)
                    self._post(ev, pid)
                time.sleep(0.01)

    _KEYCODES = {"return": 36, "tab": 48, "space": 49, "delete": 51,
                 "escape": 53, "left": 123, "right": 124, "down": 125, "up": 126,
                 **{c: k for c, k in zip("abcdefghijklmnopqrstuvwxyz",
                    [0, 11, 8, 2, 14, 3, 5, 4, 34, 38, 40, 37, 46, 45, 31, 35,
                     12, 15, 1, 17, 32, 9, 13, 7, 16, 6])}}
    _MODS = {"cmd": Quartz.kCGEventFlagMaskCommand, "super": Quartz.kCGEventFlagMaskCommand,
             "shift": Quartz.kCGEventFlagMaskShift, "alt": Quartz.kCGEventFlagMaskAlternate,
             "ctrl": Quartz.kCGEventFlagMaskControl}

    def _press_key(self, key, pid=None):
        """xdotool-style: 'Return', 'cmd+c', 'Escape' (subset for P0)."""
        parts = key.lower().split("+")
        mods, base = parts[:-1], parts[-1]
        code = self._KEYCODES.get(base)
        if code is None:
            raise ValueError(f"unsupported key {key!r} in P0")
        flags = 0
        for m in mods:
            flags |= self._MODS[m]
        with _INPUT_LOCK:
            for down in (True, False):
                ev = Quartz.CGEventCreateKeyboardEvent(_event_source(), code, down)
                if flags:
                    Quartz.CGEventSetFlags(ev, flags)
                self._post(ev, pid)

    def _scroll_at(self, x, y, direction, pages, pid=None):
        with _INPUT_LOCK:
            move = Quartz.CGEventCreateMouseEvent(_event_source(), Quartz.kCGEventMouseMoved,
                                                  (x, y), 0)
            self._post(move, pid)
            amount = int(10 * pages) * (1 if direction in ("up", "left") else -1)
            vertical = direction in ("up", "down")
            ev = Quartz.CGEventCreateScrollWheelEvent(
                _event_source(), Quartz.kCGScrollEventUnitLine, 2 if not vertical else 1,
                amount if vertical else 0, amount if not vertical else 0)
            self._post(ev, pid)

    # ------------------------------------------------------------ L2 dispatch

    def handle(self, msg: dict) -> dict:
        """Single L2 message in, single L2 message out."""
        mtype = msg.get("type")
        if mtype == "list_targets":
            return self.list_targets()
        if mtype == "observe":
            return self.observe(msg["target"], msg.get("mode", "tree_diff"))
        if mtype == "act":
            return self.act(msg["obs_id"], msg["target"], msg["action"],
                            msg.get("risk_class", "none"),
                            msg.get("intent_text", ""),
                            msg.get("approved", False))
        if mtype == "act_batch":
            # A batch reuses one obs_id, so raw element_index values only stay
            # valid while the tree is unchanged. After each action we check the
            # tree: if it changed, the remaining actions' indices are stale, so
            # we stop rather than click the wrong element (codex guidance — never
            # reuse the first observation's index list across a mutation).
            results = []
            obs_id = msg["obs_id"]
            target = msg["target"]
            base = self._latest.get(target.lower())
            base_lines = list(base.lines) if base else None
            approvals = msg.get("approvals", {})   # {action_index: True}
            for i, action in enumerate(msg["actions"]):
                r = self.act(obs_id, target, action, msg.get("risk_class", "none"),
                             approved=bool(approvals.get(i) or approvals.get(str(i))))
                results.append(r)
                # Stop on an error OR a safety gate — never keep clicking past a
                # confirm/handoff that wasn't approved (review finding).
                if r["type"] in ("error", "confirm_request", "handoff_request"):
                    break
                # Settle before re-checking: a UI that updates asynchronously
                # (100-500ms later) would otherwise still show the old tree and
                # the guard would wave a stale index through (review finding).
                time.sleep(AUTO_WAIT_BASE_S)
                fresh = Observation(target, base.pid) if base else None
                if fresh is not None:
                    self._walk_windows(fresh)
                    if fresh.lines != base_lines:
                        results.append({"type": "error", "error": "batch_invalidated",
                                        "detail": "tree changed mid-batch; remaining "
                                        "actions need a fresh observe/resolve"})
                        break
            out = {"type": "acted_batch", "results": results}
            then = msg.get("then_observe")
            if then and results and results[-1]["type"] == "acted":
                out["observation"] = self.observe(target, then)
            return out
        return {"type": "error", "error": "unknown_message", "detail": str(mtype)}


def serve_stdio():
    """JSON-lines server: one L2 message per line on stdin, reply on stdout."""
    import json
    import sys
    driver = Driver()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            reply = driver.handle(json.loads(line))
        except Exception as e:
            reply = {"type": "error", "error": "bad_message", "detail": str(e)}
        sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    serve_stdio()
