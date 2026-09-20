import unittest
from unittest.mock import patch
from nlc.general import run


class App:
    def __init__(self, result=None):
        self.count = 0
        self.calls = []
        self.result = result or {'type':'acted', 'unverified':'observe_effect',
            'input_route':'window_bound_mouse', 'reobserve_required':True}
    def tree(self):
        return f'0 Window Title: Test\n  1 Button Description: Increment, Identifier: increment\n  2 StaticText Value: {self.count}, Identifier: count'
    def observe(self, *args, **kwargs):
        return {'type':'observation', 'obs_id':'obs', 'tree':self.tree()}
    def probe(self, obs_id, target, index):
        return {'type':'probe','line':self.tree().splitlines()[index]}
    def act(self, obs_id, target, action, **kwargs):
        self.calls.append(dict(action))
        if action['name'] == 'click':
            self.count += 1
            return self.result
        return {'type':'acted'}


class GeneralWindowObservationTests(unittest.TestCase):
    def test_reobserves_and_discards_old_batch_before_reading(self):
        app = App()
        plans = []
        app.wait_until = lambda *args: self.fail('waited for discarded batch expectation')
        def planner(goal, candidates, history):
            plans.append([c['label'] for c in candidates])
            if len(plans) == 1:
                return {'actions':[{'do':'click','candidate':candidates[0]},
                                   {'do':'read','candidate':candidates[1]}],
                        'expect':'result of an unexecuted later action'}
            if len(plans) == 2:
                self.assertEqual('1', candidates[1]['label'])
                return {'actions':[{'do':'read','candidate':candidates[1]}]}
            return {'done':True}
        result = run(app,'fixture','increment once and read',planner)
        self.assertTrue(result.ok, result.why)
        self.assertEqual(['1'],result.said)
        self.assertEqual(['click','read'],[a['name'] for a in app.calls])
        self.assertEqual(3,result.asked)
        self.assertEqual('dispatched',result.events[0]['state'])
        self.assertEqual('unknown',result.certainty)

    def test_uncertain_or_unrecognized_response_does_not_resume(self):
        for changed in ({'unverified':'cannot_complete'}, {'input_route':'other'},
                        {'reobserve_required':False}):
            with self.subTest(changed=changed):
                app = App()
                app.result.update(changed)
                def planner(goal, candidates, history):
                    return {'actions':[{'do':'click','candidate':candidates[0]},
                                       {'do':'read','candidate':candidates[1]}]}
                result = run(app,'fixture','increment',planner)
                self.assertFalse(result.ok)
                self.assertEqual(1,len(app.calls))
                self.assertEqual('unknown',result.events[0]['state'])

    def test_remaining_run_deadline_reaches_actual_helper_message(self):
        from unittest.mock import patch
        from nlc.helper import InheritedHelperDriver
        from nlc.model_budget import model_deadline, remaining_model_seconds
        for outer, expected in [(None, 101.0), (100.5, 100.5)]:
            with self.subTest(outer=outer):
                now = [100.0]
                class HelperApp(App):
                    def __init__(self):
                        super().__init__()
                        self.messages = []
                    def _send(self, message):
                        self.messages.append(message)
                        return self.result
                    act = InheritedHelperDriver.act
                app = HelperApp()
                def planner(goal, candidates, history):
                    now[0] += .25
                    return {'actions':[{'do':'click', 'candidate':candidates[0]}]}
                with patch('time.monotonic', side_effect=lambda: now[0]):
                    with model_deadline(outer):
                        run(app, 'fixture', 'click once', planner, max_screens=1, timeout_seconds=1)
                    self.assertEqual(420, remaining_model_seconds())
                self.assertEqual(1,len(app.messages))
                self.assertEqual(expected, app.messages[0]['click_deadline_uptime'])

    def test_prepare_window_is_capability_gated_and_reobserved(self):
        from nlc.general import candidates
        from nlc.promote import candidate_from
        self.assertFalse(any(c['role']=='Window' for c in candidates(App().tree())))
        class PrepareApp(App):
            capabilities={'action.prepare_window'}
            def act(self, obs_id, target, action, **kwargs):
                if action['name']=='prepare_window':
                    self.calls.append(dict(action))
                    self.count=1
                    assert 'enabled' not in kwargs['expect']
                    return {'type':'acted','unverified':'observe_effect',
                            'input_route':'window_prepare','reobserve_required':True}
                return super().act(obs_id,target,action,**kwargs)
        app=PrepareApp();plans=[]
        def planner(goal, cands, history):
            plans.append(cands)
            if len(plans)==1:
                window=next(c for c in cands if c['role']=='Window')
                return {'actions':[{'do':'prepare_window','candidate':window},
                    {'do':'click','candidate':next(c for c in cands if c['role']=='Button')}],
                    'expect':'discard this whole batch expectation'}
            if len(plans)==2:
                return {'actions':[{'do':'read','candidate':next(c for c in cands if c['role']=='StaticText')}]}
            return {'done':True}
        result=run(app,'fixture','prepare and read state',planner)
        self.assertTrue(result.ok,result.why)
        self.assertEqual([a['name'] for a in app.calls],['prepare_window','read'])
        self.assertEqual(result.said,['1'])
        self.assertEqual(result.events[0]['state'],'dispatched')
        self.assertEqual(result.certainty,'unknown')
        result.verified=True
        from nlc.promote import deployable
        candidate=candidate_from(result,'fixture','prepare and read state')
        self.assertIsNotNone(candidate)
        self.assertIsNone(deployable(candidate,'prepare','prepare and read state')[0])

    def test_prepare_question_requires_capability_and_last_action(self):
        from nlc.ask import what_to_do_here
        from nlc.general import candidates
        cs=candidates(App().tree(),capabilities={'action.prepare_window'})
        pick=next(i for i,c in enumerate(cs) if c['role']=='Window')
        q=what_to_do_here('prepare',cs)
        self.assertIsNotNone(q.check({'actions':[{'pick':pick,'do':'prepare_window'}]}))
        self.assertIsNone(q.check({'actions':[{'pick':pick,'do':'prepare_window'},{'pick':0,'do':'click'}]}))
        cs[pick]['can']=[]
        self.assertIsNone(what_to_do_here('prepare',cs).check({'actions':[{'pick':pick,'do':'prepare_window'}]}))

    def test_read_history_preserves_value_beyond_candidate_label(self):
        class LongApp(App):
            def tree(self):return '0 Window Title: Test\n  1 StaticText Value: '+('x'*180)+' END, Identifier: answer'
        app=LongApp();histories=[]
        def planner(goal,cands,history):
            histories.append(list(history))
            return {'actions':[{'do':'read','candidate':cands[0]}]} if len(histories)==1 else {'done':True}
        result=run(app,'fixture','read the complete answer',planner)
        self.assertTrue(result.ok,result.why)
        self.assertIn('x'*180+' END',histories[1][0])
        self.assertEqual(result.said,['x'*180+' END'])

    def test_window_preparation_precedes_large_button_list(self):
        from nlc.general import candidates
        tree='0 AXWindow Title: Test\n'+'\n'.join(f'  {i+1} Button Title: Button {i}' for i in range(200))
        cs=candidates(tree,capabilities={'action.prepare_window'})
        self.assertEqual(cs[0]['can'],['prepare_window'])
        self.assertEqual(cs[0]['role'],'AXWindow')


