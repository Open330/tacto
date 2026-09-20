"""The three drivers have to answer the same questions the same way.

NLC has three ways to reach a screen: the in-process `Driver`, the signed
helper over an inherited socket, and the same helper over its own socket. They
are supposed to be one L2 protocol with different transports underneath.

They were not. Python sent `expect` with every action and the launcher let it
through; the helper's `handle()` called `act(obs_id, target, action)` and never
read it. `probe` and `selection` came back `unknown_message`. So "stop if the
selection moved", "stop if a dialog appeared" and "read the current value" held
against the in-process driver — which is what the scenarios exercised — and did
not exist on the signed path, which is the recommended one. `scroll` was in the
launcher's allowlist and had no branch in the helper at all.

A test suite that only ever drove one transport could not see any of that, so
this file drives each of them through the same contract. Where the GUI is not
available it says so and stops, rather than reporting a contract failure it did
not observe.

    uv run python tests/test_helper_contract.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CALC = "com.apple.calculator"

# Nothing is imported from `nlc.driver` at module scope on purpose. It needs
# PyObjC, so a top-level import turns `unittest discover` into an import error
# on any machine without it — a Linux box, or the browser-only venv — and an
# import error is indistinguishable from a broken contract (peer finding).


def _drivers():
    """Every transport this machine can currently offer, with its name."""
    from nlc.driver import Driver
    out = [("in-process", Driver())]
    try:
        from nlc.helper import InheritedHelperDriver
        if InheritedHelperDriver.available():
            out.append(("inherited-helper", InheritedHelperDriver()))
    except Exception as e:                       # pragma: no cover - env only
        print(f"  (inherited-helper 사용 불가: {e})")
    return out


def _candidates(tree):
    from nlc.general import candidates
    return candidates(tree)


def _observe(d):
    """Observe, or say plainly why not.

    `obs["tree"]` on an error dict raises KeyError, which reports a contract
    failure when the truth is that nothing could be seen — and the reason the
    driver gave is exactly what a reader needs.
    """
    obs = d.observe(CALC, "tree_full")
    assert obs.get("type") == "observation", \
        f"관측 실패: {obs.get('error')} {str(obs.get('detail'))[:60]}"
    return obs


def _reset_key(tree):
    """The calculator's clear key, whatever it is called right now.

    "All Clear" when nothing is entered, "Clear" once something is — the same
    state-dependent label the recipes have always had to allow for.
    """
    return next((c for c in _candidates(tree)
                 if c["label"] in ("All Clear", "Clear")), None)


CHECKS = []


class Skipped(Exception):
    """This check could not run, and says so instead of passing.

    A check that returns early reads exactly like a check that passed, which
    is how two of the select checks below sat green while testing nothing
    (peer review). Anything that cannot run raises this and is counted
    separately — never towards "통과".
    """


def skip(why):
    raise Skipped(why)


def check(name):
    def wrap(fn):
        CHECKS.append((name, fn))
        return fn
    return wrap


@check("expect 가 맞으면 통과하고 틀리면 거부한다")
def _expect_round_trip(d):
    obs = _observe(d)
    c = _reset_key(obs["tree"])
    assert c, "지우기 버튼을 찾지 못했습니다"
    act = {"name": "click", "element_index": c["element_index"]}
    true_claim = d.act(obs["obs_id"], CALC, act,
                       expect={"role": c["role"], "label": c["match"]})
    assert true_claim["type"] == "acted", f"참인 주장이 거부됨: {true_claim}"

    obs2 = _observe(d)
    c2 = _reset_key(obs2["tree"])
    false_claim = d.act(obs2["obs_id"], CALC,
                        {"name": "click", "element_index": c2["element_index"]},
                        expect={"role": c2["role"], "label": "Equals"})
    assert false_claim.get("error") == "target_changed", false_claim
    # The Python side reads this to decide whether re-running is even allowed.
    assert false_claim.get("effect") == "not_started", false_claim


@check("expect.role 은 라벨이 아니라 요소의 역할과 비교한다")
def _role_is_a_role(d):
    obs = _observe(d)
    c = _reset_key(obs["tree"])
    r = d.act(obs["obs_id"], CALC,
              {"name": "click", "element_index": c["element_index"]},
              expect={"role": "TextField", "label": c["match"]})
    assert r.get("error") == "target_changed", f"역할이 달라도 통과함: {r}"


@check("probe 가 트리 없이 한 요소의 지금 값을 돌려준다")
def _probe_one_element(d):
    obs = _observe(d)
    readable = [x for x in _candidates(obs["tree"]) if "read" in x["can"]]
    assert readable, "읽을 수 있는 요소가 없습니다"
    r = d.probe(obs["obs_id"], CALC, readable[0]["element_index"])
    assert r.get("type") == "probe", f"probe 를 모릅니다: {r}"
    assert isinstance(r.get("line"), str) and r["line"], r


@check("probe 는 오래된 관찰을 거부한다")
def _probe_is_fresh(d):
    obs = _observe(d)
    _observe(d)                                  # the first one is now stale
    r = d.probe(obs["obs_id"], CALC, 0)
    assert r.get("error") == "stale_observation", r


@check("selection 이 짧고 안정된 지문을 돌려준다")
def _selection_digest(d):
    a = d.selection(CALC)
    assert a.get("type") == "selection", f"selection 을 모릅니다: {a}"
    assert a.get("digest"), a
    b = d.selection(CALC)
    assert a["digest"] == b["digest"], "가만히 있는데 지문이 바뀝니다"


@check("expect.selection 이 다르면 같은 버튼이라도 거부한다")
def _selection_is_the_target(d):
    obs = _observe(d)
    c = _reset_key(obs["tree"])
    r = d.act(obs["obs_id"], CALC,
              {"name": "click", "element_index": c["element_index"]},
              expect={"role": c["role"], "label": c["match"],
                      "selection": "이 화면의 것이 아닌 지문"})
    assert r.get("error") == "target_changed", f"선택이 달라도 통과함: {r}"
    assert r.get("effect") == "not_started", r


@check("모르는 동작은 거부하고, 실행 전임을 말한다")
def _unknown_action(d):
    obs = _observe(d)
    r = d.act(obs["obs_id"], CALC, {"name": "정체불명", "element_index": 0})
    assert r["type"] == "error", r
    assert r.get("effect") == "not_started", f"실행 여부를 말하지 않습니다: {r}"


@check("scroll 은 세 드라이버 모두가 알거나 모두가 모른다")
def _scroll_is_agreed(d):
    obs = _observe(d)
    r = d.act(obs["obs_id"], CALC,
              {"name": "scroll", "element_index": 0, "direction": "down"})
    # Either it worked, or it failed for a reason about THIS screen — never
    # "unknown action", which would mean this transport does not have it.
    assert "unknown action" not in str(r.get("detail", "")), \
        f"이 드라이버는 scroll 을 모릅니다: {r}"


@check("blocked 과 모르는 위험 등급은 보내지 않고 거절한다")
def _risk_classes_are_honoured(d):
    obs = _observe(d)
    c = _reset_key(obs["tree"])
    act = {"name": "click", "element_index": c["element_index"]}
    # `blocked` means nobody could say what this would do, so there is no
    # approval that fixes it and nothing to ask. It used to travel to the
    # helper as though it had said `none`.
    r = d.act(obs["obs_id"], CALC, act, risk_class="blocked", approved=True)
    assert r["type"] == "error" and r.get("effect") == "not_started", r
    r2 = d.act(obs["obs_id"], CALC, act, risk_class="아무거나", approved=True)
    assert r2["type"] == "error", f"모르는 등급이 통과함: {r2}"
    assert r2.get("effect") == "not_started", r2
    # And the ones it does know still behave.
    r3 = d.act(obs["obs_id"], CALC, act, risk_class="handoff")
    assert r3["type"] == "handoff_request", r3
    r4 = d.act(obs["obs_id"], CALC, act, risk_class="confirm")
    assert r4["type"] == "confirm_request", r4


@check("정수가 아닌 인덱스는 실행 전에 거절된다")
def _index_is_whole(d):
    obs = _observe(d)
    for bad in (1.5, True, "2"):
        r = d.act(obs["obs_id"], CALC, {"name": "click", "element_index": bad})
        assert r["type"] == "error", f"{bad!r} 가 인덱스로 통과함: {r}"
        assert r.get("effect") == "not_started", r


@check("read 는 요소를 지정해도 되고 안 해도 된다")
def _read_index_is_optional(d):
    obs = _observe(d)
    readable = [x for x in _candidates(obs["tree"]) if "read" in x["can"]]
    assert readable, "읽을 수 있는 요소가 없습니다"
    # Naming one is how a batch answers with the screen AFTER its earlier
    # actions. The launcher used to refuse the field outright, so this worked
    # in-process and was unreachable through the signed path.
    named = d.act(obs["obs_id"], CALC,
                  {"name": "read", "element_index": readable[0]["element_index"]})
    assert named["type"] == "acted", f"요소를 지정한 read 가 거부됨: {named}"
    obs2 = _observe(d)
    bare = d.act(obs2["obs_id"], CALC, {"name": "read"})
    assert bare["type"] == "acted", f"요소 없는 read 가 거부됨: {bare}"


NAVIGATION_KEYS = ("Tab", "shift+Tab", "Up", "Down", "Left", "Right")


@check("어떤 키도 합성하지 않는다 — 여섯 개짜리 예외도 없다")
def _no_key_is_synthesised(d):
    """The navigation keys used to be the one allowed kind of synthetic input.

    They are gone with the rest of it. A key press names no element: it goes
    wherever focus happens to be, which is a place this process does not own
    and cannot check afterwards — and getting focus there first is exactly the
    theft this project exists to avoid. So every key is refused, the six that
    were once allowed included, and the refusal says what to use instead.

    This check was the inverse of itself until now, and it ran only inside the
    signed launcher, so nothing noticed for three commits.
    """
    obs = _observe(d)
    for key in NAVIGATION_KEYS:
        r = d.act(obs["obs_id"], CALC, {"name": "press_key", "key": key})
        assert r["type"] == "error", f"{key} 가 통과함: {r}"
        assert r.get("effect") == "not_started", f"{key}: {r}"
        # A refusal that does not say what to do instead reads as a defect.
        assert "click" in str(r.get("detail", "")), \
            f"{key} 거절이 대안을 말하지 않습니다: {r}"


@check("Escape 는 눌러주지 않는다 — 사용자가 중단하는 키다")
def _escape_is_the_users(d):
    obs = _observe(d)
    for key in ("Escape", "shift+Escape"):
        r = d.act(obs["obs_id"], CALC, {"name": "press_key", "key": key})
        assert r["type"] == "error", f"{key} 가 통과함: {r}"
        assert r.get("effect") == "not_started", r
    # And the run is still usable: refusing Escape must not have stopped it.
    after = _observe(d)
    assert after.get("type") == "observation", \
        f"Escape 를 거부하면서 실행까지 멈췄습니다: {after}"


@check("커밋·호출 키와 조합키는 거절된다")
def _only_navigation(d):
    obs = _observe(d)
    # Return commits, space activates, and a modifier combination invokes —
    # which is why this is a named set and not a key primitive.
    for key in ("Return", "Enter", "space", "a", "cmd+q", "ctrl+c",
                "cmd+Tab", "shift+Return", "F1", ""):
        r = d.act(obs["obs_id"], CALC, {"name": "press_key", "key": key})
        assert r["type"] == "error", f"{key!r} 가 통과함: {r}"
        assert r.get("effect") == "not_started", f"{key!r}: {r}"


@check("press_key 는 요소를 지정하지 않는다 — 포커스가 정한다")
def _press_key_has_no_element(d):
    obs = _observe(d)
    r = d.act(obs["obs_id"], CALC,
              {"name": "press_key", "key": "Tab", "element_index": 0})
    # Naming an element would suggest the key goes there, and it does not.
    assert r["type"] == "error", f"element_index 가 붙은 press_key 가 통과함: {r}"


@check("대상이 없는 동작에 요소 기대를 걸면 거절된다")
def _unanswerable_expectation(d):
    """An expectation nothing can check is refused, not quietly dropped.

    `press_key` and `menu` name no element, so `expect.role`/`label` had
    nowhere to be compared — and the comparison was skipped, silently. An
    ignored precondition looks exactly like a met one, which is the failure
    this whole contract exists to stop (peer question).

    `press_key` was the vehicle here until it stopped being an action at all.
    A bare `read` is the remaining one that names no element, so the property
    is checked through that instead of disappearing with the old vehicle.
    """
    for field, value in (("role", "Button"), ("label", "7"),
                         ("attribute", "Title"), ("enabled", True)):
        obs = _observe(d)          # each action spends its observation
        r = d.act(obs["obs_id"], CALC, {"name": "read"}, expect={field: value})
        assert r["type"] == "error", \
            f"확인할 수 없는 expect.{field} 가 통과함: {r}"
        assert r.get("effect") == "not_started", r
    # The ones that ARE answerable without an element still work.
    obs = _observe(d)
    ok = d.act(obs["obs_id"], CALC, {"name": "read"}, expect={"no_modal": True})
    assert ok["type"] == "acted", f"no_modal 만 건 expect 가 거부됨: {ok}"


@check("거절된 키는 캐럿도 움직이지 않는다 (TextEdit 임시 문서)")
def _refused_keys_move_nothing(d):
    """A refusal that half-happened would be the worst of both.

    This check used to read the caret back to prove a navigation key had
    ARRIVED — "posted to the pid" is not "reached the focused element", and AX
    promises nothing there. Keys are not sent at all now, so the question turns
    around: the refusal must be complete. `effect: not_started` is a claim
    about the world, and the caret is somewhere the claim can be checked.

    Nothing leaves this machine and no file is saved.
    """
    from nlc.driver import Driver, _attr
    from ApplicationServices import AXUIElementCreateApplication

    probe = Driver()
    pid = probe._resolve("com.apple.TextEdit", launch=False)
    if pid is None:
        return                      # nothing to type into here
    obs = d.observe("com.apple.TextEdit", "tree_full")
    if obs.get("type") != "observation":
        # The same rule the rest of the suite uses: a screen this machine
        # cannot show may be skipped, and nothing else may. A document the
        # suite closed earlier is the first case, not a broken contract.
        why = obs.get("error")
        assert why in ("gui_session_unavailable", "no_window_content",
                       "empty_tree"), f"TextEdit 관측 실패: {obs}"
        return

    def caret():
        app = AXUIElementCreateApplication(pid)
        el = _attr(app, "AXFocusedUIElement")
        if el is None:
            return None
        return _attr(el, "AXSelectedTextRange")

    where = caret()
    if where is None:
        return          # no editable focus here; the delivery checks still ran

    for key in ("Right", "Right", "Left"):
        r = d.act(obs["obs_id"], "com.apple.TextEdit",
                  {"name": "press_key", "key": key})
        assert r["type"] == "error", f"{key} 가 통과함: {r}"
        assert r.get("effect") == "not_started", f"{key}: {r}"
        obs = d.observe("com.apple.TextEdit", "tree_full")
        assert caret() == where, (
            f"{key} 를 거절했다면서 캐럿이 {where} 에서 {caret()} 로 "
            f"움직였습니다 — not_started 가 사실이 아닙니다")


FINDER = "com.apple.finder"


def _bring_forward(bundle, d, timeout=4.0):
    """Activate an app the way a person would, and wait until it really is front.

    Only the harness does this. The driver refuses instead, and says so — a
    background window is not somewhere a menu can be opened, and pretending
    otherwise is the synthetic activation this project removed.
    """
    import time
    from AppKit import NSWorkspace, NSRunningApplication
    pid = d._resolve(bundle, launch=False)
    if pid is None:
        return False
    workspace = NSWorkspace.sharedWorkspace()
    front = workspace.frontmostApplication()
    if front is not None and int(front.processIdentifier()) == pid:
        return True
    app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    if app is None:
        return False
    app.activateWithOptions_(0)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        front = workspace.frontmostApplication()
        if front is not None and int(front.processIdentifier()) == pid:
            return True
        time.sleep(0.05)
    return False


def _view_popup(d):
    """Finder's view popup, when this machine is set up to answer about it.

    Checked BEFORE anything is pressed, not excused afterwards. The conditions
    are real limits of the contract, not of this check: with two Finder windows
    the app has two of these popups and no identifier to tell them apart, so a
    selection cannot be attributed and the driver says so. That is correct
    behaviour, and a check that demanded confirmation here would be demanding
    something the surface honestly cannot give.
    """
    from ApplicationServices import AXUIElementCreateApplication
    from nlc.driver import Driver, _attr
    probe = Driver()
    pid = probe._resolve(FINDER, launch=False)
    if pid is None:
        skip("Finder 가 실행 중이 아닙니다")
    # Only real windows. `AXWindows` also carries the desktop, which is an
    # AXScrollArea and has no view popup — counting it made one open window
    # look like two and silently disabled both of these checks (peer review).
    windows = [w for w in (_attr(AXUIElementCreateApplication(pid), "AXWindows") or [])
               if _attr(w, "AXRole") == "AXWindow"]
    if len(windows) != 1:
        skip(f"Finder 창이 {len(windows)}개라 어느 팝업의 값인지 귀속할 수 없습니다")
    # A popup only opens in the app that is in front. The driver refuses in a
    # background app rather than faking the activation, which is the whole
    # point — so the harness does what a person does and brings Finder forward
    # first. Only the harness may; nothing inside the driver does this.
    if not _bring_forward(FINDER, probe):
        skip("Finder 를 앞으로 가져오지 못했습니다 — 팝업은 앞선 앱에서만 열립니다")
    obs = d.observe(FINDER, "tree_full")
    if obs.get("type") != "observation":
        skip(f"Finder 를 관측할 수 없습니다: {obs.get('error')}")
    for line in obs["tree"].splitlines():
        if "PopUpButton" in line and "view" in line:
            return obs, int(line.strip().split()[0])
    skip("이 Finder 창에는 보기 팝업이 없습니다")


def _close_open_menus(pid):
    """Cancel any menu this app has standing.

    A `select_option` that fails partway leaves the menu it opened on screen,
    and the next check then reports "메뉴가 3개 열려" — a failure caused
    entirely by the previous one. One check's wreckage must not be read as the
    next one's defect, and a menu left over someone's Finder is not something
    to leave behind either.

    `AXCancel` is the menu's own action: nothing is synthesised here.
    """
    from ApplicationServices import (AXUIElementCreateApplication,
                                     AXUIElementPerformAction)
    from nlc.driver import _attr
    app = AXUIElementCreateApplication(pid)
    found = []

    def walk(node, depth=0):
        if depth > 6 or len(found) > 8:
            return
        if _attr(node, "AXRole") == "AXMenu":
            found.append(node)
            return                      # its items are not menus to cancel
        for child in (_attr(node, "AXChildren") or [])[:60]:
            walk(child, depth + 1)

    # Only menus hanging off the app's own windows and controls. The menu BAR
    # always has menus in it and cancelling those is neither needed nor ours.
    for window in (_attr(app, "AXWindows") or []):
        walk(window)
    for menu in found:
        AXUIElementPerformAction(menu, "AXCancel")


def _the_only_view_popup(pid):
    """The view popup of Finder's single window, walked here rather than
    borrowed from a driver's cache. Returns None if it is not unique."""
    from ApplicationServices import AXUIElementCreateApplication
    from nlc.driver import _attr
    found = []

    def walk(node, depth=0):
        if depth > 8 or len(found) > 1:
            return
        if _attr(node, "AXRole") == "AXPopUpButton":
            found.append(node)
        for child in (_attr(node, "AXChildren") or [])[:60]:
            walk(child, depth + 1)

    app = AXUIElementCreateApplication(pid)
    for window in (_attr(app, "AXWindows") or []):
        if _attr(window, "AXRole") == "AXWindow":
            walk(window)
    return found[0] if len(found) == 1 else None


