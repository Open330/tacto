"""NLC deterministic replay — token-efficient computer-use emulation.

The insight behind PROTOCOL.md's token/determinism goal: a model is only needed
to *author* a flow once. After that, the flow is a fixed list of steps that
replay with zero model tokens.

A step does not store a volatile `element_index` (those are recomputed on every
observation). It stores a **stable Selector** — role plus identifier/title/
description — that is re-resolved against a fresh tree at replay time, and an
optional **expect** predicate that verifies the action landed (D7) before moving
on. Determinism comes from resolving selectors ourselves instead of asking a
model; token efficiency comes from spending zero tokens on replay and, during
authoring, from AX-tree diffs.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field, asdict

from . import safety
from .contract import (SafetyContract, ContextBinding, policy,
                       validate, describe, ApprovalToken)
from .driver import Driver


def _norm(s: str) -> str:
    """Normalize a string for comparison: drop Unicode format chars (Cf: bidi
    marks etc.) and apply NFC. Applied to BOTH observed and expected values so
    they compare symmetrically (review finding: normalization was one-sided)."""
    import unicodedata
    stripped = "".join(c for c in s if unicodedata.category(c) != "Cf")
    return unicodedata.normalize("NFC", stripped)


def _element_block(tree: str, idx: int) -> str:
    """Return the line for element `idx` plus its subtree (more-indented lines)."""
    lines = tree.splitlines()
    start = next((k for k, l in enumerate(lines) if re.match(rf"\s*{idx}\s", l)), None)
    if start is None:
        return ""
    base = len(lines[start]) - len(lines[start].lstrip())
    out = [lines[start]]
    for l in lines[start + 1:]:
        if not l.strip():
            continue
        if (len(l) - len(l.lstrip())) <= base:
            break
        out.append(l)
    return "\n".join(out)


class TargetMissing(LookupError):
    """Zero elements matched the selector."""


class TargetAmbiguous(LookupError):
    """More than one element matched — never silently pick one (codex policy)."""


@dataclass
class Selector:
    """Stable way to re-find an element across observations.

    Matched against a tree line, most-specific field first. `identifier` (AX
    identifier, e.g. "Seven") is the strongest key when present; `title`/`desc`
    are the practical fallback for apps that expose no identifiers.

    Resolution requires exactly one match (codex resolver policy): zero raises
    TargetMissing, more than one raises TargetAmbiguous. `ancestor` scopes the
    search so a repeated label in another pane can't create ambiguity, and
    `allow_nth` is the *explicit* opt-in for a known-repeating element — there is
    no silent find()[0]. AXIdentifier is nullable and not guaranteed stable
    across app versions, so identifier is preferred but never assumed present.
    """
    role: str | None = None
    identifier: str | list[str] | None = None   # str, or any-of list for a
                                                 # state-dependent control (e.g.
                                                 # AllClear↔Clear) — still unique
    title: str | None = None
    desc: str | None = None
    ancestor: str | None = None       # identifier/title of a scoping container
    allow_nth: int | None = None      # explicit index into matches; None = require unique

    # Known AX attribute keys as the driver formats them ("Key: value, Key2: …").
    _ATTR_KEYS = ("Title", "Description", "Value", "Help", "Identifier",
                  "Placeholder")

    @staticmethod
    def _stable_id(v: str) -> str:
        """AXIdentifier can carry volatile state appended after ';' — a real
        device showed 'StandardInputView;value:408', where the identifier
        changes with every keystroke. The part before the first ';' is the
        stable identity; the rest is state. Splitting on this structured
        separator does NOT reintroduce prefix matching ('Save' still never
        matches 'SaveAs'), so exactness is preserved where it matters."""
        return v.split(";", 1)[0].strip()

    @classmethod
    def _attr(cls, line: str, key: str) -> str | None:
        """Extract one attribute's EXACT value from a tree line (not substring).
        Value runs from 'Key: ' to the next ', <KnownKey>: ' or end / [disabled].
        Exact comparison prevents 'Save' matching 'SaveAs' (review finding).
        Unicode bidi/format marks (e.g. the LTR mark macOS Calculator wraps its
        digits in) are stripped so '\\u200e408' compares equal to '408'."""
        others = "|".join(k for k in cls._ATTR_KEYS if k != key)
        m = re.search(rf"{key}:\s*(.+?)(?:,\s*(?:{others}):|\s*\[disabled\]|$)", line)
        return _norm(m.group(1).strip()) if m else None

    def matches(self, line: str) -> bool:
        if self.role:
            # role token is the 2nd field on the line: "<idx> <Role> ..."
            toks = line.strip().split()
            if len(toks) < 2 or toks[1].lower() != self.role.lower():
                return False
        if self.identifier:
            ids = [self.identifier] if isinstance(self.identifier, str) else self.identifier
            val = self._attr(line, "Identifier")   # already normalized
            if val is None:
                return False
            want = {_norm(i).lower() for i in ids}
            want |= {self._stable_id(w) for w in want}
            if val.lower() not in want and self._stable_id(val.lower()) not in want:
                return False
        if self.title:
            val = self._attr(line, "Title")
            if val is None or val.lower() != _norm(self.title).lower():
                return False
        if self.desc:
            val = self._attr(line, "Description")
            if val is None or val.lower() != _norm(self.desc).lower():
                return False
        # at least one positive key required — role alone is not a selector
        return any([self.identifier, self.title, self.desc])

    def _in_scope(self, lines: list[str], line_no: int) -> bool:
        """True if the matched line sits under an ancestor container. The ancestor
        is matched on an exact attribute value (Identifier/Title), not a substring
        of the whole line, so ancestor='main' doesn't match 'Title: domain main
        settings' (review finding)."""
        if not self.ancestor:
            return True
        want = _norm(self.ancestor).lower()
        indent = len(lines[line_no]) - len(lines[line_no].lstrip())
        for j in range(line_no - 1, -1, -1):
            prev = lines[j]
            if not prev.strip():
                continue
            pind = len(prev) - len(prev.lstrip())
            if pind < indent:
                for key in ("Identifier", "Title"):
                    v = self._attr(prev, key)
                    if v is not None and (v.lower() == want
                                          or self._stable_id(v.lower()) == want):
                        return True
                indent = pind   # climb to the next shallower ancestor
        return False

    def resolve(self, tree: str) -> tuple[int, str]:
        """Return (element_index, resolver_trace) for a unique match in a fresh tree."""
        lines = tree.splitlines()
        hits = []
        for i, line in enumerate(lines):
            m = re.match(r"\s*(\d+)\s", line)
            if m and self.matches(line) and self._in_scope(lines, i):
                hits.append(int(m.group(1)))
        strat = "id" if self.identifier else ("title" if self.title else "desc")
        if self.ancestor:
            strat += f"@{self.ancestor}"
        if not hits:
            raise TargetMissing(f"selector {self} matched nothing")
        if self.allow_nth is not None:
            if not (0 <= self.allow_nth < len(hits)):   # reject negatives too
                raise TargetMissing(f"{self} allow_nth={self.allow_nth} out of "
                                    f"range 0..{len(hits)-1}")
            return hits[self.allow_nth], f"{strat}[nth={self.allow_nth}]"
        if len(hits) > 1:
            raise TargetAmbiguous(f"selector {self} matched {len(hits)} elements: {hits}")
        return hits[0], strat


