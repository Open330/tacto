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
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tacto.evaluation import foreground_allowed

CALC = "com.apple.calculator"

# Nothing is imported from `tacto.driver` at module scope on purpose. It needs
# PyObjC, so a top-level import turns `unittest discover` into an import error
# on any machine without it — a Linux box, or the browser-only venv — and an
# import error is indistinguishable from a broken contract (peer finding).


def _drivers():
    """Every transport this machine can currently offer, with its name."""
    from tacto.driver import Driver
    out = [("in-process", Driver())]
    try:
        from tacto.helper import InheritedHelperDriver
        if InheritedHelperDriver.available():
            out.append(("inherited-helper", InheritedHelperDriver()))
    except Exception as e:                       # pragma: no cover - env only
        print(f"  (inherited-helper 사용 불가: {e})")
    return out


def _candidates(tree):
    from tacto.general import candidates
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


@check("입력 요소를 지정하지 않은 탐색 키는 전송하지 않는다")
def _no_key_is_synthesised(d):
    """The old target-less shape cannot address a window-bound input channel."""
    obs = _observe(d)
    for key in NAVIGATION_KEYS:
        r = d.act(obs["obs_id"], CALC, {"name": "press_key", "key": key})
        assert r["type"] == "error", f"unbound {key} was sent: {r}"
        assert r.get("effect") == "not_started", r


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


@check("창 자체는 탐색 키의 입력 요소가 아니다")
def _press_key_has_no_element(d):
    obs = _observe(d)
    r = d.act(obs["obs_id"], CALC,
              {"name": "press_key", "key": "Tab", "element_index": 0})
    # Index0 is the window, not the app current input element.
    assert r["type"] == "error", f"window accepted an input-element key: {r}"
    assert r.get("effect") == "not_started", r


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
    from tacto.driver import Driver, _attr
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
    if not foreground_allowed():
        return False
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
        # `frontmostApplication()` is a snapshot the run loop refreshes, and a
        # script runs no run loop — without the pump it kept answering Finder
        # after Calendar had already come forward, and this timed out on an
        # activation that had worked.
        d._pump(0.05)
        front = workspace.frontmostApplication()
        if front is not None and int(front.processIdentifier()) == pid:
            return True
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
    from tacto.driver import Driver, _attr
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


SELECT_FIXTURE = "ai.callabo.nlc.select-fixture"
_FIXTURE = {}          # built once per process, torn down by its own timeout


