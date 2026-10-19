"""Host evaluation hook never returns stale bindings or repeats a child Save."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock

spec=importlib.util.spec_from_file_location('native_recovery_interleave',
    Path(__file__).resolve().parents[1]/'examples/probes/native_recovery_interleave.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class InterleaveTests(unittest.TestCase):
    def setUp(self):
        self.state={'stage':'8','sheet':'false','accepted':'8'}
        self.original={'type':'observation','obs_id':'before-child','tree':'stage8'}
        self.fresh={'type':'observation','obs_id':'after-child','tree':'stage8'}
        self.driver=Mock();self.driver.observe.return_value=self.fresh
        self.child=Mock(return_value={'passed':True,'cleanup_status':'closed',
            'exact_saved_text':True,'evidence_error':None,'save_dispatch_type':'acted'})
        self.hook=module.OwnedTextEditInterleave(target='parent',driver=self.driver,
            receiving=lambda:dict(self.state),run_child=self.child)
        self.predicate=lambda tree:tree=='stage8'

    def test_child_receipt_requires_fresh_parent_observation(self):
        out=self.hook.after_wait('parent',self.predicate,self.original)
        self.assertIs(out,self.fresh);self.driver.observe.assert_called_once_with('parent','tree_full')
        self.assertEqual('completed',self.hook.rows[0]['phase'])
        self.hook.after_wait('parent',self.predicate,self.fresh)
        self.child.assert_called_once()

    def test_failed_child_latches_without_repeating_save(self):
        for error in (None,'raised','missing_evidence_status','interrupt'):
            with self.subTest(error=error):
                self.setUp()
                if error=='raised':self.child.side_effect=RuntimeError('child lost reply')
                elif error=='interrupt':self.child.side_effect=KeyboardInterrupt()
                elif error=='missing_evidence_status':self.child.return_value.pop('evidence_error')
                else:self.child.return_value['passed']=False
                failure = KeyboardInterrupt if error=='interrupt' else RuntimeError
                with self.assertRaises(failure):self.hook.after_wait('parent',self.predicate,self.original)
                with self.assertRaisesRegex(RuntimeError,'owned_interleave_failed'):
                    self.hook.after_wait('parent',self.predicate,self.original)
                self.child.assert_called_once();self.driver.observe.assert_not_called()
                self.assertEqual('failed',self.hook.rows[0]['phase'])

    def test_parent_change_and_failed_readback_stop_before_continuation(self):
        for changed in ('state','observation','predicate','after_reobserve'):
            with self.subTest(changed=changed):
                self.setUp()
                if changed=='state':
                    def child():
                        self.state['stage']='9';return {'passed':True,'cleanup_status':'closed',
                            'exact_saved_text':True,'evidence_error':None,'save_dispatch_type':'acted'}
                    self.child.side_effect=child
                elif changed=='observation':self.driver.observe.return_value={'type':'error'}
                elif changed=='after_reobserve':
                    def observe(*args):
                        self.state['stage']='9';return self.fresh
                    self.driver.observe.side_effect=observe
                else:self.driver.observe.return_value={**self.fresh,'tree':'stage9'}
                with self.assertRaises(RuntimeError):self.hook.after_wait('parent',self.predicate,self.original)
                with self.assertRaisesRegex(RuntimeError,'owned_interleave_failed'):
                    self.hook.after_wait('parent',self.predicate,self.original)
                self.child.assert_called_once()

    def test_unverified_and_other_app_observations_do_not_start_child(self):
        for target,response in (('other',self.original),('parent',{'type':'timeout'})):
            self.assertIs(response,self.hook.after_wait(target,self.predicate,response))
        self.state['stage']='7';self.hook.after_wait('parent',self.predicate,self.original)
        self.child.assert_not_called()
        self.state['stage']='8';self.state['sheet']='true'
        with self.assertRaises(RuntimeError):self.hook.after_wait('parent',self.predicate,self.original)
        self.child.assert_not_called()
