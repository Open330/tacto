import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from examples.benchmark_agent import run_agent
from examples.benchmark_matched_conditions import aggregate


class MatchedConditionsTests(unittest.TestCase):
    def test_both_arms_receive_identical_explicit_model_and_effort(self):
        stream='\n'.join(json.dumps(e) for e in [
            {'type':'thread.started','thread_id':'fresh'},
            {'type':'turn.completed','usage':{'input_tokens':20,'cached_input_tokens':5,'output_tokens':2}}])
        with tempfile.TemporaryDirectory() as tmp:
            calls=[]
            def launch(argv,**kwargs):
                calls.append((list(argv),kwargs))
                child=Mock();child.returncode=0;child.__enter__=Mock(return_value=child);child.__exit__=Mock(return_value=False)
                return child
            with patch('examples.benchmark_agent.subprocess.Popen',side_effect=launch), \
                 patch('tacto.provider_io.communicate_owned_provider',return_value=(stream,'')), \
                 patch('tacto.provider_io.require_provider_child_ownership'):
                for arm in ('nlc','sky'):
                    row=run_agent('codex','same task, layer '+arm,Path(tmp)/arm,
                                  model='gpt-6.1-sol',reasoning_effort='medium')
                    self.assertEqual('gpt-6.1-sol',row['requested_model'])
                    self.assertEqual('medium',row['requested_reasoning_effort'])
                    self.assertTrue(row['fresh_workspace']);self.assertFalse(row['history_supplied'])
            normalized=[]
            for argv,kwargs in calls:
                argv=list(argv);argv[argv.index('-C')+1]='<fresh-workspace>';normalized.append(argv)
            self.assertEqual(normalized[0],normalized[1])
            self.assertIn('--ephemeral',calls[0][0])
            self.assertIn('model_reasoning_effort="medium"',calls[0][0])
            self.assertNotEqual(calls[0][1]['cwd'],calls[1][1]['cwd'])
            self.assertNotIn('TACTO_HELPER_FD',calls[0][1]['env'])

    def test_unknown_usage_and_failed_tasks_do_not_get_zero_token_savings(self):
        rows=[{'arm':arm,'exit':0,'oracle':{'passed':True},'seconds':2,
               'usage':{'total_input_tokens':30,'new_input_tokens':10,'cache_read_tokens':20,'output_tokens':3}}
              for arm in ('nlc','sky')]
        self.assertEqual(30,aggregate(rows)['sky']['total_input_tokens'])
        rows[1]['usage']={};self.assertIsNone(aggregate(rows)['sky']['total_input_tokens'])
        rows[0]['exit']=1;self.assertEqual(0,aggregate(rows)['nlc']['passed'])
        self.assertIsNone(aggregate(rows)['nlc']['total_input_tokens'])
        self.assertEqual(30,aggregate(rows)['nlc']['observed_usage_all_attempts']['total_input_tokens']['sum'])
        self.assertEqual(0,aggregate(rows)['nlc']['observed_usage_all_attempts']['total_input_tokens']['unknown_records'])
        self.assertEqual(1,aggregate(rows)['sky']['observed_usage_all_attempts']['total_input_tokens']['unknown_records'])
        self.assertIsNone(aggregate(rows)['sky']['observed_usage_all_attempts']['total_input_tokens']['sum'])