def _select_fixture(d, lifetime=180):
    """An owned popup to check `select_option` against, started if needed.

    Finder's view popup was the subject here because it is reversible. On
    macOS 27 it stopped answering: `AXPress` and `AXShowMenu` both return
    kAXErrorSuccess and nothing opens, and the window refuses `AXFocused` the
    same way — set returns 0, the value stays false. The driver is right to
    refuse rather than claim it worked, but that leaves the happy path of
    `select_option` with nothing to prove itself on.

    So the subject is ours: ordinary AppKit controls, no AX overrides, a popup
    whose selection is echoed into a separate label by the control's own action
    callback. That label is the witness — it moves only if the selection really
    happened, not because the driver says so.

    Returns the pid. Skips, with the reason, when this machine cannot build or
    start it — checked before anything is pressed, never after.
    """
    import subprocess
    import time
    from tacto.driver import Driver

    probe = Driver()
    pid = _FIXTURE.get("pid")
    if pid is not None and probe._resolve(SELECT_FIXTURE, launch=False) == pid:
        if not _bring_forward(SELECT_FIXTURE, probe):
            skip("픽스처를 앞으로 가져오지 못했습니다 — 팝업은 앞선 앱에서만 열립니다")
        return pid

    import plistlib
    import tempfile
    root = pathlib.Path(__file__).resolve().parents[1]
    source = root / "tests/fixtures/NativeSelectFixture.swift"
    if not source.is_file():
        skip(f"{source.relative_to(root)} 이 없습니다")
    swiftc = pathlib.Path("/Library/Developer/CommandLineTools/usr/bin/swiftc")
    if not swiftc.is_file():
        skip("swiftc 가 없습니다 (Command Line Tools 미설치)")
    sdks = sorted(pathlib.Path("/Library/Developer/CommandLineTools/SDKs").glob("MacOSX*.sdk"))
    if not sdks:
        skip("macOS SDK 를 찾지 못했습니다")

    # Kept for the life of the process, not a `with` block: the fixture has to
    # outlive the build and both checks, and it ends itself on --timeout.
    work = _FIXTURE.get("dir") or tempfile.mkdtemp(prefix="nlc-select-fixture-")
    _FIXTURE["dir"] = work
    app = pathlib.Path(work) / "NLC Select Fixture.app"
    executable = app / "Contents/MacOS/NativeSelectFixture"
    if not executable.is_file():
        executable.parent.mkdir(parents=True, exist_ok=True)
        (app / "Contents/Info.plist").write_bytes(plistlib.dumps({
            "CFBundleIdentifier": SELECT_FIXTURE,
            "CFBundleExecutable": executable.name,
            "CFBundleName": "NLC Select Fixture",
            "CFBundlePackageType": "APPL",
            "CFBundleVersion": "1",
        }))
        build = subprocess.run(
            [str(swiftc), "-parse-as-library", "-sdk", str(sdks[-1]),
             str(source), "-o", str(executable)],
            capture_output=True, text=True)
        if build.returncode != 0:
            skip(f"픽스처 빌드 실패: {build.stderr.strip()[:160]}")

    # A fixture left over from an earlier run answers to the same bundle id,
    # so `_resolve` would keep returning ITS pid and the wait below would time
    # out against a process that is perfectly healthy — and it may be the
    # accessory one, which cannot come forward at all. It is ours and
    # disposable, so it goes.
    stale = probe._resolve(SELECT_FIXTURE, launch=False)
    if stale is not None:
        from AppKit import NSRunningApplication
        app_obj = NSRunningApplication.runningApplicationWithProcessIdentifier_(stale)
        if app_obj is not None:
            app_obj.terminate()
        # `_pump` between polls, or this never ends: NSWorkspace's app list is
        # a snapshot refreshed by run-loop notifications, and a plain script
        # runs no run loop — so a process that died a second ago stays in the
        # list forever and `_resolve` keeps handing back its pid. `_resolve`
        # pumps only when it fails to find something, which is the opposite of
        # the case here.
        gone = time.monotonic() + 8
        while time.monotonic() < gone:
            probe._pump()
            if probe._resolve(SELECT_FIXTURE, launch=False) is None:
                break
            time.sleep(0.1)
        else:
            skip("이전 픽스처가 종료하지 않아 새로 시작할 수 없습니다")

    # `--foreground` is deliberate and said out loud: a popup menu opens only
    # in the app that is in front, so a check of the happy path has to put its
    # subject there. The driver never does this; the harness does, on a fixture
    # that exists for it. The same fixture without the flag is what the
    # background-refusal probes use.
    # Opened through LaunchServices, not by running the executable. Measured
    # here the same way it was measured for the helper: a binary exec'd
    # directly is never allowed to come forward — `activate` returns and
    # nothing happens — while the same bundle opened by LaunchServices is
    # frontmost within a second. Twice now, the answer to "why will macOS not
    # let this process do the thing it was granted" has been how it was
    # started.
    launch = subprocess.run(
        # `-g`: LaunchServices activates what it opens unless told not to,
        # so dropping `--foreground` from the fixture was not enough — the
        # launch itself took the screen. Measured by the focus witness during a
        # default run: ['ai.callabo.nlc.select-fixture', 'com.apple.finder'],
        # with Finder frontmost at both ends. In foreground mode the fixture
        # still comes forward, because `--foreground` makes it activate itself.
        ["open", "-g", "-n", "-a", str(app), "--args",
         "--timeout", str(lifetime)]
        # `--foreground` makes the fixture activate itself, which is a
        # second way to take the screen and has nothing to do with
        # `_bring_forward`. Gating one and not the other left the run
        # pulling the fixture in front anyway.
        + (["--foreground"] if foreground_allowed() else []),
        capture_output=True, text=True)
    if launch.returncode != 0:
        skip(f"픽스처를 열지 못했습니다: {launch.stderr.strip()[:160]}")
    # Ready means answerable, not merely running. A pid appears before the
    # window does, and a check that asked in between reported
    # `no_window_content` — which reads as a contract failure and is really
    # "we asked too early". The fixture's own result label is the readiness
    # signal: it exists once the controls are built.
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        probe._pump()
        pid = probe._resolve(SELECT_FIXTURE, launch=False)
        if pid is not None and _fixture_label(pid, "nlc-fixture-result") is not None:
            _FIXTURE["pid"] = pid
            if not _bring_forward(SELECT_FIXTURE, probe):
                skip("픽스처를 앞으로 가져오지 못했습니다 — "
                     "팝업은 앞선 앱에서만 열립니다")
            return pid
        time.sleep(0.1)
    skip("픽스처가 15초 안에 쓸 수 있는 상태가 되지 않았습니다")


