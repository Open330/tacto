"""Safety contract schema + deterministic authorization policy.

The architecture this replaces: a regex over a button's label. That failed the
moment a Korean calculator's "모두 지우기" (clear the entry) matched the same
keyword as "메일 삭제" (delete stored mail). The problem was never the language
list — it was the abstraction level.

The split (advisor-reviewed):

    AUTHORING (model may run, once)
        user intent + OS identity + AX context + UI text[UNTRUSTED]
            -> SafetyContract        (facts & meaning, NOT a risk level)

    DETERMINISTIC POLICY (code only)
        SafetyContract -> none | preapproved | confirm | handoff | blocked

    REPLAY (zero model tokens)
        re-validate the contract's invariants; never re-infer meaning.
        "Are X, Y, Z — the reasons this was judged safe — still true?"

Two rules give the design its teeth:
  * The model never declares a risk level. It states effects/resource/
    destination/reversibility and what it could NOT determine (`unknowns`).
    An unknown on an authorization-critical field blocks; it does not default
    to "safe", and it does not default to "ask the user" either — you cannot
    consent to something the system cannot describe.
  * Authorization can only originate from the user's own intent, never from
    text read off the screen. Evidence carries provenance, and UI content is
    permanently untrusted for authorization purposes.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field, asdict

POLICY_VERSION = 1

# ---------------------------------------------------------------- vocabulary

# What the action does to the world.
EFFECTS = {
    "read",              # observe only
    "write_ephemeral",   # edits session/entry state; does not outlive that scope
    "write_persistent",  # stores something that outlives the session
    "delete",            # removes something
    "transmit",          # sends data to a third party (message, form, upload)
    "financial",         # money moves
    "credential",        # creates/stores/changes an authentication secret
    "install",           # installs or runs newly acquired software
    "system_config",     # changes OS/security settings
}
DATA_CLASSES = {"none", "personal", "credential", "financial", "health",
                "biometric", "telemetry"}
REVERSIBILITY = {"reversible", "recoverable", "irreversible", "unknown"}
# Scope of the thing acted on — this is what separates a calculator's AC from
# deleting an account, even though both are "delete" with an identical AX shape.
RESOURCE_SCOPES = {"entry", "item", "collection", "account", "all", "unknown"}

# Evidence provenance. Only `user_intent` may ever authorize.
PROVENANCE = {"user_intent", "os_identity", "runtime_signal", "ui_content"}
UNTRUSTED_PROVENANCE = {"ui_content"}

# Fields whose meaning must be known before anything may run unattended.
AUTHORIZATION_CRITICAL = {"effects", "reversibility", "resource", "destination"}


@dataclass
class Evidence:
    source: str          # one of PROVENANCE
    field: str           # e.g. "bundle_id", "AXHelp", "user said"
    value: str
    # Which effects the user's own words cover, when something has read them
    # and said so in this vocabulary. Matching the words themselves was the
    # original plan and it never once fired: it compared a Korean sentence
    # against the string "write_persistent", so `preapproved` was unreachable
    # by anyone who did not happen to say a Python identifier out loud.
    covers: tuple = ()

    def authorizes(self) -> bool:
        """Only the user's own stated intent can ground authorization."""
        return self.source == "user_intent"


@dataclass
class Resource:
    object_type: str | None = None      # "calculator entry", "email", "account"
    object_identity: str | None = None  # stable id of the specific object
    selection_count: int | None = None  # 1 vs 1,000 changes the stakes
    scope: str = "unknown"              # RESOURCE_SCOPES


@dataclass
class Destination:
    kind: str = "none"                  # none | same_app | same_origin | third_party
    identity: str | None = None         # recipient, origin, workspace
    account: str | None = None


