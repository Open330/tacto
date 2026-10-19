"""A coordinate is an address nobody has resolved yet.

Controls that draw themselves expose no accessibility element of their own, so
an `element_index` cannot reach them: canvases, custom widgets, anything an app
renders without telling AX about it. They got `needs_pointer` and a handoff.

The way out is not synthetic input. Codex Computer Use does not post mouse
events on macOS at all — `nm -u` on all three of its binaries shows the whole
AXUIElement surface, including `AXUIElementCopyElementAtPosition`, and not one
event-posting symbol: no CGEventPost, no CGEventPostToPid, no
CGWarpMouseCursorPosition (2026-09-21). That is WHY it does not take focus, and
the "virtual pointer" is this: a coordinate read off a screenshot, translated
into the real element underneath, pressed like any other.

So these tests are about the translation staying a translation — the thing
pressed is a real element, in the app the caller named, and nothing is faked.
"""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HELPER = (ROOT / "helper/TactoHelper.swift").read_text()
LAUNCHER = (ROOT / "launcher/tacto.swift").read_text()
DRIVER = (ROOT / "tacto/driver.py").read_text()


class NothingIsSynthesised(unittest.TestCase):
    def test_no_event_posting_came_back_with_it(self):
        for name, text in (("helper", HELPER), ("driver", DRIVER)):
            with self.subTest(side=name):
                for symbol in ("CGEventPost", "CGEventCreateMouseEvent",
                               "CGWarpMouseCursorPosition", "cghidEventTap"):
                    for line in text.splitlines():
                        stripped = line.strip()
                        if stripped.startswith("//") or stripped.startswith("#"):
                            continue
                        self.assertNotIn(symbol, stripped,
                                         f"{name} is synthesising input again")

    def test_the_point_is_resolved_not_clicked_at(self):
        self.assertIn("AXUIElementCopyElementAtPosition", HELPER)
        self.assertIn("AXUIElementCopyElementAtPosition", DRIVER)

    def test_no_activation_is_taken_to_make_it_work(self):
        # The old failure was faking activation so a click could be aimed into
        # a background window. Resolving a point needs none of that.
        body = HELPER[HELPER.index("func elementAtPoint("):]
        body = body[:body.index("\n}\n")]
        self.assertNotIn("activate", body)
        self.assertNotIn("Frontmost", body)


class ThePointStaysInsideWhatWasNamed(unittest.TestCase):
    def test_the_helper_refuses_another_app_s_element(self):
        # The system-wide element answers about whatever is under a coordinate,
        # including an app the caller never named.
        body = HELPER[HELPER.index("func elementAtPoint("):]
        body = body[:body.index("\n}\n")]
        self.assertIn("AXUIElementGetPid", body)
        self.assertIn("owner == p", body)

    def test_the_driver_refuses_another_app_s_element(self):
        body = DRIVER[DRIVER.index("def _element_at_point"):]
        body = body[:body.index("\n    def ", 10)]
        self.assertIn("AXUIElementGetPid", body)
        self.assertIn("owner != obs.pid", body)

    def test_the_point_is_bounds_checked_against_the_window(self):
        self.assertIn("창 밖입니다", DRIVER)
        body = HELPER[HELPER.index("    func element() -> AXUIElement? {"):]
        body = body[:body.index("\n    }\n")]
        self.assertIn("x < size.width", body)
        self.assertIn("y < size.height", body)