@check("선택지는 이름으로 고르고, 고른 대로 됐는지 확인한다")
def _select_option_round_trip(d):
    """Open the menu, press the item, and check the control reads back.

    Native popups do not expose their options until the menu is open —
    measured: `AXChildren` is empty and the menu turns up elsewhere in the app.
    So the real path is the one a person takes, and every step of it is
    checked. Finder's view popup is used because it is reversible: this leaves
    it exactly as it found it.
    """
    obs, idx = _view_popup(d)
    from nlc.driver import Driver, _attr
    probe = Driver()
    pid = probe._resolve(FINDER, launch=False)
    # Read through the control we are about to act on, by CF identity. The
    # identity-free "exactly one popup of this role" fallback could answer out
    # of a DIFFERENT popup once this one was gone (peer review).
    #
    # The handle comes from THIS process's own walk, not from `d._latest` —
    # that is an in-process Driver internal, and reaching for it meant this
    # check crashed with AttributeError on the signed transport, which is the
    # one that ships. `_view_popup` has already established there is exactly
    # one Finder window, so "the popup in it" is an attributable handle.
    control = _the_only_view_popup(pid)
    if control is None:
        skip("보기 팝업 핸들을 이 프로세스에서 찾지 못했습니다")
    start = probe._reread_selection(pid, "AXPopUpButton", None, control)
    if start not in ("as Icons", "as List", "as Columns", "as Gallery"):
        skip(f"이 검사가 아는 보기 팝업이 아닙니다: {start!r}")
    other = "as List" if start != "as List" else "as Icons"
    try:
        r = d.act(obs["obs_id"], FINDER,
                  {"name": "select_option", "element_index": idx,
                   "option_label": other})
        assert r["type"] == "acted", f"{other} 를 고르지 못했습니다: {r}"
        assert r.get("selected") == other, r
        # The selection really happened — checked here, not taken from the
        # driver's word for it.
        landed = probe._reread_selection(pid, "AXPopUpButton", None, control)
        if landed is not None:
            assert landed == other, f"고른 것이 반영되지 않았습니다: {landed!r}"
        # And the claim matches the fact. Confirming something that did not
        # happen is the one answer that must never appear; saying "could not
        # confirm" when the control cannot be re-found is correct, not a
        # failure — several popups on screen and no identifier means there is
        # nothing to attribute the value to.
        if r.get("unverified") is None:
            assert landed == other, \
                f"확인했다고 하는데 실제로는 {landed!r} 입니다"
    finally:
        # Put it back whatever happened. A check that changes someone's Finder
        # and leaves it changed is a check nobody can run twice.
        _close_open_menus(pid)
        try:
            obs2, idx2 = _view_popup(d)
            d.act(obs2["obs_id"], FINDER,
                  {"name": "select_option", "element_index": idx2,
                   "option_label": start})
        except Skipped:
            pass
        _close_open_menus(pid)
    back = probe._reread_selection(pid, "AXPopUpButton", None, control)
    assert back == start, f"원래 보기로 못 되돌렸습니다: {start!r} -> {back!r}"