def _fixture_popup(d):
    """The fixture's choice popup: its observation, its index, and its pid."""
    import time
    pid = _select_fixture(d)
    # A pid is not a window, and OUR pid is not the helper's view of one.
    # Two separate delays, both of them "we asked too early" rather than a
    # broken contract:
    #
    #   no_window_content — the app exists, its window does not yet;
    #   target_not_found  — the helper is a different process with its own
    #                       NSWorkspace snapshot, and an app launched after it
    #                       started takes a moment to appear in that list.
    #
    # Reporting either as a failure blames the contract for the clock.
    obs = None
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        obs = d.observe(SELECT_FIXTURE, "tree_full")
        if obs.get("type") == "observation":
            break
        if obs.get("error") not in ("no_window_content", "target_not_found"):
            break
        time.sleep(0.3)
    if obs is None or obs.get("type") != "observation":
        skip(f"픽스처를 관측할 수 없습니다: {obs.get('error') if obs else 'none'}")
    for line in obs["tree"].splitlines():
        if "PopUpButton" in line and "nlc-fixture-choice" in line:
            return obs, int(line.strip().split()[0]), pid
    skip("픽스처에 choice 팝업이 없습니다")


def _fixture_label(pid, identifier):
    """Read one of the fixture's labels straight from AX, not from the driver.

    The driver's own report is what is being checked, so the confirmation
    cannot come from it.
    """
    from ApplicationServices import AXUIElementCreateApplication
    from tacto.driver import _attr
    found = []

    def walk(node, depth=0):
        if depth > 8 or found:
            return
        if _attr(node, "AXIdentifier") == identifier:
            found.append(node)
            return
        for child in (_attr(node, "AXChildren") or [])[:60]:
            walk(child, depth + 1)

    app = AXUIElementCreateApplication(pid)
    for window in (_attr(app, "AXWindows") or []):
        walk(window)
    return _attr(found[0], "AXValue") if found else None


