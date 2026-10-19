"""The CLI picks the UI Automation driver on Windows and refuses --helper there."""
import unittest
from unittest.mock import patch

from tacto import cli


class WindowsCliTests(unittest.TestCase):
    def test_windows_uses_uia_driver_and_never_falls_back_to_helper(self):
        made = []
        class Fake:
            def __init__(self):
                made.append(self)
        with patch.object(cli.sys, "platform", "win32"), \
             patch("tacto.windows_driver.WindowsDriver", Fake):
            self.assertIsInstance(cli._make_driver([]), Fake)
            with self.assertRaises(SystemExit):
                cli._make_driver(["--helper"])
        self.assertEqual(1, len(made))

    def test_screen_commands_skip_macos_session_probes_on_windows(self):
        import sys
        dispatched = []
        blocked = dict.fromkeys(("tacto.session", "Quartz"))
        with patch.object(cli.sys, "platform", "win32"), \
             patch.dict(sys.modules, blocked), \
             patch("tacto.windows_driver.keep_awake.__enter__", lambda self: self), \
             patch("tacto.windows_driver.keep_awake.__exit__", lambda self, *exc: False), \
             patch.object(cli, "_dispatch", side_effect=lambda cmd, rest: dispatched.append(cmd) or 0):
            self.assertEqual(0, cli.main(["apps"]))
            self.assertEqual(2, cli.main(["doctor"]))
        self.assertEqual(["apps"], dispatched)

    def test_macos_selection_is_unchanged(self):
        with patch.object(cli.sys, "platform", "darwin"), \
             patch("tacto.driver.Driver") as driver, \
             patch("tacto.helper.InheritedHelperDriver") as helper:
            cli._make_driver([])
            cli._make_driver(["--helper"])
        driver.assert_called_once_with()
        helper.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