@dataclass
class SafetyContract:
    """What the action MEANS. Authored once; never a risk level."""
    effects: list[str] = field(default_factory=list)
    data_classes: list[str] = field(default_factory=lambda: ["none"])
    resource: Resource = field(default_factory=Resource)
    destination: Destination = field(default_factory=Destination)
    reversibility: str = "unknown"
    commit_point: bool | None = None     # is THIS the step that commits?
    evidence: list[Evidence] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)

    # -- schema validation (a malformed contract must not read as "safe") --
    def problems(self) -> list[str]:
        bad = []
        for e in self.effects:
            if e not in EFFECTS:
                bad.append(f"unknown effect {e!r} (use one of: {', '.join(sorted(EFFECTS))})")
        for d in self.data_classes:
            if d not in DATA_CLASSES:
                bad.append(f"unknown data_class {d!r} (use one of: {', '.join(sorted(DATA_CLASSES))})")
        if self.reversibility not in REVERSIBILITY:
            bad.append(f"unknown reversibility {self.reversibility!r} (use one of: {', '.join(sorted(REVERSIBILITY))})")
        if self.resource.scope not in RESOURCE_SCOPES:
            bad.append(f"unknown resource scope {self.resource.scope!r} (use one of: {', '.join(sorted(RESOURCE_SCOPES))})")
        if self.destination.kind not in ("none", "same_app", "same_origin", "third_party"):
            bad.append(f"unknown destination kind {self.destination.kind!r}")
        for ev in self.evidence:
            if ev.source not in PROVENANCE:
                bad.append(f"unknown evidence provenance {ev.source!r}")
        if not self.effects:
            bad.append("no effects stated")
        return bad

    def authorization_scope(self) -> list[Evidence]:
        """Authorization may only come from user intent — never from the screen."""
        return [e for e in self.evidence if e.authorizes()]


# ---------------------------------------------------------------- policy

@dataclass
class Decision:
    risk: str            # none | preapproved | confirm | handoff | blocked
    reason: str
    # `blocked` is deliberately distinct from `confirm`: when the system cannot
    # describe what it would be doing, asking the user to approve it is not a
    # safety control — there is nothing meaningful to consent to.


def _covers(scope: list[Evidence], contract: "SafetyContract") -> bool:
    """Does the user's own stated intent actually cover this action?

    Pre-approval means the request named the thing. Evidence read off the screen
    can describe what a control does, but it can never be the reason we are
    allowed to press it — so only `user_intent` evidence counts, and it has to
    mention the effect and, when data leaves, the destination."""
    if not scope:
        return False
    import re as _re
    said = " ".join(e.value.lower() for e in scope)

    def mentioned(term: str) -> bool:
        # Word-ish boundary so "bob" does not match "bobby"; Hangul has no \b,
        # so fall back to a plain containment test for non-ASCII terms.
        t = term.lower()
        if t.isascii():
            return _re.search(rf"(?<![\w@.]){_re.escape(t)}(?![\w@.])", said) is not None
        return t in said

    # EVERY effect must be covered — approving "save" should not also license
    # the "transmit" the same contract declares (advisor finding: any() let one
    # match cover the rest).
    declared = set()
    for e in scope:
        declared |= {str(c) for c in (e.covers or ())}
    if declared:
        if not set(contract.effects) <= declared:
            return False
    elif not all(mentioned(eff) for eff in contract.effects):
        return False
    dest = contract.destination.identity
    if contract.destination.kind == "third_party" and dest:
        return mentioned(dest)
    return True


# Severity order. Composing effects takes the MAXIMUM of what each one needs:
# an early return per effect meant adding a destructive effect could REMOVE the
# confirmation the others required — `write_persistent` alone asked for one,
# `write_persistent` plus `delete` on a reversible entry asked for none at all
# (advisor finding, reproduced). Authorization must never fall when the action
# grows.
_SEVERITY = {"none": 0, "preapproved": 1, "confirm": 2, "handoff": 3, "blocked": 4}


def _worst(decisions: list[Decision]) -> Decision:
    return max(decisions, key=lambda d: _SEVERITY[d.risk])


