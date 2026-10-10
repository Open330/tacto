"""Signed comparison owns one exact directory; disk and document must agree."""
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from tests.test_product_save_oracle import comparison


class SignedSaveOracleTests(unittest.TestCase):
    def setUp(self):
        work=tempfile.TemporaryDirectory();self.addCleanup(work.cleanup)
        directory=Path(work.name).resolve();directory.chmod(0o700)
        path=directory/comparison.OWNED_NAME
        self.text='line one  two\n한글 🙂';path.write_bytes(self.text.encode())
        st=directory.lstat()
        self.fixture=SimpleNamespace(directory=directory,path=path,name=path.name,
            directory_identity=(st.st_dev,st.st_ino),read=Mock(return_value=self.text))
        self.patch=patch.dict(comparison._SIGNED_OWNED,{'fixture':self.fixture},clear=True)
        self.patch.start();self.addCleanup(self.patch.stop)

    def test_exact_owned_disk_and_signed_body_pass(self):
        passed,detail=comparison.signed_owned_saved_oracle(self.text)
        self.assertTrue(passed);self.assertIn('saved_state_only',detail)

    def test_wrong_disk_or_body_cannot_pass(self):
        self.fixture.path.write_bytes((self.text+'\n').encode())
        self.assertFalse(comparison.signed_owned_saved_oracle(self.text)[0])
        self.fixture.path.write_bytes(self.text.encode());self.fixture.read.return_value='wrong'
        self.assertFalse(comparison.signed_owned_saved_oracle(self.text)[0])

    def test_wrong_directory_identity_and_extra_file_refused(self):
        original=self.fixture.directory_identity;self.fixture.directory_identity=(0,0)
        self.assertFalse(comparison.signed_owned_saved_oracle(self.text)[0])
        self.fixture.directory_identity=original
        (self.fixture.directory/'unexpected').write_bytes(b'')
        self.assertFalse(comparison.signed_owned_saved_oracle(self.text)[0])

    def test_symlink_and_hardlink_refused(self):
        original=self.fixture.path.read_bytes();self.fixture.path.unlink()
        self.fixture.path.symlink_to('/nonexistent')
        self.assertFalse(comparison.signed_owned_saved_oracle(self.text)[0])
        self.fixture.path.unlink();self.fixture.path.write_bytes(original)
        link=self.fixture.directory.parent/('hardlink-'+self.fixture.directory.name)
        os.link(self.fixture.path,link);self.addCleanup(link.unlink)
        self.assertFalse(comparison.signed_owned_saved_oracle(self.text)[0])

    def test_save_and_chain_use_owned_path_and_signed_cleanup(self):
        for name in ('textedit_save_owned','calc_to_textedit'):
            task=next(task for task in comparison.TASKS if task.name==name)
            self.assertIs(task.cleanup,comparison.signed_owned_cleanup)
            self.assertIn(str(self.fixture.path),task.ask())
            self.assertIsNotNone(task.preview)

    def test_failed_calculator_setup_does_not_create_document(self):
        with patch.object(comparison,'calc_reset',side_effect=RuntimeError('setup_failed')),\
             patch.object(comparison,'signed_owned_setup') as setup:
            with self.assertRaises(RuntimeError):comparison.signed_calc_then_owned_setup()
            setup.assert_not_called()

    def test_chain_requires_both_calculator_and_document_result(self):
        for display,body,expected in [('437','437',True),('0','437',False),('437','wrong',False)]:
            with self.subTest(display=display,body=body),patch.object(comparison,'_calc_display',return_value=display):
                self.fixture.read.return_value=body
                passed,detail=comparison.signed_chain_oracle('437')
                self.assertEqual(expected,passed)
                self.assertIn('no_explicit_save_required',detail)
