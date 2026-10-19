"""no_window_content names ONE cause, so the person knows what to do.

Reported 2026-09-28: an agent reading Safari got "locked screen, hidden, or
minimized" while the lock state sat beside it in the same reply. The screen
was in fact locked.
"""
import unittest
from unittest.mock import patch


class NoWindowReasonTests(unittest.TestCase):
    def why(self, lock, hidden=False, windows=()):
        from tacto import driver
        app = type("App", (), {"isHidden": lambda self: hidden})()
        fake = type("NSRunningApplication", (), {
            "runningApplicationWithProcessIdentifier_": staticmethod(lambda pid: app)})
        with patch("AppKit.NSRunningApplication", fake), \
             patch.object(driver, "AXUIElementCreateApplication", return_value="ax"), \
             patch.object(driver, "_attr", side_effect=lambda el, name:
                          list(windows) if name == "AXWindows" else el):
            return driver._why_no_window(1, lock)

    def test_a_locked_screen_is_said_first_and_plainly(self):
        reason, detail = self.why({"locked": True}, hidden=True)
        self.assertEqual("screen_locked", reason)
        self.assertIn("잠겨", detail)

    def test_hidden_minimized_and_none(self):
        self.assertEqual("app_hidden", self.why({"locked": False}, hidden=True)[0])
        self.assertEqual("minimized", self.why({"locked": False}, windows=[True, True])[0])
        self.assertEqual("no_visible_window", self.why({"locked": False})[0])

    def test_a_window_the_server_holds_offscreen_is_named(self):
        import Quartz
        offscreen = [{"kCGWindowOwnerPID": 1, "kCGWindowLayer": 0, "kCGWindowIsOnscreen": False,
                      "kCGWindowBounds": {"Height": 400}}]
        with patch.object(Quartz, "CGWindowListCopyWindowInfo", return_value=offscreen):
            reason, detail = self.why({"locked": False})
        self.assertEqual("window_offscreen", reason)
        self.assertIn("reopen_window", detail)
        # A menu-bar-height strip or another layer is not a window someone lost.
        for info in ({"kCGWindowOwnerPID": 1, "kCGWindowLayer": 0, "kCGWindowBounds": {"Height": 24}},
                     {"kCGWindowOwnerPID": 1, "kCGWindowLayer": 25, "kCGWindowBounds": {"Height": 400}},
                     {"kCGWindowOwnerPID": 2, "kCGWindowLayer": 0, "kCGWindowBounds": {"Height": 400}}):
            with patch.object(Quartz, "CGWindowListCopyWindowInfo", return_value=[info]):
                self.assertEqual("no_visible_window", self.why({"locked": False})[0])

    def test_a_bare_helper_reply_is_explained_the_same_way(self):
        from tacto import helper
        d = object.__new__(helper.HelperDriver)
        d._last_action_at = 0
        d._send = lambda msg: {"type": "error", "error": "no_window_content", "target": "t"}
        with patch.object(helper, "_explain_no_window",
                          return_value={"lock": {"locked": True}, "reason": "screen_locked",
                                        "detail": "화면이 잠겨 있습니다"}):
            out = helper.HelperDriver.observe(d, "t")
        self.assertEqual(("screen_locked", "화면이 잠겨 있습니다"), (out["reason"], out["detail"]))

    def test_the_skill_says_what_each_reason_asks_for(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1] / "skill"
        entry = (root / "SKILL.md").read_text()
        self.assertIn("references/recovery.md", entry)
        skill = (root / "references/recovery.md").read_text()
        for reason in ("screen_locked", "app_hidden", "minimized", "no_visible_window",
                       "window_offscreen"):
            self.assertIn(reason, skill)


