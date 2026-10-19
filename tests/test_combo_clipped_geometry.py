"""A partially visible row must be scrolled before a bound popup click."""
import unittest
from examples.probe_native_select import _combo_row_scroll_direction,run_probe
from examples.eval import _owned_select_fixture


class ComboClippedGeometryTests(unittest.TestCase):
    def test_partial_row_and_reopened_row_require_opposite_scrolls(self):
        viewport=(134,723,228,28)
        self.assertIsNone(_combo_row_scroll_direction(viewport,(148,723,200,18)))
        self.assertEqual('down',_combo_row_scroll_direction(viewport,(148,741,200,18)))
        self.assertEqual('up',_combo_row_scroll_direction(viewport,(148,713,200,18)))
        self.assertIsNone(_combo_row_scroll_direction(viewport,(148,733,200,18)))

    def test_missing_unusable_or_uncontainable_geometry_cannot_authorize_input(self):
        for row in ((148,723,200,29),(133,723,200,18),(148,723,215,18),
                    (148,float('nan'),200,18),(148,723,200,0),(148,True,200,18),
                    (148,723,200)):
            with self.subTest(row=row),self.assertRaises(ValueError):
                _combo_row_scroll_direction((134,723,228,28),row)

    def test_invalid_fixture_configuration_fails_before_driver_or_bootstrap(self):
        for value in (True,-1,21,'1'):
            with self.subTest(value=value),self.assertRaises(ValueError):
                run_probe(None,combo=True,visible_items=value)
            with self.subTest(value=value),self.assertRaises(ValueError):
                _owned_select_fixture(combo=True,visible_items=value)
        with self.assertRaises(ValueError):run_probe(None,visible_items=1)
        with self.assertRaises(ValueError):_owned_select_fixture(visible_items=1)
        with self.assertRaises(ValueError):_owned_select_fixture(combo=True,replacement=True,visible_items=1)
