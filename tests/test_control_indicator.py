"""Whatever touches the screen shows that it is happening.

Found 2026-09-20 while verifying browsers: the outline was wired at two CLI
commands and two signed-helper messages, so every other way in — a script
holding a Driver, `nlc observe`, the whole evaluation suite, this session's own
browser and Calculator probes — drove somebody's machine with nothing on screen
to say so. Asking each caller to remember is how it went missing; the driver
owns it now.
"""
import importlib.util
import os
import pathlib
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch


@unittest.skipUnless(importlib.util.find_spec('ApplicationServices'), 'PyObjC required')
class ControlIndicatorTests(unittest.TestCase):
    def setUp(self):
        from nlc import driver
        self.m = driver
        self.d = object.__new__(driver.Driver)
        self.d._indicate = True
        self.d._indicator = None
        self.d._indicator_pid = None
        self.d._indicator_beat = None
        self.started = []

    def fake_indicator(self, alive=True):
        started = self.started
        class Fake:
            def __init__(self, pid, heartbeat=None, ttl=4.0, **kw):
                self.pid, self.heartbeat, self.ttl = pid, heartbeat, ttl
                self._alive = alive
            def __enter__(self):
                started.append(self)
                return self
            def __exit__(self, *exc):
                self._alive = False
                return False
            def alive(self):
                return self._alive
        return Fake

    def show(self, pid, **kw):
        import nlc.overlay
        with patch.object(nlc.overlay, 'Indicator', self.fake_indicator(**kw)):
            self.d._showing_control(pid)

    def test_touching_an_app_puts_the_outline_on_it(self):
        self.show(4321)
        self.assertEqual(1, len(self.started))
        self.assertEqual(4321, self.started[0].pid)
        self.assertTrue(self.d.indicator()['showing'])
        self.assertEqual(4321, self.d.indicator()['pid'])

    def test_the_same_app_again_does_not_start_a_second_one(self):
        self.show(4321)
        self.show(4321)
        self.assertEqual(1, len(self.started), 'one outline, kept alive')

    def test_a_different_app_moves_the_outline(self):
        self.show(4321)
        self.show(9876)
        self.assertEqual(2, len(self.started))
        self.assertFalse(self.started[0].alive(), 'the first is taken down')
        self.assertEqual(9876, self.d.indicator()['pid'])

    def test_the_heartbeat_is_touched_every_time(self):
        self.show(4321)
        beat = self.d._indicator_beat
        self.assertTrue(os.path.exists(beat))
        os.utime(beat, (0, 0))
        self.show(4321)
        self.assertGreater(os.stat(beat).st_mtime, 1,
                           'a driver still working keeps the outline alive')

    def test_a_dead_child_is_replaced_rather_than_assumed(self):
        self.show(4321, alive=False)
        self.show(4321, alive=False)
        self.assertEqual(2, len(self.started))

    def test_failing_to_draw_never_takes_down_the_task(self):
        import nlc.overlay
        def explode(*a, **kw):
            raise RuntimeError('no window server')
        with patch.object(nlc.overlay, 'Indicator', explode):
            self.d._showing_control(4321)          # must not raise
        self.assertFalse(self.d.indicator()['showing'], 'and it says so')

    def test_nothing_is_drawn_for_a_target_that_is_not_running(self):
        for pid in (None, 0, -1, True, '4321'):
            with self.subTest(pid=pid):
                self.show(pid)
        self.assertEqual([], self.started)

    def test_the_opt_out_is_explicit(self):
        self.d._indicate = False
        self.show(4321)
        self.assertEqual([], self.started)
        self.assertFalse(self.d.indicator()['enabled'])

    def test_closing_takes_the_outline_down_and_cleans_up(self):
        self.show(4321)
        beat = self.d._indicator_beat
        self.d.close()
        self.assertFalse(self.started[0].alive())
        self.assertFalse(os.path.exists(beat))
        self.assertIsNone(self.d.indicator()['pid'])

    def test_observe_and_act_both_show_it(self):
        """The two places that reach another app, checked at the surface."""
        import inspect
        for name in ('observe', 'act'):
            source = inspect.getsource(getattr(self.m.Driver, name))
            self.assertIn('_showing_control(', source,
                          f'{name} must say that it is happening')


