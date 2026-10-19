"""A model claim and readback alone do not attest the physical select route."""
from copy import deepcopy
import unittest
from examples.probe_native_select import TARGET,_combo_model_receipt_valid,_record_native_call,_model_probe_failure_reasons,_record_receiving_check,_combo_scroll_progress,_require_combo_scroll_receiving,_expected_scroll_receiving_failure


class ComboModelReceiptTests(unittest.TestCase):
    def test_expected_stop_requires_failed_task_unchanged_state_and_no_later_input(self):
        geometry={'popup_id':17,'viewport':[0,0,20,10],'row':[0,10,10,8],'direction':'down'}
        report={'passed':False,'loop_ok':False,'source_stable':True,'provider_calls':[],
            'audit':{'status':'UNCHANGED_SAMPLED'},'readback':['Alpha','0'],
            'combo_readback':{'control_value':'Alpha','selected_index':'0','state':'open'},
            'events':[{'state':'dispatched'},{'state':'unknown'}],
            'native_receipts':[
                {'target':TARGET,'status':'returned','action':{'name':'open_select'},'result':{'type':'acted','opened':True}},
                {'target':TARGET,'status':'returned','action':{'name':'scroll','direction':'up','pages':1},
                 'result':{'type':'acted'},'receiving_check_error_class':'RuntimeError',
                 'scroll_evidence':{'scoped':True,'readback_before':['Alpha','0'],'readback_after':['Alpha','0'],
                    'before':geometry,'after':{**geometry,'row':[0,12,10,8]}}}]}
        self.assertTrue(_expected_scroll_receiving_failure(report,2))
        for calls in (True,1,3):self.assertFalse(_expected_scroll_receiving_failure(report,calls))
        for change in ({'passed':True},{'source_stable':False},{'readback':['Beta','1']},
                       {'provider_calls':[{'status':'ok'}]},{'audit':{'status':'INCONCLUSIVE'}},
                       {'native_receipts':report['native_receipts']+[{'action':{'name':'select_custom_option'}}]},
                       {'events':[{'state':'confirmed'}]}):
            self.assertFalse(_expected_scroll_receiving_failure({**report,**change},2))
        broken=deepcopy(report);broken['native_receipts'][1]['scroll_evidence']['after']={}
        self.assertFalse(_expected_scroll_receiving_failure(broken,2))

    def test_no_progress_after_returned_input_fails_receiving_check_without_erasing_receipt(self):
        receipts=[];raw={'type':'acted','obs_id':'s1','note':'not verified — observe again'}
        calls=[]
        def operation(*args):calls.append(args);return raw
        _record_native_call(receipts,operation,'s1',TARGET,{'name':'scroll','direction':'down'})
        geometry={'popup_id':17,'viewport':[0,0,20,10],'row':[0,30,10,8],'direction':'down'}
        proof={'scoped':True,'readback_before':['Alpha','0'],'readback_after':['Alpha','0'],
               'before':geometry,'after':deepcopy(geometry)}
        with self.assertRaisesRegex(RuntimeError,'model_scroll_receiving_unverified'):
            _record_receiving_check(receipts[0],lambda:_require_combo_scroll_receiving(proof,'down'))
        self.assertEqual(1,len(calls))
        self.assertEqual('returned',receipts[0]['status'])
        self.assertIs(raw,receipts[0]['result'])
        self.assertEqual('RuntimeError',receipts[0]['receiving_check_error_class'])

    def test_progress_requires_smaller_clipped_distance_in_the_requested_direction(self):
        before={'popup_id':17,'viewport':[0,0,20,10],'row':[0,30,10,8],'direction':'down'}
        after={'popup_id':17,'viewport':[0,0,20,10],'row':[0,15,10,8],'direction':'down'}
        self.assertTrue(_combo_scroll_progress(before,after,'down'))
        upward={**before,'row':[0,-20,10,8],'direction':'up'}
        self.assertTrue(_combo_scroll_progress(upward,{**after,'row':[0,-5,10,8],'direction':'up'},'up'))
        # Moving farther away, shrinking a row, drifting horizontally, or
        # claiming a direction without matching measured geometry cannot pass.
        for changed in ({'row':[0,35,10,8]},{'row':[1,15,10,8]},
                        {'row':[0,15,10,4]},{'direction':None},
                        {'viewport':[0,0,20,40]},{'popup_id':True},
                        {'row':[0,float('nan'),10,8]},
                        {'row':[0,-50,10,8],'direction':'up'},
                        {'row':[0,10**400,10,8]}):
            with self.subTest(changed=changed):self.assertFalse(_combo_scroll_progress(before,{**after,**changed},'down'))
        visible={**before,'row':[0,2,10,8],'direction':None}
        self.assertFalse(_combo_scroll_progress(visible,{**visible,'row':[0,1,10,8]},'down'))
        for malformed in ({},None,{'popup_id':17,'viewport':None,'row':[]}):
            self.assertFalse(_combo_scroll_progress(malformed,after,'down'))

    def test_post_input_read_failure_retains_returned_backend_receipt_and_stops(self):
        receipts=[];raw={'type':'acted','obs_id':'s1'}
        _record_native_call(receipts,lambda *a:raw,'s1',TARGET,{'name':'scroll'})
        def missing():raise RuntimeError('private receiving details')
        with self.assertRaises(RuntimeError):_record_receiving_check(receipts[0],missing)
        self.assertEqual('returned',receipts[0]['status'])
        self.assertIs(raw,receipts[0]['result'])
        self.assertEqual('RuntimeError',receipts[0]['receiving_check_error_class'])
        self.assertNotIn('private receiving details',str(receipts))

    def test_scrolling_requires_owned_progress_and_unchanged_independent_selection(self):
        opening={'target':TARGET,'action':{'name':'open_select'},'result':{'type':'acted','opened':True}}
        selection={'target':TARGET,'obs_id':'s3','action':{'name':'select_custom_option','option_label':'Beta'},
                   'result':{'type':'acted','selected':'Beta','input_route':'combo_popup_bound_click',
                             'selection_transition_verified':True,'posted_events':2,'obs_id':'s3'}}
        scroll={'target':TARGET,'action':{'name':'scroll','direction':'down'},'result':{'type':'acted'},
                'scroll_evidence':{'scoped':True,'readback_before':['Alpha','0'],'readback_after':['Alpha','0'],
                    'before':{'popup_id':17,'viewport':[0,0,20,10],'row':[0,10,10,8],'direction':'down'},
                    'after':{'popup_id':17,'viewport':[0,0,20,10],'row':[0,2,10,8],'direction':None}}}
        self.assertFalse(_combo_model_receipt_valid([opening,scroll,selection]))
        self.assertTrue(_combo_model_receipt_valid([opening,scroll,selection],allow_scrolling=True))
        for change in ({'scoped':False},{'readback_after':['Beta','1']},{'after':scroll['scroll_evidence']['before']},
                       {'after':{**scroll['scroll_evidence']['after'],'popup_id':18}}):
            broken=deepcopy(scroll);broken['scroll_evidence'].update(change)
            self.assertFalse(_combo_model_receipt_valid([opening,broken,selection],allow_scrolling=True))
        self.assertFalse(_combo_model_receipt_valid([opening,selection,scroll],allow_scrolling=True))

    def test_large_combo_requires_the_requested_option_and_independent_index(self):
        opening={'target':TARGET,'action':{'name':'open_select'},'result':{'type':'acted','opened':True}}
        selection={'target':TARGET,'obs_id':'s1','action':{'name':'select_custom_option','option_label':'Choice127'},
                   'result':{'type':'acted','selected':'Choice127','input_route':'combo_popup_bound_click',
                             'selection_transition_verified':True,'posted_events':2,'obs_id':'s1'}}
        self.assertTrue(_combo_model_receipt_valid([opening,selection],option='Choice127'))
        self.assertFalse(_combo_model_receipt_valid([opening,selection]))
        report={'loop_ok':True,'requested_option':'Choice127','requested_index':130,
                'readback':['Choice127','1'],'said':['Choice127'],'audit':{'status':'UNCHANGED_SAMPLED'},
                'source_stable':True,'prompt_metrics':[{'tokens':10}],'prompt_budget':2500,'combo':True,
                'combo_readback':{'control_value':'Choice127','selected_index':'130','state':'closed'},
                'native_receipts':[opening,selection],'visible_items':8}
        self.assertEqual([],_model_probe_failure_reasons(report))
        report['combo_readback']['selected_index']='1'
        self.assertIn('combo_readback_mismatch',_model_probe_failure_reasons(report))

    def test_goal_reached_does_not_hide_failed_input_audit(self):
        report={'loop_ok':True,'why':'목표에 도달했습니다','requested_option':'Beta',
                'readback':['Beta','1'],'said':['Beta'],'audit':{'status':'UNCHANGED_SAMPLED'},
                'source_stable':True,'prompt_metrics':[{'tokens':3}],'prompt_budget':10,'combo':False}
        self.assertEqual([],_model_probe_failure_reasons(report))
        self.assertEqual(['input_audit_not_unchanged'],_model_probe_failure_reasons({**report,'audit':{'status':'CHANGED'}}))
        self.assertEqual(['source_changed_or_unverified'],_model_probe_failure_reasons({**report,'source_stable':False}))
        self.assertEqual(['prompt_budget_unverified'],_model_probe_failure_reasons({**report,'prompt_metrics':[{'tokens':11}]}))

    def test_transport_exception_is_retained_without_inventing_a_backend_result(self):
        receipts=[]
        def failed(*args,**kwargs):raise RuntimeError('private error message')
        with self.assertRaises(RuntimeError):
            _record_native_call(receipts,failed,'o1',TARGET,{'name':'open_select'})
        self.assertEqual(1,len(receipts))
        self.assertEqual('RuntimeError',receipts[0]['exception_class'])
        self.assertEqual('attempted',receipts[0]['status'])
        self.assertNotIn('result',receipts[0])
        self.assertNotIn('private error message',str(receipts))
        response={'type':'acted'}
        returned=_record_native_call(receipts,lambda *args:response,'o2',TARGET,{'name':'read'})
        self.assertIs(response,returned)
        self.assertIs(response,receipts[1]['result'])
        self.assertEqual('returned',receipts[1]['status'])

    def test_complete_pair_and_receiving_transition_are_required(self):
        opening={'target':TARGET,'action':{'name':'open_select'},'result':{'type':'acted','opened':True}}
        receipt={'target':TARGET,'obs_id':'o1','action':{'name':'select_custom_option','option_label':'Beta'},
                 'result':{'type':'acted','selected':'Beta','input_route':'combo_popup_bound_click',
                           'selection_transition_verified':True,'posted_events':2,'obs_id':'o1'}}
        self.assertTrue(_combo_model_receipt_valid([opening,receipt]))
        for change in ({'type':'error'},{'selected':'Alpha'},{'input_route':'window_bound_element_click'},
                       {'selection_transition_verified':1},{'selection_transition_verified':False},
                       {'posted_events':1},{'posted_events':True},{'unverified':'observe_effect'},{'obs_id':'old'}):
            with self.subTest(change=change):
                modified=deepcopy(receipt);modified['result'].update(change)
                self.assertFalse(_combo_model_receipt_valid([opening,modified]))
        self.assertFalse(_combo_model_receipt_valid([]))
        self.assertFalse(_combo_model_receipt_valid([opening,receipt,receipt]))
        self.assertFalse(_combo_model_receipt_valid([opening,{**receipt,'target':'another_app'}]))
        self.assertFalse(_combo_model_receipt_valid([opening,{**receipt,'action':{'name':'select_custom_option','option_label':'Alpha'}}]))
        for action in ('press_key','set_value','click','open_select'):
            self.assertFalse(_combo_model_receipt_valid([opening,{'action':{'name':action}},receipt]))
        self.assertFalse(_combo_model_receipt_valid([receipt,opening]))
        self.assertFalse(_combo_model_receipt_valid([{**opening,'result':{'type':'acted','opened':1}},receipt]))
