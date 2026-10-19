"""Replay cannot widen a pinned document on polling, failure or a stale token."""
import unittest
from unittest.mock import Mock, patch
from tacto.document_scope import DocumentScopedDriver, DocumentScopeUnavailable
from tacto.helper import HelperDriver

TARGET='com.apple.TextEdit';PATH='/owned/document.txt'
CAPS={'evidence.document_text.path_selector','observe.document_token'}


class ScopedDriverTests(unittest.TestCase):
    def test_host_metadata_capability_is_not_advertised_by_replay_only_scope(self):
        inner = self.inner()
        inner.capabilities = CAPS | {'evidence.document_attachment_metadata'}
        driver = DocumentScopedDriver(inner, TARGET, PATH)
        self.assertNotIn('evidence.document_attachment_metadata', driver.capabilities)

    def inner(self):
        d=Mock(capabilities=CAPS)
        d.document_text.return_value={'type':'document_text','document_path':PATH,
            'document_token':'pin','text':'owned','text_complete':True}
        d.observe.return_value={'type':'observation','document_token':'pin','obs_id':'fresh',
            'tree':'0 Window Title: "owned", Document: "/owned/document.txt"\n  1 TextArea Value: owned'}
        d.act.return_value={'type':'acted'}
        return d

    def test_real_replay_reads_and_reobserves_only_pinned_body(self):
        from tacto.flow import Flow,Step,Capture,Selector,replay
        inner=self.inner()
        inner.observe.return_value['tree']='0 Window Title: "owned", Document: "/owned/document.txt"\n  1 TextArea Value: owned, Identifier: body'
        driver=DocumentScopedDriver(inner,TARGET,PATH)
        field=Selector(role='TextArea',identifier='body',ancestor=PATH)
        from tacto.contract import Envelope
        safety=Envelope.minimal().contract_for({'read'},'read owned document')
        flow=Flow('read_owned',TARGET,[Step('read',safety=safety,captures=[Capture('body',field=field)]),
                                      Step('read',safety=safety,captures=[Capture('body',field=field)])],schema=2)
        result=replay(driver,flow)
        self.assertTrue(result.ok,result.log)
        self.assertEqual('owned',result.log[-1]['captured']['body'])
        for call in inner.observe.call_args_list:self.assertEqual('pin',call.kwargs['document_token'])

    def test_cli_replay_uses_scoped_driver_and_bad_flags_never_construct(self):
        from tacto.cli import _run_flow
        from tacto.flow import Flow,Step
        from tacto.contract import Envelope
        from contextlib import redirect_stdout
        import io
        flow=Flow('read_owned',TARGET,[Step('read',safety=Envelope.minimal().contract_for({'read'},'read'))],schema=2)
        inner=self.inner()
        with patch('tacto.cli._make_driver',return_value=inner),redirect_stdout(io.StringIO()):
            self.assertEqual(0,_run_flow(flow,['--document',PATH,'--no-indicator']))
        for call in inner.observe.call_args_list:self.assertEqual('pin',call.kwargs['document_token'])
        for args in (['--document'],['--document','relative'],['--document',PATH,'--all-windows'],
                     ['--document',PATH,'--document',PATH]):
            with patch('tacto.cli._make_driver') as make,redirect_stdout(io.StringIO()):
                self.assertEqual(2,_run_flow(flow,args))
            make.assert_not_called()

    def test_scope_rejects_global_actions_even_if_backend_would_accept(self):
        inner=self.inner();inner.capabilities=CAPS|{'action.read','action.menu','action.menu.focused_document','action.double_click'}
        driver=DocumentScopedDriver(inner,TARGET,PATH);driver.observe(TARGET)
        self.assertNotIn('action.menu',driver.capabilities)
        self.assertNotIn('action.menu.focused_document',driver.capabilities)
        self.assertIn('action.read',driver.capabilities)
        for action in ({'name':'menu','element_index':0,'path':['File','Close']},
                       {'name':'click','element_index':99},{'name':'click','element_index':True}):
            self.assertEqual('error',driver.act('fresh',TARGET,action)['type'])
        inner.act.assert_not_called()

    def test_exact_path_save_requires_dedicated_capability_and_never_widens(self):
        from tacto.existing_file_receipt import SAVE_PATH
        inner=self.inner();inner.capabilities=CAPS|{'action.menu.document_token_save','action.menu.focused_document'}
        driver=DocumentScopedDriver(inner,TARGET,PATH);driver.observe(TARGET)
        self.assertIn('action.menu.focused_document',driver.capabilities)
        good={'name':'menu','path':SAVE_PATH,'focused_document':PATH}
        self.assertEqual('acted',driver.act('fresh',TARGET,good)['type'])
        inner.act.assert_called_once_with('fresh',TARGET,good)
        inner.act.reset_mock()
        for action in ({**good,'focused_document':'/other'},
                       {**good,'path':['File',['Save As…','Save']]},
                       {**good,'element_index':0}, {'name':'menu','path':SAVE_PATH}):
            self.assertEqual('error',driver.act('fresh',TARGET,action)['type'])
        inner.act.assert_not_called()
        self.assertEqual(1,inner.document_text.call_count)
        self.assertEqual(2,inner.observe.call_count)

    def test_repeated_observations_and_polling_keep_one_token(self):
        inner=self.inner();driver=DocumentScopedDriver(inner,TARGET,PATH)
        driver.observe(TARGET,require_window=False)
        result=driver.wait_until(TARGET,lambda tree:'owned' in tree)
        self.assertEqual('observation',result['type'])
        self.assertEqual(1,inner.document_text.call_count)
        for call in inner.observe.call_args_list:
            self.assertEqual('pin',call.kwargs['document_token'])

    def test_refused_observation_invalidates_action_without_repinning(self):
        for reply in ({'type':'error','error':'no_window_content'},
                      {'type':'observation','document_token':'foreign','obs_id':'fresh','tree':'private'}):
            inner=self.inner();driver=DocumentScopedDriver(inner,TARGET,PATH)
            driver.observe(TARGET)
            inner.observe.return_value=reply
            self.assertEqual('error',driver.observe(TARGET)['type'])
            self.assertEqual('error',driver.act('fresh',TARGET,{'name':'click','element_index':1})['type'])
            inner.act.assert_not_called()
            self.assertEqual(1,inner.document_text.call_count)
            self.assertEqual(3,inner.observe.call_count)

    def test_other_target_path_token_or_observation_never_dispatches(self):
        inner=self.inner();driver=DocumentScopedDriver(inner,TARGET,PATH)
        driver.observe(TARGET)
        inner.reset_mock()
        for target,token in [('other',None),(TARGET,'foreign')]:
            self.assertEqual('error',driver.observe(target,document_token=token)['type'])
        self.assertEqual('error',driver.act('stale',TARGET,{'name':'read'})['type'])
        for kwargs in ({'document_path':'/other'},{'document_token':'foreign'}):
            self.assertEqual('error',driver.document_text(TARGET,**kwargs)['type'])
        inner.observe.assert_not_called();inner.act.assert_not_called();inner.document_text.assert_not_called()

    def test_receipt_reads_are_token_selected_and_changed_identity_is_refused(self):
        inner=self.inner();driver=DocumentScopedDriver(inner,TARGET,PATH)
        driver.document_text(TARGET,document_path=PATH)
        inner.document_text.assert_called_with(TARGET,document_token='pin',max_utf16_units=1000000)
        inner.document_text.return_value['document_path']='/other'
        self.assertEqual('error',driver.document_text(TARGET)['type'])

    def test_failed_pin_is_not_an_app_wide_driver(self):
        inner=self.inner();inner.document_text.return_value['text_complete']=False
        with self.assertRaises(DocumentScopeUnavailable):DocumentScopedDriver(inner,TARGET,PATH)
        inner.observe.assert_not_called()

    def test_scoped_helper_unhide_retry_preserves_selector_on_wire(self):
        driver=object.__new__(HelperDriver)
        driver.capabilities=CAPS;driver._last_action_at=0
        driver._send=Mock(side_effect=[{'type':'error','error':'no_window_content'},
            {'type':'observation','document_token':'pin','obs_id':'fresh','tree':'owned'}])
        with patch('tacto.helper._explain_no_window',return_value={'reason':'app_hidden'}), \
             patch('tacto.helper._unhide_target',return_value=True):
            self.assertEqual('observation',driver.observe(TARGET,document_token='pin')['type'])
        self.assertEqual(2,driver._send.call_count)
        for call in driver._send.call_args_list:self.assertEqual('pin',call.args[0]['document_token'])


if __name__=='__main__':unittest.main()
