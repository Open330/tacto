"""NLC safety rule engine — the §1.5 confirmations policy as code (D6).

Maps an action + its resolved target's context to a risk_class. This is a
deliberately conservative, auditable rule set: when a signal is ambiguous it
escalates rather than waves through. The classifier is deterministic and takes
no model tokens; it runs on *every* replay execution, not just authoring.

risk_class levels (rising):
  none        — observation / non-mutating, always allowed
  preapproved — allowed only if the original request named it (login, upload…)
  confirm     — blocking confirm right before the action, even if pre-approved
  handoff     — the user must do it themselves (password submit, safety bypass)
"""

from __future__ import annotations

import re

# Signals matched against the target's tree line (label/identifier/description),
# lowercased. Word-ish boundaries keep "clear" from firing on "nuclear".
_HANDOFF = [
    r"\bchange password\b", r"\bnew password\b", r"\bconfirm password\b",
    r"\bnot secure\b", r"\bproceed anyway\b", r"\bbypass\b",
]
_CONFIRM = [
    r"\bdelete\b", r"\bremove\b", r"\btrash\b", r"\bempty trash\b",
    r"\bpay\b", r"\bpurchase\b", r"\bbuy now\b", r"\bplace order\b",
    r"\bcheckout\b", r"\btransfer\b", r"\bsend\b", r"\bpost\b", r"\bpublish\b",
    r"\bsubmit\b", r"\bconfirm\b", r"\bunsubscribe\b", r"\bsubscribe\b",
    r"\bcreate account\b", r"\bsign up\b", r"\bapi key\b",
    r"\bcaptcha\b", r"\bcaptcha\b",
]
_PREAPPROVED = [
    r"\blog ?in\b", r"\bsign ?in\b", r"\bupload\b", r"\bchoose file\b",
    r"\bmove\b", r"\brename\b", r"\ballow\b", r"\bage verif",
]

_H = [re.compile(p) for p in _HANDOFF]
_C = [re.compile(p) for p in _CONFIRM]
_P = [re.compile(p) for p in _PREAPPROVED]

# Field kinds that count as sensitive-data entry -> transmission on submit.
_SENSITIVE_FIELD = re.compile(
    r"password|passcode|otp|one[- ]time|card number|cvv|ssn|social security|"
    r"account number|routing")


def classify(action: dict, target_line: str = "", *, into_sensitive_field=False,
             is_submit=False) -> tuple[str, str]:
    """Return (risk_class, reason).

    action:       the L2 action dict (name + params).
    target_line:  the resolved target's tree line (its label/identifier).
    into_sensitive_field / is_submit: caller-supplied context flags for
                  typing into credential fields and for final-submit steps.
    """
    name = action.get("name", "")
    low = target_line.lower()

    # Observation-like / pure navigation never gates.
    if name in ("scroll",):
        return "none", "non-mutating navigation"

    # Typing a password/OTP is transmission-adjacent -> hand off to the user.
    if name in ("type_text", "set_value") and into_sensitive_field:
        return "handoff", "credential entry: user must type it"
    if name in ("type_text", "set_value") and _SENSITIVE_FIELD.search(low):
        return "handoff", "target looks like a credential field"

    for rx in _H:
        if rx.search(low):
            return "handoff", f"handoff signal: {rx.pattern}"

    # A final submit inherits the risk of what it submits.
    if is_submit:
        return "confirm", "final submit step"

    for rx in _C:
        if rx.search(low):
            return "confirm", f"confirm signal: {rx.pattern}"
    for rx in _P:
        if rx.search(low):
            return "preapproved", f"preapproval-eligible: {rx.pattern}"

    return "none", "no risk signal"


def merge(recorded: str, classified: str) -> str:
    """Escalation-only merge: the stricter of the recipe's declared risk_class
    and the live classifier wins. A recipe can raise risk but never lower what
    the classifier detects at run time."""
    order = {"none": 0, "preapproved": 1, "confirm": 2, "handoff": 3}
    return recorded if order[recorded] >= order[classified] else classified


# ---- richer authorization model (codex browser-safety guidance) -------------
#
# A regex over a button's name is a first-pass signal, not a real authorization
# decision — a "Delete" label is not necessarily a data deletion, and typing a
# value into a form can be a *transmission* the name never hints at. The web
# safety review recommends deciding on the tuple, not the label:
#
#   effect        read | write | transmit | delete | financial | destructive
#   data_class    none | personal | credential | financial | biometric | health
#   destination   same-origin | third-party (origin/account the data reaches)
#   authorization user-authored intent | pre-approved scope | none
#
# and binding an approval token to (action + target + payload-digest +
# destination + expiry), re-evaluated on navigation/account/file change. A page
# that *says* "the user approved this" is untrusted content, never an approval.
# `classify()` above is the label-signal layer; this tuple layer is the P2+
# authorization engine that sits over it. Documented here as the agreed shape;
# the current build ships the label layer plus the escalation merge.
