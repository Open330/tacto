"""Worker protocol/boundary checks need no real GUI, model, or TCC grants."""
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'examples' / 'probes'))
from comparison_fixture_worker import FixtureWorker, serve
spec = importlib.util.spec_from_file_location('comparison_worker_test_host', ROOT / 'examples/product_comparison.py')
comparison = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = comparison
spec.loader.exec_module(comparison)


class ProtocolTests(unittest.TestCase):
    def test_closed_protocol_rejects_arbitrary_paths_tokens_and_replays(self):
        for request in ({'id':1,'op':'write'}, {'id':1,'op':'oracle','token':'private'},
                        {'id':True,'op':'cleanup'}, {'id':2,'op':'cleanup'},
                        {'id':1,'op':[]}, {'id':1,'op':'setup','path':'/user/doc'}):
            host = Mock()
            out = io.StringIO()
            self.assertEqual(2, serve(host, io.StringIO(json.dumps(request)+'\n'), out))
            self.assertEqual('invalid_request', json.loads(out.getvalue())['error'])
            self.assertEqual([], host.mock_calls)

    def test_eof_does_not_authorize_cleanup(self):
        host = Mock()
        self.assertEqual(0, serve(host, io.StringIO(), io.StringIO()))
        host.signed_owned_cleanup.assert_not_called()

    def test_operation_error_is_sanitized_and_followup_evidence_still_available(self):
        host = Mock(_SIGNED_OWNED={'fixture': object()})
        host.signed_owned_setup.side_effect = RuntimeError('PRIVATE TOKEN')
        host.signed_fixture_evidence.return_value = {'path':'owned'}
        out = io.StringIO()
        self.assertEqual(0, serve(host, io.StringIO(
            '{"id":1,"op":"setup"}\n{"id":2,"op":"evidence"}\n{"id":3,"op":"shutdown"}\n'), out))
        replies = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertNotIn('PRIVATE', out.getvalue())
        self.assertEqual({'path':'owned'}, replies[1]['value'])
        self.assertTrue(replies[2]['value']['fixture_left_as_is'])
        host.signed_owned_cleanup.assert_not_called()

    def test_oversized_frame_stops_without_dispatch(self):
        host, out = Mock(), io.StringIO()
        self.assertEqual(2, serve(host, io.StringIO('x'*65537+'\n'), out))
        self.assertEqual([], host.mock_calls)

    def test_real_pipe_wrong_sequence_poisons_channel_and_child_is_reaped(self):
        with tempfile.TemporaryDirectory() as directory:
            launcher = Path(directory)/'fake-worker'
            launcher.write_text('#!'+sys.executable+'\nimport sys,json\nfor line in sys.stdin:\n print(json.dumps({"id":99,"ok":True,"value":None}),flush=True)\n')
            launcher.chmod(0o700)
            worker = FixtureWorker(str(launcher), timeout=2)
            try:
                with self.assertRaisesRegex(RuntimeError, 'transport_unverified'):
                    worker.request('check')
                with self.assertRaisesRegex(RuntimeError, 'channel_unavailable'):
                    worker.request('cleanup')
            finally:
                worker.close()
            self.assertIsNotNone(worker.process.returncode)

    def test_external_signed_controller_is_rejected_before_worker_launch(self):
        with patch('tacto.helper.InheritedHelperDriver.available', return_value=True), \
             patch('comparison_fixture_worker.FixtureWorker') as worker, \
             self.assertRaises(SystemExit) as error:
            comparison.main(['--external-owned-worker','/fake','--only','textedit_edit_owned'])
        self.assertEqual(2,error.exception.code)
        worker.assert_not_called()

    def test_worker_adapter_does_not_export_oracle_text_or_handle(self):
        worker = Mock()
        worker.request.return_value = [True,'exact']
        with patch.object(comparison,'_FIXTURE_WORKER',worker):
            self.assertEqual((True,'exact'),comparison.signed_owned_oracle(comparison.TEXT))
            with self.assertRaisesRegex(RuntimeError,'unsupported_worker_oracle'):
                comparison.signed_owned_oracle('arbitrary')
        worker.request.assert_called_once_with('oracle')

    def test_unsigned_worker_stops_before_reading_rpc(self):
        with patch('tacto.helper.InheritedHelperDriver.available', return_value=False), \
             patch('comparison_fixture_worker.serve') as server, \
             patch('sys.stderr', io.StringIO()):
            self.assertEqual(2,comparison.main(['--fixture-worker']))
        server.assert_not_called()

    def test_setup_failure_retains_partial_artifact_without_repeating_setup(self):
        worker = Mock(poisoned=False)
        worker.request.side_effect = [{'path':'owned-partial'}, RuntimeError('failed')]
        with patch.object(comparison,'_FIXTURE_WORKER',worker), \
             patch.object(comparison,'_WORKER_EVIDENCE',None):
            with self.assertRaises(RuntimeError):
                comparison.signed_owned_setup()
            self.assertEqual({'path':'owned-partial'},comparison.signed_fixture_evidence())
        self.assertEqual(['prepare','setup'],[c.args[0] for c in worker.request.call_args_list])

    def test_poisoned_setup_keeps_prepared_identity_without_more_rpc(self):
        worker=Mock(poisoned=False)
        prepared={'path':'/owned/doc.txt','directory_identity':[10,20],'setup_started':False}
        def request(op):
            if op=='prepare':return prepared
            worker.poisoned=True
            raise RuntimeError('fixture_worker_channel_unavailable')
        worker.request.side_effect=request
        with patch.object(comparison,'_FIXTURE_WORKER',worker), \
             patch.object(comparison,'_WORKER_EVIDENCE',None):
            with self.assertRaises(RuntimeError):comparison.signed_owned_setup()
            self.assertEqual(prepared,comparison.signed_fixture_evidence())
        self.assertEqual(['prepare','setup'],[c.args[0] for c in worker.request.call_args_list])

    def test_setup_failure_row_preserves_identity_and_never_starts_agent(self):
        worker=Mock(poisoned=True)
        prepared={'path':'/owned/doc.txt','directory_identity':[10,20]}
        worker.request.side_effect=[prepared,RuntimeError('fixture_worker_channel_unavailable')]
        task=next(t for t in comparison.TASKS if t.name=='textedit_edit_owned')
        with patch.object(comparison,'_FIXTURE_WORKER',worker), \
             patch.object(comparison,'_WORKER_EVIDENCE',None), \
             patch.object(comparison,'_locked',return_value=False), \
             patch.object(comparison,'_agent_executable_available',return_value=True), \
             patch.object(comparison,'_run_agent') as agent:
            row=comparison.run_one(task,'nlc')
        self.assertEqual(prepared,row['owned_fixture_evidence'])
        self.assertFalse(row['comparison_eligible'])
        self.assertEqual('setup_failed_left_as_is',row['cleanup_status'])
        self.assertEqual('unverified_after_failure',row['setup_outcome'])
        self.assertEqual('prepare_reply_before_setup',row['owned_fixture_evidence_scope'])
        self.assertEqual(['prepare','setup'],[c.args[0] for c in worker.request.call_args_list])
        agent.assert_not_called()

    def test_environment_loss_after_setup_keeps_identity_without_cleanup(self):
        worker=Mock(poisoned=False)
        prepared={'path':'/owned/doc.txt','directory_identity':[10,20]}
        worker.request.side_effect=[prepared,{'ask':'owned goal','evidence':prepared}]
        task=next(t for t in comparison.TASKS if t.name=='textedit_edit_owned')
        with patch.object(comparison,'_FIXTURE_WORKER',worker), \
             patch.object(comparison,'_WORKER_EVIDENCE',None), \
             patch.object(comparison,'_locked',side_effect=[False,None]), \
             patch.object(comparison,'_agent_executable_available',return_value=True), \
             patch.object(comparison,'_run_agent') as agent:
            row=comparison.run_one(task,'nlc')
        self.assertEqual(prepared,row['owned_fixture_evidence'])
        self.assertFalse(row['comparison_eligible'])
        self.assertEqual('environment_unavailable_left_as_is',row['cleanup_status'])
        self.assertEqual(['prepare','setup'],[c.args[0] for c in worker.request.call_args_list])
        agent.assert_not_called()

    def test_real_worker_pipes_remove_helper_markers_and_shutdown_reaps(self):
        with tempfile.TemporaryDirectory() as directory:
            launcher=Path(directory)/'fake-worker'
            launcher.write_text('#!'+sys.executable+'\nimport sys,json,os\n'
                'for line in sys.stdin:\n'
                ' r=json.loads(line)\n'
                ' print(json.dumps({"id":r["id"],"ok":True,"value":{k:os.environ.get(k) for k in ("TACTO_HELPER_FD","TACTO_LAUNCHER","TACTO_HELPER_TOKEN")}}),flush=True)\n'
                ' if r["op"]=="shutdown": break\n')
            launcher.chmod(0o700)
            with patch.dict(os.environ,{'TACTO_HELPER_FD':'999','TACTO_LAUNCHER':'1','TACTO_HELPER_TOKEN':'secret'}):
                worker=FixtureWorker(str(launcher),timeout=2)
                try:
                    self.assertTrue(all(v is None for v in worker.request('check').values()))
                    self.assertFalse(os.get_inheritable(worker.process.stdout.fileno()))
                    self.assertFalse(os.get_inheritable(worker.process.stdin.fileno()))
                    self.assertEqual('secret',os.environ['TACTO_HELPER_TOKEN'])
                finally:
                    worker.close()
            self.assertEqual(0,worker.process.returncode)


