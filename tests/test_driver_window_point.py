"""Public point actions require explicit host authorization and an observed window."""
import importlib.util
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


@unittest.skipUnless(importlib.util.find_spec('ApplicationServices'), 'PyObjC required')
class DriverWindowPointTests(unittest.TestCase):
    def setUp(self):
        from nlc.driver import Driver
        self.driver=Driver.__new__(Driver)
        self.window=object()
        self.obs=SimpleNamespace(target='fixture',pid=42,obs_id='owned',elements=[self.window])
        self.driver._latest={'fixture':self.obs}
        self.driver._last_action_at=0
        self.driver._showing_control=Mock()
        self.action={'name':'click_point','element_index':0,'point':{'x':280,'y':120}}

    def test_missing_or_nonboolean_host_authorization_cannot_dispatch(self):
        self.driver._perform=Mock()
        for risk,approved in [('none',False),('none',True),('preapproved',False),('preapproved',1),('confirm','yes')]:
            result=self.driver.act('owned','fixture',self.action,risk_class=risk,approved=approved)
            self.assertEqual(result['effect'],'not_started')
            self.assertEqual(result['error'],'coordinate_authorization_required')
        self.driver._perform.assert_not_called()

    def test_window_point_dispatch_preserves_authorized_parameters_and_partial_effect(self):
        from nlc.native_window_dispatch import WindowDispatchFailure
        with patch('nlc.driver._attr',return_value='AXWindow'), patch.object(self.driver,'_click_background_point') as click:
            click.return_value={'input_route':'window_bound_mouse','posted_events':2,
                'unverified':'observe_effect','reobserve_required':True}
            result=self.driver.act('owned','fixture',self.action,risk_class='preapproved',approved=True)
            self.assertEqual(result['type'],'acted')
            self.assertEqual(result['posted_events'],2)
            self.assertEqual(click.call_args.args[:3],(self.obs,self.window,(280.,120.)))
            click.side_effect=WindowDispatchFailure('stopped after down',attempted=1)
            result=self.driver.act('owned','fixture',self.action,risk_class='preapproved',approved=True)
            self.assertEqual((result['effect'],result['posted_or_attempted_events']),('unknown',1))

    def test_bad_shape_coordinates_and_nonwindow_target_refuse_before_dispatch(self):
        cases=[{**self.action,'window_index':0},{**self.action,'point':{'x':True,'y':1}},
               {**self.action,'point':{'x':float('nan'),'y':1}},
               {**self.action,'point':{'x':-1,'y':1}},
               {**self.action,'point':{'x':1}}, {**self.action,'element_index':True}]
        with patch('nlc.driver._attr',return_value='AXWindow'),patch.object(self.driver,'_click_background_point') as click:
            for action in cases:
                result=self.driver.act('owned','fixture',action,risk_class='preapproved',approved=True)
                self.assertEqual(result['effect'],'not_started',result)
            click.assert_not_called()
        with patch('nlc.driver._attr',return_value='AXButton'),patch.object(self.driver,'_click_background_point') as click:
            result=self.driver.act('owned','fixture',self.action,risk_class='preapproved',approved=True)
            self.assertEqual(result['effect'],'not_started')
            click.assert_not_called()