def _for_effect(effect: str, c: SafetyContract, data: set) -> Decision:
    """What ONE effect requires, judged on its own terms."""
    if effect == "credential":
        return Decision("handoff", "credential material — the user enters it")
    if effect == "system_config":
        return Decision("handoff", "changes OS/security configuration")
    if effect == "financial":
        # Never below confirm on the user's own say-so alone: money moving is
        # the case where "the request mentioned it" is not enough. The
        # commit_point distinction only says WHICH step stops, not whether one
        # does — previously `commit_point=False` produced `preapproved` with no
        # user evidence at all.
        return Decision("confirm", "financial effect")
    if effect == "install":
        return Decision("confirm", "installs or runs new software")
    if effect == "transmit":
        if c.destination.kind == "third_party":
            return Decision("confirm", f"sends {sorted(data)} to "
                                       f"{c.destination.identity or 'a third party'}")
        return Decision("confirm", "sends data out of this app")
    if effect == "delete":
        # Judged by WHAT is deleted and whether it can come back — not by the
        # word on the button. This is what makes a calculator's AC and "delete
        # account" different despite identical AX shape.
        if c.resource.scope == "entry" and c.reversibility == "reversible":
            return Decision("none", "clears an ephemeral entry; reversible")
        if c.reversibility == "irreversible" or c.resource.scope in ("account", "all"):
            return Decision("confirm", f"irreversible/wide deletion "
                                       f"(scope={c.resource.scope}, {c.reversibility})")
        if (c.resource.selection_count or 1) > 1:
            return Decision("confirm", f"deletes {c.resource.selection_count} items")
        return Decision("confirm", "deletes stored data")
    if effect == "write_persistent":
        if c.destination.kind in ("none", "same_app") and data <= {"none"}:
            # `preapproved` exists only when the user's own words cover the
            # action, so check the evidence rather than trusting the label.
            if _covers(c.authorization_scope(), c):
                return Decision("preapproved", "user's request covers this write")
            return Decision("confirm", "stores data — the request did not name it")
        return Decision("confirm", "persists data beyond the session")
    if effect in ("read", "write_ephemeral"):
        return Decision("none", "reads or edits an ephemeral entry")
    return Decision("confirm", f"unclassified effect {effect!r}")


def policy(c: SafetyContract) -> Decision:
    """SafetyContract -> authorization level. Deterministic; no model, no tokens."""
    problems = c.problems()
    if problems:
        return Decision("blocked", f"malformed contract: {'; '.join(problems)}")

    # A model writes "resource.scope", not "resource". Matching only the bare
    # name let "I could not determine the resource" through as if it had been
    # determined — a fail-open in the one place that must fail closed.
    unknown_critical = [u for u in c.unknowns
                        if u.split(".", 1)[0] in AUTHORIZATION_CRITICAL]
    if unknown_critical:
        return Decision("blocked", f"undetermined {', '.join(unknown_critical)} — "
                                   "cannot describe the action well enough to run it")

    eff, data = set(c.effects), set(c.data_classes)
    # A credential in the DATA is as much the user's to enter as one in the
    # effects; this used to be checked only alongside the effect.
    decisions = [_for_effect(e, c, data) for e in sorted(eff)]
    if "credential" in data:
        decisions.append(Decision("handoff", "credential material — the user enters it"))
    if not decisions:                      # `problems()` already rejects this
        return Decision("blocked", "no effects stated")
    decision = _worst(decisions)
    # `unknowns` is advisory model output. The actual contract fields still
    # have to support unattended permission even when that list is empty.
    # Keep this independent of the combined risk: adding a second effect must
    # not downgrade an incomplete write from blocked to confirm.
    if eff & {"write_ephemeral", "write_persistent"}:
        missing = []
        if c.reversibility == "unknown":
            missing.append("reversibility")
        if c.resource.scope == "unknown":
            missing.append("resource.scope")
        if c.destination.kind in ("same_origin", "third_party") and not c.destination.identity:
            missing.append("destination.identity")
        if missing:
            return Decision("blocked", "undetermined " + ", ".join(missing))
    return decision


# ---------------------------------------------------------------- binding

