from dataclasses import replace
import hashlib
from pathlib import Path
import struct
import unittest
from unittest.mock import Mock, patch

from nlc.native_visual import NativeVisualCapture, NativeVisualError
from nlc.native_visual_actions import capture_window, NativeVisualClickExecutor, run_native_visual
from nlc.visual import plan_visual


def shot(identity='nv1'):
    png = b'\x89PNG\r\n\x1a\n' + struct.pack('>I', 13) + b'IHDR' + struct.pack('>II', 800, 400)
    return NativeVisualCapture(identity, 'o1', 'owned', 42, 17, 3,
        hashlib.sha256(png).hexdigest(), {'width':800, 'height':400, 'coordinate_space':'window_pixels'},
        1, 'helper_native_window_id', png, (100, 200, 400, 200))


class NativeVisualActions(unittest.TestCase):
    def setUp(self):
        self.driver = Mock()
        self.driver.capabilities={'action.click_point','click_point.window_bound','click_point.host_authorization'}
        self.driver.act.return_value = {'type':'acted','posted_events':2,'window_id':17,'obs_id':'o1',
            'mechanism':'pid_window_events','input_route':'window_bound_mouse','unverified':'observe_effect','reobserve_required':True}
        self.executor = NativeVisualClickExecutor(self.driver, 'owned')
        self.proposal = {'action':'click', 'x':560, 'y':240}

    def test_retina_image_pixels_convert_to_window_points_and_require_observation(self):
        capture=shot()
        with patch('nlc.native_visual_actions.capture_window', return_value=shot('fresh')):
            event=self.executor.click(capture, self.proposal, lambda *args: True)
        self.assertEqual('dispatched', event['effect'])
        self.assertEqual({'x':280, 'y':120}, event['window_point'])
        self.assertEqual({'name':'click_point','element_index':3,'point':{'x':280,'y':120}},
                         self.driver.act.call_args.args[2])
        self.assertIs(True, self.driver.act.call_args.kwargs['approved'])
        self.assertIs(True, event['reobserve_required'])
        self.assertNotIn('verified', event)

    def test_every_identity_geometry_or_pixel_drift_refuses_before_dispatch(self):
        changes=[{'window_id':18},{'pid':43},{'obs_id':'o2'},{'window_index':4},
                 {'window_frame':(101,200,400,200)},{'sha256':'changed'},
                 {'viewport':{'width':400,'height':200,'coordinate_space':'window_pixels'}}]
        for number, change in enumerate(changes):
            capture=shot('drift'+str(number))
            with patch('nlc.native_visual_actions.capture_window', return_value=replace(capture,**change)):
                event=self.executor.click(capture,self.proposal,lambda *args: True)
            self.assertEqual(('not_started','stale_capture'),(event['effect'],event['reason']))
        self.driver.act.assert_not_called()

    def test_no_authority_cancel_and_invalid_proposal_never_dispatch(self):
        for number,authorize in enumerate((None,lambda *args: False,lambda *args: 1)):
            event=self.executor.click(shot('auth'+str(number)),self.proposal,authorize)
            self.assertEqual('not_started',event['effect'])
        for number,x in enumerate((True,-1,800,float('nan'),float('inf'))):
            event=self.executor.click(shot('bad'+str(number)),{**self.proposal,'x':x},lambda *args: True)
            self.assertEqual('invalid_coordinates',event['reason'])
        with patch('nlc.native_visual_actions.capture_window') as capture:
            event=self.executor.click(shot('cancel'),self.proposal,lambda *args: True,cancelled=lambda:True)
            capture.assert_not_called()
        self.assertEqual('not_started',event['effect']);self.driver.act.assert_not_called()

    def test_backend_must_declare_all_native_point_guards_before_any_input(self):
        for number,caps in enumerate(((), {'action.click_point'}, {'action.click_point','click_point.window_bound'})):
            self.driver.capabilities=caps
            with patch('nlc.native_visual_actions.capture_window') as fresh:
                event=self.executor.click(shot('cap'+str(number)),self.proposal,lambda *args:True)
                fresh.assert_not_called()
            self.assertEqual('native_point_capability_missing',event['reason'])
        self.driver.act.assert_not_called()

    def test_uncertain_dispatch_is_one_use_and_never_retried(self):
        self.driver.act.side_effect=TimeoutError('after post')
        capture=shot('uncertain')
        with patch('nlc.native_visual_actions.capture_window',return_value=capture):
            event=self.executor.click(capture,self.proposal,lambda *args:True)
        self.assertEqual('unknown',event['effect'])
        again=NativeVisualClickExecutor(self.driver,'owned').click(capture,self.proposal,lambda *args:True)
        self.assertEqual('capture_already_used',again['reason']);self.driver.act.assert_called_once()

    def test_refused_dispatch_preserves_no_effect(self):
        self.driver.act.return_value={'type':'error','effect':'not_started','error':'target_changed'}
        with patch('nlc.native_visual_actions.capture_window',return_value=shot('fresh')):
            event=self.executor.click(shot('refused'),self.proposal,lambda *args:True)
        self.assertEqual(('not_started','target_changed'),(event['effect'],event['reason']))

    def test_public_capture_checks_mapping_digest_dimensions_and_removes_copy(self):
        capture=shot('read')
        paths=[]
        def read(target,**kwargs):
            paths.append(Path(kwargs['path']));paths[-1].write_bytes(capture.png)
            return {'type':'capture',**capture.to_dict()}
        self.driver.capture.side_effect=read
        actual=capture_window(self.driver,'o1','owned',window_index=3)
        self.assertEqual(capture.png,actual.png);self.assertFalse(paths[-1].exists())
        for change in ({'image_mapping':None},{'window_frame':None},{'sha256':'bad'},{'width':799},{'pid':True}):
            def bad(target,change=change,**kwargs):
                Path(kwargs['path']).write_bytes(capture.png)
                return {'type':'capture',**capture.to_dict(),**change}
            self.driver.capture.side_effect=bad
            with self.assertRaises(NativeVisualError):
                capture_window(self.driver,'o1','owned',window_index=3)

    def test_native_planner_uses_image_pixels_not_window_points(self):
        with patch('nlc.visual.query_visual',return_value={'answer':'{"action":"click","x":560,"y":240}'}) as query:
            result=plan_visual(shot('plan'),'click green',provider='claude',timeout=12)
        self.assertEqual(self.proposal,result)
        self.assertIn('image pixels of this single native window',query.call_args.args[1])
        self.assertEqual('claude',query.call_args.kwargs['provider'])

    def test_loop_only_host_verifies_and_replacement_window_stops(self):
        self.driver.observe.return_value={'type':'observation','obs_id':'o1','tree':'owned'}
        selector=Mock();selector.resolve.return_value=(3,'title')
        with patch('nlc.native_visual_actions.capture_window',return_value=shot('done')):
            result=run_native_visual(self.driver,'owned','green',selector,
                lambda *args:{'action':'done'},lambda *args:True,lambda *args:False)
        self.assertEqual('UNVERIFIED',result.state);self.driver.act.assert_not_called()
        with patch('nlc.native_visual_actions.capture_window',side_effect=[shot('one'),shot('fresh'),replace(shot('two'),window_id=18)]):
            result=run_native_visual(self.driver,'owned','green',selector,
                lambda *args:self.proposal,lambda *args:True,lambda *args:False)
        self.assertEqual(('REFUSED','native_window_replaced'),(result.state,result.reason))
        self.driver.act.assert_called_once()
        self.driver.reset_mock()
        with patch('nlc.native_visual_actions.capture_window',return_value=shot('verified')):
            result=run_native_visual(self.driver,'owned','green',selector,
                lambda *args:self.fail('planner called'),None,lambda *args:True)
        self.assertEqual('DONE',result.state);self.driver.act.assert_not_called()

    def test_zero_deadline_does_not_observe(self):
        result=run_native_visual(self.driver,'owned','green',Mock(),Mock(),Mock(),Mock(),timeout_seconds=0)
        self.assertEqual('STOPPED',result.state);self.driver.observe.assert_not_called()
