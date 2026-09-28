"""no_window_content names ONE cause, so the person knows what to do.

Reported 2026-09-28: an agent reading Safari got "locked screen, hidden, or
minimized" while the lock state sat beside it in the same reply. The screen
was in fact locked.
"""
import unittest
from unittest.mock import patch


class NoWindowReasonTests(unittest.TestCase):
    def why(self, lock, hidden=False, windows=()):
        from nlc import driver
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

    def test_a_bare_helper_reply_is_explained_the_same_way(self):
        from nlc import helper
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
        skill = (Path(__file__).resolve().parents[1] / "skill" / "SKILL.md").read_text()
        for reason in ("screen_locked", "app_hidden", "minimized", "no_visible_window"):
            self.assertIn(reason, skill)
