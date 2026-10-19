import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from examples.probes import native_snapshot_planning as probe
from tests.test_candidate_context_paging import screen


class SnapshotPlanningTests(unittest.TestCase):
    def fixture(self, root):
        path=root/'snapshot.json'
        path.write_text(json.dumps({'evaluation_kind':'real_model_owned_appkit',
            'target':'ai.callabo.nlc.select-fixture','requested_option':'Choice127',
            'plans':[{'candidates':screen()}]}))
        return path

    def test_other_app_refuses_before_output_or_model(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=self.fixture(root);data=json.loads(path.read_text())
            data['target']='other.app';path.write_text(json.dumps(data))
            with patch('tacto.ask.ask') as model,self.assertRaises(ValueError):
                probe.run(path,0,'codex',root/'output.json')
            model.assert_not_called();self.assertFalse((root/'output.json').exists())

    def test_model_failure_preserves_usage_and_never_claims_ui_completion(self):
        from tacto.provider_observation import record_provider_result
        from tacto.provider_result import ProviderResult
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=self.fixture(root);out=root/'output.json'
            def fail(question, **kwargs):
                kwargs['_request_guard'](question.prompt,0)
                record_provider_result(ProviderResult('owned','codex','codex','cli-json',
                    'ok','PRIVATE_RESPONSE',None,.1,total_input_tokens=42,output_tokens=3))
                raise RuntimeError('PRIVATE_EXCEPTION')
            with patch('tacto.ask.ask',side_effect=fail):
                result=probe.run(path,0,'codex',out)
            self.assertEqual('RuntimeError',result['error_class'])
            self.assertEqual(1,len(result['provider_calls']))
            self.assertEqual(42,result['usage']['tokens']['total_input_tokens']['observed_sum'])
            self.assertEqual(0,result['host_ui_actions_executed'])
            self.assertFalse(result['provider_side_effects_verified'])
            self.assertFalse(result['task_completion_verified'])
            self.assertFalse(result['comparison_eligible'])
            self.assertNotIn('PRIVATE_',out.read_text())
            self.assertEqual(0o600,out.stat().st_mode&0o777)
