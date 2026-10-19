"""The real browser image planner shares the loop and outer model deadlines."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tacto.model_budget import model_deadline, remaining_model_seconds
from tacto.provider_result import ProviderResult
from tacto.visual import VisualCapture, plan_visual
from tacto.visual_actions import run_visual


class VisualModelDeadlineTests(unittest.TestCase):
    def exercise(self, *, outer=None, expires=False):
        now=[100.]
        clock=SimpleNamespace(monotonic=lambda:now[0])
        driver=Mock()
        driver.observe.return_value={'type':'observation','obs_id':'owned'}
        capture=VisualCapture('capture','owned','page','hash','state',
                              {'width':200,'height':100},0.,b'owned-fixture')
        def read(*args):
            now[0]=104. if outer is None else 101.
            return capture
        def provider(*args, **kwargs):
            if expires:
                now[0]=111.
                raise TimeoutError('PRIVATE_PROVIDER_MESSAGE')
            return ProviderResult('request','codex','codex','cli-json','ok',
                json.dumps({'answer':json.dumps({'action':'stop'})}),None,.1)
        with patch('tacto.visual_actions.time',clock), patch('tacto.model_budget.time',clock), \
             patch('tacto.visual_actions.capture_browser',side_effect=read), \
             patch('tacto.helper.InheritedHelperDriver.available',return_value=False), \
             patch('tacto.codex_result.codex_result',side_effect=provider) as call:
            with model_deadline(outer):
                result=run_visual(driver,'page','read owned image',
                    lambda c,g,h:plan_visual(c,g,h,provider='codex',timeout=60),
                    lambda *a:True,lambda *a:False,timeout_seconds=10)
            self.assertEqual(60,remaining_model_seconds(60))
        self.assertEqual([],result.history)
        driver._page.mouse.click.assert_not_called()
        return result,call

    def test_builtin_planner_receives_only_remaining_loop_time(self):
        result,call=self.exercise()
        self.assertEqual('planner_stopped',result.reason)
        self.assertEqual(6,call.call_args.kwargs['timeout'])

    def test_shorter_outer_deadline_is_not_extended(self):
        result,call=self.exercise(outer=102.)
        self.assertEqual('planner_stopped',result.reason)
        self.assertEqual(1,call.call_args.kwargs['timeout'])

    def test_provider_timeout_after_deadline_is_stopped_without_click_or_retry(self):
        result,call=self.exercise(expires=True)
        self.assertEqual(('STOPPED','deadline_exceeded'),(result.state,result.reason))
        call.assert_called_once()
        self.assertNotIn('PRIVATE',repr(result.to_dict()))
