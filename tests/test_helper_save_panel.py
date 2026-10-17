"""Read-only requests reject ambiguous values before reaching the helper."""
from types import SimpleNamespace
import unittest
from tacto.helper import HelperDriver, InheritedHelperDriver


class HelperSavePanelTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.answer = {'type': 'save_panel', 'authorizes_save': False}
        self.driver = SimpleNamespace(capabilities={'evidence.save_panel'},
            _send=lambda message: self.calls.append(message) or self.answer)

    def read(self, obs='o1', target='owned', index=0, **kwargs):
        return HelperDriver.save_panel(self.driver, obs, target, index, **kwargs)

    def test_invalid_inputs_never_reach_transport(self):
        for index in (True, -1, 5000, 1.0, '1', None):
            with self.subTest(index=index):
                self.assertEqual(self.read(index=index)['effect'], 'not_started')
        for kwargs in ({'require_format': 1}, {'panel_token': ''}, {'panel_token': True},
                       {'panel_token': 'x' * 129}, {'document_token': 'foreign'}):
            self.assertEqual(self.read(**kwargs)['effect'], 'not_started')
        for token in ('', True, 'x' * 129):
            self.assertEqual(self.read(target='com.apple.TextEdit', document_token=token)['effect'], 'not_started')
        for obs in ('', ' ', 'x' * 129, None):
            self.assertEqual(self.read(obs=obs)['effect'], 'not_started')
        self.driver.capabilities.clear()
        self.assertEqual(self.read()['effect'], 'not_started')
        self.assertEqual(self.calls, [])

    def test_pinned_request_and_response_forwarded_on_both_transports(self):
        for implementation in (HelperDriver, InheritedHelperDriver):
            result = implementation.save_panel(self.driver, 'o1', 'owned', 7,
                panel_token='pinned', require_format=True)
            self.assertIs(result, self.answer)
            self.assertEqual(self.calls[-1], {'type': 'save_panel', 'target': 'owned',
                'obs_id': 'o1', 'window_element_index': 7, 'require_format': True, 'panel_token': 'pinned'})
            result = implementation.save_panel(self.driver, 'o2', 'com.apple.TextEdit', 0,
                document_token='document', panel_token='panel')
            self.assertIs(result, self.answer)
            self.assertEqual(self.calls[-1]['document_token'], 'document')

    def test_guarded_click_requires_explicit_host_approval_and_capability(self):
        action = {'name': 'click', 'element_index': 7}
        self.driver.capabilities.add('action.click.save_panel_guard')
        for kwargs in ({}, {'risk_class': 'preapproved', 'approved': 1},
                       {'risk_class': 'none', 'approved': True},
                       {'risk_class': 'confirm', 'approved': False}):
            result = HelperDriver.act(self.driver, 'o1', 'owned', action,
                                      save_panel_token='panel', **kwargs)
            self.assertEqual(result['effect'], 'not_started')
        for token in ('', True, 'x' * 129):
            result = HelperDriver.act(self.driver, 'o1', 'owned', action,
                save_panel_token=token, risk_class='preapproved', approved=True)
            self.assertEqual(result['effect'], 'not_started')
        for malformed in ({'name':'click','element_index':True},
                          {'name':'click','element_index':-1},
                          {'name':'click','point':{'x':1,'y':1}},
                          {'name':'click','element_index':7,'extra':0},
                          {'name':'press_key','element_index':7,'key':'Tab'}):
            self.assertEqual(HelperDriver.act(self.driver, 'o1', 'owned', malformed,
                save_panel_token='panel', risk_class='preapproved', approved=True)['effect'], 'not_started')
        self.driver.capabilities.clear()
        self.assertEqual(HelperDriver.act(self.driver, 'o1', 'owned', action,
            save_panel_token='panel', risk_class='preapproved', approved=True)['effect'], 'not_started')
        self.assertEqual(self.calls, [])

    def test_guarded_click_forwarded_by_both_signed_transports(self):
        self.driver.capabilities.add('action.click.save_panel_guard')
        for implementation in (HelperDriver, InheritedHelperDriver):
            result = implementation.act(self.driver, 'o1', 'owned',
                {'name':'click','element_index':7}, save_panel_token='panel',
                risk_class='preapproved', approved=True)
            self.assertIs(result, self.answer)
            self.assertEqual(self.calls[-1]['save_panel_token'], 'panel')
            self.assertGreater(self.calls[-1]['click_deadline_uptime'], 0)
