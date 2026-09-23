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
import math
import os
import subprocess
import threading
import time
import uuid

# Synthetic input targets a specific process; the lock prevents our callers
# from interleaving key pairs. It does not isolate human input or pin a window's
# first responder. AX actions address elements but may have app-specific focus
# side effects, so background operation still needs independent verification.
_INPUT_LOCK = threading.Lock()


def _aborted_now() -> bool:
    """Has the person stopped this run, as of right now?

    In-process there is no user-stop channel at all: the Esc watcher and the
    on-screen indicator live in the signed helper, because they only mean
    something in a process that holds the permissions and can be seen to hold
    them. So this is always False here — stated rather than left implicit,
    because a reader comparing the two surfaces should see that the in-process
    driver offers the person no way out, which is one more reason the signed
    path is the recommended one.
    """
    return False

from AppKit import NSRunningApplication, NSWorkspace
from CoreFoundation import CFEqual, CFGetTypeID
from Foundation import NSArray
from ApplicationServices import (
    AXIsProcessTrusted,
    AXUIElementGetTypeID,
    AXUIElementCreateApplication,
    AXUIElementCopyAttributeValue,
    AXUIElementIsAttributeSettable,
    AXUIElementSetMessagingTimeout,
    AXUIElementGetAttributeValueCount,
    AXUIElementCopyAttributeValues,
    AXUIElementCopyActionNames,
    AXUIElementPerformAction,
    AXUIElementSetAttributeValue,
)

# There is no CGEvent source here any more, and no marker to tag one with.
# Synthetic input used to be stamped with NLC_EVENT_MARKER so a cooperating app
# or an audit tool could tell our events apart from a person's — the best
# public signal available, because macOS has no "this window is being
# automated" flag. Nothing is synthesised now: every action addresses the
# element it names through the accessibility API, so there is no event for
# anyone to mistake for a human's.

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
# kAXErrorCannotComplete. Apple documents it as possible when an action's
# callback times out during modal processing, WITHOUT meaning the action failed.
AX_CANNOT_COMPLETE = -25204
# The one AX error that says the element does not implement this action at all,
# so nothing can have happened and a different action may be tried. Every other
# failure leaves open the possibility that something did.
AX_ACTION_UNSUPPORTED = -25206
# How many items a popup may offer before this driver stops claiming it can
# report them. The choice is checked against the FULL option set, so a list we
# only partly read could hide the very change the check exists to catch.
MAX_MENU_OPTIONS = 200

class _AfterOpening(RuntimeError):
    """A refusal that came after the menu was already on screen.

    The selection did not happen, but the screen is not where it was — so what
    the caller is left with depends on whether the menu closed again. Either
    way the caller has to look again before it acts on anything it observed
    before this: a menu opened and closed over the screen it was holding.
    """

    def __init__(self, message: str, effect: str, **facts):
        super().__init__(message)
        self.effect = effect
        # What the caller is actually left with. `effect` says whether the
        # intended thing happened; these say what is on screen and whether the
        # pin survived, which is what decides if a corrected retry is possible.
        self.facts = facts


class _NeedsForeground(RuntimeError):
    """Needs the app in front, so it was not done.

    Measured, 2026-09-20: a menu command wired through the responder chain does
    not run in a background app. `AXUIElementPerformAction` returns 0 and
    nothing happens, because the command needs a key window and AppKit will not
    give one to an inactive app — `NSWindow.makeKey()` was called on an owned
    fixture and `isKeyWindow` stayed false.

    What used to sit here instead was a synthetic `NSEventTypeAppKitDefined`
    (subtype 1, ApplicationActivated) posted to the target, which makes the app
    BELIEVE it is active while the window server disagrees. It worked, and it
    leaked: the owned fixture ended every run still reporting `isActive=true`,
    and a real TextEdit was found reporting `AXFrontmost=true` for six hours
    while the person was working in another app. An acquire with no release is
    a lie the next caller then reads as truth — our own activation gate skipped
    activation because of it, and the click that followed silently did nothing.

    So the app is never told it is active. Callers that need the front say so
    here, and the layer that can ask a person decides.
    """


class _NeedsPointer(RuntimeError):
    """Could not be done without moving the person's pointer, so it was not done.

    Distinct from a plain refusal because there IS a way — it just costs the
    person their pointer and their front app, which is not a price this driver
    pays on its own. The layer that can ask a person gets to decide.
    """


class NotStarted(RuntimeError):
    """A refusal that provably precedes any effect.

    The engine treats an ordinary failure as "the action may or may not have
    happened", because a thrown exception cannot say how far it got. That is the
    safe default, but it is also wrong for the common cases — a menu that is
    disabled, an app that would not come forward, an index out of range — where
    nothing was dispatched at all. Those raise this instead, and only those may
    be retried without a witness.
    """


def _line_attr(line: str, key: str):
    """One attribute out of a tree line, without importing the flow layer."""
    import re as _re
    others = "|".join(k for k in ("Title", "Description", "Value", "Help",
                                  "Identifier", "Placeholder") if k != key)
    m = _re.search(rf"{key}:\s*(.+?)"
                   rf"(?:,\s*(?:{others}):|\s*\[(?:disabled|focused)\]|$)", line)
    return m.group(1).strip() if m else None


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
        if not isinstance(v, (str, int, float)) or (v == "" and name != "AXValue"):
            continue
        if isinstance(v, str):
            v = _one_line(v)
        parts.append(f"{name[2:]}: {v}")   # strip the AX prefix
    return ", ".join(parts)


# Every separator `str.splitlines` breaks on. A value that carried one used to
# end its element's line early and start a new "line" at indent 0 — a Calendar
# event's multi-line location did exactly that, and every selector scoped to
# the focused window then climbed to that fragment, took it for the window, and
# found nothing.
_LINE_BREAKS = {ord(c): "\\n" for c in "\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029"}


def _one_line(text: str) -> str:
    """A label as it must appear in the tree: on one line."""
    return text.replace("\r\n", "\n").translate(_LINE_BREAKS)


def _real_windows(pid):
    """The app's windows, with the things that are not windows taken out.

    `AXWindows` is not a list of windows. Two measured intruders:

      * an `AXHelpTag` — a tooltip — 85x20 at index 0 on Calculator, so a
        `window_index` meant different things depending on where the mouse
        happened to be resting;
      * macOS's screen-capture badge, which it puts ON the window being
        captured and which then appears in that app's own window list: 66x20
        inside a 460x232 window, subrole AXDialog, title "Window", no close,
        minimize or zoom button (2026-09-21). Holding the control indicator is
        what puts it there, so saying "NLC is working on this window" was what
        made that window ambiguous.

    The second rule is about what the thing IS, not what it is called: sitting
    entirely inside another of this app's windows with no way to close it makes
    it an attachment to that window, not a window. A real dialog that overlaps
    its parent still has a close button; a standalone one is contained by
    nothing.
    """
    windows = [w for w in (_attr(AXUIElementCreateApplication(pid), "AXWindows") or [])
               if _attr(w, "AXRole") == "AXWindow"]
    frames = [_elem_frame(w) for w in windows]

    def contains(outer, inner):
        if outer is None or inner is None or outer == inner:
            return False
        ox, oy, ow, oh = outer
        ix, iy, iw, ih = inner
        return (ox <= ix and oy <= iy
                and ix + iw <= ox + ow and iy + ih <= oy + oh)

    keep = []
    for i, window in enumerate(windows):
        if _attr(window, "AXCloseButton") is None and any(
                contains(frames[j], frames[i]) for j in range(len(windows)) if j != i):
            continue
        keep.append(window)
    return keep


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


class _OpenSelect:
    """What one `open_select` pinned, so the choice can be checked against it.

    Everything here is captured at the moment the menu came up, and every field
    is something the later choice must still find true. The two identities are
    the point: `control` is the CF identity of the popup that was pressed, and
    `menu` the CF identity of the menu THAT press produced. Without both, a
    choice could land in a menu some other control opened, or press an item in
    our menu on behalf of a control that has since been replaced.
    """
    __slots__ = ("obs_id", "pid", "index", "control", "identifier", "role",
                 "label", "menu", "options", "options_obs_id")

    def __init__(self, obs_id, pid, index, control, identifier, role, label, menu):
        self.obs_id = obs_id
        # Which app this menu belongs to. Without it, observing a DIFFERENT
        # app would look for our menu among that app's menus, fail to find it,
        # and throw away a menu that is still standing in the first one (peer
        # review).
        self.pid = pid
        self.index = index
        self.control = control
        self.identifier = identifier
        self.role = role
        self.label = label
        self.menu = menu
        # Filled in by the observation that reports this menu — never here.
        # A choice may only be made from a list the caller was actually shown.
        self.options: list | None = None
        self.options_obs_id: str | None = None


class Observation:
    def __init__(self, target: str, pid: int):
        self.obs_id = f"o{uuid.uuid4().hex[:8]}"
        self.target = target
        self.pid = pid
        self.elements: list = []   # index -> AXUIElement
        self.lines: list[str] = []
        self.created_at = time.time()
        # A PID alone can be reused after an app restarts. Coordinate fallback
        # must be tied to the process that supplied this observation.
        self.process_launch_identity = None
        try:
            from .native_process_identity import process_start_identity
            self.process_launch_identity = process_start_identity(pid)
        except Exception:
            pass  # AX actions remain available; coordinate fallback will refuse.
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
            extra = _secondary_actions(el)
            actions = (", Secondary Actions: "
                       + ", ".join(a[2:] for a in extra)) if extra else ""
            self.lines.append(f"{indent}{idx} {role[2:] if role.startswith('AX') else role}"
                              + (f"  {label}" if label else "") + disabled + actions)
        children = _attr(el, "AXChildren") or []
        for child in children[:MAX_CHILDREN_PER_NODE]:
            self.walk(child, depth + 1)
        if len(children) > MAX_CHILDREN_PER_NODE:
            self.truncated = True
            self.lines.append("  " * (depth + 1)
                              + f"… ({len(children) - MAX_CHILDREN_PER_NODE}개 더 있음)")


# What `secondary_action` will actually carry. An AX action name goes straight
# to AXUIElementPerformAction, so the launcher enumerates what may cross, and
# an observation that advertised anything else would be describing a refusal.
SECONDARY_ACTIONS = ("AXShowMenu", "AXIncrement", "AXDecrement",
                     "AXConfirm", "AXCancel")


def _secondary_actions(el):
    """The actions this element offers beyond being pressed.

    Nothing said which elements accept a `secondary_action`, so a caller could
    only find out by trying one and reading the refusal — the capability was
    announced and unusable. Press is left out because it is what `click`
    already means; what is here is what is otherwise invisible.
    """
    from ApplicationServices import AXUIElementCopyActionNames
    try:
        error, names = AXUIElementCopyActionNames(el, None)
    except Exception:
        return ()
    if error != 0 or not names:
        return ()
    return tuple(n for n in SECONDARY_ACTIONS if n in names)


