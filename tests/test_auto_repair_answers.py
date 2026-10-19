"""A checked continuation must still publish answers read before the failure."""
import contextlib
import io
import unittest
from unittest.mock import patch
from tacto.auto_repair import AutoRepair
from tacto.cli import _run_flow
from tacto.flow_batch import BatchDriver
from tests import test_auto_repair as helpers
from tests.test_flow_batch import SignedCalculator


class RepairAnswerTests(unittest.TestCase):
    def run_repaired(self, suffix_answers=False):
        flow=helpers.recipe();flow.steps[0].answers=True
        flow.steps[1].answers=suffix_answers
        app=SignedCalculator();output=io.StringIO()
        automatic=AutoRepair(chooser=lambda *_:helpers.response())
        with contextlib.redirect_stdout(output),contextlib.redirect_stderr(io.StringIO()), \
             patch('tacto.cli._indicator_for',return_value=contextlib.nullcontext()):
            code,_=automatic.run(flow,{},['--helper'],driver=BatchDriver(app),run_flow=_run_flow,emit=lambda _:None)
        return code,app,output.getvalue()

    def test_answer_from_completed_prefix_is_printed_after_suffix_success(self):
        code,app,output=self.run_repaired()
        self.assertEqual(0,code)
        self.assertIn('done — 결과: 1',output)
        self.assertEqual([2,3],[a['element_index'] for a in app.calls])

    def test_later_suffix_answer_takes_precedence_as_in_uninterrupted_replay(self):
        code,app,output=self.run_repaired(suffix_answers=True)
        self.assertEqual(0,code)
        self.assertIn('done — 결과: 12',output)
        self.assertEqual([2,3],[a['element_index'] for a in app.calls])
