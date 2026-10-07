"""Click deadlines cross the helper transport without renewing caller budgets."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from nlc.helper import InheritedHelperDriver
from nlc.model_budget import model_deadline


class HelperClickDeadlineTests(unittest.TestCase):
    def test_expired_request_never_reaches_transport(self):
        sent = []
        driver = SimpleNamespace(_send=lambda msg: sent.append(msg))
        with patch('time.monotonic', return_value=100), model_deadline(99):
            result = InheritedHelperDriver.act(driver, 'o1', 'fixture', {'name': 'click', 'element_index': 1})
        self.assertEqual(result['effect'], 'not_started')
        self.assertEqual(result['error'], 'input_deadline_expired')
        self.assertEqual(sent, [])

    def test_navigation_pair_shares_the_caller_deadline_without_sending_expired_input(self):
        sent=[]
        driver=SimpleNamespace(_send=lambda message:sent.append(message) or {'type':'acted'})
        action={'name':'press_key','element_index':3,'key':'Tab'}
        with patch('time.monotonic', return_value=100), model_deadline(101.25):
            InheritedHelperDriver.act(driver,'o1','fixture',action)
        self.assertEqual(sent[-1]['click_deadline_uptime'],101.25)
        self.assertEqual(sent[-1]['action'],action)
        with patch('time.monotonic', return_value=100), model_deadline(99):
            result=InheritedHelperDriver.act(driver,'o1','fixture',action)
        self.assertEqual(result['error'],'input_deadline_expired')
        self.assertEqual(len(sent),1)

    def test_absolute_deadline_is_capped_and_other_actions_do_not_claim_it(self):
        sent = []
        def send(msg):
            sent.append(msg)
            return {'type': 'acted'}
        driver = SimpleNamespace(_send=send)
        with patch('time.monotonic', return_value=100), model_deadline(101.25):
            InheritedHelperDriver.act(driver, 'o1', 'fixture', {'name': 'click', 'element_index': 1})
        self.assertEqual(sent[-1]['click_deadline_uptime'], 101.25)
        with patch('time.monotonic', return_value=100):
            InheritedHelperDriver.act(driver, 'o1', 'fixture', {'name': 'click', 'element_index': 1})
        self.assertEqual(sent[-1]['click_deadline_uptime'], 105)
        InheritedHelperDriver.act(driver, 'o1', 'fixture', {'name': 'read'})
        self.assertNotIn('click_deadline_uptime', sent[-1])