@check("없는 선택지·모호한 값은 거절하고 무엇이 있는지 말한다")
def _select_option_refusals(d):
    obs, idx = _view_popup(d)
    missing = d.act(obs["obs_id"], FINDER,
                    {"name": "select_option", "element_index": idx,
                     "option_label": "이런 선택지는 없습니다"})
    assert missing["type"] == "error", f"없는 선택지가 통과함: {missing}"
    assert missing.get("effect") == "not_started", missing
    # The refusal names what IS there, like every other target failure here.
    assert "있는 것" in str(missing.get("detail", "")), missing

    obs2, idx2 = _view_popup(d)
    # `value` exists for shape-compatibility with the browser and must agree:
    # a native menu item has a title and nothing behind it.
    clash = d.act(obs2["obs_id"], FINDER,
                  {"name": "select_option", "element_index": idx2,
                   "option_label": "as Columns", "value": "columns"})
    assert clash["type"] == "error", f"값이 어긋나는데 통과함: {clash}"
    assert clash.get("effect") == "not_started", clash


@check("선택지가 없는 컨트롤에는 select_option 이 통하지 않는다")
def _select_option_needs_options(d):
    obs = _observe(d)
    c = _reset_key(obs["tree"])
    r = d.act(obs["obs_id"], CALC,
              {"name": "select_option", "element_index": c["element_index"],
               "option_label": "아무거나"})
    assert r["type"] == "error", f"버튼에 select_option 이 통했습니다: {r}"
    assert r.get("effect") == "not_started", r


