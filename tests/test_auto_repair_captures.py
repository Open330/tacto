"""Exercise private capture continuation through real replay with an offline app."""
import contextlib
from copy import deepcopy
import io
import json
import pickle
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from tacto.auto_repair import AutoRepair
from tacto.cli import _run_flow
from tacto.continuation_state import ReplayContinuation
from tacto.flow import Capture, Flow, Postcondition, Selector, Step, replay
from tacto.flow_batch import BatchDriver
from tests import test_auto_repair as helpers
from tests.test_runtime_contract import App, contract


class Fields(App):
    def __init__(self, source='private-value-7437'):
        super().__init__()
        self.source=source
        self.values={2:'empty',3:'empty'}
        self.protected=[]
        self.source_after_first=None
        self.read_calls=0

    @property
    def tree(self):
        return ('0 Window Title: Capture Test\n'
                f'  1 StaticText Identifier: Source, Value: {self.source}\n'
                f'  2 TextField Identifier: First, Title: First input, Value: {self.values[2]}\n'
                f'  3 TextField Identifier: Second, Title: Second input, Value: {self.values[3]}')

    def observe(self,*args,**kwargs):
        return super().observe(*args)

    def protect_value(self,value):
        self.protected.append(value)

    def act(self,obs,target,action,**kwargs):
        if action['name']=='read':
            self.read_calls+=1
            return {'type':'acted'}
        self.calls.append(deepcopy(action))
        self.values[action['element_index']]=action['value']
        if self.source_after_first and action['element_index']==2:
            self.source=self.source_after_first
        return {'type':'acted'}


def check(identifier, equals=None):
    return Postcondition(field=Selector(identifier=identifier),equals=equals,
                         contains='Identifier: '+identifier if equals is None else None,
                         attribute='Value')


def recipe(*, sensitive=True):
    return Flow(name='copy captured value',target='com.example.capture',steps=[
        Step(action='read',selector=Selector(identifier='Source'),
             postcondition=check('Source'),safety=contract(),
             captures=[Capture(name='value',field=Selector(identifier='Source'),sensitive=sensitive)]),
        Step(action='set_value',selector=Selector(role='TextField',identifier='OldFirst'),
             params={'value':'{{value}}'},safety=contract(('write_ephemeral',)),
             postcondition=check('First','{{value}}')),
    ])


