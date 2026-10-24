"""Native/model evaluation failures must not publish private report bodies."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]


def load():
    spec=importlib.util.spec_from_file_location('navigation_eval',ROOT/'examples/eval.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


class NavigationEvaluationSummaryTests(unittest.TestCase):
    def test_real_scenario_retains_failure_code_without_report_contents(self):
        module=load()
        report={'passed':False,'error_class':'AssertionError','error':'PRIVATE_ERROR',
                'final':{'field':'PRIVATE_UI_STATE'},'source_stable_during_run':True,
                'owned_child_reaped':True,'input_audit':{'status':'INCONCLUSIVE','raw_report':'PRIVATE_AUDIT'},
                'general':{'provider_calls':[{'error':{'code':'organization_access_disabled',
                                                      'message':'PRIVATE_PROVIDER'}}]}}
        with tempfile.TemporaryDirectory() as directory:
            module.__file__=str(Path(directory)/'examples/eval.py')
            main=Mock(return_value=report)
            with patch('runpy.run_path',return_value={'main':main}), \
                    patch('tacto.helper.InheritedHelperDriver') as driver, \
                    contextlib.redirect_stdout(io.StringIO()) as stdout,self.assertRaises(AssertionError) as failure:
                driver.available.return_value=True
                module.scenario_navigation_model_claude()
            summary=json.loads(str(failure.exception))
            self.assertEqual(summary['provider_error_codes'],['organization_access_disabled'])
            self.assertTrue(summary['source_subset_stable'])
            self.assertNotIn('PRIVATE',stdout.getvalue()+str(failure.exception))
            output=main.call_args.args[1]
            from datetime import date
            self.assertEqual(output.parent.name,date.today().isoformat())

    def test_malformed_and_unknown_metadata_are_not_coerced(self):
        module=load()
        summary=module._navigation_failure_summary({'passed':1,'source_stable_during_run':'yes',
            'owned_child_reaped':None,'error_class':'PRIVATE BODY WITH SPACES',
            'input_audit':{'status':[]},'general':{'provider_calls':[None,{'error':{'code':[]}},
                {'error':{'code':'PRIVATE_CODE','message':'PRIVATE'}}]}})
        self.assertIsNone(summary['passed']);self.assertIsNone(summary['source_subset_stable'])
        self.assertIsNone(summary['error_class']);self.assertEqual(summary['input_audit_status'],'unknown')
        self.assertEqual(summary['provider_error_codes'],[])
        self.assertNotIn('PRIVATE',json.dumps(summary))


class NativeRecoverySummaryTests(unittest.TestCase):
    def test_completed_task_with_changed_input_stays_failed_and_explains_both(self):
        module=load()
        report={'passed':False,'error_class':None,'task_oracle_passed':True,
                'parent_task_oracle_passed':True,'multi_app_saved_state_verified':True,
                'source_subset_stable':True,'owned_child_reaped':True,
                'input_audit':{'status':'CHANGED','raw_report':'PRIVATE_FOCUS'},
                'final':{'text':'PRIVATE_DOCUMENT'}}
        with tempfile.TemporaryDirectory() as directory:
            module.__file__=str(Path(directory)/'examples/eval.py')
            with patch('runpy.run_path',return_value={'main':Mock(return_value=report)}), \
                 patch('tacto.helper.InheritedHelperDriver') as driver, \
                 contextlib.redirect_stdout(io.StringIO()) as stdout, \
                 self.assertRaises(AssertionError) as failure:
                driver.available.return_value=True
                module.scenario_signed_native_recovery_multi_app()
            summary=json.loads(str(failure.exception))
            self.assertFalse(summary['passed'])
            self.assertTrue(summary['task_oracle_passed'])
            self.assertTrue(summary['multi_app_saved_state_verified'])
            self.assertEqual('CHANGED',summary['input_audit_status'])
            self.assertTrue(summary['source_subset_stable'])
            self.assertNotIn('PRIVATE',stdout.getvalue()+str(failure.exception))

    def test_uncertain_metadata_does_not_become_completion_or_leak_messages(self):
        module=load()
        summary=module._native_recovery_failure_summary({'passed':1,'task_oracle_passed':'yes',
            'source_subset_stable':[],'error_class':'PRIVATE ERROR MESSAGE',
            'input_audit':{'status':'PRIVATE_UNKNOWN'},'final':{'text':'PRIVATE'}})
        self.assertIsNone(summary['passed'])
        self.assertIsNone(summary['task_oracle_passed'])
        self.assertIsNone(summary['source_subset_stable'])
        self.assertIsNone(summary['error_class'])
        self.assertEqual('unknown',summary['input_audit_status'])
        self.assertNotIn('PRIVATE',json.dumps(summary))
