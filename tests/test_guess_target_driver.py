"""Target guessing works with any driver, including the signed helper's."""
import unittest
from tacto.authoring import guess_target


class HelperStyleDriver:
    """Like InheritedHelperDriver: a running-app list and no `_resolve`."""
    def list_targets(self):
        return {"apps": [{"id": "com.apple.finder", "displayName": "Finder"},
                         {"id": "com.apple.Safari", "displayName": "Safari"}]}


class GuessTargetTests(unittest.TestCase):
    def test_running_app_named_in_the_request_is_guessed_without_resolve(self):
        self.assertEqual("com.apple.finder", guess_target("In Finder, select target.txt", HelperStyleDriver()))

    def test_ambiguous_or_not_running_is_not_guessed(self):
        self.assertIsNone(guess_target("Finder and Safari", HelperStyleDriver()))
        self.assertIsNone(guess_target("TextEdit에 써줘", HelperStyleDriver()))


if __name__ == "__main__":
    unittest.main()