@unittest.skipUnless(importlib.util.find_spec('ApplicationServices'), 'PyObjC required')
class IndicatorLifetimeTests(unittest.TestCase):
    """The outline leaves on its own, because one that never does stops being read."""

    def test_the_child_is_told_the_heartbeat_and_the_deadline(self):
        from nlc.overlay import Indicator
        seen = {}
        class Popen:
            pid = 999999
            def __init__(self, command, **kw):
                seen['command'] = command
            def poll(self):
                return None
            def terminate(self):
                pass
        with patch('subprocess.Popen', Popen):
            with Indicator(4321, heartbeat='/tmp/beat', ttl=2.5):
                pass
        self.assertIn('/tmp/beat', seen['command'])
        self.assertIn('2.5', seen['command'])
        self.assertIn('4321', seen['command'])

    def test_without_a_heartbeat_the_child_is_not_given_one(self):
        from nlc.overlay import Indicator
        seen = {}
        class Popen:
            pid = 999999
            def __init__(self, command, **kw):
                seen['command'] = command
            def poll(self):
                return None
            def terminate(self):
                pass
        with patch('subprocess.Popen', Popen):
            with Indicator(4321):
                pass
        self.assertEqual(['4321'], [c for c in seen['command'] if c == '4321'])
        self.assertEqual(4, len(seen['command']), 'python -m nlc.overlay <pid>')


ROOT = pathlib.Path(__file__).resolve().parent.parent


class TheIndicatorCoversNothing(unittest.TestCase):
    """It was an outline drawn around the target window. It is a menu-bar item.

    The outline was wrong twice over. It put our own drawing on top of
    someone's screen, and it carried `canJoinAllSpaces` — "make sure it is
    visible", which in practice meant it followed the user to every desktop and
    drew a box around empty space over unrelated work, while the window it
    described was not on that screen at all (user report, 2026-09-21).

    A menu-bar item hides nothing and follows nobody. These tests exist because
    the obvious "fix" is to bring the panel back with one flag removed, and the
    panel itself was the problem.
    """

    def test_the_helper_shows_a_menu_bar_item(self):
        text = (ROOT / "helper/NLCHelper.swift").read_text()
        self.assertIn("NSStatusBar.system.statusItem(withLength:", text)
        self.assertIn("func showControlIndicator(forPID", text)

    def test_the_python_indicator_shows_a_menu_bar_item(self):
        text = (ROOT / "nlc/overlay.py").read_text()
        self.assertIn("statusItemWithLength_", text)

    def test_neither_draws_a_window_over_the_screen(self):
        for path, banned in (("helper/NLCHelper.swift", ("NSPanel(", "orderFrontRegardless")),
                             ("nlc/overlay.py", ("NSPanel", "NSBezierPath"))):
            text = (ROOT / path).read_text()
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("//") or stripped.startswith("#"):
                    continue
                for token in banned:
                    self.assertNotIn(token, stripped,
                                     f"{path} is drawing over the screen again")

    def test_no_collection_behaviour_survives_anywhere(self):
        # There is no panel to give a Space behaviour to. A reference to one is
        # the panel coming back.
        for path in ("helper/NLCHelper.swift", "nlc/overlay.py"):
            text = (ROOT / path).read_text()
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("//") or stripped.startswith("#"):
                    continue
                self.assertNotIn("ollectionBehavior", stripped, path)

    def test_the_indicator_still_gates_every_action(self):
        # The point of it is that a caller cannot act invisibly. Losing the
        # outline must not lose that.
        text = (ROOT / "helper/NLCHelper.swift").read_text()
        self.assertGreaterEqual(text.count("ensureVisibleControl(forPID: p)"), 4)
        self.assertIn("indicator_unavailable", text)

    def test_what_the_gate_hangs_on_is_the_signal_that_cannot_hide(self):
        # Measured: on a full menu bar macOS collapses extra items behind an
        # overflow chevron while the item still reports isVisible == true. A
        # promise the person can see this happening cannot rest on that, so the
        # refusal hangs on the capture stream, whose indicator is the system's
        # own and took a slot ahead of the chevron rather than behind it.
        text = (ROOT / "helper/NLCHelper.swift").read_text()
        body = text[text.index("func ensureVisibleControl("):]
        body = body[:body.index("\n}\n")]
        self.assertIn("return ensureControlStream(forPID: pid)", body)
        # The menu-bar item is best effort and must not be able to fail the run.
        self.assertIn("_ = DispatchQueue.main.sync", body)

    def test_the_held_stream_sees_one_window_not_the_screen(self):
        # `SCContentFilter(display:)` stood here and captured everything, so an
        # indicator meaning "NLC is looking" was looking at every other window
        # the person had open (user report, 2026-09-21). The indicator may not
        # cost more than what it announces.
        text = (ROOT / "helper/NLCHelper.swift").read_text()
        body = text[text.index("func ensureControlStream(forPID"):]
        body = body[:body.index("\n}\n")]
        self.assertIn("SCContentFilter(desktopIndependentWindow:", body)
        # In code, not in the comment that explains why it is gone.
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("//"):
                continue
            self.assertNotIn("SCContentFilter(display:", stripped)

    def test_the_stream_follows_the_window_it_is_about(self):
        # A run that moves to another window must move the indicator with it,
        # or it reports capture of something we stopped touching.
        text = (ROOT / "helper/NLCHelper.swift").read_text()
        self.assertIn("controlStreamWindow", text)
        body = text[text.index("func ensureControlStream(forPID"):]
        body = body[:body.index("\n}\n")]
        self.assertIn("stopCapture", body)

    def test_the_refusal_names_the_permission_it_needs(self):
        # "조작 표시를 띄울 수 없습니다" told nobody what to do about it, and the
        # cause is now one specific switch.
        text = (ROOT / "helper/NLCHelper.swift").read_text()
        self.assertEqual(text.count("화면 기록 에서 'NLC Helper'"), 4)

    def test_the_held_stream_keeps_nothing(self):
        # It is lit by really capturing, so it takes the least it can: the
        # frames are dropped and the stream is small and slow. A sink that
        # started keeping frames would be taking more of someone's screen than
        # the indicator promises.
        text = (ROOT / "helper/NLCHelper.swift").read_text()
        sink = text[text.index("final class DiscardingSink"):]
        sink = sink[:sink.index("\n}\n")]
        self.assertNotIn("append", sink)
        self.assertNotIn("=", sink.split("of type: SCStreamOutputType)")[-1])

    def test_it_names_the_app_being_driven(self):
        # "something is happening" is not the same promise as "this app is
        # being driven", and the menu bar has room to say which.
        swift = (ROOT / "helper/NLCHelper.swift").read_text()
        python = (ROOT / "nlc/overlay.py").read_text()
        self.assertIn("localizedName", swift)
        self.assertIn("localizedName", python)