class ForegroundRefusalTests(unittest.TestCase):
    """A refusal that names what a PERSON can do is not a failure report.

    The driver refuses rather than taking the front for itself (the synthetic
    ApplicationActivated event that used to do so leaked, and its own gate then
    read the leak as truth). That refusal is only worth anything if the layers
    above carry it to someone who can act on it.
    """
    def _run(self, refusal):
        class Refusing(App):
            def act(self, obs_id, target, action, **kwargs):
                self.calls.append(dict(action))
                return {'type': 'error', 'error': 'action_failed',
                        'effect': 'not_started', **refusal,
                        'detail': 'background'}
        app = Refusing()
        def planner(goal, cands, history):
            return {'actions': [{'do': 'click',
                                 'candidate': next(c for c in cands if c['role'] == 'Button')}]}
        return app, run(app, 'fixture', 'press it', planner)

    def test_needs_foreground_stops_with_an_actionable_reason(self):
        app, result = self._run({'needs_foreground': True})
        self.assertFalse(result.ok)
        self.assertEqual('needs_foreground', result.stop_reason)
        self.assertIn('앞', result.why)          # says what the person can do
        self.assertNotIn('실행 실패', result.why)  # and not that it broke
        self.assertEqual(['click'], [a['name'] for a in app.calls])

    def test_needs_pointer_is_carried_the_same_way(self):
        _, result = self._run({'needs_pointer': True})
        self.assertEqual('needs_pointer', result.stop_reason)

    def test_an_ordinary_failure_is_still_a_failure(self):
        _, result = self._run({})
        self.assertEqual('', result.stop_reason)
        self.assertIn('실행 실패', result.why)

    def test_agent_reports_a_handoff_rather_than_a_failure(self):
        from nlc.agent import Agent
        from nlc.general import GeneralResult
        for reason, expected in (('needs_foreground', 'needs_handoff'),
                                 ('needs_pointer', 'needs_handoff'),
                                 ('screen_limit', 'failed'), ('', 'failed')):
            with self.subTest(reason=reason):
                outcome = GeneralResult(ok=False, why='앱을 앞으로 가져와 주세요',
                                        stop_reason=reason)
                agent = Agent(driver=App(), store=None,
                              general=lambda *a, **k: outcome)
                with patch('nlc.authoring.guess_target', return_value='fixture'):
                    turn = agent._work_it_out('버튼 눌러줘', {'target': 'fixture'},
                                              ask=lambda _: True)
                self.assertEqual(expected, turn.status)
