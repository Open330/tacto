"""A role token never carries the separator comma (KakaoTalk, 2026-10-10).

A labelless element with secondary actions printed "3 Cell, Secondary Actions:
ShowMenu"; the role token read "Cell," and click_ancestor ["Cell"] and
role="Cell" matched nothing. Rendering no longer puts a comma there, and
parsing tolerates lines from helpers that still do."""
import pathlib
import unittest

from tacto.flow import Selector, _same_role

ROOT = pathlib.Path(__file__).resolve().parents[1]
OLD = """0 Window  Title: KakaoTalk
  1 Table
    2 Row
      3 Cell, Secondary Actions: ShowMenu
        4 StaticText  Value: 김형철, 대태호"""
NEW = OLD.replace("3 Cell, Secondary Actions", "3 Cell  Secondary Actions")


class RoleTokenCommaTests(unittest.TestCase):
    def test_same_role_ignores_a_trailing_comma(self):
        self.assertTrue(_same_role("Cell,", "Cell"))
        self.assertTrue(_same_role("AXCell,", "cell"))
        self.assertFalse(_same_role("Cell,", "Row"))

    def test_click_ancestor_and_role_selectors_on_both_renderings(self):
        for tree in (OLD, NEW):
            with self.subTest(tree=tree.splitlines()[3]):
                idx, _ = Selector(role="StaticText", value="김형철, 대태호",
                                  click_ancestor=["Cell"]).resolve(tree)
                self.assertEqual(3, idx)
                self.assertTrue(Selector(role="Cell").matches(tree.splitlines()[3]))
                self.assertEqual("ShowMenu", Selector._attr(tree.splitlines()[3], "Secondary Actions"))

    def test_the_driver_puts_no_comma_after_a_bare_role(self):
        driver = (ROOT / "tacto/driver.py").read_text()
        self.assertIn('("  " if not (label or disabled or selected) else ", ")', driver)


if __name__ == "__main__":
    unittest.main()
