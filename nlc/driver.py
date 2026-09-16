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

from AppKit import NSWorkspace
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


def _attr(el, name):
    err, value = AXUIElementCopyAttributeValue(el, name, None)
    return value if err == 0 else None


def _elem_label(el):
    """Compact one-line description of an AX element."""
    parts = []
    for name in ("AXTitle", "AXDescription", "AXValue", "AXHelp",
                 "AXIdentifier", "AXPlaceholderValue"):
        v = _attr(el, name)
        if v is None or v == "":
            continue
        key = name[2:]  # strip AX
        parts.append(f"{key}: {v}")
    return ", ".join(str(p) for p in parts)


def _elem_frame(el):
    """(x, y, w, h) in screen coords, or None."""
    pos, size = _attr(el, "AXPosition"), _attr(el, "AXSize")
    if pos is None or size is None:
        return None
    err_p, p = Quartz.AXValueGetValue(pos, Quartz.kAXValueCGPointType, None)
    err_s, s = Quartz.AXValueGetValue(size, Quartz.kAXValueCGSizeType, None)
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

    def walk(self, el, depth=0):
        if depth > MAX_TREE_DEPTH:
            return
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
        for child in children:
            self.walk(child, depth + 1)


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

    def _resolve(self, target: str, launch=True):
        want = target.lower()
        for app in NSWorkspace.sharedWorkspace().runningApplications():
            bid = (app.bundleIdentifier() or "").lower()
            name = (app.localizedName() or "").lower()
            if want in (bid, name):
                return int(app.processIdentifier())
        if launch:
            # Like Codex's get_app_state: launch in background transparently.
            flag = "-b" if "." in target else "-a"
            subprocess.run(["open", "-g", flag, target], check=True)
            deadline = time.time() + 10
            while time.time() < deadline:
                time.sleep(0.3)
                pid = self._resolve(target, launch=False)
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
            obs.walk(w)
        return bool(windows)

    def observe(self, target: str, mode: str = "tree_diff"):
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
            return {"type": "error", "error": "target_not_found", "target": target}

        obs = Observation(target, pid)
        has_windows = self._walk_windows(obs)
        if not has_windows:
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
        if not obs.elements:
            return {"type": "error", "error": "empty_tree", "target": target,
                    "detail": "window present but no AX elements (AX-opaque app?)"}

        prev = self._latest.get(target.lower())
        self._latest[target.lower()] = obs

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
                "waited_ms": waited_ms}

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
        # D6 stub: policy engine is P1, but the gate exists at the schema level.
        if risk_class == "handoff":
            return {"type": "handoff_request", "reason": intent_text or "user_action_required"}
        if risk_class == "confirm" and not approved:
            return {"type": "confirm_request", "what": intent_text,
                    "detail": "resend with approved=true after user consent"}

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
                self._mouse_click(frame[0] + frame[2] / 2, frame[1] + frame[3] / 2)
                return {"fallback": "coordinate_click"}
        elif name == "set_value":
            el = self._element(obs, action)
            err = AXUIElementSetAttributeValue(el, "AXValue", action["value"])
            if err != 0:
                raise RuntimeError(f"AXValue set failed ({err})")
        elif name == "type_text":
            self._type_text(action["text"])
        elif name == "press_key":
            self._press_key(action["key"])
        elif name == "scroll":
            el = self._element(obs, action)
            frame = _elem_frame(el)
            if frame is None:
                raise RuntimeError("no frame for scroll target")
            self._scroll_at(frame[0] + frame[2] / 2, frame[1] + frame[3] / 2,
                            action.get("direction", "down"), action.get("pages", 1))
        elif name == "secondary_action":
            el = self._element(obs, action)
            err = AXUIElementPerformAction(el, action["action"])
            if err != 0:
                raise RuntimeError(f"secondary action {action['action']!r} failed ({err})")
        else:
            raise ValueError(f"unknown action {name!r}")

    # ------------------------------------------------------------ input synthesis

    def _mouse_click(self, x, y):
        with _INPUT_LOCK:   # global focus-dependent input — serialize across flows
            for etype in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
                ev = Quartz.CGEventCreateMouseEvent(None, etype, (x, y),
                                                    Quartz.kCGMouseButtonLeft)
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
                time.sleep(0.03)

    def _type_text(self, text):
        with _INPUT_LOCK:
            for ch in text:
                for down in (True, False):
                    ev = Quartz.CGEventCreateKeyboardEvent(None, 0, down)
                    Quartz.CGEventKeyboardSetUnicodeString(ev, len(ch), ch)
                    Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
                time.sleep(0.01)

    _KEYCODES = {"return": 36, "tab": 48, "space": 49, "delete": 51,
                 "escape": 53, "left": 123, "right": 124, "down": 125, "up": 126,
                 **{c: k for c, k in zip("abcdefghijklmnopqrstuvwxyz",
                    [0, 11, 8, 2, 14, 3, 5, 4, 34, 38, 40, 37, 46, 45, 31, 35,
                     12, 15, 1, 17, 32, 9, 13, 7, 16, 6])}}
    _MODS = {"cmd": Quartz.kCGEventFlagMaskCommand, "super": Quartz.kCGEventFlagMaskCommand,
             "shift": Quartz.kCGEventFlagMaskShift, "alt": Quartz.kCGEventFlagMaskAlternate,
             "ctrl": Quartz.kCGEventFlagMaskControl}

    def _press_key(self, key):
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
                ev = Quartz.CGEventCreateKeyboardEvent(None, code, down)
                if flags:
                    Quartz.CGEventSetFlags(ev, flags)
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)

    def _scroll_at(self, x, y, direction, pages):
      with _INPUT_LOCK:
        move = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved,
                                              (x, y), 0)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, move)
        amount = int(10 * pages) * (1 if direction in ("up", "left") else -1)
        vertical = direction in ("up", "down")
        ev = Quartz.CGEventCreateScrollWheelEvent(
            None, Quartz.kCGScrollEventUnitLine, 2 if not vertical else 1,
            amount if vertical else 0, amount if not vertical else 0)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)

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
            for action in msg["actions"]:
                r = self.act(obs_id, target, action, msg.get("risk_class", "none"))
                results.append(r)
                if r["type"] == "error":
                    break
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
            if then and results and results[-1]["type"] != "error":
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
