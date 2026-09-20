import copy
import importlib.util
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
                     dom_result='NLC-2026-confirmed', said='NLC-2026-confirmed')
        with patch.object(bench, '_run_bounded', return_value=(0, json.dumps(event))):
            return bench.run_task(self.manifest, 'input-next-read')

    def test_real_runner_adapter_requires_all_three_oracles(self):
        result = self.success()
        self.assertEqual('passed', result['status'])
        self.assertEqual(3, len(result['evidence']))
        self.assertIsNone(result['model'])
        event = dict(event='result', passed=True, loop_ok=True,
                     dom_result='WRONG', said='NLC-2026-confirmed')
        with patch.object(bench, '_run_bounded', return_value=(0, json.dumps(event))):
            result = bench.run_task(self.manifest, 'input-next-read')
        self.assertEqual('failed', result['status'])

    def test_missing_and_unavailable_are_not_successes(self):
        baseline = bench.run_task(self.manifest, 'input-next-read', 'codex-computer-use')
        self.assertEqual('unavailable', baseline['status'])
        summary = bench.summarize(self.manifest, [self.success(), baseline])
        self.assertEqual(0.2, summary['totals']['nlc']['planned_task_completion_rate'])
        self.assertEqual(0, summary['totals']['codex-computer-use']['planned_task_completion_rate'])
        self.assertEqual(4, summary['totals']['nlc']['missing_tasks'])
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
        self.assertEqual(0, summary['totals']['nlc']['fully_passed_tasks'])
        self.assertEqual(1, summary['totals']['nlc']['passed_runs'])

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
            (root / 'nlc').mkdir()
            (root / 'examples').mkdir()
            (root / 'reviews').mkdir()
            (root / 'nlc/core.py').write_text('original')
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
                (root / 'nlc/core.py').write_text('changed')
                self.assertNotEqual(first['source_sha256'], bench.source_snapshot(self.manifest, manifest_file)['source_sha256'])
                (root / 'nlc/core.py').write_text('original')
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


try:
    import playwright.sync_api
    HAS_BROWSER = True
except ImportError:
    HAS_BROWSER = False


@unittest.skipUnless(HAS_BROWSER, 'optional playwright dependency not installed')
class WebBenchmarkFixtureTests(unittest.TestCase):
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
