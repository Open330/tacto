"""Machine-readable evaluation results; no GUI or model dependencies."""
from collections import Counter
import json
import os
from pathlib import Path
import tempfile


# Whether a harness may take the person's focus.
#
# One switch, imported by everyone who could take it. It lived in
# examples/eval.py alone, and `tests/test_helper_contract.py` has its own
# `_bring_forward` — so `every_transport_answers_the_same` went on pulling
# Finder and the select fixture to the front while the default run believed it
# could not. A guard that only covers the paths you remembered is not a guard.
_foreground_allowed = False


def allow_foreground(allowed: bool) -> None:
    global _foreground_allowed
    _foreground_allowed = bool(allowed)


def foreground_allowed() -> bool:
    return _foreground_allowed


class EvaluationFailure(AssertionError):
    def __init__(self, stage, details):
        self.details = {"stage": stage, **details}
        super().__init__(f"{stage}: {json.dumps(details, ensure_ascii=False, default=str)}")


def require_replay(result, stage, *, accepted=None):
    """Retain replay diagnostics with registered secrets redacted by replay.

    Other observed app text and paths may remain; this is not a general PII
    scrubber. Reports are owner-only and the error also appears on stdout.
    """
    if not (result.ok if accepted is None else accepted):
        raise EvaluationFailure(stage, {"steps": result.log})


def summarize(cases):
    # A scored case always has a measured duration. Planned/preflight-skipped
    # cases do not; scenarios that skip after starting do have one.
    counts = Counter(case["status"] for case in cases)
    scored = counts["passed"] + counts["failed"]
    measured = [case["duration_ms"] for case in cases
                if case["duration_ms"] is not None]
    return {
        "selected": len(cases),
        "passed": counts["passed"], "failed": counts["failed"],
        "skipped": counts["skipped"], "planned": counts["planned"],
        "scored": scored,
        "pass_rate": counts["passed"] / scored if scored else None,
        "scored_fraction": scored / len(cases) if cases else None,
        # Includes time spent in scenarios that discover they must skip.
        "scenario_duration_ms": sum(measured),
        "mean_scored_duration_ms": (
            sum(case["duration_ms"] for case in cases
                if case["status"] in {"passed", "failed"}) / scored
            if scored else None),
    }


def write_report(path, report):
    """Publish a complete owner-only report, even when replacing an old one.

    Reasons can contain app text or file paths. Do not make those world-readable
    or leave a truncated report where a later comparison could consume it.
    """
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=".nlc-eval-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
