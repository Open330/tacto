"""The signed client refuses unapproved coordinates and preserves the deadline."""
import unittest
from unittest.mock import Mock, patch
from nlc.helper import InheritedHelperDriver


class HelperWindowPointTests(unittest.TestCase):
    def test_host_authorization_and_capability_are_required_before_send(self):
        driver=InheritedHelperDriver.__new__(InheritedHelperDriver)
        driver.capabilities={'action.click_point'};driver._send=Mock()
        action={'name':'click_point','element_index':0,'point':{'x':280,'y':120}}
        for risk,approved in [('none',False),('none',True),('confirm',False),('preapproved',1)]:
            reply=driver.act('owned','fixture',action,risk_class=risk,approved=approved)
            self.assertEqual(reply['effect'],'not_started')
        driver.capabilities=set()
        self.assertEqual(driver.act('owned','fixture',action,risk_class='preapproved',approved=True)['effect'],'not_started')
        driver._send.assert_not_called()

    def test_authorized_point_is_sent_once_with_caller_bounded_deadline(self):
        driver=InheritedHelperDriver.__new__(InheritedHelperDriver)
        driver.capabilities={'action.click_point'}
        driver._send=Mock(return_value={'type':'acted','unverified':'observe_effect'})
        action={'name':'click_point','element_index':0,'point':{'x':280,'y':120}}
        with patch('nlc.helper.time.monotonic',return_value=100),patch('nlc.model_budget.remaining_model_seconds',return_value=2):
            driver.act('owned','fixture',action,risk_class='preapproved',approved=True)
        driver._send.assert_called_once_with({'type':'act','obs_id':'owned','target':'fixture','action':action,
                                             'coordinate_authorized':True,'click_deadline_uptime':102})
