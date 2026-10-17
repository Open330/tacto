"""Unavailable AX menu state must not be reported as enabled."""
import importlib.util
import unittest
from unittest.mock import patch

@unittest.skipUnless(importlib.util.find_spec('ApplicationServices'),'PyObjC unavailable')
class MenuStateTests(unittest.TestCase):
    def test_disabled_background_menu_hands_off_without_dispatch(self):
        from types import SimpleNamespace
        from tacto import driver as m
        for enabled, front, modal, handoff in (
                (False, False, None, True),
                (False, True, None, False),
                (False, False, object(), False),
                (True, False, None, False)):
            with self.subTest(enabled=enabled, front=front, modal=bool(modal)):
                d = object.__new__(m.Driver)
                obs = SimpleNamespace(pid=42, obs_id='o1', lines=[])
                d._latest = {'fixture': obs}
                with patch.object(d, '_showing_control'), \
                     patch.object(d, '_menu_item', return_value=(object(), ['Probe', 'Command'])), \
                     patch.object(m, '_attr', return_value=enabled), \
                     patch.object(d, '_modal_sheet', return_value=modal), \
                     patch.object(d, '_frontmost', return_value=front), \
                     patch.object(m, '_aborted_now', return_value=False), \
                     patch.object(m, 'AXUIElementPerformAction', return_value=0) as dispatch:
                    result = d.act('o1', 'fixture', {'name': 'menu', 'path': ['Probe', 'Command']})
                self.assertEqual(handoff, bool(result.get('needs_foreground')))
                if enabled:
                    self.assertEqual('acted', result['type'])
                    dispatch.assert_called_once()
                else:
                    self.assertEqual('not_started', result['effect'])
                    dispatch.assert_not_called()
                if handoff:
                    self.assertIn('앞으로', result['detail'])
                elif modal:
                    self.assertIn('대화상자', result['detail'])

    def test_menu_act_requires_boolean_enabled_before_dispatch(self):
        from types import SimpleNamespace
        from tacto import driver as m
        d=object.__new__(m.Driver)
        for enabled in (None,1,0,'true',False,True):
            with self.subTest(enabled=enabled), patch.object(d,'_menu_item',return_value=(object(),['Probe','Command'])), \
                 patch.object(m,'_attr',return_value=enabled), patch.object(d,'_modal_sheet',return_value=None), \
                 patch.object(d,'_frontmost',return_value=True), \
                 patch.object(m,'AXUIElementPerformAction',return_value=0) as dispatch:
                if enabled is True:
                    d._perform(SimpleNamespace(pid=42,lines=[]),{'name':'menu','path':['Probe','Command']})
                    dispatch.assert_called_once()
                else:
                    with self.assertRaises(m.NotStarted):
                        d._perform(SimpleNamespace(pid=42,lines=[]),{'name':'menu','path':['Probe','Command']})
                    dispatch.assert_not_called()

    def test_duplicate_titles_and_alternatives_are_ambiguous(self):
        from tacto import driver as m
        d=object.__new__(m.Driver)
        for titles,path in ((['Save','Save'],['Save']), (['Save','저장'],[['Save','저장']])):
            def read(el,key):
                if key=='AXMenuBar':return 1
                if key=='AXChildren':return [2,3]
                if key=='AXRole':return 'AXMenuItem'
                if key=='AXTitle':return titles[el-2]
            with patch.object(m,'AXUIElementCreateApplication',return_value=0):
                with self.assertRaisesRegex(RuntimeError,'여러 항목'):
                    d._menu_item(42,path,read=read)

    def test_only_observed_boolean_is_a_menu_enabled_witness(self):
        from tacto import driver as m
        d=object.__new__(m.Driver);item=object()
        for value in (True,False,None,1,0,'true'):
            with self.subTest(value=value),patch.object(d,'_resolve',return_value=42) as resolve, \
                 patch.object(d,'_menu_item',return_value=(item,['Probe','Command'])), \
                 patch.object(m,'AXUIElementCopyAttributeValue',side_effect=lambda el,key,out:(0,value if key=='AXEnabled' else None)), \
                 patch.object(m,'AXUIElementPerformAction') as act, \
                 patch.object(m,'AXUIElementSetMessagingTimeout',return_value=0):
                result=d.menu_state('fixture',['Probe','Command'])
                if type(value) is bool:
                    self.assertEqual('menu_state',result['type'])
                    self.assertIs(value,result['enabled'])
                else:
                    self.assertEqual('menu_state_unavailable',result['error'])
                    self.assertNotIn('enabled',result)
                act.assert_not_called()
                resolve.assert_called_once_with("fixture", launch=False)

    def test_actual_traversal_stops_reads_on_cancel_budget_or_deadline(self):
        from tacto import driver as m
        d=object.__new__(m.Driver)
        for failure in ('cancel', 'children', 'reads', 'timeout', 'ax_error', 'none'):
            with self.subTest(failure=failure):
                state={'stop':False,'clock':0.0,'reads':[]}
                def attr(element,key):
                    state['reads'].append((element,key))
                    if failure=='cancel': state['stop']=True
                    if failure=='timeout': state['clock']=10.0
                    if key=='AXMenuBar': return 1
                    if key=='AXChildren': return [2]*5001 if failure=='children' else list(range(2,5002)) if failure=='reads' else [element+1]
                    if key=='AXRole': return 'AXMenuItem'
                    if key=='AXTitle': return 'Probe' if element==2 else 'Command'
                    if key=='AXEnabled': return True
                    if key=='AXFrontmost': return False
                with patch.object(d,'_resolve',return_value=42), \
                     patch.object(m,'AXUIElementCreateApplication',return_value=0), \
                     patch.object(m,'AXUIElementCopyAttributeValue',side_effect=lambda el,key,out:(-25204,None) if failure=='ax_error' else (0,attr(el,key))), \
                     patch.object(m,'_aborted_now',side_effect=lambda:state['stop']), \
                     patch.object(m.time,'monotonic',side_effect=lambda:state['clock']), \
                     patch('tacto.model_budget.remaining_model_seconds',return_value=5), \
                     patch.object(m,'AXUIElementSetMessagingTimeout',return_value=0), \
                     patch.object(m,'AXUIElementGetAttributeValueCount',side_effect=lambda el,key,out:(0,len(attr(el,key)))), \
                     patch.object(m,'AXUIElementCopyAttributeValues',side_effect=lambda el,key,start,n,out:(0,attr(el,key)[:n])):
                    result=d.menu_state('fixture',['Probe','Command'])
                if failure=='none':
                    self.assertIs(result['enabled'],True)
                    self.assertIs(result['target_ax_frontmost'],False)
                else:
                    self.assertEqual(result['error'],{'cancel':'aborted_by_user',
                        'children':'menu_read_budget_exceeded','reads':'menu_read_budget_exceeded','ax_error':'menu_read_failed','timeout':'menu_read_timeout'}[failure])
                    if failure in ('cancel','timeout'):
                        self.assertEqual(state['reads'],[(0,'AXMenuBar')])

    def test_invalid_path_is_rejected_before_target_resolution(self):
        from tacto import driver as m
        d=object.__new__(m.Driver)
        for path in (None, '', [], ['x']*7, [['x']*9], ['\n'], ['x'*201]):
            with self.subTest(path=path), patch.object(d,'_resolve') as resolve:
                self.assertEqual(d.menu_state('fixture',path)['error'],'invalid_menu_path')
                resolve.assert_not_called()

    def test_timeout_setup_failure_never_reads_and_reset_failure_is_not_success(self):
        from tacto import driver as m
        d=object.__new__(m.Driver)
        for mode in ('setup','reset'):
            with self.subTest(mode=mode), patch.object(d,'_resolve',return_value=42), \
                 patch.object(d,'_menu_item',return_value=(0,['Probe'])), \
                 patch.object(m,'AXUIElementCreateApplication',return_value=0), \
                 patch.object(m,'AXUIElementCopyAttributeValue',return_value=(0,True)) as copy, \
                 patch.object(m,'AXUIElementSetMessagingTimeout',side_effect=lambda el,t:-1 if (t>0)==(mode=='setup') else 0), \
                 patch.object(m,'_aborted_now',return_value=False):
                result=d.menu_state('fixture',['Probe'])
                self.assertEqual(result['error'],'menu_timeout_unavailable' if mode=='setup' else 'menu_timeout_reset_failed')
                self.assertEqual(copy.call_count,0 if mode=='setup' else 1)

    def test_menu_resolution_short_circuits_and_diagnostics_do_not_reread(self):
        from tacto import driver as m
        from unittest.mock import Mock
        d=object.__new__(m.Driver)
        reader=Mock(side_effect=lambda el,key:([1]+list(range(2,5000))) if el==0 and key=='AXChildren' else 'AXMenu' if key=='AXRole' else [10])
        self.assertEqual(d._menu_children(0,read=reader),[10])
        self.assertEqual(reader.call_count,3)
        titles=[]
        def read(el,key):
            if key=='AXMenuBar':return 1
            if key=='AXChildren':return [2,3]
            if key=='AXRole':return 'AXMenuItem'
            if key=='AXTitle':titles.append(el);return str(el)
        with patch.object(m,'AXUIElementCreateApplication',return_value=0):
            with self.assertRaises(RuntimeError):d._menu_item(42,['missing'],read=read)
        self.assertEqual(titles,[2,3])
