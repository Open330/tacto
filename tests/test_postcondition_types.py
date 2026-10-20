"""A postcondition field of the wrong type is refused when the flow loads.

Seen 2026-10-07: an agent wrote `"absent": {"role": "Sheet", ...}`; it loaded,
then `tacto run` died mid-replay with `TypeError: 'in <string>' requires string`
and the agent got a traceback instead of the one line naming the field."""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tacto.flow import Flow


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

    def test_a_postcondition_or_skip_if_that_is_not_an_object_is_refused(self):
        for key in ("postcondition", "skip_if"):
            for value in ("GoToWindow", ["x"], 0, [], "", 7):
                doc = json.loads(_flow(None))
                doc["steps"][0][key] = value
                with self.subTest(key=key, value=value), \
                        self.assertRaisesRegex(ValueError, rf"{key} must be an object"):
                    Flow.from_json(json.dumps(doc))

    def test_a_field_that_is_not_an_object_is_refused(self):
        with self.assertRaisesRegex(ValueError, r"postcondition\.field must be an object"):
            Flow.from_json(_flow({"field": "statusBarText", "contains": "7"}))

    def test_absent_or_empty_postcondition_is_none(self):
        for value in (None, {}):
            flow = Flow.from_json(_flow(value))
            self.assertIsNone(flow.steps[0].postcondition)

    def test_text_values_still_load(self):
        flow = Flow.from_json(_flow({"absent": "GoToWindow"}))
        self.assertEqual(flow.steps[0].postcondition.absent, "GoToWindow")

    def test_run_reports_invalid_flow_without_traceback(self):
        from tacto import cli
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