@check("화면을 보는 것만으로는 선택지가 열리지 않는다")
def _observing_opens_nothing(d):
    """A closed popup is reported closed, with nothing read out of it.

    This is the whole reason `open_select` exists. A native popup does not
    publish its items until its menu is posted, so an observation that listed
    them would have had to open one — and opening is an action, taken without
    anybody asking for it. The Calculator has no popup at all, which is its own
    answer: `custom_selects` must exist and be empty rather than absent.
    """
    obs = _observe(d)
    selects = obs.get("custom_selects")
    assert selects is not None, \
        f"observe 가 custom_selects 를 말하지 않습니다: {sorted(obs)}"
    assert isinstance(selects, dict), selects
    for index, state in selects.items():
        assert state.get("state") in ("closed", "open", "unsupported"), state
        if state.get("state") != "open":
            assert state.get("options") == [], \
                f"{index} 번은 열려 있지 않은데 선택지를 말합니다: {state}"
        for option in state.get("options") or []:
            # Labels and nothing else. A native menu item has a title and no
            # value behind it, and inventing one would promise a distinction
            # this surface does not have.
            assert set(option) == {"label", "disabled"}, option


@check("열어 둔 목록이 없으면 고를 수 없다")
def _choosing_needs_an_open_list(d):
    """`select_custom_option` may only choose from a list that was observed.

    It does not open anything to find out whether the option exists: the whole
    point of separating the steps is that the caller is shown the list first,
    and a choice made without that is a choice made from nowhere.
    """
    obs = _observe(d)
    c = _reset_key(obs["tree"])
    r = d.act(obs["obs_id"], CALC,
              {"name": "select_custom_option",
               "element_index": c["element_index"], "option_label": "아무거나"})
    assert r["type"] == "error", f"열어 둔 목록 없이 통과했습니다: {r}"
    assert r.get("effect") == "not_started", r


