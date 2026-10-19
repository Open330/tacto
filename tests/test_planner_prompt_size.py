"""A screen question carries instructions only for what that screen offers."""
import unittest

from tacto.ask import _HERE, here_instructions, what_to_do_here
from tacto.general import candidates

KEYPAD = """0 Window Title: Calculator
  1 Group Identifier: CalculatorKeypadView
    2 Button Identifier: One, Description: 1
    3 Button Identifier: Equals, Description: equals
  4 ScrollArea Identifier: StandardInputView, Value: 0"""


class PlannerPromptTests(unittest.TestCase):
    def test_keypad_question_omits_paragraphs_for_absent_actions(self):
        prompt = what_to_do_here('계산', candidates(KEYPAD)).prompt
        for absent in ('`inspect_menu`', '`upload`', '`select_option`', '`prepare_window`',
                       '`double_click`', '`submit`은', '"scope"', '"option_label"', '"key"'):
            self.assertNotIn(absent, prompt)
        for kept in ('"do": "click"|"read"|"scroll"', '`scroll`은', '`finishes`는',
                     '무시하십시오'):
            self.assertIn(kept, prompt)

    def test_location_is_said_once_per_run_of_candidates(self):
        prompt = what_to_do_here('계산', candidates(KEYPAD)).prompt
        self.assertEqual(1, prompt.count('안: CalculatorKeypadView'))
        self.assertIn('[안: CalculatorKeypadView / 창: Calculator]\n0. 1 [Button]', prompt)

    def test_an_offered_action_brings_its_paragraph_and_fields(self):
        text = here_instructions({'click', 'select_custom_option'})
        self.assertIn('`open_select`는', text)
        self.assertIn('"option_label"', text)
        self.assertIn('visible=false', text)
        self.assertNotIn('`upload`', text)

    def test_with_every_action_the_instructions_are_the_full_text(self):
        self.assertEqual(_HERE, here_instructions(None))
        for paragraph in ('`inspect_menu`', '`upload`', '`submit`은', '`double_click`',
                          '"key": "<press_key'):
            self.assertIn(paragraph, _HERE)


if __name__ == '__main__':
    unittest.main()
