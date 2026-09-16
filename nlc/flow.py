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
            if val is None or val.lower() not in [_norm(i).lower() for i in ids]:
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
                    if v is not None and v.lower() == want:
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
    is_submit: bool = False            # context for the safety classifier
    into_sensitive_field: bool = False # context for the safety classifier


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
                                     contains=pc.get("contains"))
            steps.append(Step(action=s["action"], selector=sel,
                              params=s.get("params", {}),
                              risk_class=s.get("risk_class", "none"),
                              intent_text=s.get("intent_text", ""),
                              expect=s.get("expect"), postcondition=post,
                              require_enabled=s.get("require_enabled", True),
                              is_submit=s.get("is_submit", False),
                              into_sensitive_field=s.get("into_sensitive_field", False)))
        return Flow(name=d["name"], target=d["target"], steps=steps)


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

        # D6 on every replay: classify live and take the stricter of recipe vs
        # detected risk. The classifier never lowers a recipe's declared risk.
        detected, reason = safety.classify(
            action, target_line, is_submit=step.is_submit,
            into_sensitive_field=step.into_sensitive_field)
        risk = safety.merge(step.risk_class, detected)

        r = driver.act(obs["obs_id"], flow.target, action,
                       risk_class=risk, intent_text=step.intent_text,
                       approved=approvals.get(i, False))

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
                res.record(status="error", step=i, action=step.action,
                           error="postcondition_timeout", detail=obs.get("last_obs"))
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
        res.record(status="ok", step=i, action=step.action, resolver=trace,
                   risk=risk, verified=step.expect or (post and "postcond"))

    res.record(status="done", steps=len(flow.steps))
    return res


# ------------------------------------------------------------ example flow

def calculator_12x34() -> Flow:
    """Hand-authored flow — the codex demo as a deterministic, tokenless replay."""
    KP = "CalculatorKeypadView"   # ancestor scope: keeps repeated labels unambiguous
    def digit(name, d):
        return Step("click", Selector(role="Button", identifier=name, ancestor=KP),
                    intent_text=f"{d} 버튼")
    return Flow(name="calc_12x34", target="com.apple.calculator", steps=[
        Step("click", Selector(role="Button", identifier=["AllClear", "Clear"], ancestor=KP), intent_text="초기화"),
        digit("One", "1"), digit("Two", "2"),
        Step("click", Selector(role="Button", identifier="Multiply", ancestor=KP), intent_text="곱하기"),
        digit("Three", "3"), digit("Four", "4"),
        Step("click", Selector(role="Button", identifier="Equals", ancestor=KP), intent_text="계산",
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
