"""Safe-save read races retry observations, never the consequential input."""
import errno
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nlc.artifact_receipt import ArtifactReceiptError
from nlc.commit_evidence import CommitEvidence, make_event
from nlc.existing_file_receipt import ExistingTextFileReceipt, SAVE_PATH


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []
        self.on_sleep = lambda: None

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds
        self.on_sleep()


class ExistingFileReadPollingTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(os.path.realpath(directory.name)) / 'note.txt'
        self.path.write_bytes(b'expected')
        self.clock = Clock()
        owner = self

        class Document:
            capabilities = {'evidence.document_text', 'action.menu.focused_document'}
            text = 'expected'
            reads = 0

            def document_text(self, target, **kwargs):
                self.reads += 1
                return {'type': 'document_text', 'document_token': 'held',
                        'text_complete': True, 'text': self.text,
                        'document_path': str(owner.path)}

        self.doc = Document()
        self.receipt = ExistingTextFileReceipt(
            self.doc, document_path=str(self.path), timeout_seconds=.03,
            poll_interval=.01, clock=self.clock, sleep=self.clock.sleep)
        self.event = make_event(
            {'name': 'menu', 'path': SAVE_PATH, 'focused_document': str(self.path)},
            {'write_persistent'}, 'com.apple.TextEdit', obs_id='owned')
        self.context = self.receipt.prepare_commit(self.event)

    def after(self):
        return self.receipt.after_commit(self.event, {'type': 'acted'}, self.context)

    def replace(self, content=b'expected'):
        other = self.path.with_name('replacement.txt')
        other.write_bytes(content)
        os.replace(other, self.path)

    def eof(self, change, *, repeat=False):
        read = os.read
        changed = False

        def boundary(fd, count):
            nonlocal changed
            data = read(fd, count)
            if not data and (repeat or not changed):
                changed = True
                change()
            return data
        return patch('nlc.existing_file_receipt.os.read', side_effect=boundary)

    def test_one_safe_save_replacement_then_stable_exact_file_is_accepted(self):
        with self.eof(self.replace):
            proof = self.after()
        self.assertIs(type(proof), CommitEvidence)
        self.assertEqual([.01], self.clock.sleeps)
        self.assertEqual(3, self.doc.reads)  # prepare + two independently bound reads
        with self.assertRaisesRegex(ArtifactReceiptError, 'already_used'):
            self.after()

    def test_wrong_replacement_never_uses_old_descriptor_bytes(self):
        with self.eof(lambda: self.replace(b'wrong')):
            with self.assertRaisesRegex(ArtifactReceiptError, 'file_does_not_hold_text'):
                self.after()
        self.assertEqual(b'wrong', self.path.read_bytes())
        self.assertAlmostEqual(.03, sum(self.clock.sleeps))

    def test_repeated_safe_save_races_expire_with_last_closed_code(self):
        with self.eof(self.replace, repeat=True):
            with self.assertRaisesRegex(ArtifactReceiptError, 'file_changed_during_read'):
                self.after()
        self.assertAlmostEqual(.03, sum(self.clock.sleeps))
        with self.assertRaisesRegex(ArtifactReceiptError, 'already_used'):
            self.after()

    def test_missing_name_before_read_can_settle_after_one_wait(self):
        self.path.unlink()
        self.clock.on_sleep = lambda: self.path.write_bytes(b'expected')
        self.assertIs(type(self.after()), CommitEvidence)
        self.assertEqual([.01], self.clock.sleeps)

    def test_missing_name_after_eof_can_settle_after_one_wait(self):
        self.clock.on_sleep = lambda: self.path.write_bytes(b'expected')
        with self.eof(self.path.unlink):
            self.assertIs(type(self.after()), CommitEvidence)
        self.assertEqual([.01], self.clock.sleeps)

    def test_inode_swap_between_lstat_and_open_is_retryable(self):
        opener = os.open
        switched = False

        def opening(path, flags, *args, **kwargs):
            nonlocal switched
            if str(path) == str(self.path) and not switched:
                switched = True
                self.replace()
            return opener(path, flags, *args, **kwargs)

        with patch('nlc.existing_file_receipt.os.open', side_effect=opening):
            self.assertIs(type(self.after()), CommitEvidence)
        self.assertEqual([.01], self.clock.sleeps)

    def test_missing_name_at_open_can_settle_on_next_observation(self):
        opener = os.open
        first = True

        def opening(path, flags, *args, **kwargs):
            nonlocal first
            if first:
                first = False
                raise FileNotFoundError(errno.ENOENT, 'PRIVATE_SENTINEL')
            return opener(path, flags, *args, **kwargs)

        with patch('nlc.existing_file_receipt.os.open', side_effect=opening):
            self.assertIs(type(self.after()), CommitEvidence)
        self.assertEqual([.01], self.clock.sleeps)

    def test_permission_and_io_errors_are_immediate_and_sanitized(self):
        for error in (errno.EACCES, errno.EIO):
            with self.subTest(errno=error):
                # A fresh adapter is needed because every after call is one-shot.
                self.receipt = ExistingTextFileReceipt(
                    self.doc, document_path=str(self.path), timeout_seconds=.03,
                    poll_interval=.01, clock=self.clock, sleep=self.clock.sleep)
                self.context = self.receipt.prepare_commit(self.event)
                with patch('nlc.existing_file_receipt.os.open',
                           side_effect=OSError(error, 'PRIVATE_SENTINEL')) as opener:
                    with self.assertRaises(ArtifactReceiptError) as failure:
                        self.after()
                self.assertEqual('file_unreadable', str(failure.exception))
                self.assertEqual(1, opener.call_count)
                self.assertEqual([], self.clock.sleeps)

    def test_persistent_missing_name_expires_without_evidence(self):
        self.path.unlink()
        with self.assertRaisesRegex(ArtifactReceiptError, 'file_unreadable'):
            self.after()
        self.assertAlmostEqual(.03, sum(self.clock.sleeps))

    def test_nonregular_file_is_not_a_transient_rename_gap(self):
        self.path.unlink()
        os.mkfifo(self.path)
        with self.assertRaisesRegex(ArtifactReceiptError, 'not_a_regular_owned_file'):
            self.after()
        self.assertEqual([], self.clock.sleeps)

    def test_multiple_links_are_refused_without_polling(self):
        os.link(self.path, self.path.with_name('another-name.txt'))
        with self.assertRaisesRegex(ArtifactReceiptError, 'not_a_regular_owned_file'):
            self.after()
        self.assertEqual([], self.clock.sleeps)

    def test_public_error_text_alone_does_not_authorize_polling(self):
        error = ArtifactReceiptError('file_changed_during_read')
        error.public_code = 'file_changed_during_read'
        with patch('nlc.existing_file_receipt._file_state', side_effect=error) as reader:
            with self.assertRaises(ArtifactReceiptError):
                self.after()
        self.assertEqual(1, reader.call_count)
        self.assertEqual([], self.clock.sleeps)

    def test_document_changes_between_file_polls_stop_before_next_file_read(self):
        self.clock.on_sleep = lambda: setattr(self.doc, 'text', 'changed')
        with self.eof(self.replace):
            with self.assertRaisesRegex(ArtifactReceiptError, 'changed_during_save'):
                self.after()
        self.assertEqual([.01], self.clock.sleeps)

    def test_matching_but_late_read_cannot_issue_a_receipt(self):
        def slow_read():
            self.clock.now += .04
        with self.eof(slow_read):
            with self.assertRaisesRegex(ArtifactReceiptError, 'file_readback_deadline'):
                self.after()
        self.assertEqual([], self.clock.sleeps)

    def test_prepare_requires_initial_stable_file_without_polling(self):
        fresh = ExistingTextFileReceipt(
            self.doc, document_path=str(self.path), timeout_seconds=.03,
            clock=self.clock, sleep=self.clock.sleep)
        with self.eof(self.replace):
            with self.assertRaisesRegex(ArtifactReceiptError, 'file_changed_during_read'):
                fresh.prepare_commit(self.event)
        self.assertEqual([], self.clock.sleeps)


if __name__ == '__main__':
    unittest.main()
