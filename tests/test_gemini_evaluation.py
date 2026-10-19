"""Provider selection belongs to a single signed telemetry probe, never task text."""
import contextlib
import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]


class GeminiEvaluationTests(unittest.TestCase):
    def test_receiving_cancel_probe_is_isolated_and_offline_never_bootstraps(self):
        spec=importlib.util.spec_from_file_location('receiving_cancel_eval',ROOT/'examples/eval.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with patch.object(module,'_native_visual_owned') as bootstrap:
            _,selected,cases=module._parse_plan(['native_image_receiving_cancel','--offline'])
        bootstrap.assert_not_called()
        self.assertEqual('skipped',cases[0]['status'])
        self.assertTrue(cases[0]['isolated'])
        self.assertEqual({'gui','native'},set(cases[0]['requirements']))
        with patch.object(module,'_native_visual_owned',return_value=True) as bootstrap:
            self.assertTrue(selected[0][1]())
        bootstrap.assert_called_once_with('fixed',receiving_cancel=True)

    def test_receiving_failure_probe_is_native_isolated_and_never_calls_model_offline(self):
        spec=importlib.util.spec_from_file_location('scroll_failure_eval',ROOT/'examples/eval.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with patch.object(module,'_owned_select_fixture') as bootstrap:
            _,selected,cases=module._parse_plan(['native_select_scroll_receiving_failure_fixture','--offline'])
        bootstrap.assert_not_called()
        self.assertEqual('skipped',cases[0]['status'])
        self.assertTrue(cases[0]['isolated'])
        self.assertEqual({'gui','native'},set(cases[0]['requirements']))
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            module._parse_plan(['native_select_scroll_receiving_failure_fixture','--model-provider','codex'])

    def test_view_replacement_fixture_is_isolated_and_exact_mode_never_bootstraps_invalid_input(self):
        spec=importlib.util.spec_from_file_location('view_replace_eval',ROOT/'examples/eval.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        _,selected,cases=module._parse_plan(['native_select_view_replacement_fixture','--offline'])
        self.assertEqual('skipped',cases[0]['status'])
        self.assertEqual({'gui','native'},set(cases[0]['requirements']))
        self.assertTrue(cases[0]['isolated'])
        with patch.object(module,'scenario_native_select_process_exit_fixture') as probe:
            selected[0][1]()
            probe.assert_called_once_with(replacement=True)
        for value in (1,'true',None):
            with patch.object(module,'Driver') as driver,self.assertRaises(ValueError):
                module._owned_select_fixture(combo=True,replacement=value)
            driver.assert_not_called()
            with patch.object(module,'_owned_select_fixture') as bootstrap,self.assertRaises(ValueError):
                module.scenario_native_select_process_exit_fixture(replacement=value)
            bootstrap.assert_not_called()
        with patch.object(module,'Driver') as driver,self.assertRaises(ValueError):
            module._owned_select_fixture(combo=False,replacement=True)
        driver.assert_not_called()

    def test_process_exit_fixture_is_isolated_and_offline_never_bootstraps(self):
        spec=importlib.util.spec_from_file_location('process_exit_eval',ROOT/'examples/eval.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with patch.object(module,'_owned_select_fixture') as bootstrap:
            _,selected,cases=module._parse_plan(['native_select_process_exit_fixture','--offline'])
        bootstrap.assert_not_called()
        self.assertEqual('skipped',cases[0]['status'])
        self.assertEqual({'gui','native'},set(cases[0]['requirements']))
        self.assertTrue(cases[0]['isolated'])

    def test_combo_model_is_isolated_provider_scoped_and_invalid_mode_never_launches(self):
        spec=importlib.util.spec_from_file_location('combo_model_eval',ROOT/'examples/eval.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        for provider in ('codex','claude'):
            _,selected,cases=module._parse_plan(['native_select_combo_model_fixture','--model-provider',provider,'--offline'])
            self.assertEqual('skipped',cases[0]['status'])
            self.assertEqual({'gui','native','model'},set(cases[0]['requirements']))
            with patch.object(module,'scenario_native_select_model_fixture') as probe:
                selected[0][1]()
                probe.assert_called_once_with(provider,combo=True)
        for provider in ('gemini','opencode'):
            with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
                module._parse_plan(['native_select_combo_model_fixture','--model-provider',provider])
        for arguments in ({'combo':1},{'combo':'true'},{'combo':True,'options':198}):
            with patch.object(module,'Driver') as driver,self.assertRaises(ValueError):
                module.scenario_native_select_model_fixture('codex',**arguments)
            driver.assert_not_called()

    def test_large_combo_keeps_isolation_provider_and_host_selected_fixture(self):
        spec=importlib.util.spec_from_file_location('large_combo_eval',ROOT/'examples/eval.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        for provider in ('codex','claude'):
            with patch.object(module,'_owned_select_fixture') as bootstrap:
                _,selected,cases=module._parse_plan(['native_select_combo_many_model_fixture','--model-provider',provider,'--offline'])
            bootstrap.assert_not_called()
            self.assertEqual('skipped',cases[0]['status'])
            self.assertTrue(cases[0]['isolated'])
            self.assertEqual({'gui','native','model'},set(cases[0]['requirements']))
            with patch.object(module,'scenario_native_select_model_fixture') as probe:
                selected[0][1]()
                probe.assert_called_once_with(provider,options=128,combo=True,visible_items=8)
        for options in (True,-1,198,256):
            with patch.object(module,'Driver') as driver,self.assertRaises(ValueError):
                module._owned_select_fixture(combo=True,options=options)
            driver.assert_not_called()

    def test_combo_provider_unavailable_requires_unchanged_control_and_row(self):
        spec=importlib.util.spec_from_file_location('combo_unavailable_eval',ROOT/'examples/eval.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        base={'passed':False,'events':[],'readback':['Alpha','0'],'source_stable':True,
              'combo_readback':{'control_value':'Alpha','selected_index':'0','state':'closed'},
              'native_receipts':[],
              'audit':{'status':'UNCHANGED_SAMPLED'},'provider_calls':[{'status':'error',
              'error':{'code':'organization_access_disabled'}}]}
        def invoke(report):
            with patch.object(module,'_owned_select_fixture',return_value=None), \
                 patch('tacto.helper.InheritedHelperDriver'), \
                 patch('examples.probe_native_select.run_and_save',return_value=report), \
                 contextlib.redirect_stdout(io.StringIO()):
                return module.scenario_native_select_combo_model_fixture('claude')
        self.assertEqual('provider_unavailable:organization_access_disabled',invoke(base))
        with self.assertRaises(AssertionError):invoke({**base,'native_receipts':[{'action':{'name':'open_select'}}]})
        for readback in (None,{'control_value':'Beta','selected_index':'1','state':'closed'},
                         {'control_value':'Alpha','selected_index':'0','state':'open'}):
            with self.subTest(readback=readback),self.assertRaises(AssertionError):
                invoke({**base,'combo_readback':readback})

    def test_duplicate_fixture_is_isolated_and_mode_is_not_truthy_coerced(self):
        spec=importlib.util.spec_from_file_location('duplicate_eval',ROOT/'examples/eval.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        _,selected,cases=module._parse_plan(['native_select_duplicate_fixture','--offline'])
        self.assertEqual('skipped',cases[0]['status'])
        self.assertEqual({'gui','native'},set(cases[0]['requirements']))
        with patch.object(module,'scenario_native_select_fixture') as probe:
            selected[0][1]()
            probe.assert_called_once_with(duplicates=True)
        for value in (1,'true',None):
            with patch.object(module,'Driver') as driver,self.assertRaises(ValueError):
                module._owned_select_fixture(duplicates=value)
            driver.assert_not_called()

    def test_many_native_options_are_explicitly_isolated_and_invalid_count_never_launches(self):
        spec=importlib.util.spec_from_file_location('many_select_eval',ROOT/'examples/eval.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        _,selected,cases=module._parse_plan(['native_select_many_codex','--offline'])
        self.assertEqual('skipped',cases[0]['status'])
        self.assertEqual({'gui','native','model'},set(cases[0]['requirements']))
        with patch.object(module,'scenario_native_select_model_fixture') as probe:
            selected[0][1]()
            probe.assert_called_once_with('codex',options=128)
        for count in (True,-1,257,'128'):
            with patch.object(module,'Driver') as driver,self.assertRaises(ValueError):
                module._owned_select_fixture(options=count)
            driver.assert_not_called()

    def test_native_unavailable_requires_no_events_initial_readback_and_stable_audit(self):
        spec=importlib.util.spec_from_file_location('native_eval',ROOT/'examples/eval.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        base={'passed':False,'events':[],'readback':['Alpha','0'],'source_stable':True,
              'audit':{'status':'UNCHANGED_SAMPLED'},'provider_calls':[{'status':'error',
              'error':{'code':'organization_access_disabled'}}]}
        def invoke(report):
            with patch.object(module,'_owned_select_fixture',return_value=None), \
                 patch('tacto.helper.InheritedHelperDriver'), \
                 patch('examples.probe_native_select.run_and_save',return_value=report), \
                 contextlib.redirect_stdout(io.StringIO()):
                return module.scenario_native_select_model_fixture('claude')
        self.assertEqual('provider_unavailable:organization_access_disabled',invoke(base))
        for changed in ({'events':[{'action':'open_select'}]}, {'readback':['Beta','1']},
                        {'source_stable':False}, {'audit':{'status':'UNKNOWN'}},
                        {'provider_calls':[{'status':'error','error':{'code':'GeminiProtocolError'}}]}):
            with self.subTest(changed=changed),self.assertRaises(AssertionError):invoke({**base,**changed})

    def test_explicit_backbone_is_scoped_and_offline_never_calls_it(self):
        spec=importlib.util.spec_from_file_location('gemini_eval',ROOT/'examples/eval.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        for provider in ('gemini','opencode','codex','claude'):
            args,selected,cases=module._parse_plan(['signed_provider_envelope','--model-provider',provider,'--offline'])
            self.assertEqual({'provider':provider},selected[0][1].keywords)
            self.assertEqual('skipped',cases[0]['status'])
            self.assertEqual({'model'},set(cases[0]['requirements']))
        for args in (['signed_native_journal_worker','--model-provider','gemini'],
                     ['signed_provider_envelope','signed_producer_file','--model-provider','gemini'],
                     ['signed_provider_envelope','--model-provider','unknown']):
            with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):module._parse_plan(args)
        for provider in ('codex','claude'):
            _,selected,cases=module._parse_plan(['native_select_model_fixture','--model-provider',provider,'--offline'])
            self.assertEqual({'provider':provider},selected[0][1].keywords)
            self.assertEqual('skipped',cases[0]['status'])
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            module._parse_plan(['native_select_model_fixture','--model-provider','gemini'])