class LifecycleTests(unittest.TestCase):
    def witness(self):
        witness=Mock()
        witness.__enter__=Mock(return_value=witness)
        witness.__exit__=Mock(return_value=False)
        witness.summary.return_value={'activations':[],'initial':'owned-test'}
        return witness

    def test_unknown_session_never_creates_fixture(self):
        from comparison_fixture_worker import probe_lifecycle
        worker=Mock()
        result=probe_lifecycle(worker,lambda:None,self.witness)
        self.assertEqual('not_run',result['status'])
        worker.request.assert_not_called()

    def test_setup_failure_preserves_evidence_and_requests_only_owned_cleanup_once(self):
        from comparison_fixture_worker import probe_lifecycle
        worker=Mock(poisoned=False)
        worker.request.side_effect=[RuntimeError('PRIVATE'),{'path':'partial'},
            {'status':'document_status_unavailable_left_as_is','evidence':{'path':'partial'}}]
        result=probe_lifecycle(worker,lambda:False,self.witness)
        self.assertEqual('failed',result['status'])
        self.assertEqual('partial',result['fixture_evidence_before_cleanup']['path'])
        self.assertNotIn('PRIVATE',str(result))
        self.assertEqual(['setup','evidence','cleanup'],[c.args[0] for c in worker.request.call_args_list])

    def test_environment_loss_after_setup_never_requests_cleanup(self):
        from comparison_fixture_worker import probe_lifecycle
        worker=Mock(poisoned=False)
        worker.request.side_effect=[{'evidence':{'initial_empty_text_verified':True}},
            {'observable':True,'exact_empty':True},{'path':'owned'}]
        locks=iter([False,None])
        result=probe_lifecycle(worker,lambda:next(locks),self.witness)
        self.assertEqual('failed',result['status'])
        self.assertEqual('session_unavailable_left_as_is',result['cleanup_status'])
        self.assertEqual(['setup','empty_oracle','evidence'],[c.args[0] for c in worker.request.call_args_list])

    def test_empty_oracle_and_actual_close_removal_needed_for_pass(self):
        from comparison_fixture_worker import probe_lifecycle
        for removed,activations in [(True,[]),(False,[]),(True,['other-app'])]:
            worker=Mock(poisoned=False)
            worker.request.side_effect=[{'evidence':{'initial_empty_text_verified':True}},
                {'observable':True,'exact_empty':True},{'path':'owned'},
                {'status':'closed','evidence':{'closed_observed':True,'artifact_removed':removed}}]
            witness=self.witness();witness.summary.return_value={'activations':activations}
            result=probe_lifecycle(worker,lambda:False,lambda:witness)
            self.assertEqual('passed' if removed and not activations else 'failed',result['status'])
            self.assertFalse(result['comparison_eligible'])
            self.assertFalse(result['agent_executed'])

    def test_lost_cleanup_reply_keeps_effect_unknown(self):
        from comparison_fixture_worker import probe_lifecycle
        worker=Mock(poisoned=False)
        def request(op):
            if op=='setup':return {'evidence':{'initial_empty_text_verified':True}}
            if op=='empty_oracle':return {'observable':True,'exact_empty':True}
            if op=='evidence':return {'path':'owned'}
            worker.poisoned=True
            raise RuntimeError('unknown')
        worker.request.side_effect=request
        result=probe_lifecycle(worker,lambda:False,self.witness)
        self.assertEqual('cleanup_effect_unknown',result['cleanup_status'])
        self.assertEqual('failed',result['status'])
        self.assertEqual(1,[c.args[0] for c in worker.request.call_args_list].count('cleanup'))


