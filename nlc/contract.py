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
    "write_ephemeral",   # edits an entry/field; lost on close, trivially undone
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
                bad.append(f"unknown effect {e!r}")
        for d in self.data_classes:
            if d not in DATA_CLASSES:
                bad.append(f"unknown data_class {d!r}")
        if self.reversibility not in REVERSIBILITY:
            bad.append(f"unknown reversibility {self.reversibility!r}")
        if self.resource.scope not in RESOURCE_SCOPES:
            bad.append(f"unknown resource scope {self.resource.scope!r}")
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


def policy(c: SafetyContract) -> Decision:
    """SafetyContract -> authorization level. Deterministic; no model, no tokens."""
    problems = c.problems()
    if problems:
        return Decision("blocked", f"malformed contract: {'; '.join(problems)}")

    unknown_critical = [u for u in c.unknowns if u in AUTHORIZATION_CRITICAL]
    if unknown_critical:
        return Decision("blocked", f"undetermined {', '.join(unknown_critical)} — "
                                   "cannot describe the action well enough to run it")

    eff, data = set(c.effects), set(c.data_classes)

    # The user must act themselves: secrets and security posture.
    if "credential" in eff or "credential" in data:
        return Decision("handoff", "credential material — the user enters it")
    if "system_config" in eff:
        return Decision("handoff", "changes OS/security configuration")

    # Money and third-party transmission always stop at the commit point.
    if "financial" in eff:
        return Decision("confirm" if c.commit_point is not False else "preapproved",
                        "financial effect")
    if "transmit" in eff and c.destination.kind == "third_party":
        return Decision("confirm", f"sends {sorted(data)} to {c.destination.identity or 'a third party'}")
    if "install" in eff:
        return Decision("confirm", "installs or runs new software")

    # Deletion is judged by WHAT is deleted and whether it can come back —
    # not by the word on the button. This is what makes a calculator's AC and
    # "delete account" different despite identical AX shape.
    if "delete" in eff:
        if c.resource.scope == "entry" and c.reversibility == "reversible":
            return Decision("none", "clears an ephemeral entry; reversible")
        if c.reversibility == "irreversible" or c.resource.scope in ("account", "all"):
            return Decision("confirm", f"irreversible/wide deletion "
                                       f"(scope={c.resource.scope}, {c.reversibility})")
        if (c.resource.selection_count or 1) > 1:
            return Decision("confirm", f"deletes {c.resource.selection_count} items")
        return Decision("confirm", "deletes stored data")

    if "write_persistent" in eff:
        if c.destination.kind in ("none", "same_app") and data <= {"none"}:
            return Decision("preapproved", "stores non-sensitive data in the same app")
        return Decision("confirm", "persists data beyond the session")

    if eff <= {"read"}:
        return Decision("none", "read-only")
    if eff <= {"read", "write_ephemeral"}:
        return Decision("none", "edits an ephemeral entry")

    return Decision("confirm", f"unclassified effects {sorted(eff)}")


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
    ancestor: str | None = None
    operation: str | None = None          # the action primitive
    params_digest: str | None = None      # digest of the payload
    resource_identity: str | None = None
    selection_count: int | None = None
    destination_identity: str | None = None
    label_fingerprint: str | None = None  # SOFT: drift only
    policy_version: int = POLICY_VERSION

    HARD_FIELDS = ("bundle_id", "team_id", "role", "identifier", "ancestor",
                   "operation", "params_digest", "resource_identity",
                   "selection_count", "destination_identity")

    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(asdict(self), sort_keys=True, default=str).encode()
        ).hexdigest()[:32]


@dataclass
class Validation:
    hard: list[str] = field(default_factory=list)   # meaning no longer applies
    drift: list[str] = field(default_factory=list)  # re-evaluation advisable

    @property
    def ok(self) -> bool:
        return not self.hard


def validate(recorded: ContextBinding, live: ContextBinding) -> Validation:
    """Is the contract's basis still true? Never re-infers meaning."""
    v = Validation()
    if recorded.policy_version != live.policy_version:
        v.hard.append(f"policy_version {recorded.policy_version}->{live.policy_version}")
    for f in ContextBinding.HARD_FIELDS:
        want, got = getattr(recorded, f), getattr(live, f)
        if want is None:            # not pinned at authoring time
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
