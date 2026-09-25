"""L3/L4 — the natural-language agent over the deterministic replay core.

This is the layer non-technical users actually talk to. It turns an intent into
a plan, narrates each step in plain language (L4), runs it through the tokenless
replay engine (L2), and handles the two things a beginner can't: a confirmation
gate and a recovery when a step fails.

Design stance (matches PROTOCOL.md):
  - Known task  -> load the stored flow and replay it: ZERO model tokens.
  - Unknown task -> this is where a model authors a new flow (the `author` hook).
    Authoring is the only path that spends tokens; replay never does.
  - Confirmation and hand-off are surfaced to the user in everyday words, never
    as raw protocol messages.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable

from .protocol import Driver, default_driver
from .flow import Flow, replay, ReplayResult
from .store import FlowStore


@dataclass
class Turn:
    """One agent turn's user-facing result."""
    status: str            # done | needs_confirm | needs_handoff | failed | unknown_task
    say: str               # plain-language message for the user
    flow: str | None = None
    detail: dict | None = None


# The unfilled slots, in the words a person would use for them.
_SLOT_KO = {"target": "무엇을", "recipient": "누구에게", "amount": "얼마나",
            "location": "어디에", "when": "언제", "account": "어떤 발신 계정으로",
            "body": "어떤 내용을"}


def _candidate_name(goal: str) -> str:
    """A stable name for a proposal, so the same job files under one entry."""
    import hashlib
    import re as _re
    words = _re.findall(r"[가-힣A-Za-z]+", goal)[:3]
    stem = "_".join(words) or "task"
    return f"{stem}_{hashlib.sha1(goal.encode()).hexdigest()[:6]}"


def _readable(value) -> str:
    """A captured value as a person would read it, not as Python prints it.

    A day's calendar entries carry their whole summary, so running several of
    them together with commas produces a paragraph nobody can scan. Short lists
    stay on one line; longer ones get a line each.
    """
    if not isinstance(value, list):
        return str(value)
    if not value:
        return "(없음)"
    inline = ", ".join(str(v) for v in value)
    if len(value) > 1 and len(inline) > 60:
        return "\n" + "\n".join(f"  · {v}" for v in value)
    return inline


@dataclass
class Match:
    """Which recipe a request resolved to, and the values it supplied.

    The values matter as much as the name: a recipe is a template, so "다운로드
    폴더 열어줘" and "문서 폴더 열어줘" are the same procedure with different
    inputs. Returning only a name would force one stored recipe per folder.
    """
    name: str
    params: dict


# Folders addressed by their Korean name map to the menu item's own title,
# because the menu bar is the thing being driven and it speaks the system's
# language, not the user's.
_FOLDERS = {
    "다운로드": "Downloads", "내려받기": "Downloads", "downloads": "Downloads",
    "문서": "Documents", "도큐먼트": "Documents", "documents": "Documents",
    "데스크탑": "Desktop", "데스크톱": "Desktop", "바탕화면": "Desktop",
    "desktop": "Desktop",
    "홈": "Home", "홈폴더": "Home", "home": "Home",
    "응용프로그램": "Applications", "앱": "Applications",
    "applications": "Applications",
    "최근": "Recents", "최근항목": "Recents", "recents": "Recents",
}

_OPS = {"+": "+", "-": "-", "*": "*", "x": "*", "×": "*", "/": "/", "÷": "/",
        "더하기": "+", "빼기": "-", "곱하기": "*", "나누기": "/"}


def _calc(m: re.Match) -> dict:
    return {"a": m.group(1), "b": m.group(3), "op": _OPS[m.group(2).lower()]}


def _folder(m: re.Match) -> dict:
    return {"folder": _FOLDERS[m.group(1).lower()]}


def _frontmost() -> str:
    """The app the person is looking at.

    "대화상자 닫아줘" means the dialog in front of them; naming a fixed app
    would be right only by accident.
    """
    from AppKit import NSWorkspace
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    return str(app.bundleIdentifier() or app.localizedName()) if app else ""


_WEEKDAY_KO = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]


def _today(_m: re.Match) -> dict:
    """Today's weekday, in both the app's language and the user's.

    Calendar labels its day columns in the system's language while this process
    computes in its own, so the recipe is given both spellings and matches
    whichever the screen actually shows.
    """
    import datetime
    t = datetime.date.today()
    return {"weekday_en": t.strftime("%A"), "weekday_ko": _WEEKDAY_KO[t.weekday()]}


# Intent resolution: map a natural-language request to a stored recipe AND the
# values it needs. Deterministic and tokenless. A real deployment extends this
# with the model only when nothing matches (the `author` path below).
# What a request can carry that a regex has no slot for. A rule that matches
# anyway is not a fast path — it is a wrong answer arrived at quickly. Measured,
# not imagined: `"굵게 하지 마"` matched the bold recipe and made the text bold,
# `"메일 말고 문서 폴더 열어줘"` opened Mail, and `"어제 온 메일"` and `"오늘 온
# 메일"` resolved identically. Nothing failed in any of the three, so no amount
# of `--think` could have caught them.
#
# Each rule declares the meanings it accounts for. Anything else the utterance
# carries makes the fast path stand down and say so, rather than answer.
_MARKERS: list[tuple[str, tuple[str, ...]]] = [
    # Negation is matched by the shared rule in `ask`, because a word list
    # only ever knew 하다 — "읽지 마" and "보내지 마" walked straight past it.
    ("negation", ()),
    ("time:today", ("오늘", "today", "지금")),
    ("time:other", ("어제", "그제", "내일", "모레", "지난", "저번", "이번 주",
                    "이번주", "아까", "최근", "yesterday", "tomorrow")),
]


def _find_decision(steps, into) -> bool:
    """Is there a branching decision called `into`, at any depth?"""
    return any(st.action == "decide" and st.params.get("into") == into
               and "branches" in st.params for st in _all_steps(steps))


def _graft(steps, into, choice, new) -> list:
    """`steps` with `new` kept as the `choice` branch of decision `into`,
    wherever in the nesting that decision is."""
    from dataclasses import replace as _replace
    out = []
    for st in steps:
        if st.action == "decide" and "branches" in st.params:
            branches = dict(st.params["branches"])
            if st.params.get("into") == into:
                branches[choice] = list(new)
            else:
                branches = {c: _graft(b, into, choice, new) for c, b in branches.items()}
            st = _replace(st, params={**st.params, "branches": branches})
        out.append(st)
    return out


