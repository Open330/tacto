import copy
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('task_benchmark', Path(__file__).resolve().parents[1] / 'examples/task_benchmark.py')
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


class TaskBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads(bench.DEFAULT_MANIFEST.read_text())

    def success(self):
        event = dict(event='result', passed=True, loop_ok=True,
                     dom_result='TACTO-2026-confirmed', said='TACTO-2026-confirmed')
        with patch.object(bench, '_run_bounded', return_value=(0, json.dumps(event))):
            return bench.run_task(self.manifest, 'input-next-read')

    def test_real_runner_adapter_requires_all_three_oracles(self):
        result = self.success()
        self.assertEqual('passed', result['status'])
        self.assertEqual(3, len(result['evidence']))
        self.assertIsNone(result['model'])
        event = dict(event='result', passed=True, loop_ok=True,
                     dom_result='WRONG', said='TACTO-2026-confirmed')
        with patch.object(bench, '_run_bounded', return_value=(0, json.dumps(event))):
            result = bench.run_task(self.manifest, 'input-next-read')
        self.assertEqual('failed', result['status'])

    def test_missing_and_unavailable_are_not_successes(self):
        baseline = bench.run_task(self.manifest, 'input-next-read', 'codex-computer-use')
        self.assertEqual('unavailable', baseline['status'])
        summary = bench.summarize(self.manifest, [self.success(), baseline])
        self.assertEqual(0.2, summary['totals']['tacto']['planned_task_completion_rate'])
        self.assertEqual(0, summary['totals']['codex-computer-use']['planned_task_completion_rate'])
        self.assertEqual(4, summary['totals']['tacto']['missing_tasks'])
        self.assertFalse(summary['equivalence_established'])

    def test_unimplemented_task_never_executes(self):
        self.manifest['tasks'][1]['runner'] = None
        self.manifest['tasks'][1]['implemented'] = False
        with patch.object(bench, '_run_bounded') as run:
            result = bench.run_task(self.manifest, 'select-read')
        self.assertEqual('unavailable', result['status'])
        run.assert_not_called()

    def test_changed_conditions_cannot_be_compared(self):
        result = self.success()
        changed = copy.deepcopy(self.manifest)
        changed['tasks'][0]['time_budget_seconds'] += 1
        with self.assertRaises(ValueError):
            bench.summarize(changed, [result])
        result['condition_id'] = bench.condition_id(changed['tasks'][0])
        changed['tasks'][0]['goal'] = 'different request'
        with self.assertRaises(ValueError):
            bench.summarize(changed, [result])

    def test_missing_evidence_or_duplicate_id_is_rejected(self):
        result = self.success()
        with self.assertRaises(ValueError):
            bench.summarize(self.manifest, [result, result])
        result['evidence'].pop()
        with self.assertRaises(ValueError):
            bench.validate_result(result, self.manifest)

    def test_failures_are_not_hidden_by_passing_retry(self):
        passed = self.success()
        failed = copy.deepcopy(passed)
        failed.update(run_id='retry-failed', status='failed', reason='oracle failed')
        summary = bench.summarize(self.manifest, [passed, failed])
        self.assertEqual(0, summary['totals']['tacto']['fully_passed_tasks'])
        self.assertEqual(1, summary['totals']['tacto']['passed_runs'])

    def test_timeout_and_missing_outcome_do_not_pass(self):
        with patch.object(bench, '_run_bounded', side_effect=subprocess.TimeoutExpired('probe', 180)):
            result = bench.run_task(self.manifest, 'input-next-read')
        self.assertEqual('timeout', result['status'])
        with patch.object(bench, '_run_bounded', return_value=(0, '{}')):
            result = bench.run_task(self.manifest, 'input-next-read')
        self.assertEqual('failed', result['status'])

    def test_runner_fixture_change_rejected_before_execution(self):
        self.manifest['tasks'][0]['initial_conditions']['fixture_sha256'] = 'changed'
        with patch.object(bench, '_run_bounded') as run:
            with self.assertRaises(ValueError):
                bench.run_task(self.manifest, 'input-next-read')
        run.assert_not_called()

    def test_skips_require_reason_and_duration_cannot_be_nan(self):
        result = self.success()
        result.update(status='skipped', reason=None)
        with self.assertRaises(ValueError):
            bench.validate_result(result, self.manifest)
        result.update(reason='no browser', elapsed_seconds=float('nan'))
        with self.assertRaises(ValueError):
            bench.validate_result(result, self.manifest)

    def test_source_snapshot_hashes_allowlist_bytes_and_effective_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'tacto').mkdir()
            (root / 'examples').mkdir()
            (root / 'reviews').mkdir()
            (root / 'tacto/core.py').write_text('original')
            for name in ('task_benchmark.py', 'benchmark_web_runner.py', 'probe_browser_task.py'):
                (root / 'examples' / name).write_text(name)
            manifest_file = root / 'reviews/manifest.json'
            manifest_file.write_text(json.dumps(self.manifest))
            with patch.object(bench, 'ROOT', root):
                first = bench.source_snapshot(self.manifest, manifest_file)
                (root / 'reviews/output.jsonl').write_text('new output')
                (root / '.venv').mkdir()
                (root / '.venv/ignored.py').write_text('environment noise')
                self.assertEqual(first['source_sha256'], bench.source_snapshot(self.manifest, manifest_file)['source_sha256'])
                (root / 'tacto/core.py').write_text('changed')
                self.assertNotEqual(first['source_sha256'], bench.source_snapshot(self.manifest, manifest_file)['source_sha256'])
                (root / 'tacto/core.py').write_text('original')
                manifest_file.write_text(json.dumps(self.manifest) + '\n')
                self.assertNotEqual(first['source_sha256'], bench.source_snapshot(self.manifest, manifest_file)['source_sha256'])
                altered = copy.deepcopy(self.manifest)
                altered['tasks'][0]['goal'] += ' changed'
                self.assertNotEqual(bench.source_snapshot(self.manifest, manifest_file)['source_sha256'],
                                    bench.source_snapshot(altered, manifest_file)['source_sha256'])

    def test_run_records_start_end_source_without_hiding_change(self):
        start = {'git_head': 'head', 'source_sha256': 'a' * 64, 'dirty': True, 'hashed_files': []}
        end = {**start, 'source_sha256': 'b' * 64}
        with patch.object(bench, 'source_snapshot', side_effect=[start, end]):
            result = self.success()
        self.assertEqual(result['source']['start'], start)
        self.assertEqual(result['source']['end'], end)
        self.assertFalse(result['source']['stable_during_run'])
        self.assertEqual(result['source']['source_sha256'], start['source_sha256'])
        self.assertIn('python', result['environment'])
        self.assertIn('playwright', result['environment'])

    def test_summary_separates_providers_sources_and_keeps_legacy_unknown(self):
        original = self.success()
        records = [original]
        changed = copy.deepcopy(original)
        changed.update(run_id='different-code')
        changed['source']['source_sha256'] = 'c' * 64
        records.append(changed)
        other = copy.deepcopy(original)
        other.update(run_id='different-provider', provider='claude')
        records.append(other)
        legacy = copy.deepcopy(original)
        legacy.update(run_id='old-no-source')
        legacy.pop('source')
        records.append(legacy)
        summary = bench.summarize(self.manifest, records)
        self.assertEqual(len(summary['cohorts']), 4)
        self.assertTrue(summary['mixed_cohorts'])
        unknown = next(c for c in summary['cohorts'] if c['source_sha256'] is None)
        self.assertEqual(unknown['source_status'], 'unknown')
        self.assertNotIn('source', legacy)

    def test_unmeasured_baseline_has_null_measured_rate_and_zero_coverage(self):
        baseline = bench.run_task(self.manifest, 'input-next-read', 'codex-computer-use')
        summary = bench.summarize(self.manifest, [baseline])
        for system in self.manifest['systems']:
            tally = summary['totals'][system]
            self.assertIsNone(tally['measured_run_pass_rate'])
            self.assertEqual(tally['measurement_coverage'], 0)
        self.assertIsNone(summary['cohorts'][0]['measured_run_pass_rate'])


class ExternalBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads(bench.DEFAULT_MANIFEST.read_text())

    def test_observed_provider_metadata_preserves_unknown_and_attempt_totals(self):
        from tacto.provider_result import ProviderResult, ProviderAttempt
        observed = ProviderResult('request', 'auto', 'claude', 'cli', 'ok', 'secret', None, 1,
            model='observed-model', output_tokens=4,
            attempts=(ProviderAttempt('claude', 'cli', 'ok', 1, output_tokens=4),)).to_dict()
        observed.pop('text')
        result = {}
        bench.attach_provider_metadata(result, {'provider_calls': [observed]})
        self.assertEqual(result['provider'], 'claude')
        self.assertEqual(result['model'], 'observed-model')
        self.assertEqual(result['model_usage']['tokens']['output_tokens']['observed_sum'], 4)
        self.assertIsNone(result['model_usage']['tokens']['total_input_tokens']['observed_sum'])
        bench.attach_provider_metadata(result, {})
        self.assertIsNone(result['provider'])
        self.assertIsNone(result['model'])
        with self.assertRaises(ValueError):
            bench.attach_provider_metadata({}, {'provider_calls': [dict(observed, text='secret')]})
        with self.assertRaises(ValueError):
            bench.attach_provider_metadata({}, {'provider_calls': [None]})

    def test_external_adapter_validates_conditions_artifact_bytes_and_oracle_links(self):
        import hashlib
        task = self.manifest['tasks'][0]
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / 'raw.json'
            artifact.write_text('{"observed":"TACTO-2026-confirmed"}')
            record = dict(task_id=task['id'], condition_id=bench.condition_id(task),
                system='codex-computer-use', run_id='external-run', status='passed',
                elapsed_seconds=10, provider=None, model=None, environment={'sky':'0.7.1'},
                evidence=[dict(oracle=o, passed=True, observed='recorded', artifact='raw.json')
                          for o in task['oracles']],
                external_observation=dict(transport='trusted node_repl sky RPC',
                    execution_mode='fixed-actions', artifacts=[dict(path='raw.json',
                    sha256=hashlib.sha256(artifact.read_bytes()).hexdigest())]))
            imported = bench.import_external_result(self.manifest, record, directory)
            self.assertEqual(imported['measurement_mode'], 'fixed-actions')
            self.assertNotIn('artifact_validation', record)
            broken = copy.deepcopy(record)
            broken['evidence'][0]['artifact'] = 'missing.json'
            with self.assertRaises(ValueError):
                bench.import_external_result(self.manifest, broken, directory)
            broken = copy.deepcopy(record)
            broken['external_observation']['artifacts'][0]['path'] = '../raw.json'
            with self.assertRaises(ValueError):
                bench.import_external_result(self.manifest, broken, directory)
            artifact.write_text('changed')
            with self.assertRaises(ValueError):
                bench.import_external_result(self.manifest, record, directory)

    def test_external_adapter_never_dispatches_or_relabels_unavailable(self):
        record = bench.run_task(self.manifest, 'input-next-read', 'codex-computer-use')
        with patch.object(bench, '_run_bounded') as runner:
            with self.assertRaises(ValueError):
                bench.import_external_result(self.manifest, record, '.')
        runner.assert_not_called()


