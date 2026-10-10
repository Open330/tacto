"""Real-file races at readback must never issue stale save evidence."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nlc.artifact_receipt import ArtifactReceiptError
from nlc.commit_evidence import make_event
from nlc.existing_file_receipt import ExistingTextFileReceipt, SAVE_PATH, _file_state


class ReadbackStabilityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(os.path.realpath(self.directory.name)) / 'note.txt'
        self.path.write_bytes(b'expected')

    def at_eof(self, change):
        read = os.read
        changed = False

        def boundary(fd, count):
            nonlocal changed
            data = read(fd, count)
            if not data and not changed:
                changed = True
                change()
            return data
        return patch('nlc.existing_file_receipt.os.read', side_effect=boundary)

    def test_unchanged_file_has_exact_bytes_and_current_identity(self):
        result = _file_state(str(self.path))
        self.assertEqual(b'expected', result[5])
        self.assertEqual(self.path.stat().st_ino, result[1])

    def test_named_file_replaced_after_read_refuses_old_descriptor_bytes(self):
        def replace():
            other = self.path.with_name('replacement.txt')
            other.write_bytes(b'wrong')
            os.replace(other, self.path)
        with self.at_eof(replace), self.assertRaises(ArtifactReceiptError) as failure:
            _file_state(str(self.path))
        self.assertEqual('file_changed_during_read', failure.exception.public_code)

    def test_in_place_mutation_even_with_restored_mtime_refuses(self):
        original = self.path.stat()

        def overwrite():
            self.path.write_bytes(b'changed!')  # Same size; mtime restored below.
            os.utime(self.path, ns=(original.st_atime_ns, original.st_mtime_ns))
            self.assertEqual(original.st_size, self.path.stat().st_size)
            self.assertNotEqual(original.st_ctime_ns, self.path.stat().st_ctime_ns)
        with self.at_eof(overwrite), self.assertRaises(ArtifactReceiptError) as failure:
            _file_state(str(self.path))
        self.assertEqual('file_changed_during_read', failure.exception.public_code)

    def test_final_name_changed_to_symlink_refuses(self):
        def replace():
            other = self.path.with_name('other.txt')
            other.write_bytes(b'expected')
            self.path.unlink()
            self.path.symlink_to(other)
        with self.at_eof(replace), self.assertRaises(ArtifactReceiptError) as failure:
            _file_state(str(self.path))
        self.assertEqual('file_changed_during_read', failure.exception.public_code)

    def test_file_removed_after_read_returns_closed_error(self):
        with self.at_eof(self.path.unlink), self.assertRaises(ArtifactReceiptError) as failure:
            _file_state(str(self.path))
        self.assertEqual('file_unreadable', failure.exception.public_code)

    def test_parent_changed_to_symlink_to_same_file_refuses(self):
        parent = self.path.parent / 'parent'
        parent.mkdir()
        self.path = parent / 'note.txt'
        self.path.write_bytes(b'expected')

        def redirect_parent():
            moved = parent.with_name('moved')
            parent.rename(moved)
            parent.symlink_to(moved, target_is_directory=True)
        with self.at_eof(redirect_parent), self.assertRaises(ArtifactReceiptError) as failure:
            _file_state(str(self.path))
        self.assertEqual('file_changed_during_read', failure.exception.public_code)

    def test_no_receipt_after_replacement_and_no_second_attempt(self):
        path = str(self.path)

        class Document:
            capabilities = {'evidence.document_text', 'action.menu.focused_document'}

            def document_text(self, target, **kwargs):
                return {'type': 'document_text', 'document_token': 'held',
                        'text_complete': True, 'text': 'expected', 'document_path': path}

        receipt = ExistingTextFileReceipt(Document(), document_path=path)
        event = make_event({'name': 'menu', 'path': SAVE_PATH, 'focused_document': path},
                           {'write_persistent'}, 'com.apple.TextEdit', obs_id='owned')
        context = receipt.prepare_commit(event)

        def replace():
            other = self.path.with_name('replacement.txt')
            other.write_bytes(b'wrong')
            os.replace(other, self.path)

        with self.at_eof(replace), self.assertRaises(ArtifactReceiptError) as failure:
            receipt.after_commit(event, {'type': 'acted'}, context)
        self.assertEqual('file_changed_during_read', failure.exception.public_code)
        with self.assertRaises(ArtifactReceiptError) as second:
            receipt.after_commit(event, {'type': 'acted'}, context)
        self.assertEqual('already_used', second.exception.public_code)


if __name__ == '__main__':
    unittest.main()
