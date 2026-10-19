"""Owned actual-app probe must reserve private artifacts before touching UI."""
import os
from pathlib import Path
import runpy
import stat
import tempfile
import unittest
from unittest.mock import Mock
ROOT=Path(__file__).resolve().parents[1]
PROBE=runpy.run_path(str(ROOT/'examples/probes/signed-document-rtf.py'))

class SignedRTFArtifactTests(unittest.TestCase):
    def test_all_three_are_private_exclusive_and_partial_reservations_roll_back(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder).resolve()/'result.json'
            paths,files=PROBE['reserve_artifacts'](output)
            for key,path in paths.items():
                self.assertEqual(0o600,stat.S_IMODE(path.stat().st_mode))
                files[key].write(key.encode());files[key].close()
            for key,path in paths.items():
                self.assertEqual(key.encode(),path.read_bytes())
            with self.assertRaises(FileExistsError):PROBE['reserve_artifacts'](output)
            self.assertEqual(b'report',output.read_bytes())
        for suffix in ('.owned.rtf','.range.rtf'):
            with self.subTest(suffix=suffix),tempfile.TemporaryDirectory() as folder:
                output=Path(folder).resolve()/'result.json';prior=output.with_suffix(suffix);prior.write_bytes(b'prior')
                driver=Mock()
                with self.assertRaises(FileExistsError):PROBE['main'](driver,output)
                driver.list_targets.assert_not_called()
                self.assertEqual(b'prior',prior.read_bytes())
                self.assertFalse(output.exists())
                other=output.with_suffix('.range.rtf' if suffix=='.owned.rtf' else '.owned.rtf')
                self.assertFalse(other.exists())

    def test_nonprivate_or_symlink_parent_refuses_before_driver(self):
        with tempfile.TemporaryDirectory() as folder:
            parent=Path(folder).resolve()/'public';parent.mkdir(mode=0o755);driver=Mock()
            with self.assertRaises(ValueError):PROBE['main'](driver,parent/'result.json')
            driver.list_targets.assert_not_called()
            parent.chmod(0o700);alias=Path(folder).resolve()/'alias';alias.symlink_to(parent,target_is_directory=True)
            with self.assertRaises(ValueError):PROBE['main'](driver,alias/'result.json')
            self.assertEqual([],list(parent.iterdir()))
