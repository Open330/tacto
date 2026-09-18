"""Did the task succeed — judged apart from whatever the run says about itself.

The engine can tell you a recipe finished and a model can tell you it is done.
Neither is the question. A selector can match uniquely, a binding can hold, a
postcondition can be true, and the thing that happened can still not be what
was asked for — and for someone who cannot check their own computer, a wrong
result reported as success is worse than a refusal.

So a receipt is issued by something that did not run the task, from three
separate questions (advisor's formulation):

    G   the goal:      is what was wanted now there?
    F   the invariant: is what should not have moved still where it was?
    H   the history:   did anything happen that was not allowed to?

All three are needed. A screen that ends up looking right says nothing about a
message sent halfway through, which is why H is about the effects that
occurred and not about the final picture.

What this deliberately does NOT do is judge Korean. It cannot decide whether
"문서 저장하기" was achieved by looking at a window title. What it can do is say
exactly which of the three it checked and which it could not — and a receipt
that reports `미확인` is doing its job, where one that guesses is the failure
mode this whole module exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# A receipt's state. `done` is the only one that means the task succeeded, and
# it is deliberately hard to reach.
DONE = "done"              # wanted thing observed, nothing forbidden happened
PARTIAL = "partial"        # some of it, and we can say which
UNVERIFIED = "unverified"  # it ran; nothing confirmed the result
REFUSED = "refused"        # stopped before the effect, on purpose
RECOVERED = "recovered"    # an effect happened and was undone — not success


@dataclass
class OutcomeSpec:
    """What would have to be true for this task to have succeeded.

    Written per task kind by a person, not per run by a model: "성공 조건을 잘
    써라" produces whatever the model finds convenient, and the point of a
    verifier is to be the thing that was not convinced (advisor finding).
    """
    must_read: list = field(default_factory=list)   # values the run must report
    must_not_change: list = field(default_factory=list)  # literals to still see
    forbidden: frozenset = frozenset()              # effects that must not occur

    @classmethod
    def for_envelope(cls, envelope) -> "OutcomeSpec":
        """The weakest spec a request implies on its own.

        Anything outside what was asked for is forbidden — that much follows
        from the envelope without anyone writing a task definition. It is not
        much, and pretending otherwise is why this returns so little.
        """
        from .contract import EFFECTS
        allows = set(getattr(envelope, "allows", ()) or ())
        return cls(forbidden=frozenset(EFFECTS - allows))


@dataclass
class TaskReceipt:
    """What a verifier is prepared to say, and what it is not.

    `unchecked` is the field that matters most: it is how "we did not look"
    stops being reported as "it was fine".
    """
    state: str
    checked: list = field(default_factory=list)
    unchecked: list = field(default_factory=list)
    problems: list = field(default_factory=list)
    said: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.state == DONE

    def say(self) -> str:
        """The receipt in the words the person gets."""
        if self.state == DONE:
            body = "\n".join(f"결과는 {v} 입니다" for v in self.said) or "다 됐어요."
        elif self.state == PARTIAL:
            body = "여기까지만 됐어요: " + "; ".join(self.problems)
        elif self.state == RECOVERED:
            body = "하던 일을 되돌렸어요. 부탁하신 일은 아직 안 됐습니다."
        elif self.state == REFUSED:
            body = "; ".join(self.problems) or "진행하지 않았어요."
        else:
            body = ("실행은 했는데 제대로 됐는지 확인하지 못했어요."
                    if not self.said else
                    "\n".join(f"결과는 {v} 입니다" for v in self.said)
                    + "\n다만 제대로 됐는지는 확인하지 못했어요.")
        # The unchecked list always stays in the receipt, and is only SAID when
        # something is actually wrong. "원한 결과가 맞는지" is missing from every
        # spec that has no `must_read`, so saying it on every success would put
        # a caveat on every sentence — and a warning that is always there is one
        # nobody reads, which is its own way of hiding something.
        if self.unchecked and self.state != DONE:
            body += "\n(확인하지 못한 것: " + ", ".join(self.unchecked) + ")"
        return body


def verify(spec: OutcomeSpec, result, after_tree: str = "") -> TaskReceipt:
    """Judge a finished run against what should have been true.

    Takes the run as EVIDENCE, never as a verdict: `result.ok` says the loop
    reached its end, which is the input to this question and not the answer.
    """
    checked, unchecked, problems = [], [], []

    # H — what actually happened, against what the request covered. This is the
    # one check that does not depend on the screen, and the one a final picture
    # could never answer: a message sent halfway through leaves no trace in it.
    happened = set(getattr(result, "effects", ()) or ())
    stray = happened & set(spec.forbidden)
    if stray:
        problems.append("부탁하신 범위 밖의 일이 일어났습니다: "
                        + ", ".join(sorted(stray)))
    checked.append("일어난 일이 부탁하신 범위 안인가")

    # G — is the wanted thing there? Only answerable when the spec named
    # something to look for. Saying so beats guessing.
    said = list(getattr(result, "said", ()) or [])
    if spec.must_read:
        missing = [v for v in spec.must_read
                   if not any(v in str(s) for s in said)]
        if missing:
            problems.append("읽어야 할 값을 못 봤습니다: " + ", ".join(missing))
        checked.append("원한 값이 결과에 있는가")
    else:
        unchecked.append("원한 결과가 맞는지")

    # F — is what should not have moved still there?
    if spec.must_not_change:
        if after_tree:
            gone = [v for v in spec.must_not_change if v not in after_tree]
            if gone:
                problems.append("그대로여야 할 것이 사라졌습니다: "
                                + ", ".join(gone))
            checked.append("건드리지 말아야 할 것이 그대로인가")
        else:
            unchecked.append("건드리지 말아야 할 것")

    if problems:
        state = PARTIAL if getattr(result, "verified", False) else UNVERIFIED
    elif not getattr(result, "ok", False):
        state = REFUSED
        problems = [getattr(result, "why", "") or "진행하지 않았습니다"]
    elif getattr(result, "verified", False) and not unchecked:
        state = DONE
    elif getattr(result, "verified", False):
        # It confirmed what it looked at, and there were things it did not look
        # at. That is not the same as success and does not get to be called it.
        state = DONE if not spec.must_read and said else UNVERIFIED
    else:
        state = UNVERIFIED
    return TaskReceipt(state=state, checked=checked, unchecked=unchecked,
                       problems=problems, said=said)
