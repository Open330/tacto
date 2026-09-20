import dataclasses
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from nlc import native_mouse_events as mouse


class FakeBackend:
    def __init__(self, *, corrupt=False, fail_second=False):
        self.corrupt, self.fail_second = corrupt, fail_second
        self.created = []
        self.order = []

    def create(self, kind, window_id, local, number, timestamp, pressure):
        if self.fail_second and kind == 2:
            raise RuntimeError("second construction failed")
        event = dict(kind=kind, flags=0, event_number=number,
                     click_count=1, pressure=pressure)
        self.created.append((local, timestamp, event))
        return object(), event

    def configure(self, event, window_id, point, relative):
        event.update(point=point, window_point=(-1, -1))
        self.order.append("global")
        event.update(window_id=window_id, handling_window=window_id,
                     window_point=relative if not self.corrupt else (-1, -1))
        self.order.append("local")

    def read(self, event):
        return event


class NativeMouseEventsTests(unittest.TestCase):
    def test_invalid_inputs_never_load_backend(self):
        invalid = [
            dict(window_id=v) for v in (True, 0, -1, 1.0, "3", 2**32)
        ] + [dict(bounds=v) for v in ((0, 0, 0, 2), (0, 0, 2, -1),
             (0, 0, float("inf"), 2), (False, 0, 2, 2), (0, 0, 2),
             (1e308, 0, 1e308, 2))] + [dict(point=v) for v in (
             (True, 2), (float("nan"), 2), (101, 20), (30, 201), (-1, 20))
        ] + [dict(event_number=v) for v in (True, -1, 2**31, 2.0)]
        with patch.object(mouse, "_load_backend") as load:
            for change in invalid:
                with self.subTest(change=change), self.assertRaises(ValueError):
                    mouse.build_window_click(**(dict(window_id=3, bounds=(0, 0, 100, 200),
                                                     point=(30, 20)) | change))
            load.assert_not_called()

    def test_pair_coordinates_number_pressure_and_immutable_metadata(self):
        backend = FakeBackend()
        with patch.object(mouse, "_load_backend", return_value=backend):
            result = mouse.build_window_click(7, (120, 610, 620, 252), (150, 626), 91)
        self.assertEqual(result.window_point, (30, 16))
        self.assertEqual(backend.order, ["global", "local", "global", "local"])
        self.assertEqual([c[0] for c in backend.created], [(30, 236)] * 2)
        self.assertEqual([c[1] for c in backend.created], [result.timestamp] * 2)
        self.assertEqual((result.down["event_number"], result.up["event_number"]), (91, 91))
        self.assertEqual((result.down["pressure"], result.up["pressure"]), (1, 0))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.window_id = 9

    def test_local_reset_or_partial_pair_never_returns_prepared_click(self):
        for backend in (FakeBackend(corrupt=True), FakeBackend(fail_second=True)):
            with self.subTest(backend=backend), patch.object(mouse, "_load_backend", return_value=backend):
                with self.assertRaises(mouse.NativeMouseEventError):
                    mouse.build_window_click(7, (0, 0, 100, 100), (30, 16))

    def test_missing_spi_does_not_construct_pair(self):
        with patch.object(mouse, "_load_backend", side_effect=mouse.NativeMouseEventsUnavailable("missing")):
            with self.assertRaises(mouse.NativeMouseEventsUnavailable):
                mouse.build_window_click(7, (0, 0, 100, 100), (30, 16))

    def test_default_numbers_distinct_across_threads_and_bounded(self):
        with patch.object(mouse, "_last_number", 0x7FFFFFFE):
            self.assertEqual(mouse._next_event_number(), 0x7FFFFFFF)
            self.assertEqual(mouse._next_event_number(), 1)
            with ThreadPoolExecutor(max_workers=4) as pool:
                numbers = list(pool.map(lambda _: mouse._next_event_number(), range(100)))
            self.assertEqual(len(set(numbers)), 100)
            self.assertTrue(all(0 < number <= 0x7FFFFFFF for number in numbers))

    def test_real_configure_sets_local_after_global_reset(self):
        # Exercise production configure, not the higher-level fake's algorithm.
        backend = mouse._MacBackend.__new__(mouse._MacBackend)
        event = {}
        order = []
        def global_set(ref, point):
            ref.update(point=point, local=(-1, -1))
            order.append("global")
        def local_set(ref, point):
            ref["local"] = (point.x, point.y)
            order.append("local")
        backend.q = SimpleNamespace(
            CGEventSetFlags=lambda ref, flags: ref.update(flags=flags),
            CGEventSetLocation=global_set,
            CGEventSetIntegerValueField=lambda ref, field, value: ref.update({field: value}),
            kCGMouseEventWindowUnderMousePointer="under",
            kCGMouseEventWindowUnderMousePointerThatCanHandleThisEvent="handling")
        backend.objc = SimpleNamespace(pyobjc_id=lambda ref: ref)
        backend.point_type = lambda x, y: SimpleNamespace(x=x, y=y)
        backend.setter = local_set
        backend.configure(event, 7, (150, 626), (30, 16))
        self.assertEqual(order, ["global", "local"])
        self.assertEqual(event, dict(flags=0, point=(150, 626), local=(30, 16), under=7, handling=7))

    @unittest.skipUnless(sys.platform == "darwin", "macOS dependencies required")
    def test_actual_creation_only_spi_and_global_readback(self):
        # Numeric window ID is intentionally not resolved and no event is posted.
        # This tests serialization only, not ownership or receiver behavior.
        if any(importlib.util.find_spec(name) is None for name in ("AppKit", "Quartz", "objc")):
            self.skipTest("macOS Python bindings unavailable")
        backend = mouse._load_backend()  # Missing SPI on macOS is a failure.
        with patch.object(mouse, "_load_backend", return_value=backend):
            result = mouse.build_window_click(7, (120, 610, 620, 252), (150, 626), 91)
        for event in (result.down, result.up):
            fields = backend.read(event)
            self.assertEqual(fields["point"], (150, 626))
            self.assertEqual(fields["window_point"], (30, 16))
            self.assertEqual(fields["event_number"], 91)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(importlib.util.find_spec('ApplicationServices'), 'PyObjC required')
