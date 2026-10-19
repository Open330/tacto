"""Malformed acceptance clocks never turn dispatch into completion evidence."""
import hashlib
from pathlib import Path
import tempfile
import unittest

from tacto.artifact_receipt import ArtifactReceiptError, FreshFileReceipt
from tacto.commit_evidence import CommitEvidence, make_event
from tacto.delivery_receipt import DeliveredMessageReceipt, InboxDelivery, PendingDelivery
from tacto.existing_file_receipt import ExistingTextFileReceipt, SAVE_PATH
from tacto.producer_file_receipt import ProducerFileReceipt, ProducerObservation


class ReceiptClockTests(unittest.TestCase):
    kinds = ('fresh', 'existing', 'producer', 'delivery')

    def test_invalid_callback_configuration_never_falls_back_to_real_time(self):
        for kind in self.kinds:
            for clock in (False,0,'clock',{}):
                with self.subTest(kind=kind,clock=clock), self.assertRaises(ValueError):
                    self.fixture(kind,clock)

    def test_falsy_callable_is_still_the_explicit_host_clock(self):
        class Clock:
            def __bool__(self): return False
            def __call__(self): return float('nan')
        for kind in self.kinds:
            with self.subTest(kind=kind):
                receipt,event,context,reads=self.fixture(kind,Clock())
                with self.assertRaisesRegex(ArtifactReceiptError,'^invalid_receipt_clock$'):
                    receipt.after_commit(event,{'type':'acted'},context)

    def test_replay_exposes_only_the_closed_clock_refusal_code(self):
        from tacto.flow import _receipt_public_codes
        self.assertIn('invalid_receipt_clock',_receipt_public_codes())
        self.assertNotIn('PRIVATE_CLOCK_SENTINEL',_receipt_public_codes())

    def fixture(self, kind, clock, timeout=.03):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        directory = Path(folder.name).resolve(); directory.chmod(0o700)
        path = directory / 'note.txt'; body = '소유 테스트\n'
        digest = hashlib.sha256(body.encode()).hexdigest()
        reads = []
        event = make_event({'name':'click','element_index':1}, ['write_persistent'], 'owned.app', obs_id='owned')
        state = [None]
        if kind == 'fresh':
            receipt = FreshFileReceipt(directory, path.name, body, clock=clock, timeout_seconds=timeout)
            publish = lambda: path.write_text(body)
        elif kind == 'existing':
            path.write_text('old')
            class Doc:
                capabilities = {'evidence.document_text','action.menu.focused_document'}
                def document_text(self, *args, **kwargs):
                    reads.append('document')
                    return {'type':'document_text','document_token':'owned-token','document_path':str(path),
                            'text':body,'text_complete':True}
            event = make_event({'name':'menu','path':SAVE_PATH,'focused_document':str(path)},
                               ['write_persistent'],'com.apple.TextEdit',obs_id='owned')
            receipt = ExistingTextFileReceipt(Doc(), document_path=str(path), clock=clock, timeout_seconds=timeout)
            publish = lambda: path.write_text(body)
        elif kind == 'producer':
            state[0] = ProducerObservation('owned.app',42,123.,'owned-nonce',0,'ready')
            def observe(): reads.append('producer'); return state[0]
            receipt = ProducerFileReceipt(directory,path.name,body,producer='owned.app',pid=42,
                start_time=123.,nonce='owned-nonce',read_producer=observe,clock=clock,timeout_seconds=timeout)
            def publish():
                path.write_text(body)
                state[0] = ProducerObservation('owned.app',42,123.,'owned-nonce',1,'written',str(path),digest)
        else:
            event = make_event({'name':'click','element_index':1},['transmit'],'owned.app',obs_id='owned')
            def bind(e):
                return PendingDelivery('owned-request',e['target'],e['obs_id'],e['action_digest'],'sender','recipient',digest)
            def inbox(request): reads.append('inbox'); return state[0]
            receipt = DeliveredMessageReceipt(target='owned.app',account='sender',recipient='recipient',text=body,
                bind_request=bind,read_inbox=inbox,clock=clock,timeout_seconds=timeout)
            def publish(): state[0] = InboxDelivery('owned-request','owned-message','sender','recipient',digest,'delivered')
        context = receipt.prepare_commit(event); publish()
        return receipt, event, context, reads

    def test_bad_initial_clock_refuses_before_post_dispatch_read_and_consumes_attempt(self):
        for kind in self.kinds:
            for value in (float('nan'),float('inf'),float('-inf'),True,'0',10**400):
                with self.subTest(kind=kind,value_type=type(value).__name__):
                    receipt,event,context,reads = self.fixture(kind,lambda:value)
                    before = list(reads)
                    with self.assertRaisesRegex(ArtifactReceiptError,'^invalid_receipt_clock$') as error:
                        receipt.after_commit(event,{'type':'acted'},context)
                    self.assertEqual('invalid_receipt_clock',error.exception.public_code)
                    self.assertEqual(before,reads)
                    with self.assertRaises(ArtifactReceiptError):
                        receipt.after_commit(event,{'type':'acted'},context)

    def test_nonfinite_or_rewinding_final_clock_never_accepts_matching_receiving_state(self):
        for kind in self.kinds:
            for value in (float('nan'),float('inf'),-1.):
                with self.subTest(kind=kind,value=value):
                    calls=[]
                    def clock():
                        calls.append(None); return 0. if len(calls)<=2 else value
                    receipt,event,context,reads = self.fixture(kind,clock)
                    with self.assertRaisesRegex(ArtifactReceiptError,'^invalid_receipt_clock$'):
                        receipt.after_commit(event,{'type':'error','effect':'unknown'},context)

    def test_overflowed_deadline_is_not_unlimited_receipt_acceptance(self):
        for kind in self.kinds:
            with self.subTest(kind=kind):
                receipt,event,context,reads = self.fixture(kind,lambda:1e308,timeout=1e308)
                with self.assertRaisesRegex(ArtifactReceiptError,'^invalid_receipt_clock$'):
                    receipt.after_commit(event,{'type':'acted'},context)

    def test_clock_exception_is_sanitized_without_cause(self):
        def clock(): raise RuntimeError('PRIVATE_CLOCK_SENTINEL')
        for kind in self.kinds:
            with self.subTest(kind=kind):
                receipt,event,context,reads = self.fixture(kind,clock)
                with self.assertRaisesRegex(ArtifactReceiptError,'^invalid_receipt_clock$') as error:
                    receipt.after_commit(event,{'type':'acted'},context)
                self.assertIsNone(error.exception.__cause__)
                self.assertNotIn('PRIVATE',str(error.exception))

    def test_finite_nondecreasing_clock_still_accepts_exact_owned_state(self):
        for kind in self.kinds:
            with self.subTest(kind=kind):
                receipt,event,context,reads = self.fixture(kind,lambda:0.)
                self.assertIs(type(receipt.after_commit(event,{'type':'acted'},context)),CommitEvidence)


if __name__ == '__main__':
    unittest.main()