@dataclass
class Postcondition:
    """Structured verification that the action landed (D7).

    `equals` asserts the field's value EXACTLY (an attribute value equals the
    expected, so '408' does not match a field showing '1408' — review finding).
    `contains` is an explicit substring test. A postcondition with neither is
    unverifiable and checks False rather than silently passing (review finding)."""
    field: Selector | None = None     # None = assert against the whole tree
    equals: str | None = None         # exact attribute-value match
    contains: str | None = None       # explicit substring (field block, or tree)
    attribute: str | None = None      # narrow `equals` to one attribute (e.g.
                                      # "Value"); None = any of _ATTR_KEYS

    def _attr_values(self, block: str):
        keys = [self.attribute] if self.attribute else Selector._ATTR_KEYS
        for ln in block.splitlines():
            for k in keys:
                v = Selector._attr(ln, k)
                if v is not None:
                    yield v

    def check(self, tree: str) -> bool:
        if self.equals is None and self.contains is None:
            return False   # nothing to verify -> not satisfied
        want = _norm(self.equals) if self.equals is not None else None
        if self.field is not None:
            try:
                idx, _ = self.field.resolve(tree)
            except LookupError:
                return False
            # Read the field's own line plus its subtree (deeper indent): AX
            # values commonly live on a child StaticText, not the container line.
            block = _element_block(tree, idx)
            if want is not None and not any(v == want for v in self._attr_values(block)):
                return False
            if self.contains is not None and self.contains not in block:
                return False
            return True
        # whole-tree assertion. `equals` is still EXACT: some element's attribute
        # value equals it (not a substring of the raw tree — '408' must not match
        # '1408' just because the digits appear somewhere; review finding).
        if want is not None and not any(v == want for v in self._attr_values(tree)):
            return False
        if self.contains is not None and self.contains not in tree:
            return False
        return True