try:
    import playwright.sync_api
    HAS_BROWSER = True
except ImportError:
    HAS_BROWSER = False


@unittest.skipUnless(HAS_BROWSER, 'optional playwright dependency not installed')
class WebBenchmarkFixtureTests(unittest.TestCase):
    def test_input_probe_records_actual_provider_requests_through_common_boundary(self):
        from tacto.provider_result import ProviderResult
        spec = importlib.util.spec_from_file_location('probe_browser_task',
            bench.ROOT / 'examples/probe_browser_task.py')
        probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(probe)
        answers = iter([
            {'actions': [{'pick': 0, 'do': 'set_value', 'value': 'TACTO-2026'},
                         {'pick': 1, 'do': 'click'}]},
            {'actions': [{'pick': 0, 'do': 'read'}]},
            {'done': True, 'actions': []}])
        calls = []
        def provider(name, prompt, timeout):
            calls.append(name)
            return ProviderResult(request_id='owned-' + str(len(calls)),
                requested_provider=name, provider=name, transport='cli-json', status='ok',
                text=json.dumps(next(answers)), error=None, elapsed_seconds=.01,
                model='observed-test-model', input_tokens=10, output_tokens=3)
        output = io.StringIO()
        with patch('sys.argv', ['probe_browser_task', '--provider', 'claude']), \
             patch('sys.stdout', output), patch('tacto.authoring._try_cli', side_effect=provider):
            self.assertEqual(0, probe.main(), output.getvalue())
        result = json.loads(output.getvalue().splitlines()[-1])
        self.assertTrue(result['passed'])
        self.assertEqual(['claude'] * 3, calls)
        self.assertEqual(3, len(result['provider_calls']))
        self.assertTrue(all(c['model'] == 'observed-test-model' and c['input_tokens'] == 10
                            for c in result['provider_calls']))

    def test_all_four_fixtures_with_fixed_planners(self):
        spec = importlib.util.spec_from_file_location('benchmark_web_runner', bench.ROOT / 'examples/benchmark_web_runner.py')
        web = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(web)
        for task in web.FIXTURES:
            with self.subTest(task=task):
                sequence = []
                if task in ('save-readback', 'combined'):
                    sequence.append(('Content', 'set_value', {'value': web.CONTENT}))
                if task in ('select-read', 'combined'):
                    sequence.append(('Choice', 'select_option', {'value': 'beta'}))
                if task in ('scroll-read', 'combined'):
                    sequence.append(('Scroll anchor', 'scroll', {'direction': 'down', 'pages': 10}))
                if task in ('save-readback', 'combined'):
                    sequence.append(('Save', 'click', {}))
                read = 'readback' if task in ('save-readback', 'combined') else ('Result' if task == 'select-read' else 'Marker')
                sequence.append((read, 'read', {}))
                count = 0
                def planner(goal, candidates, done):
                    nonlocal count
                    if count >= len(sequence):
                        return {'done': True}
                    label, action, params = sequence[count]
                    count += 1
                    candidate = next(c for c in candidates if c['label'] == label)
                    answer = {'actions': [{'do': action, 'candidate': candidate, **params}]}
                    if label == 'Save':
                        answer['expect'] = 'Saved'
                    return answer
                result = web.execute(task, planner=planner)
                self.assertTrue(result['passed'], result)
                self.assertEqual(set(web.ORACLES[task]), {e['oracle'] for e in result['evidence']})
                self.assertEqual('fixed', result['planner_mode'])

    def test_model_done_claim_cannot_satisfy_independent_oracles(self):
        spec = importlib.util.spec_from_file_location('benchmark_web_runner', bench.ROOT / 'examples/benchmark_web_runner.py')
        web = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(web)
        result = web.execute('combined', planner=lambda *args: {'done': True})
        self.assertFalse(result['passed'])
        by_name = {e['oracle']: e for e in result['evidence']}
        for name in ('form_value', 'selected_value', 'scroll_position', 'destination', 'file_readback', 'read_answer', 'receipt'):
            self.assertFalse(by_name[name]['passed'], name)
        self.assertEqual(0, by_name['destination']['observed']['writes'])

    def test_batched_edit_then_save_replans_before_commit(self):
        spec = importlib.util.spec_from_file_location('benchmark_web_runner', bench.ROOT / 'examples/benchmark_web_runner.py')
        web = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(web)
        def planner(goal, candidates, done):
            def action(label, operation, **params):
                return {'candidate': next(c for c in candidates if c['label'] == label),
                        'do': operation, **params}
            if not done:
                return {'actions': [action('Content', 'set_value', value=web.CONTENT),
                                    action('Save', 'click')], 'expect': 'Saved'}
            if len(done) == 1:
                return {'actions': [action('Save', 'click')], 'expect': 'Saved'}
            if len(done) == 2:
                return {'actions': [action('readback', 'read')]}
            return {'done': True}
        result = web.execute('save-readback', planner=planner)
        self.assertTrue(result['passed'], result)
        self.assertEqual(4, result['model_calls'])
        self.assertEqual([{'do': 'set_value', 'label': 'Content'}, {'do': 'click', 'label': 'Save'}],
                         result['plans'][0]['actions'])
        self.assertNotIn(web.CONTENT, json.dumps(result['plans']))


if __name__ == '__main__':
    unittest.main()
