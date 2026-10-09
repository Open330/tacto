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
    capabilities = {'evidence.document_text', 'action.menu.focused_document'}

    def __init__(self, path, text):
        self.path, self.text, self.token = path, text, 'tok'

    def document_text(self, target, document_token=None, document_path=None, **k):
        self.selected = document_path
        return {'type': 'document_text', 'document_token': self.token, 'text': self.text,
                'text_complete': True, 'document_path': self.path}


def save_event(path, obs='o'):
    return make_event({'name': 'menu', 'path': SAVE_PATH, 'focused_document': path},
                      {'write_persistent'}, 'com.apple.TextEdit', obs_id=obs)


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
        event = save_event(self.path)
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
        with self.assertRaisesRegex(ArtifactReceiptError, 'file_does_not_hold_text'):
            self.run_save(self.receipt())

    def test_different_bytes_no_receipt(self):
        with self.assertRaisesRegex(ArtifactReceiptError, 'file_does_not_hold_text'):
            self.run_save(self.receipt(), lambda: Path(self.path).write_text('new text 4417\n', encoding='utf-8'))

    def test_text_or_document_changing_during_the_save_refuses(self):
        r = self.receipt()
        def edit():
            self.doc.text = 'something else'
        with self.assertRaisesRegex(ArtifactReceiptError, 'changed_during_save'):
            self.run_save(r, edit)
        r2 = ExistingTextFileReceipt(Doc(self.path, 'x'), document_path=self.path,
                                     timeout_seconds=0.2, poll_interval=0.01)
        event = save_event(self.path); ctx = r2.prepare_commit(event)
        r2._driver.token = 'other'
        with self.assertRaisesRegex(ArtifactReceiptError, 'document_identity_changed'):
            r2.after_commit(event, {}, ctx)

    def test_only_a_canonical_txt_path_and_only_that_document(self):
        rtf = os.path.join(self.dir, 'doc.rtf'); Path(rtf).write_text('x')
        for path in (rtf, None, 'relative.txt'):
            with self.subTest(path=path), self.assertRaises(ExistingFileReceiptUnavailable):
                self.receipt(document_path=path)
        r = self.receipt()
        r.prepare_commit(save_event(self.path))
        self.assertEqual(self.path, self.doc.selected)      # selected by path
        other = os.path.join(self.dir, 'other.txt'); Path(other).write_text('o')
        self.doc.path = other
        with self.assertRaisesRegex(ArtifactReceiptError, 'different_document'):
            self.receipt().prepare_commit(save_event(self.path))

    def test_a_decomposed_backend_path_is_the_same_document(self):
        import unicodedata
        composed = os.path.join(self.dir, '노트.txt')
        Path(composed).write_text('old', encoding='utf-8')
        self.doc.path = unicodedata.normalize('NFD', composed)    # how TextEdit reports it
        r = self.receipt(document_path=composed)
        event = save_event(composed)
        context = r.prepare_commit(event)
        Path(composed).write_text('new text 4417', encoding='utf-8')
        proof = r.after_commit(event, {'type': 'acted'}, context)
        self.assertIs(type(proof), CommitEvidence)

    def test_a_symlinked_file_is_refused(self):
        real = os.path.join(self.dir, 'real.txt'); Path(real).write_text('old')
        os.unlink(self.path); os.symlink(real, self.path)
        # realpath(path) != path → refused before anything is read
        with self.assertRaises(ExistingFileReceiptUnavailable):
            self.receipt()

    def test_only_file_save_menu_and_once(self):
        r = self.receipt()
        for action in ({'name': 'menu', 'path': ['File', 'Save As…'], 'focused_document': self.path},
                       {'name': 'menu', 'path': SAVE_PATH},                       # unbound press
                       {'name': 'menu', 'path': SAVE_PATH, 'focused_document': self.path + 'x'},
                       {'name': 'click', 'element_index': 3}):
            event = make_event(action, {'write_persistent'}, 'com.apple.TextEdit', obs_id='o')
            with self.subTest(action=action), self.assertRaises(ArtifactReceiptError):
                r.prepare_commit(event)
        event = save_event(self.path); ctx = r.prepare_commit(event)
        with self.assertRaises(ArtifactReceiptError):
            r.prepare_commit(save_event(self.path))
        Path(self.path).write_text('new text 4417', encoding='utf-8')
        r.after_commit(event, {}, ctx)
        with self.assertRaises(ArtifactReceiptError):
            r.after_commit(event, {}, ctx)

    def test_a_fifo_named_txt_is_refused_without_blocking(self):
        fifo = os.path.join(self.dir, 'pipe.txt'); os.mkfifo(fifo)
        self.doc.path = fifo
        import time
        started = time.monotonic()
        with self.assertRaises(ArtifactReceiptError):
            self.receipt(document_path=fifo).prepare_commit(save_event(fifo))
        self.assertLess(time.monotonic() - started, 1.0)

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
        for caps in (set(), {'evidence.document_text'}):     # no witness / no helper-side file check
            self.agent.driver = type('Partial', (), {'capabilities': caps})()
            self.assertIsNone(self.agent.commit_adapter_for(self.bound(self.doc, 'note.txt')))

    def test_the_consent_text_names_the_file(self):
        flow = self.bound(self.doc, 'note.txt')
        self.assertIn(self.doc, flow.steps[0].intent_text)
        self.assertEqual('note.txt', flow.steps[0].binding.resource_identity)
        # the witness is rendered too: the focused window titled note.txt
        self.assertEqual('note.txt', flow.steps[0].binding.resource_identity_from['title'])


