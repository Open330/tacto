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
import os
import re
import time
import copy

from .storage import write_private
from dataclasses import dataclass, field

# A trace records what was on screen, which is the point — and also the risk: an
# accessibility tree can contain messages, account numbers, names and anything
# typed into a form. Traces are written redacted and owner-only by default, and
# a caller that genuinely needs the raw values has to ask for them.
_SECRETY = re.compile(
    r"(비밀번호|비번|카드\s*번호|주민등록번호|주민번호|인증번호|계좌|"
    r"password|passcode|otp|cvv|ssn|account\s*number|token|secret|api[_ ]?key)",
    re.IGNORECASE)
_LONG_DIGITS = re.compile(r"\b\d[\d ,\-]{7,}\b")      # card/account-like runs


def _redact_text(text: str) -> str:
    out = []
    for line in text.splitlines():
        if _SECRETY.search(line):
            # keep the structure (role, identifier) so selectors stay debuggable,
            # drop the value
            out.append(re.sub(r"(Value|Title|Description):\s*[^,]*",
                              r"\1: [redacted]", line))
        else:
            out.append(_LONG_DIGITS.sub("[redacted]", line))
    return "\n".join(out)


def _redact(obj):
    if isinstance(obj, dict):
        return {k: ("[redacted]" if k in ("text", "value") and isinstance(v, str)
                    else _redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    if isinstance(obj, str):
        return _redact_text(obj)
    return obj


@dataclass
class Trace:
    """An ordered record of a run: observations, actions and their outcomes."""
    flow: str = ""
    target: str = ""      # the APP target (bundle id / origin), never the intent
    intent: str = ""      # the natural-language request, recorded for context
    started_at: float = field(default_factory=time.time)
    events: list = field(default_factory=list)
    schema: int = 2
    redacted: bool = False
    sensitive_values: set = field(default_factory=set, repr=False)
    capabilities: list = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps({"schema": self.schema, "redacted": self.redacted,
                           "flow": self.flow, "target": self.target,
                           "intent": self.intent,
                           "started_at": self.started_at, "events": self.events,
                           "capabilities": self.capabilities},
                          ensure_ascii=False, indent=2)

    @staticmethod
    def load(path: str) -> "Trace":
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        t = Trace(flow=d.get("flow", ""), target=d.get("target", ""),
                  intent=d.get("intent", ""), started_at=d.get("started_at", 0.0))
        t.events = d.get("events", [])
        t.schema = d.get("schema", 1)
        t.redacted = d.get("redacted", False)
        t.capabilities = d.get("capabilities", [])
        return t

    def save(self, path: str, redact: bool = True) -> str:
        """Write the trace. Redacted and owner-only unless told otherwise —
        sharing a trace to debug a failure should not mean sharing a mailbox."""
        data = json.loads(self.to_json())
        if redact:
            data["intent"] = "[redacted]" if data["intent"] else ""
            data["events"] = _redact(data["events"])
            def scrub(value):
                if isinstance(value, str):
                    for secret in sorted(self.sensitive_values, key=len, reverse=True):
                        if secret:
                            value = value.replace(secret, "[redacted]")
                    return value
                if isinstance(value, dict):
                    return {k: scrub(v) for k, v in value.items()}
                if isinstance(value, list):
                    return [scrub(v) for v in value]
                return value
            data = scrub(data)
            data["redacted"] = True
        return write_private(path, json.dumps(data, ensure_ascii=False, indent=2))


class TracingDriver:
    """Record the complete L2 request/response stream without changing it."""

    @property
    def capabilities(self):
        return getattr(self._inner, "capabilities", ())

    def document_attachment_metadata(self, target, document_token, *, max_utf16_units=100000, timeout_seconds=3):
        return self._inner.document_attachment_metadata(target, document_token,
            max_utf16_units=max_utf16_units, timeout_seconds=timeout_seconds)

    def document_window_status(self, target, document_token):
        # A past absence observation cannot authorize cleanup of a live file.
        return self._inner.document_window_status(target, document_token)

    def document_rtf_export(self, target, document_token, *, max_utf16_units=1000000,
                            timeout_seconds=3):
        return self._inner.document_rtf_export(target, document_token,
            max_utf16_units=max_utf16_units, timeout_seconds=timeout_seconds)

    @property
    def prepare_commit(self):
        return getattr(self._inner, "prepare_commit", None)

    @property
    def after_commit(self):
        return getattr(self._inner, "after_commit", None)

    @property
    def commit_expected(self):
        return getattr(self._inner, "commit_expected", None)

    def document_snapshot(self, target, document_token=None):
        # Live host evidence is deliberately not persisted as a reusable
        # receipt or copied document body in the execution trace.
        return self._inner.document_snapshot(target, document_token=document_token)

    def document_text(self, target, document_token=None, *, max_utf16_units=1000000,
                      document_path=None):
        # A live document identity token is not evidence a recorded trace can
        # replay, and complete bodies should not be copied into trace files.
        kwargs = {'document_token': document_token, 'max_utf16_units': max_utf16_units}
        if document_path is not None:
            kwargs['document_path'] = document_path
        return self._inner.document_text(target, **kwargs)

    def __init__(self, inner, trace: Trace | None = None):
        self._inner = inner
        self.trace = trace or Trace()
        self.trace.capabilities = sorted(self.capabilities)
        self._sensitive_captures = []

    def protect_captures(self, captures):
        self._sensitive_captures = list(captures)

    def protect_value(self, value):
        if isinstance(value, str):
            self.trace.sensitive_values.add(value)
        elif isinstance(value, (list, tuple)):
            for item in value:
                self.protect_value(item)

    def _record(self, kind, request, result):
        for obs in (result, result.get("last_obs", {}) if isinstance(result, dict) else {}):
            if isinstance(obs, dict) and obs.get("tree"):
                for capture in self._sensitive_captures:
                    self.protect_value(capture.read(obs["tree"]))
        self.trace.events.append(copy.deepcopy({"kind": kind, "request": request,
                                                 "result": result}))
        return result

    def identity(self, target):
        result = self._inner.identity(target) if hasattr(self._inner, "identity") else target
        return self._record("identity", {"target": target}, result)

    def list_targets(self):
        return self._record("list_targets", {}, self._inner.list_targets())

    def observe(self, target, mode="tree_full", require_window=True, *, document_token=None, reveal=None):
        if document_token is not None:
            # A live document token and its scoped body cannot be made into
            # reusable recorded evidence. Never fall back to an app-wide read.
            return self._inner.observe(target, mode, require_window=require_window,
                                       document_token=document_token)
        # Preserve compatibility with drivers whose default is implicit; a
        # reveal is part of what was asked, so it is part of the recording.
        extra = {} if reveal is None else {"reveal": list(reveal)}
        result = (self._inner.observe(target, mode, **extra) if require_window else
                  self._inner.observe(target, mode, require_window=False, **extra))
        return self._record("observe", {"target": target, "mode": mode,
                                       "require_window": require_window, **extra}, result)

    def act(self, obs_id, target, action, expect=None, risk_class="none",
            intent_text="", approved=False, *, save_panel_token=None, submit_authorized=False):
        request = dict(obs_id=obs_id, target=target, action=action, expect=expect,
                       risk_class=risk_class, intent_text=intent_text, approved=approved)
        if submit_authorized is not False:
            request["submit_authorized"] = submit_authorized
        if save_panel_token is not None:
            # Connection-owned live evidence is not a replayable action proof.
            return self._inner.act(**request, save_panel_token=save_panel_token)
        try:
            result = self._inner.act(**request)
        except Exception as exc:
            result = {"type": "error", "error": "driver_exception",
                      "effect": "unknown", "detail": str(exc)}
        return self._record("act", request, result)

    def save_panel(self, obs_id, target, window_element_index, *, panel_token=None,
                   document_token=None, require_format=False):
        return self._inner.save_panel(obs_id, target, window_element_index,
            panel_token=panel_token, document_token=document_token, require_format=require_format)

    def probe(self, obs_id, target, element_index):
        request = dict(obs_id=obs_id, target=target, element_index=element_index)
        method = getattr(self._inner, "probe", None)
        result = method(**request) if method else {"type": "error", "error": "unsupported_probe"}
        return self._record("probe", request, result)

    def menu_items(self, target, path=None):
        request = {"target":target, "path":[] if path is None else path}
        method = getattr(self._inner, "menu_items", None)
        result = method(**request) if method else {"type":"error", "error":"unsupported_menu_items", "effect":"not_started"}
        return self._record("menu_items", request, result)

    def menu_state(self, target, path):
        request = {"target":target, "path":path}
        method = getattr(self._inner, "menu_state", None)
        result = method(**request) if method else {"type":"error", "error":"unsupported_menu_state", "effect":"not_started"}
        return self._record("menu_state", request, result)

    def selection(self, target):
        method = getattr(self._inner, "selection", None)
        result = method(target) if method else {"type": "error", "error": "unsupported_selection"}
        return self._record("selection", {"target": target}, result)

    def wait_until(self, target, predicate, timeout_ms=4000, poll_ms=250):
        result = self._inner.wait_until(target, predicate, timeout_ms, poll_ms)
        return self._record("wait_until", {"target": target, "timeout_ms": timeout_ms,
                                          "poll_ms": poll_ms}, result)

    def handle(self, msg):
        return self._record("handle", {"msg": msg}, self._inner.handle(msg))


class TraceDriver:
    """Consume exactly the recorded stream; divergent requests cannot pass."""

    def __init__(self, trace: Trace):
        if trace.schema != 2:
            raise ValueError("legacy trace lacks request records; record a schema 2 trace")
        self.trace = trace
        self.capabilities = frozenset(trace.capabilities) - {
            "evidence.document_snapshot", "evidence.document_text",
            "evidence.document_text.path_selector", "observe.document_token", "evidence.document_window_status",
            "evidence.save_panel", "action.click.save_panel_guard", "evidence.document_rtf_export", "evidence.document_attachment_metadata",
            "action.window_input", "window_input.window_bound", "window_input.host_authorization"}
        self._i = 0

    def document_attachment_metadata(self, target, document_token, *, max_utf16_units=100000, timeout_seconds=3):
        return {'type':'error','error':'document_attachment_metadata_unavailable','effect':'not_started'}

    def document_window_status(self, target, document_token):
        return {'type':'error', 'error':'document_window_status_unavailable', 'effect':'not_started'}

    def document_rtf_export(self, target, document_token, *, max_utf16_units=1000000,
                            timeout_seconds=3):
        return {'type':'error', 'error':'document_rtf_export_unavailable', 'effect':'not_started'}

    def _take(self, kind, request):
        if self._i >= len(self.trace.events):
            return {"type": "error", "error": "trace_exhausted", "effect": "not_started"}
        event = self.trace.events[self._i]
        if event.get("kind") != kind or event.get("request") != request:
            return {"type": "error", "error": "trace_divergence", "effect": "not_started",
                    "detail": f"event {self._i}: request differs from recording"
                              + (" (trace is redacted)" if self.trace.redacted else "")}
        self._i += 1
        return copy.deepcopy(event["result"])

    def identity(self, target):
        result = self._take("identity", {"target": target})
        return result if isinstance(result, str) else None

    def list_targets(self):
        return self._take("list_targets", {})

    def observe(self, target, mode="tree_full", require_window=True, *, document_token=None, reveal=None):
        if document_token is not None:
            return {'type':'error', 'error':'document_snapshot_unavailable', 'effect':'not_started'}
        return self._take("observe", {"target": target, "mode": mode, "require_window": require_window,
                                      **({} if reveal is None else {"reveal": list(reveal)})})

    def act(self, obs_id, target, action, expect=None, risk_class="none",
            intent_text="", approved=False, *, save_panel_token=None, submit_authorized=False):
        if action.get('name') == 'window_input':
            return {'type':'error', 'error':'window_input_unavailable', 'effect':'not_started'}
        if save_panel_token is not None:
            return {'type':'error', 'error':'save_panel_guard_unavailable', 'effect':'not_started'}
        request = dict(obs_id=obs_id, target=target, action=action,
                       expect=expect, risk_class=risk_class,
                       intent_text=intent_text, approved=approved)
        if submit_authorized is not False:
            request["submit_authorized"] = submit_authorized
        return self._take("act", request)

    def save_panel(self, obs_id, target, window_element_index, *, panel_token=None,
                   document_token=None, require_format=False):
        return {'type':'error', 'error':'save_panel_unavailable', 'effect':'not_started'}

    def probe(self, obs_id, target, element_index):
        return self._take("probe", dict(obs_id=obs_id, target=target, element_index=element_index))

    def menu_items(self, target, path=None):
        return self._take("menu_items", {"target":target, "path":[] if path is None else path})

    def menu_state(self, target, path):
        return self._take("menu_state", {"target":target, "path":path})

    def selection(self, target):
        return self._take("selection", {"target": target})

    def wait_until(self, target, predicate, timeout_ms=4000, poll_ms=250):
        result = self._take("wait_until", {"target": target, "timeout_ms": timeout_ms,
                                          "poll_ms": poll_ms})
        if result.get("type") == "observation" and not predicate(result["tree"]):
            return {"type": "error", "error": "trace_divergence", "detail": "predicate changed"}
        return result

    def handle(self, msg):
        return self._take("handle", {"msg": msg})


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