def _all_steps(steps) -> list:
    """Every step, including those inside a decision's branches."""
    out = []
    for st in steps:
        out.append(st)
        if st.action == "decide":
            for branch in (st.params.get("branches") or {}).values():
                out.extend(_all_steps(branch))
    return out


def _shape(flow) -> list:
    """What a flow does, for comparing a template's rendering to a walk."""
    return [(st.action, st.selector and (st.selector.role, st.selector.identifier,
             st.selector.title, st.selector.desc, st.selector.ancestor),
             sorted(st.params.items()) if st.action != "decide" else None)
            for st in flow.steps]


def _markers(intent: str) -> set[str]:
    from .ask import _refuses
    low = intent.lower()
    found = {tag for tag, words in _MARKERS if words and any(w in low
                                                             for w in words)}
    if _refuses(intent) or "대신" in intent:
        found.add("negation")
    return found


# Things that would be ANOTHER instruction if they turn up in the part of a
# request a rule did not consume: a number it is not using, or a verb for
# something it is not doing. Measured, not imagined — on a real machine:
#
#   "1351 곱하기 59913 하고 123123 을 더한다음 계산해줘"
#       matched `1351 곱하기 59913`, dropped `+ 123123`, and reported
#       80,942,463 — the product alone — as the answer.
#   "메일 보내줘"
#       matched `메일` and resolved to the recipe that OPENS the inbox.
#
# Both succeeded. A rule matching a prefix of what was said is not the same as
# a rule answering it, and the leftover is where the difference shows.
_STILL_WORK = re.compile(
    r"\d"                                        # a number it is not using
    r"|곱하|더하|빼기|나누|제곱|퍼센트"              # another operation
    r"|저장|보내|전송|지워|삭제|버려"                # another consequential verb
    r"|열어|닫아|써|적어|입력|굵게|복사|붙여")


def _leftover(intent: str, m: "re.Match") -> str:
    return intent[:m.start()] + intent[m.end():]


_INTENT_RULES: list[tuple[re.Pattern, str, object, set]] = [
    (re.compile(r"(\d+)\s*(곱하기|더하기|빼기|나누기|[-+x×*/÷])\s*(\d+)"),
     "calc_arith", _calc, set()),
    # Checked early: it is the way out of a stuck run, so it must not be
    # shadowed by a rule that matches the app's name inside the same sentence.
    (re.compile(r"(대화상자|다이얼로그|팝업|창).{0,6}(닫아|닫기|취소|없애)"
                r"|(dialog|sheet).{0,6}(close|cancel)"),
     "close_dialog", lambda _m: {"app": _frontmost()}, set()),
    (re.compile(r"(메일|이메일|mail|inbox|받은\s*편지)"), "mail_inbox",
     lambda m: {}, set()),
    (re.compile(r"(오늘|today).{0,6}(일정|스케줄|약속|schedule)"
                r"|(일정|스케줄).{0,4}(뭐|뭔가|있어|알려)"), "calendar_today", _today,
     # This one really is about today, so saying so is not extra meaning.
     {"time:today"}),
    # A question about a folder is answered, not acted on; it is checked first
    # because "다운로드 폴더 보여줘" and "다운로드 폴더에 뭐 있어?" overlap.
    (re.compile(r"(" + "|".join(sorted(_FOLDERS, key=len, reverse=True)) +
                r")\s*(폴더|디렉터리|folder)?\s*(에|안에)?\s*"
                r"(몇\s*개|몇개|얼마나)", re.I),
     "finder_count_items", _folder, set()),
    (re.compile(r"(" + "|".join(sorted(_FOLDERS, key=len, reverse=True)) +
                r")\s*(폴더|디렉터리|folder)?\s*(에|안에)?\s*"
                r"(뭐|무엇|무슨)", re.I),
     "finder_list_items", _folder, set()),
    (re.compile(r"(" + "|".join(sorted(_FOLDERS, key=len, reverse=True)) +
                r")\s*(폴더|디렉터리|folder)?\s*(을|를|로)?\s*(열어|열기|보여|open)",
                re.I), "finder_open_folder", _folder, set()),
    # Saving is checked before writing: "'보고서' 로 저장해줘" contains a quoted
    # string too, and reading it as "type 보고서" would be the wrong task.
    (re.compile(r"[\"'“](.+?)[\"'”]\s*(으로|로)?\s*저장", re.S),
     "textedit_save_as", lambda m: {"filename": m.group(1)}, set()),
    (re.compile(r"(새|빈)\s*(문서|파일)"), "textedit_new_document",
     lambda m: {}, set()),
    # An explicitly quoted string is a stronger signal than a stray "굵게", so
    # the write rule is tried first.
    (re.compile(r"[\"'“](.+?)[\"'”]\s*(라고|이라고)?\s*(써|적어|입력|write)",
                re.S), "textedit_write_note",
     lambda m: {"text": m.group(1)}, set()),
    (re.compile(r"(굵게|볼드|bold)"), "textedit_emphasize", lambda m: {}, set()),
]


