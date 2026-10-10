"""The save comparison must score exact bytes, without GUI or model calls."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('save_oracle_comparison', ROOT / 'examples/product_comparison.py')
comparison = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = comparison
spec.loader.exec_module(comparison)


class SaveOracleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        directory = os.path.realpath(self.temp.name)
        st = os.stat(directory)
        self.path = Path(directory) / comparison.OWNED_NAME
        self.text = 'line one  two\n한글 🙂'
        self.path.write_bytes(self.text.encode())
        comparison._OWNED.update(directory=directory, path=str(self.path), inode=(st.st_dev, st.st_ino))
        self.addCleanup(comparison._OWNED.clear)
        for name, value in (('owned_window', object()), ('_text_of', self.text)):
            mock = patch.object(comparison, name, return_value=value)
            mock.start(); self.addCleanup(mock.stop)

    def test_exact_disk_and_window_pass(self):
        self.assertTrue(comparison.owned_saved_oracle(self.text)[0])

    def test_whitespace_newline_bom_and_unicode_differences_fail(self):
        import unicodedata
        for disk in (self.text + '\n', self.text.replace('  ', ' '),
                     self.text.replace('\n', '\r\n'), '\ufeff' + self.text,
                     unicodedata.normalize('NFD', self.text)):
            with self.subTest(disk=disk):
                self.path.write_bytes(disk.encode())
                self.assertFalse(comparison.owned_saved_oracle(self.text)[0])

    def test_exact_disk_with_different_window_fails(self):
        with patch.object(comparison, '_text_of', return_value=self.text.replace('  ', ' ')):
            self.assertFalse(comparison.owned_saved_oracle(self.text)[0])

    def test_missing_owned_window_fails(self):
        with patch.object(comparison, 'owned_window', return_value=None):
            self.assertFalse(comparison.owned_saved_oracle(self.text)[0])

    def test_unreadable_body_is_not_an_empty_document(self):
        self.path.write_bytes(b'')
        with patch.object(comparison, '_text_of', return_value=None):
            self.assertFalse(comparison.owned_saved_oracle('')[0])

    def test_symlink_and_fifo_are_rejected_before_open(self):
        self.path.unlink()
        for make in (lambda: self.path.symlink_to('/nonexistent-save-target'),
                     lambda: os.mkfifo(self.path)):
            make()
            with patch('os.open', side_effect=AssertionError('unsafe open')):
                self.assertFalse(comparison.owned_saved_oracle(self.text)[0])
            self.path.unlink()

    def test_path_replacement_during_read_cannot_pass(self):
        real_read = os.read
        changed = False

        def read(fd, count):
            nonlocal changed
            data = real_read(fd, count)
            if not data and not changed:
                changed = True
                other = self.path.with_name('replace.txt')
                other.write_bytes(b'wrong')
                os.replace(other, self.path)
            return data

        with patch('os.read', side_effect=read):
            self.assertFalse(comparison.owned_saved_oracle(self.text)[0])

    def test_oversized_file_is_not_read(self):
        with self.path.open('wb') as stream:
            stream.truncate(4 * 1024 * 1024 + 1)
        with patch('os.read', side_effect=AssertionError('over-budget read')):
            self.assertFalse(comparison.owned_saved_oracle(self.text)[0])

    def test_save_task_declares_new_oracle_contract(self):
        task = next(t for t in comparison.TASKS if t.name == 'textedit_save_owned')
        self.assertEqual('signed-owned-document-exact-utf8-saved-state-v3', task.oracle_contract)


if __name__ == '__main__':
    unittest.main()
