"""Scoped sessions pin once, never widen after loss, and close on every exit."""
import contextlib
import io
import json
import os
import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from tacto.cli import _run_flow
from tacto.flow_session import run_session
from tests import test_document_scoped_driver as scoped
PATH, TARGET = scoped.PATH, scoped.TARGET


class DocumentSessionTests(unittest.TestCase):
    def inner(self):
        inner=scoped.ScopedDriverTests().inner()
        inner.observe.return_value['tree'] += ', Identifier: First Text View'
        return inner

    def run_session(self, inner, *, args=None, runner=None, doc=None):
        flow=doc or {'schema':2,'name':'read-owned','target':TARGET,
              'safety_defaults':{'effects':['read'],'reversibility':'reversible',
                                 'resource':{'object_type':'document','scope':'item'}},
              'steps':[{'action':'read','postcondition':{'field':{'role':'TextArea','identifier':'First Text View'},
                                                       'attribute':'Value','equals':'owned'}}]}
        make=Mock(return_value=inner);messages=[]
        read,write=os.pipe()
        os.write(write,(json.dumps(flow)+'\n').encode());os.close(write)
        with os.fdopen(read) as stream,contextlib.redirect_stdout(io.StringIO()), \
             patch('tacto.cli._make_driver',side_effect=AssertionError('second connection')), \
             patch('tacto.cli._indicator_for',return_value=contextlib.nullcontext()):
            code=run_session(args or [TARGET,'--helper','--document',PATH,'--guide'],
                             make_driver=make,run_flow=runner or _run_flow,stdin=stream,emit=messages.append)
        return code,make,messages

    def test_real_replay_uses_one_pin_and_one_connection(self):
        inner=self.inner()
        code,make,messages=self.run_session(inner)
        self.assertEqual(0,code)
        self.assertEqual(1,make.call_count)
        inner.document_text.assert_called_once_with(TARGET,document_path=PATH,max_utf16_units=1_000_000)
        self.assertGreater(len(inner.observe.call_args_list),2)
        for call in inner.observe.call_args_list:self.assertEqual('pin',call.kwargs['document_token'])
        inner.close.assert_called_once()
        self.assertTrue(any(x.startswith('TACTO_SESSION_READY') for x in messages))

    def test_generated_guide_flow_executes_on_the_same_scoped_driver(self):
        from tacto.command_help import session_guide
        doc=json.loads(session_guide(TARGET,document=PATH).splitlines()[1])
        doc['steps'][0]['postcondition']['equals']='owned'
        inner=self.inner();code,_,_=self.run_session(inner,doc=doc)
        self.assertEqual(0,code)
        self.assertEqual(1,inner.document_text.call_count)
        inner.close.assert_called_once()

    def test_guide_edit_keeps_named_selector_and_unverified_persistence(self):
        from tacto.command_help import session_guide
        doc=json.loads(session_guide(TARGET,document=PATH).splitlines()[1])
        step=doc['steps'][0]
        step.update(action='set_value',params={'value':'changed'})
        step['postcondition']['equals']='changed'
        step['safety']={'effects':['write_persistent'],'evidence':[{
            'source':'user_intent','field':'asked','value':'Type changed in the owned document',
            'covers':['write_persistent']}]}
        inner=self.inner()
        def receive(obs,target,action):
            inner.observe.return_value['tree']=inner.observe.return_value['tree'].replace('Value: owned','Value: changed')
            return {'type':'acted'}
        inner.act.side_effect=receive
        captured=io.StringIO()
        def runner(*args,**kwargs):
            with contextlib.redirect_stdout(captured):return _run_flow(*args,**kwargs)
        code,_,_=self.run_session(inner,doc=doc,runner=runner)
        self.assertEqual(1,code)  # body edit is not a persistence receipt
        self.assertIn('commit_unverified',captured.getvalue())
        inner.act.assert_called_once()
        self.assertEqual(1,inner.act.call_args.args[2]['element_index'])
        self.assertEqual(1,inner.document_text.call_count)
        inner.close.assert_called_once()

    def test_saved_flow_reports_scoped_replay_without_persisting_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            save=str(Path(tmp)/'read-owned.json');inner=self.inner()
            code,_,messages=self.run_session(inner,args=[TARGET,'--helper','--document',PATH,'--save',save])
            self.assertEqual(0,code)
            saved=json.loads(Path(save).read_text())
            self.assertNotIn('document_token',saved)
            scope=json.loads(next(x.removeprefix('TACTO_FLOW_SCOPE ') for x in messages if x.startswith('TACTO_FLOW_SCOPE ')))
            self.assertFalse(scope['scope_stored_in_flow'])
            self.assertEqual(['tacto','run',save,'--helper','--document',PATH],scope['replay_argv'])
            self.assertEqual(1,inner.document_text.call_count)
            inner.close.assert_called_once()

    def test_run_callback_receives_existing_wrapper_without_repin_flag(self):
        from tacto.document_scope import DocumentScopedDriver
        inner=self.inner();runner=Mock(return_value=0)
        code,_,_=self.run_session(inner,runner=runner)
        self.assertEqual(0,code)
        self.assertIsInstance(runner.call_args.kwargs['_driver'],DocumentScopedDriver)
        self.assertNotIn('--document',runner.call_args.args[1])
        self.assertEqual(1,inner.document_text.call_count)
        inner.close.assert_called_once()

    def test_pin_failure_closes_base_without_reading_other_windows(self):
        inner=self.inner();inner.document_text.return_value={'type':'error'}
        runner=Mock()
        code,_,messages=self.run_session(inner,runner=runner)
        self.assertEqual(1,code)
        inner.observe.assert_not_called();runner.assert_not_called();inner.close.assert_called_once()
        self.assertFalse(any(x.startswith('TACTO_SESSION_READY') for x in messages))

    def test_token_loss_during_replay_stops_without_repin_or_input(self):
        inner=self.inner();first=dict(inner.observe.return_value)
        inner.observe.side_effect=[first,first,{'type':'error','error':'document_scope_unavailable'}]
        code,_,_=self.run_session(inner)
        self.assertEqual(1,code)
        self.assertEqual(1,inner.document_text.call_count)
        inner.act.assert_not_called();inner.close.assert_called_once()
        for call in inner.observe.call_args_list:self.assertEqual('pin',call.kwargs['document_token'])

    def test_bad_document_flags_do_not_connect(self):
        for args in ([TARGET,'--helper','--document'],[TARGET,'--helper','--document','relative'],
                     [TARGET,'--helper','--document',PATH,'--document',PATH],
                     ['com.apple.finder','--helper','--document',PATH],
                     [TARGET,'--helper','--document',PATH,'--all-windows']):
            with self.subTest(args=args):
                inner=self.inner();code,make,_=self.run_session(inner,args=args)
                self.assertEqual(2,code);make.assert_not_called();inner.close.assert_not_called()

    def test_windows_document_flag_is_refused_before_connection(self):
        inner=self.inner()
        with patch('tacto.flow_session.sys.platform','win32'):
            code,make,_=self.run_session(inner,args=[TARGET,'--document',PATH])
        self.assertEqual(2,code);make.assert_not_called()
