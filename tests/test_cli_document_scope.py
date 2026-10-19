"""CLI scope pins before observation; malformed replies never widen reads."""
import io
import json
from contextlib import redirect_stdout
import unittest
import unicodedata
from unittest.mock import Mock,patch

from tacto.cli import _document_observation,main

TARGET='com.apple.TextEdit';PATH='/Users/x/저장 검증.txt'
CAPS={'evidence.document_text.path_selector','observe.document_token'}


class ScopeTests(unittest.TestCase):
    def driver(self):
        driver=Mock(capabilities=CAPS)
        driver.document_text.return_value={'type':'document_text','document_path':unicodedata.normalize('NFD',PATH),
            'document_token':'native-token','text':'owned body','text_complete':True}
        driver.observe.return_value={'type':'observation','document_token':'native-token','tree':'owned only'}
        return driver

    def test_bounded_path_pin_then_scoped_observation_in_one_connection(self):
        driver=self.driver()
        self.assertEqual('owned only',_document_observation(driver,TARGET,PATH)['tree'])
        driver.document_text.assert_called_once_with(TARGET,document_path=PATH,max_utf16_units=1_000_000)
        driver.observe.assert_called_once_with(TARGET,'tree_full',document_token='native-token')

    def test_missing_capability_invalid_path_or_target_never_reads(self):
        for target,path,caps in [(TARGET,PATH,set()),('com.apple.finder',PATH,CAPS),
                                 (TARGET,'relative',CAPS),(TARGET,'/a/../b',CAPS),(TARGET,'/x\0',CAPS)]:
            with self.subTest(target=target,path=path):
                driver=self.driver();driver.capabilities=caps
                self.assertEqual('error',_document_observation(driver,target,path)['type'])
                driver.document_text.assert_not_called();driver.observe.assert_not_called()

    def test_bad_pin_never_observes(self):
        changes=[{'document_path':'/other.txt'},{'document_token':''},{'document_token':'x'*129},
                 {'text':None},{'text_complete':1},{'text_complete':False},{'type':'error'},
                 {'text':'🙂'*500001}]
        for change in changes:
            with self.subTest(change=change):
                driver=self.driver();driver.document_text.return_value.update(change)
                self.assertEqual('error',_document_observation(driver,TARGET,PATH)['type'])
                driver.observe.assert_not_called()

    def test_stale_pin_or_exception_never_falls_back(self):
        for problem in ('wrong_token','read_error'):
            driver=self.driver()
            if problem=='wrong_token':driver.observe.return_value['document_token']='foreign'
            else:driver.observe.side_effect=RuntimeError('private body must not escape')
            result=_document_observation(driver,TARGET,PATH)
            self.assertEqual('document_scope_unavailable',result['error'])
            self.assertNotIn('private',str(result));driver.observe.assert_called_once()

    def test_cli_prints_only_scoped_tree(self):
        driver=self.driver()
        with patch('tacto.cli._make_driver',return_value=driver),redirect_stdout(io.StringIO()) as output:
            self.assertEqual(0,main(['observe',TARGET,'--document',PATH,'--helper']))
        self.assertEqual('owned only',output.getvalue().strip())

    def test_json_observation_preserves_document_pin_without_widening(self):
        driver=self.driver()
        with patch('tacto.cli._make_driver',return_value=driver),redirect_stdout(io.StringIO()) as output:
            self.assertEqual(0,main(['observe',TARGET,'--document',PATH,'--helper','--json']))
        self.assertEqual('owned only',json.loads(output.getvalue())['tree'])
        driver.observe.assert_called_once_with(TARGET,'tree_full',document_token='native-token')

    def test_invalid_cli_combinations_never_construct_driver(self):
        for args in [['--document'],['--document',PATH,'--all-windows'],['--document','relative'],
                     ['--document',PATH,'--document',PATH]]:
            with patch('tacto.cli._make_driver') as driver,redirect_stdout(io.StringIO()):
                self.assertEqual(2,main(['observe',TARGET,*args]))
            driver.assert_not_called()


class PinDiagnosticTests(unittest.TestCase):
    def test_known_backend_refusals_keep_closed_cause_without_observe_or_input(self):
        from tacto.document_scope import DocumentScopedDriver,DocumentScopeUnavailable
        for detail,expected in [('document read deadline elapsed','read_deadline'),
                                ('AXSubrole unavailable: -25202','window_subrole_unreadable'),
                                ('AXStringForRange unavailable: -25212','text_range_unreadable'),
                                ('initial document selection is ambiguous or absent','selection_ambiguous_or_absent')]:
            d=Mock(capabilities=CAPS)
            d.document_text.return_value={'type':'error','error':'document_snapshot_unavailable','detail':detail}
            result=_document_observation(d,TARGET,PATH)
            self.assertEqual('pin_unavailable',result['reason_code'])
            self.assertEqual(expected,result['pin_reason_code'])
            self.assertEqual('not_started',result['effect'])
            with self.assertRaises(DocumentScopeUnavailable) as error:DocumentScopedDriver(d,TARGET,PATH)
            self.assertIn('pin_reason='+expected,str(error.exception))
            d.observe.assert_not_called();d.act.assert_not_called()

    def test_unknown_or_private_details_are_never_echoed_or_prefix_matched(self):
        from tacto.document_scope import pin_failure_reason
        for detail in ['PRIVATE','AXSubrole unavailable: -25202 PRIVATE','AXWindows unavailable\nPRIVATE',{},None]:
            d=Mock(capabilities=CAPS)
            d.document_text.return_value={'type':'error','error':'document_snapshot_unavailable','detail':detail}
            result=_document_observation(d,TARGET,PATH)
            self.assertNotIn('pin_reason_code',result)
            self.assertNotIn('PRIVATE',str(result))
        self.assertEqual('user_aborted',pin_failure_reason({'type':'error','error':'aborted_by_user','detail':'PRIVATE'}))

    def test_cli_prints_closed_cause_and_never_repeats_pin(self):
        d=Mock(capabilities=CAPS)
        d.document_text.return_value={'type':'error','error':'document_snapshot_unavailable','detail':'document traversal budget exceeded'}
        with patch('tacto.cli._make_driver',return_value=d),redirect_stdout(io.StringIO()) as out:
            self.assertEqual(1,main(['observe',TARGET,'--helper','--document',PATH]))
        self.assertIn('pin_reason=traversal_budget',out.getvalue())
        d.document_text.assert_called_once();d.observe.assert_not_called();d.act.assert_not_called()
