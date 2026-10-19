"""Native recovery evaluation remains explicit, isolated, and private."""
import contextlib
import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import Mock, patch


class SignedRecoveryEvaluationTests(unittest.TestCase):
    def evaluator(self):
        spec = importlib.util.spec_from_file_location('owned_recovery_eval',Path(__file__).resolve().parents[1]/'examples/eval.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_native_recovery_requires_explicit_isolated_gui_run(self):
        module = self.evaluator()
        _, selected, cases = module._parse_plan(['signed_native_recovery','--offline'])
        self.assertEqual(len(selected),1)
        self.assertTrue(cases[0]['isolated'])
        self.assertEqual(set(cases[0]['requirements']),{'gui','native'})
        self.assertEqual(cases[0]['status'],'skipped')
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            module._parse_plan(['signed_native_recovery','signed_element_click'])

    def test_repeated_and_budget_stop_are_explicit_isolated_offline_skips(self):
        module=self.evaluator()
        for name,kwargs in (
            ('signed_native_recovery_repeated',{'repeated':True}),
            ('signed_native_recovery_budget_stop',{'repeated':True,'exhaust_budget':True}),
            ('signed_native_recovery_multi_app',{'repeated':True,'interleave_textedit':True}),
            ('signed_native_recovery_multi_app_disconnect',{'repeated':True,'interleave_textedit':True,'interleave_disconnect':True}),
            ('signed_native_recovery_multi_app_parent_change',{'repeated':True,'interleave_textedit':True,'interleave_parent_change':True})):
            with self.subTest(name=name),patch.object(module,'scenario_signed_native_recovery') as probe:
                _,selected,cases=module._parse_plan([name,'--offline'])
                probe.assert_not_called()
                self.assertEqual('skipped',cases[0]['status'])
                self.assertTrue(cases[0]['isolated'])
                self.assertEqual({'gui','native'},set(cases[0]['requirements']))
                selected[0][1]()
                probe.assert_called_once_with(**kwargs)

    def test_base_wrapper_passes_scope_and_rejects_misreported_scope(self):
        module=self.evaluator()
        for repeated,exhaust_budget in ((False,False),(True,False),(True,True)):
            report={'passed':True,'stages':24 if repeated else 12,
                'recovery_budget':1 if exhaust_budget or not repeated else 3,
                'expected_budget_exhaustion':exhaust_budget}
            main=Mock(return_value=report)
            with patch('runpy.run_path',return_value={'main':main}), \
                    patch('tacto.helper.InheritedHelperDriver') as driver,contextlib.redirect_stdout(io.StringIO()):
                driver.available.return_value=True
                module.scenario_signed_native_recovery(repeated=repeated,exhaust_budget=exhaust_budget)
                self.assertEqual({'repeated':repeated,'exhaust_budget':exhaust_budget},main.call_args.kwargs)
                report['stages']=99
                with self.assertRaisesRegex(AssertionError,'stage scope mismatch'):
                    module.scenario_signed_native_recovery(repeated=repeated,exhaust_budget=exhaust_budget)

    def test_failure_summary_never_echoes_foreground_audit(self):
        module = self.evaluator()
        report={'passed':False,'input_audit':{'raw_report':'PRIVATE_INPUT_SENTINEL'}}
        with patch('runpy.run_path',return_value={'main':Mock(return_value=report)}), \
                patch('tacto.helper.InheritedHelperDriver') as driver, \
                contextlib.redirect_stdout(io.StringIO()) as out, self.assertRaises(AssertionError) as failure:
            driver.available.return_value=True
            module.scenario_signed_native_recovery()
        self.assertNotIn('PRIVATE_INPUT_SENTINEL',str(failure.exception))
        self.assertNotIn('PRIVATE_INPUT_SENTINEL',out.getvalue())

    def test_multi_app_wrapper_requires_all_saved_boundaries(self):
        module=self.evaluator()
        report={'passed':True,'stages':24,'recovery_budget':3,
            'expected_budget_exhaustion':False,'interleave_textedit_requested':True,
            'multi_app_saved_state_verified':True,
            'interleaved_textedit':[{'parent_stage':n,'phase':'completed'} for n in (8,14,20)]}
        main=Mock(return_value=report)
        with patch('runpy.run_path',return_value={'main':main}), \
                patch('tacto.helper.InheritedHelperDriver') as driver,contextlib.redirect_stdout(io.StringIO()):
            driver.available.return_value=True
            module.scenario_signed_native_recovery_multi_app()
            self.assertEqual({'repeated':True,'exhaust_budget':False,'interleave_textedit':True},main.call_args.kwargs)
            report['interleaved_textedit'][1]['phase']='failed'
            with self.assertRaisesRegex(AssertionError,'interleave incomplete'):
                module.scenario_signed_native_recovery_multi_app()

    def test_disconnect_wrapper_requires_real_channel_and_incomplete_task(self):
        module=self.evaluator()
        report={'passed':True,'stages':24,'recovery_budget':3,'expected_budget_exhaustion':False,
            'interleave_textedit_requested':True,'expected_interleave_disconnect':True,
            'helper_channel_shutdown':True,'helper_channel_poisoned':True,
            'expected_stop_verified':True,'task_oracle_passed':False,
            'interleaved_textedit':[{'parent_stage':8,'phase':'failed'}]}
        main=Mock(return_value=report)
        with patch('runpy.run_path',return_value={'main':main}), \
                patch('tacto.helper.InheritedHelperDriver') as driver,contextlib.redirect_stdout(io.StringIO()):
            driver.available.return_value=True
            module.scenario_signed_native_recovery_multi_app_disconnect()
            self.assertEqual({'repeated':True,'exhaust_budget':False,'interleave_textedit':True,
                'interleave_disconnect':True},main.call_args.kwargs)
            report['task_oracle_passed']=True
            with self.assertRaisesRegex(AssertionError,'disconnect stop unverified'):
                module.scenario_signed_native_recovery_multi_app_disconnect()
            report['task_oracle_passed']=False;report['helper_channel_shutdown']=False
            with self.assertRaisesRegex(AssertionError,'disconnect channel unverified'):
                module.scenario_signed_native_recovery_multi_app_disconnect()

    def test_parent_change_wrapper_requires_actual_injection_and_incomplete_task(self):
        module=self.evaluator()
        report={'passed':True,'stages':24,'recovery_budget':3,'expected_budget_exhaustion':False,
            'interleave_textedit_requested':True,'expected_interleave_parent_change':True,
            'parent_change_injected':True,'expected_stop_verified':True,'task_oracle_passed':False,
            'interleaved_textedit':[{'parent_stage':8,'phase':'failed'}]}
        main=Mock(return_value=report)
        with patch('runpy.run_path',return_value={'main':main}),patch('tacto.helper.InheritedHelperDriver') as driver,contextlib.redirect_stdout(io.StringIO()):
            driver.available.return_value=True
            module.scenario_signed_native_recovery_multi_app_parent_change()
            self.assertTrue(main.call_args.kwargs['interleave_parent_change'])
            report['parent_change_injected']=False
            with self.assertRaisesRegex(AssertionError,'parent change scope unverified'):
                module.scenario_signed_native_recovery_multi_app_parent_change()
            report['parent_change_injected']=True;report['task_oracle_passed']=True
            with self.assertRaisesRegex(AssertionError,'parent change stop unverified'):
                module.scenario_signed_native_recovery_multi_app_parent_change()


if __name__=='__main__': unittest.main()