@dataclass
class Step:
    action: str                       # click | set_value | type_text | press_key | scroll | secondary_action
    selector: Selector | None = None  # None for target-less actions (type_text, press_key)
    params: dict = field(default_factory=dict)   # value/text/key/direction/pages/action
    risk_class: str = "none"
    intent_text: str = ""
    expect: str | None = None         # P0 substring postcondition (kept for simple flows)
    postcondition: Postcondition | None = None   # P1 structured postcondition
    require_enabled: bool = True       # precondition: target must not be [disabled]
    is_submit: bool = False            # legacy classifier context
    into_sensitive_field: bool = False # legacy classifier context
    # Authored-once semantics (the model states meaning, not risk) and the facts
    # that judgment rested on. When present these supersede `risk_class` and the
    # legacy keyword classifier entirely.
    safety: "SafetyContract | None" = None
    binding: "ContextBinding | None" = None
    # Synthesized input (type_text/press_key) can be silently ignored by the
    # target app — the event posting "succeeding" proves nothing (advisor
    # finding). Such steps must therefore verify their effect; this flag is the
    # explicit, auditable opt-out for genuinely unverifiable cases.
    unverified_ok: bool = False


@dataclass
class Flow:
    name: str
    target: str
    steps: list[Step] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps({
            "name": self.name, "target": self.target,
            "steps": [{**asdict(s)} for s in self.steps],
        }, ensure_ascii=False, indent=2)

    @staticmethod
    def from_json(text: str) -> "Flow":
        d = json.loads(text)
        steps = []
        for s in d["steps"]:
            sel = Selector(**s["selector"]) if s.get("selector") else None
            post = None
            if s.get("postcondition"):
                pc = s["postcondition"]
                pf = Selector(**pc["field"]) if pc.get("field") else None
                post = Postcondition(field=pf, equals=pc.get("equals"),
                                     contains=pc.get("contains"),
                                     attribute=pc.get("attribute"))
            contract = None
            if s.get("safety"):
                from .contract import Resource, Destination, Evidence
                sf = s["safety"]
                r, dd = sf.get("resource") or {}, sf.get("destination") or {}
                contract = SafetyContract(
                    effects=sf.get("effects", []),
                    data_classes=sf.get("data_classes", ["none"]),
                    resource=Resource(**r) if r else Resource(),
                    destination=Destination(**dd) if dd else Destination(),
                    reversibility=sf.get("reversibility", "unknown"),
                    commit_point=sf.get("commit_point"),
                    evidence=[Evidence(**e) for e in sf.get("evidence", [])],
                    unknowns=sf.get("unknowns", []))
            binding = None
            if s.get("binding"):
                binding = ContextBinding(**s["binding"])
            steps.append(Step(action=s["action"], selector=sel,
                              params=s.get("params", {}),
                              risk_class=s.get("risk_class", "none"),
                              intent_text=s.get("intent_text", ""),
                              expect=s.get("expect"), postcondition=post,
                              require_enabled=s.get("require_enabled", True),
                              is_submit=s.get("is_submit", False),
                              into_sensitive_field=s.get("into_sensitive_field", False),
                              safety=contract, binding=binding,
                              unverified_ok=s.get("unverified_ok", False)))
        return Flow(name=d["name"], target=d["target"], steps=steps)


def _live_binding(driver, flow, step, action, target_line) -> ContextBinding:
    """Re-derive, from the live screen, the facts the contract was judged on."""
    import hashlib, json as _json
    rec = step.binding
    payload = {k: v for k, v in action.items() if k != "element_index"}
    ident = driver.identity(flow.target) if hasattr(driver, "identity") else flow.target
    return ContextBinding(
        bundle_id=ident if rec.bundle_id else None,
        team_id=rec.team_id,          # signer re-check is driver work (P2)
        role=(step.selector.role if step.selector else None) if rec.role else None,
        identifier=(_first_id(step.selector) if step.selector else None) if rec.identifier else None,
        ancestor=(step.selector.ancestor if step.selector else None) if rec.ancestor else None,
        operation=step.action if rec.operation else None,
        params_digest=(hashlib.sha256(
            _json.dumps(payload, sort_keys=True, default=str).encode()
        ).hexdigest()[:32]) if rec.params_digest else None,
        resource_identity=rec.resource_identity,
        selection_count=rec.selection_count,
        destination_identity=rec.destination_identity,
        label_fingerprint=(hashlib.sha256(target_line.encode()).hexdigest()[:16]
                           if rec.label_fingerprint else None),
        policy_version=rec.policy_version,
    )


