"""Witnessing File > Save of an existing .txt document (no GUI, temp files)."""
import os
import tempfile
import unittest
from pathlib import Path

from nlc.artifact_receipt import ArtifactReceiptError
from nlc.commit_evidence import CommitEvidence, evidence_error, make_event
from nlc.existing_file_receipt import (SAVE_PATH, ExistingFileReceiptUnavailable,
                                       ExistingTextFileReceipt)


class Doc:
    capabilities = {'evidence.document_text'}

    def __init__(self, path, text):
        self.path, self.text, self.token = path, text, 'tok'

    def document_text(self, target, document_token=None, document_path=None, **k):
        self.selected = document_path
        return {'type': 'document_text', 'document_token': self.token, 'text': self.text,
                'text_complete': True, 'document_path': self.path}


def save_event(obs='o'):
    return make_event({'name': 'menu', 'path': SAVE_PATH}, {'write_persistent'},
                      'com.apple.TextEdit', obs_id=obs)


class ExistingFileReceiptTests(unittest.TestCase):
    def setUp(self):
        self.dir = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__('shutil').rmtree(self.dir, True))
        self.path = os.path.join(self.dir, 'note.txt')
        Path(self.path).write_text('old', encoding='utf-8')
        self.doc = Doc(self.path, 'new text 4417')

    def receipt(self, **k):
        return ExistingTextFileReceipt(self.doc, document_path=k.pop('document_path', self.path),
                                       timeout_seconds=0.2, poll_interval=0.01, **k)

    def run_save(self, receipt, write=None):
        event = save_event()
        context = receipt.prepare_commit(event)
        if write is not None:
            write()
        return event, receipt.after_commit(event, {'type': 'acted'}, context)

    def test_a_save_that_writes_the_text_gets_a_receipt(self):
        r = self.receipt()
        event, proof = self.run_save(r, lambda: Path(self.path).write_text('new text 4417', encoding='utf-8'))
        self.assertIs(type(proof), CommitEvidence)
        self.assertIsNone(evidence_error({**event, 'commit_evidence': proof}, {}))
        self.assertEqual(self.path, proof.evidence.path)

    def test_a_file_already_holding_the_text_is_the_saved_state(self):
        # macOS autosave writes documents that have a file; the receipt
        # certifies the state after the Save, not which writer made it.
        Path(self.path).write_text('new text 4417', encoding='utf-8')
        event, proof = self.run_save(self.receipt())
        self.assertIs(type(proof), CommitEvidence)

    def test_no_write_no_receipt(self):
        with self.assertRaisesRegex(ArtifactReceiptError, 'does not hold'):
            self.run_save(self.receipt())

    def test_different_bytes_no_receipt(self):
        with self.assertRaisesRegex(ArtifactReceiptError, 'does not hold'):
            self.run_save(self.receipt(), lambda: Path(self.path).write_text('new text 4417\n', encoding='utf-8'))

    def test_text_or_document_changing_during_the_save_refuses(self):
        r = self.receipt()
        def edit():
            self.doc.text = 'something else'
        with self.assertRaisesRegex(ArtifactReceiptError, 'changed'):
            self.run_save(r, edit)
        r2 = ExistingTextFileReceipt(Doc(self.path, 'x'), document_path=self.path,
                                     timeout_seconds=0.2, poll_interval=0.01)
        event = save_event(); ctx = r2.prepare_commit(event)
        r2._driver.token = 'other'
        with self.assertRaisesRegex(ArtifactReceiptError, 'identity changed'):
            r2.after_commit(event, {}, ctx)

    def test_only_a_canonical_txt_path_and_only_that_document(self):
        rtf = os.path.join(self.dir, 'doc.rtf'); Path(rtf).write_text('x')
        for path in (rtf, None, 'relative.txt'):
            with self.subTest(path=path), self.assertRaises(ExistingFileReceiptUnavailable):
                self.receipt(document_path=path)
        r = self.receipt()
        r.prepare_commit(save_event())
        self.assertEqual(self.path, self.doc.selected)      # selected by path
        other = os.path.join(self.dir, 'other.txt'); Path(other).write_text('o')
        self.doc.path = other
        with self.assertRaisesRegex(ArtifactReceiptError, 'different document'):
            self.receipt().prepare_commit(save_event())

    def test_a_symlinked_file_is_refused(self):
        real = os.path.join(self.dir, 'real.txt'); Path(real).write_text('old')
        os.unlink(self.path); os.symlink(real, self.path)
        # realpath(path) != path → refused before anything is read
        with self.assertRaises(ExistingFileReceiptUnavailable):
            self.receipt()

    def test_only_file_save_menu_and_once(self):
        r = self.receipt()
        for action in ({'name': 'menu', 'path': ['File', 'Save As…']},
                       {'name': 'click', 'element_index': 3}):
            event = make_event(action, {'write_persistent'}, 'com.apple.TextEdit', obs_id='o')
            with self.subTest(action=action), self.assertRaises(ArtifactReceiptError):
                r.prepare_commit(event)
        event = save_event(); ctx = r.prepare_commit(event)
        with self.assertRaises(ArtifactReceiptError):
            r.prepare_commit(save_event())
        Path(self.path).write_text('new text 4417', encoding='utf-8')
        r.after_commit(event, {}, ctx)
        with self.assertRaises(ArtifactReceiptError):
            r.after_commit(event, {}, ctx)

    def test_driver_without_text_witness_is_unavailable(self):
        class Bare: capabilities = set()
        with self.assertRaises(ExistingFileReceiptUnavailable):
            ExistingTextFileReceipt(Bare(), document_path=self.path)


class RecipeWiringTests(unittest.TestCase):
    def setUp(self):
        from nlc.agent import Agent
        self.dir = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__('shutil').rmtree(self.dir, True))
        self.doc = os.path.join(self.dir, 'note.txt'); Path(self.doc).write_text('')
        agent = Agent.__new__(Agent)
        agent.commit_adapter_factory = None
        agent.driver = Doc(self.doc, 'x')
        agent.narrate = lambda *a: None
        self.agent = agent

    def bound(self, document, name):
        from nlc.library import textedit_save
        return textedit_save().bind(document=document, name=name)

    def test_the_exact_bound_recipe_gets_a_receipt_for_that_path(self):
        receipt = self.agent.commit_adapter_for(self.bound(self.doc, 'note.txt'))
        self.assertIsInstance(receipt, ExistingTextFileReceipt)
        self.assertEqual(self.doc, receipt._path)

    def test_anything_else_gets_none(self):
        from dataclasses import replace
        self.assertIsNone(self.agent.commit_adapter_for(self.bound(self.doc, 'other.txt')))
        flow = self.bound(self.doc, 'note.txt')
        changed = replace(flow, steps=[replace(flow.steps[0], unverified_ok=False)])
        self.assertIsNone(self.agent.commit_adapter_for(changed))
        reworded = replace(flow, steps=[replace(flow.steps[0], intent_text='다른 설명')])
        self.assertIsNone(self.agent.commit_adapter_for(reworded))
        self.agent.driver = type('NoText', (), {'capabilities': set()})()
        self.assertIsNone(self.agent.commit_adapter_for(self.bound(self.doc, 'note.txt')))

    def test_the_consent_text_names_the_file(self):
        flow = self.bound(self.doc, 'note.txt')
        self.assertIn(self.doc, flow.steps[0].intent_text)
        self.assertEqual('note.txt', flow.steps[0].binding.resource_identity)
        # the witness is rendered too: the focused window titled note.txt
        self.assertEqual('note.txt', flow.steps[0].binding.resource_identity_from['title'])

if __name__ == '__main__':
    unittest.main()
