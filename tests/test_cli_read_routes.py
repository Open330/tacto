"""CLI read commands must respect an explicitly selected permission boundary."""
import contextlib
import io
import json
import unittest
from unittest.mock import Mock, patch

from tacto import cli
from tacto.helper import HelperUnavailable


class CLIReadRouteTests(unittest.TestCase):
    def test_json_preserves_complete_observation_and_option_visibility_without_input(self):
        driver=self.fake()
        observation={'type':'observation','obs_id':'s1','tree':'0 Window Title: A\n  1 ComboBox Identifier: choice\n2 Window Title: B',
            'custom_selects':{'1':{'state':'open','options':[{'label':'Beta','disabled':False,'visible':False,'scroll_direction':'down'}]}},
            'truncated':False}
        driver.observe.return_value=observation
        with patch('tacto.helper.InheritedHelperDriver',return_value=driver),patch('tacto.driver.Driver',side_effect=AssertionError('fallback')),contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(0,cli._dispatch('observe',['--json','owned.fixture','--helper']))
        self.assertEqual(observation,json.loads(output.getvalue()))
        driver.observe.assert_called_once_with('owned.fixture','tree_full');driver.act.assert_not_called()

    def test_json_error_keeps_reason_and_bad_metadata_fails_without_partial_output(self):
        for observation,expected in [({'type':'error','error':'no_window_content','reason':'minimized'},'no_window_content'),
                                     ({'type':'observation','tree':'PRIVATE TREE','invalid':float('nan')},'observation_serialization_failed')]:
            driver=self.fake();driver.observe.return_value=observation
            with patch.object(cli,'_make_driver',return_value=driver),contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(1,cli._dispatch('observe',['owned.fixture','--helper','--json']))
            decoded=json.loads(output.getvalue());self.assertEqual(expected,decoded['error'])
            if expected=='no_window_content':self.assertEqual('minimized',decoded['reason'])
            self.assertNotIn('PRIVATE',output.getvalue());driver.act.assert_not_called()

    def test_json_invalid_arguments_do_not_connect(self):
        for args in (['--json'],['owned.fixture','--json','--json'],['owned.fixture','--json','--unknown']):
            with self.subTest(args=args),patch.object(cli,'_make_driver') as factory,contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(2,cli._dispatch('observe',args))
            factory.assert_not_called()

    def fake(self):
        driver = Mock()
        driver.list_targets.return_value = {'apps': [{'id': 'owned.fixture', 'displayName': 'Owned fixture'}]}
        driver.observe.return_value = {'type': 'observation', 'tree': '0 Window  Title: Owned fixture'}
        return driver

    def test_apps_and_observe_explicit_helper_never_construct_python_driver(self):
        for command in (['apps', '--helper'], ['observe', 'owned.fixture', '--helper']):
            driver = self.fake()
            with self.subTest(command=command), patch('tacto.helper.InheritedHelperDriver', return_value=driver) as signed, \
                    patch('tacto.driver.Driver', side_effect=AssertionError('Python fallback forbidden')), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli._dispatch(command[0], command[1:]), 0)
                signed.assert_called_once_with()
            if command[0] == 'apps': driver.list_targets.assert_called_once_with()
            else: driver.observe.assert_called_once_with('owned.fixture', 'tree_full')

    def test_failed_signed_connection_never_falls_back_to_python(self):
        for command in (['apps', '--helper'], ['observe', 'owned.fixture', '--helper']):
            with self.subTest(command=command), patch('tacto.helper.InheritedHelperDriver', side_effect=HelperUnavailable('signed channel unavailable')), \
                    patch('tacto.driver.Driver', side_effect=AssertionError('Python fallback forbidden')) as direct, \
                    contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(HelperUnavailable): cli._dispatch(command[0], command[1:])
                direct.assert_not_called()

    def test_main_reports_helper_failure_without_traceback_or_private_transport_text(self):
        for flags in ([],['--json']):
            with patch.object(cli,'_main',side_effect=HelperUnavailable('PRIVATE CHANNEL DETAIL')), \
                    patch('tacto.access_log.AccessLog'),contextlib.redirect_stdout(io.StringIO()) as out, \
                    contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(2,cli.main(['apps','--helper',*flags]))
            text=out.getvalue()+err.getvalue()
            self.assertIn('helper_unavailable',text)
            self.assertNotIn('Traceback',text);self.assertNotIn('PRIVATE',text)
            if flags:self.assertEqual('unknown',json.loads(out.getvalue())['effect'])

    def test_lease_refusal_preserves_code_without_private_peer_detail(self):
        from tacto.helper import HelperLeaseRefused
        for code in ('helper_busy', 'aborted_by_user'):
            with patch.object(cli, '_main', side_effect=HelperLeaseRefused(code)), \
                    patch('tacto.access_log.AccessLog'), contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(2, cli.main(['apps', '--helper']))
            result = json.loads(out.getvalue())
            self.assertEqual(code, result['error'])
            self.assertEqual('not_started', result['effect'])
            self.assertEqual(code == 'helper_busy', result['retryable'])

    def test_explicit_python_route_is_preserved(self):
        for command in (['apps'], ['observe', 'owned.fixture']):
            driver = self.fake()
            with self.subTest(command=command), patch('tacto.driver.Driver', return_value=driver) as direct, \
                    patch('tacto.helper.InheritedHelperDriver', side_effect=AssertionError('unexpected helper')), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli._dispatch(command[0], command[1:]), 0)
                direct.assert_called_once_with()

    def test_observe_helper_flag_before_target_is_not_used_as_bundle_id(self):
        driver = self.fake()
        with patch.object(cli, '_make_driver', return_value=driver), \
                patch('tacto.driver.Driver', side_effect=AssertionError('unrouted native access')), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli._dispatch('observe', ['--helper', 'owned.fixture']), 0)
        driver.observe.assert_called_once_with('owned.fixture', 'tree_full')

    def test_observe_missing_target_does_not_connect_or_launch(self):
        with patch.object(cli, '_make_driver') as factory, \
                patch('tacto.driver.Driver', side_effect=AssertionError('unrouted native access')), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli._dispatch('observe', ['--helper']), 2)
        factory.assert_not_called()

    def test_signed_list_error_is_reported_without_fallback_or_key_error(self):
        driver = self.fake()
        driver.list_targets.return_value = {'type': 'error', 'error': 'gui_session_unavailable'}
        with patch.object(cli, '_make_driver', return_value=driver), \
                patch('tacto.driver.Driver', side_effect=AssertionError('unrouted native access')), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli._dispatch('apps', ['--helper']), 1)
        self.assertIn('gui_session_unavailable', output.getvalue())


    def test_observe_reports_specific_window_reason(self):
        driver = self.fake()
        driver.observe.return_value = {'type':'error','error':'no_window_content',
                                       'reason':'minimized','detail':'창이 최소화됐습니다'}
        with patch.object(cli, '_make_driver', return_value=driver), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli._dispatch('observe', ['owned.fixture', '--helper']), 1)
        self.assertIn('reason=minimized', output.getvalue())
        self.assertNotIn('screen_locked', output.getvalue())


if __name__ == '__main__': unittest.main()
