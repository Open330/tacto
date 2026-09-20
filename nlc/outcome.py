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
import unicodedata


def same_value(expected, observed) -> bool:
    """Identity comparisons are exact; bob must never authorize bobby."""
    if isinstance(expected, str) and isinstance(observed, str):
        return unicodedata.normalize("NFC", expected.strip()) == unicodedata.normalize(
            "NFC", observed.strip())
    return type(expected) is type(observed) and expected == observed


def observed_absent(value) -> bool:
    # Missing evidence (None) is not evidence of absence.
    return value is False or value == "" or value == []

# A receipt's state. `done` is the only one that means the task succeeded, and
# it is deliberately hard to reach.
EVERY_EFFECT = frozenset({"read", "write_ephemeral", "write_persistent",
                          "delete", "transmit", "financial", "credential",
                          "install", "system_config"})

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
    kind: str = ""                                  # which task kind, if any
    required: tuple = ()                            # the checks that kind needs
    expected: dict = field(default_factory=dict)    # what each one expects

    @property
    def commit_checks(self) -> tuple:
        return tuple(c for c in self.required if c.when == "before_commit")

    def before_commit(self, observed: dict) -> list[str]:
        problems = []
        for check in self.commit_checks:
            got = observed.get(check.name)
            if got is None:
                problems.append(f"{check.name}: 실행 전에 확인하지 못했습니다")
            elif check.kind == "reports":
                want = self.expected.get(check.name)
                if want is None:
                    problems.append(f"{check.name}: 요청에서 기대값을 정하지 못했습니다")
                elif not same_value(want, got):
                    problems.append(f"{check.name}: 기대 {want!r}, 관찰 {got!r}")
            elif check.kind == "absent":
                if not observed_absent(got):
                    problems.append(f"{check.name}: 비어 있지 않습니다 ({got!r})")
            else:
                problems.append(f"{check.name}: 지원하지 않는 실행 전 검사")
        return problems

    def unfilled(self) -> list:
        """Checks this kind requires that nobody supplied a value for.

        Named rather than dropped: a condition with no expected value has not
        been satisfied, it has been skipped, and the difference is the whole
        reason a receipt carries an `unchecked` list.
        """
        return [c.name for c in self.required
                if c.needs_value and c.name not in self.expected]

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
    # The facts read off the screen just before the commit. Observations only.
    seen = dict(getattr(result, "witnessed", {}) or {})

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
    wants_any = any(c.kind == "any_value" for c in spec.required)
    if wants_any:
        if not said:
            problems.append("사람에게 전달된 값이 없습니다")
        checked.append("값이 사람에게 전달됐는가")
    if spec.must_read:
        witnessed_values = [str(v) for v in seen.values()]
        missing = [v for v in spec.must_read
                   if not any(same_value(v, str(s)) for s in said)
                   and not any(same_value(v, w) for w in witnessed_values)]
        if missing:
            problems.append("읽어야 할 값을 못 봤습니다: " + ", ".join(missing))
        checked.append("원한 값이 결과에 있는가")
    elif not wants_any:
        unchecked.append("원한 결과가 맞는지")

    # F — is what should not have moved still there?
    named_invariants = {spec.expected[c.name] for c in spec.required
                        if c.kind == "unchanged" and c.name in spec.expected
                        and isinstance(spec.expected[c.name], str)}
    legacy_invariants = [v for v in spec.must_not_change if v not in named_invariants]
    if legacy_invariants:
        if after_tree:
            gone = [v for v in legacy_invariants if v not in after_tree]
            if gone:
                problems.append("그대로여야 할 것이 사라졌습니다: "
                                + ", ".join(gone))
            checked.append("건드리지 말아야 할 것이 그대로인가")
        else:
            unchecked.append("건드리지 말아야 할 것")

    # Each against what the request said it should be. This is the comparison
    # the whole `source` rule exists to make possible: one side observed, the
    # other stated in advance, and neither allowed to be the other.
    for check in spec.required:
        if check.when != "before_commit" or check.kind != "reports":
            continue
        want = spec.expected.get(check.name)
        got = seen.get(check.name)
        if want is None:
            continue                       # nothing to compare against
        if got is None:
            unchecked.append(f"{check.name} (커밋 전 화면에서 확인 못 함)")
            continue
        if not same_value(want, got):
            problems.append(f"{check.name}: {want!r} 를 기대했는데 화면은 "
                            f"{got!r} 입니다")
        checked.append(f"{check.name} (커밋 직전)")

    # A declared check is not a passed check. Every kind needs evidence.
    history = getattr(result, "events", [])
    for check in spec.required:
        if check.kind == "absent":
            got = seen.get(check.name)
            if got is None:
                unchecked.append(check.name)
            elif not observed_absent(got):
                problems.append(f"{check.name}: 비어 있지 않습니다 ({got!r})")
            else:
                checked.append(check.name)
        elif check.kind == "exactly_once":
            effect = {"save_file": "write_persistent", "send_message": "transmit"}.get(spec.kind)
            events = [e for e in history if effect in e.get("effects", [])]
            from .commit_evidence import evidence_error
            if not events or any(e.get("state") != "confirmed" or
                                 evidence_error({**e, "commit_evidence":
                                     getattr(result, "commit_evidence", {}).get(e.get("event_id"))},
                                                spec.expected) is not None
                                 for e in events):
                unchecked.append(check.name)
            elif len(events) != 1:
                problems.append(f"{check.name}: {len(events)}회 실행됐습니다")
            else:
                checked.append(check.name)
        elif check.kind == "reports" and check.when == "after":
            want, got = spec.expected.get(check.name), seen.get(check.name)
            if want is None or got is None:
                unchecked.append(check.name)
            elif not same_value(want, got):
                problems.append(f"{check.name}: 기대 {want!r}, 관찰 {got!r}")
            else:
                checked.append(check.name)
        elif check.kind == "unchanged" and check.name in spec.expected:
            if check.name in seen:
                if not same_value(spec.expected[check.name], seen[check.name]):
                    problems.append(f"{check.name}: 원래 값과 다릅니다")
                else:
                    checked.append(check.name)
            else:
                unchecked.append(check.name)
        elif check.kind not in ("reports", "any_value", "unchanged"):
            unchecked.append(check.name)

    # "Nothing changed" can be ARGUED rather than observed, when the request
    # permitted nothing that could change anything and nothing else occurred.
    # Stated as an argument, not folded in as if it had been looked at: it
    # shows that WE changed nothing, and says nothing about the app moving on
    # its own.
    argued = set()
    read_only = set(spec.forbidden) >= (EVERY_EFFECT - {"read"})
    if read_only and happened <= {"read"}:
        for check in spec.required:
            if check.kind == "unchanged" and check.name not in spec.expected:
                argued.add(check.name)
        if argued:
            checked.append("바꿀 수 있는 일은 허용되지도, 일어나지도 않음")

    # A required condition nobody supplied a value for has not been satisfied;
    # it has been skipped. For a task kind that says what it needs, skipping
    # one is the difference between "it worked" and "we did not look".
    skipped = [n for n in spec.unfilled() if n not in argued]
    if skipped:
        unchecked.extend(skipped)
        checked.append(f"'{spec.kind}' 이 요구하는 조건 "
                       f"{len(spec.required) - len(skipped)}/{len(spec.required)}")

    if problems:
        state = PARTIAL if getattr(result, "verified", False) else UNVERIFIED
    elif not getattr(result, "ok", False):
        state = REFUSED
        problems = [getattr(result, "why", "") or "진행하지 않았습니다"]
    elif spec.kind and unchecked:
        # The kind said what it takes to know this worked, and some of it was
        # never looked at. Calling that success would be the machine grading
        # its own paper on the questions it chose to answer.
        state = UNVERIFIED
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


