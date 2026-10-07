"""A postcondition field of the wrong type is refused when the flow loads.

Seen 2026-10-07: an agent wrote `"absent": {"role": "Sheet", ...}`; it loaded,
then `nlc run` died mid-replay with `TypeError: 'in <string>' requires string`
and the agent got a traceback instead of the one line naming the field."""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nlc.flow import Flow


def _flow(post):
    return json.dumps({"schema": 2, "name": "t", "target": "com.apple.calculator",
                       "steps": [{"action": "read", "selector": {"role": "StaticText"},
                                  "postcondition": post}]})


class PostconditionTypeTests(unittest.TestCase):
    def test_object_under_absent_is_refused_at_load(self):
        with self.assertRaisesRegex(ValueError, r"postcondition\.absent must be text"):
            Flow.from_json(_flow({"absent": {"role": "Sheet"}}))

    def test_each_text_field_is_checked(self):
        for key in ("equals", "contains", "absent", "attribute"):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, key):
                Flow.from_json(_flow({key: ["x"]}))

    def test_text_values_still_load(self):
        flow = Flow.from_json(_flow({"absent": "GoToWindow"}))
        self.assertEqual(flow.steps[0].postcondition.absent, "GoToWindow")

    def test_run_reports_invalid_flow_without_traceback(self):
        from nlc import cli
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write(_flow({"absent": {"role": "Sheet"}}))
        self.addCleanup(os.unlink, fh.name)
        err = io.StringIO()
        with redirect_stderr(err):
            code = cli._dispatch("run", [fh.name])
        self.assertEqual(code, 2)
        self.assertIn("invalid_flow:", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())


if __name__ == "__main__":
    unittest.main()
