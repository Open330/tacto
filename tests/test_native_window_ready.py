import unittest
from tacto.native_window_ready import require_window_ready, WindowNotReady

class WindowReadyTests(unittest.TestCase):
    def setUp(self):
        self.tree={'window':{'AXModal':False,'AXMinimized':False,'AXRole':'AXWindow','AXChildren':['group']},
                   'group':{'AXRole':'AXGroup','AXChildren':[]}}
        self.errors={}
    def read(self,node,name):
        if (node,name) in self.errors:return self.errors[node,name],None
        return (0,self.tree[node][name]) if name in self.tree[node] else (-25205,None)
    def check(self,**kw):
        return require_window_ready('window',lambda:['window'],self.read,lambda a,b:a==b,**kw)
    def test_unsupported_sheets_requires_complete_tree(self):
        self.assertTrue(self.check())
        self.tree['group']['AXChildren']=['sheet'];self.tree['sheet']={'AXRole':'AXSheet'}
        with self.assertRaises(WindowNotReady):self.check()
    def test_failed_read_is_not_an_empty_tree(self):
        for node,key in (('group','AXChildren'),('group','AXRole'),('window','AXModal'),('window','AXSheets')):
            self.errors={(node,key):-25204}
            with self.subTest(key=key),self.assertRaises(WindowNotReady):self.check()
    def test_budget_and_cycle_refuse(self):
        with self.assertRaises(WindowNotReady):self.check(max_nodes=1)
        self.tree['group']['AXChildren']=['group']
        with self.assertRaises(WindowNotReady):self.check(max_nodes=20)
    def test_modal_minimized_and_malformed_values_refuse(self):
        for key,value in (('AXModal',True),('AXModal',0),('AXMinimized',True),('AXSheets','')):
            self.setUp();self.tree['window'][key]=value
            with self.subTest(key=key,value=value),self.assertRaises(WindowNotReady):self.check()
    def test_window_replacement_during_read_refuses(self):
        calls=iter([['window'],['replacement']])
        with self.assertRaisesRegex(WindowNotReady,'identities changed'):
            require_window_ready('window',lambda:next(calls),self.read,lambda a,b:a==b)

    def test_order_only_change_is_distinguished_but_still_refused(self):
        self.tree['second'] = dict(self.tree['window'], AXChildren=[])
        calls=iter([['window','second'],['second','window']])
        with self.assertRaisesRegex(WindowNotReady,'order changed'):
            require_window_ready('window',lambda:next(calls),self.read,lambda a,b:a==b)

    def test_duplicate_other_window_is_not_reported_as_order_only_change(self):
        self.tree['second'] = dict(self.tree['window'], AXChildren=[])
        self.tree['third'] = dict(self.tree['window'], AXChildren=[])
        calls=iter([['window','second','second'],['second','window','third']])
        with self.assertRaisesRegex(WindowNotReady,'identities changed'):
            require_window_ready('window',lambda:next(calls),self.read,lambda a,b:a==b)

    def test_order_diagnostic_comparisons_are_bounded_for_large_window_list(self):
        nodes=['window']+[f'other-{i}' for i in range(64)]
        for node in nodes[1:]:self.tree[node]=dict(self.tree['window'],AXChildren=[])
        calls=iter([nodes,list(reversed(nodes))]); comparisons=[]
        def equal(a,b):
            comparisons.append((a,b));return a==b
        with self.assertRaisesRegex(WindowNotReady,'identities changed'):
            require_window_ready('window',lambda:next(calls),self.read,equal)
        self.assertLessEqual(len(comparisons),len(nodes)+1)

    def test_late_modal_change_is_rechecked(self):
        def read(node,name):
            result=self.read(node,name)
            if node=='group':self.tree['window']['AXModal']=True
            return result
        with self.assertRaises(WindowNotReady):
            require_window_ready('window',lambda:['window'],read,lambda a,b:a==b)
