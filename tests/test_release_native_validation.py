"""Release result predicates accept current native quoting and reject ambiguity."""
import unittest
from examples.validate_native_release import observed

class NativeReleasePredicates(unittest.TestCase):
    def test_quoted_and_unquoted_titles_identify_the_same_window(self):
        for title in ('Tacto-A', '"Tacto-A"'):
            tree = '0 Window  Title: ' + title + ', Identifier: "FinderWindow" [focused]'
            self.assertTrue(observed(tree, role='Window', title='Tacto-A'))

    def test_another_window_with_a_similar_title_does_not_pass(self):
        self.assertFalse(observed('0 Window  Title: "Tacto-A-copy", Identifier: "FinderWindow"',
                                  role='Window', title='Tacto-A'))

    def test_ambiguous_windows_do_not_pass(self):
        self.assertFalse(observed('0 Window  Title: "Tacto-A"\n1 Window  Title: "Tacto-A"',
                                  role='Window', title='Tacto-A'))

    def test_window_and_heading_are_separate_required_observations(self):
        tree='0 Window  Title: "Computer - Wikipedia"\n  1 Heading  Title: Computer, Value: 1'
        self.assertTrue(observed(tree, role='Window', title='Computer - Wikipedia'))
        self.assertTrue(observed(tree, role='Heading', title='Computer'))
        self.assertFalse(observed(tree, role='Heading', title='Computer hardware'))