class Agent:
    def __init__(self, driver: Driver | None = None, store: FlowStore | None = None,
                 narrate: Callable[[str], None] | None = None,
                 author: Callable[[str, Driver], Flow] | None = None,
                 judge: Callable | None = None,
                 interpret: Callable | None = None,
                 general: Callable | None = None,
                 outcome_context: Callable | None = None,
                 commit_adapter_factory: Callable | None = None,
                 decide: Callable | None = None,
                 review: Callable | None = None):
        self.driver = driver if driver is not None else default_driver()
        self.store = store or FlowStore()
        self.narrate = narrate or (lambda s: None)
        self.author = author   # optional model-backed flow author (spends tokens)
        # Injectable so a caller — a test, or a host with its own model — can
        # supply judgment without this module reaching for a provider.
        self.judge = judge
        # Answers a recipe's planned `decide` steps. Unlike `judge`, which is
        # for surprises, a recipe that has a decide step needs this every run.
        self.decide = decide
        # Looks over a freshly walked branch for judgment points of its own,
        # the way promotion looks over a walk: (record, flow, names in use)
        # -> flow. Without it a learned branch replays exactly as walked.
        self.review = review
        # Reading the request itself, for the requests the patterns decline.
        # Separate from `judge`: this one is given the user's words and never
        # the screen, which is why what it returns may say what the user wants.
        self.interpret = interpret
        # Doing a task no recipe covers, by asking about each screen. The other
        # half of the product: a recipe answers "again", this answers "at all".
        self.general = general
        # Host-supplied expectations are obtained before execution, never from
        # its result. Callback(intent, reading) -> (expected, provenance).
        self.outcome_context = outcome_context
        self.commit_adapter_factory = commit_adapter_factory
        # The last thing this agent did, kept so "방금 것 취소해줘" has
        # something to mean. The person should not have to find the app's Undo
        # menu — that is one more computer task, and finding it is the part
        # they cannot do (advisor finding).
        self._last = None

    # ------------------------------------------------------------ intent

    def resolve(self, intent: str) -> Match | None:
        """Which recipe this request means — by pattern, with zero tokens.

        Returns None both when nothing matched and when something matched but
        the request carries meaning the rule has no slot for. The second case
        is the important one: answering it quickly is how "굵게 하지 마" made
        text bold. `unrepresented()` says which it was.
        """
        said = _markers(intent)
        for rx, name, extract, covers in _INTENT_RULES:
            m = rx.search(intent)
            if m is None:
                continue
            if said - covers:                 # said more than this rule can hold
                continue
            if _STILL_WORK.search(_leftover(intent, m)):
                continue                      # matched a prefix, not the request
            try:
                return Match(name, extract(m))
            except KeyError:
                # The shape matched but a value did not (a folder we have no
                # menu item for). Keep looking rather than resolving to a recipe
                # we cannot supply.
                continue
        # a request may also name a stored flow directly
        if not said:
            hit = self._match_stored(intent)
            if hit is not None:
                return hit
            for name in self.store.list():
                if name.lower() in intent.lower():
                    return Match(name, {})
        return None

    def _match_stored(self, sentence: str) -> Match | None:
        """A recipe a run promoted, found by the sentence it was promoted from.

        Built-in recipes are found by rules someone wrote; a promoted one has
        no rule, only the request it came from with its inputs marked. Exactly
        one recipe must match: two means the sentence does not say which, and
        picking one would run it unattended on a guess.
        """
        from .flow import content_match, intent_pattern
        stored = self._stored_templates()
        hits = []
        for name, flow in stored.items():
            for template in flow.intents:
                m = intent_pattern(template, flow.params).fullmatch(sentence)
                if m is not None:
                    hits.append(Match(name, m.groupdict()))
                    break
        if hits:
            return hits[0] if len(hits) == 1 else None
        # Not the sentence, but the same request: the same content words and
        # verb, one number per input. Still no model, and still only if exactly
        # one recipe claims it — and every value it read must be one it can
        # also see in what was said.
        found = {}
        for name, flow in stored.items():
            for template in flow.intents:
                values = content_match(template, flow.params, sentence)
                if values is not None:
                    found.setdefault(name, values)
                    break
        if len(found) != 1:
            return None
        (name, values), = found.items()
        return Match(name, values)

    def _match_from_goal(self, goal: str) -> Match | None:
        """Run the patterns against a cleaned-up goal rather than the raw words.

        "메일 말고 문서 폴더 열어줘" carries an exclusion no rule can hold, but
        the goal it reduces to — "문서 폴더 열기" — is exactly what the folder
        rule was written for, parameters and all. So reading the request does
        not have to produce a recipe; it only has to produce a sentence the
        fast path can finish.
        """
        for rx, name, extract, _covers in _INTENT_RULES:
            m = rx.search(goal)
            if m is None:
                continue
            # The same guard as the raw request, and for the same reason: a
            # cleaned-up goal is still a sentence a rule can match half of.
            # Without it here, "1351 곱하기 59913 하고 123123 을 더한다음" was
            # held back by the fast path and then let straight back in by the
            # reading — and answered with the product alone.
            if _STILL_WORK.search(_leftover(goal, m)):
                continue
            try:
                return Match(name, extract(m))
            except KeyError:
                continue
        return self._match_stored(goal)

    def read_request(self, intent: str) -> tuple[Match | None, dict]:
        """What the user asked for, when the patterns could not say.

        Two bounded questions, each with a checkable answer: what was asked
        (from their words alone, and the quote has to be IN those words), and
        which of our recipes carries it out (from a closed list). Neither is
        "what should I do" — that has no answer anything could check.
        """
        import time as _time
        from .ask import what_was_asked, which_task
        if self.interpret is None:
            return None, {}
        # "어제" has to mean the day before the moment they spoke, and keep
        # meaning it if the run crosses midnight.
        now = _time.strftime("%Y-%m-%d %H:%M %Z")
        known = self._known_tasks()
        reading = self.interpret(what_was_asked(
            intent, now=now, tasks=known, inputs=self._template_inputs()))
        if not reading:
            return None, {}
        if reading.get("negated"):
            # They said not to. There is no recipe for that, and running the
            # thing they named is the one answer that is certainly wrong.
            return None, reading
        goal = reading.get("goal", "")
        match = self._match_from_goal(goal)
        if match is not None:
            return match, reading
        if reading.get("picked"):
            # The reading saw the list: its pick (checked) or its "none" is
            # the answer, and asking which_task again would be the same
            # question twice.
            if reading.get("pick"):
                return Match(reading["pick"], reading.get("pick_values") or {}), reading
            return None, reading
        if not known:
            return None, reading
        pick = self.interpret(which_task(
            goal, known, excluded=reading.get("excluded", ()),
            inputs=self._template_inputs(), words=intent))
        if pick and pick.get("pick"):
            return Match(pick["pick"], pick.get("values") or {}), reading
        return None, reading

    def _stored_templates(self) -> dict:
        """Promoted recipes that say which sentences they answer."""
        out = {}
        for name in self.store.list():
            try:
                flow = self.store.load(name)
            except Exception:
                continue
            if flow.intents:
                out[name] = flow
        return out

    def _template_inputs(self) -> dict:
        return {name: dict(flow.params)
                for name, flow in self._stored_templates().items() if flow.params}

    def _work_it_out(self, intent: str, reading: dict, ask) -> Turn:
        """Do a task with no recipe, one question per screen.

        The goal comes from the reading when there was one, because "메일 말고
        문서 폴더 열어줘" is a worse instruction to work from than the "문서
        폴더 열기" it reduces to.
        """
        from .authoring import guess_target
        from .contract import Envelope
        goal = reading.get("goal") or intent
        target = guess_target(intent, self.driver) or _frontmost()
        if not target:
            return Turn("unknown_task",
                        "어느 앱에서 할 일인지 알 수 없어요. 앱 이름을 같이 "
                        "말씀해 주시겠어요?")
        self.narrate(f"처음 하는 일이라 화면을 보면서 해볼게요: {goal}")
        # What the request authorizes, from the user's own words. Without a
        # reading there is none, and the loop falls back to looking and typing
        # into what is in front of it — never saving, sending or deleting.
        env: Envelope | None = None
        if reading.get("allows"):
            env = Envelope(quote=reading.get("quote", ""),
                           allows=frozenset(reading["allows"]),
                           scope=reading.get("scope", "entry"),
                           reversibility=reading.get("reversibility",
                                                     "reversible"))
        # What the run says about itself is evidence, not the verdict. The
        # receipt is issued by something that did not run the task, and its
        # `unchecked` list is what stops "we did not look" from being reported
        # as "it was fine".
        from .outcome import (DONE, RECOVERED, OutcomeSpec, replace_state,
                              spec_for, verify)
        # A task kind says what it takes to know this worked. Everything it
        # requires that the request did not supply stays unfilled, and an
        # unfilled requirement is reported as unchecked rather than assumed —
        # so a send whose recipient nobody confirmed comes back "보내긴 했는데
        # 확인 못 했다" instead of "다 됐어요".
        spec = OutcomeSpec.for_envelope(env or Envelope.minimal())
        kind = reading.get("kind")
        if kind:
            # What the user said, under the names the task kind checks by.
            # Only slots they actually filled — a value nobody stated is not an
            # expectation, it is a guess with a confident label on it.
            said_slots = reading.get("values") or {}
            by_check = {"recipient": "받는 사람", "target": "저장한 이름",
                        "location": "저장 위치", "account": "보내는 계정",
                        "body": "보낸 내용"}
            supplied, where = {}, {}
            for slot, value in said_slots.items():
                name = by_check.get(slot)
                if name and value:
                    supplied[name] = value
                    where[name] = "request"
            try:
                if self.outcome_context is not None:
                    expected, provenance = self.outcome_context(intent, reading)
                    from .outcome import same_value
                    for name, value in expected.items():
                        if name in supplied and not same_value(supplied[name], value):
                            raise ValueError(f"{name}: 요청과 사전 정보가 다릅니다")
                        if name not in supplied:
                            supplied[name] = value
                            where[name] = provenance.get(name)
                spec = spec_for(kind, env or Envelope.minimal(), supplied, where)
            except Exception as exc:
                return Turn("failed", f"실행 전에 필요한 정보를 확인하지 못했습니다: {exc}")
        try:
            # The reading's goal is a summary, and a summary can drop the
            # rules: "짝수면 ×2, 홀수면 +1, 그다음 20보다 크면 −5…" came back as
            # "조건에 따라 두 단계 계산", and the walk had nothing left to do.
            # The person's own words go along; the goal still leads.
            told = goal if goal.strip() == intent.strip() else (
                f"{goal}\n(사용자가 한 말 그대로: {intent[:400]})")
            r = self.general(target, told, ask, env, spec)
        except Exception as e:
            return Turn("failed", f"화면을 보며 해봤지만 실패했어요: {e}")
        # A run that arrived is a proposal for a recipe. Filed, never deployed:
        # a recipe replays with no model call, so nobody is watching it, and a
        # wrong one keeps being wrong. What it would take to deploy is written
        # down beside it.
        receipt = verify(spec, r, after_tree=getattr(r, "after_tree", ""))
        if r.ok and receipt.ok:
            try:
                from .promote import CandidateStore, candidate_from
                cand = candidate_from(r, target, goal,
                                      reading.get("quote", ""), env,
                                      target_identity=self.driver.identity(target))
                if cand is not None:
                    CandidateStore().record(
                        cand, intent, _candidate_name(goal))
            except Exception:
                pass                    # proposing is never worth failing over
        # An automatic verifier that finds a problem should put things back
        # inside the window where that is possible, rather than wait for
        # someone who cannot work their computer to ask (advisor finding).
        # Remembered whether or not anything went wrong: "방금 것 취소해줘"
        # is usually said about a run that went exactly as asked.
        if r.effects - {"read"}:
            self._last = {"effects": set(r.effects), "certainty": r.certainty,
                          "witnessed": dict(r.witnessed), "goal": goal}
        undone = None
        if receipt.problems and r.effects - {"read"}:
            undone = self._put_it_back(r)
            if undone is not None and undone.ok:
                receipt = replace_state(receipt, RECOVERED)
        did = ("여기까지 했어요: " + ", ".join(r.did[:8])
               + (" …" if len(r.did) > 8 else "")) if r.did else ""
        detail = {"screens": r.path, "asked": r.asked, "did": r.did,
                  "state": receipt.state, "checked": receipt.checked,
                  "unchecked": receipt.unchecked,
                  "undone": undone.__dict__ if undone else None}
        if undone is not None and not undone.ok and undone.why:
            receipt.problems.append(f"되돌리지 못했습니다 — {undone.why}")
        if r.ok:
            return Turn(receipt.state,
                        receipt.say(), detail=detail)
        # "Bring the app to the front" is something the person can do and then
        # ask again; reporting it as a failure tells them the task is broken.
        status = ("needs_handoff"
                  if r.stop_reason in ("needs_foreground", "needs_pointer")
                  else "failed")
        return Turn(status,
                    "\n".join(x for x in (f"{r.why}.", did, receipt.say())
                               if x), detail=detail)

    def _put_it_back(self, r):
        """Undo what this run caused, where we actually hold the means.

        Three ways this returns without acting, and each is the right answer:
        we have no inverse for that effect, the inverse needed something nobody
        captured before the effect, or the effect may not have happened at all.
        The last one is the one that looks like caution and is not: reversing
        is itself an action, and "probably sent, so unsend it" deletes a
        message that may be the only copy.
        """
        from .undo import capability_for, may_reverse, reverse
        cap = capability_for(r.effects)
        ok, why = may_reverse(r.certainty, cap, r.witnessed)
        if not ok:
            from .undo import Reversal
            return Reversal(why=why, inverse=cap.inverse if cap else "")
        out = reverse(cap, r.witnessed)
        self.narrate("하던 일을 되돌렸어요." if out.ok
                     else f"되돌리려 했지만 실패했어요: {out.why}")
        return out

    def _known_tasks(self) -> list[tuple[str, str]]:
        from .library import builders
        out = []
        for name, build in sorted(builders().items()):
            try:
                f = build()
                # A recipe describes itself through the words its steps were
                # written in — the same sentences the user hears while it runs.
                desc = " · ".join(st.intent_text for st in f.steps
                                  if st.intent_text)[:120]
                out.append((name, desc or name))
            except Exception:
                out.append((name, name))
        # A promoted recipe describes itself by the sentence it answers.
        builtin = {n for n, _ in out}
        for name, flow in sorted(self._stored_templates().items()):
            if name not in builtin:
                out.append((name, flow.intents[0][:120]))
        return out

    def unrepresented(self, intent: str) -> set[str]:
        """What this request carries that the fast path cannot express.

        Empty means the patterns simply do not know this task, and authoring is
        the answer. Non-empty means a rule DID match and was held back — the
        request needs reading, not a new recipe.
        """
        said = _markers(intent)
        for rx, _name, _extract, covers in _INTENT_RULES:
            m = rx.search(intent)
            if m is None:
                continue
            if said - covers:
                return said - covers
            if _STILL_WORK.search(_leftover(intent, m)):
                return {"unconsumed"}
        return set()

    # ------------------------------------------------------------ consent

    def authorize(self, flow: Flow, ask: Callable[[str], bool] | None = None
                  ) -> tuple[dict, list[str]]:
        """Collect the consent a flow needs BEFORE any of it runs.

        Asking mid-run would mean stopping half-finished and asking someone to
        approve something that has already partly happened. Asking first also
        lets them decline while the screen is still untouched.

        Returns the approvals to hand to `replay`, plus the reasons any step
        cannot proceed. `preapproved` steps are granted without a question:
        the policy reached that level only because the user's own request
        already named the action, and re-asking would train people to click
        through prompts. `confirm` steps go to `ask`; `handoff` steps cannot be
        approved by anyone but the person at the keyboard.
        """
        from .contract import describe, issue_approval, policy

        approvals: dict = {}
        blocked: list[str] = []
        decided = {st.params.get("into") for st in flow.steps if st.action == "decide"}
        for i, st in enumerate(flow.steps):
            if st.safety is None:
                continue
            decision = policy(st.safety)
            if decision.risk == "none":
                continue
            # A value decided during the run is not known yet; consent to a
            # step that uses one is asked when it is (replay's `confirm`).
            used = json.dumps([st.selector and st.selector.__dict__, st.params],
                              ensure_ascii=False, default=str)
            if decision.risk == "confirm" and any("{{%s}}" % n in used for n in decided):
                continue
            what = describe(st.safety, decision)
            if decision.risk == "handoff":
                blocked.append(f"{what} — 이 단계는 직접 해주셔야 해요")
                continue
            if decision.risk == "blocked":
                blocked.append(f"{what} — 무슨 일이 일어날지 확정할 수 없어 실행하지 않습니다")
                continue
            if st.binding is None:
                # Consent is bound to a described action; with nothing to bind
                # it to, approving would authorize "step i" rather than the
                # thing the person agreed to.
                blocked.append(f"{what} — 이 단계에 고정된 설명이 없어 승인할 수 없어요")
                continue
            if decision.risk == "confirm":
                if ask is None or not ask(what):
                    blocked.append(f"{what} — 진행하지 않았습니다")
                    continue
            approvals[i] = issue_approval(st.binding, st.safety)
        return approvals, blocked

    # ------------------------------------------------------------ run

    # What a person says when they want the last thing undone. Matched before
    # anything else, because "그거 아니야" while a save is on screen must not
    # resolve to some recipe that happens to mention a file.
    _TAKE_IT_BACK = re.compile(
        r"(방금|아까|그거|그것|저거).{0,8}(취소|되돌|무르|아니)"
        r"|^(취소해|되돌려|무르기|아니야|아니에요)")

    def take_it_back(self) -> Turn:
        """Undo the last task, if we held the means when it ran."""
        from .undo import capability_for, may_reverse, reverse
        last = self._last
        if last is None:
            return Turn("done", "되돌릴 만한 게 없어요. 아직 아무것도 안 했어요.")
        cap = capability_for(last["effects"])
        ok, why = may_reverse(last["certainty"], cap, last["witnessed"])
        if not ok:
            return Turn("needs_handoff", f"되돌릴 수 없어요 — {why}")
        out = reverse(cap, last["witnessed"])
        self._last = None if out.ok else self._last
        return Turn("done" if out.ok else "failed",
                    "되돌렸어요. 부탁하셨던 일은 안 된 상태예요." if out.ok
                    else f"되돌리려 했지만 실패했어요: {out.why}")

    def handle(self, intent: str, approvals: dict[int, bool] | None = None,
               ask: Callable[[str], bool] | None = None,
               think: int | None = None) -> Turn:
        if self._TAKE_IT_BACK.search(intent.strip()):
            return self.take_it_back()
        match = self.resolve(intent)
        values: dict = {}
        reading: dict = {}
        read_to_find = match is None     # did finding the recipe take a model?
        if match is None:
            # Read it whenever the patterns could not answer — not only when
            # one was held back. A request the patterns simply do not know went
            # to the screen loop with no reading, and therefore with no
            # envelope, and therefore unable to do anything consequential: the
            # first corpus run could not save a file because nobody had
            # established that saving was what was asked for.
            #
            # `unrepresented()` still says WHICH of the two it was, because a
            # rule that matched and was held back is a reading problem while
            # nothing matching at all may be a missing recipe.
            match, reading = self.read_request(intent)
            if match is None and reading.get("negated"):
                return Turn("done", f"'{reading['quote']}' 라고 하셔서 "
                                    f"아무것도 하지 않았어요.")
            if reading.get("missing"):
                # An unfinished instruction is the user's to finish. Measured:
                # "저장해줘" with no object saved SOMETHING and reported success
                # in 16 of 144 corpus cases — a wrong success, and the kind
                # this product's users cannot catch. Guessing which thing they
                # meant is not a smaller version of asking; it is a different
                # answer to a question nobody asked.
                return Turn("needs_confirm", "말씀 중에 " + ", ".join(
                    _SLOT_KO.get(m, m) for m in reading["missing"])
                    + " 를 못 들었어요. 알려주시면 바로 할게요.",
                    detail={"missing": reading["missing"]})
            if match is None and self.general is None and self.author is None:
                return Turn("unknown_task",
                            "말씀을 정확히 이해하지 못했어요. 조금 다르게 "
                            "말씀해 주시겠어요?")
            if match is None and reading and self.unrepresented(intent) \
                    and self.general is None:
                return Turn("unknown_task",
                            "말씀을 정확히 이해하지 못했어요. 조금 다르게 "
                            "말씀해 주시겠어요?")
        if match is None:
            # Authoring is asked for on purpose (`--learn`): it produces a
            # recipe, which is the answer to "do this again". Without it the
            # answer to "do this at all" is to work it out against the screen,
            # which is what a person who cannot use a computer actually needs
            # the first time they ask for anything.
            if self.author is None:
                if self.general is None:
                    return Turn("unknown_task",
                                "그 작업은 아직 방법을 몰라요. 한 번만 알려주시면 "
                                "다음부터는 제가 알아서 할게요.")
                return self._work_it_out(intent, reading, ask)
            try:
                flow = self.author(intent, self.driver)   # the token-spending path
            except Exception as e:
                return Turn("unknown_task",
                            f"그 작업을 배우는 데 실패했어요. 조금 더 구체적으로 말씀해 주시겠어요? ({e})")
            self.store.save(flow)                          # learn once
        else:
            from .library import builders
            name, values = match.name, match.params
            builder = builders().get(name)
            if builder is not None:
                # A recipe that ships with NLC comes from the code, always. The
                # store used to win once a name had been saved, which froze
                # every built-in at whatever it looked like the first time it
                # ran — five of them were already stale against fixes made
                # since. The saved copy is kept current so `nlc show` and
                # `nlc list` still describe what actually runs; someone who
                # wants their own version saves it under a different name.
                flow = builder()
                if not self.store.has(name) or \
                        self.store.load(name).to_json() != flow.to_json():
                    self.store.save(flow)
            elif self.store.has(name):
                flow = self.store.load(name)
            else:
                return Turn("unknown_task", "그 작업 레시피를 찾지 못했어요.")

        # A stored recipe may be a template. Running it unbound sends
        # "{{digit[item]}}" to the screen as if it were a selector, which is how
        # `nlc do` broke while the suite — which binds explicitly — stayed green.
        # The values come from the request itself, so "문서 폴더 열어줘" and
        # "다운로드 폴더 열어줘" are one recipe, not two.
        # A recipe promoted from an agent's walk recorded the screen as it was
        # that once — a label like Calculator's `Clear`/`All Clear` depends on
        # what the display held. Where its steps declared that surprise, it may
        # ask once by default; a built-in recipe was written against the app,
        # not an afternoon, and stays free unless asked.
        walked = bool(flow.intents)
        template = flow                  # the stored recipe, before binding
        if think is None:
            think = 1 if walked else 0
        if flow.params or flow.tables or flow.derived:
            try:
                flow = flow.bind(**values)
            except Exception as e:
                return Turn("failed",
                            f"'{flow.name}' 에 필요한 값이 없어요: {e}", flow.name)

        # Consent before contact with the screen. Without this a save or a send
        # gated mid-run and the command simply ended there, because nothing in
        # the CLI could answer the question the gate asked.
        if approvals is None:
            approvals, blocked = self.authorize(flow, ask)
            if blocked:
                return Turn("needs_handoff", "\n".join(blocked), flow.name)

        plan = self._plan_text(flow)
        self.narrate(plan)

        # `think` is how many times this run may stop and ask. Zero — the
        # default — keeps it strictly deterministic and free; the cost is paid
        # per surprise, not per run, and only where a step said it expects one.
        judge = self.judge
        if think and judge is None:
            from .judge import model_judge
            judge = model_judge(target=flow.target, narrate=self.narrate)
        try:
            receipt = self.commit_adapter_for(flow)
        except Exception as exc:
            return Turn("failed", f"저장 결과를 검증할 준비를 하지 못했어요: {exc}", flow.name)
        hooks = self.commit_adapter_hooks(receipt)
        walk = None
        if self.general is not None:
            def walk(goal, question, choice):
                return self._walk_branch(flow.target, goal, question, choice, ask,
                                         taken=flow.runtime_names())
        res = replay(self.driver, flow, approvals=approvals,
                     narrate=self.narrate, escalate=judge, budget=think,
                     decide=self.decide, walk=walk,
                     confirm=(lambda text: bool(ask(text))) if ask else None, **hooks)
        self._report_what_it_asked(flow, res)
        turn = self._to_turn(flow, res)
        if read_to_find and walked and turn.status == "done":
            self._learn_phrasing(template, intent, values)
        learned = getattr(res, "learned", None)
        if learned and turn.status == "done":
            self._learn_branches(template, learned, values)
        return turn

    def _walk_branch(self, target, goal, question, choice, ask, taken=frozenset()) -> dict:
        """Carry on from here with the agent, for an answer never seen before.

        The steps before the decision already ran; the walk starts from the
        screen they left. What it returns is a proposal: the steps count only
        if the walk arrived, verified, and they would pass promotion.
        """
        from .contract import policy
        from .promote import candidate_from, deployable
        told = (f"{goal} — 이번 경우는 '{question}' 의 답이 '{choice}' 입니다. "
                "앞 단계는 이미 했으니, 지금 화면에서 이어서 끝까지 하십시오.")
        try:
            r = self.general(target, told, ask, None, None)
        except Exception as exc:
            return {"ok": False, "why": f"화면을 보며 해봤지만 실패했어요: {exc}"}
        if not (r.ok and r.verified):
            return {"ok": False, "why": r.why}
        steps = None
        c = candidate_from(r, target, told, told, None,
                           target_identity=getattr(r, "target_identity", None))
        if c is not None:
            walked_flow, why = deployable(c, "branch", told)
            if walked_flow is not None and self.review is not None:
                # The branch may itself have turned on something it saw.
                try:
                    walked_flow = self.review(
                        {"candidate": {k: v for k, v in vars(c).items() if k != "envelope"},
                         "request": told}, walked_flow, taken)
                except Exception:
                    pass                 # reviewing is never worth losing the walk
            # A branch replays with no one asked, so only steps that need no
            # consent may be kept in one — at any depth.
            if walked_flow is not None and all(
                    st.action == "decide" or (st.safety is not None
                                              and policy(st.safety).risk == "none")
                    for st in _all_steps(walked_flow.steps)):
                steps = walked_flow.steps
        return {"ok": True, "said": list(r.said), "steps": steps}

    def _learn_branches(self, template: Flow, learned: dict, values: dict) -> None:
        """Keep the steps walked for a new answer as that answer's branch.

        The walk ran with this run's values; kept as they are, the branch would
        replay them for every later input. They are turned back into the
        recipe's inputs, and kept only if doing so reproduces the walk.
        """
        from dataclasses import replace as _replace
        from .reparam import digits_table, parameterize
        steps = list(template.steps)
        changed = False
        for (into, choice), walked in learned.items():
            if not walked or not _find_decision(steps, into):
                continue
            branch = Flow(template.name, template.target, steps=list(walked))
            mapping = {str(v): k for k, v in values.items()
                       if isinstance(v, str) and v and k in template.params}
            if mapping:
                branch = parameterize(branch, mapping, tables=digits_table())
                concrete = Flow(template.name, template.target, steps=list(walked))
                if _shape(branch.bind(**{p: lit for lit, p in mapping.items()})) != _shape(concrete):
                    continue
            steps = _graft(steps, into, choice, branch.steps)
            changed = True
            self.narrate(f"다음부터 '{choice}' 인 경우도 바로 할 수 있어요")
        if changed:
            try:
                self.store.save(_replace(template, steps=steps))
            except Exception:
                pass

    def _learn_phrasing(self, template: Flow, said: str, values: dict) -> None:
        """Remember a new way of asking for a promoted recipe — once it worked.

        Finding this recipe took a model: the words matched no sentence it
        knew. The run then finished and checked out, so those words, with the
        values that came from them marked as inputs, become one more sentence
        it answers, and the same request next time costs nothing to find.

        Only the finding is learned, never the doing: nothing about what the
        recipe runs changes. And only a sentence that is not ambiguous: every
        value appears in it exactly once, and no other recipe claims it.
        """
        import re as _re
        from .flow import intent_pattern
        sentence = " ".join(said.split())
        if not sentence or len(sentence) > 200 or set(values) != set(template.params):
            return
        for key, value in values.items():
            hits = _re.findall(rf"(?<![0-9]){_re.escape(str(value))}(?![0-9])", sentence)
            if len(hits) != 1:
                return
            sentence = _re.sub(rf"(?<![0-9]){_re.escape(str(value))}(?![0-9])",
                               "{{%s}}" % key, sentence)
        if sentence in template.intents or len(template.intents) >= 20:
            return
        rx = intent_pattern(sentence, template.params)
        for name, other in self._stored_templates().items():
            if name != template.name and any(
                    rx.fullmatch(t) or intent_pattern(t, other.params).fullmatch(said)
                    for t in other.intents):
                return
        from dataclasses import replace as _replace
        try:
            self.store.save(_replace(template, intents=[*template.intents, sentence]))
        except Exception:
            return
        self.narrate("다음부터는 이렇게 말씀하셔도 바로 알아들어요")

    def commit_adapter_for(self, flow):
        """Prepare per-run host evidence before any replay action changes UI.

        The built-in adapter recognizes the exact shipped, bound save recipe;
        a flow name alone cannot select a document or grant receipt authority.
        Other trusted hosts can explicitly supply their own factory.
        """
        if self.commit_adapter_factory is not None:
            return self.commit_adapter_factory(self.driver, flow)
        from .textedit_receipt import SNAPSHOT_CAPABILITY, TARGET, make_textedit_receipt
        if (SNAPSHOT_CAPABILITY not in (getattr(self.driver, "capabilities", ()) or ())
                or flow.target != TARGET or flow.name != "textedit_save_as"
                or not flow.steps or flow.steps[-1].binding is None):
            return None
        from .library import textedit_save_as
        binding = flow.steps[-1].binding
        name, folder = binding.resource_identity, binding.destination_identity
        canonical = textedit_save_as().bind(filename=name, folder=folder)
        if canonical.to_json() != flow.to_json():
            return None
        if folder != "Downloads":
            raise ValueError("자동 저장 검증은 현재 Downloads의 새 RTF 파일만 지원합니다")
        from pathlib import Path
        directory = (Path.home() / "Downloads").resolve()
        self.narrate(f"저장 결과 확인: {directory / name}")
        return make_textedit_receipt(self.driver, target=TARGET,
                                     directory=str(directory), filename=name)

    @staticmethod
    def commit_adapter_hooks(receipt):
        if receipt is None:
            return {}
        return dict(prepare_commit=receipt.prepare_commit,
                    after_commit=receipt.after_commit,
                    commit_expected=receipt.commit_expected)

    def _report_what_it_asked(self, flow: Flow, res: ReplayResult) -> None:
        """Say what the run had to ask about — and do not quietly keep it.

        Persisting the fix looked like the way to make escalation pay for
        itself. It was wrong twice: the flow in hand is the BOUND one, so saving
        it would write this run's filename and text into the template, and
        whether a fix is durable depends on the condition it holds under, not on
        its IR shape. Until the IR can carry that condition, a fix that worked
        once is news for the person, not knowledge for the recipe.
        """
        for e in res.resolved:
            what = e.get("recovery") or "대상을 다시 지목"
            self.narrate(f"  ({flow.name}: '{what}' 로 이번 한 번 넘어갔습니다 — "
                         f"레시피에 저장하지는 않았어요)")

    # ------------------------------------------------------------ L4 phrasing

    def _plan_text(self, flow: Flow) -> str:
        steps = [s.intent_text for s in flow.steps if s.intent_text]
        if len(steps) <= 1:
            return f"'{flow.name}'을(를) 시작할게요."
        return "이런 순서로 할게요: " + " → ".join(steps)

    def _to_turn(self, flow: Flow, res: ReplayResult) -> Turn:
        last = res.log[-1]
        if res.ok and last.get("status") == "done":
            value = last.get("result")
            # A recipe that exists to answer a question has its answer in
            # `captured`, not in a verified result. Reporting only "다 됐어요"
            # for those would finish the task without saying what was found.
            captured = last.get("captured") or {}
            # A value read only so a decision could be made is the recipe's
            # working, not its answer. Where steps say which ones answer,
            # only those are reported.
            answering = {c.name for st in _all_steps(flow.steps) if st.answers
                         for c in st.captures}
            if answering & set(captured):
                captured = {k: v for k, v in captured.items() if k in answering}
            skipped = [e for e in res.log if e.get("status") == "skipped"]
            acted = [e for e in res.log if e.get("status") == "ok"]
            if value:
                say = f"다 됐어요. 결과는 {value} 입니다."
            elif skipped and not acted and not captured:
                # Every step stood down because the world already looked the way
                # the recipe wanted. "다 됐어요" would suggest work happened.
                say = "이미 그렇게 되어 있어서 할 일이 없었어요."
            elif len(captured) == 1:
                say = "확인했어요. " + _readable(next(iter(captured.values())))
            elif captured:
                # One line each: a folder listing and its summary run together
                # into something nobody can read at a glance.
                say = "확인했어요.\n" + "\n".join(
                    f"  {k}: {_readable(v)}" for k, v in captured.items())
            else:
                say = "다 됐어요. 결과를 확인했습니다."
            return Turn("done", say, flow.name, last)

        unverified = next((e for e in res.log if e.get("status") == "unverified"), None)
        if unverified is not None:
            return Turn("unverified", "동작은 요청했지만 실제 저장·전달 결과를 확인하지 못했습니다. "
                        "같은 동작을 자동으로 다시 실행하지 않았습니다.",
                        flow.name, unverified)

        gated = next((e for e in res.log if e.get("status") == "gated"), None)
        if gated:
            step = flow.steps[gated["step"]]
            what = step.intent_text or step.action
            if gated["gate"] == "handoff_request":
                return Turn("needs_handoff",
                            f"이 단계는 직접 해주셔야 해요: {what}. 끝나시면 이어서 할게요.",
                            flow.name, gated)
            return Turn("needs_confirm",
                        f"'{what}' 단계는 확인이 필요해요. 진행할까요? ({gated.get('reason','')})",
                        flow.name, gated)

        err = next((e for e in res.log if e.get("status") == "error"), last)
        step_i = err.get("step")
        where = flow.steps[step_i].intent_text if isinstance(step_i, int) else "시작"
        extra = ""
        if err.get("error") == "postcondition_timeout" and "observed" in err:
            extra = (f" (기대값 {err.get('expected')!r}, 화면에서 읽은 값 "
                     f"{err.get('observed')!r})")
        return Turn("failed", f"'{where}' 단계에서 막혔어요. {self._why(err)}{extra}",
                    flow.name, err)

    # Plain-language cause, so the user knows what to DO. The raw error can be
    # nested under `detail` (e.g. the initial observe), so dig for it.
    _CAUSE = {
        "target_not_found": "그 앱을 찾지 못했어요. 앱이 켜져 있는지, 이름이 맞는지 확인해 주세요.",
        "gui_session_unavailable": "지금은 화면에 접근할 수 없어요. tmux 안에서 실행 중이라면, tmux 밖 터미널에서 실행하거나 `reattach-to-user-namespace uv run nlc ...` 로 실행해 주세요.",
        "no_window_content": "앱 창이 보이지 않아요. 화면 잠금을 풀고 창을 띄워 주세요.",
        "empty_tree": "앱 화면을 읽을 수 없어요. 창을 한 번 클릭해 주세요.",
        "target_missing": "화면에서 눌러야 할 항목을 찾지 못했어요. 화면이 예상과 다른 것 같아요.",
        "target_ambiguous": "같은 이름의 항목이 여러 개라 무엇을 눌러야 할지 모르겠어요.",
        "precondition_failed": "눌러야 할 항목이 지금은 비활성 상태예요.",
        "postcondition_timeout": "동작은 했는데 결과가 화면에 나타나지 않았어요.",
        "expect_not_met": "결과가 예상과 달라요.",
        "stale_observation": "화면이 도중에 바뀌었어요. 다시 시도해 주세요.",
        "action_failed": "그 동작을 수행하지 못했어요.",
    }

    # A dialog left open by an earlier run blocks everything that follows, and
    # the fix is one sentence — but only if the person is told what it is.
    _DIALOG_HINT = "  (\"대화상자 닫아줘\" 라고 말씀하시면 제가 닫아드릴게요.)"

    def _why(self, err: dict) -> str:
        code = err.get("error")
        detail = err.get("detail")
        if code is None and isinstance(detail, dict):
            code = detail.get("error")
            detail = detail.get("detail")
        base = self._CAUSE.get(code, "화면 상태를 다시 확인해 주세요.")
        if isinstance(detail, str) and "대화상자" in detail:
            return detail + self._DIALOG_HINT
        # A driver-supplied Korean detail (candidate app list, session hint) is
        # more actionable than the generic line — prefer it when present.
        if isinstance(detail, str) and detail and detail != base:
            return detail
        return base


if __name__ == "__main__":
    a = Agent(narrate=lambda s: print(f"  🗣  {s}"))
    print("USER: 계산기로 12 곱하기 34 해줘\n")
    t = a.handle("계산기로 12 곱하기 34 해줘")
    print(f"\nBOT [{t.status}]: {t.say}")