class FreshFixtureOrchestrationTests(unittest.TestCase):
    """main() with every outside effect faked: no app build, UI, provider or install."""

    def run_main(self, *, agent=None, state=None, survive_close=False):
        import examples.benchmark_matched_conditions as module
        events, fixtures = [], []
        class Process:
            def __init__(self, pid):
                self.pid, self.code = pid, None
            def poll(self):
                return self.code
        class Fixture:
            def __init__(self, base, app):
                self.base = Path(base); self.base.mkdir(mode=0o700)
                self.process = Process(1000 + len(fixtures)); fixtures.append(self)
                self.state = None
                live = [f for f in fixtures[:-1] if f.process.poll() is None]
                events.append(('start', self.base.name, len(live)))
            def setup(self, job, changed=False):
                self.state = (state or (lambda job: {'active': False, 'amount': '0', 'bonus': False,
                    'commits': [], 'control_version': 2 if changed else 1, 'factor': '0',
                    'job': job['job'], 'phase': 'entry', 'result': 0, 'sequence': 1}))(job)
                return dict(self.state)
            def oracle(self, job):
                expected = {k: job[k] for k in ('job', 'amount', 'factor', 'bonus')}
                expected['result'] = int(job['result'])
                done = {**self.state, 'phase': 'done', 'commits': [expected]}
                return {'passed': True, 'single_commit': True, 'state': done, 'active': False}
            def close(self):
                events.append(('close', self.base.name))
                if not survive_close:
                    self.process.code = 0
        def run_agent(provider, prompt, workdir, **kwargs):
            events.append(('agent', Path(workdir).name))
            if agent:
                agent(Path(workdir).name)
            return {'exit': 0, 'seconds': 1.0, 'usage': {'total_input_tokens': 10, 'new_input_tokens': 4,
                    'cache_read_tokens': 6, 'output_tokens': 1}}
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)/'home'
            (home/'.local/bin').mkdir(parents=True); (home/'.local/bin/codex').write_text('')
            (home/'.codex').mkdir(); (home/'.codex/config.toml').write_text('model="x"\n')
            out = Path(tmp)/'out'
            argv = ['benchmark', '--output', str(out), '--launcher', str(home/'launcher')]
            with patch.object(module.sys, 'argv', argv), \
                 patch.object(module.Path, 'home', return_value=home), \
                 patch.object(module, 'runtime_evidence', return_value={'skill': '/skill'}), \
                 patch.object(module, 'guide_evidence', return_value={'path': '/skill'}), \
                 patch.object(module, 'guide_runtime_compatibility', return_value={'compatible': True}), \
                 patch.object(module, 'manifest', return_value={'source': 'same'}), \
                 patch.object(module, 'agent_evidence', return_value={'sha256': 'a'}), \
                 patch.object(module, 'runtime_aliases', return_value={}), \
                 patch.object(module, 'build', return_value=Path(tmp)/'app'), \
                 patch.object(module, 'prompt', side_effect=lambda job, target, arm, **kw: f'{arm}:{job["job"]}'), \
                 patch.object(module, 'Fixture', Fixture), \
                 patch.object(module, 'run_agent', side_effect=run_agent):
                try:
                    module.main()
                    error = None
                except Exception as exc:
                    error = exc
            report = json.loads((out/'report.json').read_text()) if (out/'report.json').exists() else None
        return events, fixtures, report, error

    def test_every_arm_gets_its_own_fixture_closed_before_the_next(self):
        events, fixtures, report, error = self.run_main()
        self.assertIsNone(error)
        self.assertEqual(6, len(fixtures))
        names = [e[1] for e in events if e[0] == 'start']
        self.assertEqual(['fixture-01-nlc', 'fixture-01-sky', 'fixture-02-sky', 'fixture-02-nlc',
                          'fixture-03-nlc', 'fixture-03-sky'], names)
        self.assertTrue(all(e[2] == 0 for e in events if e[0] == 'start'))
        for start in range(0, len(events), 3):
            kind = [e[0] for e in events[start:start + 3]]
            self.assertEqual(['start', 'agent', 'close'], kind)
        self.assertTrue(report['initial_states_matched'])
        self.assertTrue(report['passed'])
        self.assertFalse(report['comparison_eligible'])
        isolation = report['fixture_isolation']
        self.assertTrue(isolation['process_per_arm'] and isolation['previous_fixture_ended_before_next'])
        self.assertEqual([], isolation['initial_state_keys_excluded'])
        for row in report['rows']:
            self.assertEqual([], row['initial_state']['commits'])
            self.assertEqual(1, row['initial_state']['sequence'])
            self.assertEqual(0, row['fixture_isolation']['exit_code'])
        self.assertEqual(6, len({row['fixture_isolation']['pid'] for row in report['rows']}))
        self.assertEqual(12, report['summary']['nlc']['observed_usage_all_attempts']['new_input_tokens']['sum'])

    def test_agent_failure_still_closes_that_arms_fixture_and_stops(self):
        def agent(name):
            if name == '01-sky':
                raise RuntimeError('provider crashed')
        events, fixtures, report, error = self.run_main(agent=agent)
        self.assertIsInstance(error, RuntimeError)
        self.assertEqual(2, len(fixtures))
        self.assertTrue(all(f.process.poll() == 0 for f in fixtures))
        self.assertEqual(('close', 'fixture-01-sky'), events[-1])
        self.assertIsNone(report)

    def test_a_fixture_that_survives_close_or_shared_history_is_refused(self):
        events, fixtures, report, error = self.run_main(survive_close=True)
        self.assertIsInstance(error, RuntimeError)
        self.assertEqual(1, len(fixtures))
        self.assertEqual('owned fixture still running', str(error))
        for stale in ({'commits': [{'job': 'P00'}]}, {'sequence': 2}, {'phase': 'done'}, {'active': True},
                      {'extra': 1}):
            with self.subTest(stale):
                def state(job, stale=stale):
                    return {'active': False, 'amount': '0', 'bonus': False, 'commits': [],
                            'control_version': 1, 'factor': '0', 'job': job['job'], 'phase': 'entry',
                            'result': 0, 'sequence': 1, **stale}
                events, fixtures, report, error = self.run_main(state=state)
                self.assertIsInstance(error, ValueError)
                self.assertEqual(1, len(fixtures))
                self.assertEqual(0, fixtures[0].process.poll())
                self.assertNotIn('agent', [e[0] for e in events])


if __name__=='__main__':unittest.main()
