"""Receiving evidence is independent of an acted/posted response."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from examples.probes import signed_popover_receipt as probe

class PopoverReceiptTests(unittest.TestCase):
    def record(self):
        return {'nonce':'n'*32,'seq':1,'mode':'transient','popover_shown':True,
            'accepts_first_mouse':True,'popover_window_visible':True,'popover_closes':0,'activations':0,'window_keys':[True,False],
            'receivers':[{'downs':0,'ups':0},{'downs':0,'ups':0}],'window_events':[[],[]]}

    def test_callback_rejects_foreign_partial_boolean_counters_and_unbounded_events(self):
        value=self.record();data=json.dumps(value).encode()+b'\n'
        self.assertEqual(probe.callback(data+b'{"partial":','n'*32,'transient'),value)
        self.assertIsNone(probe.callback(b'{"partial":','n'*32,'transient'))
        for update in ({'nonce':'foreign'},{'seq':True},{'seq':0},{'mode':'clear'},
                       {'receivers':[{'downs':True,'ups':0},{'downs':0,'ups':0}]},
                       {'accepts_first_mouse':False},{'window_keys':[1,False]},{'popover_window_visible':1},
                       {'window_events':[[{}]*65,[]]}):
            with self.assertRaises(ValueError):probe.callback(json.dumps({**value,**update}).encode()+b'\n','n'*32,'transient')
        with self.assertRaises(ValueError):probe.callback(b' '*131073,'n'*32,'transient')

    def test_receipt_requires_fresh_exact_content_pair_no_other_receiver_or_bad_route(self):
        before=self.record();after=copy.deepcopy(before);after['seq']=2
        after['receivers'][0]={'downs':1,'ups':1}
        after['window_events'][0]=[{'phase':phase,'window_matches':True,'popover_shown':True,'receiver_hit':True} for phase in ('down','up')]
        self.assertTrue(probe.received_once(before,after,0))
        for change in ('stale','missing_up','duplicate','other_receiver','wrong_window'):
            invalid=copy.deepcopy(after)
            if change=='stale':invalid['seq']=before['seq']
            elif change=='missing_up':invalid['receivers'][0]['ups']=0
            elif change=='duplicate':invalid['receivers'][0]['downs']=2
            elif change=='other_receiver':invalid['receivers'][1]['downs']=1
            else:invalid['window_events'][0][0]['window_matches']=False
            self.assertFalse(probe.received_once(before,invalid,0),change)
        self.assertFalse(probe.received_once(before,None,0))

    def test_routing_refusal_cannot_promote_unknown_or_missing_receipt_to_verified(self):
        before=self.record();after=copy.deepcopy(before);after['seq']=2
        response={'type':'error','effect':'not_started','posted_or_attempted_events':0,
            'preparation_diagnostic':{'phase':'dispatch','reason':'target_window_not_focused','elapsed_ms':1}}
        self.assertTrue(probe.routing_refusal_verified(response,before,after))
        for change in ({'effect':'unknown'},{'type':'acted'},{'posted_or_attempted_events':1},
                       {'posted_or_attempted_events':False},{'preparation_diagnostic':None}):
            self.assertFalse(probe.routing_refusal_verified({**response,**change},before,after))
        self.assertFalse(probe.routing_refusal_verified(response,before,before))
        changed=copy.deepcopy(after);changed['receivers'][1]['downs']=1
        self.assertFalse(probe.routing_refusal_verified(response,before,changed))
        changed=copy.deepcopy(after);changed['window_events'][1].append({})
        self.assertFalse(probe.routing_refusal_verified(response,before,changed))

    def test_refusal_probe_and_preparation_modes_are_incompatible_before_native_bootstrap(self):
        with patch.object(probe.subprocess,'Popen') as launch,self.assertRaisesRegex(ValueError,'incompatible_probe_modes'):
            probe._run(None,{},prepared=True,routing_refusal=True)
        launch.assert_not_called()

    def test_report_reservation_refuses_existing_and_symlink_before_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'existing';path.write_text('KEEP')
            link=Path(directory)/'link';link.symlink_to(path)
            for output in (path,link):
                with patch.object(probe,'_run') as run,self.assertRaises(FileExistsError):probe.main(None,output)
                run.assert_not_called();self.assertEqual(path.read_text(),'KEEP')

    def test_interruption_preserves_partial_failed_cases_privately(self):
        def stop(driver,report,**kwargs):
            report['cases'].append({'mode':'transient','passed':False,'response':{'type':'acted'}})
            raise KeyboardInterrupt()
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'report'
            with patch.object(probe,'_run',side_effect=stop),self.assertRaises(KeyboardInterrupt):probe.main(None,output)
            report=json.loads(output.read_text());self.assertFalse(report['passed'])
            self.assertEqual(report['cases'][0]['response']['type'],'acted')
            self.assertEqual(output.stat().st_mode&0o777,0o600)
