"""Provider selection is explicit; parsing never issues a model request."""
import unittest
from unittest.mock import patch
from tacto.auto_repair import AutoRepair, parse_options, choose_candidate, RepairRefused
from tests import test_auto_repair as fixtures


class AutoRepairProviderTests(unittest.TestCase):
    def test_both_providers_parse_without_calling_a_chooser(self):
        with patch('tacto.auto_repair.choose_candidate') as choose:
            for provider in ('codex','claude'):
                clean,automatic=parse_options(['--helper','--auto-repair',provider])
                self.assertEqual(['--helper'],clean)
                self.assertEqual(provider,automatic.provider)
            choose.assert_not_called()

    def test_claude_uses_same_typed_result_path_with_explicit_provider(self):
        with patch('tacto.authoring.reach_model_result',return_value=fixtures.response()) as route:
            choose_candidate({'candidates':[]},20,provider='claude')
        self.assertEqual('claude',route.call_args.kwargs['provider'])
        self.assertEqual(20,route.call_args.kwargs['timeout_seconds'])
        self.assertTrue(route.call_args.kwargs['expect_json'])

    def test_default_chooser_retains_provider_for_each_page(self):
        with patch('tacto.auto_repair.choose_candidate',return_value=fixtures.response()) as choose:
            automatic=AutoRepair(provider='claude')
            automatic.chooser({'candidates':[]},30)
            automatic.chooser({'candidates':[]},20)
        self.assertEqual(['claude','claude'],[c.kwargs['provider'] for c in choose.call_args_list])

    def test_observed_wrong_provider_is_not_used_to_repair(self):
        import contextlib,io
        from tacto.cli import _run_flow
        from tacto.flow_batch import BatchDriver
        from tests.test_flow_batch import SignedCalculator
        app=SignedCalculator();messages=[]
        automatic=AutoRepair(provider='claude',chooser=lambda *_:fixtures.response())
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()), \
             patch('tacto.cli._indicator_for',return_value=contextlib.nullcontext()):
            code,_=automatic.run(fixtures.recipe(),{},['--helper'],driver=BatchDriver(app),run_flow=_run_flow,emit=messages.append)
        self.assertEqual(1,code)
        self.assertEqual([2],[action['element_index'] for action in app.calls])
        self.assertTrue(any('provider_mismatch' in message for message in messages))

    def test_prose_response_has_a_specific_closed_error_and_no_suffix_action(self):
        helper=fixtures.AutoRepairTests()
        code,_,automatic,app,messages=helper.execute(
            chooser=lambda *_:fixtures.response('explanation before {"candidate":2}'))
        self.assertEqual(1,code)
        self.assertEqual([2],[action['element_index'] for action in app.calls])
        self.assertIn('TACTO_AUTO_REPAIR_STOP invalid_candidate_response',messages)
        self.assertFalse(any('explanation before' in message for message in messages))

    def test_unrecognized_provider_does_not_enable_fallback(self):
        with self.assertRaises(RepairRefused):AutoRepair(provider='auto')
        with self.assertRaises(RepairRefused):choose_candidate({},10,provider='auto')
        with self.assertRaises(RepairRefused):parse_options(['--auto-repair','auto'])