class FocusedDocumentBoundaryTests(unittest.TestCase):
    """The press is bound to the file by the helper, not by a title match."""
    ROOT = Path(__file__).resolve().parents[1]

    def test_recipe_binds_the_press_to_the_file(self):
        from nlc.library import textedit_save
        step = textedit_save().bind(document='/tmp/a/note.txt', name='note.txt').steps[0]
        self.assertEqual('/tmp/a/note.txt', step.params['focused_document'])

    def test_helper_checks_focused_and_main_windows_canonically(self):
        swift = (self.ROOT / 'helper/NLCHelper.swift').read_text()
        body = swift[swift.index('func focusedDocumentMismatch('):]
        body = body[:body.index('\n}\n')]
        for needed in ('kAXFocusedWindowAttribute as String, kAXMainWindowAttribute as String',
                       'precomposedStringWithCanonicalMapping'):
            self.assertIn(needed, body)
        case = swift[swift.index('    case "menu":'):swift.index('    case "open_folder":')]
        self.assertLess(case.index('focusedDocumentMismatch'), case.index('AXUIElementPerformAction(item'))

    def test_launcher_and_driver(self):
        launcher = (self.ROOT / 'launcher/nlc.swift').read_text()
        self.assertIn('"menu":             (["name", "path"], ["focused_document"]),', launcher)
        driver = (self.ROOT / 'nlc/driver.py').read_text()
        self.assertIn('elif name == "menu" and "focused_document" in action:', driver)

    def test_preparation_errors_show_only_closed_codes(self):
        from nlc.flow import replay
        from nlc.library import textedit_save
        from nlc.existing_file_receipt import _refuse
        class App:
            capabilities = {'action.menu.focused_document'}
            def observe(self, *a, **k):
                return {'type': 'observation', 'obs_id': 'o',
                        'tree': '0 Window  Title: note.txt [focused]'}
            def act(self, *a, **k): raise AssertionError('acted')
            def wait_until(self, *a, **k): return self.observe()
        def leak(event): raise RuntimeError('SECRET document text')
        def leak_receipt(event): raise ArtifactReceiptError('PRIVATE_TEST_TEXT_SENTINEL')
        def coded(event): raise _refuse('no_canonical_path')
        flow = textedit_save().bind(document='/tmp/a/note.txt', name='note.txt')
        def malformed(value):
            def raise_it(event):
                error = ArtifactReceiptError('PRIVATE_TEST_TEXT_SENTINEL')
                error.public_code = value
                raise error
            return raise_it
        cases = [(leak, "'RuntimeError'"), (leak_receipt, "'ArtifactReceiptError'"),
                 (coded, 'ArtifactReceiptError: no_canonical_path')]
        # a malformed public_code (unhashable, wrong type, unknown) shows the class only
        cases += [(malformed(v), "'ArtifactReceiptError'") for v in ([], {}, None, 3, 'not_a_code')]
        for prepare, shown in cases:
            result = replay(App(), flow, approvals={0: True}, prepare_commit=prepare)
            detail = str(result.log[-1])
            self.assertIn(shown, detail)
            self.assertNotIn('SECRET', detail)
            self.assertNotIn('PRIVATE_TEST_TEXT_SENTINEL', detail)


