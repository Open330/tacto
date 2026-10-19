"""Real driver polling: transient window reads, terminal errors, fixed deadline."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from tacto.helper import InheritedHelperDriver
from tacto.observation_state import retryable_observation_error
try:
    from tacto.driver import Driver
except ImportError:
    Driver = None


class Clock:
    def __init__(self): self.value = 0; self.sleeps = []
    def now(self): return self.value
    def sleep(self, seconds): self.sleeps.append(seconds); self.value += seconds


class ObservationWaitErrorsTests(unittest.TestCase):
    def backends(self):
        result = [(InheritedHelperDriver.wait_until, 'tacto.helper.time')]
        if Driver is not None: result.append((Driver.wait_until, 'tacto.driver.time'))
        return result

    def run_wait(self, fn, module, observe, predicate=lambda tree:tree == 'ready'):
        clock = Clock()
        with patch(module + '.monotonic', side_effect=clock.now), patch(module + '.sleep', side_effect=clock.sleep):
            result = fn(SimpleNamespace(observe=observe), 'owned', predicate, timeout_ms=100, poll_ms=60)
        return result, clock

    def test_terminal_errors_stop_on_first_read_without_sleep_or_predicate(self):
        for fn, module in self.backends():
            for code in ('aborted_by_user','target_not_found','helper_unavailable','screen_locked',
                         'document_scope_unavailable','window_list_unavailable','unknown_error'):
                with self.subTest(backend=module,code=code):
                    error = {'type':'error','error':code}
                    observe, predicate = Mock(return_value=error), Mock()
                    result, clock = self.run_wait(fn,module,observe,predicate)
                    self.assertEqual(result,error)
                    self.assertEqual(observe.call_count,1)
                    self.assertEqual(clock.sleeps,[])
                    predicate.assert_not_called()

    def test_temporary_windowless_state_can_settle_within_same_deadline(self):
        for fn,module in self.backends():
            for reason in ('no_visible_window', 'window_offscreen'):
                with self.subTest(backend=module,reason=reason):
                    ready={'type':'observation','obs_id':'o','tree':'ready'}
                    observe=Mock(side_effect=[{'type':'error','error':'no_window_content',
                        'reason':reason,'lock':{'locked':False}},ready])
                    result,clock=self.run_wait(fn,module,observe)
                    self.assertEqual(result,ready)
                    self.assertEqual(observe.call_count,2)
                    self.assertLessEqual(sum(clock.sleeps),.1)

    def test_windowless_deadline_returns_error_not_false_condition_timeout(self):
        for fn,module in self.backends():
            for reason in (None, 'window_offscreen'):
                with self.subTest(backend=module,reason=reason):
                    error={'type':'error','error':'no_window_content','reason':reason}
                    observe=Mock(return_value=error)
                    result,clock=self.run_wait(fn,module,observe)
                    self.assertEqual(result,error)
                    self.assertEqual(observe.call_count,3)
                    self.assertAlmostEqual(sum(clock.sleeps),.1)

    def test_real_false_condition_keeps_timeout_and_last_observation(self):
        for fn,module in self.backends():
            with self.subTest(backend=module):
                last={'type':'observation','obs_id':'o','tree':'not ready'}
                result,clock=self.run_wait(fn,module,Mock(return_value=last))
                self.assertEqual(result['type'],'timeout')
                self.assertEqual(result['last_obs'],last)
                self.assertAlmostEqual(sum(clock.sleeps),.1)

    def test_lock_hidden_minimized_unknown_reason_and_unknown_lock_are_not_polled(self):
        for additions in ({'reason':'screen_locked'},{'reason':'app_hidden'},{'reason':'minimized'},
                          {'reason':'unknown'},
                          {'lock':{'locked':True}},{'lock':{}},{'lock':{'locked':None}}):
            self.assertFalse(retryable_observation_error({'type':'error','error':'no_window_content',**additions}))


class WindowListingWaitTests(unittest.TestCase):
    backends=ObservationWaitErrorsTests.backends
    run_wait=ObservationWaitErrorsTests.run_wait
    def listing_error(self,**updates):
        listing={'version':1,'status':'role_unavailable','ax_error':0,'raw_count':2,
                 'role_window_count':1,'role_unavailable_count':1,'role_transient_count':1,
                 'filtered_count':1,'badge_count':0,**updates}
        return {'type':'error','error':'window_list_unavailable','window_listing_diagnostic':listing}

    def test_transient_read_recovers_by_observation_with_same_deadline(self):
        transient=[self.listing_error(),self.listing_error(status='ax_error',ax_error=-25204,
            raw_count=0,role_window_count=0,role_unavailable_count=0,role_transient_count=0,filtered_count=0)]
        for fn,module in self.backends():
            for error in transient:
                ready={'type':'observation','obs_id':'o','tree':'ready'}
                observe=Mock(side_effect=[error,ready]);result,clock=self.run_wait(fn,module,observe)
                self.assertEqual(result,ready);self.assertEqual(observe.call_count,2)
                self.assertLessEqual(sum(clock.sleeps),.1)

    def test_persistent_transient_failure_returns_original_error_at_deadline(self):
        for fn,module in self.backends():
            error=self.listing_error();observe=Mock(return_value=error)
            result,clock=self.run_wait(fn,module,observe)
            self.assertEqual(result,error);self.assertEqual(observe.call_count,3)
            self.assertAlmostEqual(sum(clock.sleeps),.1)

    def test_malformed_unclassified_and_locked_failures_stop_without_polling(self):
        cases=[self.listing_error(role_transient_count=0),
               self.listing_error(status='malformed_array',role_window_count=0,
                    role_unavailable_count=0,role_transient_count=0,filtered_count=0),
               {'type':'error','error':'window_list_unavailable'},
               {**self.listing_error(),'lock':{'locked':True}},
               {**self.listing_error(),'reason':'screen_locked'}]
        for fn,module in self.backends():
            for error in cases:
                observe=Mock(return_value=error);predicate=Mock()
                result,clock=self.run_wait(fn,module,observe,predicate)
                self.assertEqual(result,error);self.assertEqual(observe.call_count,1)
                self.assertEqual(clock.sleeps,[]);predicate.assert_not_called()
