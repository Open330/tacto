"""Menu discovery is read-only, complete, bounded and process scoped."""
import importlib.util
import unittest
from unittest.mock import patch, Mock
from tacto.helper import HelperDriver, InheritedHelperDriver

class MenuItemsTransportTests(unittest.TestCase):
    def test_transports_preserve_explicit_root_and_path(self):
        for cls in (HelperDriver, InheritedHelperDriver):
            d=object.__new__(cls);d._send=Mock(return_value={'type':'menu_items'})
            d.menu_items('fixture')
            d._send.assert_called_once_with({'type':'menu_items','target':'fixture','path':[]})

class MenuItemsTraceTests(unittest.TestCase):
    def test_record_replay_preserves_path_and_rejects_divergence(self):
        from tacto.trace import Trace, TracingDriver, TraceDriver
        trace=Trace()
        inner=Mock();inner.capabilities=['menu_items','menu_state']
        inner.menu_items.return_value={'type':'menu_items','path':[], 'items':[{'title':'Probe','path':['Probe'],'enabled':None,'has_submenu':True}], 'complete':True}
        inner.menu_state.return_value={'type':'menu_state','path':['Probe','Read'],'enabled':False}
        recording=TracingDriver(inner,trace)
        root=recording.menu_items('fixture')
        state=recording.menu_state('fixture',['Probe','Read'])
        replay=TraceDriver(trace)
        self.assertEqual(replay.menu_items('other')['error'],'trace_divergence')
        self.assertEqual(replay.menu_items('fixture'),root)
        self.assertEqual(replay.menu_state('fixture',['Probe','Other'])['error'],'trace_divergence')
        self.assertEqual(replay.menu_state('fixture',['Probe','Read']),state)
        self.assertEqual(replay.menu_items('fixture')['error'],'trace_exhausted')

@unittest.skipUnless(importlib.util.find_spec('ApplicationServices'),'PyObjC unavailable')
class NativeMenuItemsTests(unittest.TestCase):
    def call(self, *, duplicate=False, enabled=True, changed=False, failed=False, missing=False, path=None):
        from tacto import driver as m
        d=object.__new__(m.Driver)
        nodes={0:{'AXMenuBar':1},1:{'AXChildren':[2]},2:{'AXTitle':'Probe','AXRole':'AXMenuBarItem','AXChildren':[3],'AXEnabled':True},
               3:{'AXRole':'AXMenu','AXChildren':[4,5]},4:{'AXRole':'AXMenuItem','AXTitle':'Read','AXEnabled':enabled,'AXChildren':[]},
               5:{'AXRole':'AXMenuItem','AXTitle':'Read' if duplicate else 'Write','AXEnabled':False,'AXChildren':[]}}
        def raw(el,key,out):
            return (-25212,None) if missing and key=='AXChildren' and el==1 else (-25204,None) if failed and key=='AXTitle' else (0,nodes[el].get(key))
        with patch.object(d,'_resolve',return_value=42), patch.object(m,'AXUIElementCreateApplication',return_value=0), \
             patch.object(m,'AXUIElementCopyAttributeValue',side_effect=raw), \
             patch.object(m,'AXUIElementGetAttributeValueCount',side_effect=lambda el,key,out:(-25212,None) if missing and el==1 else (0,len(nodes[el].get(key,[])))), \
             patch.object(m,'AXUIElementCopyAttributeValues',side_effect=lambda el,key,start,n,out:(0,nodes[el].get(key,[])[:n])), \
             patch.object(m,'AXUIElementSetMessagingTimeout',return_value=0), \
             patch.object(m,'CFGetTypeID',return_value=42), patch.object(m,'AXUIElementGetTypeID',return_value=42), \
             patch('tacto.native_process_identity.process_start_identity',side_effect=[1,2] if changed else [1,1]), \
             patch.object(m,'AXUIElementPerformAction') as dispatch:
            result=d.menu_items('fixture',path)
            dispatch.assert_not_called()
            return result

    def test_root_children_and_unknown_enabled(self):
        root=self.call()
        self.assertTrue(root['complete'])
        self.assertEqual(root['items'],[{'title':'Probe','path':['Probe'],'enabled':True,'has_submenu':True}])
        rows=self.call(path=['Probe'],enabled=None)
        self.assertIsNone(rows['items'][0]['enabled'])
        self.assertIs(rows['items'][0]['has_submenu'],False)
        self.assertIs(rows['items'][1]['enabled'],False)

    def test_unobserved_parent_is_not_an_empty_complete_menu(self):
        result=self.call(missing=True)
        self.assertEqual(result['error'],'menu_state_unavailable')
        self.assertNotIn('complete',result)

    def test_no_partial_listing_on_ambiguity_error_or_process_change(self):
        for kwargs,error in [({'duplicate':True},'menu_ambiguous'),({'failed':True},'menu_read_failed'),({'changed':True},'target_changed')]:
            result=self.call(path=['Probe'],**kwargs)
            self.assertEqual(result['error'],error)
            self.assertNotIn('items',result)