def _first_id(sel) -> str | None:
    i = getattr(sel, "identifier", None)
    return i[0] if isinstance(i, list) and i else i


def _approved(approvals, i, step, action) -> bool:
    """Accept either a bound ApprovalToken (preferred) or a legacy {index: True}.

    A token is checked against the step's binding and contract, so consent given
    for one described action cannot silently authorize a different one."""
    if not approvals:
        return False
    tok = approvals.get(i, approvals.get(str(i)))
    if isinstance(tok, ApprovalToken):
        if step.safety is None or step.binding is None:
            return False
        ok, _why = tok.authorizes(step.binding, step.safety)
        if ok:
            tok.spend()
        return ok
    return bool(tok)


class ReplayResult:
    def __init__(self):
        self.ok = True
        self.log: list[dict] = []

    def record(self, **kw):
        self.log.append(kw)
        if kw.get("status") == "error":
            self.ok = False


def replay(driver: Driver, flow: Flow, approvals: dict[int, bool] | None = None,
           narrate=None) -> ReplayResult:
    """Run a flow deterministically. Zero model tokens.

    approvals: {step_index: True} pre-authorizes confirm-class steps.
    narrate:   optional callback(str) for the L4 user-facing narration.
    """
    approvals = approvals or {}
    res = ReplayResult()

    obs = driver.observe(flow.target, "tree_full")
    if obs["type"] != "observation":
        res.record(status="error", stage="initial_observe", detail=obs)
        return res

    # Synthesized-input steps must be verifiable: delivery of a keyboard event
    # is not evidence the app accepted it, so an unverified type/key step could
    # report success while nothing happened (or worse, happened elsewhere).
    for i, step in enumerate(flow.steps):
        if step.action in ("type_text", "press_key") and \
                step.postcondition is None and step.expect is None and \
                not step.unverified_ok:
            res.record(status="error", step=i, action=step.action,
                       error="unverifiable_input_step",
                       detail=f"step {i} ({step.action}) has no postcondition/"
                              "expect; add one or set unverified_ok=True explicitly")
            return res

    for i, step in enumerate(flow.steps):
        if narrate and step.intent_text:
            narrate(step.intent_text)

        action = {"name": step.action, **step.params}
        trace = None
        target_line = ""
        if step.selector is not None:
            try:
                action["element_index"], trace = step.selector.resolve(obs["tree"])
            except TargetAmbiguous as e:
                res.record(status="error", step=i, action=step.action,
                           error="target_ambiguous", detail=str(e))
                return res
            except TargetMissing as e:
                res.record(status="error", step=i, action=step.action,
                           error="target_missing", detail=str(e))
                return res
            idx = action["element_index"]
            target_line = next((l for l in obs["tree"].splitlines()
                                if re.match(rf"\s*{idx}\s", l)), "")
            # Precondition (TOCTOU/enabled): re-verify the resolved target on the
            # fresh observation right before acting.
            if step.require_enabled and "[disabled]" in target_line:
                res.record(status="error", step=i, action=step.action,
                           error="precondition_failed",
                           detail=f"target disabled: {target_line.strip()}")
                return res

        # Authorization. When the step carries an authored SafetyContract, the
        # decision comes from the deterministic policy over that contract, and
        # replay only re-validates the facts the judgment rested on — it never
        # re-infers meaning, and no keyword is consulted. Steps without a
        # contract fall back to the legacy label classifier.
        gate_desc = step.intent_text
        if step.safety is not None:
            if step.binding is not None:
                live = _live_binding(driver, flow, step, action, target_line)
                v = validate(step.binding, live)
                if not v.ok:
                    # The recorded meaning no longer applies. This is NOT a
                    # confirm — the user cannot consent to something whose
                    # description has gone stale; it needs re-authoring.
                    res.record(status="error", step=i, action=step.action,
                               error="contract_invalidated", detail="; ".join(v.hard))
                    return res
                if v.drift:
                    res.record(status="drift", step=i, detail="; ".join(v.drift))
            decision = policy(step.safety)
            risk, reason = decision.risk, decision.reason
            gate_desc = describe(step.safety, decision)
            if risk == "blocked":
                res.record(status="error", step=i, action=step.action,
                           error="unverifiable_action", detail=reason)
                return res
        else:
            detected, reason = safety.classify(
                action, target_line, is_submit=step.is_submit,
                into_sensitive_field=step.into_sensitive_field)
            risk = safety.merge(step.risk_class, detected)

        r = driver.act(obs["obs_id"], flow.target, action,
                       risk_class=risk, intent_text=gate_desc,
                       approved=_approved(approvals, i, step, action))

        if r["type"] in ("confirm_request", "handoff_request"):
            res.record(status="gated", step=i, gate=r["type"], risk=risk,
                       reason=reason, detail=r)
            return res
        if r["type"] == "error":
            res.record(status="error", step=i, action=step.action, detail=r)
            return res

        # D7: verify by polling to a monotonic deadline, not a blind sleep.
        post = step.postcondition
        if post is not None:
            obs = driver.wait_until(flow.target, post.check)
            if obs["type"] != "observation":
                seen = None
                last = obs.get("last_obs") or {}
                if post.field is not None and last.get("tree"):
                    try:
                        idx, _ = post.field.resolve(last["tree"])
                        seen = list(post._attr_values(_element_block(last["tree"], idx)))
                    except Exception:
                        seen = "field-not-found"
                res.record(status="error", step=i, action=step.action,
                           error="postcondition_timeout",
                           expected=post.equals, observed=seen)
                return res
        else:
            obs = driver.observe(flow.target, "tree_full")
            if obs["type"] != "observation":
                res.record(status="error", step=i, stage="verify_observe", detail=obs)
                return res
            if step.expect is not None and step.expect not in obs["tree"]:
                # settle a moment more, then re-check (transient render lag)
                obs = driver.wait_until(flow.target, lambda t: step.expect in t,
                                        timeout_ms=3000)
                if obs["type"] != "observation":
                    res.record(status="error", step=i, action=step.action,
                               error="expect_not_met",
                               detail=f"expect {step.expect!r} not in tree")
                    return res
        # Capture the value the verification actually read, so the agent can
        # tell the user the ANSWER — not just that something finished. A
        # calculator run that reports "done" without saying 408 has not really
        # answered the question that was asked.
        result_value = None
        if post is not None and post.field is not None and obs.get("tree"):
            try:
                fidx, _ = post.field.resolve(obs["tree"])
                vals = [v for v in post._attr_values(_element_block(obs["tree"], fidx))]
                result_value = vals[0] if vals else None
            except Exception:
                result_value = None
        res.record(status="ok", step=i, action=step.action, resolver=trace,
                   risk=risk, verified=step.expect or (post and "postcond"),
                   result=result_value)

    final = next((e.get("result") for e in reversed(res.log)
                  if e.get("status") == "ok" and e.get("result")), None)
    res.record(status="done", steps=len(flow.steps), result=final)
    return res


