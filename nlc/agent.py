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

import re
from dataclasses import dataclass
from typing import Callable

from .driver import Driver
from .flow import Flow, replay, ReplayResult
from .store import FlowStore


@dataclass
class Turn:
    """One agent turn's user-facing result."""
    status: str            # done | needs_confirm | needs_handoff | failed | unknown_task
    say: str               # plain-language message for the user
    flow: str | None = None
    detail: dict | None = None


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
    ("negation", ("하지 마", "하지마", "하지말", "말고", "말아", "빼고", "제외",
                  "아니라", "아니고", "대신", "안 해", "안해")),
    ("time:today", ("오늘", "today", "지금")),
    ("time:other", ("어제", "그제", "내일", "모레", "지난", "저번", "이번 주",
                    "이번주", "아까", "최근", "yesterday", "tomorrow")),
]


def _markers(intent: str) -> set[str]:
    low = intent.lower()
    return {tag for tag, words in _MARKERS if any(w in low for w in words)}


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
                 general: Callable | None = None):
        self.driver = driver or Driver()
        self.store = store or FlowStore()
        self.narrate = narrate or (lambda s: None)
        self.author = author   # optional model-backed flow author (spends tokens)
        # Injectable so a caller — a test, or a host with its own model — can
        # supply judgment without this module reaching for a provider.
        self.judge = judge
        # Reading the request itself, for the requests the patterns decline.
        # Separate from `judge`: this one is given the user's words and never
        # the screen, which is why what it returns may say what the user wants.
        self.interpret = interpret
        # Doing a task no recipe covers, by asking about each screen. The other
        # half of the product: a recipe answers "again", this answers "at all".
        self.general = general

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
            for name in self.store.list():
                if name.lower() in intent.lower():
                    return Match(name, {})
        return None

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
        return None

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
        reading = self.interpret(what_was_asked(intent, now=now))
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
        known = self._known_tasks()
        if not known:
            return None, reading
        pick = self.interpret(which_task(
            goal, known, excluded=reading.get("excluded", ())))
        if pick and pick.get("pick"):
            return Match(pick["pick"], {}), reading
        return None, reading

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
        env = None
        if reading.get("allows"):
            env = Envelope(quote=reading.get("quote", ""),
                           allows=frozenset(reading["allows"]),
                           scope=reading.get("scope", "entry"),
                           reversibility=reading.get("reversibility",
                                                     "reversible"))
        try:
            r = self.general(target, goal, ask, env)
        except Exception as e:
            return Turn("failed", f"화면을 보며 해봤지만 실패했어요: {e}")
        said = "\n".join(f"결과는 {v} 입니다" for v in r.said) if r.said else ""
        if r.ok and not r.verified:
            # It stopped, which is not the same as it worked. Saying "다 됐어요"
            # here is the report someone who cannot check for themselves has no
            # way to catch being wrong.
            return Turn("done", (said + "\n" if said else "")
                        + "다만 제대로 됐는지는 확인하지 못했어요.",
                        detail={"screens": r.path, "asked": r.asked,
                                "verified": False})
        if r.ok:
            return Turn("done", said or "끝냈어요.",
                        detail={"screens": r.path, "asked": r.asked,
                                "verified": True})
        did = ("여기까지 했어요: " + ", ".join(r.did[:8])
               + (" …" if len(r.did) > 8 else "")) if r.did else ""
        return Turn("failed",
                    "\n".join(x for x in (f"{r.why}.", did, said) if x),
                    detail={"screens": r.path, "asked": r.asked,
                            "did": r.did})

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
        for i, st in enumerate(flow.steps):
            if st.safety is None:
                continue
            decision = policy(st.safety)
            if decision.risk == "none":
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

    def handle(self, intent: str, approvals: dict[int, bool] | None = None,
               ask: Callable[[str], bool] | None = None,
               think: int = 0) -> Turn:
        match = self.resolve(intent)
        values: dict = {}
        reading: dict = {}
        if match is None and self.unrepresented(intent):
            # A rule DID match and was held back because the request said more
            # than it could hold. That is a reading problem, not a missing
            # recipe — authoring a new one here would learn the wrong task.
            match, reading = self.read_request(intent)
            if match is None and reading.get("negated"):
                return Turn("done", f"'{reading['quote']}' 라고 하셔서 "
                                    f"아무것도 하지 않았어요.")
            if match is None and self.general is None and self.author is None:
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
        res = replay(self.driver, flow, approvals=approvals,
                     narrate=self.narrate, escalate=judge, budget=think)
        self._report_what_it_asked(flow, res)
        return self._to_turn(flow, res)

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
