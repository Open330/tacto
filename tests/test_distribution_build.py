"""A distribution must name a version and one unmodified committed source."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'setup'))
from build_distribution import release_version,committed_sources

class DistributionBuildTests(unittest.TestCase):
    def test_release_tags_are_closed(self):
        self.assertEqual(('0.1.0','15'),release_version('v0.1.0-preview.15'))
        self.assertEqual(('1.0.0',None),release_version('v1.0.0'))
        for tag in ('main','../v0.1.0','v0.1.0-preview.0','v0.1.0-preview.01','v0.1.0; echo bad'):
            with self.subTest(tag=tag),self.assertRaises(ValueError):release_version(tag)
    def test_dirty_or_untracked_sources_cannot_claim_a_commit(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            def git(*args):return subprocess.run(['git','-C',folder,*args],check=True,capture_output=True)
            git('init');git('config','user.name','Test');git('config','user.email','test@example.invalid')
            (root/'tacto').mkdir();source=root/'tacto/first_run.py';source.write_text('version = 1\n')
            git('add','.');git('commit','-m','fixture')
            before=committed_sources(root)
            self.assertIn('tacto/first_run.py',before['files'])
            source.write_text('version = 2\n')
            with self.assertRaises(RuntimeError):committed_sources(root)
            git('checkout','--','.');(root/'untracked').write_text('new')
            with self.assertRaises(RuntimeError):committed_sources(root)
            (root/'untracked').unlink()
            self.assertEqual(before,committed_sources(root))