class TheIndicatorCanBeActedOn(unittest.TestCase):
    """An indicator you cannot act on is half of what it promises.

    It said "NLC is working on Calculator · Esc" and that was the whole of it:
    someone who did not know about Esc was told something was happening and
    given nothing to do about it. Codex Computer Use's own status item carries
    a menu (`ComputerUseIPCCodexStatusItemMenuState`); macOS has no system
    indicator for accessibility control to inherit one from — Control Center's
    sensor indicators cover camera, microphone and screen capture, and nothing
    else — so this is ours to build.
    """

    def test_the_indicator_has_a_menu(self):
        text = (ROOT / "helper/NLCHelper.swift").read_text()
        self.assertIn("func controlMenu(for appName: String?) -> NSMenu", text)
        self.assertIn("item.menu = controlMenu(for: who)", text)

    def test_the_menu_names_what_is_being_driven(self):
        text = (ROOT / "helper/NLCHelper.swift").read_text()
        body = text[text.index("func controlMenu(for appName"):]
        body = body[:body.index("\n}\n")]
        self.assertIn("appName.map", body)

    def test_stopping_goes_through_the_one_abort_everything_consults(self):
        # Not a second flag. Every action already asks `abortState`, and a
        # menu that set its own would be a second answer to one question.
        text = (ROOT / "helper/NLCHelper.swift").read_text()
        body = text[text.index("@objc func stopNow(_ sender: Any?)"):]
        body = body[:body.index("\n    }\n")]
        self.assertIn("abortState.stopNow()", body)

    def test_the_menu_stop_is_not_routed_through_the_staleness_check(self):
        # `note(at:)` exists because an Esc can reach the run loop after the
        # run it was aimed at ended. A click on an indicator that only exists
        # during this run cannot be stale, and manufacturing a timestamp to
        # pass that test would be asking a question whose answer is known.
        text = (ROOT / "helper/NLCHelper.swift").read_text()
        self.assertIn("func stopNow() {", text)
        body = text[text.index("@objc func stopNow(_ sender: Any?)"):]
        body = body[:body.index("\n    }\n")]
        self.assertNotIn("note(at:", body)

    def test_the_menu_target_cannot_be_read_before_it_exists(self):
        # `showControlIndicator` is reachable from diagnostic flags that exit
        # before a global declared further down would have been initialised —
        # the trap that silently ate the logger's output earlier today. A
        # type's static is created on first use.
        text = (ROOT / "helper/NLCHelper.swift").read_text()
        self.assertIn("static let shared = ControlMenuTarget()", text)
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("//"):
                continue
            self.assertNotIn("let controlMenuTarget =", stripped,
                             "a top-level global here depends on source order")