@dataclass
class ContextBinding:
    """The facts the contract's judgment rested on, recorded for re-validation.

    Tiered on purpose: identity/operation/resource/destination changing means the
    recorded meaning no longer applies (hard). A label changing is ordinary
    localization or an app update (soft drift) — treating that as a hard failure
    would break flows on every translation."""
    bundle_id: str | None = None
    team_id: str | None = None            # code-signing identity (high-risk steps)
    role: str | None = None
    identifier: str | None = None
    title: str | list[str] | None = None
    desc: str | list[str] | None = None
    value: str | list[str] | None = None
    ancestor: str | None = None
    operation: str | None = None          # the action primitive
    params_digest: str | None = None      # digest of the payload
    resource_identity: str | None = None
    selection_count: int | None = None
    destination_identity: str | None = None
    label_fingerprint: str | None = None  # SOFT: drift only
    policy_version: int = POLICY_VERSION
    # Where a pinned identity can be re-read from the screen, as
    # {"role":…, "identifier":…, "attribute":…}. A pin with no witness is
    # refused at replay rather than quietly passing, so without these a recipe
    # cannot tie consent to WHICH document it is saving or WHO it is messaging —
    # only to which button it presses. They say how to check the fact, not what
    # the fact is, so they are not themselves invariants.
    resource_identity_from: dict | None = None
    destination_identity_from: dict | None = None
    subrole: str | None = None

    HARD_FIELDS = ("bundle_id", "team_id", "role", "identifier", "subrole", "title", "desc", "value", "ancestor",
                   "operation", "params_digest", "resource_identity",
                   "selection_count", "destination_identity")

    def digest(self) -> str:
        data = asdict(self)
        if self.subrole is None:
            data.pop('subrole')  # preserve existing approval digests
        return hashlib.sha256(
            json.dumps(data, sort_keys=True, default=str).encode()
        ).hexdigest()[:32]


@dataclass
class Validation:
    hard: list[str] = field(default_factory=list)   # meaning no longer applies
    drift: list[str] = field(default_factory=list)  # re-evaluation advisable
    unverifiable: list[str] = field(default_factory=list)  # pinned but unobservable

    @property
    def ok(self) -> bool:
        # An invariant we pinned but cannot re-observe is not a pass. Treating
        # it as one is how a recorded resource/destination silently "stayed the
        # same" forever (advisor finding): fail closed and say which field.
        return not self.hard and not self.unverifiable


def validate(recorded: ContextBinding, live: ContextBinding) -> Validation:
    """Is the contract's basis still true? Never re-infers meaning."""
    v = Validation()
    if recorded.subrole is None and live.subrole is not None:
        v.hard.append("subrole was not pinned in the recorded action")
    if recorded.policy_version != live.policy_version:
        v.hard.append(f"policy_version {recorded.policy_version}->{live.policy_version}")
    for f in ContextBinding.HARD_FIELDS:
        want, got = getattr(recorded, f), getattr(live, f)
        if want is None:            # not pinned at authoring time
            continue
        if got is None:             # pinned, but nothing re-observed it
            v.unverifiable.append(f)
            continue
        if got != want:
            v.hard.append(f"{f}: {want!r} -> {got!r}")
    if recorded.label_fingerprint and live.label_fingerprint and \
            recorded.label_fingerprint != live.label_fingerprint:
        v.drift.append("label changed (localization or app update)")
    return v


# ---------------------------------------------------------------- approval

@dataclass
class ApprovalToken:
    """Consent bound to a specific action, not a step number.

    `{step_index: True}` approved "step 3" — it would still read as approved if
    step 3 came to mean something else. A token is tied to the binding digest,
    the effects consented to, the destination, and an expiry."""
    binding_digest: str
    effects: list[str]
    destination_identity: str | None
    issued_at: float
    expires_at: float
    one_shot: bool = True
    _spent: bool = False

    def authorizes(self, binding: ContextBinding, contract: SafetyContract,
                   now: float | None = None) -> tuple[bool, str]:
        now = now or time.time()
        if self._spent:
            return False, "approval already used"
        if now > self.expires_at:
            return False, "approval expired"
        if self.binding_digest != binding.digest():
            return False, "approval was for a different action/target/payload"
        if set(contract.effects) - set(self.effects):
            return False, (f"action now has effects the user did not approve: "
                           f"{sorted(set(contract.effects) - set(self.effects))}")
        if contract.destination.identity != self.destination_identity:
            return False, "destination changed since approval"
        return True, "approved"

    def spend(self):
        if self.one_shot:
            self._spent = True


