"""Exercise the real evaluator entry point without GUI or model side effects."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from nlc.evaluation import require_replay
from nlc.flow import ReplayResult

ROOT = Path(__file__).resolve().parents[1]


class EvaluationReports(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("eval_reports", ROOT / "examples/eval.py")
        self.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.m)
        self.m.SCENARIOS = [("local (test)", lambda: True)]
        self.m.SCENARIO_REQUIREMENTS = {}
        self.m.ISOLATED = set()
        self.m._gui_reason = Mock(side_effect=AssertionError("unexpected GUI probe"))
        self.m._ensure_window = Mock(side_effect=AssertionError("unexpected app launch"))
        self.m._seal_offline = Mock()

    def run_eval(self, *argv):
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "results with spaces.json"
            output = io.StringIO()
            with patch("sys.argv", ["eval.py", *argv, "--report", str(path)]), \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                with self.assertRaises(SystemExit) as stopped:
                    self.m.main()
            report = json.loads(path.read_text()) if path.exists() else None
            if path.exists():
                self.assertEqual(0o600, path.stat().st_mode & 0o777)
            return stopped.exception.code, report, output.getvalue()

    def test_preview_never_probes_or_executes_even_with_foreground(self):
        run = Mock(side_effect=AssertionError("executed preview"))
        self.m.SCENARIOS = [("screen", run)]
        self.m.SCENARIO_REQUIREMENTS = {"screen": {"gui", "model", "foreground"}}
        code, report, _ = self.run_eval("--preview", "--foreground")
        self.assertEqual(0, code)
        self.assertEqual("plan", report["kind"])
        self.assertEqual("planned", report["cases"][0]["status"])
        self.assertIsNone(report["summary"]["pass_rate"])
        self.assertIsNone(report["duration_ms"])
        self.m._seal_offline.assert_not_called()
        run.assert_not_called()

    def test_report_counts_executed_failures_and_skips_separately(self):
        def broken():
            raise ValueError("bad receipt")
        self.m.SCENARIOS = [("pass", lambda: True), ("fail", broken),
                            ("skip", lambda: "missing fixture")]
        code, report, _ = self.run_eval()
        self.assertEqual(1, code)
        summary = report["summary"]
        self.assertEqual((1, 1, 1, 2, 3), tuple(summary[k] for k in
                         ("passed", "failed", "skipped", "scored", "selected")))
        self.assertEqual(0.5, summary["pass_rate"])
        self.assertAlmostEqual(2 / 3, summary["scored_fraction"])
        self.assertEqual("fail", report["cases"][1]["name"])
        self.assertIn("bad receipt", report["cases"][1]["error"])
        self.assertTrue(all(c["duration_ms"] >= 0 for c in report["cases"]))
        self.assertIsNone(report["model_cost_usd"])

    def test_unknown_filter_and_unknown_option_are_errors_before_gui_access(self):
        for argv in [("typo",), ("local", "typo"), ("--offine",)]:
            with self.subTest(argv=argv):
                code, report, _ = self.run_eval(*argv)
                self.assertEqual(2, code)
                self.assertIsNone(report)

    def test_empty_registry_is_not_a_successful_preview(self):
        self.m.SCENARIOS = []
        code, report, _ = self.run_eval("--preview")
        self.assertEqual(2, code)
        self.assertIsNone(report)

    def test_a_plan_with_nothing_to_run_is_inconclusive(self):
        """Selected is not the same as runnable.

        An empty REGISTRY was already caught. This is the case that was not:
        scenarios matched the filter and every one of them was excluded by a
        flag, so the plan had nothing in it and still exited 0. A real run
        returns 2 in that situation, and preview was the one path left out of
        the rule — which is exactly how a mistyped flag or an over-narrow
        filter passes CI looking green.
        """
        run = Mock()
        self.m.SCENARIOS = [("screen", run)]
        self.m.SCENARIO_REQUIREMENTS = {"screen": {"gui"}}
        code, report, printed = self.run_eval("--preview", "--offline")
        self.assertEqual(2, code)
        self.assertEqual(0, report["summary"]["planned"])
        self.assertEqual(1, report["summary"]["selected"])
        run.assert_not_called()
        # And it still says what it found, rather than only failing.
        self.assertIn("SKIPPED", printed)

    def test_a_plan_with_something_to_run_is_not_an_error(self):
        run = Mock()
        self.m.SCENARIOS = [("local", run)]
        self.m.SCENARIO_REQUIREMENTS = {"local": set()}
        code, report, _ = self.run_eval("--preview", "--offline")
        self.assertEqual(0, code)
        self.assertEqual(1, report["summary"]["planned"])
        run.assert_not_called()

    def test_invalid_report_parent_is_rejected_before_execution(self):
        run = Mock()
        self.m.SCENARIOS = [("local", run)]
        with tempfile.TemporaryDirectory() as work:
            destination = str(Path(work) / "missing" / "report.json")
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as stopped:
                    self.m._parse_plan(["--report", destination])
            self.assertEqual(2, stopped.exception.code)
            run.assert_not_called()

    def test_all_skipped_is_inconclusive_not_success(self):
        self.m.SCENARIOS = [("screen", lambda: self.fail("should not run"))]
        self.m.SCENARIO_REQUIREMENTS = {"screen": {"gui"}}
        code, report, _ = self.run_eval("--offline")
        self.assertEqual(2, code)
        self.assertEqual(1, report["summary"]["skipped"])
        self.assertIsNone(report["summary"]["pass_rate"])
        self.assertIsNone(report["cases"][0]["duration_ms"])
        self.assertEqual("offline", report["cases"][0]["reason_code"])

    def test_isolated_selection_cannot_share_a_process(self):
        self.m.SCENARIOS = [("isolated", lambda: True), ("ordinary", lambda: True)]
        self.m.ISOLATED = {"isolated"}
        code, _, _ = self.run_eval("isolated", "ordinary")
        self.assertEqual(2, code)
        code, report, _ = self.run_eval("isolated")
        self.assertEqual(0, code)
        self.assertEqual(1, report["summary"]["scored"])
        code, report, _ = self.run_eval()
        self.assertEqual(0, code)
        self.assertEqual("isolated_process", report["cases"][0]["reason_code"])

    def test_failure_is_not_erased_by_a_later_lock(self):
        second = Mock()
        self.m.SCENARIOS = [("first", lambda: False), ("second", second)]
        self.m.SCENARIO_REQUIREMENTS = {k: {"gui"} for k in ("first", "second")}
        self.m._gui_reason = Mock(side_effect=["", "screen locked"])
        code, report, _ = self.run_eval("first", "second")
        self.assertEqual(1, code)
        first, later = report["cases"]
        self.assertEqual("failed", first["status"])
        self.assertEqual("screen locked", first["environment_after_failure"])
        self.assertEqual("skipped", later["status"])
        second.assert_not_called()

    def test_default_foreground_gate_is_reported_before_any_gui_probe(self):
        self.m.SCENARIOS = [("focus", lambda: self.fail("should not run"))]
        self.m.SCENARIO_REQUIREMENTS = {"focus": {"gui", "foreground"}}
        code, report, _ = self.run_eval()
        self.assertEqual(2, code)
        self.assertEqual("foreground_required", report["cases"][0]["reason_code"])

    def test_replay_failure_retains_redacted_stage_and_step(self):
        result = ReplayResult()
        result.protect("secret text")
        result.record(status="error", step=2, action="menu",
                      detail="secret text: needs_foreground")
        self.m.SCENARIOS = [("save", lambda: require_replay(result, "save_as"))]
        code, report, output = self.run_eval()
        self.assertEqual(1, code)
        details = report["cases"][0]["details"]
        self.assertEqual("save_as", details["stage"])
        self.assertEqual(2, details["steps"][0]["step"])
        self.assertNotIn("secret text", json.dumps(report) + output)


if __name__ == "__main__":
    unittest.main()
