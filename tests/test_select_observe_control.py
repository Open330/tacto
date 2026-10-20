import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import Mock, patch

from examples.probe_native_select import run_observe_control


@unittest.skipUnless(sys.platform=='darwin', 'native driver framework imports require macOS')
class SelectObserveControlTests(unittest.TestCase):
    def test_observations_require_same_owned_pid_closed_menu_and_unchanged_receiving(self):
        tree = ('0 Window\n  1 PopUpButton  Value: Alpha, Identifier: tacto-fixture-choice\n'
                '  2 StaticText  Value: Alpha, Identifier: tacto-fixture-result\n'
                '  3 StaticText  Value: 0, Identifier: tacto-fixture-count')
        for mode in ('normal','wrong_pid','menu_open','count_changed','input_changed'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root=Path(directory).resolve();root.chmod(0o700)
                driver=Mock()
                driver.act.side_effect=AssertionError('control must not dispatch input')
                driver.observe.return_value={'type':'observation','tree':tree.replace('Value: 0','Value: 1') if mode=='count_changed' else tree,
                    'custom_selects':{'1':{'state':'open' if mode=='menu_open' else 'closed'}}}
                audit=Mock();audit.__enter__=Mock(return_value=audit);audit.__exit__=Mock(return_value=False)
                audit.report.return_value={'status':'CHANGED' if mode=='input_changed' else 'UNCHANGED_SAMPLED'}
                with patch('tacto.driver.Driver') as native, patch('tacto.driver._attr',return_value=False), \
                     patch('tacto.driver.AXUIElementCreateApplication'), \
                     patch('tacto.input_audit_process.ProcessInputAudit',return_value=audit), \
                     patch('examples.probe_native_select.owned_popup_capabilities',return_value={'authorizes_input':False}), \
                     patch('examples.focus_diagnostics.FocusDiagnosticReader') as reader, patch('examples.probe_native_select.time.sleep'):
                    native.return_value._resolve.return_value=99 if mode=='wrong_pid' else 42
                    reader.return_value.output=str(root/'focus.jsonl')
                    report=run_observe_control(driver,output=root/'report.json',focus_diagnostics_dir=root,expected_pid=42)
                self.assertEqual(mode=='normal',report['passed'],report)
                driver.act.assert_not_called()
                self.assertEqual(0,report['GUI_input_requests'])
                if mode=='wrong_pid':driver.observe.assert_not_called()
                self.assertEqual(report,json.loads((root/'report.json').read_text()))

    def test_capability_reads_reject_wrong_owner_and_duplicate_controls_without_input(self):
        from examples.probe_native_select import owned_popup_capabilities
        nodes={'root':['one'],'one':[],'two':[]}
        def attribute(node,name):
            if name=='AXChildren':return nodes[node]
            if name=='AXIdentifier':return 'tacto-fixture-choice' if node!='root' else None
            if name=='AXRole':return 'AXPopUpButton' if node!='root' else 'AXApplication'
            self.fail('unexpected metadata read')
        for mode in ('normal','wrong_owner','duplicate','unreadable'):
            with self.subTest(mode=mode):
                nodes['root']=['one','two'] if mode=='duplicate' else ['one']
                def copy(node,name,out):
                    if mode=='unreadable' and name=='AXChildren':return -25204,None
                    return 0,attribute(node,name)
                with patch('ApplicationServices.AXUIElementCopyAttributeValue',side_effect=copy), \
                     patch('CoreFoundation.CFEqual',side_effect=lambda a,b:a==b), \
                     patch('ApplicationServices.AXUIElementGetPid',return_value=(0,99 if mode=='wrong_owner' else 42)), \
                     patch('ApplicationServices.AXUIElementCopyActionNames',return_value=(0,['AXPress'])), \
                     patch('ApplicationServices.AXUIElementIsAttributeSettable',return_value=(0,False)), \
                     patch('ApplicationServices.AXUIElementPerformAction',side_effect=AssertionError('input prohibited')), \
                     patch('ApplicationServices.AXUIElementSetAttributeValue',side_effect=AssertionError('mutation prohibited')):
                    if mode=='normal':
                        result=owned_popup_capabilities('root',42)
                        self.assertFalse(result['attributes']['AXValue']['settable'])
                        self.assertFalse(result['authorizes_input'])
                    else:
                        with self.assertRaises(RuntimeError):owned_popup_capabilities('root',42)

    def test_opening_diagnostic_dispatches_once_and_never_selects_after_audit_failure(self):
        from examples.probe_native_select import run_probe,TARGET
        tree=('1 PopUpButton  Identifier: tacto-fixture-choice\n'
              '2 StaticText  Value: Alpha, Identifier: tacto-fixture-result\n'
              '3 StaticText  Value: 0, Identifier: tacto-fixture-count')
        for mode in ('normal','input_changed','audit_unavailable','wrong_pid','opening_refused','opening_unobserved'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as directory:
                root=Path(directory).resolve();root.chmod(0o700)
                driver=Mock();count=[0]
                def observe(*args):
                    count[0]+=1
                    return {'type':'observation','obs_id':'o','tree':tree,
                            'custom_selects':{'1':{'state':'open' if count[0]>=3 and mode!='opening_unobserved' else 'closed'}}}
                driver.observe.side_effect=observe
                driver.act.return_value=({'type':'error','effect':'not_started'} if mode=='opening_refused'
                                         else {'type':'acted','opened':True})
                audit=Mock();audit.__enter__=Mock(return_value=audit);audit.__exit__=Mock(return_value=False)
                audit.report.return_value={'status':{'input_changed':'CHANGED','audit_unavailable':'UNVERIFIABLE'}.get(mode,'UNCHANGED_SAMPLED')}
                with patch('tacto.driver.Driver') as native,patch('tacto.driver._attr',return_value=False), \
                     patch('tacto.driver.AXUIElementCreateApplication'), \
                     patch('tacto.input_audit_process.ProcessInputAudit',return_value=audit), \
                     patch('examples.focus_diagnostics.FocusDiagnosticReader'),patch('examples.probe_native_select.time.sleep'):
                    native.return_value._resolve.return_value=99 if mode=='wrong_pid' else 42
                    report=run_probe(driver,focus_diagnostics_dir=root,opening_only=True,expected_pid=42)
                self.assertEqual(mode=='normal',report['passed'],report)
                self.assertEqual(0 if mode=='wrong_pid' else 1,driver.act.call_count)
                if driver.act.call_count:
                    self.assertEqual(('o',TARGET,{'name':'open_select','element_index':1}),driver.act.call_args.args)
                    self.assertFalse(report['selection_task_complete'])
                    self.assertEqual(1,len(report['steps']))

    def test_opening_diagnostic_requires_owned_pid_and_private_diagnostics(self):
        from examples.probe_native_select import run_probe
        driver=Mock()
        for config in ({'opening_only':True},{'opening_only':True,'expected_pid':True,'focus_diagnostics_dir':'/tmp'},
                       {'opening_only':'true'}):
            with self.subTest(config=config),self.assertRaises(ValueError):run_probe(driver,**config)
        driver.act.assert_not_called();driver.observe.assert_not_called()