# --------------------------------------------------- what each task requires

# Where an expected value may come from. `result` is deliberately absent: an
# expectation read back out of the run that produced it is a consistency check
# wearing a verifier's clothes (advisor finding). "We selected 엄마, and the
# recipient was 엄마" proves the code can remember a variable.
SOURCES = {"request", "prior", "relationship"}


@dataclass(frozen=True)
class Check:
    """One thing that has to be true, and how anyone would know.

    Four fields because "verify the result" is not a specification: a condition
    with no stated source can be filled from whatever is convenient, and one
    with no stated moment gets checked after the effect it was supposed to
    prevent.
    """
    name: str
    source: str        # where the expected value comes from — never the result
    when: str          # before_commit | after
    kind: str          # reports | any_value | unchanged | exactly_once | absent

    @property
    def needs_value(self) -> bool:
        """Whether an expected value has to be supplied for this check.

        A read's answer is what the run went to find out, so "the value equals
        X" cannot be sourced from the request without inventing X. What CAN be
        required is that a value reached the person at all — `any_value` — and
        conflating the two is how a check ends up expecting whatever the run
        produced.
        """
        return self.kind in ("reports", "unchanged")

    def __post_init__(self):
        if self.source not in SOURCES:
            raise ValueError(f"기대값 출처가 아닙니다: {self.source!r}")


