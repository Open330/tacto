from pathlib import Path
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from tacto.document_packages import _snapshot, capture_rtfd_package, PackageEvidenceError


class PackageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.parent = Path(temp.name).resolve()
        self.package = self.parent / 'owned.rtfd'
        self.package.mkdir(mode=0o700)
        self.file = self.package / 'TXT.rtf'
        self.file.write_bytes(b'owned')
        self.file.chmod(0o600)

    def snapshot(self, **kwargs):
        return _snapshot(self.package, time.monotonic() + 3,
                         kwargs.get('entries', 20), kwargs.get('depth', 4), kwargs.get('payload', 100))

    def test_exact_bytes_and_empty_directory_identity_are_captured(self):
        empty = self.package / 'empty'
        empty.mkdir(mode=0o700)
        first, count, size = self.snapshot()
        self.assertEqual((2, 5), (count, size))
        self.assertEqual(first, self.snapshot()[0])
        empty.rmdir()
        empty.mkdir(mode=0o700)
        self.assertNotEqual(first, self.snapshot()[0])

    def test_links_hardlinks_and_nonprivate_entries_refuse(self):
        self.file.chmod(0o644)
        with self.assertRaises(PackageEvidenceError): self.snapshot()
        self.file.chmod(0o600)
        os.link(self.file, self.parent / 'other')
        with self.assertRaises(PackageEvidenceError): self.snapshot()
        (self.parent / 'other').unlink()
        self.file.unlink()
        self.file.symlink_to(self.parent / 'missing')
        with self.assertRaises(PackageEvidenceError): self.snapshot()

    def test_aggregate_limit_refuses_before_reading_payload(self):
        with patch('tacto.document_packages.os.read') as read:
            with self.assertRaisesRegex(PackageEvidenceError, 'payload_bound'):
                self.snapshot(payload=4)
            read.assert_not_called()

    def test_directory_topology_change_during_read_refuses(self):
        original = os.read
        def changed(fd, count):
            raw = original(fd, count)
            if not (self.package / 'new').exists():
                (self.package / 'new').mkdir(mode=0o700)
            return raw
        with patch('tacto.document_packages.os.read', side_effect=changed):
            with self.assertRaises(PackageEvidenceError): self.snapshot()

    def test_file_replacement_during_read_refuses(self):
        original = os.read
        changed = [False]
        def replaced(fd, count):
            raw = original(fd, count)
            if not changed[0]:
                changed[0] = True
                self.file.unlink()
                self.file.write_bytes(b'owned')
                self.file.chmod(0o600)
            return raw
        with patch('tacto.document_packages.os.read', side_effect=replaced):
            with self.assertRaisesRegex(PackageEvidenceError, 'identity_changed'):
                self.snapshot()

    def test_depth_entries_and_deadline_refuse(self):
        (self.package / 'deep').mkdir(mode=0o700)
        with self.assertRaisesRegex(PackageEvidenceError, 'depth_bound'): self.snapshot(depth=0)
        with self.assertRaisesRegex(PackageEvidenceError, 'entry_bound'): self.snapshot(entries=1)
        with self.assertRaisesRegex(PackageEvidenceError, 'deadline'):
            _snapshot(self.package, time.monotonic()-1, 20, 4, 100)

    def test_host_path_is_not_normalized_and_invalid_bounds_read_nothing(self):
        for path in [str(self.package)+'/', str(self.package)+'/../owned.rtfd', 'owned.rtfd']:
            with self.assertRaises(PackageEvidenceError): capture_rtfd_package(path)
        with patch('tacto.document_packages._snapshot') as read:
            for kwargs in [{'max_entries':True}, {'max_depth':17}, {'timeout_seconds':float('nan')}, {'max_utf16_units':0}]:
                with self.assertRaises(ValueError): capture_rtfd_package(self.package, **kwargs)
            read.assert_not_called()

    def test_second_snapshot_difference_prevents_native_decode(self):
        with patch('tacto.document_packages._snapshot', side_effect=[(('a',), 1, 5), (('b',), 1, 5)]):
            with self.assertRaisesRegex(PackageEvidenceError, 'snapshot_changed'):
                capture_rtfd_package(self.package)

    @unittest.skipUnless(sys.platform == 'darwin', 'AppKit required')
    def test_actual_owned_rtfd_decodes_all_payloads_without_authority(self):
        from AppKit import NSDocumentTypeDocumentAttribute, NSRTFDTextDocumentType
        from examples.probes.attachment_payloads import owned_body
        from examples.probes.signed_document_attachments import write_owned_package
        from tacto.document_attachments import compare_attachment_payloads
        self.file.unlink()
        self.package.rmdir()
        body = owned_body()
        wrapper, error = body.fileWrapperFromRange_documentAttributes_error_(
            (0, body.length()), {NSDocumentTypeDocumentAttribute:NSRTFDTextDocumentType}, None)
        self.assertIsNone(error)
        write_owned_package(self.package, wrapper)
        captured = capture_rtfd_package(self.package)
        self.assertTrue(compare_attachment_payloads(body, captured.body)['captured_attachment_payloads_equal'])
        self.assertEqual(5, captured.entry_count)
        self.assertEqual(300, captured.attachment_inventory['payload_bytes'])
        self.assertFalse(captured.authorizes_save)
        self.assertFalse(captured.attachment_completeness_verified)
        self.assertFalse(captured.producer_attribution_verified)

    @unittest.skipUnless(sys.platform == 'darwin', 'AppKit required')
    def test_post_decode_package_change_refuses_and_preserves_no_authority(self):
        from AppKit import NSDocumentTypeDocumentAttribute, NSRTFDTextDocumentType
        from examples.probes.attachment_payloads import owned_body
        from examples.probes.signed_document_attachments import write_owned_package
        self.file.unlink()
        self.package.rmdir()
        body = owned_body()
        wrapper, _ = body.fileWrapperFromRange_documentAttributes_error_(
            (0, body.length()), {NSDocumentTypeDocumentAttribute:NSRTFDTextDocumentType}, None)
        write_owned_package(self.package, wrapper)
        def change(*args, **kwargs):
            self.file.write_bytes(b'changed')
            return {}
        with patch('tacto.document_packages.attachment_inventory', side_effect=change):
            with self.assertRaisesRegex(PackageEvidenceError, 'snapshot_changed'):
                capture_rtfd_package(self.package)

    def test_probe_refuses_existing_report_before_capture(self):
        from examples.probes.rtfd_package_payloads import main
        output = self.parent/'report.json'
        output.write_text('KEEP')
        with patch('examples.probes.rtfd_package_payloads.capture_rtfd_package') as capture:
            with self.assertRaises(FileExistsError): main(self.package, output)
            capture.assert_not_called()
        self.assertEqual('KEEP', output.read_text())

    def test_probe_initial_hash_failure_still_records_report(self):
        from examples.probes.rtfd_package_payloads import main
        output = self.parent/'report.json'
        with patch('examples.probes.rtfd_package_payloads.Path.read_bytes', side_effect=PermissionError), \
             patch('examples.probes.rtfd_package_payloads.capture_rtfd_package') as capture:
            result = main(self.package, output)
            capture.assert_not_called()
        import json
        self.assertEqual(result, json.loads(output.read_text()))
        self.assertFalse(result['passed'])
        self.assertFalse(result['source_stable'])
        self.assertEqual('PermissionError', result['error_class'])

    @unittest.skipUnless(sys.platform == 'darwin', 'AppKit required')
    def test_late_result_construction_refuses(self):
        from AppKit import NSDocumentTypeDocumentAttribute, NSRTFDTextDocumentType
        from examples.probes.attachment_payloads import owned_body
        from examples.probes.signed_document_attachments import write_owned_package
        from tacto.document_packages import CapturedRTFDPackage
        self.file.unlink()
        self.package.rmdir()
        body = owned_body()
        wrapper, _ = body.fileWrapperFromRange_documentAttributes_error_(
            (0, body.length()), {NSDocumentTypeDocumentAttribute:NSRTFDTextDocumentType}, None)
        write_owned_package(self.package, wrapper)
        clock = [100.0]
        def late(*args):
            result = CapturedRTFDPackage(*args)
            clock[0] = 104.0
            return result
        with patch('tacto.document_packages.time.monotonic', side_effect=lambda:clock[0]), \
             patch('tacto.document_packages.CapturedRTFDPackage', side_effect=late):
            with self.assertRaisesRegex(PackageEvidenceError, 'deadline'):
                capture_rtfd_package(self.package)
