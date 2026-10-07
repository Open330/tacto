"""Capture transport budgets and real artifact files, without GUI input."""
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from nlc.helper import InheritedHelperDriver


class HelperCaptureArtifacts(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.work = Path(work.name)
        self.source = self.work / 'helper.png'
        self.source.write_bytes(b'owned screenshot artifact')
        self.calls = []
        self.answer = {'type': 'capture', 'path': str(self.source),
                       'sha256': hashlib.sha256(self.source.read_bytes()).hexdigest()}
        def send(message):
            self.calls.append(message)
            return self.answer
        self.driver = SimpleNamespace(_send=send)

    def capture(self, **kwargs):
        return InheritedHelperDriver.capture(self.driver, 'fixture', obs_id='o1', **kwargs)

    def test_private_new_artifact_and_budget_reach_transport(self):
        destination = self.work / 'copy.png'
        result = self.capture(path=destination, timeout_seconds=.25, window_index=2)
        self.assertEqual(result['type'], 'capture')
        self.assertEqual(destination.read_bytes(), self.source.read_bytes())
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.calls[0]['timeout_seconds'], .25)
        self.assertEqual(self.calls[0]['window_index'], 2)
        self.assertEqual(result['helper_path'], str(self.source))

    def test_existing_file_and_symlink_never_overwritten(self):
        destination = self.work / 'existing'
        destination.write_bytes(b'user content')
        link = self.work / 'symlink'
        link.symlink_to(destination)
        for path in (destination, link):
            self.assertEqual(self.capture(path=path)['type'], 'error')
            self.assertEqual(destination.read_bytes(), b'user content')
        self.assertTrue(link.is_symlink())

    def test_tampered_helper_artifact_rejected_and_partial_removed(self):
        destination = self.work / 'copy.png'
        self.source.write_bytes(b'different bytes after helper observation')
        result = self.capture(path=destination)
        self.assertEqual(result['error'], 'visual_read_failed')
        self.assertFalse(destination.exists())
        self.assertTrue(self.source.exists())

    def test_invalid_budget_and_window_do_not_send(self):
        for budget in (True, False, None, '5', 0, -1, float('nan'), float('inf'), 11):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                self.capture(timeout_seconds=budget)
        for index in (True, -1, 64, 1.5, '1'):
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.capture(window_index=index)
        self.assertEqual(self.calls, [])