@check("선택지가 없는 컨트롤은 열 수도 없다")
def _opening_needs_a_popup(d):
    obs = _observe(d)
    c = _reset_key(obs["tree"])
    r = d.act(obs["obs_id"], CALC,
              {"name": "open_select", "element_index": c["element_index"]})
    assert r["type"] == "error", f"버튼에 open_select 가 통했습니다: {r}"
    assert r.get("effect") == "not_started", r


@check("사라진 컨트롤의 값을 옆 컨트롤에서 가져오지 않는다")
def _no_borrowed_confirmation(_d):
    """Confirmation must come from the control that was acted on.

    `same_id or same_role` fell back to "some other control of this role reads
    as what we asked for" — and it fell back exactly when the identified
    control had vanished, which is when a wrong answer is most likely. A
    truncated search could also call something unique because it stopped before
    the second one (peer review).

    Source-only: the walk is driven by a fake hierarchy, no screen involved.
    """
    import nlc.driver as D
    from nlc.driver import Driver

    class Node:
        def __init__(self, role=None, ident=None, value=None, kids=()):
            self.role, self.ident, self.value, self.kids = role, ident, value, kids

    def fake_attr(node, key):
        if not isinstance(node, Node):
            return None
        return {"AXRole": node.role, "AXIdentifier": node.ident,
                "AXValue": node.value, "AXChildren": list(node.kids)}.get(key)

    d = Driver.__new__(Driver)          # no permissions needed for the walk
    # The control that was acted on, when the case says it survived.
    here = Node("AXPopUpButton", None, "as List")
    ident_here = Node("AXPopUpButton", "chooser", "as List")
    gone = Node("AXPopUpButton", None, "as List")   # never placed in a tree
    cases = {
        # The control we pressed is still there. It answers, and the fact that
        # two others share its role and carry no identifier does not matter —
        # this is not "a control like it", it IS it. The old rule called this
        # unattributable and refused to confirm anything.
        "누른 그 컨트롤이 살아 있다": (Node(kids=[
            here,
            Node("AXPopUpButton", None, "as Icons"),
            Node("AXPopUpButton", None, "as Gallery")]), None, here, "as List"),
        # Root's regression: it is gone, it had no identifier, and a DIFFERENT
        # popup is left holding exactly the value we asked for. That is not
        # evidence about the control that vanished.
        "사라졌는데 옆이 원하던 값": (Node(kids=[
            Node("AXPopUpButton", None, "as List")]), None, gone, None),
        # Same, with an identifier that no longer resolves.
        "사라졌는데 옆이 같은 값": (Node(kids=[
            Node("AXPopUpButton", None, "as List")]), "chooser", gone, None),
        # Gone, but its identifier names exactly one — that one speaks for it.
        "식별자로 찾음": (Node(kids=[
            ident_here,
            Node("AXPopUpButton", None, "as Icons")]), "chooser", gone, "as List"),
        # Two with the same identifier attribute nothing.
        "같은 식별자 둘": (Node(kids=[
            Node("AXPopUpButton", "chooser", "as List"),
            Node("AXPopUpButton", "chooser", "as Icons")]), "chooser", gone, None),
        # No identifier, one control, and it is NOT the one we pressed. The
        # weaker "exactly one of this role" fact used to confirm here, which is
        # the bug (peer review).
        "식별자 없고 하나뿐이지만 다른 것": (Node(kids=[
            Node("AXPopUpButton", None, "as List")]), None, gone, None),
        # No identifier and two: nothing to attribute, as before.
        "식별자 없고 둘": (Node(kids=[
            Node("AXPopUpButton", None, "as List"),
            Node("AXPopUpButton", None, "as Icons")]), None, gone, None),
    }
    real_attr, real_app = D._attr, D.AXUIElementCreateApplication
    real_equal = D.CFEqual
    real_copy = D.AXUIElementCopyAttributeValue
    try:
        D.AXUIElementCopyAttributeValue = lambda node, key, _: ((0, fake_attr(node, key))
            if fake_attr(node, key) is not None else (-25205, None))
        D._attr = fake_attr
        D.CFEqual = lambda a, b: a is b      # the fake tree is not CF objects
        for name, (tree, ident, control, expected) in cases.items():
            D.AXUIElementCreateApplication = lambda _pid, _t=tree: _t
            got = d._reread_selection(1, "AXPopUpButton", ident, control)
            assert got == expected, f"{name}: {got!r} 를 돌려줬습니다 (기대 {expected!r})"
        # A search that stopped early must not claim an identifier is unique.
        wide = Node(kids=[Node("AXGroup", None, None,
                               [Node("AXPopUpButton", "chooser", "as List")])
                          for _ in range(70)])
        D.AXUIElementCreateApplication = lambda _pid: wide
        assert d._reread_selection(1, "AXPopUpButton", "chooser", gone) is None, \
            "잘린 탐색인데 고유하다고 판단했습니다"
    finally:
        D._attr, D.AXUIElementCreateApplication = real_attr, real_app
        D.CFEqual = real_equal
        D.AXUIElementCopyAttributeValue = real_copy


