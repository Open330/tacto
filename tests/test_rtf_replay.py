"""Real RTF readback through replay; synthetic UI, no foreground application."""
import hashlib
from pathlib import Path
import tempfile
import unittest
import uuid

from tacto.contract import ContextBinding, Resource, SafetyContract, issue_approval
from tacto.flow import Flow, Selector, Step, replay
from tacto.rtf_receipt import FreshRTFReceipt


class FileWritingUI:
    def __init__(self, path, raw, wrong_document=False):
        self.path = path
        self.raw = raw
        self.wrong_document = wrong_document
        self.dispatches = 0
        self.document = None

    def identity(self, target):
        return target

    def observe(self, *args, **kwargs):
        return {'type': 'observation', 'obs_id': 'save-panel',
                'tree': '0 Button Title: Save'}

    def act(self, obs_id, target, action, **kwargs):
        assert action['name'] == 'click' and action['element_index'] == 0
        self.dispatches += 1
        if self.raw is not None:
            self.path.write_bytes(self.raw)
        self.document = str(self.path.with_name('different.rtf') if self.wrong_document else self.path)
        return {'type': 'acted'}


@unittest.skipUnless(Path('/usr/bin/textutil').is_file(), 'macOS textutil decoder')
class RTFReplayTests(unittest.TestCase):
    def run_save(self, text, raw, *, wrong_document=False, existing=False,
                 timeout_seconds=.5):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name).resolve()
        path = directory / ('nlc-replay-' + uuid.uuid4().hex + '.rtf')
        driver = FileWritingUI(path, raw, wrong_document)
        adapter = FreshRTFReceipt(directory, path.name, text,
                                  document_path=lambda: driver.document,
                                  timeout_seconds=timeout_seconds, poll_interval=.02)
        # Exercise the same driver hook discovery used by native/eval callers.
        driver.prepare_commit = adapter.prepare_commit
        driver.after_commit = adapter.after_commit
        driver.commit_expected = adapter.commit_expected
        if existing:
            path.write_bytes(b'pre-existing content')
        contract = SafetyContract(effects=['write_persistent'], reversibility='recoverable',
                                  resource=Resource(scope='item', object_identity=path.name))
        binding = ContextBinding(bundle_id='com.apple.TextEdit', role='Button', title='Save',
                                 operation='click')
        flow = Flow('fresh_rtf_save', 'com.apple.TextEdit', [
            Step('click', Selector(role='Button', title='Save'), safety=contract, binding=binding)])
        result = replay(driver, flow, approvals={0: issue_approval(binding, contract)})
        return driver, result

    def test_korean_and_empty_text_confirm_one_dispatch_with_real_decoder(self):
        for text, raw in [('한글', b'{\\rtf1\\ansi\\uc1 \\u54620?\\u44544?}'),
                          ('', b'{\\rtf1\\ansi }')]:
            with self.subTest(text=text):
                # Match the adapter's production readback budget for this
                # positive integration with an external macOS decoder. Tight
                # deadline refusal is covered separately in test_rtf_receipt.
                driver, result = self.run_save(text, raw, timeout_seconds=3)
                self.assertTrue(result.ok, result.log)
                self.assertEqual(result.log[-1]['status'], 'done')
                self.assertEqual(driver.dispatches, 1)
                self.assertEqual(len(result.events), 1)
                event = result.events[0]
                self.assertEqual(event['state'], 'confirmed')
                proof = result.commit_evidence[event['event_id']]
                self.assertEqual(proof.action_digest, event['action_digest'])
                self.assertEqual(proof.evidence.sha256, hashlib.sha256(raw).hexdigest())
                self.assertEqual(driver.path.read_bytes(), raw)

    def test_missing_corrupt_or_wrong_document_stays_unverified_without_retry(self):
        for case, raw, wrong_document in [('missing', None, False),
                                          ('corrupt', b'not an RTF document', False),
                                          ('wrong AXDocument', b'{\\rtf1\\ansi Hello}', True)]:
            with self.subTest(case=case):
                driver, result = self.run_save('Hello', raw, wrong_document=wrong_document)
                self.assertFalse(result.ok)
                self.assertEqual(result.log[-1]['status'], 'unverified', result.log)
                self.assertEqual(driver.dispatches, 1)
                self.assertEqual(len(result.events), 1)
                self.assertEqual(result.events[0]['state'], 'dispatched')
                self.assertEqual(result.commit_evidence, {})

    def test_file_created_before_prepare_prevents_dispatch_and_preserves_bytes(self):
        driver, result = self.run_save('Hello', b'{\\rtf1\\ansi Hello}', existing=True)
        self.assertFalse(result.ok)
        self.assertEqual(driver.dispatches, 0)
        self.assertEqual(result.events, [])
        self.assertEqual(result.commit_evidence, {})
        self.assertEqual(result.log[-1]['detail']['error'], 'host_evidence_preparation_failed', result.log)
        self.assertEqual(result.log[-1]['detail']['effect'], 'not_started')
        self.assertEqual(driver.path.read_bytes(), b'pre-existing content')


if __name__ == '__main__':
    unittest.main()