class AgentTraceTests(unittest.TestCase):
    def test_raw_streams_are_separate_private_files_with_matching_hashes(self):
        import hashlib,stat
        with tempfile.TemporaryDirectory() as directory, patch.object(comparison,'ROOT',Path(directory)):
            report=comparison._preserve_agent_trace('{"type":"item.completed"}\n','transport diagnostic')
            for name,expected in [('stdout','{"type":"item.completed"}\n'),('stderr','transport diagnostic')]:
                entry=report['streams'][name];path=Path(entry['path'])
                self.assertEqual(expected,path.read_text())
                self.assertEqual(0o600,stat.S_IMODE(path.stat().st_mode))
                self.assertEqual(hashlib.sha256(expected.encode()).hexdigest(),entry['sha256'])


class ChainTaskTests(unittest.TestCase):
    def test_unknown_task_cannot_spawn_worker(self):
        with patch('comparison_fixture_worker.subprocess.Popen') as spawn, self.assertRaises(ValueError):
            FixtureWorker('/fake',task='arbitrary-command')
        spawn.assert_not_called()

    def test_chain_server_uses_only_fixed_setup_ask_and_both_leg_oracle(self):
        host=Mock()
        host.signed_calc_then_owned_setup.return_value={'calculator_initial_display':'0'}
        host.signed_chain_ask.return_value='fixed owned task'
        host.signed_fixture_evidence.return_value={'path':'owned'}
        host.signed_chain_oracle.return_value=(False,'document matches; calculator differs')
        out=io.StringIO()
        serve(host,io.StringIO('{"id":1,"op":"setup"}\n{"id":2,"op":"oracle"}\n'),out,task='calc_to_textedit')
        responses=[json.loads(l) for l in out.getvalue().splitlines()]
        self.assertEqual('0',responses[0]['value']['setup_evidence']['calculator_initial_display'])
        self.assertFalse(responses[1]['value'][0])
        host.signed_chain_oracle.assert_called_once_with('437')
        host.signed_owned_setup.assert_not_called()
        host.signed_owned_oracle.assert_not_called()

    def test_chain_oracle_needs_both_actual_receiving_states(self):
        for document,calculated in [(True,'437'),(False,'437'),(True,'438'),(False,'438')]:
            with patch.object(comparison,'_FIXTURE_WORKER',None), \
                 patch.object(comparison,'signed_owned_oracle',return_value=(document,'owned')), \
                 patch.object(comparison,'_calc_display',return_value=calculated):
                passed,_=comparison.signed_chain_oracle('437')
            self.assertEqual(document and calculated=='437',passed)

    def test_external_controller_forwards_chain_to_worker_and_fresh_runner(self):
        result={'status':'passed','usage':None,'seconds':0,'activations':[],
                'observed':'fixture-only','comparison_eligible':False}
        worker=Mock();worker.request.return_value={'task':'calc_to_textedit'}
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(comparison,'ROOT',Path(directory)), \
             patch('tacto.helper.InheritedHelperDriver.available',return_value=False), \
             patch.dict(os.environ,{'TACTO_HELPER_FD':'','TACTO_LAUNCHER':'','TACTO_HELPER_TOKEN':''}), \
             patch('comparison_fixture_worker.FixtureWorker',return_value=worker) as factory, \
             patch.object(comparison,'_locked',return_value=False), \
             patch.object(comparison,'run_one',return_value=result) as run, \
             patch('sys.stdout',io.StringIO()):
            code=comparison.main(['--external-owned-worker',sys.executable,'--only','calc_to_textedit','--arms','nlc'])
        self.assertEqual(2,code)
        self.assertEqual('calc_to_textedit',factory.call_args.kwargs['task'])
        self.assertEqual('calc_to_textedit',run.call_args.args[0].name)
        worker.close.assert_called_once()