# Capabilities that describe the CHANNEL rather than what can be done to a
# window. The in-process driver does not announce these and should not: there
# is no channel to make promises about.
#
#   action.click.deadline_uptime — the client stamps a click with an absolute
#     uptime past which the helper must not perform it. It exists because a
#     separate process can receive a click after the moment that asked for it
#     stopped being true. In-process the call is synchronous; there is no gap
#     for a stale click to arrive in.
WIRE_ONLY = frozenset({"action.click.deadline_uptime"})


@check("네이티브와 도우미가 같은 약속 목록을 말한다")
def _capability_lists_agree(_d):
    """Two surfaces honouring one contract must ANNOUNCE the same one.

    `press_key` existed in-process, behaved correctly, and was invisible: the
    class declared no capabilities at all, so a caller asking what it could do
    heard nothing and read that as "cannot" (peer finding). Silence is not a
    capability, and two lists that can drift will.

    Exact equality is not the rule, and demanding it was wrong: a few
    capabilities are about the WIRE, not about what can be done to a window,
    and the in-process driver has no wire. Those are named one by one below,
    with the reason, so that a genuine divergence still fails here instead of
    being waved through by a loosened comparison.

    Source-only — no screen, no helper, no permission.
    """
    import pathlib
    import re
    from nlc.driver import Driver
    swift = pathlib.Path(__file__).resolve().parents[1] / "helper" / "NLCHelper.swift"
    text = swift.read_text(encoding="utf-8")
    block = text.split("let helperCapabilities: [String] = [", 1)[1].split("]", 1)[0]
    announced = set(re.findall(r'"([^"]+)"', block))
    native = set(Driver.capabilities)
    assert WIRE_ONLY <= announced, (
        f"전송 전용이라던 약속을 도우미가 말하지 않습니다: "
        f"{sorted(WIRE_ONLY - announced)}")
    assert not (WIRE_ONLY & native), (
        f"네이티브가 전송 전용 약속을 말합니다 — 지킬 수 있다면 "
        f"WIRE_ONLY 에서 빼세요: {sorted(WIRE_ONLY & native)}")
    assert (announced - WIRE_ONLY) == native, (
        f"약속 목록이 어긋났습니다 — 도우미만: "
        f"{sorted(announced - WIRE_ONLY - native)}, "
        f"네이티브만: {sorted(native - announced)}")


