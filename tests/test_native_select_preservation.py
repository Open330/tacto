"""Real Python select methods over fake AX objects; never sends native input."""
from contextlib import ExitStack
import importlib.util
from types import SimpleNamespace
import unittest
from unittest.mock import patch


@unittest.skipUnless(importlib.util.find_spec('ApplicationServices'), 'PyObjC unavailable')
class SelectPreservationTests(unittest.TestCase):
    def setUp(self):
        from tacto import driver as m
        self.m = m
        self.driver = object.__new__(m.Driver)
        self.driver._select = None
        self.control, self.menu, self.item = object(), object(), object()
        self.obs = SimpleNamespace(pid=4321, obs_id='observed', elements=[self.control])
        self.opened = True
        self.selected = 'Alpha'
        self.actions = []
        self.attributes = {
            self.control: {'AXRole':'AXPopUpButton','AXIdentifier':'choice','AXEnabled':True},
            self.menu: {'AXChildren':[self.item]},
            self.item: {'AXTitle':'Beta','AXEnabled':True},
        }
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name, value in [('_attr', lambda el,key:self.attributes.get(el,{}).get(key)),
                            ('_elem_label',lambda el:'Choice'),
                            ('CFEqual',lambda a,b:a is b), ('_aborted_now',lambda:False),
                            ('AXUIElementPerformAction',self.perform)]:
            self.stack.enter_context(patch.object(m,name,side_effect=value))
        self.stack.enter_context(patch.object(m.time,'sleep'))
        self.stack.enter_context(patch.object(self.driver,'_frontmost',return_value=False))
        self.stack.enter_context(patch.object(self.driver,'_app_menus',side_effect=lambda pid:[self.menu] if self.opened else []))
        self.stack.enter_context(patch.object(self.driver,'_reread_selection',side_effect=lambda *a:self.selected))

    def perform(self, element, action):
        self.actions.append((element,action))
        if element is self.control and action == 'AXShowMenu': self.opened = True
        if element is self.menu and action == 'AXCancel': self.opened = False
        if element is self.item and action == 'AXPress': self.selected = 'Beta'
        return 0

    def pin(self):
        self.driver._select = self.m._OpenSelect('opened',4321,0,self.control,'choice',
                                               'AXPopUpButton','Choice',self.menu)
        self.driver._custom_selects(self.obs)

    def test_split_refusal_preserves_menu_then_fresh_observation_can_choose(self):
        self.pin()
        with self.assertRaises(self.m._AfterOpening):
            self.driver._select_custom_option(self.obs,{'element_index':0,'option_label':'Missing'})
        self.assertEqual([],self.actions,'refusal must not mutate the menu')
        self.assertTrue(self.opened)
        self.obs.obs_id = 'observed-again'
        metadata = self.driver._custom_selects(self.obs)
        self.assertEqual('open',metadata['0']['state'])
        result = self.driver._select_custom_option(self.obs,{'element_index':0,'option_label':'Beta'})
        self.assertEqual('Beta',result['selected'])
        self.assertEqual([(self.item,'AXPress')],self.actions)

    def test_direct_refusal_does_not_cancel_or_claim_no_effect_after_opening(self):
        self.opened = False
        with self.assertRaises(self.m._AfterOpening) as raised:
            self.driver._select_option(self.obs,{'element_index':0,'option_label':'Missing'})
        self.assertEqual('unknown',raised.exception.effect)
        self.assertEqual([(self.control,'AXShowMenu')],self.actions)
        self.assertTrue(self.opened)

    def test_success_with_menu_still_open_does_not_dispatch_cleanup(self):
        self.pin()
        result = self.driver._select_custom_option(self.obs,{'element_index':0,'option_label':'Beta'})
        self.assertEqual('Beta',result['selected'])
        self.assertEqual([(self.item,'AXPress')],self.actions)
        self.assertTrue(result.get('menu_left_open'))

    def test_press_error_does_not_claim_zero_selections(self):
        self.pin()
        def failed_press(element, action):
            self.actions.append((element,action))
            if element is self.item:
                self.selected='Beta'  # callback changed state before reporting an error
                self.opened=False
                return -25200
            return 0
        with patch.object(self.m,'AXUIElementPerformAction',side_effect=failed_press):
            with self.assertRaises(self.m._AfterOpening) as raised:
                self.driver._select_custom_option(self.obs,{'element_index':0,'option_label':'Beta'})
        self.assertEqual('unknown',raised.exception.effect)
        facts=getattr(raised.exception,'facts',{})
        self.assertNotEqual(0,facts.get('selections_made'))
        self.assertIsNot(True,facts.get('menu_open'))
        self.assertEqual([(self.item,'AXPress')],self.actions)

    def test_multiple_new_menus_are_not_cancelled(self):
        for method in (self.driver._open_select,self.driver._select_option):
            with self.subTest(method=method.__name__):
                self.actions.clear()
                self.driver._select=None
                with patch.object(self.driver,'_app_menus',side_effect=[[],[self.menu,object()]]):
                    with self.assertRaises(self.m._AfterOpening):
                        method(self.obs,{'element_index':0,'option_label':'Missing'})
                self.assertEqual([(self.control,'AXShowMenu')],self.actions)

    def test_other_app_observation_does_not_forget_menu_left_open(self):
        self.pin()
        self.driver._select_custom_option(self.obs,{'element_index':0,'option_label':'Beta'})
        other=SimpleNamespace(pid=9876,obs_id='other-app',elements=[])
        self.driver._custom_selects(other)
        self.obs.obs_id='back-to-owned-app'
        state=self.driver._custom_selects(self.obs)
        self.assertEqual('unsupported',state['0']['state'])
        self.assertEqual([(self.item,'AXPress')],self.actions)

    def test_direct_refusal_reports_remaining_menu_as_unsupported(self):
        self.opened=False
        with self.assertRaises(self.m._AfterOpening):
            self.driver._select_option(self.obs,{'element_index':0,'option_label':'Missing'})
        state=self.driver._custom_selects(self.obs)
        self.assertEqual('unsupported',state['0']['state'])

    def test_second_app_open_cannot_discard_first_app_menu(self):
        self.pin()
        original=self.driver._select
        other=SimpleNamespace(pid=9876,obs_id='other',elements=[self.control])
        with patch.object(self.driver,'_app_menus',side_effect=lambda pid:[self.menu] if pid==4321 else []):
            with self.assertRaises(self.m.NotStarted):
                self.driver._open_select(other,{'element_index':0})
        self.assertIs(original,self.driver._select)
        self.assertEqual([],self.actions)

    def test_open_and_direct_select_do_not_reopen_spent_menu(self):
        self.pin()
        self.driver._select_custom_option(self.obs,{'element_index':0,'option_label':'Beta'})
        self.actions.clear()
        for method in (self.driver._open_select,self.driver._select_option):
            with self.subTest(method=method.__name__):
                with self.assertRaises(self.m.NotStarted):
                    method(self.obs,{'element_index':0,'option_label':'Beta'})
        self.assertEqual([],self.actions)
