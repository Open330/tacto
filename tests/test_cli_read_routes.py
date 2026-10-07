"""CLI read commands must respect an explicitly selected permission boundary."""
import contextlib
import io
import unittest
from unittest.mock import Mock, patch

from nlc import cli
from nlc.helper import HelperUnavailable


class CLIReadRouteTests(unittest.TestCase):
    def fake(self):
        driver = Mock()
        driver.list_targets.return_value = {'apps': [{'id': 'owned.fixture', 'displayName': 'Owned fixture'}]}
        driver.observe.return_value = {'type': 'observation', 'tree': '0 Window  Title: Owned fixture'}
        return driver

    def test_apps_and_observe_explicit_helper_never_construct_python_driver(self):
        for command in (['apps', '--helper'], ['observe', 'owned.fixture', '--helper']):
            driver = self.fake()
            with self.subTest(command=command), patch('nlc.helper.InheritedHelperDriver', return_value=driver) as signed, \
                    patch('nlc.driver.Driver', side_effect=AssertionError('Python fallback forbidden')), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli._dispatch(command[0], command[1:]), 0)
                signed.assert_called_once_with()
            if command[0] == 'apps': driver.list_targets.assert_called_once_with()
            else: driver.observe.assert_called_once_with('owned.fixture', 'tree_full')

    def test_failed_signed_connection_never_falls_back_to_python(self):
        for command in (['apps', '--helper'], ['observe', 'owned.fixture', '--helper']):
            with self.subTest(command=command), patch('nlc.helper.InheritedHelperDriver', side_effect=HelperUnavailable('signed channel unavailable')), \
                    patch('nlc.driver.Driver', side_effect=AssertionError('Python fallback forbidden')) as direct, \
                    contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(HelperUnavailable): cli._dispatch(command[0], command[1:])
                direct.assert_not_called()

    def test_explicit_python_route_is_preserved(self):
        for command in (['apps'], ['observe', 'owned.fixture']):
            driver = self.fake()
            with self.subTest(command=command), patch('nlc.driver.Driver', return_value=driver) as direct, \
                    patch('nlc.helper.InheritedHelperDriver', side_effect=AssertionError('unexpected helper')), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli._dispatch(command[0], command[1:]), 0)
                direct.assert_called_once_with()

    def test_observe_helper_flag_before_target_is_not_used_as_bundle_id(self):
        driver = self.fake()
        with patch.object(cli, '_make_driver', return_value=driver), \
                patch('nlc.driver.Driver', side_effect=AssertionError('unrouted native access')), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli._dispatch('observe', ['--helper', 'owned.fixture']), 0)
        driver.observe.assert_called_once_with('owned.fixture', 'tree_full')

    def test_observe_missing_target_does_not_connect_or_launch(self):
        with patch.object(cli, '_make_driver') as factory, \
                patch('nlc.driver.Driver', side_effect=AssertionError('unrouted native access')), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli._dispatch('observe', ['--helper']), 2)
        factory.assert_not_called()

    def test_signed_list_error_is_reported_without_fallback_or_key_error(self):
        driver = self.fake()
        driver.list_targets.return_value = {'type': 'error', 'error': 'gui_session_unavailable'}
        with patch.object(cli, '_make_driver', return_value=driver), \
                patch('nlc.driver.Driver', side_effect=AssertionError('unrouted native access')), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli._dispatch('apps', ['--helper']), 1)
        self.assertIn('gui_session_unavailable', output.getvalue())


if __name__ == '__main__': unittest.main()
