"""CLI diagnostic recording is bounded and never a completion oracle."""
import json
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest.mock import patch

from nlc.preparation_diagnostic import MARKER, diagnostic_lines, preparation_diagnostic
from tests.test_product_comparison_provenance import comparison


class PreparationDiagnosticTransportTests(unittest.TestCase):
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

    def test_result_is_a_copy(self):
        source = self.detail(); output = preparation_diagnostic(source)
        source['reason'] = 'PRIVATE_SENTINEL'
        self.assertEqual('readiness_deadline', output['reason'])

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

        def run(argv, **kwargs):
            Path(argv[argv.index('-o') + 1]).write_text('claimed success')
            return subprocess.CompletedProcess(argv, 0, '\n'.join(json.dumps(e) for e in events), '')

        task = comparison.Task('probe', 'goal', lambda _: (False, 'no new folder'))
        with patch.dict(sys.modules, {'focus_witness':types.SimpleNamespace(FocusWitness=Witness)}), \
             patch.object(comparison, '_locked', return_value=False), \
             patch.object(comparison, 'source_manifest', return_value={'sha256':'fixed','files':{}}), \
             patch.object(comparison.subprocess, 'run', side_effect=run):
            result = comparison.run_one(task, 'nlc')
        self.assertEqual([self.detail()], result['reported_preparation_diagnostics'])
        self.assertEqual('failed', result['status'])
        self.assertFalse(result['oracle_passed'])
        self.assertNotIn('PRIVATE_SENTINEL', json.dumps(result))

    def test_replay_retains_diagnostics_when_postcondition_times_out(self):
        from nlc.flow import replay
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
        from nlc.flow import replay
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
        from nlc import cli
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
