"""First task connection failures remain one small machine-readable result."""
import contextlib
import io
import json
import unittest
from unittest.mock import patch, Mock
from tacto import cli
from tacto.helper import HelperUnavailable


class FirstRunCLITests(unittest.TestCase):
    def test_failed_connection_is_reported_without_native_fallback_or_input(self):
        for error in (HelperUnavailable('PRIVATE user path'), OSError('PRIVATE connection'), ValueError('PRIVATE handshake')):
            with self.subTest(error=type(error).__name__), patch.object(cli,'_make_driver',side_effect=error) as make, \
                    patch('tacto.first_run.run_first_task') as run, \
                    patch('tacto.driver.Driver',side_effect=AssertionError('fallback forbidden')) as direct, \
                    contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()) as stderr:
                self.assertEqual(1,cli._dispatch('first-run',['--helper','--yes']))
                result=json.loads(output.getvalue())
                self.assertEqual(('first_run','failed','connect','helper_unavailable','not_started'),
                                 tuple(result[k] for k in ('type','status','stage','error','effect')))
                self.assertFalse(result['repeat_write']);self.assertFalse(result['save_command_sent'])
                self.assertEqual('tacto doctor --check',result['hint'])
                self.assertNotIn('PRIVATE',output.getvalue());self.assertEqual('',stderr.getvalue())
                make.assert_called_once();run.assert_not_called();direct.assert_not_called()

    def test_normal_success_preserves_receipt_and_closes_exact_connection(self):
        driver=Mock();result=dict(type='first_run',status='text_verified',save_command_sent=False)
        with patch.object(cli,'_make_driver',return_value=driver), \
                patch('tacto.first_run.run_first_task',return_value=result) as run, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(0,cli._dispatch('first-run',['--helper','--yes']))
        self.assertEqual(result,json.loads(output.getvalue()))
        run.assert_called_once_with(driver);driver.close.assert_called_once()

    def test_invalid_arguments_never_connect_or_start_the_task(self):
        for args in ([],['--helper'],['--yes'],['--helper','--yes','--yes']):
            with self.subTest(args=args),patch.object(cli,'_make_driver') as make,contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(2,cli._dispatch('first-run',args))
                make.assert_not_called()
