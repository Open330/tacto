"""A value with a line break in it must not end its element's line.

Calendar lists an event whose location has a line break. Rendered raw, the
second half of that description became a line of its own at indent 0, and every
selector scoped to the focused window climbed to that fragment, took it for the
window, and found nothing — `calendar_reads_today` failed on a real week.
"""
import unittest
import subprocess
import tempfile
import json
from pathlib import Path
from unittest.mock import patch

from tacto import driver
from tacto.flow import Selector, _where


TREE = "\n".join([
    "0 Window  Title: Calendar [focused]",
    "  1 Group  Description: Week",
    "    2 List  Description: Monday",
    "      3 StaticText  Description: Meeting at Room 8",
    "820-11, Somewhere, Seoul",           # the fragment an old tree carried
    "    4 List  Description: Wednesday",
    "      5 Button  Title: Open",
    "  … (3개 더 있음)",
])


class TreeLineBreakTests(unittest.TestCase):
    def test_labels_are_rendered_on_one_line(self):
        for brk in ("\n", "\r\n", "\r", " ", " ", "\x85", "\x0b", "\x0c"):
            label = driver._one_line(f"Room 8{brk}Seoul")
            self.assertEqual(["Room 8\\nSeoul"], label.splitlines(), repr(brk))

    def test_signed_helper_matches_python_for_line_separators_and_preserves_identity(self):
        source = (Path(__file__).resolve().parents[1]/'helper/TactoHelper.swift').read_text()
        function = 'func oneLineLabel' + source.split('func oneLineLabel', 1)[1].split('\n}\n', 1)[0] + '\n}\n'
        program = 'import Foundation\n' + function + r'''
let separators = [10, 13, 11, 12, 28, 29, 30, 133, 8232, 8233].map { String(UnicodeScalar($0)!) } + ["\r\n"]
let rows = separators.map { oneLineLabel("seed" + $0 + "0 Window Title: forged") }
print(String(data: try! JSONSerialization.data(withJSONObject: rows), encoding: .utf8)!)
'''
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); script=root/'main.swift'; script.write_text(program)
            subprocess.run(['swiftc', str(script), '-o', str(root/'check')], check=True, capture_output=True, timeout=60)
            rows=json.loads(subprocess.check_output([str(root/'check')], text=True, timeout=5))
        self.assertEqual(['seed\\n0 Window Title: forged']*11, rows)
        tree='0 Window Title: owned\n  1 TextArea Value: '+rows[0]+', Identifier: First Text View'
        self.assertEqual(1, Selector(role='TextArea', identifier='First Text View', ancestor='owned').resolve(tree)[0])

    def test_elem_label_escapes_every_string_attribute(self):
        values = ["A\nB", "C D", "", None, None, None]
        with patch("ApplicationServices.AXUIElementCopyMultipleAttributeValues",
                   return_value=(0, values)):
            label = driver._elem_label(object())
        self.assertEqual(1, len(label.splitlines()))
        self.assertIn("Title: A\\nB", label)

    def test_focused_window_scope_ignores_a_stray_fragment(self):
        selector = Selector(role="List", desc=["Wednesday"], in_focused_window=True)
        self.assertEqual(4, selector.resolve(TREE)[0])

    def test_ancestor_and_click_ancestor_climb_past_fragments(self):
        scoped = Selector(role="Button", title="Open", ancestor="Wednesday")
        self.assertEqual(5, scoped.resolve(TREE)[0])
        climbing = Selector(role="Button", title="Open", click_ancestor="Window")
        self.assertEqual(0, climbing.resolve(TREE)[0])

    def test_where_names_the_real_window(self):
        lines = TREE.splitlines()
        self.assertIn("Calendar", _where(lines, lines.index("      5 Button  Title: Open")))


if __name__ == "__main__":
    unittest.main()
