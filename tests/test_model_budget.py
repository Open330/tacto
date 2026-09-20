"""Deadline propagation without model calls or native UI."""
import unittest
from unittest.mock import patch
from nlc.model_budget import model_deadline, remaining_model_seconds


class ModelBudgetTests(unittest.TestCase):
    def test_nested_deadlines_cannot_expand_and_context_is_restored(self):
        with patch('nlc.model_budget.time.monotonic',return_value=100):
            with model_deadline(105):
                self.assertEqual(5,remaining_model_seconds())
                with model_deadline(150): self.assertEqual(5,remaining_model_seconds())
                with model_deadline(102): self.assertEqual(2,remaining_model_seconds())
                self.assertEqual(5,remaining_model_seconds())
            self.assertEqual(420,remaining_model_seconds())
        for bad in (True,0,-1,float('nan'),float('inf')):
            with self.assertRaises(ValueError): remaining_model_seconds(bad)

    def test_expired_budget_never_calls_provider(self):
        from nlc.authoring import reach_model
        with patch('nlc.model_budget.time.monotonic',return_value=100), model_deadline(99), \
             patch('nlc.authoring._ask_model') as provider:
            with self.assertRaises(TimeoutError): reach_model('q','claude')
            provider.assert_not_called()

    def test_provider_fallback_consumes_same_budget_including_subseconds(self):
        from nlc.authoring import _ask_model
        now=[100.0];seen=[]
        def first(name,prompt,timeout):
            seen.append(timeout);now[0]+=.6
            return None
        def second(name,prompt,timeout):
            seen.append(timeout)
            return '{}'
        with patch('nlc.authoring.time.monotonic',side_effect=lambda:now[0]), \
             model_deadline(101), patch('nlc.authoring._try_muxa',side_effect=first), \
             patch('nlc.authoring._try_cli',side_effect=second):
            self.assertEqual('{}',_ask_model('q','codex'))
        self.assertAlmostEqual(1,seen[0]);self.assertAlmostEqual(.4,seen[1])

    def test_ask_does_not_hide_timeout_as_invalid_model_answer(self):
        from nlc.ask import Q,ask
        with patch('nlc.ask.reach_model',side_effect=TimeoutError):
            with self.assertRaises(TimeoutError): ask(Q('test','q',lambda value:value))

    def test_paging_uses_one_deadline(self):
        from nlc.ask import ask_here_paged
        from types import SimpleNamespace
        now=[100.0];seen=[]
        def answer(*args,**kwargs):
            seen.append(remaining_model_seconds());now[0]+=.6
            return {'next_page':True} if len(seen)==1 else {'done':True}
        page=SimpleNamespace(total=2,picks=(0,),token_count=10,overflow=False,
                             items=((0,{'label':'A','can':['read']}),),next_cursor='cursor')
        with patch('nlc.ask.time.monotonic',side_effect=lambda:now[0]), \
             patch('nlc.candidate_paging.page_candidates',return_value=page), \
             patch('nlc.ask.ask',side_effect=answer):
            result=ask_here_paged('goal',[],timeout_seconds=1)
        self.assertTrue(result['done'])
        self.assertAlmostEqual(1,seen[0]);self.assertAlmostEqual(.4,seen[1])

    def test_general_passes_remaining_budget_and_never_dispatches_late_answer(self):
        from nlc.general import run
        class Driver:
            capabilities=()
            def __init__(self): self.actions=[]
            def observe(self,*a,**kw):
                return {'type':'observation','obs_id':'o','tree':'0 Button Title: Next'}
            def act(self,*a,**kw): self.actions.append(a);return {'type':'acted'}
        now=[100.0];seen=[];driver=Driver()
        def planner(goal,candidates,done):
            seen.append(remaining_model_seconds());now[0]+=2
            return {'actions':[{'do':'click','candidate':candidates[0]}]}
        with patch('nlc.general.time.monotonic',side_effect=lambda:now[0]):
            result=run(driver,'test','next',planner,timeout_seconds=1)
        self.assertEqual([1],seen)
        self.assertEqual('deadline_exceeded',result.stop_reason)
        self.assertEqual([],driver.actions)
        self.assertEqual(420,remaining_model_seconds())

    def test_commit_witness_inherits_remaining_budget_and_timeout_stops_action(self):
        from nlc.general import run
        from nlc.contract import Envelope
        from nlc.outcome import OutcomeSpec,Check
        class Driver:
            capabilities=()
            def __init__(self): self.actions=[]
            def observe(self,*args,**kwargs):
                return {'type':'observation','obs_id':'o','tree':'0 Window Title: Test\n  1 Button Title: Save\n  2 StaticText Title: Location, Value: target'}
            def act(self,*args,**kwargs): self.actions.append(args);return {'type':'acted'}
        for when in ('before_commit','after'):
            now=[100.0];seen=[];driver=Driver()
            def planner(goal,candidates,done):
                now[0]+=.2
                return {'done':True} if when=='after' else {'actions':[{'do':'click','candidate':candidates[0]}]}
            def witness(field,candidates):
                seen.append(remaining_model_seconds())
                raise TimeoutError('model timed out')
            spec=OutcomeSpec(required=(Check('destination','request',when,'reports'),),expected={'destination':'target'})
            env=Envelope(quote='save',allows=frozenset({'read','write_persistent'}),scope='item')
            with patch('nlc.general.time.monotonic',side_effect=lambda:now[0]):
                result=run(driver,'test','save',planner,timeout_seconds=1,spec=spec,envelope=env,
                           ask_where=witness,approve=lambda _:True)
            self.assertEqual(1,len(seen),(when,result.why))
            self.assertAlmostEqual(.8,seen[0])
            self.assertEqual('model_timeout',result.stop_reason)
            self.assertFalse(result.ok)
            self.assertEqual([],driver.actions)

    def test_real_sleeping_local_provider_is_bounded_before_ui_dispatch(self):
        import sys,time
        from nlc.ask import Q,ask
        from nlc.authoring import _run_provider
        from nlc.general import run
        class Driver:
            capabilities=()
            def __init__(self): self.actions=[]
            def observe(self,*a,**kw): return {'type':'observation','obs_id':'o','tree':'0 Button Title: Next'}
            def act(self,*a,**kw): self.actions.append(a);return {'type':'acted'}
        driver=Driver()
        def slow_cli(provider,prompt,timeout):
            return _run_provider([sys.executable,'-c','import time; time.sleep(5); print("{}")'],timeout).stdout
        def planner(goal,candidates,done):
            return ask(Q('test','q',lambda _: {'actions':[{'do':'click','candidate':candidates[0]}]}),provider='claude')
        started=time.monotonic()
        with patch('nlc.helper.InheritedHelperDriver.available',return_value=False), \
             patch('nlc.authoring._try_cli',side_effect=slow_cli):
            result=run(driver,'test','next',planner,timeout_seconds=.1)
        self.assertLess(time.monotonic()-started,2)
        self.assertEqual('deadline_exceeded',result.stop_reason)
        self.assertEqual([],driver.actions)