class Driver:
    """Persistent L2 endpoint. One instance per session (obs registry lives here)."""

    # What this surface honours, in the same vocabulary the signed helper
    # announces over `ping` and the browser declares on its class. Three ways
    # to reach a screen, one list of promises.
    #
    # It has to be declared even though this driver IS the implementation: a
    # caller asks the surface what it can do, and a surface that stays silent
    # reads as one that cannot — which is how `press_key` came to exist here,
    # behave correctly, and still not appear in what an in-process run was
    # offered (peer finding). Silence is not a capability.
    #
    # Kept in step with `helperCapabilities` in helper/NLCHelper.swift.
    capabilities = frozenset({
        "observe", "act", "probe", "selection",
        "evidence.document_text", "evidence.visual",
        "expect.role", "expect.label", "expect.attribute", "expect.enabled",
        "expect.no_modal", "expect.selection",
        "effect",
        "action.click", "action.set_value", "action.type_text", "action.read",
        "action.menu", "action.scroll", "action.secondary_action",
        "action.select_option",
        "select_option.label_only",
        # The two-step form, for a caller that does not already know what the
        # popup contains: open it, look at the observation that follows, then
        # choose from THAT. Same words the browser backend uses.
        "action.open_select", "action.select_custom_option", "action.prepare_window", "menu_state", "menu_items",
        "custom_select.label_only",
        # Said separately because the browser's `press_key` includes Escape and
        # this one does not.
        # The observation names which elements accept a secondary action.
        # Announcing `action.secondary_action` while saying nothing about where
        # it applies left a caller to find out by trying one and reading the
        # refusal — a capability that was advertised and unusable.
        "observe.secondary_actions",
        # A coordinate can be an address: `capture` reports a viewport in
        # window points, and a point in that space resolves to the real
        # element under it — which is how a control that draws itself and
        # exposes no AX element of its own becomes reachable.
        "action.click.by_point",
        # No press_key capability is announced. Keys are not synthesised at
        # all now, so naming one would be advertising a refusal — and a
        # caller reading "navigation_only" plans around a Tab that never
        # arrives. Silence is not a capability, and neither is a promise
        # kept by an error message.
    })

    def __init__(self):
        if not AXIsProcessTrusted():
            raise RuntimeError(
                "accessibility_permission_missing: grant this process "
                "Accessibility permission in System Settings")
        self._latest: dict[str, Observation] = {}   # target -> newest obs
        self._last_action_at = 0.0
        # Showing that this is happening belongs to whatever touches the
        # screen, not to whoever remembered to wrap the call. It used to be
        # wired at two CLI commands and two helper messages, so every other
        # way in — a script holding a Driver, `nlc observe`, the whole
        # evaluation suite — drove somebody's machine with nothing on screen
        # to say so (found 2026-09-20 while verifying browsers).
        self._indicator = None
        self._indicator_pid = None
        self._indicator_beat = None
        self._indicate = os.environ.get("NLC_NO_INDICATOR") != "1"
        # The one menu this driver opened and has not yet spent, if any. It is
        # a registry of ONE: a second open would leave a menu standing that
        # nothing is tracking, and the caller would have no way to say which of
        # them it meant.
        self._select: _OpenSelect | None = None
        # A menu this driver opened, already spent on a selection, that did not
        # close itself. We no longer act on it, but it is still on the person's
        # screen — and an observation that called the control "closed" would be
        # telling the next step a menu is out of the way when it is not.
        self._spent_menu: tuple | None = None

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
    def _running_apps():
        """Every running application, as one overridable call.

        A seam, not an abstraction: name resolution has to prefer the app a
        person can see over a background extension that shares its name, and
        that rule can only be tested when both are running. Being able to hand
        in a pair makes the check deterministic instead of "whenever the machine
        happens to have a collision".
        """
        return list(NSWorkspace.sharedWorkspace().runningApplications())

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
        for app in self._running_apps():
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
        # The same list `capture` and point-addressing use, and for the same
        # reason: macOS's screen-capture badge is a window in the app's own
        # AXWindows, so while the control indicator was up every observation of
        # the app being driven began with a phantom `Window  Title: Window,
        # Identifier: _NS:8` — our own way of saying "NLC is working here"
        # walked into the tree it was working on, and shifted every element
        # index behind it.
        windows = _real_windows(obs.pid)
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
    def _menu_children(el, read=None):
        """Children of a menu node, descending through the AXMenu wrapper.

        A menu bar item holds an AXMenu child which holds the actual items, and
        a submenu repeats that nesting; flattening it here keeps the caller's
        path free of structural noise the user never sees.
        """
        read = read or _attr
        kids = read(el, "AXChildren") or []
        menu = next((c for c in kids if read(c, "AXRole") == "AXMenu"), None)
        if menu is not None:
            return read(menu, "AXChildren") or []
        return kids

    def _menu_item(self, pid: int, path, read=None):
        """Resolve a menu command by title path, e.g. ["Format", "Font", "Bold"].

        Each element may be a string or a list of acceptable titles, so one
        recipe survives a localized menu ("Format" / "서식").
        """
        read = read or _attr
        if not path:
            raise ValueError("menu path is empty")
        app_el = AXUIElementCreateApplication(pid)
        node = read(app_el, "AXMenuBar")
        if node is None:
            raise RuntimeError("앱이 메뉴 막대를 노출하지 않습니다")
        walked: list[str] = []
        for want in path:
            wants = [want] if isinstance(want, str) else list(want)
            children = self._menu_children(node, read=read)
            hit = None
            have = []
            hit_title = None
            for c in children:
                title = read(c, "AXTitle")
                if title and len(have) < 25:
                    have.append(title)
                if title in wants:
                    if hit is not None:
                        raise RuntimeError("메뉴 경로가 여러 항목과 일치합니다")
                    hit_title = title
                    hit = c
            if hit is None:
                where = " > ".join(walked) or "메뉴 막대"
                raise RuntimeError(
                    f"{where} 에 {wants!r} 메뉴가 없습니다. 있는 항목: {have[:25]}")
            walked.append(hit_title)
            node = hit
        return node, walked

    @staticmethod
    def _frontmost(pid: int) -> bool:
        """Is this app the one the person is working in? A question, not a request.

        What used to live here raised the app: an `AXFrontmost` write plus
        `NSRunningApplication.activate()`. Both are gone, and the reason is the
        whole point of this change — every caller of it took the person's focus
        as a side effect of an action they asked for somewhere else, and a
        switch to turn that off would still leave the path sitting there for
        the next caller to reach for.

        So there is no raise-the-app path in this driver any more. Callers that
        genuinely need the app in front now say so in their refusal, and the
        layer that can ask a person decides what to do about it.
        """
        # NOT `AXFrontmost`. An app answers that question itself, and a
        # background TextEdit was measured answering "yes" for six hours
        # (2026-09-20). Two apps reported it at once. The window server owns
        # this fact, so ask the window server.
        from AppKit import NSWorkspace
        front = NSWorkspace.sharedWorkspace().frontmostApplication()
        return front is not None and int(front.processIdentifier()) == pid

    @staticmethod
    def _blocking_hint(obs) -> str:
        """What the window is showing, when a menu says it cannot be used.

        An app that has just been updated greets you with a what's-new screen
        that disables everything behind it — not a sheet, so nothing flags it,
        and "this menu is disabled" leaves the person with nowhere to go. Naming
        the button that IS live tells them what is in the way.
        """
        live = []
        for line in obs.lines:
            if "[disabled]" in line or " Button" not in line:
                continue
            for key in ("Title", "Description"):
                v = _line_attr(line, key)
                if v:
                    live.append(v)
                    break
            if len(live) >= 5:
                break
        return f" 지금 화면에서 누를 수 있는 것: {', '.join(live)}" if live else ""

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
        return self._read_menu(target, path)

    def menu_items(self, target: str, path=None) -> dict:
        """Read immediate menu children without preparing or activating an app."""
        return self._read_menu(target, [] if path is None else path, listing=True)

    def _read_menu(self, target, path, *, listing=False):
        def valid_title(title):
            return (isinstance(title, str) and 0 < len(title) <= 200
                    and all(ord(c) >= 32 and ord(c) != 127 for c in title))
        if (not isinstance(path, list) or not (0 if listing else 1) <= len(path) <= 6
                or any(not (valid_title(level) or
                    isinstance(level, list) and 1 <= len(level) <= 8
                    and all(valid_title(title) for title in level)) for level in path)):
            return {"type": "error", "error": "invalid_menu_path", "effect": "not_started"}
        from .model_budget import remaining_model_seconds
        started = time.monotonic()
        try:
            deadline = started + remaining_model_seconds(5)
        except TimeoutError:
            return {"type": "error", "error": "menu_read_timeout", "effect": "not_started"}
        read_error = None
        reads = 0
        def read(element, attribute, operation="value", count=0):
            nonlocal read_error, reads
            if read_error is not None:
                return None
            if listing:
                try:
                    valid_element = CFGetTypeID(element) == AXUIElementGetTypeID()
                except Exception:
                    valid_element = False
                if not valid_element:
                    read_error = "menu_read_failed"
                    return None
            if attribute == "AXChildren" and operation == "value":
                size = read(element, attribute, operation="count")
                if size is None:
                    return None
                if type(size) is not int or not 0 <= size <= 5000:
                    read_error = "menu_read_budget_exceeded"
                    return None
                children = [] if size == 0 else read(element, attribute, operation="range", count=size)
                if children is None:
                    read_error = read_error or "menu_state_changed"
                    return None
                after = read(element, attribute, operation="count")
                if read_error is not None:
                    return None
                if after != size or len(children) != size:
                    read_error = "menu_state_changed"
                    return None
                return children
            if _aborted_now():
                read_error = "aborted_by_user"
                return None
            if time.monotonic() >= deadline:
                read_error = "menu_read_timeout"
                return None
            if reads >= 5000:
                read_error = "menu_read_budget_exceeded"
                return None
            reads += 1
            timeout = min(1.0, deadline - time.monotonic())
            if timeout <= 0:
                read_error = "menu_read_timeout"
                return None
            try:
                configured = AXUIElementSetMessagingTimeout(element, timeout)
            except Exception:
                configured = -1
            if configured != 0:
                read_error = "menu_timeout_unavailable"
                return None
            try:
                if operation == "count":
                    status, value = AXUIElementGetAttributeValueCount(element, attribute, None)
                elif operation == "range":
                    status, value = AXUIElementCopyAttributeValues(element, attribute, 0, count, None)
                else:
                    status, value = AXUIElementCopyAttributeValue(element, attribute, None)
            except Exception:
                read_error = "menu_read_failed"
                return None
            finally:
                try:
                    reset = AXUIElementSetMessagingTimeout(element, 0)
                except Exception:
                    reset = -1
                if reset != 0 and read_error is None:
                    read_error = "menu_timeout_reset_failed"
            if _aborted_now():
                read_error = "aborted_by_user"
                return None
            if time.monotonic() >= deadline:
                read_error = "menu_read_timeout"
                return None
            if status != 0:
                if status in (-25205, -25212):
                    return None
                read_error = "menu_read_failed"
                return None
            if attribute == "AXChildren" and operation != "count" and value is not None and not isinstance(value, (list, tuple, NSArray)):
                read_error = "menu_read_failed"
                return None
            if attribute == "AXChildren" and operation != "count" and value is not None and len(value) > 5000:
                read_error = "menu_read_budget_exceeded"
                return None
            return value
        pid = self._resolve(target, launch=False)
        if pid is None:
            return {"type": "error", "error": "target_not_found", "target": target}
        if listing:
            from .native_process_identity import process_start_identity
            try:
                start_identity = process_start_identity(pid)
            except Exception:
                return {"type":"error", "error":"target_changed", "effect":"not_started"}
        def titled_children(parent):
            nonlocal read_error
            children = read(parent, "AXChildren")
            if children is None:
                read_error = read_error or "menu_state_unavailable"
                return []
            for child in children:
                if read(child, "AXRole") == "AXMenu":
                    children = read(child, "AXChildren")
                    if children is None:
                        read_error = read_error or "menu_state_unavailable"
                        return []
                    break
            found, titles = [], set()
            for child in children:
                title = read(child, "AXTitle")
                if title is None or title == "":
                    continue
                if not valid_title(title):
                    read_error = read_error or "invalid_menu_title"
                    return []
                if title in titles:
                    read_error = read_error or "menu_ambiguous"
                    return []
                titles.add(title)
                found.append((child,title))
            return found
        try:
            if listing:
                item, walked = read(AXUIElementCreateApplication(pid), "AXMenuBar"), []
                if item is None:
                    raise RuntimeError("menu bar unavailable")
                for want in path:
                    wants = [want] if isinstance(want,str) else want
                    matches = [(child,title) for child,title in titled_children(item) if title in wants]
                    if len(matches) != 1:
                        if len(matches) > 1:read_error = read_error or "menu_ambiguous"
                        raise RuntimeError("menu path not unique")
                    item,title = matches[0]
                    walked.append(title)
            else:
                item, walked = self._menu_item(pid, path, read=read)
        except Exception as e:
            return {"type": "error", "error": read_error or "menu_not_found", "effect":"not_started", "detail": str(e)}
        if listing:
            rows = []
            for child,title in titled_children(item):
                enabled = read(child, "AXEnabled")
                children = read(child, "AXChildren")
                roles = None if children is None else [read(c, "AXRole") for c in children]
                submenu = (None if roles is None else True if "AXMenu" in roles
                           else None if any(not isinstance(role,str) for role in roles) else False)
                rows.append({"title":title, "path":[*walked,title],
                             "enabled":enabled if type(enabled) is bool else None,
                             "has_submenu":submenu})
            if _aborted_now():
                read_error = "aborted_by_user"
            if time.monotonic() >= deadline:
                read_error = read_error or "menu_read_timeout"
            try:
                if self._resolve(target, launch=False) != pid or process_start_identity(pid) != start_identity:
                    read_error = read_error or "target_changed"
            except Exception:
                read_error = read_error or "target_changed"
            if read_error:
                return {"type":"error", "error":read_error, "effect":"not_started"}
            return {"type":"menu_items", "path":walked, "items":rows, "complete":True}
        enabled = read(item, "AXEnabled")
        if type(enabled) is not bool:
            return {"type": "error", "error": read_error or "menu_state_unavailable",
                    "path": walked, "detail": "메뉴의 활성 상태를 읽지 못했습니다"}
        frontmost = read(AXUIElementCreateApplication(pid), "AXFrontmost")
        mark = read(item, "AXMenuItemMarkChar")
        shortcut = read(item, "AXMenuItemCmdChar")
        if read_error:
            return {"type": "error", "error": read_error, "effect": "not_started"}
        return {"type": "menu_state", "path": walked,
                "target_ax_frontmost": frontmost if type(frontmost) is bool else None,
                "enabled": enabled,
                "mark": mark,
                "shortcut": shortcut}

    def identity(self, target: str) -> str:
        """Stable identity namespace for contract bindings: the bundle id."""
        return target

    def document_text(self, target: str, document_token=None, *, max_utf16_units=1000000):
        """Read a pinned document's complete text without changing focus.

        This is not the save receipt's document_snapshot capability: file
        format and final save-panel settings are not yet witnessed here.
        """
        from .native_document import TextEditDocumentReader, DocumentSnapshotError
        from .native_document_ax import AXDocumentAdapter
        if not hasattr(self, '_document_reader'):
            self._document_reader = TextEditDocumentReader(AXDocumentAdapter(self._resolve))
        try:
            return self._document_reader.read(target, document_token=document_token,
                                              max_utf16_units=max_utf16_units)
        except DocumentSnapshotError as exc:
            return {'type': 'error', 'error': 'document_snapshot_unavailable',
                    'effect': 'not_started', 'detail': str(exc)}

    def visual_read(self) -> dict:
        """Can this driver take a picture of a window, and may it act on one?

        Answering "no, and here is why" is the point: a caller that cannot see
        an AX-opaque window needs to know whether asking for pixels is worth a
        round trip, and a person needs to know a permission is what is missing
        rather than the app.
        """
        try:
            from .native_visual import ScreenCaptureBackend
            allowed = ScreenCaptureBackend().permission()
        except Exception as exc:
            return {"available": False, "reason": f"unavailable: {exc}"}
        if allowed is not True:
            return {"available": False, "reason": "screen_capture_permission_missing",
                    "detail": "시스템 설정 > 개인정보 보호 및 보안 > 화면 기록 에서 켜주세요"}
        # Even when it is available, a picture is something to READ. It does
        # not authorize an action and it does not witness a commit — the same
        # rule `visual.py` states for the browser, kept here so one transport
        # cannot quietly mean more than the other.
        return {"available": True, "authorizes_action": False,
                "commit_witness": False}

    def capture(self, target: str, *, obs_id: str | None = None,
                window_index: int | None = None, path=None,
                timeout_seconds: float = 5) -> dict:
        """A picture of ONE observed window, for what the tree cannot describe.

        Only the window that was observed, never the desktop and never a
        region — the same narrowing the accessibility path already has, for
        the same reason: a caller asked about an app, not about the screen.

        Pixels are not returned inline. `path` saves the PNG and the answer
        says where, because an image large enough to be useful is large enough
        that handing it back by default is a decision, not a convenience.
        """
        obs = self._latest.get(target.lower())
        if obs is None:
            return {"type": "error", "error": "stale_observation",
                    "effect": "not_started",
                    "detail": "observe first: a capture is of an OBSERVED window"}
        if obs_id is not None and obs.obs_id != obs_id:
            return {"type": "error", "error": "stale_observation",
                    "effect": "not_started",
                    "detail": f"latest is {obs.obs_id}"}
        allowed = self.visual_read()
        if not allowed.get("available"):
            return {"type": "error", "error": "visual_read_unavailable",
                    "effect": "not_started", **allowed}
        self._showing_control(obs.pid)
        try:
            from .native_visual import capture_native, NativeVisualError
            shot = capture_native(self, obs.obs_id, target,
                                  window_index=window_index,
                                  timeout_seconds=timeout_seconds)
        except Exception as exc:
            return {"type": "error", "error": "visual_read_failed",
                    "effect": "not_started", "detail": str(exc)}
        answer = {"type": "capture", **shot.to_dict()}
        if path is not None:
            answer["path"] = str(shot.save(path))
        return answer

    INDICATOR_TTL = 4.0

    def _showing_control(self, pid) -> None:
        """Put the outline on this app, and keep it there while we are working.

        Never raises and never blocks the action: a failure to draw is reported
        through the child's own absence, and losing the outline must not be a
        reason the person's task stops. It is still not silent — `indicator()`
        reports what is on screen, and the signed helper refuses outright when
        it cannot draw, because that transport holds the grant.

        The outline is torn down by a heartbeat rather than a scope: a driver
        that CAN act at any moment is not one that IS acting, and an outline
        that never leaves stops being read.
        """
        if not getattr(self, "_indicate", False) or type(pid) is not int or pid <= 0:
            return
        try:
            beat = getattr(self, "_indicator_beat", None)
            if beat is None:
                import tempfile
                handle, beat = tempfile.mkstemp(prefix="nlc-indicator-")
                os.close(handle)
                self._indicator_beat = beat
            os.utime(beat, None)                      # this is the heartbeat
            current = getattr(self, "_indicator", None)
            if (current is not None and current.alive()
                    and getattr(self, "_indicator_pid", None) == pid):
                return
            self.hide_indicator()
            from .overlay import Indicator
            self._indicator = Indicator(pid, heartbeat=beat, ttl=self.INDICATOR_TTL)
            self._indicator.__enter__()
            self._indicator_pid = pid
        except Exception:
            # Best effort. `indicator()` tells the truth about what happened.
            self._indicator = None

    def indicator(self) -> dict:
        """What is on screen saying this is happening, if anything."""
        current = getattr(self, "_indicator", None)
        return {"showing": bool(current is not None and current.alive()),
                "pid": getattr(self, "_indicator_pid", None),
                "enabled": bool(getattr(self, "_indicate", False))}

    def hide_indicator(self) -> None:
        current = getattr(self, "_indicator", None)
        if current is not None:
            try:
                current.__exit__(None, None, None)
            except Exception:
                pass
        self._indicator = None
        self._indicator_pid = None

    def close(self) -> None:
        """Take the outline down; a driver nobody holds is not driving."""
        self.hide_indicator()
        beat = getattr(self, "_indicator_beat", None)
        if beat:
            try:
                os.unlink(beat)
            except OSError:
                pass
        self._indicator_beat = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

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
        self._showing_control(pid)
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
            # The one case the protocol reserves a picture for: a window that
            # is there and says nothing about itself. It stayed an error with
            # no `obs_id`, so the fallback it names had nothing to be called
            # with — `native_visual` was written, tested, and reachable from
            # nowhere (found 2026-09-20). Register the observation so a caller
            # can cite it, and say whether a picture is actually available.
            self._latest[target.lower()] = obs
            return {"type": "error", "error": "empty_tree", "target": target,
                    "obs_id": obs.obs_id, "waited_ms": waited_ms,
                    "visual_read": self.visual_read(),
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
        answer = {"type": "observation", "obs_id": obs.obs_id, "target": target,
                  "mode": mode, "tree": tree, "element_count": len(obs.elements),
                  "custom_selects": self._custom_selects(obs),
                  "truncated": obs.truncated, "waited_ms": waited_ms}
        if obs.truncated:
            # A tree that stopped early describes less than the window holds,
            # which is the other half of "screenshots are for where the tree
            # is incomplete". Saying so here is how a caller knows to ask.
            answer["visual_read"] = self.visual_read()
        return answer

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

    def act(self, obs_id: str, target: str, action: dict, expect: dict | None = None,
            risk_class: str = "none", intent_text: str = "",
            approved: bool = False):
        if risk_class not in ("none", "preapproved", "confirm", "handoff"):
            return {"type": "error", "error": "unverifiable_action", "effect": "not_started"}
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
        self._showing_control(getattr(obs, "pid", None))
        if obs is None or obs.obs_id != obs_id:
            return {"type": "error", "error": "stale_observation",
                    "effect": "not_started",
                    "detail": "re-observe and re-derive element_index "
                              f"(latest is {obs.obs_id if obs else 'none'})"}

        # What the caller believed it was acting on, checked against the
        # element ITSELF rather than by re-reading the screen. A batch planned
        # on one observation has to know its target is still that target, and
        # walking the tree again costs 9.8 SECONDS in Mail — measured — while
        # reading one element's own attributes costs about one millisecond.
        # Checking here rather than beforehand also closes the gap between the
        # check and the action, which a separate probe would leave open.
        if expect:
            why = self._mismatch(obs, action, expect)
            if why:
                return {"type": "error", "error": "target_changed",
                        "effect": "not_started", "detail": why}

        name = action.get("name")
        try:
            def before_pointer():
                if self._latest.get(target.lower()) is not obs:
                    raise NotStarted("observation changed before window input")
                if self._resolve(target, launch=False) != obs.pid:
                    raise NotStarted("target process changed before window input")
                if expect:
                    why = self._mismatch(obs, action, expect)
                    if why:
                        raise NotStarted(why)
            result = self._perform(obs, action, before_pointer=before_pointer)
        except Exception as e:  # surface, never swallow
            # An exception says where it was raised, not how far the action
            # got. Only the raises that provably precede dispatch claim so.
            if isinstance(e, _AfterOpening):
                return {"type": "error", "error": "action_failed",
                        "action": name, "effect": e.effect,
                        # A menu came up over whatever the caller was holding,
                        # so its observation is behind the times even when the
                        # selection never happened (peer review).
                        "reobserve_required": True, "detail": str(e),
                        **e.facts}
            if isinstance(e, _NeedsForeground):
                return {"type": "error", "error": "action_failed", "action": name,
                        "effect": "not_started", "needs_foreground": True,
                        "detail": str(e)}
            if isinstance(e, _NeedsPointer):
                return {"type": "error", "error": "action_failed", "action": name,
                        "effect": "not_started", "needs_pointer": True,
                        "detail": str(e)}
            return {"type": "error", "error": "action_failed", "action": name,
                    "effect": ("not_started" if isinstance(e, NotStarted)
                               else "unknown"),
                    "detail": str(e)}
        self._last_action_at = time.time()
        return {"type": "acted", "action": name, "obs_id": obs_id,
                "note": "not verified — observe again and check the result (D7)",
                **(result or {})}

    @staticmethod
    def selection_digest(pid: int) -> str:
        """What this app currently has selected and focused, as one short string.

        A control's identity is not what a consequential action is ABOUT. The
        same Delete button stays exactly where it is while the selected message
        changes underneath it, so role and label agreeing proves only that the
        button is the button (advisor finding). What the delete would fall on is
        the selection, and that is read here.

        Two or three AX reads on the front window — not a tree walk, which is
        the whole point: nine and a half seconds in Mail buys nothing a caller
        could not have got for a millisecond.
        """
        app = AXUIElementCreateApplication(pid)
        parts = []
        focused = _attr(app, "AXFocusedUIElement")
        if focused is not None:
            parts.append("focus:" + (_elem_label(focused) or "?")[:80])
        win = _attr(app, "AXFocusedWindow")
        if win is not None:
            for key in ("AXSelectedRows", "AXSelectedChildren",
                        "AXSelectedTextRange"):
                v = _attr(win, key)
                if isinstance(v, list):
                    parts.append(f"{key}:{len(v)}:" + "|".join(
                        (_elem_label(x) or "?")[:40] for x in v[:8]))
                elif v is not None:
                    parts.append(f"{key}:{v}")
        import hashlib
        return hashlib.sha1("\n".join(parts).encode()).hexdigest()[:12]

    def selection(self, target: str) -> dict:
        """The current selection digest, for a caller about to plan against it."""
        pid = self._resolve(target, launch=False)
        if pid is None:
            return {"type": "error", "error": "target_not_found"}
        return {"type": "selection", "digest": self.selection_digest(pid)}

    def _mismatch(self, obs, action: dict, expect: dict) -> str:
        """Why this is no longer what the caller meant — or "" if it still is."""
        if expect.get("no_modal") and self._modal_sheet(obs.pid):
            return "화면에 대화상자가 열렸습니다"
        want_sel = expect.get("selection")
        if want_sel and want_sel != self.selection_digest(obs.pid):
            return "고른 것이 바뀌었습니다 — 같은 버튼이라도 대상이 다릅니다"
        idx = action.get("element_index")
        if idx is None:
            # No element to compare against. An expectation that names one is
            # not weakly satisfied here, it is unanswerable — and an ignored
            # precondition looks exactly like a met one, which is the failure
            # this contract was built to stop. So it is refused rather than
            # skipped (peer question: how does `expect` behave without an
            # index? It used to be silently dropped).
            named = [k for k in ("role", "label", "attribute", "enabled")
                     if expect.get(k) is not None]
            if named:
                return (f"이 동작은 대상 요소를 지정하지 않는데 "
                        f"{', '.join(named)} 을(를) 기대했습니다 — 확인할 수 "
                        f"없는 조건입니다")
            return ""
        try:
            el = self._element(obs, action)
        except Exception as e:
            return str(e)
        if expect.get("enabled") and _attr(el, "AXEnabled") is False:
            return "대상이 비활성 상태입니다"
        want_role = expect.get("role")
        if want_role:
            # The element's OWN role, not a substring of its label. Comparing
            # against the label line looked like it worked and could never
            # pass: `_elem_label` describes what a thing is called, and a
            # Button's label does not contain the word "Button". Every action
            # the screen loop tried was refused as "대상이 달라졌습니다" while
            # the element was sitting exactly where it always had been.
            role = _attr(el, "AXRole") or ""
            if role.startswith("AX"):
                role = role[2:]
            if role != want_role:
                return f"대상이 달라졌습니다 (기대 {want_role}, 지금 {role or '?'})"
        seen = _elem_label(el)
        if seen is None:
            return "대상이 화면에서 사라졌습니다"
        want = expect.get("label")
        if want is not None:
            from .flow import Selector, _norm
            key = expect.get("attribute")
            attrs = (key,) if key else Selector._ATTR_KEYS
            def normalized(value, attr):
                value = _norm(value)
                return Selector._stable_id(value) if attr == "Identifier" else value
            if not any((got := Selector._attr(seen, attr)) is not None
                       and normalized(got, attr) == normalized(want, attr) for attr in attrs):
                return f"대상이 달라졌습니다 (기대 {want!r}, 지금 {seen[:60]!r})"
        return ""

    def probe(self, obs_id: str, target: str, element_index: int) -> dict:
        """One element's own attributes, with no tree walk.

        A read late in a batch needs what the screen says NOW, and re-observing
        to get it is what made Mail unaffordable."""
        obs = self._latest.get(target.lower())
        if obs is None or obs.obs_id != obs_id:
            return {"type": "error", "error": "stale_observation"}
        try:
            el = self._element(obs, {"element_index": element_index})
        except Exception as e:
            return {"type": "error", "error": "target_missing", "detail": str(e)}
        return {"type": "probe", "line": _elem_label(el) or ""}

    def _element(self, obs, action):
        """The element this action is about — by index, or by a point.

        A coordinate is an address nobody has resolved yet. Controls that draw
        themselves expose no AX element of their own, so an index cannot reach
        them and they used to get `needs_pointer` and a handoff. Resolving the
        point is what reaches them WITHOUT synthesising anything: the thing
        pressed is still a real element, still belonging to the named app, and
        the press is still the app's own action.

        `point` is in window coordinates of `window_index` (default 0) — the
        same space `capture` reports its viewport in, so a coordinate read off
        a screenshot can be handed straight back.
        """
        idx = action.get("element_index")
        if idx is not None or "point" not in action:
            if type(idx) is not int or not (0 <= idx < len(obs.elements)):
                raise NotStarted(f"element_index {idx} out of range "
                                 f"0..{len(obs.elements)-1}")
            return obs.elements[idx]
        return self._element_at_point(obs, action)

    def _element_at_point(self, obs, action):
        from ApplicationServices import (AXUIElementCopyElementAtPosition,
                                         AXUIElementGetPid)
        point = action.get("point")
        if not isinstance(point, dict) or set(point) != {"x", "y"}:
            raise NotStarted("point 은 {x, y} 여야 합니다")
        try:
            x, y = float(point["x"]), float(point["y"])
        except (TypeError, ValueError):
            raise NotStarted("point 의 x, y 는 숫자여야 합니다")
        if not (math.isfinite(x) and math.isfinite(y)):
            raise NotStarted("point 의 x, y 는 유한해야 합니다")

        windows = _real_windows(obs.pid)
        index = action.get("window_index", 0)
        if type(index) is not int or not (0 <= index < len(windows)):
            raise NotStarted(f"window_index {index} out of range 0..{len(windows)-1}")
        frame = _elem_frame(windows[index])
        if frame is None:
            raise NotStarted("창의 위치를 읽지 못했습니다")
        wx, wy, ww, wh = frame
        # Bounds-checked against that window. A point outside it is not this
        # app's to press, and the pid check below refuses anything that turns
        # out to belong to someone else's window anyway.
        if not (0 <= x < ww and 0 <= y < wh):
            raise NotStarted(f"point ({x}, {y}) 가 창 밖입니다 ({ww}x{wh})")
        # Asked of the APP, not the screen. System-wide hit testing is a
        # z-order question — measured, a point inside a background window
        # returned an AXWebArea belonging to a browser that was on top — so it
        # would answer about whatever the person happened to have in front.
        # The application element answers about that app's own hierarchy,
        # which is what a window-relative coordinate meant, and it works for a
        # window nobody has brought forward.
        found = AXUIElementCopyElementAtPosition(
            AXUIElementCreateApplication(obs.pid), wx + x, wy + y, None)
        error, element = found if isinstance(found, tuple) else (0, found)
        if error != 0 or element is None:
            raise NotStarted(f"({x}, {y}) 위치에 접근성 요소가 없습니다")
        owner = AXUIElementGetPid(element, None)
        owner = owner[1] if isinstance(owner, tuple) else owner
        if owner != obs.pid:
            # The system-wide element answers about whatever is under that
            # coordinate, including an app the caller never named.
            raise NotStarted(f"({x}, {y}) 위치의 요소는 다른 앱의 것입니다")
        return element

    def _perform(self, obs, action, *, before_pointer=None):
        name = action["name"]
        if name == "prepare_window":
            if set(action) != {"name", "element_index"}:
                raise NotStarted("prepare_window requires only an observed window index")
            el = self._element(obs, action)
            if _attr(el, 'AXRole') != 'AXWindow':
                raise NotStarted("prepare_window requires an AXWindow")
            return self._prepare_window(obs, el)
        if name == "click":
            el = self._element(obs, action)
            # Some custom AX elements accept a no-op AXPress while explicitly
            # advertising no press action. Respect the advertised action list
            # before dispatch instead of treating that no-op as a useful click.
            try:
                actions_error, actions = AXUIElementCopyActionNames(el, None)
                # PyObjC can expose CFArray as NSArray rather than a list.
                actions = tuple(actions) if actions is not None else None
            except Exception:
                actions_error, actions = -1, None
            if (actions_error == 0 and actions is not None
                    and all(isinstance(a, str) for a in actions) and 'AXPress' not in actions):
                if self._select_row(el):
                    return {"input_route": "row_selection"}
                raise _NeedsPointer(
                    "이 요소는 누를 수 있다고 알리지 않습니다 — 이 드라이버는 "
                    "좌표 클릭을 합성하지 않습니다. 사람이 직접 눌러야 합니다")
            err = AXUIElementPerformAction(el, "AXPress")
            if err == AX_CANNOT_COMPLETE:
                # NOT a failure. Apple's AXUIElement.h says an action callback
                # that does modal processing can time out and return this while
                # having done the work. Clicking again "to be safe" is how a
                # Send button sends twice — so report it and let the step's
                # postcondition decide whether it landed.
                return {"unverified": "cannot_complete",
                        "note": "AXPress timed out; it may still have happened"}
            if err == AX_ACTION_UNSUPPORTED:
                # Only unsupported is a definitive reason to consider another
                # dispatch mechanism. Unknown AX failures must never be turned
                # into a retry or a claim that no effect occurred.
                if self._select_row(el):
                    return {"input_route": "row_selection"}
                raise _NeedsPointer(
                    "이 요소는 누를 수 있다고 알리지 않습니다 — 이 드라이버는 "
                    "좌표 클릭을 합성하지 않습니다. 사람이 직접 눌러야 합니다")
            if err != 0:
                raise RuntimeError(f"AXPress failed ({err}); effect is unknown; do not retry blindly")
        elif name == "set_value":
            el = self._element(obs, action)
            self._focus_for_write(el)
            err = AXUIElementSetAttributeValue(el, "AXValue", action["value"])
            if err != 0:
                raise RuntimeError(f"AXValue set failed ({err})")
            landed = _attr(el, "AXValue")
            if isinstance(landed, str) and landed != action["value"]:
                raise NotStarted(
                    "값을 썼다고 응답했지만 요소에 남지 않았습니다 — "
                    f"지금 값: {landed!r}")
        elif name == "type_text":
            el = self._element(obs, action)
            role = _attr(el, "AXRole")
            if role not in self.TEXT_ROLES:
                # The signed helper has always refused this; in-process did
                # not, so "fill in a field" and "type into a terminal" were the
                # same primitive on one transport and not the other.
                raise NotStarted(f"텍스트 입력 요소가 아닙니다: {role}")
            text = action["text"]
            if any(ord(c) < 0x20 or ord(c) == 0x7F for c in text):
                raise NotStarted("제어문자는 입력할 수 없습니다")
            self._type_text(text, obs.pid, el)
        elif name == "select_option":
            return self._select_option(obs, action)
        elif name == "open_select":
            return self._open_select(obs, action)
        elif name == "select_custom_option":
            return self._select_custom_option(obs, action)
        elif name == "press_key":
            # Tab and the arrows exist to walk a keyboard focus toward a
            # control. Every other action here already NAMES its control, so
            # walking to it was never the point — it was how you reach a thing
            # when your only channel is the keyboard. Accessibility is not
            # that channel: it addresses the element.
            #
            # There is no general accessibility translation for "press Tab"
            # either. It means "whatever this app decides comes next", which
            # is not a question the API answers. So this is a refusal rather
            # than a worse imitation of one.
            raise NotStarted(
                "이 드라이버는 키를 합성하지 않습니다 — "
                "요소를 직접 지정하는 click / set_value / type_text 를 쓰십시오")
        elif name == "scroll":
            el = self._element(obs, action)
            return self._scroll(el, action.get("direction", "down"),
                                action.get("pages", 1))
        elif name == "read":
            if "element_index" in action:
                self._element(obs, action)
            # Look, change nothing. A recipe that must carry a value from one
            # part of the screen to another had no way to say "now read this":
            # every step had to act, so reading was only ever a side effect of
            # doing something else.
            return None
        elif name == "menu":
            # This used to raise the app first, because an app validates its
            # menus while it is the active one — measured: a background
            # TextEdit reports `Edit > Select All` disabled when nothing is
            # wrong with it. That is a real precondition, but taking the
            # person's focus to satisfy it is not this action's to decide, so
            # the attempt is made where the app stands and the refusal below
            # says whether being in the background is the likely reason.
            try:
                item, walked = self._menu_item(obs.pid, action["path"])
            except Exception as e:
                raise NotStarted(str(e)) from e
            # Menu items are validated lazily by the app, so a disabled item is
            # a real precondition failure worth reporting by name rather than a
            # silent no-op press.
            enabled = _attr(item, "AXEnabled")
            if type(enabled) is not bool:
                raise NotStarted("메뉴의 활성 상태를 읽지 못했습니다")
            if not enabled:
                # A modal sheet disables the whole menu bar, and "this menu is
                # disabled" is a dead end when the real answer is "a dialog is
                # waiting". An earlier run that stopped mid-flow leaves exactly
                # that state behind, so this is the common case, not an exotic one.
                if self._modal_sheet(obs.pid):
                    raise NotStarted(
                        f"앱에 열린 대화상자가 있어 '{' > '.join(walked)}' 메뉴를 "
                        f"쓸 수 없습니다. 대화상자를 닫고 다시 시도해 주세요")
                if not self._frontmost(obs.pid):
                    # Match the signed helper's handoff signal. A disabled
                    # background menu may depend on the key window; do not
                    # activate the app or claim foregrounding guarantees it.
                    raise _NeedsForeground(
                        f"메뉴 '{' > '.join(walked)}' 가 비활성 상태입니다. "
                        "배경 앱에는 키 윈도우가 없어 사용할 수 없는 메뉴일 수 "
                        "있습니다. 앱을 앞으로 가져온 뒤 다시 시도해 주세요")
                raise NotStarted(f"메뉴 '{' > '.join(walked)}' 가 지금은 "
                                 f"비활성 상태입니다.{self._blocking_hint(obs)}")
            err = AXUIElementPerformAction(item, "AXPress")
            if err == AX_ACTION_UNSUPPORTED:
                raise NotStarted(f"메뉴 '{' > '.join(walked)}' AXPress unsupported")
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
            if err == AX_ACTION_UNSUPPORTED:
                raise NotStarted(f"secondary action {action['action']!r} unsupported")
            if err != 0:
                raise RuntimeError(f"secondary action {action['action']!r} failed ({err})")
        else:
            raise NotStarted(f"unknown action {name!r}")

    # ------------------------------------------------------------ dispatch
    #
    # There is no input synthesis here any more. Every action addresses the
    # element it names, through the accessibility API, and what cannot be done
    # that way is refused rather than mimed with a CGEvent.
    #
    # The path this replaces was already the careful version: events went to
    # the target pid rather than the global HID tap, so a background app could
    # not have its keystrokes land in whatever the person was using. But a
    # posted event still carries no address inside that process, so the caller
    # had to believe the app routed it the way a human gesture would. Measured
    # 2026-09-20: Codex Computer Use ships zero input synthesis across all 56
    # Mach-O binaries in its bundle and drives macOS entirely through
    # AXUIElementPerformAction / AXUIElementSetAttributeValue.

    TEXT_ROLES = ("AXTextField", "AXTextArea", "AXComboBox", "AXSearchField")

    @staticmethod
    def _focused(pid):
        return _attr(AXUIElementCreateApplication(pid), "AXFocusedUIElement")


    @staticmethod
    def _focus_for_write(element) -> None:
        """Give the element the app's focus before writing into it.

        A web text field accepts `AXValue` and `AXSelectedText` as settable,
        returns success from the write, and keeps the old value: measured on
        Safari 2026-09-20, the write only lands once `AXFocused` is set. That
        is the same shape as an AXPress that returns 0 and does nothing — a
        silent success — so the focus is arranged rather than hoped for.

        This moves focus INSIDE the app, to the element the caller named. It
        does not activate the app and does not touch the window server's idea
        of what is in front; a background app's own focused element is its
        business. Elements that do not offer `AXFocused` (most native fields)
        are left alone.
        """
        settable = AXUIElementIsAttributeSettable(element, "AXFocused", None)
        if not (isinstance(settable, tuple) and settable[0] == 0 and settable[1] is True):
            return
        if _attr(element, "AXFocused") is True:
            return
        AXUIElementSetAttributeValue(element, "AXFocused", True)

    def _type_text(self, text, pid, element):
        """Write the text INTO the named element, addressed, in one call.

        A keystroke carries no address. It goes wherever the first responder
        is, so naming an element and then posting keys to the process meant the
        caller believed it typed into a field while the app decided for itself
        where the characters went. Everything this method used to be was about
        that gap: a focus check before, another after taking the lock, another
        between every pair of characters, and a partial-write `unknown` for
        when the answer changed halfway through.

        `AXSelectedText` closes the gap instead of watching it. The write names
        the element, so it lands there or it does not land at all — there is no
        half-typed state left to report, and no focus to require or disturb.
        It replaces the current selection, which with an empty selection is an
        insert at the caret: exactly what typing does.

        Verified 2026-09-20 against a background editable NSTextView: Korean
        and an emoji arrived intact while the fixture's own key-event counter
        stayed at 0.
        """
        if not text:
            return
        settable = AXUIElementIsAttributeSettable(element, "AXSelectedText", None)
        # PyObjC hands back (error, bool). Anything but an explicit yes is a no.
        if not (isinstance(settable, tuple) and settable[0] == 0 and settable[1] is True):
            raise NotStarted(
                "이 요소는 텍스트 삽입을 지원하지 않습니다 — "
                "AXSelectedText 를 쓸 수 없는 요소입니다")
        if _aborted_now():
            raise NotStarted("사용자가 중단했습니다 — 아무것도 입력하지 않았습니다")
        before = _attr(element, "AXValue")
        with _INPUT_LOCK:
            self._focus_for_write(element)
            err = AXUIElementSetAttributeValue(element, "AXSelectedText", text)
        if err == AX_CANNOT_COMPLETE:
            # As with AXPress: a callback doing modal processing can time out
            # having done the work. Writing again "to be safe" is how one
            # sentence becomes two.
            raise _AfterOpening(
                "텍스트 삽입이 시간 초과했습니다 — 들어갔을 수도 있습니다", "unknown")
        if err != 0:
            raise NotStarted(f"텍스트 삽입에 실패했습니다 ({err})")
        # A web field reports success and keeps its old value when the write
        # did not take. Ask the element what it says now instead of believing
        # the return code.
        after = _attr(element, "AXValue")
        if isinstance(after, str) and after == before and text not in (after or ""):
            raise NotStarted(
                "삽입했다고 응답했지만 요소의 값이 그대로입니다 — "
                "들어가지 않았습니다")

    NAVIGATION_KEYS = {
        "Tab": ("tab", 0), "shift+Tab": ("tab", "shift"),
        "Up": ("up", 0), "Down": ("down", 0),
        "Left": ("left", 0), "Right": ("right", 0),
    }

    SELECTABLE_ROLES = ("AXPopUpButton", "AXComboBox")


    @staticmethod
    def _select_row(element) -> bool:
        """Clicking a row's label means selecting that row. Say that instead.

        A save panel's sidebar entry is an AXStaticText inside an AXRow inside
        an AXOutline, and the label advertises no AXPress — which is why a
        coordinate click used to be the only way to pick a folder. The outline
        answers `AXSelectedRows`, and setting it is the same request with an
        address on it: measured 2026-09-20, settable on both TextEdit's save
        sheet and Finder's sidebar.

        Returns False rather than raising, so the caller can fall through to
        its refusal when this is not a row at all.
        """
        row = node = element
        containers = ("AXOutline", "AXTable", "AXBrowser", "AXList")
        for _ in range(12):
            if node is None:
                return False
            role = _attr(node, "AXRole")
            if role == "AXRow":
                row = node
            elif role in containers:
                if row is element and _attr(element, "AXRole") != "AXRow":
                    return False          # no row between the label and the list
                settable = AXUIElementIsAttributeSettable(node, "AXSelectedRows", None)
                if not (isinstance(settable, tuple) and settable[0] == 0
                        and settable[1] is True):
                    return False
                return AXUIElementSetAttributeValue(node, "AXSelectedRows", [row]) == 0
            node = _attr(node, "AXParent")
        return False

    def _prepare_window(self, obs, element):
        """Make this window the key one, by asking it rather than clicking it.

        What stood here posted a synthetic mouse down/up pair at the window's
        title bar, addressed to the process. That is a gesture mimed at an app
        in the hope it reads it the way a person's click would — and it needed
        the app in front first, because an inactive app has no key window to
        give.

        `AXMain` and `AXFocused` are the same request written as a question the
        window can answer. The frontmost requirement stays: this picks which
        window of an app the person is already in becomes key, and it is not a
        way to bring an app forward.
        """
        if not self._frontmost(obs.pid):
            raise _NeedsForeground(
                "이 동작은 앱이 앞에 있어야 합니다 — 배경 앱에는 보내지 않았습니다")
        if _attr(element, "AXMinimized") is not False:
            raise NotStarted("최소화된 창은 준비할 수 없습니다")
        wrote = []
        for name in ("AXMain", "AXFocused"):
            settable = AXUIElementIsAttributeSettable(element, name, None)
            if not (isinstance(settable, tuple) and settable[0] == 0 and settable[1] is True):
                continue
            if _aborted_now():
                raise NotStarted("사용자가 중단했습니다")
            if AXUIElementSetAttributeValue(element, name, True) == 0:
                wrote.append(name)
        if not wrote:
            raise NotStarted(
                "이 창은 AXMain/AXFocused 를 받지 않습니다 — 준비하지 못했습니다")
        # Ask the window what actually happened rather than assuming the write
        # took: a window can accept the attribute and still not become key.
        became = _attr(element, "AXMain") is True or _attr(element, "AXFocused") is True
        return {"input_route": "window_prepare", "wrote": wrote,
                "window_reports_key": became,
                **({} if became else {"unverified": "observe_effect",
                                      "reobserve_required": True})}

    @staticmethod
    def _app_menus(pid, depth=7):
        """Every AXMenu currently in this app's hierarchy.

        A popup's menu does NOT appear under the button — measured: pressing it
        leaves `AXChildren` empty and the menu turns up elsewhere in the app,
        alongside the twenty-odd menus the menu bar already owns. So the way to
        find the one that just opened is to know which ones were already there.
        """
        found = []

        def walk(node, d):
            if d > depth:
                return
            if _attr(node, "AXRole") == "AXMenu":
                found.append(node)
            for child in (_attr(node, "AXChildren") or [])[:60]:
                walk(child, d + 1)

        walk(AXUIElementCreateApplication(pid), 0)
        return found

    # ---------------------------------------------------- open, look, choose
    #
    # `select_option` does all three in one call, which only works when the
    # caller already knows the option's name. A general run does not: nothing
    # in the tree says what a closed popup contains, and the one way to find
    # out is to open it — which is a change to the screen, not a reading of it.
    #
    # So the steps are separated, in the same words the browser backend uses:
    # `open_select` opens and stops, the observation that follows reports what
    # is now visible, and `select_custom_option` chooses from THAT observation.
    # Observing never opens anything, and nothing ever re-opens a menu by
    # itself — a second open is a second change nobody asked for.

    @staticmethod
    def _menu_options(menu):
        """Every item this menu offers, in order, as {label, disabled}.

        No `value`. A native menu item has a title and nothing behind it, and
        inventing a value space here would promise a distinction this surface
        does not have — the same reason `select_option` refuses a `value` that
        differs from the label.

        Returns None when the menu offers more than this driver will vouch for:
        the choice is checked against the FULL set, so a partly-read list could
        hide the change that check exists to catch.
        """
        out = []
        for item in (_attr(menu, "AXChildren") or []):
            if len(out) >= MAX_MENU_OPTIONS:
                return None
            title = _attr(item, "AXTitle")
            if not title:
                continue                      # separators
            out.append({"label": title,
                        "disabled": _attr(item, "AXEnabled") is False})
        return out

    def _menu_is_ours(self, pid, pending) -> bool:
        """Is the menu we pinned still the menu that is up?

        CFEqual against what the app currently owns. A menu that closed and a
        menu that was replaced by a different one are the same answer here:
        not ours, so nothing may be pressed in it.
        """
        return any(CFEqual(pending.menu, m) for m in self._app_menus(pid))

    def _custom_selects(self, obs):
        """What each popup on this screen offers — WITHOUT opening anything.

        A closed native popup does not publish its items: the menu does not
        exist until it is opened. So a closed one is reported with no options
        and the caller is offered `open_select` instead, and the only menu read
        here is the one this driver opened, found again by the identity it was
        pinned under rather than by being the menu that happens to be up.
        """
        # `getattr`, because a Driver can be constructed without __init__ (the
        # contract tests do exactly that) and observing must not depend on
        # optional bookkeeping existing.
        spent = getattr(self, '_spent_menu', None)
        if spent is not None and spent[0] != obs.pid:
            # Someone looked at a different app. That says nothing about the
            # menu left standing over here, and forgetting it because of a
            # glance elsewhere would orphan it (peer review).
            spent = None
        elif spent is not None and not any(CFEqual(spent[2], m)
                                           for m in self._app_menus(obs.pid)):
            self._spent_menu = spent = None   # same app, and it really closed
        pending = self._select
        if pending is not None and pending.pid != obs.pid:
            # Someone looked at a different app. That says nothing about the
            # menu we left standing over here, so it is not consulted and not
            # thrown away — this app's popups are simply all closed as far as
            # this observation is concerned (peer review).
            pending = None
        elif pending is not None and not self._menu_is_ours(obs.pid, pending):
            # It went away on its own — the app closed it, or the person
            # clicked elsewhere. Re-opening is not this function's business.
            self._select = pending = None
        out = {}
        for i, el in enumerate(obs.elements):
            if _attr(el, "AXRole") not in self.SELECTABLE_ROLES:
                continue
            if pending is None or not CFEqual(el, pending.control):
                if spent is not None and CFEqual(el, spent[1]):
                    # Its menu is still up. Saying "closed" here would be
                    # false, and offering `open_select` would offer something
                    # that cannot work while a menu is already posted. So:
                    # neither. This driver does not dismiss it either — in one
                    # measured case an automatic AXCancel was followed by the
                    # popup refusing to open again
                    # (native-select-blocker.md). That is a fact about this
                    # driver cancelling, not about the menu being closed.
                    out[str(i)] = {
                        "state": "unsupported", "options": [],
                        "note": ("이 컨트롤의 메뉴가 아직 열려 있어 자동으로 닫지 "
                                 "않았습니다 — 이전 실앱 검사에서 자동 AXCancel 뒤 "
                                 "재열림 실패가 있었습니다."),
                    }
                    continue
                out[str(i)] = {"state": "closed", "options": []}
                continue
            options = self._menu_options(pending.menu)
            if options is None:
                # More items than this driver will vouch for. Saying
                # "unsupported" offers no operation at all, which is the honest
                # answer — better than a list we cannot re-check.
                out[str(i)] = {"state": "unsupported", "options": []}
                continue
            # The index is per-observation; the identity is what persists. Both
            # are remembered so the choice can be required to cite the same
            # element in the same look.
            pending.index = i
            pending.options = options
            pending.options_obs_id = obs.obs_id
            out[str(i)] = {"state": "open", "options": options}
        return out

    def _menu_slot_busy(self):
        """Is a menu this driver opened still on screen? One slot, one menu.

        Asked BEFORE anything is dispatched, and it mutates nothing. Two ways
        the slot is taken:

          * a pin nobody has spent — the caller opened a menu and has not
            chosen from it;
          * a menu already spent that did not close itself.

        Either way a second `AXShowMenu` would put a menu on top of a menu,
        and this driver will not dismiss the first to make room — so it
        refuses instead, without sending anything.

        A pin is NOT liveness-checked here. The observation path drops a pin
        whose menu has gone, and checking it here would let a look at one app
        throw away what is standing in another (peer review).
        """
        if self._select is not None:
            return ("이미 열어 둔 선택지가 있습니다 — 먼저 고르거나 화면을 "
                    "다시 관측하세요")
        spent = getattr(self, '_spent_menu', None)
        if spent is None:
            return None
        if not any(CFEqual(spent[2], m) for m in self._app_menus(spent[0])):
            self._spent_menu = None           # it closed; the slot is free
            return None
        return ("앞서 연 메뉴가 아직 열려 있습니다 — 자동으로 닫지 않으므로 "
                "새로 열지 않습니다")

    def _forget_pin(self):
        """Stop tracking the menu. Send nothing.

        This used to `AXCancel` the menu on the way out. In the one case that
        was measured, a popup whose menu had been dismissed that way then
        refused to post a menu at all — every later AXShowMenu returned success
        and nothing appeared (native-select-blocker.md). That is what is known:
        an automatic cancel from here was followed by an unusable control. It
        is not a claim about menus being closed some other way.

        So a refusal now leaves the menu exactly where it is. The caller is
        told it is open; what to do about it belongs to whoever can see the
        screen, not to the step that failed.
        """
        self._select = None

    def _open_select(self, obs, action):
        """Open a popup's menu and stop there.

        Stopping is the point. What the menu contains is not knowable until it
        is open, so this buys exactly one observation's worth of knowledge and
        nothing else: it does not choose, and it does not report what it found,
        because a reading taken here would be a reading the caller never saw.
        """
        el = self._element(obs, action)
        role = _attr(el, "AXRole")
        if role not in self.SELECTABLE_ROLES:
            raise NotStarted(f"선택지를 가진 컨트롤이 아닙니다: {role}")
        if _attr(el, "AXEnabled") is False:
            raise NotStarted("이 컨트롤은 지금 쓸 수 없습니다")
        busy = self._menu_slot_busy()
        if busy:
            raise NotStarted(busy)
        if _aborted_now():
            raise NotStarted("사용자가 중단한 상태입니다")
        # A popup posts its menu while its app is frontmost. We do not make
        # that true — raising the app is the thing this driver no longer does —
        # so the attempt is made as-is and the refusal names the reason.
        was_front = self._frontmost(obs.pid)
        before = self._app_menus(obs.pid)
        opened = AXUIElementPerformAction(el, "AXShowMenu")
        if opened == AX_ACTION_UNSUPPORTED:
            # Provably nothing happened — this element has no AXShowMenu — so
            # trying the other way is not a second attempt at the same thing.
            opened = AXUIElementPerformAction(el, "AXPress")
        if opened == AX_CANNOT_COMPLETE:
            # A timeout is not a failure: the callback may have done the work
            # and simply not returned in time. Pressing again "to be safe" is
            # how one open becomes two — the same ambiguous-duplicate bug that
            # `click` already refuses to commit (peer review).
            raise _AfterOpening(
                "선택지를 여는 동작이 시간 내에 끝나지 않았습니다 — "
                "열렸을 수도 있어 다시 누르지 않았습니다", "unknown")
        if opened != 0:
            # Anything else is unexplained, and unexplained is not "nothing
            # happened".
            raise _AfterOpening(f"선택지를 열지 못했습니다 ({opened})", "unknown")
        time.sleep(0.4)                       # drawn, then populated
        appeared = [m for m in self._app_menus(obs.pid)
                    if not any(CFEqual(m, b) for b in before)]
        if not appeared:
            raise _AfterOpening(
                "선택지를 열었다는데 목록이 나타나지 않았습니다"
                + ("" if was_front else " (이 앱이 앞에 있지 않습니다)"), "unknown")
        if len(appeared) > 1:
            # They are NOT dismissed. Which of them this control owns is
            # exactly what we could not work out, so cancelling them is
            # cancelling something we cannot attribute.
            raise _AfterOpening(
                f"메뉴가 {len(appeared)}개 열려 어느 것이 이 컨트롤의 "
                f"것인지 알 수 없습니다", "unknown",
                menus_opened=len(appeared), menu_left_open=True,
                option_selected=False, selections_made=0)
        self._select = _OpenSelect(
            obs_id=obs.obs_id, pid=obs.pid, index=action["element_index"],
            control=el, identifier=_attr(el, "AXIdentifier"), role=role,
            label=_elem_label(el), menu=appeared[0])
        # NOT `unverified`. Exactly one menu appeared and we are holding its
        # identity, so what this action set out to do is confirmed — and a
        # caller that treats `unverified` as a failure would stop the very loop
        # this action exists to start (peer review). `opened` says the menu is
        # up; it is deliberately a different word from the one a completed
        # selection answers with.
        return {"opened": True, "reobserve_required": True,
                "note": "선택지를 열었습니다 — 다시 관측한 뒤 그 화면에서 고르세요"}

    def _select_custom_option(self, obs, action):
        """Choose from the menu THIS driver opened, as the caller last saw it.

        Every refusal below happens before anything is pressed, so the count of
        selections made is zero — the screen is only ever left with our own
        menu closed. What it will not do is fix the situation: a control that
        moved, a menu that was replaced, or a list that changed since the
        caller looked are all reasons to go back and look again, not reasons to
        open something a second time.
        """
        want = action.get("option_label")
        value = action.get("value")
        if not isinstance(want, str) or not want:
            raise NotStarted("select_custom_option 에 option_label 이 필요합니다")
        if value is not None and value != want:
            raise NotStarted(
                f"이 화면의 선택지에는 값이 따로 없습니다 — value({value!r}) 와 "
                f"option_label({want!r}) 이 달라 무엇을 고를지 알 수 없습니다")
        pending = self._select
        if pending is None:
            raise NotStarted("열어 둔 선택지가 없습니다 — open_select 로 먼저 여세요")
        # Two kinds of refusal, and the difference is whether the thing we
        # pinned is still there.
        #
        #   keep  — the pin is still good and the caller can fix its request:
        #           re-observe and name an option that exists. The menu stays
        #           open, so the corrected attempt has something to choose
        #           from.
        #   drop  — what we pinned is not what is on screen any more, so the
        #           reference is worthless. We stop tracking it. We do NOT
        #           close anything: the menu belongs to the screen now, and a
        #           cancel here is what used to leave the control unusable.
        #
        # Neither presses anything. Selections made: zero, both ways.
        def keep(why):
            return _AfterOpening(why, "not_started", menu_open=True,
                                 selections_made=0, pin_kept=True,
                                 retry_after_reobserve=True)

        def drop(why):
            self._forget_pin()
            return _AfterOpening(why, "not_started", menu_open=None,
                                 selections_made=0, pin_kept=False,
                                 retry_after_reobserve=False)

        if pending.options_obs_id != obs.obs_id or pending.options is None:
            # Opened, and then chosen from without looking. The options this
            # choice names came from nowhere the caller was shown — but the
            # menu is fine, so looking again is all this needs.
            raise keep("선택지를 연 뒤 화면을 다시 관측하지 않았습니다 — "
                       "관측한 목록에서만 고를 수 있습니다")

        index = action.get("element_index")
        if index != pending.index:
            raise keep(f"열어 둔 선택지는 {pending.index} 번 요소의 것입니다")
        el = self._element(obs, action)
        if not CFEqual(el, pending.control):
            raise drop("선택지를 연 컨트롤이 그 자리에 없습니다")
        if _attr(el, "AXIdentifier") != pending.identifier:
            raise drop("컨트롤의 식별자가 바뀌었습니다")
        if _elem_label(el) != pending.label:
            raise drop("컨트롤의 이름이 바뀌었습니다")
        if obs.pid != pending.pid:
            raise drop("선택지를 연 앱과 고르려는 앱이 다릅니다")
        if not self._menu_is_ours(pending.pid, pending):
            raise drop("열어 둔 선택지 목록이 사라졌거나 다른 목록으로 바뀌었습니다")
        if _attr(el, "AXEnabled") is False:
            # The control may be enabled again in a moment, and the menu we
            # pinned is still the right menu, so this is not a broken pin.
            raise keep("이 컨트롤은 지금 쓸 수 없습니다")
        now = self._menu_options(pending.menu)
        if now != pending.options:
            # Not "the item we want is still there" — the whole list. An item
            # appearing above the one we mean moves nothing we can see, and is
            # exactly how a choice lands on the wrong row. The menu is still
            # ours, so a fresh observation refreshes the list and the next
            # attempt can be right.
            raise keep("관측한 뒤 선택지 목록이 달라졌습니다")
        items = [it for it in (_attr(pending.menu, "AXChildren") or [])
                 if _attr(it, "AXTitle") == want]
        if not items:
            here = ", ".join(o["label"] for o in now[:12]) or "(없음)"
            raise keep(f"'{want}' 라는 선택지가 없습니다 — 있는 것: {here}")
        if len(items) > 1:
            raise keep(f"'{want}' 라는 선택지가 {len(items)}개라 "
                       f"어느 것인지 알 수 없습니다")
        if _attr(items[0], "AXEnabled") is False:
            raise keep(f"'{want}' 는 지금 고를 수 없습니다")
        # Checked HERE, with the menu on screen and the press one line away:
        # the whole point of the stop is that it lands between the thing being
        # offered and the thing being done.
        if _aborted_now():
            raise keep("고르기 직전에 사용자가 중단했습니다")

        menu, role, identity = pending.menu, pending.role, pending.identifier
        self._forget_pin()                    # spent, whatever happens next
        # Recorded BEFORE the press, not after. If the press fails we may not
        # be able to say whether the menu is still up, and a menu this driver
        # opened must never be described to the next observation as closed
        # (peer review). The next observation drops this by itself once the
        # menu is really gone.
        self._spent_menu = (obs.pid, el, menu)
        pressed = AXUIElementPerformAction(items[0], "AXPress")
        if pressed != 0 and pressed != AX_CANNOT_COMPLETE:
            # A backend error is not "no effect". The press was ALREADY
            # attempted, so how many selections happened is not something this
            # code knows — claiming zero here would be inventing evidence
            # (peer review). Only the attempt is certain; whether the menu is
            # still up is re-read rather than assumed, and left out when it
            # cannot be read.
            facts = {'press_attempted': True, 'pin_kept': False}
            try:
                facts['menu_left_open'] = any(
                    CFEqual(menu, m) for m in self._app_menus(obs.pid))
            except Exception:
                pass                          # unknown stays unsaid
            raise _AfterOpening(f"'{want}' 를 고르지 못했습니다 ({pressed})",
                                "unknown", **facts)
        time.sleep(0.3)
        still_open = any(CFEqual(menu, m) for m in self._app_menus(obs.pid))
        if not still_open:
            self._spent_menu = None           # it closed itself, as menus do
        reads_as = self._reread_selection(obs.pid, role, identity, el)
        if reads_as == want:
            # Reported, not tidied. A menu that stays up after a successful
            # choice is a fact about the screen the caller has to know, and
            # this driver does not cancel it (see `_forget_pin`).
            out = {"selected": want}
            if still_open:
                out["menu_left_open"] = True
                out["note"] = ("고른 뒤에도 메뉴가 남아 있습니다 — 자동으로 "
                               "닫지 않았습니다")
            return out
        # Pressed, and the control does not read back as asked. Not "did not
        # happen" — it happened and did not take, or this control names its
        # value differently from its menu.
        return {"unverified": "selection_unconfirmed", "selected": want,
                "reads_as": reads_as, "menu_left_open": still_open,
                "note": f"'{want}' 를 눌렀는데 컨트롤은 {reads_as!r} 로 읽힙니다"}

    def _select_option(self, obs, action):
        """Choose a named option from a popup, and check that it took.

        The real mechanism is open-menu → read → press the item, which is the
        path a person takes. Every step of it is checked: the control is one
        that HAS options, the menu actually opened, the wanted option is there
        and enabled and unambiguous, and afterwards the control reads back as
        the thing that was asked for.

        The menu is closed on every failure after it opened. Leaving one up
        blocks the app for whatever runs next, and Escape — the usual way to
        dismiss one — is the key this system will not press (see `_press_key`).
        """
        el = self._element(obs, action)
        want = action.get("option_label")
        value = action.get("value")
        if not isinstance(want, str) or not want:
            raise NotStarted("select_option 에 option_label 이 필요합니다")
        if value is not None and value != want:
            # A native menu item has a title and nothing else — there is no
            # value space behind it. Accepting a different `value` would be
            # promising a distinction this surface does not have.
            raise NotStarted(
                f"이 화면의 선택지에는 값이 따로 없습니다 — value({value!r}) 와 "
                f"option_label({want!r}) 이 달라 무엇을 고를지 알 수 없습니다")
        role = _attr(el, "AXRole")
        # Captured BEFORE anything is pressed, because the control itself may
        # not survive: measured on Finder's view popup, choosing an option
        # rebuilds the toolbar and the handle we were holding reads back as
        # `None`. Verifying through it would report every successful selection
        # as unconfirmed.
        # Only what does NOT move with the value. Finder's view popup is
        # described as "column view" and becomes "icon view" the moment you
        # change it, so a description is a name for the current answer rather
        # than for the control — the same trap a calculator display set when it
        # identified itself as its own readout.
        identity = _attr(el, "AXIdentifier")
        if role not in self.SELECTABLE_ROLES:
            raise NotStarted(f"선택지를 가진 컨트롤이 아닙니다: {role}")
        if _attr(el, "AXEnabled") is False:
            raise NotStarted("이 컨트롤은 지금 쓸 수 없습니다")
        busy = self._menu_slot_busy()
        if busy:
            raise NotStarted(busy)

        # A popup posts its menu while its app is frontmost. We do not make
        # that true — raising the app is the thing this driver no longer does —
        # so the attempt is made as-is and the refusal names the reason.
        was_front = self._frontmost(obs.pid)
        # CFEqual, not `id()`. Each AX call can hand back a NEW wrapper around
        # the same accessibility element, so comparing wrapper identities
        # compares nothing about the menus (peer review).
        before = self._app_menus(obs.pid)
        opened = AXUIElementPerformAction(el, "AXShowMenu")
        if opened == AX_ACTION_UNSUPPORTED:
            # Provably nothing happened — this element has no AXShowMenu — so
            # trying the other way is not a second attempt at the same thing.
            opened = AXUIElementPerformAction(el, "AXPress")
        if opened == AX_CANNOT_COMPLETE:
            # A timeout is not a failure: the callback may have done the work
            # and simply not returned in time. Pressing again "to be safe" is
            # how one open becomes two — the same ambiguous-duplicate bug that
            # `click` already refuses to commit (peer review).
            raise _AfterOpening(
                "선택지를 여는 동작이 시간 내에 끝나지 않았습니다 — "
                "열렸을 수도 있어 다시 누르지 않았습니다", "unknown")
        if opened != 0:
            # Anything else is unexplained, and unexplained is not "nothing
            # happened".
            raise _AfterOpening(f"선택지를 열지 못했습니다 ({opened})", "unknown")
        time.sleep(0.4)                       # the menu is drawn, then populated

        opened_menus = [m for m in self._app_menus(obs.pid)
                        if not any(CFEqual(m, b) for b in before)]
        if not opened_menus:
            # The action reported success and nothing appeared. Whether the UI
            # moved is precisely what we cannot say — something was pressed
            # (peer review).
            raise _AfterOpening(
                "선택지를 열었다는데 목록이 나타나지 않았습니다"
                + ("" if was_front else " (이 앱이 앞에 있지 않습니다)"), "unknown")
        if len(opened_menus) > 1:
            # Taking the first would be choosing at random which menu this
            # control owns (peer review) — and so would cancelling them all.
            # Nothing is dismissed here for the same reason.
            raise _AfterOpening(
                f"메뉴가 {len(opened_menus)}개 열려 어느 것이 이 컨트롤의 "
                f"것인지 알 수 없습니다", "unknown",
                menus_opened=len(opened_menus), menu_left_open=True,
                option_selected=False, selections_made=0)
        menu = opened_menus[0]
        # Tracked from the moment there is exactly one menu and we know which.
        # Waiting until just before the press left every refusal in between
        # describing an open menu as a closed popup (peer review).
        self._spent_menu = (obs.pid, el, menu)

        def refuse(why):
            """Refuse, and leave the menu exactly where this action put it.

            This used to `AXCancel` the menu and then report `not_started`
            when the cancel succeeded. Both halves were wrong. In the one
            measured case, an automatic cancel from here was followed by the
            control refusing to post a menu again — every later AXShowMenu
            returned success and nothing appeared
            (native-select-blocker.md). And `not_started` was a false claim:
            THIS action opened the menu, so the screen is not where it was,
            whatever happened to the selection.

            So: nothing is dismissed, the effect is `unknown`, and the facts
            that are actually known travel with it.
            """
            return _AfterOpening(why, "unknown", menu_opened_by_this_action=True,
                                 menu_left_open=True, option_selected=False,
                                 selections_made=0)

        def refuse_after_press(why):
            """Same, for a failure that came AFTER the item was pressed.

            Nothing here may say how many selections happened: the press was
            attempted and its outcome is precisely what the error leaves
            unknown (peer review).
            """
            facts = {'menu_opened_by_this_action': True, 'press_attempted': True}
            try:
                facts['menu_left_open'] = any(
                    CFEqual(menu, m) for m in self._app_menus(obs.pid))
            except Exception:
                pass
            return _AfterOpening(why, "unknown", **facts)

        items = []
        for it in (_attr(menu, "AXChildren") or []):
            title = _attr(it, "AXTitle")
            if not title:
                continue                      # separators
            items.append((title, it, _attr(it, "AXEnabled") is not False,
                          _attr(it, "AXMenuItemMarkChar")))
        matches = [i for i in items if i[0] == want]
        if not matches:
            here = ", ".join(t for t, _, _, _ in items[:12]) or "(없음)"
            raise refuse(f"'{want}' 라는 선택지가 없습니다 — 있는 것: {here}")
        if len(matches) > 1:
            raise refuse(f"'{want}' 라는 선택지가 {len(matches)}개라 "
                         f"어느 것인지 알 수 없습니다")
        title, item, enabled, _mark = matches[0]
        if not enabled:
            raise refuse(f"'{want}' 는 지금 고를 수 없습니다")

        # The menu has been on screen for a moment, and the person may have
        # stopped the run in it. Checked again HERE rather than only before it
        # opened: the whole point of the stop is that it lands between the
        # thing being offered and the thing being done (peer review).
        if _aborted_now():
            raise refuse("선택지를 여는 사이에 사용자가 중단했습니다")
        pressed = AXUIElementPerformAction(item, "AXPress")
        if pressed != 0 and pressed != AX_CANNOT_COMPLETE:
            # A backend error is not "no effect", and the item was pressed.
            raise refuse_after_press(f"'{want}' 를 고르지 못했습니다 ({pressed})")
        time.sleep(0.3)
        # The menu normally closes itself when an item is pressed. If this one
        # did not, it is still this control's menu — reported, never dismissed.
        still_open = any(CFEqual(menu, m) for m in self._app_menus(obs.pid))
        if not still_open:
            self._spent_menu = None
        now = self._reread_selection(obs.pid, role, identity, el)
        if now == want:
            out = {"selected": want}
            if still_open:
                out["menu_left_open"] = True
            return out
        # Pressed, and the control does not read back as asked. The effect is
        # not "did not happen" — it happened and did not take, or the control
        # names its value differently from its menu.
        return {"unverified": "selection_unconfirmed",
                "selected": want, "reads_as": now,
                "menu_left_open": still_open,
                "note": f"'{want}' 를 눌렀는데 컨트롤은 {now!r} 로 읽힙니다"}

    def _reread_selection(self, pid, role, identity, control=None):
        """What the control reads as now, found again rather than remembered.

        The handle from before the press may be dead, so the control is located
        afresh — but "afresh" must still mean the SAME control. Three things
        can be true after an action, and they are not interchangeable:

          * the control is still there — then it is the only thing that may
            say what its value is, and it is found by CF identity rather than
            by description, because a popup renames itself to whatever it now
            holds;
          * the control is gone but its identifier uniquely names one — then
            that one speaks for it;
          * neither — then there is nothing here that can honestly answer, and
            the answer is None.

        What must never happen is the third case answering out of some other
        popup that happens to read as what was asked for. That fallback fired
        exactly when the control we acted on had vanished, which is precisely
        when a wrong answer is most likely (peer review) — the same shape as
        the ancestor re-targeting bug.

        Passing the original control also buys capability rather than only
        safety: an app with several unidentified popups used to be unverifiable
        on principle, and now verifies fine as long as the one we pressed is
        still alive.
        """
        if identity == "":
            identity = None
        same_id, alive = [], []
        truncated = [False]
        visited = [0]

        def read(node, name, *, optional=False):
            error, value = AXUIElementCopyAttributeValue(node, name, None)
            if error == 0 and value is not None:
                if name != "AXChildren" and not isinstance(value, str):
                    truncated[0] = True
                    return None
                return value
            if optional and error in (-25205, -25212):
                return None
            truncated[0] = True
            return None

        def walk(node, d=0):
            if alive:
                return                      # found the one that matters
            if d > 12 or visited[0] >= 3000:
                truncated[0] = True
                return
            visited[0] += 1
            # Bounded by depth and a node budget, NOT by "we have seen enough
            # controls of this role". That old cap stopped the walk before it
            # could reach the control we were holding, which turned a decisive
            # CF-identity match into "not found" in any app with more than a
            # handful of popups (measured on Finder).
            # Optional, and the distinction is the whole three-way contract:
            # an element that HAS no AXRole is simply not the one we want,
            # while an element we could not ASK is a hole in the search. Read
            # non-optionally, the first was recorded as the second — and since
            # the application element itself has no role, every walk began
            # truncated and the identifier path below could never fire.
            if read(node, "AXRole", optional=True) == role:
                if control is not None and CFEqual(node, control):
                    alive.append(node)
                if identity is not None and read(node, "AXIdentifier", optional=True) == identity:
                    same_id.append(node)
            children = read(node, "AXChildren", optional=True)
            if children is None:
                return
            if not isinstance(children, (list, tuple, NSArray)):
                truncated[0] = True
                return
            if len(children) > 60:
                truncated[0] = True
            for child in children[:60]:
                walk(child, d + 1)

        walk(AXUIElementCreateApplication(pid))
        # The control we acted on, still in the tree. Nothing else can outrank
        # this: it is not "a control like the one we pressed", it IS the one.
        # Truncation does not matter here — we found it, and a second copy of
        # the same element is not a thing that exists.
        if alive:
            return _attr(alive[0], "AXValue")
        # It is gone. An identifier NAMES a control, so it may still speak for
        # it — but only if it names exactly one, and only if the walk actually
        # saw the whole app: one of two looks exactly like one of one.
        if identity is not None and not truncated[0] and len(same_id) == 1:
            return _attr(same_id[0], "AXValue")
        # Gone, and nothing names it. "Some other popup of this role reads as
        # what we asked for" is not evidence about the control that vanished.
        return None

    def _scroll(self, element, direction, pages):
        """Move the scroll bar the app already exposes.

        A wheel event has no address: it goes to whatever is under a pointer
        position we made up, in a process we named. A scroll bar is a real
        element with a real value, so this asks the app to be where we want
        instead of miming a gesture at it.

        `AXValue` on a scroll bar is 0.0 at the top and 1.0 at the bottom. A
        page is taken as a tenth, matching the ten lines the wheel event used.
        """
        step = 0.1 * pages * (-1 if direction in ("up", "left") else 1)
        want_vertical = direction in ("up", "down")
        bar = self._scroll_bar(element, want_vertical)
        if bar is None:
            raise NotStarted(
                f"{'세로' if want_vertical else '가로'} 스크롤 막대를 찾지 못했습니다 — "
                "이 요소는 그 방향으로 스크롤되지 않습니다")
        settable = AXUIElementIsAttributeSettable(bar, "AXValue", None)
        if not (isinstance(settable, tuple) and settable[0] == 0 and settable[1] is True):
            raise NotStarted("스크롤 막대의 값을 바꿀 수 없습니다")
        current = _attr(bar, "AXValue")
        if not isinstance(current, (int, float)):
            raise NotStarted("스크롤 막대의 현재 위치를 읽지 못했습니다")
        target = min(1.0, max(0.0, float(current) + step))
        if _aborted_now():
            raise NotStarted("사용자가 중단했습니다 — 스크롤하지 않았습니다")
        err = AXUIElementSetAttributeValue(bar, "AXValue", target)
        if err != 0:
            raise NotStarted(f"스크롤에 실패했습니다 ({err})")
        return {"scrolled_from": round(float(current), 4), "scrolled_to": round(target, 4)}

    @staticmethod
    def _scroll_bar(element, vertical):
        """The scroll bar that governs this element, looking outward.

        The observed element is usually the content, not the scroller, so this
        walks up to the enclosing scroll area rather than expecting the caller
        to have named one.
        """
        node, seen = element, 0
        while node is not None and seen < 12:
            seen += 1
            if _attr(node, "AXRole") == "AXScrollArea":
                for child in (_attr(node, "AXChildren") or [])[:40]:
                    if _attr(child, "AXRole") != "AXScrollBar":
                        continue
                    orientation = _attr(child, "AXOrientation")
                    if orientation == ("AXVerticalOrientation" if vertical
                                       else "AXHorizontalOrientation"):
                        return child
                return None
            node = _attr(node, "AXParent")
        return None

    # ------------------------------------------------------------ L2 dispatch

    def handle(self, msg: dict) -> dict:
        """Single L2 message in, single L2 message out."""
        mtype = msg.get("type")
        if mtype == "list_targets":
            return self.list_targets()
        if mtype == "observe":
            return self.observe(msg["target"], msg.get("mode", "tree_diff"))
        if mtype == "document_text":
            return self.document_text(msg["target"], msg.get("document_token"),
                                      max_utf16_units=msg.get("max_utf16_units", 1000000))
        if mtype == "act":
            # Keywords, not positions: `expect` sits between `action` and
            # `risk_class`, and a positional call would have quietly passed the
            # risk class as the expectation.
            return self.act(msg["obs_id"], msg["target"], msg["action"],
                            expect=msg.get("expect"),
                            risk_class=msg.get("risk_class", "none"),
                            intent_text=msg.get("intent_text", ""),
                            approved=msg.get("approved", False))
        if mtype == "probe":
            return self.probe(msg["obs_id"], msg["target"],
                              msg["element_index"])
        if mtype == "selection":
            return self.selection(msg["target"])
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
                r = self.act(obs_id, target, action,
                             risk_class=msg.get("risk_class", "none"),
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
