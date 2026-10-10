"""Saved-state evidence cannot turn a non-dispatched Save into completion."""
from pathlib import Path
import unittest
import tempfile

from nlc.artifact_receipt import ArtifactReceiptError
from nlc.commit_evidence import CommitEvidence
from tests.test_existing_file_receipt import Doc, save_event
from nlc.existing_file_receipt import ExistingTextFileReceipt


class ExistingDispatchTests(unittest.TestCase):
    def setUp(self):
        work=tempfile.TemporaryDirectory();self.addCleanup(work.cleanup)
        self.path=str(Path(work.name).resolve()/'note.txt')
        Path(self.path).write_text('new text',encoding='utf-8')
        self.doc=Doc(self.path,'new text')

    def receipt(self):
        return ExistingTextFileReceipt(self.doc,document_path=self.path,timeout_seconds=.2)

    def test_non_dispatch_refused_even_when_autosave_bytes_match(self):
        responses=[None,{}, {'type':'observation'},
                   {'type':'error','effect':'not_started'},
                   {'type':'error','error':'stale_observation','effect':'not_started'},
                   {'type':'acted','effect':'not_started'}]
        for response in responses:
            with self.subTest(response=response):
                Path(self.path).write_text(self.doc.text,encoding='utf-8')
                receipt=self.receipt();event=save_event(self.path)
                context=receipt.prepare_commit(event)
                with self.assertRaisesRegex(ArtifactReceiptError,'driver_dispatch_unverified'):
                    receipt.after_commit(event,response,context)
                with self.assertRaisesRegex(ArtifactReceiptError,'already_used'):
                    receipt.after_commit(event,{'type':'acted'},context)

    def test_uncertain_dispatched_error_can_only_certify_saved_state(self):
        Path(self.path).write_text(self.doc.text,encoding='utf-8')
        receipt=self.receipt();event=save_event(self.path)
        context=receipt.prepare_commit(event)
        proof=receipt.after_commit(event,{'type':'error','effect':'unknown'},context)
        self.assertIs(type(proof),CommitEvidence)
