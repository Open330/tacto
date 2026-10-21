"""A background benchmark must not quietly operate the user's foreground app."""
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from examples.probes import signed_finder_preparation as probe
try:
    from tests._artifacts import requires_signed_build
except ImportError:  # discovered with tests/ as the top level
    from _artifacts import requires_signed_build

READY={'ready':True,'reason':'background_target'}
BLOCKED={'ready':False,'reason':'target_already_foreground'}


class FinderBackgroundTests(unittest.TestCase):
    def test_stable_foreground_identity_is_required_and_no_app_is_activated(self):
        def app(bundle,pid=1):
            return SimpleNamespace(bundleIdentifier=lambda:bundle,
                processIdentifier=lambda:pid,isTerminated=lambda:False)
        for front,owned,expected in (([1,1],app('com.apple.finder'),BLOCKED),
                ([1,1],app('other'),READY),([1,2],app('other'),{'ready':False,'reason':'foreground_changed'}),
                ([1],app(None),{'ready':False,'reason':'foreground_unavailable'}),
                ([1],app('other',2),{'ready':False,'reason':'foreground_unavailable'}),
                ([None],None,{'ready':False,'reason':'foreground_unavailable'})):
            values=iter(front)
            self.assertEqual(probe.background_precondition(frontmost=lambda:next(values),
                                                           lookup=lambda _:owned),expected)

    def test_denied_stage_sends_no_input_or_diagnostic_and_does_not_claim_attempt(self):
        driver=Mock()
        staged=probe.StageDriver(driver,io.StringIO(),preflight=True,background_check=lambda:BLOCKED)
        staged.record=Mock()
        for action in ({'name':'open_folder','path':'unused'},{'name':'prepare_window','element_index':0}):
            result=staged.act('obs',probe.TARGET,action)
            self.assertEqual(result['effect'],'not_started')
        driver.act.assert_not_called();driver._send.assert_not_called()
        self.assertFalse(staged.open_folder_attempted)

    def test_foreground_change_during_preflight_stops_before_prepare(self):
        checks=iter([READY,BLOCKED])
        driver=Mock(capabilities={'diagnostic.readiness_preflight'})
        driver._send.return_value={'type':'readiness_preflight','read_only':True,'authorizes_input':False}
        staged=probe.StageDriver(driver,io.StringIO(),preflight=True,background_check=lambda:next(checks))
        staged.record=Mock()
        result=staged.act('obs',probe.TARGET,{'name':'prepare_window','element_index':0})
        self.assertEqual(result['error'],'background_precondition_failed')
        driver._send.assert_called_once();driver.act.assert_not_called()

    @requires_signed_build

    def test_initial_denial_is_not_a_failed_task_and_creates_no_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'report.json';driver=Mock()
            with patch.object(probe,'background_precondition',return_value=BLOCKED), \
                 patch.object(probe.tempfile,'mkdtemp') as create, \
                 patch('tacto.input_audit_process.ProcessInputAudit') as audit:
                report=probe.main(driver,path)
            self.assertEqual(report['precondition_status'],'not_run')
            self.assertIsNone(report['task_result_ok']);self.assertFalse(report['passed'])
            self.assertFalse(report['open_folder_attempted']);self.assertTrue(report['source_subset_stable'])
            create.assert_not_called();audit.assert_not_called();driver.act.assert_not_called()
            self.assertEqual(0o600,path.stat().st_mode&0o777)
            self.assertEqual(json.loads(path.read_text())['precondition_status'],'not_run')

    @requires_signed_build

    def test_task_outcome_remains_separate_from_changed_input_audit(self):
        audit=Mock();audit.__enter__=Mock(return_value=audit);audit.__exit__=Mock(return_value=False)
        audit.report.return_value={'status':'CHANGED'}
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)/'owned';folder.mkdir();path=Path(directory)/'report.json'
            with patch.object(probe,'background_precondition',return_value=READY), \
                 patch.object(probe.tempfile,'mkdtemp',return_value=str(folder)), \
                 patch('tacto.agent.Agent') as agent, \
                 patch('tacto.input_audit_process.ProcessInputAudit',return_value=audit), \
                 patch('tacto.flow.replay',return_value=SimpleNamespace(ok=True,events=[],preparation_diagnostics=[])):
                agent.return_value.authorize.return_value=({},[])
                report=probe.main(Mock(),path)
            self.assertTrue(report['task_result_ok'])
            self.assertFalse(report['passed'])