# ------------------------------------------------------------ example flow

def calculator_12x34() -> Flow:
    """Hand-authored flow — the codex demo as a deterministic, tokenless replay."""
    from .contract import Resource
    KP = "CalculatorKeypadView"   # ancestor scope: keeps repeated labels unambiguous
    # Contracts state MEANING; the deterministic policy derives authorization.
    # No keyword is consulted anywhere on this path.
    ENTRY = Resource(object_type="계산기 입력", scope="entry")
    keypad = SafetyContract(effects=["write_ephemeral"], resource=ENTRY,
                            reversibility="reversible")
    ac = SafetyContract(effects=["delete"], resource=ENTRY,
                        reversibility="reversible")   # clears the ENTRY -> none
    def bind(ident):
        return ContextBinding(bundle_id="com.apple.calculator", role="Button",
                              identifier=ident, ancestor=KP, operation="click")
    def digit(name, d):
        return Step("click", Selector(role="Button", identifier=name, ancestor=KP),
                    intent_text=f"{d} 버튼", safety=keypad, binding=bind(name))
    return Flow(name="calc_12x34", target="com.apple.calculator", steps=[
        Step("click", Selector(role="Button", identifier=["AllClear", "Clear"], ancestor=KP),
             intent_text="초기화", safety=ac, binding=bind("AllClear")),
        digit("One", "1"), digit("Two", "2"),
        Step("click", Selector(role="Button", identifier="Multiply", ancestor=KP),
             intent_text="곱하기", safety=keypad, binding=bind("Multiply")),
        digit("Three", "3"), digit("Four", "4"),
        Step("click", Selector(role="Button", identifier="Equals", ancestor=KP),
             intent_text="계산", safety=keypad, binding=bind("Equals"),
             # P1 structured postcondition: read the result field, poll until 408.
             postcondition=Postcondition(
                 field=Selector(role="ScrollArea", identifier="StandardInputView"),
                 equals="408", attribute="Value")),
    ])


if __name__ == "__main__":
    d = Driver()
    flow = calculator_12x34()
    print(flow.to_json())
    r = replay(d, flow, narrate=lambda s: print(f"  … {s}"))
    print("\nreplay ok:", r.ok)
    for e in r.log:
        print(" ", e)
