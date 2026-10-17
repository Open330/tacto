"""Run the actual installer in fresh user/checkouts, without executing a GUI."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class IsolatedSkillInstallTests(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory(prefix='tacto-install-test-')
        self.addCleanup(work.cleanup)
        self.root = Path(os.path.realpath(work.name)) / 'checkout with spaces'
        self.home = Path(os.path.realpath(work.name)) / 'fresh user'
        self.home.mkdir()
        (self.root / 'skill').mkdir(parents=True)
        self.script = self.root / 'skill/install.sh'
        shutil.copyfile(ROOT / 'skill/install.sh', self.script)
        (self.root / 'skill/SKILL.md').write_text('---\nname: tacto\n---\nfixture\n')
        (self.root / 'launcher').mkdir()
        # Tests link ownership and discovery only, not cryptographic signing.
        self.entry = self.root / 'launcher/tacto'
        self.entry.write_text('#!/bin/sh\nexit 0\n')
        self.entry.chmod(0o755)
        self.bin = self.home / '.local/bin'

    def run_script(self, *args, **extra_env):
        env = {'HOME': str(self.home), 'PATH': f'{self.bin}:/usr/bin:/bin',
               'NLC_BIN_DIR': str(self.bin), **extra_env}
        return subprocess.run(['/bin/bash', str(self.script), *args],
                              env=env, capture_output=True, text=True, timeout=10)

    def test_fresh_user_gets_command_and_both_missing_skill_directories(self):
        installed = self.run_script()
        self.assertEqual(0, installed.returncode, installed.stdout + installed.stderr)
        self.assertEqual(str(self.entry), os.readlink(self.bin / 'nlc'))
        for directory in ('.codex', '.claude'):
            self.assertEqual(str(self.root / 'skill'),
                             os.readlink(self.home / directory / 'skills/tacto'))
        checked = self.run_script('--check')
        self.assertEqual(0, checked.returncode, checked.stdout + checked.stderr)

    def test_missing_check_fails_without_creating_directories(self):
        checked = self.run_script('--check')
        self.assertEqual(1, checked.returncode)
        self.assertEqual([], list(self.home.iterdir()))

    def test_installed_skill_path_finds_the_same_checkout(self):
        self.assertEqual(0, self.run_script().returncode)
        env = {'HOME': str(self.home), 'PATH': f'{self.bin}:/usr/bin:/bin',
               'NLC_BIN_DIR': str(self.bin)}
        for tool in ('.codex', '.claude'):
            script = self.home / tool / 'skills/tacto/install.sh'
            checked = subprocess.run(['/bin/bash', str(script), '--check'], env=env,
                                     capture_output=True, text=True, timeout=10)
            self.assertEqual(0, checked.returncode, checked.stdout + checked.stderr)
            self.assertIn('Tacto — ' + str(self.root), checked.stdout)

    def test_no_python_console_fallback_when_launcher_is_missing(self):
        self.entry.unlink()
        console = self.root / '.venv/bin/nlc'
        console.parent.mkdir(parents=True)
        console.write_text('#!/bin/sh\nexit 0\n')
        console.chmod(0o755)
        installed = self.run_script()
        self.assertEqual(1, installed.returncode)
        self.assertIn('launcher/tacto', installed.stdout)
        self.assertEqual([], list(self.home.iterdir()))

    def test_unrelated_command_symlink_is_preserved(self):
        self.bin.mkdir(parents=True)
        other = self.home / 'other-command'
        other.write_text('unrelated')
        (self.bin / 'nlc').symlink_to(other)
        installed = self.run_script()
        self.assertEqual(1, installed.returncode)
        self.assertEqual(str(other), os.readlink(self.bin / 'nlc'))
        self.assertEqual('unrelated', other.read_text())

    def test_legacy_launcher_pattern_does_not_authorize_foreign_skill(self):
        old = self.home/'foreign checkout'
        (old/'skill').mkdir(parents=True)
        (old/'skill/SKILL.md').write_text('---\nname: unrelated\n---\n')
        self.bin.mkdir(parents=True)
        link=self.bin/'nlc';link.symlink_to(old/'launcher/nlc')
        installed=self.run_script()
        self.assertEqual(1,installed.returncode)
        self.assertEqual(str(old/'launcher/nlc'),os.readlink(link))
        self.assertFalse((self.bin/'tacto').exists())

    def test_unrelated_and_broken_skill_links_are_preserved_and_reported(self):
        foreign = self.home / 'foreign-skill'
        foreign.mkdir()
        (foreign / 'SKILL.md').write_text('---\nname: another-skill\n---\n')
        for directory, target in (('.codex', foreign), ('.claude', self.home / 'missing')):
            destination = self.home / directory / 'skills/tacto'
            destination.parent.mkdir(parents=True)
            destination.symlink_to(target)
        installed = self.run_script()
        self.assertEqual(1, installed.returncode)
        self.assertEqual(str(foreign), os.readlink(self.home / '.codex/skills/tacto'))
        self.assertEqual(str(self.home / 'missing'), os.readlink(self.home / '.claude/skills/tacto'))
        self.assertIn('not the Tacto skill', installed.stdout)
        self.assertEqual(1, self.run_script('--check').returncode)

    def test_relocated_agent_roots_are_created_without_skipping_defaults(self):
        codex = self.home / 'codex relocated'
        claude = self.home / 'claude relocated'
        extra = {'CODEX_HOME': str(codex), 'CLAUDE_CONFIG_DIR': str(claude)}
        installed = self.run_script(**extra)
        self.assertEqual(0, installed.returncode, installed.stdout + installed.stderr)
        for parent in (codex, claude, self.home / '.codex', self.home / '.claude'):
            self.assertEqual(str(self.root / 'skill'), os.readlink(parent / 'skills/tacto'))
        self.assertEqual(0, self.run_script('--check', **extra).returncode)

    def test_previous_nlc_checkout_links_are_repaired(self):
        old = self.home / 'old checkout'
        (old / 'skill').mkdir(parents=True)
        (old / 'skill/SKILL.md').write_text('---\nname: nlc\n---\nold\n')
        self.bin.mkdir(parents=True)
        destination = self.home / '.codex/skills/tacto'
        destination.parent.mkdir(parents=True)
        destination.symlink_to(old / 'skill')
        for previous_entry in ('launcher/nlc', 'launcher/tacto', '.venv/bin/nlc', '.venv/bin/tacto'):
            with self.subTest(previous_entry=previous_entry):
                for command in ('nlc', 'tacto'):
                    link=self.bin/command
                    if link.is_symlink():link.unlink()
                    link.symlink_to(old/previous_entry)
                installed = self.run_script()
                self.assertEqual(0, installed.returncode, installed.stdout + installed.stderr)
                for command in ('nlc', 'tacto'):
                    self.assertEqual(str(self.entry), os.readlink(self.bin/command))
                self.assertEqual(str(self.root / 'skill'), os.readlink(destination))

    def test_unknown_or_extra_arguments_and_help_do_not_install(self):
        for args in (('--typo',), ('--check', 'extra'), ('--help', 'extra')):
            with self.subTest(args=args):
                self.assertEqual(2, self.run_script(*args).returncode)
                self.assertEqual([], list(self.home.iterdir()))
        self.assertEqual(0, self.run_script('--help').returncode)
        self.assertEqual([], list(self.home.iterdir()))

    def test_stale_nlc_copy_and_local_additions_are_kept_in_private_backup(self):
        parent = self.home / '.codex/skills'
        old = parent / 'nlc'
        old.mkdir(parents=True)
        (old / 'SKILL.md').write_text('---\nname: nlc\n---\nold copy\n')
        (old / 'local-recipe.json').write_text('{"owned":"customization"}')
        installed = self.run_script()
        self.assertEqual(0, installed.returncode, installed.stdout + installed.stderr)
        backups = list(parent.parent.glob('.tacto-skill-backup.*/nlc'))
        self.assertEqual(1, len(backups))
        self.assertEqual('{"owned":"customization"}',
                         (backups[0] / 'local-recipe.json').read_text())
        self.assertEqual(0o700, backups[0].parent.stat().st_mode & 0o777)
        self.assertFalse(list(parent.glob('.nlc*')))
        self.assertFalse(old.exists())
        self.assertEqual(str(self.root / 'skill'), os.readlink(parent/'tacto'))
        self.assertEqual(0, self.run_script('--check').returncode)


if __name__ == '__main__':
    unittest.main()
