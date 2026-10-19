import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tacto.flow import Flow, replay
from tacto.flow_errors import feedback
from tacto.repair_packet import packet_for, patch_selector
from tacto.cli import main
from tests.test_flow_session import FlowSessionTests
from tests.test_flow_safety_defaults import Calculator
from tacto.command_help import calculator_example, session_guide


class FeedbackTests(unittest.TestCase):
    def test_field_feedback_does_not_echo_private_values(self):
        cases=[({'params':['PRIVATE_TEXT']},'params'),
               ({'steps':[{'click':'PRIVATE_TEXT','selector':{'title':'PRIVATE_TEXT'}}]},'steps[0].click'),
               ({'steps':[{'action':'PRIVATE_TEXT'}]},'steps[0].action'),
               ({'steps':[{'action':'set_value','params':['PRIVATE_TEXT']}]},'steps[0].params')]
        for changes,field in cases:
            doc=dict(calculator_example(),**changes)
            with self.assertRaises(ValueError) as caught:Flow.from_json(json.dumps(doc))
            message=feedback(caught.exception)
            self.assertIn(field,message);self.assertNotIn('PRIVATE_TEXT',message)
            self.assertLess(len(message),250)
    def test_invalid_file_refuses_before_native_driver(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'bad.json';path.write_text(json.dumps(dict(calculator_example(),params=['PRIVATE'])))
            output=io.StringIO()
            with patch('tacto.cli._make_driver',side_effect=AssertionError('native startup')),contextlib.redirect_stderr(output):
                self.assertEqual(2,main(['run',str(path),'--helper']))
            self.assertIn('params:',output.getvalue());self.assertNotIn('PRIVATE',output.getvalue())
    def test_reuse_guide_is_conditional(self):
        self.assertNotIn('Reusable template:',session_guide('app'))
        self.assertIn('Reusable template:',session_guide('app',reusable=True))


class SaveSessionTests(FlowSessionTests):
    def test_successful_session_saves_unbound_template_privately(self):
        doc=calculator_example();doc['params']={'answer':'408'}
        doc['steps'][-1]['postcondition']['equals']='{{answer}}'
        with tempfile.TemporaryDirectory() as tmp:
            dest=Path(tmp)/'saved.json'
            result,app,make,messages=self.run_with(doc,args=['com.apple.calculator','--helper','--reuse-guide','--save',str(dest)])
            self.assertEqual(0,result);self.assertEqual('408',app.value)
            saved=Flow.from_json(dest.read_text());self.assertEqual('{{answer}}',saved.steps[-1].postcondition.equals)
            self.assertEqual('408',saved.params['answer']);self.assertEqual(0o600,dest.stat().st_mode&0o777)
            self.assertIn('TACTO_FLOW_SAVED '+str(dest),messages)
            result,app,make,messages=self.run_with(doc,args=['com.apple.calculator','--helper','--save',str(dest)])
            self.assertEqual(2,result);make.assert_not_called()
    def test_failed_session_never_saves_or_resends(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest=Path(tmp)/'saved.json'
            result,app,make,messages=self.run_with(calculator_example(),args=['com.apple.calculator','--helper','--save',str(dest)],runner=lambda *a,**k:1)
            self.assertEqual(1,result);self.assertFalse(dest.exists())


class RepairTests(unittest.TestCase):
    def missing(self):
        doc=calculator_example();doc['steps']=doc['steps'][2:3]
        doc['steps'][0]['selector']['identifier']='OldOne'
        flow=Flow.from_json(json.dumps(doc));app=Calculator();result=replay(app,flow)
        self.assertFalse(result.ok);self.assertEqual([],app.calls)
        return flow,app,result
    def test_packet_has_only_failed_context_no_full_tree_or_other_steps(self):
        flow,app,result=self.missing();packet=packet_for(flow,result)
        self.assertEqual('not_started',packet['effect']);self.assertFalse(packet['automatic_resume'])
        self.assertNotIn('tree',packet);self.assertNotIn('steps',packet)
        self.assertLessEqual(len(packet['candidates']),6)
        self.assertTrue(any(c.get('identifier')=='One' for c in packet['candidates']))
    def test_offline_patch_changes_only_selector_preserves_contracts_and_params(self):
        flow,app,result=self.missing();flow.params={'amount':'11'}
        with tempfile.TemporaryDirectory() as tmp:
            src=Path(tmp)/'flow.json';packet_path=Path(tmp)/'packet.json';out=Path(tmp)/'patched.json'
            src.write_text(flow.to_json());packet_path.write_text(json.dumps(packet_for(flow,result)))
            self.assertEqual(0,patch_selector(src,packet_path,{'role':'Button','identifier':'One'},out))
            old=json.loads(flow.to_json());new=json.loads(Flow.from_json(out.read_text()).to_json())
            old['steps'][0]['selector']=new['steps'][0]['selector'];self.assertEqual(old,new)
            self.assertEqual([],app.calls);self.assertEqual(0o600,out.stat().st_mode&0o777)
            with self.assertRaises(OSError):patch_selector(src,packet_path,{'identifier':'One'},out)
    def test_unobserved_selector_and_stale_packet_are_refused(self):
        flow,app,result=self.missing()
        with tempfile.TemporaryDirectory() as tmp:
            src=Path(tmp)/'flow.json';packet_path=Path(tmp)/'packet.json';out=Path(tmp)/'patched.json'
            src.write_text(flow.to_json());packet_path.write_text(json.dumps(packet_for(flow,result)))
            with self.assertRaises(ValueError):patch_selector(src,packet_path,{'identifier':'NotObserved'},out)
            flow.steps[0].selector.identifier='Other';src.write_text(flow.to_json())
            with self.assertRaises(ValueError):patch_selector(src,packet_path,{'identifier':'One'},out)
            self.assertFalse(out.exists())
    def test_remaining_output_excludes_completed_prefix_and_refuses_capture_dependency(self):
        from tacto.flow import Capture, Selector
        flow,app,result=self.missing()
        flow.steps.insert(0,Flow.from_json(json.dumps(calculator_example())).steps[0])
        result.repair_context['step_index']=1
        with tempfile.TemporaryDirectory() as tmp:
            src=Path(tmp)/'flow.json';packet_path=Path(tmp)/'packet.json';out=Path(tmp)/'remaining.json'
            src.write_text(flow.to_json());packet_path.write_text(json.dumps(packet_for(flow,result)))
            patch_selector(src,packet_path,{'identifier':'One'},out,remaining=True)
            remaining=Flow.from_json(out.read_text())
            self.assertEqual(1,len(remaining.steps));self.assertEqual('One',remaining.steps[0].selector.identifier)
            self.assertEqual(flow.steps[1].safety,remaining.steps[0].safety)
            flow.steps[0].captures=[Capture(name='prior',field=Selector(identifier='StandardInputView'))]
            src.write_text(flow.to_json());blocked=Path(tmp)/'blocked.json'
            with self.assertRaises(ValueError):patch_selector(src,packet_path,{'identifier':'One'},blocked,remaining=True)
            self.assertFalse(blocked.exists());self.assertEqual([],app.calls)

    def test_unknown_completion_is_not_selector_repair(self):
        flow,app,result=self.missing();result.repair_context['effect']='unknown'
        self.assertIsNone(packet_for(flow,result))
    def test_run_writes_packet_without_retry_or_extra_observation(self):
        flow,app,result=self.missing()
        with tempfile.TemporaryDirectory() as tmp:
            src=Path(tmp)/'flow.json';dest=Path(tmp)/'packet.json';src.write_text(flow.to_json())
            with patch('tacto.cli._make_driver',return_value=app),patch('tacto.cli._indicator_for',return_value=contextlib.nullcontext()),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(1,main(['run',str(src),'--helper','--repair-packet',str(dest)]))
            self.assertEqual('target_missing',json.loads(dest.read_text())['error']);self.assertEqual([],app.calls)

if __name__=='__main__':unittest.main()