class FocusedDocumentCapabilityGateTests(unittest.TestCase):
    """Without the helper-side check, nothing is sent (Codex review of 31955f0)."""

    def test_helper_driver_sends_nothing_without_the_capability(self):
        from nlc.helper import HelperDriver
        driver = HelperDriver.__new__(HelperDriver)
        driver.capabilities = {'action.menu', 'evidence.document_text'}
        sent = []
        driver._send = lambda msg: sent.append(msg) or {'type': 'acted'}
        result = driver.act('o', 'com.apple.TextEdit',
                            {'name': 'menu', 'path': SAVE_PATH, 'focused_document': '/tmp/a.txt'},
                            risk_class='preapproved', approved=True)
        self.assertEqual([], sent)
        self.assertEqual(('focused_document_guard_unavailable', 'not_started'),
                         (result.get('error'), result.get('effect')))

    def test_replay_refuses_before_input_without_the_capability(self):
        from nlc.flow import replay
        from nlc.library import textedit_save
        class Old:
            capabilities = {'action.menu', 'evidence.document_text'}
            acted = []
            def observe(self, *a, **k):
                return {'type': 'observation', 'obs_id': 'o', 'tree': '0 Window  Title: note.txt [focused]'}
            def act(self, *a, **k): self.acted.append(a); return {'type': 'acted'}
            def wait_until(self, *a, **k): return self.observe()
        app = Old()
        result = replay(app, textedit_save().bind(document='/tmp/a/note.txt', name='note.txt'),
                        approvals={0: True})
        self.assertEqual([], app.acted)
        self.assertEqual('focused_document_guard_unavailable', result.log[-1].get('error'))

    def test_receipt_needs_the_capability(self):
        class TextOnly:
            capabilities = {'evidence.document_text'}
            def document_text(self, *a, **k): return {}
        with self.assertRaisesRegex(ExistingFileReceiptUnavailable, 'no_focused_document_guard'):
            ExistingTextFileReceipt(TextOnly(), document_path=os.path.realpath(tempfile.gettempdir()) + '/x.txt')


class FocusedDocumentSwiftTests(unittest.TestCase):
    """The helper's real focusedDocumentMismatch with fake reads."""

    def test_cases(self):
        import json, shutil, subprocess, unicodedata
        if not shutil.which('swift'):
            self.skipTest('Swift required')
        root = Path(__file__).resolve().parents[1]
        swift = (root / 'helper/NLCHelper.swift').read_text()
        start = swift.index("/// \"\" when the app's focused and main windows both show `want`")
        code = swift[start:swift.index('func focusedDocumentMismatch(pid: pid_t, want: String) -> String {')]
        composed = '/d/' + '노트' + '.txt'
        decomposed = unicodedata.normalize('NFD', composed)
        tail = (root / 'tests/fixtures/focused_document_judge.swift.txt').read_text(encoding='utf-8')
        tail = tail.replace('DECOMPOSED', decomposed).replace('COMPOSED', composed)
        harness = 'import Foundation\nimport ApplicationServices\n' + code + tail
        with tempfile.TemporaryDirectory(prefix='nlc-focused-doc-') as d:
            src = Path(d) / 'main.swift'
            src.write_text(harness, encoding='utf-8')
            run = subprocess.run(['swift', str(src)], capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual({
            "the approved file in both windows": True,
            "same name, other folder focused": False,
            "main window shows another file": False,
            "focused window unreadable": False,
            "decomposed name reported (TextEdit)": True,
            "approved path not canonical": False,
            "relative path": False,
        }, json.loads(run.stdout))

if __name__ == '__main__':
    unittest.main()
