"""Preparing a window of the frontmost app must not take the person's typing."""
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch


@unittest.skipUnless(sys.platform == 'darwin', 'native driver requires macOS')
class ForegroundPreparationFocusTests(unittest.TestCase):
    def world(self, *, main=False, front=None, status=0, focused='field',
              owner='other', role='AXTextField', value='draft', settable=True):
        from tacto import driver
        window, other = object(), object()
        nodes = {'field': object(), 'none': None}
        element = nodes.get(focused, focused)
        attrs = {(id(window), 'AXMain'): main,
                 (id(element), 'AXWindow'): {'target': window, 'other': other}.get(owner),
                 (id(element), 'AXRole'): role, (id(element), 'AXValue'): value}
        app = object()
        def attr(node, name):
            if node is app and name == 'AXFocusedWindow':
                return {'target': window, 'other': other}.get(front)
            return attrs.get((id(node), name))
        patches = [patch.object(driver, '_attr', side_effect=attr),
                   patch.object(driver, 'AXUIElementCreateApplication', return_value=app),
                   patch.object(driver, 'AXUIElementCopyAttributeValue',
                                return_value=(status, element)),
                   patch.object(driver, 'AXUIElementIsAttributeSettable',
                                return_value=(0, settable)),
                   patch.object(driver, 'CFEqual', side_effect=lambda a, b: a is b),
                   patch.object(driver, 'CFGetTypeID', return_value=7),
                   patch.object(driver, 'AXUIElementGetTypeID', return_value=7)]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        return window

    def decide(self, **world):
        from tacto.driver import Driver
        return Driver._foreground_preparation(123, self.world(**world))

    def test_already_prepared_window_needs_no_write(self):
        self.assertEqual('prepared', self.decide(main=True, front='target'))
        self.assertEqual('write', self.decide(main=True, front='other', role='AXButton',
                                              value=None, settable=False))

    def test_editable_focus_elsewhere_is_not_taken(self):
        from tacto.driver import _NeedsForeground
        for world in ({'role': 'AXTextField'}, {'role': 'AXTextArea'},
                      {'role': 'AXGroup', 'value': 'text', 'settable': True}):
            with self.subTest(world=world), self.assertRaises(_NeedsForeground):
                self.decide(**world)

    def test_focus_inside_target_or_not_editable_or_absent_allows_write(self):
        self.assertEqual('write', self.decide(owner='target'))
        self.assertEqual('write', self.decide(role='AXButton', value=None, settable=False))
        self.assertEqual('write', self.decide(role='AXSlider', value=3, settable=True))
        self.assertEqual('write', self.decide(status=-25212, focused='none'))

    def test_unreadable_focus_is_refused_without_a_foreground_handoff(self):
        from tacto.driver import NotStarted, _NeedsForeground
        for world in ({'status': -25204}, {'status': -25205},
                      {'status': 0, 'focused': 'none'}, {'status': -25212}):
            with self.subTest(world=world), self.assertRaises(NotStarted) as error:
                self.decide(**world)
            self.assertNotIsInstance(error.exception, _NeedsForeground)

    def test_refusal_precedes_every_write_and_reports_needs_foreground(self):
        from tacto.driver import Driver, _NeedsForeground
        driver = Driver.__new__(Driver)
        with patch.object(Driver, '_frontmost', return_value=True), \
             patch.object(Driver, '_foreground_preparation',
                          side_effect=_NeedsForeground('editable focus')), \
             patch('tacto.driver._attr', return_value=False), \
             patch('tacto.driver.AXUIElementSetAttributeValue') as write:
            with self.assertRaises(_NeedsForeground):
                driver._prepare_window(SimpleNamespace(pid=123), object())
            write.assert_not_called()

    def test_prepared_window_returns_without_writing(self):
        from tacto.driver import Driver
        driver = Driver.__new__(Driver)
        with patch.object(Driver, '_frontmost', return_value=True), \
             patch.object(Driver, '_foreground_preparation', return_value='prepared'), \
             patch('tacto.driver._attr', return_value=False), \
             patch('tacto.driver.AXUIElementSetAttributeValue') as write:
            result = driver._prepare_window(SimpleNamespace(pid=123), object())
        write.assert_not_called()
        self.assertEqual([], result['wrote'])
        self.assertTrue(result['foreground_route'])


if __name__ == '__main__':
    unittest.main()