@check("도우미가 무엇을 지킬 수 있는지 먼저 말한다")
def _capabilities_are_declared(d):
    caps = getattr(d, "capabilities", None)
    if caps is None:
        return                      # the in-process driver IS the capability
    from nlc.helper import REQUIRED_CAPABILITIES
    # That set is what the helper CLIENT puts on the wire, so it is a demand on
    # transports, not on a driver that is called directly. Asking it of the
    # in-process driver made it answer for a message nothing ever sends it.
    required = REQUIRED_CAPABILITIES
    if not hasattr(d, "_send"):
        required = required - WIRE_ONLY
    missing = required - set(caps)
    assert not missing, f"도우미가 못 지키는 것을 말하지 않았습니다: {missing}"


def live_reason() -> str:
    """Why this cannot run here — or "" if it can.

    Checked before anything is constructed, so a machine with no PyObjC, no
    Calculator or no visible screen says so instead of erroring.
    """
    try:
        from nlc.driver import Driver
    except Exception as e:
        return f"접근성 드라이버를 불러올 수 없습니다 ({type(e).__name__})"
    d = Driver()
    if d._resolve(CALC, launch=False) is None:
        return "계산기가 실행 중이 아닙니다"
    obs = d.observe(CALC, "tree_full", require_window=False)
    if obs.get("element_count", 0) == 0:
        return "화면을 볼 수 없습니다 (잠금 등)"
    return ""


