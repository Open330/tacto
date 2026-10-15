"""Migration behavior with real installer runs and isolated user profiles."""
from tests import test_skill_install_isolated as fixtures
import unittest
import os


class TactoMigrationTests(unittest.TestCase):
    setUp = fixtures.IsolatedSkillInstallTests.setUp
    run_script = fixtures.IsolatedSkillInstallTests.run_script
    def test_primary_and_legacy_commands_share_signed_entry(self):
        self.assertEqual(0,self.run_script().returncode)
        for name in ('tacto','nlc'):
            self.assertEqual(str(self.entry),os.readlink(self.bin/name))
        self.assertEqual(0,self.run_script('--check').returncode)

    def test_owned_legacy_skill_link_is_backed_up_and_discovered_once(self):
        parent=self.home/'.codex/skills';parent.mkdir(parents=True)
        legacy=parent/'nlc';legacy.symlink_to(self.root/'skill')
        result=self.run_script()
        self.assertEqual(0,result.returncode,result.stdout+result.stderr)
        self.assertFalse(legacy.is_symlink())
        self.assertEqual(['tacto'],sorted(p.name for p in parent.iterdir()))
        backups=list(parent.parent.glob('.tacto-skill-backup.*/nlc'))
        self.assertEqual(1,len(backups))
        self.assertTrue(backups[0].is_symlink())
        self.assertEqual(0,self.run_script('--check').returncode)

    def test_foreign_tacto_command_prevents_both_link_mutations(self):
        self.bin.mkdir(parents=True)
        other=self.bin/'tacto';other.write_text('foreign program')
        result=self.run_script()
        self.assertEqual(1,result.returncode)
        self.assertEqual('foreign program',other.read_text())
        self.assertFalse((self.bin/'nlc').exists())

    def test_unrelated_legacy_skill_is_not_adopted_or_deleted(self):
        parent=self.home/'.codex/skills';legacy=parent/'nlc';legacy.mkdir(parents=True)
        (legacy/'SKILL.md').write_text('---\nname: unrelated\n---\nkeep me\n')
        result=self.run_script()
        self.assertEqual(0,result.returncode,result.stdout+result.stderr)
        self.assertTrue((parent/'tacto').is_symlink())
        self.assertIn('keep me',(legacy/'SKILL.md').read_text())

    def test_branded_bin_override_wins_over_legacy_override(self):
        branded=self.home/'branded bin'
        result=self.run_script(TACTO_BIN_DIR=str(branded))
        self.assertEqual(0,result.returncode,result.stdout+result.stderr)
        self.assertTrue((branded/'tacto').is_symlink())
        self.assertTrue((branded/'nlc').is_symlink())
        self.assertFalse(self.bin.exists())
