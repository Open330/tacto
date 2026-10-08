"""Malformed recipes fail at loading, before any app input is attempted."""
import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from nlc.flow import Flow


BASE = {"schema": 2, "name": "shape-check", "target": "fixture",
        "steps": [{"action": "read", "selector": {"role": "StaticText"}}]}


class FlowShapeTests(unittest.TestCase):
    def malformed(self):
        for key in ("params", "tables", "derived"):
            for value in (None, [], 0, "x"):
                doc = copy.deepcopy(BASE)
                doc[key] = value
                yield f"Flow.{key}", doc
        for key in ("selector", "safety", "binding"):
            for value in ([], 0, False, "x"):
                doc = copy.deepcopy(BASE)
                doc["steps"][0][key] = value
                yield f"step.{key}", doc
        for key in ("resource", "destination"):
            for value in ([], 0, "x"):
                doc = copy.deepcopy(BASE)
                doc["steps"][0]["safety"] = {key: value}
                yield f"step.safety.{key}", doc
        for value in (None, {}, "x", ["x"], [None]):
            doc = copy.deepcopy(BASE)
            doc["steps"][0]["safety"] = {"evidence": value}
            yield "step.safety.evidence", doc
        for value in ([], 0, "x"):
            doc = copy.deepcopy(BASE)
            doc["tables"] = {"digit": value}
            yield "Flow.tables.digit", doc
        for value in ([], {}, None, 0):
            doc = copy.deepcopy(BASE)
            doc["steps"][0]["action"] = value
            yield "unknown Flow action", doc

    def test_malformed_shapes_are_named_load_errors(self):
        for where, doc in self.malformed():
            with self.subTest(where=where, doc=doc):
                with self.assertRaises(ValueError) as raised:
                    Flow.from_json(json.dumps(doc))
                self.assertIn(where, str(raised.exception))

    def test_cli_rejects_every_case_without_a_traceback(self):
        from nlc.cli import _dispatch
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            for where, doc in self.malformed():
                path.write_text(json.dumps(doc))
                stderr = io.StringIO()
                with self.subTest(where=where, doc=doc), redirect_stderr(stderr):
                    self.assertEqual(2, _dispatch("run", [str(path)]))
                self.assertIn("invalid_flow:", stderr.getvalue())
                self.assertNotIn("Traceback", stderr.getvalue())

    def test_valid_parameter_tables_and_optional_objects_roundtrip(self):
        doc = copy.deepcopy(BASE)
        doc.update(params={"digit": "1"}, tables={"digit": {"1": "One"}},
                   derived={"twice": "{{digit}} * 2"})
        doc["steps"][0].update(safety=None, binding=None)
        flow = Flow.from_json(json.dumps(doc))
        loaded = Flow.from_json(flow.to_json())
        self.assertEqual(flow, loaded)
        self.assertEqual("fixture", loaded.bind(digit="2").target)
