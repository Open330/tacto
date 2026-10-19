"""Host readers must not rebind the event while receipt preparation is pending."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from tacto.artifact_receipt import ArtifactReceiptError, FreshFileReceipt
from tacto.commit_evidence import action_digest, make_event
from tacto.existing_file_receipt import ExistingTextFileReceipt
from tacto.textedit_receipt import make_textedit_receipt
from tests.test_existing_file_receipt import Doc, save_event
from tests.test_textedit_receipt import SnapshotDriver
from tests.test_transcript_receipt import Chat, receipt


def change_binding(event, kind):
    if kind == 'id':
        event['event_id'] = 'different-host-request'
    elif kind == 'observation':
        event['obs_id'] = 'different-observation'
        event['action_digest'] = action_digest(event['action'], event['effects'],
                                               event['target'], obs_id=event['obs_id'])
    elif kind == 'action':
        if 'element_index' in event['action']:
            event['action']['element_index'] += 1
        else:
            event['action']['path'][0] = 'Format'
    else:
        event['effects'] = None


class ReceiptEventBindingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.directory = self.root / 'Downloads'
        self.directory.mkdir(mode=0o700)

    def test_existing_host_document_read_cannot_rebind_request(self):
        path = self.directory / 'existing.txt'; path.write_text('old')
        for kind in ('id', 'observation', 'action', 'malformed'):
            with self.subTest(kind=kind):
                doc = Doc(str(path), 'expected')
                adapter = ExistingTextFileReceipt(doc, document_path=str(path))
                event = save_event(str(path)); read = doc.document_text
                def reader(*args, **kwargs):
                    change_binding(event, kind)
                    return read(*args, **kwargs)
                with patch.object(doc, 'document_text', side_effect=reader):
                    with self.assertRaises(ArtifactReceiptError): adapter.prepare_commit(event)
                self.assertIsNone(adapter._prepared)
                self.assertEqual('old', path.read_text())

    def test_transcript_host_read_cannot_rebind_request(self):
        for kind in ('id', 'observation', 'action', 'malformed'):
            with self.subTest(kind=kind):
                chat = Chat(); adapter = receipt(chat)
                event = make_event({'name':'press_key', 'key':'Return', 'element_index':4},
                                   {'transmit'}, 'chat', obs_id='o')
                read = adapter._read_tree
                def reader(*args, **kwargs):
                    change_binding(event, kind)
                    return read(*args, **kwargs)
                with patch.object(adapter, '_read_tree', side_effect=reader):
                    with self.assertRaises(ArtifactReceiptError): adapter.prepare_commit(event)
                self.assertIsNone(adapter._prepared)
                self.assertEqual([], chat.calls)

    def test_fresh_directory_revalidation_cannot_publish_changed_binding(self):
        for kind in ('id', 'observation', 'action', 'malformed'):
            with self.subTest(kind=kind):
                adapter = FreshFileReceipt(self.directory, 'fresh.txt', 'expected')
                event = make_event({'name':'click', 'element_index':0}, {'write_persistent'},
                                   'com.apple.TextEdit', obs_id='o')
                read = adapter._check_directory
                def reader(*args, **kwargs):
                    result = read(*args, **kwargs)
                    change_binding(event, kind)
                    return result
                with patch.object(adapter, '_check_directory', side_effect=reader):
                    with self.assertRaises(ArtifactReceiptError): adapter.prepare_commit(event)
                self.assertIsNone(adapter._prepared)
                self.assertFalse((self.directory / 'fresh.txt').exists())

    def test_textedit_snapshot_cannot_rebind_request(self):
        for kind in ('id', 'observation', 'action', 'malformed'):
            with self.subTest(kind=kind):
                path = self.directory / 'owned.rtf'; driver = SnapshotDriver(path)
                with patch('tacto.textedit_receipt.Path.home', return_value=self.root):
                    adapter = make_textedit_receipt(driver, target='com.apple.TextEdit',
                        directory=str(self.directory), filename=path.name)
                event = make_event({'name':'click', 'element_index':0}, {'write_persistent'},
                                   'com.apple.TextEdit', obs_id='o')
                read = driver.document_snapshot
                def reader(*args, **kwargs):
                    change_binding(event, kind)
                    return read(*args, **kwargs)
                with patch.object(driver, 'document_snapshot', side_effect=reader):
                    with self.assertRaises(ValueError): adapter.prepare_commit(event)
                self.assertEqual(0, driver.dispatches)
                self.assertFalse(path.exists())