class SkyGuideTests(unittest.TestCase):
    def test_host_guide_is_only_added_to_sky_and_constraints_stay_present(self):
        with patch.object(comparison,'_SKY_SKILL_FILE','/host/public skill.md'):
            sky=comparison._arm_prompt('owned task','sky')
            nlc=comparison._arm_prompt('owned task','nlc')
        self.assertIn('"/host/public skill.md"',sky)
        self.assertIn('do not guess arguments',sky)
        self.assertNotIn('/host/public skill.md',nlc)
        self.assertIn(comparison.BACKGROUND_CONSTRAINT,sky)
        self.assertIn(comparison.BACKGROUND_CONSTRAINT,nlc)

    def test_external_skill_snapshot_detects_change_loss_and_oversize(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'SKILL.md';path.write_text('public contract')
            with patch.object(comparison,'_SKY_SKILL_FILE',str(path)):
                before=comparison._sky_skill_evidence()
                path.write_text('new contract')
                self.assertNotEqual(before['sha256'],comparison._sky_skill_evidence()['sha256'])
                path.write_bytes(b'x'*65537)
                self.assertEqual('guide_unavailable',comparison._sky_skill_evidence()['error'])
                path.unlink()
                self.assertEqual('guide_unavailable',comparison._sky_skill_evidence()['error'])

    def test_changed_guide_invalidates_even_a_passing_task_oracle(self):
        from tests.test_product_comparison_provenance import Provenance,comparison as host
        fixture=Provenance();fixture.setUp()
        try:
            path=fixture.root/'SKILL.md';path.write_text('first public contract')
            with patch.object(host,'_SKY_SKILL_FILE',str(path)):
                result=fixture.execute(arm='sky',setup=lambda:path.write_text('changed contract'))
            self.assertTrue(result['oracle_passed'])
            self.assertEqual('invalid',result['status'])
            self.assertEqual('sky_skill_unavailable_or_changed',result['invalid_reason'])
            self.assertFalse(result['sky_skill_evidence']['stable'])
            self.assertFalse(result['comparison_eligible'])
        finally:fixture.doCleanups()
