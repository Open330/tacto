"""Real headless Chromium; local synthetic form, deterministic repair chooser."""
import contextlib
import io
import json
import unittest
from unittest.mock import patch

from tacto.auto_repair import AutoRepair
from tacto.browser import BrowserDriver
from tacto.cli import _run_flow
from tacto.flow import Capture, Flow, Postcondition, Selector, Step
from tests.test_runtime_contract import contract
from tests import test_auto_repair as helpers
try:
    from tests._artifacts import requires_playwright
except ImportError:  # discovered with tests/ as the top level
    from _artifacts import requires_playwright


class BrowserCaptureContinuationTests(unittest.TestCase):
    @requires_playwright
    def test_twelve_steps_two_repairs_keep_capture_and_do_not_repeat_input(self):
        driver=BrowserDriver();results=[];packets=[];actions=[];messages=[];output=io.StringIO()
        secret='synthetic-capture-7437'
        try:
            driver._page.set_content('<label>Source<input id="source" value="'+secret+'" readonly></label>'
                                     '<label>First input<input id="first"></label>'
                                     '<label>Second input<input id="second"></label>')
            original_act=driver.act
            def act(obs,target,action,**kwargs):
                if action['name']=='set_value':actions.append(action['value'])
                return original_act(obs,target,action,**kwargs)
            driver.act=act
            source=Selector(role='textbox',title='Source')
            checkpoint=Postcondition(field=source,contains='Title: Source')
            first=Step(action='read',selector=source,postcondition=checkpoint,safety=contract(),
                captures=[Capture(name='read_value',field=source,sensitive=True)])
            def input_step(old,current):
                return Step(action='set_value',selector=Selector(role='textbox',title=old),
                    params={'value':'{{read_value}}'},safety=contract(('write_ephemeral',)),
                    postcondition=Postcondition(field=Selector(role='textbox',title=current),
                                                equals='{{read_value}}',attribute='Value'))
            reads=[Step(action='read',selector=source,postcondition=checkpoint,safety=contract()) for _ in range(8)]
            flow=Flow(name='capture continuation browser',target='page',steps=[first,
                input_step('Old first input','First input'),*reads,
                input_step('Old second input','Second input'),reads[0]])
            def choose(packet,timeout):
                packets.append(packet)
                title='First input' if len(packets)==1 else 'Second input'
                candidate=next(row['index'] for row in packet['candidates'] if row.get('title')==title)
                return helpers.response(json.dumps({'candidate':candidate}))
            def runner(*args,**kwargs):
                callback=kwargs['_on_result']
                def capture(result):results.append(result);callback(result)
                return _run_flow(*args,**{**kwargs,'_on_result':capture})
            automatic=AutoRepair(chooser=choose)
            with contextlib.redirect_stdout(output),contextlib.redirect_stderr(output), \
                    patch('tacto.cli._indicator_for',return_value=contextlib.nullcontext()):
                code,learned=automatic.run(flow,{},[],driver=driver,run_flow=runner,emit=messages.append)
            self.assertEqual(0,code,(output.getvalue(),messages))
            self.assertEqual(12,len(flow.steps))
            self.assertEqual(2,automatic.calls)
            self.assertEqual([secret,secret],actions)
            self.assertEqual(secret,driver._page.locator('#first').input_value())
            self.assertEqual(secret,driver._page.locator('#second').input_value())
            self.assertNotIn(secret,json.dumps([packets,messages,output.getvalue(),[r.log for r in results],learned.to_json()]))
            self.assertTrue(all(r._continuation is None for r in results))
        finally:
            driver.close()


if __name__=='__main__':unittest.main()