def _discard_fixture():
    """End the fixture rather than dismissing its menu.

    The obvious cleanup — `AXCancel` the menu that is still open — is the one
    thing that must not happen here. It was measured on this kind of control:
    an automatic cancel left it unable to post a menu ever again, every later
    `AXShowMenu` returning kAXErrorSuccess with nothing on screen. The driver
    refuses to do it and says why (`refuse_after_opening` in tacto/driver.py),
    and a harness that does it anyway hands the NEXT transport a control that
    cannot open — which is exactly how these checks first passed in-process
    and failed for the helper, in the same run.

    The fixture is ours and disposable, so a menu left standing is answered by
    ending the process. The next `_select_fixture` starts a clean one.
    """
    import os
    import signal
    from AppKit import NSRunningApplication
    # Always the CURRENT fixture, never a pid a caller remembered. A check
    # that relaunched partway — the round-trip's restore step does — would
    # otherwise kill the instance that already exited and leave the live one
    # on someone's screen.
    target = _FIXTURE.get("pid")
    _FIXTURE.pop("pid", None)
    if target is None:
        return
    app_obj = NSRunningApplication.runningApplicationWithProcessIdentifier_(target)
    if app_obj is None or app_obj.isTerminated():
        return
    # Confirmed to be the fixture before anything is signalled: a pid is not a
    # name, and the one thing a test harness must never do is kill by number
    # and hope.
    if app_obj.bundleIdentifier() != SELECT_FIXTURE:
        return
    # SIGKILL, not terminate(). An open menu runs a nested run loop, and a
    # polite termination waits for it to end — which is precisely the state
    # this is called in, so the fixture outlived every gentler attempt and the
    # next check found it still holding the bundle id.
    try:
        os.kill(target, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _the_only_view_popup(pid):
    """The view popup of Finder's single window, walked here rather than
    borrowed from a driver's cache. Returns None if it is not unique."""
    from ApplicationServices import AXUIElementCreateApplication
    from tacto.driver import _attr
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
    """Open the menu, press the item, and check it really landed.

    Native popups do not expose their options until the menu is open —
    measured: `AXChildren` is empty and the menu turns up elsewhere in the app.
    So the real path is the one a person takes, and every step of it is
    checked.

    The subject is our own fixture rather than Finder's view popup, which is
    what stood here until macOS 27 stopped answering it (see `_select_fixture`).
    Being ours buys the thing that matters most: the fixture echoes the chosen
    title into a SEPARATE label from the control's action callback, so there is
    a witness the driver does not write. `selected` in the reply is the claim;
    the label is the fact.
    """
    obs, idx, pid = _fixture_popup(d)
    start = _fixture_label(pid, "nlc-fixture-result")
    if start not in ("Alpha", "Beta"):
        skip(f"픽스처가 예상 상태가 아닙니다: {start!r}")
    other = "Beta" if start == "Alpha" else "Alpha"
    before = _fixture_label(pid, "nlc-fixture-count")
    try:
        r = d.act(obs["obs_id"], SELECT_FIXTURE,
                  {"name": "select_option", "element_index": idx,
                   "option_label": other})
        assert r["type"] == "acted", f"{other} 를 고르지 못했습니다: {r}"
        assert r.get("selected") == other, r
        # The app's own callback ran. Not the driver's word for it: this label
        # is written by the control's action, so it moves only if a real
        # selection happened.
        landed = _fixture_label(pid, "nlc-fixture-result")
        assert landed == other, f"고른 것이 반영되지 않았습니다: {landed!r}"
        after = _fixture_label(pid, "nlc-fixture-count")
        assert after != before, (
            f"값은 바뀌었는데 선택 콜백이 돌지 않았습니다 "
            f"({before!r} -> {after!r}) — 값을 직접 써넣은 것입니다")
        # And the claim matches the fact. Confirming something that did not
        # happen is the one answer that must never appear.
        if r.get("unverified") is None:
            assert landed == other, \
                f"확인했다고 하는데 실제로는 {landed!r} 입니다"
        # Put it back. Reversibility is a property worth checking, and it is
        # read back here rather than inferred — a fresh fixture would start at
        # the right value and prove nothing.
        obs2, idx2, _ = _fixture_popup(d)
        d.act(obs2["obs_id"], SELECT_FIXTURE,
              {"name": "select_option", "element_index": idx2,
               "option_label": start})
        back = _fixture_label(pid, "nlc-fixture-result")
        assert back == start, f"원래 값으로 못 되돌렸습니다: {start!r} -> {back!r}"
    finally:
        _discard_fixture()


@check("없는 선택지·모호한 값은 거절하고 무엇이 있는지 말한다")
def _select_option_refusals(d):
    obs, idx, pid = _fixture_popup(d)
    before = _fixture_label(pid, "nlc-fixture-result")
    missing = d.act(obs["obs_id"], SELECT_FIXTURE,
                    {"name": "select_option", "element_index": idx,
                     "option_label": "이런 선택지는 없습니다"})
    assert missing["type"] == "error", f"없는 선택지가 통과함: {missing}"
    # NOT `not_started`, and that is the driver being right rather than
    # generous. Finding out what a native popup offers means opening it, so by
    # the time this refusal is written the screen has already moved — and the
    # menu stays open on purpose: an automatic AXCancel from here was measured
    # to leave the control unable to post a menu ever again. What must be true
    # is narrower and is checked instead: nothing was chosen.
    assert missing.get("effect") == "unknown", missing
    assert missing.get("option_selected") is False, missing
    assert missing.get("selections_made") == 0, missing
    # The refusal names what IS there, like every other target failure here.
    assert "있는 것" in str(missing.get("detail", "")), missing
    # The menu is still standing, by design, and it is not dismissed — see
    # `_discard_fixture`. A fresh fixture is how the next case gets a control
    # that can still open.
    _discard_fixture()

    obs2, idx2, pid = _fixture_popup(d)
    # `value` exists for shape-compatibility with the browser and must agree:
    # a native menu item has a title and nothing behind it.
    clash = d.act(obs2["obs_id"], SELECT_FIXTURE,
                  {"name": "select_option", "element_index": idx2,
                   "option_label": "Beta", "value": "beta"})
    assert clash["type"] == "error", f"값이 어긋나는데 통과함: {clash}"
    # This one IS `not_started`: a title and a value that disagree is caught
    # before anything opens, so the screen has not moved.
    assert clash.get("effect") == "not_started", clash

    # And that claim is checked against the world, not taken from the reply.
    after = _fixture_label(pid, "nlc-fixture-result")
    _discard_fixture()
    assert after == before, (
        f"거절했다면서 값이 {before!r} 에서 {after!r} 로 바뀌었습니다")


@check("비활성 선택지는 고를 수 없다고 말한다")
def _select_option_disabled(d):
    """A disabled item is not a missing one, and the refusal must say which.

    The fixture keeps one — `autoenablesItems` off, `Disabled` explicitly
    disabled — because a popup whose items are all selectable cannot show the
    difference between "there is no such option" and "that one cannot be
    chosen", and a caller acting on the first when it is the second retries
    forever.
    """
    obs, idx, pid = _fixture_popup(d)
    before = _fixture_label(pid, "nlc-fixture-result")
    r = d.act(obs["obs_id"], SELECT_FIXTURE,
              {"name": "select_option", "element_index": idx,
               "option_label": "Disabled"})
    after = _fixture_label(pid, "nlc-fixture-result")
    _discard_fixture()
    assert r["type"] == "error", f"비활성 선택지가 통과함: {r}"
    assert r.get("option_selected") is False, r
    assert after == before, (
        f"거절했다면서 값이 {before!r} 에서 {after!r} 로 바뀌었습니다")


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
    import tacto.driver as D
    from tacto.driver import Driver

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
# press_key also carries an absolute helper-wire deadline.
WIRE_ONLY = frozenset({
    "action.click.deadline_uptime", "action.press_key.deadline_uptime",
})

# These features are implemented by the signed helper, not by Driver.
SIGNED_HELPER_ONLY = frozenset({
    "action.click.save_panel_guard",
    # Return to the Go to Folder path field: helper press_key only; Driver's own
    # press_key stays navigation keys.
    "press_key.return_in_go_to_folder",
    # Finder folder open through LaunchServices without activation.
    "action.open_folder",
    # File > Save bound to one file, checked at the press.
    "action.menu.focused_document",
    "evidence.document_rtf_export",
    "evidence.save_panel",
    # Window-bound double click (two click pairs on one element); Driver only
    # performs AXOpen and announces nothing.
    "action.double_click",
    # click falls back to one window-bound click when there is no AXPress.
    "click.window_bound_element",
    # Return into a bound text input or selected row (host-vouched submit).
    "press_key.return_bound",
    # LaunchServices reopen without activation; AXCancel on an open menu.
    "action.reopen_window", "action.close_menu",
    # Token-bound Save and document attachments/lifecycle proofs are native-helper APIs.
    "action.menu.document_token_save", "evidence.document_attachment_metadata",
    "evidence.document_window_status",
    # Native readiness instrumentation and strict select-option wire validation.
    "diagnostic.readiness_preflight", "diagnostic.readiness_read_cost",
    "select_option.strict_parameters",
})


def _assert_capability_contract(native, announced):
    helper_only = WIRE_ONLY | SIGNED_HELPER_ONLY
    assert helper_only <= announced, (
        f"도우미 전용 약속이 빠졌습니다: {sorted(helper_only - announced)}")
    assert not (helper_only & native), (
        f"네이티브가 도우미 전용 약속을 말합니다: {sorted(helper_only & native)}")
    assert (announced - helper_only) == native, (
        f"공통 약속 목록이 어긋났습니다 — 도우미만: "
        f"{sorted(announced - helper_only - native)}, "
        f"네이티브만: {sorted(native - announced)}")


@check("관측이 어느 요소에 secondary_action 이 통하는지 말한다")
def _secondary_actions_are_named(d):
    """An action nothing tells you where to use is one you cannot use.

    `action.secondary_action` was announced by both surfaces while no
    observation said which elements accept one, so the only way to find out
    was to send one and read the refusal. Codex Computer Use puts the same
    fact in its tree (`Secondary Actions: Copy`), and it is the difference
    between a caller that can plan and one that has to guess.
    """
    obs = _observe(d)
    named = [line for line in obs["tree"].splitlines()
             if "Secondary Actions:" in line]
    if not named:
        skip("이 화면에는 secondary action 을 가진 요소가 없습니다")
    # What is named must be something the boundary will actually carry.
    allowed = {"ShowMenu", "Increment", "Decrement", "Confirm", "Cancel"}
    for line in named:
        listed = line.split("Secondary Actions:", 1)[1]
        for name in (n.strip() for n in listed.split(",")):
            if not name:
                continue
            assert name in allowed, \
                f"통과하지 못할 동작을 광고했습니다: {name!r} in {line.strip()[:80]}"
    # And it must be true: performing one of them must not come back
    # "unsupported", which is the answer for an element that never had it.
    first = named[0]
    index = int(first.strip().split()[0])
    action = first.split("Secondary Actions:", 1)[1].split(",")[0].strip()
    r = d.act(obs["obs_id"], CALC,
              {"name": "secondary_action", "element_index": index,
               "action": "AX" + action})
    detail = str(r.get("detail", ""))
    assert "unsupported" not in detail, \
        f"있다고 한 {action} 이 지원되지 않는다고 합니다: {r}"


@check("네이티브와 도우미가 같은 약속 목록을 말한다")
def _capability_lists_agree(_d):
    """Two surfaces honouring one contract must ANNOUNCE the same one.

    `press_key` existed in-process, behaved correctly, and was invisible: the
    class declared no capabilities at all, so a caller asking what it could do
    heard nothing and read that as "cannot" (peer finding). Silence is not a
    capability, and two lists that can drift will.

    Exact equality is not the rule, and demanding it was wrong: a few
    capabilities describe the WIRE, and the in-process driver has no wire.
    Other explicitly named features are implemented only by the signed helper. Those are named one by one below,
    with the reason, so that a genuine divergence still fails here instead of
    being waved through by a loosened comparison.

    Source-only — no screen, no helper, no permission.
    """
    import re
    import ast
    root = pathlib.Path(__file__).resolve().parents[1]
    driver = ast.parse((root / "tacto" / "driver.py").read_text())
    cls = next(node for node in driver.body if isinstance(node, ast.ClassDef) and node.name == "Driver")
    caps = next(node.value for node in cls.body if isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "capabilities" for t in node.targets))
    native = set(ast.literal_eval(caps.args[0]))
    swift = root / "helper" / "TactoHelper.swift"
    text = swift.read_text(encoding="utf-8")
    block = text.split("let helperCapabilities: [String] = [", 1)[1].split("]", 1)[0]
    # Comments are prose, not data. A comment that quoted a capability name to
    # explain why it is GONE was read back as that capability being declared —
    # the list disagreed with itself because of a sentence about it.
    code = "\n".join(line for line in block.splitlines()
                     if not line.strip().startswith("//"))
    announced = set(re.findall(r'"([^"]+)"', code))
    _assert_capability_contract(native, announced)


