"""CLI diagnostic recording is bounded and never a completion oracle."""
import json
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest.mock import patch

from tacto.preparation_diagnostic import MARKER, diagnostic_lines, preparation_diagnostic
from tests.test_product_comparison_provenance import comparison


class PreparationDiagnosticTransportTests(unittest.TestCase):
    def test_identity_cause_is_closed_and_only_valid_for_identity_refusal(self):
        from tacto.preparation_diagnostic import IDENTITY_CAUSES
        base={'phase':'binding','reason':'identity_changed','elapsed_ms':1}
        for cause in IDENTITY_CAUSES:
            value={**base,'identity_cause':cause}
            self.assertEqual(value,preparation_diagnostic(value))
        for cause in (None,True,[],{},'PRIVATE_SENTINEL'):
            self.assertIsNone(preparation_diagnostic({**base,'identity_cause':cause}))
        self.assertIsNone(preparation_diagnostic({**base,'reason':'dispatched_unverified','identity_cause':'element_map'}))

    def detail(self):
        return {'phase': 'binding', 'reason': 'readiness_deadline', 'elapsed_ms': 4999,
                'scoped_ax_operations': 402}

    def test_closed_fields_only_and_no_boolean_counters(self):
        valid = self.detail()
        self.assertEqual(valid, preparation_diagnostic(valid))
        for field, value in [('phase', 'PRIVATE_SENTINEL'), ('reason', []),
                             ('elapsed_ms', True), ('elapsed_ms', -1),
                             ('scoped_ax_operations', 1000001), ('extra', 'PRIVATE_SENTINEL')]:
            invalid = {**valid, field: value}
            with self.subTest(field=field, value=value):
                self.assertIsNone(preparation_diagnostic(invalid))

    def test_profile_fields_are_optional_bounded_integer_metadata(self):
        valid={**self.detail(),'context_elapsed_ms':4500,'ax_operation_elapsed_ms':150}
        self.assertEqual(valid,preparation_diagnostic(valid))
        for key in ('context_elapsed_ms','ax_operation_elapsed_ms'):
            for value in (True,-1,600001,'private'):
                self.assertIsNone(preparation_diagnostic({**valid,key:value}))

    def test_readiness_context_and_ax_failure_survive_closed_marker(self):
        detail={**self.detail(),'reason':'readiness_attribute_failed',
                'full_context_checks':1000000,'incremental_context_checks':1000000,
                'readiness_ax_error':-25204,'context_elapsed_ms':600000,
                'ax_operation_elapsed_ms':600000}
        self.assertEqual(diagnostic_lines(MARKER+json.dumps(detail)),[detail])
        for key in ('full_context_checks','incremental_context_checks','readiness_ax_error'):
            self.assertIsNone(preparation_diagnostic({**detail,key:True}))

    def test_closed_target_refusals_survive_without_becoming_input_authority(self):
        for reason in ('target_window_not_focused','target_input_changed','target_focus_outside_window',
                       'target_input_not_focused','target_row_not_in_focused_list'):
            detail={**self.detail(),'phase':'dispatch','reason':reason}
            self.assertEqual(diagnostic_lines(MARKER+json.dumps(detail)),[detail])
            self.assertIsNone(preparation_diagnostic({**detail,'authorizes_input':True}))
        self.assertIsNone(preparation_diagnostic({**self.detail(),'reason':'PRIVATE_TARGET_DETAIL'}))

    def test_result_is_a_copy(self):
        source = self.detail(); output = preparation_diagnostic(source)
        source['reason'] = 'PRIVATE_SENTINEL'
        self.assertEqual('readiness_deadline', output['reason'])

    def test_headroom_components_are_bounded_non_authorizing_and_copied(self):
        headroom=dict(required_ms=6000,remaining_ms=2000,validation_ms=300,
                      routing_ms=10,input_ms=2,context_ms=5,event_count=3,saturated=False)
        value={**self.detail(),'headroom':headroom}
        result=preparation_diagnostic(value)
        self.assertEqual(headroom,result['headroom'])
        headroom['validation_ms']=99
        self.assertEqual(300,result['headroom']['validation_ms'])
        for updates in ({'required_ms':True},{'remaining_ms':-1},{'context_ms':600001},
                        {'event_count':0},{'saturated':1},{'document':'PRIVATE_TEXT'}):
            self.assertIsNone(preparation_diagnostic({**value,'headroom':{**headroom,**updates}}))
        self.assertEqual([result],diagnostic_lines(MARKER+json.dumps(result)))

    def test_validation_headroom_marker_survives_closed_transport(self):
        detail = {**self.detail(), 'reason': 'validation_headroom', 'phase': 'dispatch'}
        self.assertEqual([detail], diagnostic_lines(MARKER + json.dumps(detail)))

    def test_marker_only_and_invalid_lines_are_not_retained(self):
        valid = MARKER + json.dumps(self.detail())
        text = 'PRIVATE_SENTINEL\n' + valid + '\n' + MARKER + '{invalid}\n' + '  ' + valid
        self.assertEqual([self.detail()], diagnostic_lines(text))
        self.assertNotIn('PRIVATE_SENTINEL', repr(diagnostic_lines(text)))

    def test_record_and_output_budgets(self):
        line = MARKER + json.dumps(self.detail()) + '\n'
        self.assertEqual(64, len(diagnostic_lines(line * 80)))
        self.assertEqual([], diagnostic_lines('x' * (1024 * 1024 + 1)))

    def test_runner_copies_only_completed_tool_diagnostics(self):
        class Witness:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def summary(self): return {'initial':'fixture', 'activations':[]}

        diagnostic = MARKER + json.dumps(self.detail())
        item = {'type':'command_execution', 'command':'nlc run owned.json --helper',
                'aggregated_output': diagnostic + '\nPRIVATE_SENTINEL'}
        events = [{'type':'item.started', 'item':item}, {'type':'item.completed', 'item':item}]

        def run(argv, prompt, timeout, diagnostics):
            Path(argv[argv.index('-o') + 1]).write_text('claimed success')
            return subprocess.CompletedProcess(argv, 0, '\n'.join(json.dumps(e) for e in events), '')

        task = comparison.Task('probe', 'goal', lambda _: (False, 'no new folder'))
        with patch.dict(sys.modules, {'focus_witness':types.SimpleNamespace(FocusWitness=Witness)}), \
             patch.object(comparison, '_locked', return_value=False), \
             patch.object(comparison, '_agent_executable_available',return_value=True), \
             patch.object(comparison, 'source_manifest', return_value={'sha256':'fixed','files':{}}), \
             patch('tacto.helper.InheritedHelperDriver.available',return_value=True), \
             patch('tacto.codex_result.subprocess.Popen',side_effect=AssertionError('unit test must not spawn an external process')), \
             patch.object(comparison, '_run_agent', side_effect=run):
            result = comparison.run_one(task, 'nlc')
        self.assertEqual([self.detail()], result['reported_preparation_diagnostics'])
        self.assertEqual('failed', result['status'])
        self.assertFalse(result['oracle_passed'])
        self.assertNotIn('PRIVATE_SENTINEL', json.dumps(result))

    def test_replay_retains_diagnostics_when_postcondition_times_out(self):
        from tacto.flow import replay
        from tests.test_prepare_window_replay import App, PrepareWindowReplayTests
        diagnostic = {**self.detail(), 'phase':'dispatch', 'reason':'dispatched_unverified'}
        response = {'type':'acted', 'input_route':'window_prepare',
                    'unverified':'observe_effect', 'reobserve_required':True,
                    'preparation_diagnostic':diagnostic}
        app = App(changes=False, response=response)
        result = replay(app, PrepareWindowReplayTests().flow())
        self.assertFalse(result.ok)
        self.assertEqual(1, len(app.calls))
        self.assertEqual([diagnostic], result.preparation_diagnostics)

    def test_refusal_retains_diagnostics_without_creating_dispatch_event(self):
        from tacto.flow import replay
        from tests.test_prepare_window_replay import App, PrepareWindowReplayTests
        app = App(response={'type':'error', 'effect':'not_started', 'error':'action_failed',
                            'preparation_diagnostic':self.detail()})
        result = replay(app, PrepareWindowReplayTests().flow())
        self.assertFalse(result.ok)
        self.assertEqual([], result.events)
        self.assertEqual([self.detail()], result.preparation_diagnostics)

    def test_cli_without_trace_emits_diagnostic_after_postcondition_failure(self):
        import contextlib
        import io
        from tacto import cli
        from tests.test_prepare_window_replay import App, PrepareWindowReplayTests
        detail = {**self.detail(), 'phase':'dispatch', 'reason':'dispatched_unverified'}
        app = App(changes=False, response={'type':'acted', 'input_route':'window_prepare',
                  'unverified':'observe_effect', 'reobserve_required':True,
                  'preparation_diagnostic':detail})
        output = io.StringIO()
        with patch.object(cli, '_make_driver', return_value=app), \
             patch.object(cli, '_indicator_for', return_value=contextlib.nullcontext()), \
             contextlib.redirect_stdout(output):
            code = cli._run_flow(PrepareWindowReplayTests().flow(), ['--helper', '--yes'])
        self.assertEqual(1, code)
        self.assertEqual([detail], diagnostic_lines(output.getvalue()))


if __name__ == '__main__':
    unittest.main()
