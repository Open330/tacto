"""Trusted completion must be new, single, bound, and agree with disk bytes."""
from dataclasses import replace
import hashlib
from pathlib import Path
import tempfile
import unittest
from tacto.artifact_receipt import ArtifactReceiptError
from tacto.producer_file_receipt import ProducerFileReceipt,ProducerObservation
from tacto.commit_evidence import evidence_error,make_event,ProducerArtifactEvidence,ArtifactEvidence,CommitEvidence


class ProducerReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.directory=Path(self.tmp.name).resolve();self.directory.chmod(0o700)
        self.path=self.directory/'fresh.txt';self.body='새 파일\n'
        self.observation=ProducerObservation('owned.app',42,123.0,'host-nonce',0,'ready')
        self.receipt=ProducerFileReceipt(self.directory,'fresh.txt',self.body,
            producer='owned.app',pid=42,start_time=123.0,nonce='host-nonce',
            read_producer=lambda:self.observation,timeout_seconds=.03,poll_interval=.005)
        self.event=make_event({'name':'click','element_index':1},['write_persistent'],'owned.app',obs_id='o')

    def complete(self,**changes):
        self.path.write_text(self.body)
        self.observation=replace(self.observation,sequence=1,status='written',
            path=str(self.path),sha256=hashlib.sha256(self.body.encode()).hexdigest(),**changes)

    def test_callback_plus_file_confirms_uncertain_dispatch_once(self):
        ctx=self.receipt.prepare_commit(self.event);self.complete()
        proof=self.receipt.after_commit(self.event,{'type':'error','effect':'unknown'},ctx)
        self.assertIs(type(proof.evidence),ProducerArtifactEvidence)
        self.assertIsNone(evidence_error({**self.event,'commit_evidence':proof},self.receipt.commit_expected))
        with self.assertRaises(ArtifactReceiptError):self.receipt.after_commit(self.event,{'type':'acted'},ctx)

    def test_completion_during_file_preparation_never_returns_dispatch_context(self):
        from unittest.mock import patch
        original=self.receipt._fresh.prepare_commit
        def prepare_then_publish(event):
            context=original(event)
            # This write/callback occurs before ProducerFileReceipt returns
            # its context; no driver input has been requested.
            self.complete()
            return context
        with patch.object(self.receipt._fresh,'prepare_commit',side_effect=prepare_then_publish) as prepare:
            with self.assertRaisesRegex(ArtifactReceiptError,'producer_preparation_changed'):
                self.receipt.prepare_commit(self.event)
            self.assertEqual(1,prepare.call_count)
            with self.assertRaisesRegex(ArtifactReceiptError,'producer_already_prepared'):
                self.receipt.prepare_commit(self.event)
            self.assertEqual(1,prepare.call_count)
        self.assertEqual(self.body,self.path.read_text())
        with self.assertRaisesRegex(ArtifactReceiptError,'producer_commit_stale'):
            self.receipt.after_commit(self.event,{'type':'error','effect':'unknown'},self.receipt._fresh._prepared)

    def test_post_prepare_callback_change_failure_or_restart_is_closed_and_consumed(self):
        from unittest.mock import patch
        for mode in ('sequence','failed','restart','reader_error'):
            with self.subTest(mode=mode):
                self.observation=ProducerObservation('owned.app',42,123.0,'host-nonce',0,'ready')
                reads=[]
                def read():reads.append(mode);return self.observation
                receipt=ProducerFileReceipt(self.directory,'fresh.txt',self.body,
                    producer='owned.app',pid=42,start_time=123,nonce='host-nonce',read_producer=read)
                original=receipt._fresh.prepare_commit
                def prepare_then_change(event):
                    context=original(event)
                    if mode=='reader_error':
                        def failed():reads.append(mode);raise RuntimeError('PRIVATE_CALLBACK_SENTINEL')
                        receipt._reader=failed
                    else:
                        self.observation=replace(self.observation,**({'sequence':1} if mode=='sequence'
                            else {'status':'failed'} if mode=='failed' else {'start_time':124.0}))
                    return context
                expected=('producer_observation_unavailable' if mode=='reader_error' else
                    'producer_observation_invalid' if mode=='restart' else 'producer_preparation_changed')
                with patch.object(receipt._fresh,'prepare_commit',side_effect=prepare_then_change):
                    with self.assertRaisesRegex(ArtifactReceiptError,expected) as error:receipt.prepare_commit(self.event)
                self.assertEqual(2,len(reads))
                self.assertIsNone(error.exception.__cause__)
                self.assertNotIn('PRIVATE_CALLBACK_SENTINEL',str(error.exception))
                self.assertIsNone(receipt._prepared)
                with self.assertRaisesRegex(ArtifactReceiptError,'producer_already_prepared'):receipt.prepare_commit(self.event)
                self.assertEqual(2,len(reads))

    def test_callback_changed_during_file_verification_never_confirms_and_cannot_retry(self):
        from unittest.mock import patch
        for change in ({'sequence':2},{'status':'failed'},{'nonce':'foreign'},
                       {'start_time':124.0},{'path':str(self.path.parent/'other.txt')}):
            with self.subTest(change=change):
                self.path.unlink(missing_ok=True)
                self.observation=ProducerObservation('owned.app',42,123.0,'host-nonce',0,'ready')
                receipt=ProducerFileReceipt(self.directory,'fresh.txt',self.body,
                    producer='owned.app',pid=42,start_time=123,nonce='host-nonce',
                    read_producer=lambda:self.observation,timeout_seconds=.1)
                context=receipt.prepare_commit(self.event);self.complete()
                original=receipt._fresh.after_commit
                def read_file_then_change(*args,**kwargs):
                    proof=original(*args,**kwargs)
                    self.assertIsNotNone(proof)
                    self.observation=replace(self.observation,**change)
                    return proof
                with patch.object(receipt._fresh,'after_commit',side_effect=read_file_then_change):
                    with self.assertRaises(ArtifactReceiptError) as error:receipt.after_commit(self.event,{'type':'acted'},context)
                self.assertEqual(error.exception.public_code,
                    'producer_observation_invalid' if 'nonce' in change or 'start_time' in change else 'producer_callback_changed')
                with self.assertRaises(ArtifactReceiptError):receipt.after_commit(self.event,{'type':'acted'},context)

    def test_post_file_producer_read_failure_is_private_and_does_not_confirm(self):
        from unittest.mock import patch
        context=self.receipt.prepare_commit(self.event);self.complete()
        original=self.receipt._fresh.after_commit
        def read_file_then_lose_reader(*args,**kwargs):
            proof=original(*args,**kwargs)
            self.receipt._reader=lambda:(_ for _ in ()).throw(RuntimeError('PRIVATE_PRODUCER_LOG'))
            return proof
        with patch.object(self.receipt._fresh,'after_commit',side_effect=read_file_then_lose_reader):
            with self.assertRaisesRegex(ArtifactReceiptError,'producer_observation_unavailable') as error:
                self.receipt.after_commit(self.event,{'type':'error','effect':'unknown'},context)
        self.assertIsNone(error.exception.__cause__)

    def test_post_file_recheck_cannot_extend_original_deadline(self):
        from unittest.mock import patch
        now=[0.0]
        receipt=ProducerFileReceipt(self.directory,'fresh.txt',self.body,
            producer='owned.app',pid=42,start_time=123,nonce='host-nonce',
            read_producer=lambda:self.observation,timeout_seconds=1,clock=lambda:now[0])
        context=receipt.prepare_commit(self.event);self.complete()
        original=receipt._fresh.after_commit
        def file_then_slow_callback(*args,**kwargs):
            proof=original(*args,**kwargs)
            def late():
                now[0]=2.0
                return self.observation
            receipt._reader=late
            return proof
        with patch.object(receipt._fresh,'after_commit',side_effect=file_then_slow_callback):
            self.assertIsNone(receipt.after_commit(self.event,{'type':'acted'},context))
        with self.assertRaises(ArtifactReceiptError):receipt.after_commit(self.event,{'type':'acted'},context)

    def test_bytes_alone_do_not_confirm_producer(self):
        ctx=self.receipt.prepare_commit(self.event);self.path.write_text(self.body)
        self.assertIsNone(self.receipt.after_commit(self.event,{'type':'acted'},ctx))
        bare=CommitEvidence(self.event['event_id'],self.event['action_digest'],
            ArtifactEvidence('state',str(self.path),hashlib.sha256(self.body.encode()).hexdigest()))
        self.assertEqual(evidence_error({**self.event,'commit_evidence':bare},self.receipt.commit_expected),
                         'artifact_producer_evidence_missing')

    def test_duplicate_foreign_and_inconsistent_callbacks_never_confirm(self):
        for change in ({'sequence':2},{'nonce':'other'},{'pid':True},{'start_time':124},
                       {'path':str(self.path.parent/'other.txt')},{'sha256':'0'*64}):
            with self.subTest(change=change):
                self.observation=ProducerObservation('owned.app',42,123.0,'host-nonce',0,'ready')
                receipt=ProducerFileReceipt(self.directory,'fresh.txt',self.body,
                    producer='owned.app',pid=42,start_time=123,nonce='host-nonce',
                    read_producer=lambda:self.observation,timeout_seconds=.03,poll_interval=.005)
                self.path.unlink(missing_ok=True);ctx=receipt.prepare_commit(self.event)
                self.complete();self.observation=replace(self.observation,**change)
                with self.assertRaises(ArtifactReceiptError):receipt.after_commit(self.event,{'type':'acted'},ctx)

    def test_known_non_dispatch_and_wrong_event_refuse_without_second_read(self):
        ctx=self.receipt.prepare_commit(self.event)
        with self.assertRaises(ArtifactReceiptError):
            self.receipt.after_commit(self.event,{'type':'error','effect':'not_started'},ctx)
        with self.assertRaises(ArtifactReceiptError):
            self.receipt.after_commit(self.event,{'type':'acted'},ctx)

    def test_callback_cannot_authorize_a_different_target(self):
        event=make_event({'name':'click','element_index':1},['write_persistent'],'com.apple.TextEdit')
        with self.assertRaisesRegex(ArtifactReceiptError,'producer_target_mismatch'):
            self.receipt.prepare_commit(event)

    def test_late_callback_cannot_restart_file_polling_budget(self):
        now=[0.0]
        def read():
            if self.observation.status=='written':now[0]=2.0
            return self.observation
        receipt=ProducerFileReceipt(self.directory,'fresh.txt',self.body,
            producer='owned.app',pid=42,start_time=123,nonce='host-nonce',
            read_producer=read,timeout_seconds=1,clock=lambda:now[0])
        context=receipt.prepare_commit(self.event);self.complete()
        self.assertIsNone(receipt.after_commit(self.event,{'type':'acted'},context))
        with self.assertRaises(ArtifactReceiptError):
            receipt.after_commit(self.event,{'type':'acted'},context)

    def test_reader_private_errors_are_closed_before_dispatch(self):
        def read():raise RuntimeError('PRIVATE_PRODUCER_LOG')
        receipt=ProducerFileReceipt(self.directory,'fresh.txt',self.body,
            producer='owned.app',pid=42,start_time=123,nonce='host-nonce',read_producer=read)
        with self.assertRaises(ArtifactReceiptError) as error:receipt.prepare_commit(self.event)
        self.assertEqual(str(error.exception),'producer_observation_unavailable')
        self.assertIsNone(error.exception.__cause__)


if __name__=='__main__':unittest.main()