def run_all(report=print) -> int:
    """Every check against every transport. Returns the number that failed."""
    drivers = _drivers()

    bad = 0
    skipped = 0
    for label, d in drivers:
        for name, fn in CHECKS:
            try:
                fn(d)
                report(f"  PASS  [{label}] {name}")
            except Skipped as e:
                skipped += 1
                report(f"  SKIP  [{label}] {name}\n          {e}")
            except AssertionError as e:
                bad += 1
                report(f"  FAIL  [{label}] {name}\n          {e}")
            except Exception as e:
                bad += 1
                report(f"  FAIL  [{label}] {name}\n          {type(e).__name__}: {e}")
    report("=" * 58)
    total = len(drivers) * len(CHECKS)
    line = f"  {total - bad - skipped}/{total} 통과"
    if skipped:
        # Said out loud, in the one line people quote. A total that silently
        # counts checks which never ran is the bug this replaces.
        line += f" · {skipped} 건너뜀 (실행되지 않음 — 통과로 세지 않음)"
    report(line)
    return bad


# `unittest discover` picks this up, and it must not drive somebody's computer
# for having run the unit tests. It clicks a real Calculator key, so it stays
# opt-in: NLC_CONTRACT_LIVE=1, or run this file directly (peer request).
import unittest                                                  # noqa: E402


class HelperContract(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("NLC_CONTRACT_LIVE") == "1",
                         "실제 화면을 조작하므로 NLC_CONTRACT_LIVE=1 일 때만 실행")
    def test_every_transport_agrees(self):
        why = live_reason()
        if why:
            self.skipTest(why)
        self.assertEqual(run_all(), 0)


def main() -> int:
    why = live_reason()
    if why:
        print(f"{why} — 계약 검사를 건너뜁니다.")
        return 0
    return 1 if run_all() else 0


if __name__ == "__main__":
    sys.exit(main())
