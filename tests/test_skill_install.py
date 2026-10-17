"""skill/install.sh puts `nlc` on PATH and links the skill — without clobbering.

Reported from a Codex session (2026-09-28): the skill was there, `nlc` was
not — the installer only copied SKILL.md, and every copy was stale. These run
the real script against a throwaway HOME.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skill" / "install.sh"


class SkillInstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.bin = self.home / ".local" / "bin"
        for d in (".claude/skills", ".codex/skills"):
            (self.home / d).mkdir(parents=True)
        self.addCleanup(self.tmp.cleanup)

    def run_script(self, *args):
        env = {"HOME": str(self.home), "PATH": f"{self.bin}:/usr/bin:/bin",
               "NLC_BIN_DIR": str(self.bin)}
        return subprocess.run(["bash", str(SCRIPT), *args], env=env,
                              capture_output=True, text=True, timeout=30)

    def entry(self):
        for candidate in (ROOT / "launcher" / "nlc",):
            if os.access(candidate, os.X_OK):
                return candidate
        self.skipTest("no built entry point in this checkout")

    def test_installs_the_command_and_links_the_skill_everywhere(self):
        entry = self.entry()
        r = self.run_script()
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        self.assertEqual(str(entry), os.readlink(self.bin / "nlc"))
        for d in (".claude/skills/tacto", ".codex/skills/tacto"):
            self.assertEqual(str(ROOT / "skill"), os.readlink(self.home / d))

    def test_a_stale_copy_of_our_skill_is_replaced_by_a_link(self):
        self.entry()
        stale = self.home / ".codex/skills/tacto"
        stale.mkdir()
        (stale / "SKILL.md").write_text("---\nname: nlc\n---\nold\n")
        self.assertIn("stale copy", self.run_script("--check").stdout)
        self.run_script()
        self.assertTrue(stale.is_symlink())

    def test_another_skill_by_that_name_is_left_alone(self):
        self.entry()
        other = self.home / ".claude/skills/tacto"
        other.mkdir()
        (other / "SKILL.md").write_text("---\nname: something-else\n---\n")
        r = self.run_script()
        self.assertEqual(1, r.returncode, r.stdout)
        self.assertFalse(other.is_symlink())
        self.assertIn("not the Tacto skill", r.stdout)
        self.assertTrue((self.home / ".codex/skills/tacto").is_symlink())

    def test_another_program_called_nlc_is_left_alone(self):
        self.entry()
        self.bin.mkdir(parents=True)
        (self.bin / "nlc").write_text("#!/bin/sh\necho not ours\n")
        r = self.run_script()
        self.assertNotEqual(0, r.returncode)
        self.assertEqual("#!/bin/sh\necho not ours\n", (self.bin / "nlc").read_text())

    def test_check_changes_nothing(self):
        self.entry()
        r = self.run_script("--check")
        self.assertEqual(1, r.returncode)
        self.assertIn("missing", r.stdout)
        self.assertFalse((self.bin / "nlc").exists())
        self.assertFalse((self.home / ".claude/skills/tacto").exists())


if __name__ == "__main__":
    unittest.main()


class SandboxTests(unittest.TestCase):
    """Inside an agent sandbox the screen is unreachable; say so, not "grant permissions".

    Measured with Codex's workspace-write sandbox (2026-09-28): `nlc apps` said
    accessibility_permission_missing and `nlc doctor` asked for a permission
    that was already granted. sandbox_check() answered 1 inside, 0 outside.
    """
    def test_this_process_is_not_sandboxed(self):
        from nlc.session import sandboxed
        self.assertFalse(sandboxed())

    def test_screen_commands_stop_with_the_real_cause_and_the_rest_still_work(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from unittest.mock import patch
        from nlc import cli
        for cmd in ("doctor", "apps", "observe", "run", "replay", "do", "eval"):
            out = StringIO()
            with patch("nlc.session.sandboxed", return_value=True), redirect_stdout(out):
                self.assertEqual(3, cli.main([cmd, "x"]), cmd)
            self.assertIn("샌드박스", out.getvalue())
            self.assertIn("권한 문제가 아니니", out.getvalue())
        with patch("nlc.session.sandboxed", return_value=True), redirect_stdout(StringIO()):
            self.assertEqual(0, cli.main(["list"]))

    def test_the_skill_tells_an_agent_what_to_do_in_a_sandbox(self):
        skill = "\n".join(p.read_text() for p in [ROOT / "skill/SKILL.md", *sorted((ROOT / "skill/references").glob("*.md"))])
        self.assertIn("require_escalated", skill)
        self.assertIn("danger-full-access", skill)
        self.assertIn("exit code 3", skill)


class DoctorUnderTheLauncherTests(unittest.TestCase):
    """`nlc doctor` asked to grant a helper that was granted and working."""
    def test_under_the_launcher_the_inherited_channel_is_what_is_checked(self):
        from unittest.mock import patch
        from nlc import onboard
        with patch("nlc.helper.InheritedHelperDriver.available", return_value=True), \
             patch("nlc.helper.InheritedHelperDriver.__init__", return_value=None), \
             patch("nlc.helper.InheritedHelperDriver.__new__",
                   side_effect=lambda cls: object.__new__(cls)), \
             patch("nlc.helper.HelperDriver", side_effect=AssertionError("dialled directly")), \
             patch("os.path.exists", return_value=True):
            st = onboard.helper_status()
        self.assertEqual((True, "launcher"), (st["trusted"], st["channel"]))

    def test_a_real_refusal_on_that_channel_is_still_reported(self):
        from unittest.mock import patch
        from nlc import onboard
        from nlc.helper import HelperUnavailable
        with patch("nlc.helper.InheritedHelperDriver.available", return_value=True), \
             patch("nlc.helper.InheritedHelperDriver.__new__",
                   side_effect=HelperUnavailable("도우미에 접근성 권한이 없습니다")), \
             patch("os.path.exists", return_value=True):
            st = onboard.helper_status()
        self.assertFalse(st["trusted"])
        self.assertIn("접근성 권한이 없습니다", st["why"])


class ScreenRecordingLineTests(unittest.TestCase):
    """doctor said "enable this app" for screen recording without naming it."""
    def check(self):
        from nlc.onboard import Check
        return Check("screen_recording", False, "화면 읽기 권한(화면 기록)", "…", "x-apple:…")

    def test_the_helpers_own_answer_is_reported_when_it_does_the_work(self):
        from nlc.onboard import _screen_recording_for
        c = _screen_recording_for(self.check(), {"built": True, "trusted": True, "screen_capture": True})
        self.assertTrue(c.ok)
        self.assertIn("Tacto Helper", c.title)

    def test_otherwise_the_interpreter_is_named(self):
        import os, sys
        from nlc.onboard import _screen_recording_for
        c = _screen_recording_for(self.check(), {})
        self.assertFalse(c.ok)
        self.assertIn(os.path.basename(os.path.realpath(sys.executable)), c.fix_hint)

    def test_the_helper_reports_screen_capture_in_its_handshake(self):
        source = (ROOT / "helper" / "TactoHelper.swift").read_text()
        self.assertIn('"screen_capture": CGPreflightScreenCaptureAccess()', source)


class KeepAwakeTests(unittest.TestCase):
    """Every command that touches the screen holds the display awake, not only `do`."""
    def test_screen_commands_hold_the_display_awake(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from unittest.mock import patch
        from nlc import cli
        for cmd in ("observe", "run", "replay", "eval", "apps", "doctor"):
            held = []
            with patch("nlc.session.sandboxed", return_value=False), \
                 patch("nlc.session.KeepAwake.__enter__", side_effect=lambda: held.append(1)), \
                 redirect_stdout(StringIO()):
                try:
                    cli.main([cmd, "--no-such-thing-xyz"])
                except BaseException:
                    pass
            self.assertEqual([1], held, cmd)

    def test_it_is_display_and_idle_sleep_and_it_dies_with_the_command(self):
        import inspect
        from nlc.session import KeepAwake
        src = inspect.getsource(KeepAwake.__enter__)
        self.assertIn('"-di"', src)
        self.assertIn('"-w"', src)


class FocusedWindowViewTests(unittest.TestCase):
    """observe shows the window being worked on; the others by title only."""
    TREE = ("0 Window  Title: Notes, Identifier: a\n  1 TextArea  Value: x\n"
            "2 Window  Title: Draft [focused]\n  3 TextArea  Value: y\n"
            "4 Window  Title: Other\n  5 Button  Title: OK")

    def test_only_the_focused_window_with_indices_unchanged(self):
        from nlc.cli import focused_window_view
        out = focused_window_view(self.TREE)
        self.assertIn("2 Window  Title: Draft [focused]", out)
        self.assertIn("3 TextArea  Value: y", out)
        self.assertNotIn("1 TextArea", out)
        self.assertIn("다른 창 2개: Notes, Other", out)

    def test_one_window_is_printed_as_is(self):
        from nlc.cli import focused_window_view
        tree = "0 Window  Title: Calc\n  1 Button  Title: 1"
        self.assertEqual(tree, focused_window_view(tree))


class AgentFrictionTests(unittest.TestCase):
    """What one traced Codex session tripped on, writing into TextEdit (2026-10-07)."""
    def test_run_and_replay_help_print_usage_instead_of_crashing(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from nlc import cli
        for cmd in ("run", "replay"):
            out = StringIO()
            with redirect_stdout(out):
                self.assertEqual(0, cli.main([cmd, "--help"]))
            self.assertIn("usage:", out.getvalue())

    def test_a_built_in_recipe_comes_from_the_code_not_a_stale_saved_copy(self):
        from unittest.mock import patch
        from nlc import cli
        from nlc.library import builders
        with patch("nlc.cli.FlowStore") as store:
            flow = cli._load_recipe("textedit_write_note")
        store.return_value.load.assert_not_called()
        self.assertEqual(builders()["textedit_write_note"]().to_json(), flow.to_json())

    def test_an_unsaved_document_write_is_reported_as_unverified_not_failed(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from unittest.mock import patch
        from nlc import cli
        from nlc.flow import Flow
        result = type("R", (), {"ok": False, "log": [{"status": "unverified",
                                                       "error": "commit_unverified",
                                                       "effect": "unknown"}]})()
        agent = patch("nlc.cli.Agent").start()
        self.addCleanup(patch.stopall)
        agent.return_value.authorize.return_value = ({}, [])
        agent.return_value.commit_adapter_hooks.return_value = {}
        out = StringIO()
        with patch("nlc.flow.replay", return_value=result), patch("nlc.cli._make_driver"), \
             patch("nlc.cli._indicator_for"), redirect_stdout(out):
            cli._run_flow(Flow("f", "t"), ["--yes"])
        self.assertTrue(out.getvalue().startswith("unverified"))

    def test_the_skill_points_to_built_ins_and_its_docs_resolve(self):
        skill = ROOT / "skill"
        self.assertIn("tacto list", (skill / "SKILL.md").read_text())
        self.assertTrue((skill / "docs" / "recipes.md").is_file())


class DoctorCheckIsReadOnlyTests(unittest.TestCase):
    """`nlc doctor --check` opened System Settings when the helper was untrusted
    (Codex finding, 2026-10-07): a check must not change what is on screen."""
    def run_doctor(self, interactive):
        from unittest.mock import patch
        from nlc import onboard
        opened = []
        status = {"built": True, "trusted": False, "bundled": True, "path": "x",
                  "why": "도우미에 접근성 권한이 없습니다"}
        with patch.object(onboard, "helper_status", return_value=status), \
             patch.object(onboard, "open_settings", side_effect=opened.append), \
             patch.object(onboard, "request_accessibility", return_value=False):
            ok = onboard.run(interactive=interactive, narrate=lambda s: None)
        return ok, opened

    def test_check_never_opens_settings_and_is_not_ready(self):
        ok, opened = self.run_doctor(interactive=False)
        self.assertEqual((False, []), (ok, opened))

    def test_interactive_doctor_may_open_the_pane(self):
        ok, opened = self.run_doctor(interactive=True)
        self.assertFalse(ok)
        self.assertTrue(opened)