class CaptureContinuationTests(unittest.TestCase):
    def execute(self,flow=None,app=None,chooser=None,flags=None,omit_snapshot=False):
        app=app or Fields();results=[];messages=[];output=io.StringIO()
        def runner(*args,**kwargs):
            on_result=kwargs['_on_result']
            def collect(result):
                if omit_snapshot and result._continuation is not None:
                    result._continuation.clear();result._continuation=None
                results.append(result);on_result(result)
            return _run_flow(*args,**{**kwargs,'_on_result':collect})
        automatic=AutoRepair(chooser=chooser or Mock(return_value=helpers.response('{"candidate":0}')))
        with contextlib.redirect_stdout(output),contextlib.redirect_stderr(output), \
                patch('tacto.cli._indicator_for',return_value=contextlib.nullcontext()):
            code,learned=automatic.run(flow or recipe(),{},flags or ['--helper'],
                driver=BatchDriver(app),run_flow=runner,emit=messages.append)
        return code,learned,app,automatic,results,output.getvalue(),messages

    def test_sensitive_value_reaches_input_but_not_packet_logs_or_saved_recipe(self):
        code,learned,app,automatic,results,output,messages=self.execute()
        self.assertEqual(0,code,(output,messages))
        self.assertEqual(app.source,app.values[2])
        self.assertEqual(1,len(app.calls))
        self.assertEqual(1,app.read_calls)
        self.assertEqual('[redacted]',results[-1].log[-1]['captured']['value'])
        shown=json.dumps([output,messages,[r.log for r in results],automatic.chooser.call_args.args[0]])
        self.assertNotIn(app.source,shown)
        self.assertNotIn(app.source,learned.to_json())
        self.assertEqual('{{value}}',learned.steps[1].params['value'])
        self.assertIn(app.source,app.protected)
        self.assertTrue(all(r._continuation is None for r in results))

    def test_nonsensitive_capture_used_in_effective_check_and_later_suffix(self):
        code,learned,app,automatic,results,output,messages=self.execute(recipe(sensitive=False))
        self.assertEqual(0,code,(output,messages))
        self.assertEqual(app.source,app.values[2])
        self.assertEqual(app.source,results[-1].log[-1]['captured']['value'])
        self.assertNotIn(app.source,learned.to_json())

    def test_sensitive_name_stays_sensitive_when_overwritten_before_second_repair(self):
        flow=recipe();app=Fields();app.source_after_first='replacement-secret-9899'
        # A verified input step captures a new source value under the old name.
        # Its checkpoint must keep the FIRST input value despite that overwrite.
        flow.steps[1].captures=[Capture(name='value',field=Selector(identifier='Source'),sensitive=False)]
        flow.steps.append(Step(action='set_value',selector=Selector(role='TextField',identifier='OldSecond'),
             params={'value':'{{value}}'},safety=contract(('write_ephemeral',)),
             postcondition=check('Second','{{value}}')))
        choose=Mock(side_effect=[helpers.response('{"candidate":0}'),helpers.response('{"candidate":1}')])
        code,learned,app,automatic,results,output,messages=self.execute(flow,app,choose)
        self.assertEqual(0,code,(output,messages))
        self.assertEqual('private-value-7437',app.values[2])
        self.assertEqual('replacement-secret-9899',app.values[3])
        self.assertEqual([2,3],[a['element_index'] for a in app.calls])
        self.assertEqual(2,automatic.calls)
        self.assertEqual('[redacted]',results[-1].log[-1]['captured']['value'])
        shown=json.dumps([output,messages,[r.log for r in results],choose.call_args_list[1].args[0],learned.to_json()])
        self.assertNotIn('private-value-7437',shown)
        self.assertNotIn('replacement-secret-9899',shown)
        self.assertIn('replacement-secret-9899',app.protected)
        self.assertTrue(all(r._continuation is None for r in results))

    def test_terminal_decline_packet_is_redacted_and_snapshot_released(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'failure.json'
            code,_,app,automatic,results,output,messages=self.execute(
                chooser=Mock(return_value=helpers.response('{"candidate":null}')),
                flags=['--helper','--repair-packet',str(path)])
            self.assertEqual(1,code)
            self.assertEqual([],app.calls)
            self.assertNotIn(app.source,path.read_text())
            self.assertNotIn(app.source,json.dumps([output,messages,[r.log for r in results]]))
            self.assertEqual(0o600,path.stat().st_mode & 0o777)
            self.assertTrue(all(r._continuation is None for r in results))

    def test_capture_value_with_literal_template_syntax_is_data(self):
        app=Fields(source='literal {{not_an_instruction}}')
        code,_,app,_,_,output,messages=self.execute(app=app)
        self.assertEqual(0,code,(output,messages))
        self.assertEqual('literal {{not_an_instruction}}',app.values[2])


    def test_prefix_captures_without_private_snapshot_do_not_resume(self):
        code,_,app,automatic,_,output,messages=self.execute(omit_snapshot=True)
        self.assertEqual(1,code)
        self.assertEqual(0,automatic.calls)
        self.assertEqual([],app.calls)
        self.assertIn('continuation_state_unavailable',' '.join(messages))

    def test_skipped_capture_cannot_dispatch_its_unresolved_template(self):
        flow=recipe();flow.steps[0].skip_if=check('Source')
        flow.steps.insert(1,Step(action='read',selector=Selector(identifier='Source'),
                               postcondition=check('Source'),safety=contract()))
        flow.steps[2].selector.identifier='First'
        code,_,app,automatic,results,output,messages=self.execute(flow)
        self.assertEqual(1,code,(output,messages))
        self.assertEqual([],app.calls)
        self.assertEqual(0,automatic.calls)
        self.assertIn('runtime_value_missing',output)

    def test_sensitive_candidate_identity_never_becomes_learned_selector(self):
        class PrivateLabel(Fields):
            @property
            def tree(self):
                return super().tree.replace('Title: First input','Title: '+self.source)
        code,learned,app,automatic,_,output,messages=self.execute(app=PrivateLabel())
        self.assertEqual(1,code)
        self.assertEqual([],app.calls)
        self.assertEqual('OldFirst',learned.steps[1].selector.identifier)
        self.assertNotIn(app.source,json.dumps(automatic.chooser.call_args.args[0]))
        self.assertIn('captured_identity_requires_review',' '.join(messages))

    def test_decision_branch_inherits_sensitive_names_secrets_and_redacted_narration(self):
        flow=recipe();app=Fields();narration=[];seen=[]
        flow.steps[0].captures.append(Capture(name="alias",field=Selector(identifier="Source")))
        branch=Step(action='read',selector=Selector(identifier='Source'),postcondition=check('Source'),
                    safety=contract(),intent_text='Read {{value}}',answers=True)
        inner=Step(action='decide',params={'question':'Inner?', 'choices':['continue','stop'], 'into':'inner'})
        flow.steps[1]=Step(action='decide',params={'question':'Choose?', 'choices':['go','stop'], 'into':'choice',
            'goal':'test branch','branches':{'go':[inner,branch]}})
        def choose(question,choices,inputs):
            seen.append(inputs)
            return choices[0]
        result=replay(app,flow.bind(),decide=choose,narrate=narration.append)
        self.assertTrue(result.ok,result.log)
        self.assertTrue(any('branch' in row for row in result.log))
        self.assertNotIn(app.source,json.dumps([result.log,narration,seen]))
        self.assertTrue(all('value' not in inputs and 'alias' not in inputs for inputs in seen))
        self.assertEqual('[redacted]',result.log[-1]['captured']['value'])

    def test_sensitive_capture_in_decision_question_is_not_sent_to_model(self):
        flow=recipe();app=Fields();choose=Mock(return_value='go')
        flow.steps[1]=Step(action='decide',params={'question':'Use {{value}}?', 'choices':['go','stop'], 'into':'choice'})
        result=replay(app,flow.bind(),decide=choose)
        self.assertFalse(result.ok)
        choose.assert_not_called()
        self.assertNotIn(app.source,json.dumps(result.log))

    def test_parameter_template_syntax_cannot_read_a_sensitive_capture(self):
        for literal in ('{{value}}','{{unknown}}','hello {{value}} world'):
            with self.subTest(literal=literal):
                flow=recipe();flow.params={'message':literal}
                flow.steps[1].params={'value':'{{message}}'}
                flow.steps[1].postcondition.equals='{{message}}'
                code,_,app,_,_,output,messages=self.execute(flow)
                self.assertEqual(0,code,(output,messages))
                self.assertEqual(literal,app.values[2])
                self.assertNotIn(app.source,output)

    def test_parameter_literal_without_capture_survives_bind_and_runtime(self):
        flow=recipe();flow.steps[0].captures=[]
        flow.params={'message':'Mustache {{example}}'}
        flow.steps[1].params={'value':'{{message}}'}
        flow.steps[1].postcondition.equals='{{message}}'
        code,_,app,_,_,output,messages=self.execute(flow)
        self.assertEqual(0,code,(output,messages))
        self.assertEqual('Mustache {{example}}',app.values[2])

    def test_table_literal_and_runtime_table_key_keep_data_literal(self):
        for runtime_key in (False,True):
            flow=recipe(sensitive=False)
            key='value' if runtime_key else 'fixed'
            app=Fields(source='runtime-key')
            flow.tables={'messages':{'runtime-key' if runtime_key else 'fixed':'table {{value}}'}}
            flow.steps[1].params={'value':'{{messages['+key+']}}'}
            flow.steps[1].postcondition.equals='{{messages['+key+']}}'
            code,_,app,_,_,output,messages=self.execute(flow,app=app)
            self.assertEqual(0,code,(output,messages))
            self.assertEqual('table {{value}}',app.values[2])

    def test_rebinding_and_copying_bound_flow_preserves_literal_provenance(self):
        flow=recipe();flow.params={'message':'{{value}}'}
        flow.steps[1].params={'value':'{{message}}'}
        flow.steps[1].postcondition.equals='{{message}}'
        bound=deepcopy(flow.bind()).bind()
        # Replay directly with the already-correct selector, isolating binding.
        bound.steps[1].selector.identifier='First'
        app=Fields();result=replay(app,bound)
        self.assertTrue(result.ok,result.log)
        self.assertEqual('{{value}}',app.values[2])

    def test_screen_candidate_template_text_cannot_become_recipe_code(self):
        for label in ('{{value}}','{{unknown}}'):
            class TemplateLabel(Fields):
                @property
                def tree(self):
                    return super().tree.replace('Title: First input','Title: '+label)
            code,learned,app,automatic,_,output,messages=self.execute(app=TemplateLabel())
            self.assertEqual(0,code,(output,messages))
            self.assertEqual(app.source,app.values[2])
            self.assertEqual(label,learned.steps[1].selector.title)
            stored=learned.to_json()
            self.assertNotIn(app.source,stored)
            code,_,next_app,next_auto,_,output,messages=self.execute(Flow.from_json(stored),app=TemplateLabel())
            self.assertEqual(0,code,(output,messages))
            self.assertEqual(0,next_auto.calls)
            self.assertEqual(next_app.source,next_app.values[2])

    def test_bound_export_cannot_silently_turn_literal_data_into_references(self):
        flow=recipe();flow.params={'message':'{{value}}'}
        flow.steps[1].params={'value':'{{message}}'}
        flow.steps[1].postcondition.equals='{{message}}'
        bound=flow.bind()
        for compact in (False,True):
            loaded=Flow.from_json(bound.to_json(compact=compact)).bind()
            loaded.steps[1].selector.identifier='First'
            app=Fields();result=replay(app,loaded)
            self.assertTrue(result.ok,result.log)
            self.assertEqual('{{value}}',app.values[2])
        # Saving the source template retains the distinction across JSON.
        loaded=Flow.from_json(flow.to_json()).bind()
        loaded.steps[1].selector.identifier='First'
        app=Fields();result=replay(app,loaded)
        self.assertTrue(result.ok,result.log)
        self.assertEqual('{{value}}',app.values[2])

    def test_ordinary_replay_does_not_retain_private_continuation_snapshot(self):
        result=replay(Fields(),recipe().bind())
        self.assertFalse(result.ok)
        self.assertIsNone(result._continuation)

    def test_continuation_refuses_serialization_and_clear_releases_values(self):
        state=ReplayContinuation(step_index=1,captures={'value':['private']},secrets=['private'],
            sensitive_names={'value'},checkpoint=None,outcome=None)
        self.assertNotIn('private\'',repr(state))
        with self.assertRaises(TypeError):pickle.dumps(state)
        values,secrets,names=state.resume_values()
        values['value'].append('changed')
        self.assertEqual(['private'],state.resume_values()[0]['value'])
        state.clear()
        self.assertEqual(({},(),frozenset()),state.resume_values())


if __name__=='__main__':unittest.main()
