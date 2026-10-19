import contextlib
import io
import json
import tempfile
from pathlib import Path
from copy import deepcopy
import unittest
from unittest.mock import Mock, patch

from tacto.auto_repair import AutoRepair, parse_options, RepairRefused
from tacto.cli import _run_flow
from tacto.flow import Flow, Capture, Selector
from tacto.flow_batch import BatchDriver
from tacto.command_help import calculator_example
from tacto.provider_result import ProviderResult
from tests.test_flow_batch import SignedCalculator, BatchTests


def response(text='{"candidate":2}',status='ok'):
    return ProviderResult(request_id='test',requested_provider='codex',provider='codex',transport='test',
                          status=status,text=text,error=None,elapsed_seconds=.01,total_input_tokens=120,output_tokens=5)


def recipe():
    doc=calculator_example();doc['steps']=doc['steps'][2:4]
    doc['steps'][1]['selector']['identifier']='OldTwo'
    return Flow.from_json(json.dumps(doc))


class AutoRepairTests(unittest.TestCase):
    def execute(self,flow=None,chooser=None,app=None,maximum=2):
        flow=flow or recipe();app=app or SignedCalculator();app.close=Mock()
        automatic=AutoRepair(chooser=chooser or Mock(return_value=response()),maximum=maximum)
        messages=[];driver=BatchDriver(app)
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()), \
             patch('tacto.cli._indicator_for',return_value=contextlib.nullcontext()):
            code,learned=automatic.run(flow,{},['--helper'],driver=driver,run_flow=_run_flow,emit=messages.append)
        return code,learned,automatic,app,messages

    def test_agent_called_once_and_only_failed_suffix_is_replayed(self):
        code,learned,automatic,app,messages=self.execute()
        self.assertEqual(0,code);self.assertEqual('12',app.value)
        self.assertEqual([2,3],[a['element_index'] for a in app.calls])
        self.assertEqual('Two',learned.steps[1].selector.identifier)
        self.assertEqual('OldTwo',recipe().steps[1].selector.identifier)
        self.assertEqual(recipe().steps[1].safety,learned.steps[1].safety)
        self.assertEqual(1,automatic.calls)
        packet=automatic.chooser.call_args.args[0]
        self.assertNotIn('tree',packet);self.assertNotIn('steps',packet)
        self.assertTrue(any(m.startswith('TACTO_AUTO_REPAIR_RESUME ') for m in messages))

    def test_no_agent_on_success_or_unknown_effect_or_first_step_failure(self):
        good=recipe();good.steps[1].selector.identifier='Two'
        uncertain=recipe();uncertain.steps[1].selector.identifier='Two';uncertain.steps[1].postcondition.equals='wrong'
        first=recipe();first.steps=first.steps[1:]
        for flow,expected in ((good,0),(uncertain,1),(first,1)):
            code,learned,automatic,app,msg=self.execute(flow)
            self.assertEqual(expected,code);self.assertEqual(0,automatic.calls)

    def test_invalid_agent_or_decline_or_timeout_never_dispatches_suffix(self):
        for reply in (response('{"candidate":null}'),response('{"candidate":99}'),response('{"candidate":true}'),
                      response('{"candidate":2,"action":"click"}'),response('not-json'),response(None,status='timeout')):
            code,learned,automatic,app,msg=self.execute(chooser=Mock(return_value=reply))
            self.assertEqual(1,code);self.assertEqual([2],[a['element_index'] for a in app.calls])
            self.assertEqual('OldTwo',learned.steps[1].selector.identifier)

    def test_ui_change_during_model_call_prevents_resume(self):
        app=SignedCalculator()
        def choose(packet,timeout):app.value='changed';return response()
        code,learned,automatic,app,msg=self.execute(app=app,chooser=choose)
        self.assertEqual(1,code);self.assertEqual([2],[a['element_index'] for a in app.calls])

    def test_missing_verified_checkpoint_refuses_model(self):
        flow=recipe();flow.steps[0].postcondition=None
        code,learned,automatic,app,msg=self.execute(flow)
        self.assertEqual(1,code);self.assertEqual(0,automatic.calls)

    def test_verified_prefix_capture_is_preserved_in_continuation(self):
        flow=recipe();flow.steps[0].captures=[Capture(name='old',field=Selector(identifier='StandardInputView'))]
        flow.steps[1].postcondition.equals='{{old}}2'
        code,learned,automatic,app,msg=self.execute(flow)
        self.assertEqual(0,code);self.assertEqual('12',app.value)
        self.assertEqual([2,3],[a['element_index'] for a in app.calls])
        self.assertEqual('{{old}}2',learned.steps[1].postcondition.equals)

    def test_selector_choice_must_be_unique_in_full_tree_not_packet(self):
        class Ambiguous(SignedCalculator):
            @property
            def tree(self):return super().tree+'\n  9 Button Identifier: Two, Title: Two\n'
        code,learned,automatic,app,msg=self.execute(app=Ambiguous())
        self.assertEqual(1,code);self.assertEqual([2],[a['element_index'] for a in app.calls])

    def test_old_nth_override_cannot_bypass_fresh_uniqueness(self):
        class Duplicated(SignedCalculator):
            @property
            def tree(self):return super().tree+'\n  9 Button Identifier: Two, Title: Two\n'
        flow=recipe();flow.steps[1].selector.allow_nth=0
        code,learned,automatic,app,msg=self.execute(flow,app=Duplicated())
        self.assertEqual(1,code);self.assertEqual(1,automatic.calls)
        self.assertEqual([2],[a['element_index'] for a in app.calls])

    def test_candidate_title_disambiguates_duplicate_identifier(self):
        class SharedIdentifier(SignedCalculator):
            @property
            def tree(self):return super().tree+'\n  9 Button Identifier: Two, Title: Other action\n'
        code,learned,automatic,app,msg=self.execute(app=SharedIdentifier())
        self.assertEqual(0,code);self.assertEqual('Two',learned.steps[1].selector.title)
        self.assertEqual([2,3],[a['element_index'] for a in app.calls])

    def test_provider_uses_existing_signed_launcher_route(self):
        from tacto.auto_repair import choose_candidate
        with patch('tacto.authoring.reach_model_result',return_value=response()) as route:
            self.assertEqual('ok',choose_candidate({'candidates':[]},20).status)
        self.assertEqual('codex',route.call_args.kwargs['provider'])
        self.assertEqual(20,route.call_args.kwargs['timeout_seconds'])
        self.assertTrue(route.call_args.kwargs['expect_json'])

    def test_oversized_packet_refuses_without_model_call(self):
        flow=recipe()
        flow.steps[1].postcondition.equals='x'*9000
        code,learned,automatic,app,msg=self.execute(flow)
        self.assertEqual(1,code);self.assertEqual(0,automatic.calls)
        self.assertEqual([2],[a['element_index'] for a in app.calls])

    def test_missing_or_already_satisfied_result_check_refuses_repair(self):
        for post in (None, '1'):
            flow=recipe()
            if post is None:flow.steps[1].postcondition=None
            else:flow.steps[1].postcondition.equals=post
            code,learned,automatic,app,msg=self.execute(flow,chooser=Mock(return_value=response('{"candidate":1}')))
            self.assertEqual(1,code);self.assertEqual(0,automatic.calls)
            self.assertEqual([2],[a['element_index'] for a in app.calls])
            self.assertEqual('OldTwo',learned.steps[1].selector.identifier)

    def test_original_expanded_loop_is_not_an_automatic_repair_scope(self):
        for value in ('a','ab'):
            flow=recipe();flow.params={'items':value}
            flow.steps[0].action='read';flow.steps[0].selector=Selector(identifier='StandardInputView')
            flow.steps[0].postcondition.equals='0';flow.steps[0].for_each='{{items}}'
            code,learned,automatic,app,msg=self.execute(flow)
            self.assertEqual(1,code);self.assertEqual(0,automatic.calls)
            self.assertTrue(any('not_an_undispatched_static_selector_failure' in m for m in msg))

    def test_observation_activation_is_latched_and_blocks_model_and_suffix(self):
        class Activated(SignedCalculator):
            def __init__(self):super().__init__();self.reads=0
            def observe(self,*args,**kwargs):
                self.reads+=1;reply=super().observe(*args,**kwargs)
                if self.reads==2:reply['activated_target']=True
                return reply
        code,learned,automatic,app,msg=self.execute(app=Activated())
        self.assertEqual(1,code);self.assertEqual(0,automatic.calls)
        self.assertEqual([2],[a['element_index'] for a in app.calls])

    def test_failed_auto_repair_writes_only_terminal_packet(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest=Path(tmp)/'packet.json';app=SignedCalculator();automatic=AutoRepair(chooser=Mock(return_value=response('{"candidate":null}')))
            with contextlib.redirect_stdout(io.StringIO()),patch('tacto.cli._indicator_for',return_value=contextlib.nullcontext()):
                code,_=automatic.run(recipe(),{},['--helper','--repair-packet',str(dest)],driver=BatchDriver(app),run_flow=_run_flow,emit=lambda m:None)
            self.assertEqual(1,code);self.assertEqual('not_started',json.loads(dest.read_text())['effect'])
            self.assertEqual(0o600,dest.stat().st_mode&0o777)
            self.assertEqual([2],[a['element_index'] for a in app.calls])

    def test_cli_run_preserves_original_loop_scope_before_parameter_binding(self):
        from tacto.cli import main
        flow=recipe();flow.params={'items':'ab'};flow.steps[0].action='read'
        flow.steps[0].selector=Selector(identifier='StandardInputView');flow.steps[0].postcondition.equals='0'
        flow.steps[0].for_each='{{items}}';app=SignedCalculator();app.close=Mock()
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'flow.json';path.write_text(flow.to_json())
            with contextlib.redirect_stdout(io.StringIO()),patch('tacto.cli._make_driver',return_value=app), \
                 patch('tacto.auto_repair.choose_candidate') as chooser,patch('tacto.cli._indicator_for',return_value=contextlib.nullcontext()):
                self.assertEqual(1,main(['run',str(path),'--helper','--auto-repair','codex','-p','items=abc']))
                chooser.assert_not_called()
            app.close.assert_called_once()

    def test_duplicate_display_candidates_cannot_select_a_hidden_value_match(self):
        class SameLabels(SignedCalculator):
            @property
            def tree(self):
                return super().tree.replace('  4 Button',
                    '  9 Button Identifier: Two, Title: Two, Value: Old\n  4 Button')
        flow=recipe();flow.steps[1].selector.value='Old'
        code,learned,automatic,app,msg=self.execute(flow,app=SameLabels())
        self.assertEqual(1,code);self.assertEqual(1,automatic.calls)
        self.assertEqual([2],[a['element_index'] for a in app.calls])
        self.assertTrue(any('indistinguishable_candidates' in m for m in msg))

    def test_resolved_node_must_be_the_selected_node_even_with_stable_id_alias(self):
        class VolatileIDs(SignedCalculator):
            @property
            def tree(self):
                return super().tree.replace('Identifier: Two,','Identifier: Two;value:a,').replace('  4 Button',
                    '  9 Button Identifier: Two;value:b, Title: Two, Value: Old\n  4 Button')
        flow=recipe();flow.steps[1].selector.value='Old'
        code,learned,automatic,app,msg=self.execute(flow,app=VolatileIDs())
        self.assertEqual(1,code);self.assertEqual(1,automatic.calls)
        self.assertEqual([2],[a['element_index'] for a in app.calls])
        self.assertTrue(any('chosen_candidate_mismatch' in m for m in msg))

    def test_consequential_contract_does_not_transfer_to_a_new_selector(self):
        flow=recipe();flow.steps[1].safety.effects=['write_persistent']
        # Host-model result injection isolates the repair contract-transfer boundary.
        from tacto.flow import ReplayResult
        from dataclasses import asdict
        failed=ReplayResult();failed.ok=False
        failed.repair_context={'step_index':1,'action':'click','selector':asdict(flow.steps[1].selector),
            'candidates':[{'role':'Button','identifier':'Two'}],'effect':'not_started','error':'target_missing'}
        failed.log=[{'status':'ok','step':0,'verified':'postcond'}]
        automatic=AutoRepair(chooser=Mock(return_value=response()))
        def runner(segment,flags,**kwargs):kwargs['_on_result'](failed);return 1
        code,learned=automatic.run(flow,{},[],driver=BatchDriver(SignedCalculator()),run_flow=runner,emit=lambda m:None)
        self.assertEqual(1,code);self.assertEqual(0,automatic.calls)

    def test_terminal_suffix_packet_maps_to_original_step_index(self):
        flow=recipe();third=deepcopy(flow.steps[1]);third.selector.identifier='OldThree'
        third.postcondition.equals='123';flow.steps.append(third)
        automatic=AutoRepair(chooser=Mock(side_effect=[response(),response('{"candidate":null}')]))
        app=SignedCalculator()
        with tempfile.TemporaryDirectory() as tmp:
            packet=Path(tmp)/'packet.json'
            with contextlib.redirect_stdout(io.StringIO()),patch('tacto.cli._indicator_for',return_value=contextlib.nullcontext()):
                code,_=automatic.run(flow,{},['--helper','--repair-packet',str(packet)],driver=BatchDriver(app),run_flow=_run_flow,emit=lambda m:None)
            saved=json.loads(packet.read_text())
            self.assertEqual(2,saved['step_index']);self.assertEqual('OldThree',saved['selector']['identifier'])
            self.assertEqual(1,code);self.assertEqual('12',app.value)

    def test_unsupported_loop_does_not_export_a_bound_index_packet(self):
        flow=recipe();flow.steps[0].action='read';flow.steps[0].selector=Selector(identifier='StandardInputView')
        flow.steps[0].postcondition.equals='0';flow.steps[0].for_each='ab'
        automatic=AutoRepair(chooser=Mock(return_value=response()))
        with tempfile.TemporaryDirectory() as tmp:
            packet=Path(tmp)/'packet.json'
            with contextlib.redirect_stdout(io.StringIO()),patch('tacto.cli._indicator_for',return_value=contextlib.nullcontext()):
                code,_=automatic.run(flow,{},['--helper','--repair-packet',str(packet)],driver=BatchDriver(SignedCalculator()),run_flow=_run_flow,emit=lambda m:None)
            self.assertEqual(1,code);self.assertFalse(packet.exists());self.assertEqual(0,automatic.calls)

    def test_empty_wrapper_removal_remaps_choice_to_the_same_fresh_node(self):
        import re
        class Wrapper(SignedCalculator):
            def __init__(self):super().__init__();self.reindexed=False
            @property
            def tree(self):
                text=super().tree
                if not self.reindexed:
                    lines=text.splitlines();return lines[0]+'\n  101 Group\n'+'\n'.join('  '+line for line in lines[1:])
                return re.sub(r'(?m)^(\s*)(\d+)',lambda m:m[1]+str(int(m[2])+20),text)
            def act(self,obs,target,action,**kwargs):
                if self.reindexed:action={**action,'element_index':action['element_index']-20}
                return super().act(obs,target,action,**kwargs)
        app=Wrapper()
        def choose(packet,timeout):app.reindexed=True;return response()
        code,learned,automatic,app,msg=self.execute(app=app,chooser=choose)
        self.assertEqual(0,code);self.assertEqual('12',app.value)
        self.assertEqual([2,3],[a['element_index'] for a in app.calls])

    def test_global_budget_caps_model_calls(self):
        automatic=AutoRepair(maximum=1,chooser=Mock(return_value=response()))
        with contextlib.redirect_stdout(io.StringIO()),patch('tacto.cli._indicator_for',return_value=contextlib.nullcontext()):
            for _ in range(2):
                driver=BatchDriver(SignedCalculator())
                code,_=automatic.run(recipe(),{},['--helper'],driver=driver,run_flow=_run_flow,emit=lambda m:None)
        self.assertEqual(1,automatic.calls);self.assertEqual(1,code)

    def test_explicit_run_command_uses_same_automatic_path(self):
        app=SignedCalculator();app.close=Mock()
        with contextlib.redirect_stdout(io.StringIO()),patch('tacto.cli._make_driver',return_value=app), \
             patch('tacto.auto_repair.choose_candidate',return_value=response()), \
             patch('tacto.cli._indicator_for',return_value=contextlib.nullcontext()):
            self.assertEqual(0,_run_flow(recipe(),['--helper','--auto-repair','codex']))
        app.close.assert_called_once();self.assertEqual('12',app.value)

    def test_options_are_explicit_and_bounded(self):
        for args in (['--auto-repair','other'],['--repair-timeout','60'],['--auto-repair','codex','--max-repairs','0'],
                     ['--auto-repair','codex','--journal-dir','/tmp/path']):
            with self.assertRaises(RepairRefused):parse_options(args)
        clean,automatic=parse_options(['--helper','--auto-repair','codex','--max-repairs','3'])
        self.assertEqual(['--helper'],clean);self.assertEqual(3,automatic.maximum)


class AutoBatchTests(unittest.TestCase):
    def test_batch_learns_repaired_selector_for_later_jobs(self):
        # Exercise real replay engine with fake native driver and real batch framing.
        helper=BatchTests();doc=json.loads(recipe().to_json())
        doc['steps'].insert(0,calculator_example()['steps'][1])
        # Expand the reset step using original defaults before inserting it.
        doc['steps'][0]=json.loads(Flow.from_json(json.dumps(calculator_example())).to_json())['steps'][1]
        with tempfile.TemporaryDirectory() as tmp,patch('tacto.auto_repair.choose_candidate',return_value=response()) as chooser:
            packet=Path(tmp)/'packet.json'
            code,app,make,msg=helper.execute('{"params":{}}\n'*3,doc=doc,
                args=['--auto-repair','codex','--max-jobs','3','--repair-packet',str(packet)])
            self.assertFalse(packet.exists())
        self.assertEqual(0,code);self.assertEqual(1,chooser.call_count)
        results=[json.loads(m.removeprefix('TACTO_BATCH_RESULT ')) for m in msg if m.startswith('TACTO_BATCH_RESULT ')]
        self.assertEqual([1,0,0],[r['model_requests'] for r in results])
        self.assertEqual(3,len(results));self.assertEqual('12',app.value)

if __name__=='__main__':unittest.main()
