"""An unread subtree cannot establish a unique replacement control."""
import importlib.util
import unittest
from unittest.mock import patch

@unittest.skipUnless(importlib.util.find_spec('ApplicationServices'), 'PyObjC unavailable')
class SelectionReadErrors(unittest.TestCase):
    def test_failed_subtree_does_not_hide_duplicate_identifier(self):
        from nlc import driver as module
        original, candidate, branch, hidden, root = (object() for _ in range(5))
        values = {root: {'AXRole':'AXApplication','AXChildren':[candidate,branch]},
                  candidate: {'AXRole':'AXPopUpButton','AXIdentifier':'choice','AXValue':'Beta','AXChildren':[]},
                  branch: {'AXRole':'AXGroup','AXChildren':[hidden]},
                  hidden: {'AXRole':'AXPopUpButton','AXIdentifier':'choice','AXValue':'Wrong','AXChildren':[]}}
        for failed_node, failed_key in ((branch,'AXChildren'), (hidden,'AXRole'), (hidden,'AXIdentifier')):
            def read(node,key,_):
                if node is failed_node and key==failed_key: return -25204,None
                return (0,values[node][key]) if key in values[node] else (-25205,None)
            with self.subTest(attribute=failed_key), \
                 patch.object(module,'AXUIElementCreateApplication',return_value=root), \
                 patch.object(module,'CFEqual',side_effect=lambda a,b:a is b), \
                 patch.object(module,'AXUIElementCopyAttributeValue',side_effect=read):
                result=module.Driver.__new__(module.Driver)._reread_selection(1,'AXPopUpButton','choice',original)
                self.assertIsNone(result,'an unread subtree must not certify unique identity')

    def test_native_nsarray_children_preserve_live_control_readback(self):
        from Foundation import NSArray
        from nlc import driver as module
        root, original = 'root', 'original'
        values = {root: {'AXRole':'AXApplication', 'AXChildren':NSArray.arrayWithArray_([original])},
                  original: {'AXRole':'AXPopUpButton', 'AXValue':'Beta'}}
        def read(node,key,_):
            return (0,values[node][key]) if key in values[node] else (-25205,None)
        with patch.object(module,'AXUIElementCreateApplication',return_value=root), \
             patch.object(module,'CFEqual',side_effect=lambda a,b:a==b), \
             patch.object(module,'AXUIElementCopyAttributeValue',side_effect=read):
            result=module.Driver.__new__(module.Driver)._reread_selection(1,'AXPopUpButton',None,original)
        self.assertEqual(result,'Beta')
