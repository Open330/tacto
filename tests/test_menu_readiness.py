"""Read-only menu settling must never admit late readiness or retry input."""
import contextlib
import io
import json
import unittest
from unittest.mock import Mock,patch

from tacto.menu_readiness import wait_menu_enabled


class MenuReadinessTests(unittest.TestCase):
    def setUp(self):
        self.now=0
        self.driver=Mock()
        self.driver.menu_state.return_value={'type':'menu_state','enabled':False}
    def sleep(self,seconds):self.now+=seconds
    def run_wait(self,**kwargs):
        return wait_menu_enabled(self.driver,'owned',['File',['Save','저장']],clock=lambda:self.now,
                                 sleep=self.sleep,**kwargs)
    def test_disabled_then_enabled_reads_only(self):
        self.driver.menu_state.side_effect=[{'type':'menu_state','enabled':False},
                                           {'type':'menu_state','enabled':True}]
        result=self.run_wait()
        self.assertEqual(('ready','menu_enabled',2,(False,True)),
                         (result.state,result.reason,result.read_count,result.enabled_samples))
        self.driver.act.assert_not_called()
    def test_persistently_disabled_deadline_never_retries_input(self):
        result=self.run_wait(timeout_seconds=.1,poll_interval=.05)
        self.assertEqual(('timeout','deadline_exceeded',2),(result.state,result.reason,result.read_count))
        self.driver.act.assert_not_called()
    def test_late_enabled_and_cancelled_reader_cannot_report_ready(self):
        for mode in ('deadline','cancelled','cancel_error'):
            self.now=0;cancel=[False]
            def read(*args):
                if mode=='deadline':self.now=2
                else:cancel[0]=True
                return {'type':'menu_state','enabled':True}
            def checker():
                if cancel[0] and mode=='cancel_error':raise RuntimeError('PRIVATE_DETAILS')
                return cancel[0]
            self.driver.menu_state.side_effect=read
            result=self.run_wait(cancelled=checker)
            self.assertNotEqual('ready',result.state)
            self.assertEqual(1,result.read_count)
            self.assertNotIn('PRIVATE_DETAILS',str(result))
    def test_errors_malformed_and_numeric_enabled_are_not_polled(self):
        for response in (RuntimeError('PRIVATE'),{'type':'error','detail':'PRIVATE'},
                         {'type':'menu_state','enabled':1},None):
            self.driver.menu_state.reset_mock();self.driver.menu_state.side_effect=None
            if isinstance(response,Exception):self.driver.menu_state.side_effect=response
            else:self.driver.menu_state.return_value=response
            result=self.run_wait()
            self.assertEqual('unavailable',result.state)
            self.driver.menu_state.assert_called_once()
            self.assertNotIn('PRIVATE',str(result))
    def test_signed_backend_user_stop_is_not_polled_as_unavailability(self):
        self.driver.menu_state.return_value={'type':'error','error':'aborted_by_user','detail':'PRIVATE'}
        result=self.run_wait()
        self.assertEqual(('stopped','cancelled',1),(result.state,result.reason,result.read_count))
        self.driver.menu_state.assert_called_once()
        self.assertNotIn('PRIVATE',str(result))

    def test_zero_budget_and_pre_cancel_do_not_read(self):
        self.assertEqual('timeout',self.run_wait(timeout_seconds=0).state)
        self.assertEqual('stopped',self.run_wait(cancelled=lambda:True).state)
        self.driver.menu_state.assert_not_called()
    def test_read_limit_bounds_bad_clock_and_path_copy(self):
        seen=[]
        def read(target,path):
            seen.append(path[0]);path[0]='mutated'
            return {'type':'menu_state','enabled':False}
        self.driver.menu_state.side_effect=read
        result=self.run_wait(max_reads=2)
        self.assertEqual('read_limit',result.reason)
        self.assertEqual(['File','File'],seen)
    def test_checker_cannot_mutate_an_already_observed_enabled_sample(self):
        response={'type':'menu_state','enabled':False};calls=[0]
        self.driver.menu_state.return_value=response
        def checker():
            calls[0]+=1
            if calls[0]==2:response['enabled']=True
            return False
        result=self.run_wait(max_reads=1,cancelled=checker)
        self.assertEqual(('timeout',(False,)),(result.state,result.enabled_samples))
    def test_invalid_budgets_and_path_refuse_before_read(self):
        for kwargs in ({'timeout_seconds':float('nan')},{'poll_interval':0},{'max_reads':True},
                       {'max_reads':129},{'cancelled':True}):
            with self.assertRaises(ValueError):self.run_wait(**kwargs)
        with self.assertRaises(ValueError):wait_menu_enabled(self.driver,'owned',[])
        self.driver.menu_state.assert_not_called()


class MenuReadinessCliTests(unittest.TestCase):
    def test_wait_option_routes_reads_and_returns_nonzero_when_not_ready(self):
        from tacto.cli import _dispatch_command
        driver=Mock();driver.menu_state.return_value={'type':'menu_state','enabled':True}
        with patch('tacto.cli._make_driver',return_value=driver),contextlib.redirect_stdout(io.StringIO()) as out:
            status=_dispatch_command('menu-state',['owned','File','Save','--helper','--wait-enabled','1'])
        self.assertEqual(0,status)
        self.assertEqual('ready',json.loads(out.getvalue())['state'])
        driver.act.assert_not_called()
        with patch('tacto.cli._make_driver',return_value=driver),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(1,_dispatch_command('menu-state',['owned','File','Save','--wait-enabled','0']))
    def test_owned_signed_scenario_is_isolated_offline_without_bootstrap(self):
        import importlib.util
        from pathlib import Path
        path=Path(__file__).resolve().parents[1]/'examples/eval.py'
        spec=importlib.util.spec_from_file_location('menu_wait_eval',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with patch('runpy.run_path') as bootstrap:
            _,_,cases=module._parse_plan(['signed_menu_readiness','--offline'])
        bootstrap.assert_not_called()
        self.assertEqual('skipped',cases[0]['status'])
        self.assertTrue(cases[0]['isolated'])
        self.assertEqual({'gui','native'},set(cases[0]['requirements']))

    def test_invalid_wait_options_never_construct_driver(self):
        from tacto.cli import _dispatch_command
        for args in (['--wait-enabled'],['--wait-enabled','nan'],['--wait-enabled','61'],
                     ['--wait-enabled','1','--wait-enabled','2']):
            with patch('tacto.cli._make_driver') as driver,contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(2,_dispatch_command('menu-state',['owned','File','Save',*args]))
            driver.assert_not_called()
