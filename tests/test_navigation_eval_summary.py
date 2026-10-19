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
