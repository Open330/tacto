"""The CLI picks the UI Automation driver on Windows and refuses --helper there."""
import sys
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
             patch("tacto.windows_control.control.__enter__", lambda self: self), \
             patch("tacto.windows_control.control.__exit__", lambda self, *exc: False), \
             patch.object(cli, "_dispatch", side_effect=lambda cmd, rest: dispatched.append((cmd, rest)) or 0):
            self.assertEqual(0, cli.main(["apps"]))
            self.assertEqual(2, cli.main(["doctor"]))
            # The notice flag is consumed before the command parses its args.
            self.assertEqual(0, cli.main(["observe", "notepad", "--no-indicator"]))
        self.assertEqual([("apps", []), ("observe", ["notepad"])], dispatched)

    def test_run_and_do_never_load_macos_modules_on_windows(self):
        import sys
        blocked = dict.fromkeys(("tacto.driver", "tacto.overlay", "tacto.onboard", "tacto.session",
                                 "AppKit", "Quartz", "ApplicationServices"))
        with patch.object(cli.sys, "platform", "win32"), patch.dict(sys.modules, blocked):
            with cli._indicator_for("notepad", []):
                pass
            from pathlib import Path
            source = Path(cli.__file__).read_text(encoding="utf-8")
        do = source[source.index('    if cmd == "do":'):]
        do = do[:do.index('learning = "--learn" in rest')]
        self.assertIn('if sys.platform != "win32":', do)
        self.assertLess(do.index('if sys.platform != "win32":'), do.index("from . import onboard"))

    def test_scroll_amounts_match_uia_enum(self):
        import ast, pathlib
        tree = ast.parse(pathlib.Path(cli.__file__).with_name("uia_backend.py").read_text())
        values = {}
        for node in ast.walk(tree):
            if (isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Tuple)
                    and isinstance(node.value, ast.Tuple)):
                for name, value in zip(node.targets[0].elts, node.value.elts):
                    if isinstance(name, ast.Name) and isinstance(value, ast.Constant):
                        values[name.id] = value.value
        # UIA ScrollAmount: LargeDecrement 0, SmallDecrement 1, NoAmount 2, LargeIncrement 3.
        self.assertEqual({"LARGE_DECREMENT": 0, "NO_AMOUNT": 2, "LARGE_INCREMENT": 3},
                         {k: values[k] for k in ("LARGE_DECREMENT", "NO_AMOUNT", "LARGE_INCREMENT")})

    def test_authoring_names_windows_apps_by_executable(self):
        from tacto import authoring
        class Driver:
            def list_targets(self):
                return {"apps": [{"id": "notepad.exe", "displayName": "Notepad", "pid": 1, "isRunning": True}]}
            def _resolve(self, target, launch=False):
                return 1 if target == "notepad.exe" else None
        prompts = []
        with patch.object(authoring.sys, "platform", "win32"):
            self.assertEqual("notepad.exe", authoring.guess_target("메모장에 적어줘", Driver()))
            self.assertIsNone(authoring.guess_target("계산기로 계산", Driver()))
            with patch.object(authoring, "reach_model", side_effect=lambda prompt, provider: prompts.append(prompt) or "notepad.exe"):
                self.assertEqual("notepad.exe", authoring.pick_target("write a note", Driver()))
        self.assertIn("executable name", prompts[0])
        self.assertNotIn("bundle identifier", prompts[0])
        with patch.object(authoring.sys, "platform", "darwin"):
            self.assertEqual(("bundle identifier", "com.apple.calculator"), authoring._target_kind())

    @unittest.skipUnless(sys.platform == "darwin", "tacto.driver imports macOS frameworks")
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