def issue_approval(binding: ContextBinding, contract: SafetyContract,
                   ttl_seconds: float = 120.0) -> ApprovalToken:
    """Called only after a human said yes to THIS described action."""
    now = time.time()
    return ApprovalToken(binding_digest=binding.digest(),
                         effects=list(contract.effects),
                         destination_identity=contract.destination.identity,
                         issued_at=now, expires_at=now + ttl_seconds)


def describe(contract: SafetyContract, decision: Decision) -> str:
    """Plain-language description of what is about to happen, for the gate.
    A confirmation the user cannot understand is not consent."""
    r = contract.resource
    what = r.object_type or "항목"
    if r.selection_count and r.selection_count > 1:
        what = f"{what} {r.selection_count}개"
    verb = {"delete": "삭제", "transmit": "전송", "financial": "결제",
            "write_persistent": "저장", "install": "설치"}
    acts = [verb[e] for e in contract.effects if e in verb] or ["실행"]
    line = f"{what}을(를) {'·'.join(acts)}합니다"
    if contract.destination.kind == "third_party" and contract.destination.identity:
        line += f" (받는 곳: {contract.destination.identity})"
    if contract.reversibility == "irreversible":
        line += ". 되돌릴 수 없습니다"
    return line


# ---------------------------------------------------------------- envelope

# What effects a vocabulary knows about, so a request cannot ask for one that
# has no policy.
EFFECTS = {"read", "write_ephemeral", "write_persistent", "delete", "transmit",
           "financial", "credential", "install", "system_config"}
SCOPES = {"entry", "item", "collection", "account", "all"}
REVERSIBILITY = {"reversible", "recoverable", "irreversible", "unknown"}


@dataclass
class Envelope:
    """What one request authorizes, issued before any screen has been seen.

    A task with no recipe cannot be approved step by step in advance, because
    nobody knows the steps. What CAN be settled in advance is the shape of what
    the user asked for: which effects it covers, on what scale, and how
    recoverable. The path is what is unknown, not the range (advisor finding).

    So this is issued from the utterance alone — the user's own words, which is
    the only evidence that may authorize anything — and every action worked out
    against the screen is then judged twice: `policy()` says what the effect
    needs, and `admits()` says whether the request covered it at all. An effect
    outside the envelope is not a risky action, it is a different request.
    """
    quote: str = ""                      # the user's words this rests on
    allows: frozenset = frozenset()      # effects the request covers
    scope: str = "entry"
    reversibility: str = "reversible"

    @classmethod
    def minimal(cls) -> "Envelope":
        """Looking and typing into the thing in front of you, nothing more.

        The default when no reading produced one: enough to work a calculator,
        never enough to save, send or delete.
        """
        return cls(allows=frozenset({"read", "write_ephemeral"}))

    def admits(self, effects: set) -> bool:
        return set(effects) <= set(self.allows)

    def contract_for(self, effects: set, object_type: str) -> SafetyContract:
        """A contract for one concrete action, judged by the usual policy.

        The effects come from the control, which is untrusted and therefore only
        ever says what an action IS. The scope and the user's words come from
        the envelope, which is why this can reach `preapproved` without the
        screen having voted on it.
        """
        return SafetyContract(
            effects=sorted(effects),
            reversibility=self.reversibility,
            resource=Resource(object_type=object_type, scope=self.scope),
            evidence=([Evidence(source="user_intent", field="asked",
                                value=self.quote, covers=tuple(sorted(self.allows)))]
                      if self.quote else []))
