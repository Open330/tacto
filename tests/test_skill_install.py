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
        for candidate in (ROOT / "launcher" / "nlc", ROOT / ".venv" / "bin" / "nlc"):
            if os.access(candidate, os.X_OK):
                return candidate
        self.skipTest("no built entry point in this checkout")

    def test_installs_the_command_and_links_the_skill_everywhere(self):
        entry = self.entry()
        r = self.run_script()
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        self.assertEqual(str(entry), os.readlink(self.bin / "nlc"))
        for d in (".claude/skills/nlc", ".codex/skills/nlc"):
            self.assertEqual(str(ROOT / "skill"), os.readlink(self.home / d))

    def test_a_stale_copy_of_our_skill_is_replaced_by_a_link(self):
        self.entry()
        stale = self.home / ".codex/skills/nlc"
        stale.mkdir()
        (stale / "SKILL.md").write_text("---\nname: nlc\n---\nold\n")
        self.assertIn("stale copy", self.run_script("--check").stdout)
        self.run_script()
        self.assertTrue(stale.is_symlink())

    def test_another_skill_by_that_name_is_left_alone(self):
        self.entry()
        other = self.home / ".claude/skills/nlc"
        other.mkdir()
        (other / "SKILL.md").write_text("---\nname: something-else\n---\n")
        r = self.run_script()
        self.assertEqual(0, r.returncode, r.stdout)
        self.assertFalse(other.is_symlink())
        self.assertIn("not the NLC skill", r.stdout)
        self.assertTrue((self.home / ".codex/skills/nlc").is_symlink())

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
        self.assertEqual(0, r.returncode)
        self.assertIn("missing", r.stdout)
        self.assertFalse((self.bin / "nlc").exists())
        self.assertFalse((self.home / ".claude/skills/nlc").exists())


if __name__ == "__main__":
    unittest.main()
