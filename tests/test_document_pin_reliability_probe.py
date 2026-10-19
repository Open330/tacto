"""Owned probe failure boundaries; no native GUI or model invoked."""
import sys
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock,MagicMock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'examples/probes'))
import signed_document_pin_reliability as probe
from tacto.pin_diagnostic import format_pin_diagnostic


class PinReliabilityProbeTests(unittest.TestCase):
    def worker(self,cleanup='closed'):
        worker=Mock(poisoned=False)
        worker.process.returncode=0
        def request(op):
            if op=='prepare':return {'path':'/owned/doc.txt','directory_identity':[1,2]}
            if op=='setup':return {'evidence':{'path':'/owned/doc.txt'}}
            if op=='empty_oracle':return {'observable':True,'exact_empty':True}
            if op=='cleanup':return {'status':cleanup,'evidence':{'artifact_removed':cleanup=='closed'}}
            raise AssertionError(op)
        worker.request.side_effect=request
        return worker

    def execute(self,worker,stdout='owned only',exit_code=0,cycles=3):
        witness=MagicMock();witness.__enter__.return_value=witness
        witness.summary.return_value={'activations':[]}
        with patch.object(probe,'_locked',return_value=False), \
             patch.object(probe,'source_manifest',return_value={'files':{},'sha256':'same'}), \
             patch.object(probe,'FocusWitness',return_value=witness), \
             patch.object(probe,'FixtureWorker',return_value=worker) as create, \
             patch.object(probe.subprocess,'run',return_value=subprocess.CompletedProcess([],exit_code,stdout,'')) as read:
            result=probe.run('/signed/fixture',cycles)
        return result,create,read

    def test_ambiguous_cleanup_stops_without_accumulating_new_windows(self):
        result,create,read=self.execute(self.worker('close_pending_left_as_is'))
        self.assertEqual('failed',result['status'])
        self.assertEqual(1,len(result['cycles']))
        self.assertEqual(0,result['completed_cycles'])
        self.assertEqual(1,result['recorded_cycles'])
        self.assertEqual('close_pending_left_as_is',result['cycles'][0]['cleanup_status'])
        self.assertEqual(2,read.call_count);self.assertEqual(1,create.call_count)

    def test_user_abort_stops_reads_and_leaves_fixture_without_gui_cleanup(self):
        worker=self.worker()
        result,create,read=self.execute(worker,stdout=format_pin_diagnostic('user_aborted'),exit_code=1)
        self.assertEqual('failed',result['status'])
        self.assertEqual(1,read.call_count);self.assertEqual(1,create.call_count)
        self.assertEqual(['prepare','setup'],[c.args[0] for c in worker.request.call_args_list])
        self.assertEqual('user_aborted_left_as_is',result['cycles'][0]['cleanup_status'])
        worker.close.assert_called_once()

    def test_close_transport_failure_is_not_silently_passed(self):
        worker=self.worker();worker.close.side_effect=RuntimeError('PRIVATE')
        result,create,read=self.execute(worker)
        self.assertEqual('failed',result['status'])
        self.assertEqual('RuntimeError',result['cycles'][0]['worker_close_error'])
        self.assertNotIn('PRIVATE',str(result));self.assertEqual(1,create.call_count)

    def test_negative_read_without_abort_marker_stops_before_more_gui_calls(self):
        worker=self.worker()
        result,_,read=self.execute(worker,stdout='scoped_observation_unavailable',exit_code=1,cycles=1)
        self.assertEqual('failed',result['status'])
        self.assertEqual(1,result['cycles'][0]['reads'][0]['exit'])
        self.assertEqual('failure_or_interrupt_left_as_is',result['cycles'][0]['cleanup_status'])
        self.assertEqual(1,read.call_count)
        self.assertEqual(['prepare','setup'],[c.args[0] for c in worker.request.call_args_list])

    def test_unknown_initial_session_creates_no_worker(self):
        with patch.object(probe,'_locked',return_value=None),patch.object(probe,'FixtureWorker') as create:
            self.assertEqual('not_run',probe.run('/signed',3)['status'])
        create.assert_not_called()

    def test_keyboard_interrupt_keeps_partial_cycle_and_issues_no_cleanup(self):
        worker=self.worker()
        worker.request.side_effect=KeyboardInterrupt('PRIVATE')
        result,create,read=self.execute(worker)
        self.assertEqual('interrupted',result['status'])
        self.assertEqual(1,result['recorded_cycles'])
        self.assertEqual(0,result['completed_cycles'])
        self.assertEqual('failure_or_interrupt_left_as_is',result['cycles'][0]['cleanup_status'])
        read.assert_not_called();worker.close.assert_called_once()
        self.assertNotIn('PRIVATE',str(result))

    def test_interrupted_report_is_written_before_main_reraises(self):
        import json,tempfile,stat
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'report.json'
            record={'status':'interrupted','cycles':[{'error':'KeyboardInterrupt'}]}
            with patch.object(sys,'argv',['probe','--launcher',sys.executable,'--report',str(path)]), \
                 patch.object(probe,'run',return_value=record),self.assertRaises(KeyboardInterrupt):
                probe.main()
            self.assertEqual(record,json.loads(path.read_text()))
            self.assertEqual(0o600,stat.S_IMODE(path.stat().st_mode))

    def test_read_timeout_retains_partial_output_and_skips_gui_cleanup(self):
        worker=self.worker();witness=MagicMock();witness.__enter__.return_value=witness
        witness.summary.return_value={'activations':[]}
        with patch.object(probe,'_locked',return_value=False), \
             patch.object(probe,'source_manifest',return_value={'files':{},'sha256':'same'}), \
             patch.object(probe,'FocusWitness',return_value=witness), \
             patch.object(probe,'FixtureWorker',return_value=worker), \
             patch.object(probe.subprocess,'run',side_effect=subprocess.TimeoutExpired('owned',35,output=b'owned partial')) as read:
            result=probe.run('/signed',3)
        self.assertEqual('owned partial',result['cycles'][0]['reads'][0]['stdout'])
        self.assertTrue(result['cycles'][0]['reads'][0]['timed_out'])
        self.assertEqual(0,result['completed_cycles'])
        self.assertEqual(['prepare','setup'],[c.args[0] for c in worker.request.call_args_list])
        self.assertEqual(1,read.call_count)

    def test_witness_interrupt_creates_no_worker_and_keeps_interrupted_result(self):
        witness=MagicMock();witness.__enter__.side_effect=KeyboardInterrupt('PRIVATE')
        with patch.object(probe,'_locked',return_value=False), \
             patch.object(probe,'FocusWitness',return_value=witness), \
             patch.object(probe,'FixtureWorker') as create:
            result=probe.run('/signed',3)
        self.assertEqual('interrupted',result['status'])
        self.assertEqual(0,result['completed_cycles'])
        self.assertEqual([],result['cycles'])
        self.assertNotIn('PRIVATE',str(result));create.assert_not_called()

    def test_setup_failure_keeps_prepared_identity_without_further_gui_reads(self):
        worker=self.worker()
        def request(op):
            if op=='prepare':return {'path':'/owned/doc.txt','directory_identity':[10,20]}
            if op=='setup':raise RuntimeError('PRIVATE')
            raise AssertionError('no followup RPC after failure')
        worker.request.side_effect=request
        result,_,read=self.execute(worker)
        self.assertEqual('/owned/doc.txt',result['cycles'][0]['prepared_fixture']['path'])
        self.assertEqual([10,20],result['cycles'][0]['prepared_fixture']['directory_identity'])
        self.assertEqual(['prepare','setup'],[c.args[0] for c in worker.request.call_args_list])
        read.assert_not_called();self.assertEqual(0,result['completed_cycles'])
