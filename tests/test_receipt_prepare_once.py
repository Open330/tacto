"""Failed receipt preparation consumes that adapter, not the external resource."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from tacto.artifact_receipt import ArtifactReceiptError,FreshFileReceipt
from tacto.commit_evidence import make_event
from tacto.existing_file_receipt import ExistingTextFileReceipt
from tacto.textedit_receipt import make_textedit_receipt
from tests.test_existing_file_receipt import Doc,save_event
from tests.test_textedit_receipt import SnapshotDriver
from tests.test_transcript_receipt import Chat,receipt


class ReceiptPrepareOnceTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve();self.directory=self.root/'Downloads'
        self.directory.mkdir(mode=0o700)
        self.event=make_event({'name':'click','element_index':0},{'write_persistent'},'com.apple.TextEdit',obs_id='o')

    def test_fresh_existing_destination_failure_cannot_be_retried_after_removal(self):
        path=self.directory/'fresh.txt';path.write_bytes(b'preexisting')
        adapter=FreshFileReceipt(self.directory,path.name,'expected')
        with self.assertRaises(ArtifactReceiptError):adapter.prepare_commit(self.event)
        path.unlink()
        with self.assertRaisesRegex(ArtifactReceiptError,'already prepared'):
            adapter.prepare_commit(self.event)
        self.assertIsNone(adapter._prepared)
        # A new, explicit host boundary may start a new task; no global ban.
        self.assertIsNotNone(FreshFileReceipt(self.directory,path.name,'expected').prepare_commit(self.event))

    def test_fresh_invalid_event_attempt_is_also_consumed(self):
        adapter=FreshFileReceipt(self.directory,'fresh.txt','expected')
        with self.assertRaises(ArtifactReceiptError):adapter.prepare_commit({})
        with self.assertRaisesRegex(ArtifactReceiptError,'already prepared'):
            adapter.prepare_commit(self.event)
        self.assertIsNone(adapter._prepared)

    def test_existing_document_reader_failure_cannot_be_rebound_after_repair(self):
        path=self.directory/'existing.txt';path.write_bytes(b'old')
        doc=Doc(str(path),'expected')
        adapter=ExistingTextFileReceipt(doc,document_path=str(path))
        read=doc.document_text
        with patch.object(doc,'document_text',return_value={'type':'error'}) as failed:
            with self.assertRaises(ArtifactReceiptError):adapter.prepare_commit(save_event(str(path)))
            failed.assert_called_once()
        with patch.object(doc,'document_text',side_effect=read) as repaired:
            with self.assertRaisesRegex(ArtifactReceiptError,'already_prepared'):
                adapter.prepare_commit(save_event(str(path)))
            repaired.assert_not_called()
        self.assertIsNone(adapter._prepared)

    def test_existing_prepare_reservation_precedes_blocked_host_reader(self):
        import threading
        path=self.directory/'existing.txt';path.write_bytes(b'old')
        doc=Doc(str(path),'expected');adapter=ExistingTextFileReceipt(doc,document_path=str(path))
        entered=threading.Event();release=threading.Event();results=[];errors=[]
        read=doc.document_text
        def slow(*args,**kwargs):
            entered.set()
            if not release.wait(2):raise RuntimeError('test reader was not released')
            return read(*args,**kwargs)
        def prepare():
            try:results.append(adapter.prepare_commit(save_event(str(path))))
            except Exception as error:errors.append(type(error).__name__)
        with patch.object(doc,'document_text',side_effect=slow) as reader:
            thread=threading.Thread(target=prepare);thread.start()
            try:
                self.assertTrue(entered.wait(1))
                with self.assertRaisesRegex(ArtifactReceiptError,'already_prepared'):
                    adapter.prepare_commit(save_event(str(path)))
            finally:release.set();thread.join(timeout=2)
            self.assertFalse(thread.is_alive());reader.assert_called_once()
        self.assertEqual([],errors);self.assertEqual(1,len(results))

    def test_textedit_wrong_panel_attempt_cannot_be_repaired_on_same_adapter(self):
        path=self.directory/'owned.rtf';driver=SnapshotDriver(path)
        with patch('tacto.textedit_receipt.Path.home',return_value=self.root):
            adapter=make_textedit_receipt(driver,target='com.apple.TextEdit',directory=str(self.directory),filename=path.name)
        driver.panel['filename']='other.rtf'
        with self.assertRaises(ArtifactReceiptError):adapter.prepare_commit(self.event)
        driver.panel['filename']=path.name;before=len(driver.calls)
        with self.assertRaisesRegex(ArtifactReceiptError,'already prepared'):
            adapter.prepare_commit(self.event)
        self.assertEqual(before,len(driver.calls));self.assertEqual(0,driver.dispatches)
        self.assertFalse(path.exists())

    def test_transcript_recipient_failure_cannot_reuse_adapter_after_switching_room(self):
        chat=Chat(title='Room B');adapter=receipt(chat)
        event=make_event({'name':'press_key','key':'Return','element_index':4},{'transmit'},'chat',obs_id='o')
        with self.assertRaisesRegex(ArtifactReceiptError,'recipient_mismatch'):
            adapter.prepare_commit(event)
        chat.title='Room A'
        with self.assertRaisesRegex(ArtifactReceiptError,'already_prepared'):
            adapter.prepare_commit(event)
        self.assertIsNone(adapter._prepared);self.assertEqual([],chat.calls)