@check("도우미가 무엇을 지킬 수 있는지 먼저 말한다")
def _capabilities_are_declared(d):
    caps = getattr(d, "capabilities", None)
    if caps is None:
        return                      # the in-process driver IS the capability
    from tacto.helper import REQUIRED_CAPABILITIES
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
        from tacto.driver import Driver
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
# opt-in: TACTO_CONTRACT_LIVE=1, or run this file directly (peer request).
import unittest                                                  # noqa: E402


class CapabilityContract(unittest.TestCase):
    def test_actual_source_announcements_agree(self):
        _capability_lists_agree(None)

    def test_explicit_backend_differences(self):
        native = {"observe", "act"}
        announced = native | WIRE_ONLY | SIGNED_HELPER_ONLY
        _assert_capability_contract(native, announced)
        invalid = [
            (native, announced - {"observe"}),
            (native - {"observe"}, announced),
            (native, announced | {"unknown.future.capability"}),
            (native | {"unknown.future.capability"}, announced),
        ]
        invalid.extend((native, announced - {cap})
                       for cap in WIRE_ONLY | SIGNED_HELPER_ONLY)
        invalid.extend((native | {cap}, announced)
                       for cap in WIRE_ONLY | SIGNED_HELPER_ONLY)
        for native_caps, helper_caps in invalid:
            with self.subTest(native=native_caps, helper=helper_caps):
                with self.assertRaises(AssertionError):
                    _assert_capability_contract(native_caps, helper_caps)


class HelperContract(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("TACTO_CONTRACT_LIVE") == "1",
                         "실제 화면을 조작하므로 TACTO_CONTRACT_LIVE=1 일 때만 실행")
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
