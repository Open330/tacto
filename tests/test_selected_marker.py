"""[selected] marks the chosen button of a set, and does not leak into values.

2026-10-09: System Settings' Light / Dark / Auto showed no selection in the
tree, so "which appearance is set?" could not be answered (TACTO 0/2)."""
import pathlib
import unittest

from tacto.driver import _line_attr
from tacto.flow import Selector

ROOT = pathlib.Path(__file__).resolve().parents[1]
LINE = "136 Button  Description: Light, Help: Use a light appearance. [selected]"


class SelectedMarkerTests(unittest.TestCase):
    def test_value_parsing_stops_at_the_marker(self):
        self.assertEqual("Use a light appearance.", Selector._attr(LINE, "Help"))
        self.assertEqual("Use a light appearance.", _line_attr(LINE, "Help"))
        disabled = "5 Button  Title: Save [disabled] [selected]"
        self.assertEqual("Save", Selector._attr(disabled, "Title"))

    def test_selectors_still_resolve_the_marked_line(self):
        tree = ("0 Window  Title: Appearance\n  " + LINE + "\n"
                "  137 Button  Description: Dark, Help: Use a dark appearance.")
        idx, _ = Selector(role="Button", desc="Light").resolve(tree)
        self.assertEqual(136, idx)

    def test_both_backends_render_it_only_for_true(self):
        swift = (ROOT / "helper/TactoHelper.swift").read_text()
        self.assertIn('(attr(el, kAXSelectedAttribute as String) as? Bool) == true', swift)
        self.assertIn('(selected ? " [selected]" : "")', swift)
        driver = (ROOT / "tacto/driver.py").read_text()
        self.assertIn('" [selected]" if _attr(el, "AXSelected") is True else ""', driver)


if __name__ == "__main__":
    unittest.main()