class UnhideQuietlyTests(unittest.TestCase):
    """A hidden app's windows come back without it being made frontmost.

    Found in the 2026-10-07 comparison: NLC asked the person to click TextEdit
    in the Dock where Sky went on. Measured after the fix: the hidden TextEdit
    was observed with its windows and the focus witness saw no activation.
    """
    def unhide(self, hidden=True, unhid=True, fronts=(10, 10)):
        from tacto import driver
        calls = []
        app = type("App", (), {"isHidden": lambda s: hidden,
                               "unhide": lambda s: calls.append("unhide") or unhid})()
        fake = type("NSRunningApplication", (), {
            "runningApplicationWithProcessIdentifier_": staticmethod(lambda pid: app)})
        seq = iter(fronts)
        with patch("AppKit.NSRunningApplication", fake), \
             patch("tacto.native_frontmost.frontmost_pid", side_effect=lambda: next(seq)), \
             patch.object(driver, "AXUIElementCreateApplication", return_value="ax"), \
             patch.object(driver, "_attr", return_value=["w"]):
            return driver._unhide_quietly(1, wait=0.1), calls

    def test_a_hidden_app_is_unhidden_and_focus_stays(self):
        self.assertEqual((True, ["unhide"]), self.unhide())

    def test_a_visible_app_is_left_alone(self):
        self.assertEqual((False, []), self.unhide(hidden=False))

    def test_if_focus_moved_it_is_reported_not_trusted(self):
        self.assertEqual(False, self.unhide(fronts=(10, 11))[0])

    def test_unknown_focus_means_no_unhide(self):
        from tacto import driver
        from tacto.native_frontmost import FrontmostUnavailable
        app = type("App", (), {"isHidden": lambda s: True,
                               "unhide": lambda s: self.fail("unhid without knowing focus")})()
        fake = type("NSRunningApplication", (), {
            "runningApplicationWithProcessIdentifier_": staticmethod(lambda pid: app)})
        with patch("AppKit.NSRunningApplication", fake), \
             patch("tacto.native_frontmost.frontmost_pid", side_effect=FrontmostUnavailable("x")):
            self.assertFalse(driver._unhide_quietly(1))


class HelperPathUnhideTests(unittest.TestCase):
    def test_a_hidden_app_on_the_helper_path_is_unhidden_and_observed_again(self):
        from tacto import helper
        d = object.__new__(helper.HelperDriver)
        d._last_action_at = 0
        replies = iter([{"type": "error", "error": "no_window_content", "target": "t"},
                        {"type": "observation", "obs_id": "o", "tree": "0 Window"}])
        d._send = lambda msg: next(replies)
        with patch.object(helper, "_explain_no_window", return_value={"reason": "app_hidden"}), \
             patch.object(helper, "_unhide_target", return_value=True):
            self.assertEqual("observation", helper.HelperDriver.observe(d, "t")["type"])

    def test_without_an_unhide_the_reason_is_reported(self):
        from tacto import helper
        d = object.__new__(helper.HelperDriver)
        d._last_action_at = 0
        d._send = lambda msg: {"type": "error", "error": "no_window_content", "target": "t"}
        with patch.object(helper, "_explain_no_window", return_value={"reason": "app_hidden"}), \
             patch.object(helper, "_unhide_target", return_value=False):
            self.assertEqual("app_hidden", helper.HelperDriver.observe(d, "t")["reason"])


class HelperUnhideReobservationTests(unittest.TestCase):
    def test_latest_window_read_failure_is_preserved_after_one_unhide(self):
        from tacto import helper
        for failure in ({'type':'error','error':'window_list_unavailable',
                         'window_listing_diagnostic':{'status':'ax_error','ax_error':-25204}},
                        {'type':'error','error':'target_not_found'}):
            driver=object.__new__(helper.HelperDriver);driver._last_action_at=0
            replies=iter([{'type':'error','error':'no_window_content'},failure])
            driver._send=lambda message:next(replies)
            with patch.object(helper,'_explain_no_window',return_value={'reason':'app_hidden'}) as explain, \
                    patch.object(helper,'_unhide_target',return_value=True) as unhide:
                self.assertEqual(driver.observe('owned'),failure)
            self.assertEqual(unhide.call_count,1);self.assertEqual(explain.call_count,1)

    def test_latest_empty_window_reason_is_refreshed_without_second_unhide(self):
        from tacto import helper
        driver=object.__new__(helper.HelperDriver);driver._last_action_at=0
        replies=iter([{'type':'error','error':'no_window_content'},
                      {'type':'error','error':'no_window_content'}])
        driver._send=lambda message:next(replies)
        with patch.object(helper,'_explain_no_window',side_effect=[{'reason':'app_hidden'},
                {'reason':'no_visible_window'}]) as explain, \
                patch.object(helper,'_unhide_target',return_value=True) as unhide:
            self.assertEqual(driver.observe('owned')['reason'],'no_visible_window')
        self.assertEqual(unhide.call_count,1);self.assertEqual(explain.call_count,2)
