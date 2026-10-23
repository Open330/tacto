"""A list that keeps its own selection (Finder's column view) is selected
through the list and observed through it."""
from pathlib import Path
import unittest
ROOT = Path(__file__).resolve().parents[1]


class ListSelectionSourceTests(unittest.TestCase):
    def setUp(self):
        self.source = (ROOT / 'helper/TactoHelper.swift').read_text()

    def test_click_selects_through_the_list_and_only_a_read_back_counts(self):
        route = self.source[self.source.index('func selectListItem('):self.source.index('/// Everything this app currently has selected')]
        self.assertIn('AXUIElementIsAttributeSettable(list, kAXSelectedChildrenAttribute', route)
        # Finder reports an error even when the set took effect; the list decides.
        set_at = route.index('AXUIElementSetAttributeValue(list, kAXSelectedChildrenAttribute')
        self.assertLess(set_at, route.index('guard now.count == 1, CFEqual(now[0], item)'))
        self.assertIn('"effect": "unknown"', route)
        self.assertIn('abortState.isStopped', route[:set_at])
        self.assertNotIn('CGEvent', route)
        click = self.source[self.source.index('    case "click":'):self.source.index('    case "set_value":')]
        # Only for an element without AXPress, never inside a save panel, and
        # before the pointer route.
        selected = click.index('let selected = selectListItem(el, deadline: deadline)')
        self.assertLess(selected, click.index('return pointerClick()'))
        self.assertIn('!names.contains(kAXPressAction as String), savePanelToken == nil,', click)

    def test_a_row_is_selected_through_its_table_only_after_the_pointer_sent_nothing(self):
        route = self.source[self.source.index('func selectTableRow('):self.source.index('/// Everything this app currently has selected')]
        self.assertIn('AXUIElementIsAttributeSettable(table, kAXSelectedRowsAttribute', route)
        set_at = route.index('AXUIElementSetAttributeValue(table, kAXSelectedRowsAttribute')
        self.assertLess(set_at, route.index('guard now.count == 1, CFEqual(now[0], row)'))
        self.assertIn('abortState.isStopped', route[:set_at])
        click = self.source[self.source.index('    case "click":'):self.source.index('    case "set_value":')]
        fallback = click[click.index('let pointer = pointerClick()'):]
        fallback = fallback[:fallback.index('return pointer')]
        for condition in ('pointer["effect"] as? String == "not_started"',
                          '(pointer["posted_or_attempted_events"] as? Int ?? 0) == 0', 'savePanelToken == nil'):
            self.assertIn(condition, fallback)

    def test_observation_marks_items_a_list_holds_selected(self):
        walk = self.source[self.source.index('func walk(_ el: AXUIElement'):self.source.index('// MARK: - Session state')]
        self.assertIn('let selected = selectedByParent ||', walk)
        self.assertIn('attr(el, "AXSelectedChildren")', walk)
        self.assertEqual(2, walk.count('selectedByParent: selectedByParent || chosen.contains(child))'))


if __name__ == '__main__':
    unittest.main()