# The conditions a task kind must satisfy, written by a person per KIND — not
# by a model per run. Asking a model to "write good success conditions"
# produces whatever it finds convenient, and the point of a verifier is to be
# the part that was not convinced.
TASK_KINDS = {
    "save_file": [
        Check("저장 위치", "request", "before_commit", "reports"),
        Check("저장한 이름", "request", "before_commit", "reports"),
        Check("다시 읽은 내용", "prior", "after", "reports"),
        Check("요청하지 않은 덮어쓰기 없음", "prior", "before_commit", "absent"),
        Check("이번 작업의 저장 1회", "request", "after", "exactly_once"),
    ],
    "send_message": [
        Check("받는 사람", "relationship", "before_commit", "reports"),
        Check("보내는 계정", "prior", "before_commit", "reports"),
        Check("숨은 참조 없음", "request", "before_commit", "absent"),
        Check("보낸 내용", "request", "before_commit", "reports"),
        Check("이번 작업의 전송 1회", "request", "after", "exactly_once"),
        Check("원본 보존", "prior", "after", "unchanged"),
    ],
    "read_value": [
        Check("값을 사람에게 전달함", "request", "after", "any_value"),
        Check("아무것도 바뀌지 않음", "prior", "after", "unchanged"),
    ],
}


def spec_for(kind: str, envelope, expected: dict, provenance: dict) -> "OutcomeSpec":
    """The spec for one run of a task kind, with its expectations filled in.

    `provenance` says where each value came from, and a value that came from
    the run's own result is refused rather than quietly accepted — that is the
    rule this function exists to enforce. Everything else here is bookkeeping.
    """
    checks = TASK_KINDS.get(kind)
    if checks is None:
        raise KeyError(f"정의되지 않은 작업 유형: {kind!r}")
    for key, value in expected.items():
        src = provenance.get(key)
        if src not in SOURCES:
            raise ValueError(
                f"'{key}' 의 기대값 출처가 {src!r} 입니다 — 실행 결과에서 "
                f"가져온 기대값은 일관성 검사이지 검증이 아닙니다")
    base = OutcomeSpec.for_envelope(envelope)
    reports = [str(expected[c.name]) for c in checks
               if c.kind == "reports" and c.name in expected]
    keep = [str(expected[c.name]) for c in checks
            if c.kind == "unchanged" and c.name in expected]
    return OutcomeSpec(must_read=reports, must_not_change=keep,
                       forbidden=base.forbidden, kind=kind,
                       required=tuple(checks), expected=dict(expected))


def replace_state(receipt: "TaskReceipt", state: str) -> "TaskReceipt":
    """The same receipt with a different verdict.

    Used when a run's problems were undone: the harm was avoided, and the task
    still did not happen, so the state moves to `recovered` and the problems
    stay on the record rather than being cleared along with their effects.
    """
    from dataclasses import replace as _replace
    return _replace(receipt, state=state)
