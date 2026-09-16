"""Execution traces — make a run reproducible off the machine it happened on.

This session's hardest bug took several round trips because a failure on the
user's Mac could not be reproduced here: their Calculator exposed
`Identifier: StandardInputView;value:408` while ours exposed
`StandardInputView`. The flow (the IR) was already portable; what was missing
was a record of *what the screen actually looked like* during the run.

Two pieces close that gap, and they compose with the existing design rather
than replacing it:

  Flow JSON   — the intermediate representation: what to do (already existed)
  Trace JSON  — what was observed and what happened when it ran (this module)

`TracingDriver` wraps any driver and records every observation tree and action
result. `TraceDriver` replays a flow against a recorded trace with no app, no
GUI session and no permissions — so a user's failing run can be re-executed
verbatim on another machine, and a fixed flow can be regression-tested in CI.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field


@dataclass
class Trace:
    """An ordered record of a run: observations, actions and their outcomes."""
    flow: str = ""
    target: str = ""      # the APP target (bundle id / origin), never the intent
    intent: str = ""      # the natural-language request, recorded for context
    started_at: float = field(default_factory=time.time)
    events: list = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps({"flow": self.flow, "target": self.target,
                           "intent": self.intent,
                           "started_at": self.started_at, "events": self.events},
                          ensure_ascii=False, indent=2)

    @staticmethod
    def load(path: str) -> "Trace":
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        t = Trace(flow=d.get("flow", ""), target=d.get("target", ""),
                  intent=d.get("intent", ""), started_at=d.get("started_at", 0.0))
        t.events = d.get("events", [])
        return t

    def save(self, path: str) -> str:
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.to_json())
        return path


class TracingDriver:
    """Wraps a real driver and records everything it sees and does.

    Transparent: forwards every call, returns every result unchanged, so a
    traced run behaves exactly like an untraced one."""

    def __init__(self, inner, trace: Trace | None = None):
        self._inner = inner
        self.trace = trace or Trace()

    def identity(self, target):
        return self._inner.identity(target) if hasattr(self._inner, "identity") else target

    def observe(self, target, mode="tree_full"):
        r = self._inner.observe(target, mode)
        self.trace.events.append({"kind": "observe", "target": target, "mode": mode,
                                  "result": r})
        return r

    def act(self, obs_id, target, action, risk_class="none", intent_text="",
            approved=False):
        r = self._inner.act(obs_id, target, action, risk_class, intent_text, approved)
        self.trace.events.append({"kind": "act", "obs_id": obs_id, "target": target,
                                  "action": action, "risk_class": risk_class,
                                  "result": r})
        return r

    def wait_until(self, target, predicate, timeout_ms=4000, poll_ms=250):
        r = self._inner.wait_until(target, predicate, timeout_ms, poll_ms)
        self.trace.events.append({"kind": "wait_until", "target": target, "result": r})
        return r

    def handle(self, msg):
        r = self._inner.handle(msg)
        self.trace.events.append({"kind": "handle", "msg": msg, "result": r})
        return r


class TraceDriver:
    """Replays a flow against a recorded trace — no app, no GUI, no permissions.

    Observations are served in recorded order; actions are accepted and their
    recorded outcome returned. This reproduces the *observed world* of the
    original run, which is what makes a remote failure debuggable and a fix
    regression-testable. It does not re-run the real UI, and does not pretend to:
    a flow whose behaviour depends on screens the trace never captured will run
    out of recorded observations and say so.
    """

    def __init__(self, trace: Trace):
        self.trace = trace
        self._obs = [e for e in trace.events if e["kind"] in ("observe", "wait_until")]
        self._i = 0

    def identity(self, target):
        # Fall back to the requested target: a trace that did not record an app
        # identity must not silently substitute something else, or contract
        # bindings would invalidate on replay for the wrong reason.
        return self.trace.target or target

    def _next_observation(self):
        while self._i < len(self._obs):
            r = self._obs[self._i]["result"]
            self._i += 1
            if r.get("type") == "observation":
                return r
        return {"type": "error", "error": "trace_exhausted",
                "detail": "recorded run has no further observations"}

    def observe(self, target, mode="tree_full"):
        return self._next_observation()

    def act(self, obs_id, target, action, risk_class="none", intent_text="",
            approved=False):
        # Honour the same gates as a live driver so safety behaviour is part of
        # what a trace reproduces.
        if risk_class == "handoff":
            return {"type": "handoff_request", "reason": intent_text}
        if risk_class in ("confirm", "preapproved") and not approved:
            return {"type": "confirm_request", "what": intent_text,
                    "risk_class": risk_class}
        return {"type": "acted", "action": action.get("name"), "obs_id": obs_id,
                "note": "replayed from trace"}

    def wait_until(self, target, predicate, timeout_ms=4000, poll_ms=250):
        # Scan forward through recorded observations for one satisfying the
        # predicate, mirroring the live poll without the wall clock.
        start = self._i
        while self._i < len(self._obs):
            r = self._next_observation()
            if r.get("type") != "observation":
                break
            if predicate(r["tree"]):
                return r
        self._i = start
        last = self._obs[-1]["result"] if self._obs else {}
        return {"type": "timeout", "last_obs": last}


def record(driver, flow, **replay_kwargs):
    """Run a flow against a live driver while recording a trace.
    Returns (ReplayResult, Trace)."""
    from .flow import replay
    td = TracingDriver(driver, Trace(flow=flow.name, target=flow.target))
    res = replay(td, flow, **replay_kwargs)
    return res, td.trace


def rerun(trace: Trace, flow, **replay_kwargs):
    """Re-execute a flow against a recorded trace. No machine state needed."""
    from .flow import replay
    return replay(TraceDriver(trace), flow, **replay_kwargs)
