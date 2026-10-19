"""Causal host evidence can continue new input without resetting old slots."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen

from tacto.commit_evidence import (CommitEvidence, DeliveryEvidence, TranscriptEvidence,
                                ProducerArtifactEvidence, SavedFileStateEvidence, make_event)
from tacto.dispatch_journal import DurableDispatchDriver
from tacto.journal_reconciliation import CommitReconciliation, JournalReconciliationError, reconcile_journal, reconcile_and_replay


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.directory=Path(temp.name)/'journal'
        self.event=make_event({'name':'send','request_id':'first','body':'one'},{'transmit'},'owned-server',obs_id='original')
        self.expected={'받는 사람':'owned-recipient','보내는 계정':'owned-account','보낸 내용':'one'}
        self.plan=CommitReconciliation(self.event,self.expected)
        self.driver=Mock();self.driver.act.return_value={'type':'acted'}

    def proof(self, detail=None):
        detail=detail or DeliveryEvidence(receipt_id='server-receipt',recipient='owned-recipient',account='owned-account',body_sha256=hashlib.sha256(b'one').hexdigest())
        return CommitEvidence(self.event['event_id'],self.event['action_digest'],detail)

    def reserve(self):
        with DurableDispatchDriver(self.driver,self.directory) as guard:
            guard.act('original',self.event['target'],self.event['action'])
        self.driver.reset_mock()

    def test_fresh_causal_receipt_advances_without_modifying_original_record(self):
        self.reserve();old=(self.directory/'00000.jsonl').read_bytes()
        with DurableDispatchDriver(self.driver,self.directory) as guard:
            self.assertEqual(1,reconcile_journal(guard,[self.plan],lambda slot,event:self.proof()))
            self.assertEqual('acted',guard.act('fresh','owned-server',{'name':'send','request_id':'second'})['type'])
        self.assertEqual(old,(self.directory/'00000.jsonl').read_bytes())
        self.assertTrue((self.directory/'00001.jsonl').is_file());self.driver.act.assert_called_once()

    def test_close_waits_for_inflight_reconciliation_before_closing_descriptor(self):
        from concurrent.futures import ThreadPoolExecutor
        self.reserve()
        reading=threading.Event();release=threading.Event();closing=threading.Event();closed=threading.Event()
        guard=DurableDispatchDriver(self.driver,self.directory)
        self.addCleanup(guard.close_journal)
        def read(*args):
            reading.set()
            if not release.wait(3):raise TimeoutError()
            return self.proof()
        def close():
            closing.set();guard.close_journal();closed.set()
        with ThreadPoolExecutor(max_workers=2) as pool:
            reconciliation=pool.submit(reconcile_journal,guard,[self.plan],read)
            self.assertTrue(reading.wait(3))
            shutdown=pool.submit(close)
            try:
                self.assertTrue(closing.wait(3))
                self.assertFalse(closed.wait(.05))
            finally:release.set()
            self.assertEqual(1,reconciliation.result(timeout=3));shutdown.result(timeout=3)
        self.assertTrue(closed.is_set());self.assertIsNone(guard._directory_fd)
        self.driver.act.assert_not_called()

    def test_continuation_validation_precedes_evidence_and_input(self):
        from tacto.flow import Flow
        read=Mock()
        with self.assertRaisesRegex(JournalReconciliationError,'invalid_reconciliation'):
            reconcile_and_replay(None,[self.plan],read,Flow('new','owned'))
        with DurableDispatchDriver(self.driver,self.directory) as guard:
            with self.assertRaisesRegex(JournalReconciliationError,'invalid_reconciliation'):
                reconcile_and_replay(guard,[self.plan],read,Flow('new','owned'),unsupported_option=True)
            self.assertFalse(guard._reconciliation_attempted)
            with self.assertRaisesRegex(JournalReconciliationError,'invalid_commit_plan'):
                reconcile_and_replay(guard,[self.plan],read,Flow('new','owned',schema=1))
        read.assert_not_called();self.driver.act.assert_not_called()

    def test_clock_hook_cannot_dispatch_before_the_reconciliation_gate(self):
        with DurableDispatchDriver(self.driver,self.directory) as guard:
            def clock():
                guard.act('fresh','owned-server',{'name':'send','request_id':'unexpected'})
                return 0.0
            with self.assertRaises(JournalReconciliationError):
                reconcile_journal(guard,[self.plan],Mock(),clock=clock)
            self.driver.act.assert_not_called()
            self.assertEqual('not_started',guard.act('fresh','owned',{'name':'click'})['effect'])

    def test_lazy_plan_cannot_dispatch_while_the_plan_is_materialized(self):
        with DurableDispatchDriver(self.driver,self.directory) as guard:
            def plans():
                guard.act('fresh','owned-server',{'name':'send','request_id':'unexpected'})
                yield self.plan
            with self.assertRaises(JournalReconciliationError):
                reconcile_journal(guard,plans(),Mock())
            self.driver.act.assert_not_called()

    def test_nonfinite_and_backwards_clocks_never_open_the_gate(self):
        self.reserve()
        for values in ([float('nan')],[0.0,-1.0],[1.79e308]):
            with self.subTest(values=values),DurableDispatchDriver(self.driver,self.directory) as guard:
                clock=Mock(side_effect=values)
                with self.assertRaisesRegex(JournalReconciliationError,'invalid_reconciliation'):
                    reconcile_journal(guard,[self.plan],Mock(),clock=clock,
                        timeout_seconds=1.79e308 if values==[1.79e308] else 30)
                self.assertEqual('not_started',guard.act('fresh','owned',{'name':'click'})['effect'])
        self.driver.act.assert_not_called()

    def test_missing_wrong_body_and_sender_only_proofs_never_unblock(self):
        self.reserve()
        for proof in (None,True,self.proof(DeliveryEvidence('r','owned-recipient','owned-account','0'*64)),
                      self.proof(TranscriptEvidence('r','owned-recipient',hashlib.sha256(b'one').hexdigest()))):
            with self.subTest(proof=type(proof).__name__),DurableDispatchDriver(self.driver,self.directory) as guard:
                with self.assertRaisesRegex(JournalReconciliationError,'causal_commit_unverified'):
                    reconcile_journal(guard,[self.plan],lambda *args:proof)
                self.assertEqual('not_started',guard.act('fresh','owned',{'name':'click'})['effect'])
        self.driver.act.assert_not_called()

    def test_replaying_the_completed_operation_never_uses_a_new_slot(self):
        self.reserve()
        with DurableDispatchDriver(self.driver,self.directory) as guard:
            reconcile_journal(guard,[self.plan],lambda *args:self.proof())
            result=guard.act('fresh','owned-server',self.event['action'])
            self.assertEqual('dispatch_journal_reconciled_operation_replay',result['error'])
            self.assertEqual('not_started',guard.act('fresh','owned-server',{'name':'send','request_id':'new'})['effect'])
        self.assertEqual(1,len(list(self.directory.iterdir())))
        self.driver.act.assert_not_called()

    def test_changed_gui_index_does_not_turn_an_old_commit_into_new_input(self):
        self.event=make_event({'name':'click','element_index':1},{'transmit'},'owned-server',obs_id='original')
        self.plan=CommitReconciliation(self.event,self.expected);self.reserve()
        with DurableDispatchDriver(self.driver,self.directory) as guard:
            reconcile_journal(guard,[self.plan],lambda *args:self.proof())
            result=guard.act('fresh','owned-server',{'name':'click','element_index':999})
            self.assertEqual('dispatch_journal_reconciled_operation_replay',result['error'])
        self.driver.act.assert_not_called()

    def test_changed_record_during_or_after_reconciliation_blocks_input(self):
        for when in ('during','after'):
            with self.subTest(when=when):
                # Separate owned journal for each case.
                self.directory=self.directory.parent/when;self.reserve()
                record=self.directory/'00000.jsonl'
                def read(*args):
                    if when=='during':record.write_bytes(b'torn')
                    return self.proof()
                with DurableDispatchDriver(self.driver,self.directory) as guard:
                    if when=='during':
                        with self.assertRaisesRegex(JournalReconciliationError,'journal_changed'):
                            reconcile_journal(guard,[self.plan],read)
                    else:
                        reconcile_journal(guard,[self.plan],read);record.write_bytes(b'torn')
                    self.assertEqual('not_started',guard.act('fresh','owned',{'name':'click'})['effect'])
                self.driver.act.assert_not_called()

    def test_late_receipt_cancel_and_reentrant_input_remain_blocked(self):
        self.reserve()
        for mode in ('deadline','cancel','input'):
            with self.subTest(mode=mode),DurableDispatchDriver(self.driver,self.directory) as guard:
                now=[0.0];cancel=[False]
                def read(*args):
                    if mode=='deadline':now[0]=2.0
                    elif mode=='cancel':cancel[0]=True
                    else:guard.act('fresh','owned',{'name':'click'})
                    return self.proof()
                with self.assertRaises(JournalReconciliationError):
                    reconcile_journal(guard,[self.plan],read,timeout_seconds=1,
                        clock=lambda:now[0],cancelled=lambda:cancel[0])
                self.assertEqual('not_started',guard.act('fresh','owned',{'name':'click'})['effect'])
        self.driver.act.assert_not_called()

    def test_original_plan_is_independent_of_callback_mutation_and_reader_errors_are_private(self):
        self.reserve()
        with DurableDispatchDriver(self.driver,self.directory) as guard:
            def read(slot,event):
                event['event_id']='changed'
                self.plan._expected['보낸 내용']='wrong'
                return self.proof()
            reconcile_journal(guard,[self.plan],read)
        self.plan=CommitReconciliation(self.event,self.expected)
        with DurableDispatchDriver(self.driver,self.directory) as guard:
            def failed(*args):raise RuntimeError('PRIVATE_BODY_SENTINEL')
            with self.assertRaises(JournalReconciliationError) as failure:
                reconcile_journal(guard,[self.plan],failed)
            self.assertNotIn('PRIVATE_BODY_SENTINEL',str(failure.exception))
            self.assertEqual('not_started',guard.act('fresh','owned',{'name':'click'})['effect'])

    def test_callback_cannot_publish_private_text_as_a_refusal_code(self):
        self.reserve()
        with DurableDispatchDriver(self.driver,self.directory) as guard:
            def failed(*args):raise JournalReconciliationError('PRIVATE_BODY_SENTINEL')
            with self.assertRaisesRegex(JournalReconciliationError,'^reconciliation_unavailable$'):
                reconcile_journal(guard,[self.plan],failed)
            self.assertEqual('not_started',guard.act('fresh','owned',{'name':'click'})['effect'])

    def test_operation_mismatch_incomplete_plan_and_torn_catalog_refuse(self):
        self.reserve()
        wrong=CommitReconciliation(make_event({'name':'send','body':'other'},{'transmit'},'owned-server'),self.expected)
        for plans in ([wrong],[self.plan,self.plan]):
            with DurableDispatchDriver(self.driver,self.directory) as guard:
                read=Mock()
                with self.assertRaisesRegex(JournalReconciliationError,'commit_plan_mismatch'):
                    reconcile_journal(guard,plans,read)
                read.assert_not_called()
        (self.directory/'00002.jsonl').write_text('torn');(self.directory/'00002.jsonl').chmod(0o600)
        with DurableDispatchDriver(self.driver,self.directory) as guard:
            with self.assertRaisesRegex(JournalReconciliationError,'invalid_journal_catalog'):
                reconcile_journal(guard,[self.plan],Mock())

    def test_one_receiver_receipt_cannot_reconcile_two_commits(self):
        second=make_event({'name':'send','request_id':'second','body':'one'},{'transmit'},'owned-server',obs_id='next')
        second_plan=CommitReconciliation(second,self.expected)
        with DurableDispatchDriver(self.driver,self.directory) as guard:
            guard.act('original','owned-server',self.event['action'])
            guard.act('next','owned-server',second['action'])
        self.driver.reset_mock()
        with DurableDispatchDriver(self.driver,self.directory) as guard:
            def read(slot,event):
                return CommitEvidence(event['event_id'],event['action_digest'],self.proof().evidence)
            with self.assertRaisesRegex(JournalReconciliationError,'duplicate_causal_receipt'):
                reconcile_journal(guard,[self.plan,second_plan],read)
            self.assertEqual('not_started',guard.act('fresh','owned',{'name':'click'})['effect'])
        self.driver.act.assert_not_called()

    def test_producer_plan_requires_producer_evidence_not_saved_state(self):
        path=self.directory.parent/'owned.txt';path.write_bytes(b'body')
        self.event=make_event({'name':'click','element_index':1},{'write_persistent'},'owned-producer',obs_id='original')
        expected={'artifact_evidence_scope':'producer','producer':'owned-producer',
            'producer_pid':4321,'producer_start_time':1.0,'producer_nonce':'host-task',
            '저장 위치':str(path.parent),'저장한 이름':path.name,'다시 읽은 내용':'body'}
        plan=CommitReconciliation(self.event,expected);self.reserve()
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        saved=self.proof(SavedFileStateEvidence('r',str(path),digest))
        with DurableDispatchDriver(self.driver,self.directory) as guard:
            with self.assertRaisesRegex(JournalReconciliationError,'causal_commit_unverified'):
                reconcile_journal(guard,[plan],lambda *args:saved)
        # Identity/callback here is synthetic; only the file read is actual.
        producer=self.proof(ProducerArtifactEvidence('r',str(path),digest,'owned-producer',4321,1.0,'host-task',1))
        with DurableDispatchDriver(self.driver,self.directory) as guard:
            self.assertEqual(1,reconcile_journal(guard,[plan],lambda *args:producer))

    def test_worker_exit_then_actual_receiver_reconciliation_sends_only_new_request(self):
        received=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                row=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                received.append(row);self.send_response(200);self.end_headers()
            def do_GET(self):
                row=next((r for r in received if r['request_id']==self.path.lstrip('/')),None)
                self.send_response(200 if row else 404);self.end_headers()
                self.wfile.write(json.dumps(row).encode())
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        endpoint=f'http://127.0.0.1:{server.server_port}'
        script='''
import os,sys,json
from urllib.request import Request,urlopen
from tacto.dispatch_journal import DurableDispatchDriver
class Driver:
 def act(self,obs,target,action,**kwargs):
  with urlopen(Request(sys.argv[2],data=json.dumps(action).encode()),timeout=3) as reply:assert reply.status==200
  os._exit(17)
with DurableDispatchDriver(Driver(),sys.argv[1]) as guard:
 guard.act('original','owned-server',json.loads(sys.argv[3]))
'''
        proc=subprocess.run(['.venv/bin/python','-c',script,str(self.directory),endpoint,json.dumps(self.event['action'])],timeout=10)
        self.assertEqual(17,proc.returncode);old=(self.directory/'00000.jsonl').read_bytes()
        self.assertEqual(1,len(received))
        def actual_receipt(slot,event):
            with urlopen(endpoint+'/first',timeout=3) as response:row=json.load(response)
            self.assertEqual(event['action']['request_id'],row['request_id'])
            return self.proof(DeliveryEvidence(receipt_id='owned-receiver-first',recipient='owned-recipient',account='owned-account',
                body_sha256=hashlib.sha256(row['body'].encode()).hexdigest()))
        class NextDriver:
            def act(self,obs,target,action,**kwargs):
                with urlopen(Request(endpoint,data=json.dumps(action).encode()),timeout=3) as response:
                    return {'type':'acted'} if response.status==200 else {'type':'error'}
        with DurableDispatchDriver(NextDriver(),self.directory) as guard:
            reconcile_journal(guard,[self.plan],actual_receipt)
            guard.act('fresh','owned-server',{'name':'send','request_id':'second','body':'two'})
        self.assertEqual(['first','second'],[r['request_id'] for r in received])
        self.assertEqual(['one','two'],[r['body'] for r in received])
        self.assertEqual(old,(self.directory/'00000.jsonl').read_bytes())
        self.assertEqual(2,len(list(self.directory.iterdir())))