class AccessibilityScrollTests(unittest.TestCase):
    """Scrolling asks the scroll bar, rather than miming a wheel at a point.

    The wheel path it replaced had never worked vertically: `wheelCount` says
    how many amounts follow and the binding enforces it, so every up and down
    raised "Wrong number of variadic arguments, need 1, got 2" while left and
    right worked. Nothing covered it, because the scroll tests all stopped at
    the planner or the browser.
    """
    def setUp(self):
        from types import SimpleNamespace
        from nlc import driver
        self.m = driver
        self.d = object.__new__(driver.Driver)
        self.content, self.area = object(), object()
        self.vertical, self.horizontal = object(), object()
        self.attrs = {
            self.content: {'AXRole': 'AXStaticText', 'AXParent': self.area},
            self.area: {'AXRole': 'AXScrollArea',
                        'AXChildren': [self.vertical, self.horizontal]},
            self.vertical: {'AXRole': 'AXScrollBar', 'AXValue': 0.5,
                            'AXOrientation': 'AXVerticalOrientation'},
            self.horizontal: {'AXRole': 'AXScrollBar', 'AXValue': 0.5,
                              'AXOrientation': 'AXHorizontalOrientation'},
        }

    def _run(self, direction, pages=1, settable=True, set_error=0):
        from contextlib import ExitStack
        from unittest.mock import patch
        wrote = []
        with ExitStack() as stack:
            stack.enter_context(patch.object(self.m, '_attr',
                side_effect=lambda el, key: self.attrs.get(el, {}).get(key)))
            stack.enter_context(patch.object(self.m, '_aborted_now', return_value=False))
            stack.enter_context(patch.object(self.m, 'AXUIElementIsAttributeSettable',
                                             return_value=(0, settable)))
            stack.enter_context(patch.object(self.m, 'AXUIElementSetAttributeValue',
                side_effect=lambda el, key, value: (wrote.append((el, key, value)), set_error)[1]))
            result = self.d._scroll(self.content, direction, pages)
        return result, wrote

    def test_each_direction_moves_its_own_bar_the_right_way(self):
        for direction, bar, expected in (('down', self.vertical, 0.6),
                                         ('up', self.vertical, 0.4),
                                         ('right', self.horizontal, 0.6),
                                         ('left', self.horizontal, 0.4)):
            with self.subTest(direction=direction):
                result, wrote = self._run(direction)
                self.assertEqual(1, len(wrote))
                self.assertIs(bar, wrote[0][0])
                self.assertEqual('AXValue', wrote[0][1])
                self.assertAlmostEqual(expected, wrote[0][2], places=6)
                self.assertAlmostEqual(expected, result['scrolled_to'], places=6)

    def test_the_value_is_clamped_rather_than_run_off_the_end(self):
        self.attrs[self.vertical]['AXValue'] = 0.95
        _, wrote = self._run('down', pages=10)
        self.assertEqual(1.0, wrote[0][2])

    def test_a_bar_that_cannot_be_set_is_refused_with_nothing_written(self):
        with self.assertRaises(self.m.NotStarted):
            self._run('down', settable=False)

    def test_a_missing_bar_says_so_instead_of_scrolling_something_else(self):
        self.attrs[self.area]['AXChildren'] = [self.horizontal]
        with self.assertRaises(self.m.NotStarted):
            self._run('down')

    def test_no_wheel_or_pointer_event_is_built_anywhere(self):
        import inspect
        source = inspect.getsource(self.m)
        for banned in ('CGEventPost', 'CGEventCreateScrollWheelEvent',
                       'CGEventCreateMouseEvent', 'CGEventCreateKeyboardEvent'):
            self.assertNotIn(banned, source, f'{banned} is back in the driver')
