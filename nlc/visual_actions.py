"""Explicitly authorized browser pixel clicks with bounded re-observation.

Images and planner output confer no authority and cannot prove completion.
Freshness is checked immediately before dispatch but cannot make the browser's
rendering and input atomic. Timeouts/cancellation are cooperative boundaries;
an in-flight browser command may complete after a caller stops waiting.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import math
import time
import weakref

from .visual import VisualCapture, capture_browser


_SPENT_CAPTURES = weakref.WeakValueDictionary()


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


class VisualClickExecutor:
    """One-use captures, with host authorization and fresh pixel/DOM checks."""

    def __init__(self, driver, target="page"):
        self.driver = driver
        self.target = target

    def click(self, capture: VisualCapture, proposal: dict, authorize, *, cancelled=None):
        event = {"action": "click", "capture_id": capture.capture_id,
                 "effect": "not_started"}
        def denied(reason):
            return {**event, "reason": reason}
        if capture.capture_id in _SPENT_CAPTURES:
            return denied("capture_already_used")
        # A refused attempt is also spent; callers must observe before replanning.
        _SPENT_CAPTURES[capture.capture_id] = capture
        if capture.target != self.target:
            return denied("target_mismatch")
        if not isinstance(proposal, dict) or set(proposal) != {"action", "x", "y"} or proposal.get("action") != "click":
            return denied("invalid_proposal")
        x, y = proposal["x"], proposal["y"]
        width, height = capture.viewport.get("width"), capture.viewport.get("height")
        if not all(_number(v) for v in (x, y, width, height)) or not (0 <= x < width and 0 <= y < height):
            return denied("invalid_coordinates")
        event.update(x=x, y=y)
        if hashlib.sha256(capture.png).hexdigest() != capture.sha256:
            return denied("invalid_capture")
        if authorize is None:
            return denied("authorization_required")
        try:
            if cancelled is not None and cancelled():
                return denied("cancelled")
            if authorize(deepcopy(proposal), capture) is not True:
                return denied("authorization_denied")
            if cancelled is not None and cancelled():
                return denied("cancelled")
            fresh = capture_browser(self.driver, capture.obs_id, self.target)
            if (fresh.sha256 != capture.sha256 or fresh.state_sha256 != capture.state_sha256
                    or fresh.viewport != capture.viewport):
                return denied("stale_capture")
            if cancelled is not None and cancelled():
                return denied("cancelled")
        except Exception as exc:
            return denied("preflight_failed:" + type(exc).__name__)
        # Past this point a click may have reached the browser, even on exception.
        event["effect"] = "unknown"
        try:
            self.driver._page.mouse.click(x, y)
        except Exception as exc:
            return {**event, "reason": "dispatch_failed:" + type(exc).__name__}
        return {**event, "effect": "dispatched"}


@dataclass
class VisualRunResult:
    state: str
    reason: str
    history: list[dict] = field(default_factory=list)
    capture: dict | None = None

    def to_dict(self):
        return {"state": self.state, "reason": self.reason,
                "history": deepcopy(self.history), "capture": deepcopy(self.capture)}


def run_visual(driver, target, goal, planner, authorize, verify, *, max_steps=8,
               timeout_seconds=60, cancelled=None):
    """Replan after each click; only trusted host verify(capture,history) says DONE.

    planner(capture, goal, history) returns {action: click, x, y}, {action: done},
    or {action: stop}. authorize(proposal,capture) must explicitly return True.
    The host verifier must inspect the actual task result, not accept planner
    completion text or an image instruction as evidence. This boolean is not a
    typed G7 artifact/delivery receipt and does not attest external side effects. These callbacks are
    trusted application code; they are never selected from a model response.
    """
    if type(max_steps) is not int or max_steps < 0:
        raise ValueError("max_steps must be a nonnegative integer")
    if not _number(timeout_seconds) or timeout_seconds < 0:
        raise ValueError("timeout_seconds must be finite and nonnegative")
    deadline = time.monotonic() + timeout_seconds
    executor = VisualClickExecutor(driver, target)
    history = []
    latest = None

    def stopped():
        if time.monotonic() >= deadline:
            return "deadline_exceeded"
        try:
            return "cancelled" if cancelled is not None and cancelled() else None
        except Exception:
            return "cancellation_check_failed"

    def finish(state, reason):
        return VisualRunResult(state, reason, history, latest.to_dict() if latest else None)

    for step in range(max_steps + 1):
        reason = stopped()
        if reason:
            return finish("STOPPED", reason)
        try:
            obs = driver.observe(target)
            if obs.get("type") != "observation":
                return finish("UNVERIFIED", "observation_failed")
            latest = capture_browser(driver, obs["obs_id"], target)
        except Exception as exc:
            return finish("UNVERIFIED", "capture_failed:" + type(exc).__name__)
        reason = stopped()
        if reason:
            return finish("STOPPED", reason)
        try:
            complete = verify is not None and verify(latest, deepcopy(history)) is True
        except Exception:
            return finish("UNVERIFIED", "host_verification_failed")
        reason = stopped()
        if reason:
            return finish("STOPPED", reason)
        if complete:
            return finish("DONE", "host_verified")
        if step >= max_steps:
            return finish("STOPPED", "step_limit")
        try:
            proposal = planner(latest, goal, deepcopy(history))
        except Exception:
            return finish("UNVERIFIED", "planner_failed")
        reason = stopped()
        if reason:
            return finish("STOPPED", reason)
        if isinstance(proposal, dict) and proposal == {"action": "done"}:
            return finish("UNVERIFIED", "planner_done_without_host_evidence")
        if isinstance(proposal, dict) and proposal == {"action": "stop"}:
            return finish("STOPPED", "planner_stopped")
        event = executor.click(latest, proposal, authorize, cancelled=lambda: stopped() is not None)
        history.append(event)
        if event["effect"] == "not_started":
            return finish("REFUSED", event["reason"])
        if event["effect"] == "unknown":
            return finish("UNVERIFIED", event["reason"])
    raise AssertionError("unreachable")