class WindowIndexMeansOneThing(unittest.TestCase):
    """`AXWindows` is not a list of windows.

    Measured on Calculator with a tooltip up: index 0 was an `AXHelpTag` 85x20
    and the real window was index 1. A `window_index` that means different
    things depending on where the mouse is resting is worse than a wrong index
    — it is one that is right sometimes.
    """

    def test_the_helper_indexes_real_windows_only(self):
        self.assertIn("func realWindows(of app: AXUIElement)", HELPER)
        self.assertIn("kAXWindowRole", HELPER)

    def test_capture_and_the_point_path_use_the_same_list(self):
        self.assertGreaterEqual(HELPER.count("realWindows(of: app)"), 2)

    def test_the_driver_filters_too(self):
        body = DRIVER[DRIVER.index("def _real_windows(pid):"):]
        body = body[:body.index("\ndef ", 10)]
        self.assertIn('_attr(w, "AXRole") == "AXWindow"', body)
        self.assertIn('_attr(window, "AXCloseButton") is None', body)

    def test_the_badge_never_reaches_an_observation(self):
        """It did, and it was ours.

        `_real_windows` was applied to capture and to point-addressing but not
        to the walk that builds the tree, so while the control indicator was up
        every observation of the app being driven began with a phantom
        `Window  Title: Window, Identifier: _NS:8` — and every element index
        behind it moved. One filter, used everywhere an app's windows are
        listed, or it is not a filter.
        """
        helper_walk = HELPER[HELPER.index("let processStart = nativeProcessStartIdentity(p)"):]
        helper_walk = helper_walk[:helper_walk.index("if windows.isEmpty")]
        self.assertIn("realWindows(of: app, diagnostic: &windowListing)", helper_walk)
        driver_walk = DRIVER[DRIVER.index("def _walk_windows"):]
        driver_walk = driver_walk[:driver_walk.index("\n    def ", 10)]
        self.assertIn("_real_windows(obs.pid)", driver_walk)

    def test_the_capture_badge_is_not_counted_as_a_window(self):
        # Holding the control indicator makes macOS attach its capture badge to
        # the window being captured, and the badge appears in that app's own
        # AXWindows list — so saying "NLC is working on this window" was what
        # made that window ambiguous. Measured at 66x20 inside 460x232, subrole
        # AXDialog, title "Window", no close button (2026-09-21).
        for name, text in (("helper", HELPER), ("driver", DRIVER)):
            with self.subTest(side=name):
                self.assertIn("CloseButton", text)

    def test_the_point_is_asked_of_the_app_not_the_screen(self):
        # System-wide hit testing is a z-order question: measured, a point
        # inside a background window returned an AXWebArea belonging to a
        # browser that happened to be on top.
        body = HELPER[HELPER.index("func elementAtPoint("):]
        body = body[:body.index("\n}\n")]
        self.assertIn("AXUIElementCreateApplication(p)", body)
        self.assertNotIn("AXUIElementCreateSystemWide", body)
        driver_body = DRIVER[DRIVER.index("def _element_at_point"):]
        driver_body = driver_body[:driver_body.index("\n    def ", 10)]
        self.assertIn("AXUIElementCreateApplication(obs.pid)", driver_body)
        self.assertNotIn("AXUIElementCreateSystemWide", driver_body)


class TheLauncherLetsExactlyOneAddressThrough(unittest.TestCase):
    def test_click_takes_an_index_or_a_point(self):
        self.assertIn('"click":            (["name"], ["element_index", "point", "window_index"])',
                      LAUNCHER)

    def test_both_at_once_is_refused(self):
        self.assertIn("은 element_index 또는 point 중 하나만 받습니다", LAUNCHER)
        self.assertIn("에 element_index 또는 point 가 필요합니다", LAUNCHER)

    def test_the_point_shape_is_checked(self):
        self.assertIn('Set(raw.keys) == Set(["x", "y"])', LAUNCHER)
        self.assertIn("isFinite", LAUNCHER)

    def test_window_index_needs_a_point_to_go_with(self):
        self.assertIn("window_index 는 point 와 함께만 씁니다", LAUNCHER)


class BothSidesAnnounceIt(unittest.TestCase):
    def test_the_capability_is_declared_on_both(self):
        self.assertIn('"action.click.by_point"', HELPER)
        self.assertIn('"action.click.by_point"', DRIVER)


if __name__ == "__main__":
    unittest.main()


class TheTreeStaysParseable(unittest.TestCase):
    """Naming the actions must not change what a selector reads.

    A tree line is `Key: value, Key2: value`, and a value runs until the next
    KNOWN key. Appending `Secondary Actions: ShowMenu` without registering the
    key left the value running to end of line, so
    `Description: First Text View` became
    `First Text View, Secondary Actions: ShowMenu` — and four save scenarios
    stopped finding their targets the moment observations started naming
    actions. Two of them had been failing for a while and were read as
    environment.
    """

    def test_the_key_terminates_the_value_before_it(self):
        from tacto.flow import Selector
        line = "  12 TextArea  Description: First Text View, Secondary Actions: ShowMenu"
        self.assertEqual(Selector._attr(line, "Description"), "First Text View")
        self.assertEqual(Selector._attr(line, "Secondary Actions"), "ShowMenu")

    def test_an_ordinary_line_is_unaffected(self):
        from tacto.flow import Selector
        line = "  5 Button  Description: 7, Identifier: Seven"
        self.assertEqual(Selector._attr(line, "Description"), "7")
        self.assertEqual(Selector._attr(line, "Identifier"), "Seven")

    def test_a_selector_still_matches_past_the_actions(self):
        from tacto.flow import Selector
        line = "  12 TextArea  Description: First Text View, Secondary Actions: ShowMenu"
        self.assertTrue(Selector(role="TextArea", desc="First Text View").matches(line))
        # And a wrong one still does not: terminating the value must not have
        # turned exact matching back into "starts with".
        self.assertFalse(Selector(role="TextArea", desc="First Text").matches(line))

    def test_the_marker_keys_still_terminate_too(self):
        from tacto.flow import Selector
        line = ("  3 Button  Description: Mode, Secondary Actions: ShowMenu, "
                "Increment [disabled]")
        self.assertEqual(Selector._attr(line, "Description"), "Mode")
