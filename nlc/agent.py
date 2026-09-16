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


# Intent resolution: map a natural-language request to a stored recipe AND the
# values it needs. Deterministic and tokenless. A real deployment extends this
# with the model only when nothing matches (the `author` path below).
_INTENT_RULES: list[tuple[re.Pattern, str, object]] = [
    (re.compile(r"(\d+)\s*(곱하기|더하기|빼기|나누기|[-+x×*/÷])\s*(\d+)"),
     "calc_arith", _calc),
    # A question about a folder is answered, not acted on; it is checked first
    # because "다운로드 폴더 보여줘" and "다운로드 폴더에 뭐 있어?" overlap.
    (re.compile(r"(" + "|".join(sorted(_FOLDERS, key=len, reverse=True)) +
                r")\s*(폴더|디렉터리|folder)?\s*(에|안에)?\s*"
                r"(뭐|무엇|몇\s*개|몇개|얼마나)", re.I),
     "finder_count_items", _folder),
    (re.compile(r"(" + "|".join(sorted(_FOLDERS, key=len, reverse=True)) +
                r")\s*(폴더|디렉터리|folder)?\s*(을|를|로)?\s*(열어|열기|보여|open)",
                re.I), "finder_open_folder", _folder),
    # Saving is checked before writing: "'보고서' 로 저장해줘" contains a quoted
    # string too, and reading it as "type 보고서" would be the wrong task.
    (re.compile(r"[\"'“](.+?)[\"'”]\s*(으로|로)?\s*저장", re.S),
     "textedit_save_as", lambda m: {"filename": m.group(1)}),
    (re.compile(r"(새|빈)\s*(문서|파일)"), "textedit_new_document", lambda m: {}),
    # An explicitly quoted string is a stronger signal than a stray "굵게", so
    # the write rule is tried first.
    (re.compile(r"[\"'“](.+?)[\"'”]\s*(라고|이라고)?\s*(써|적어|입력|write)",
                re.S), "textedit_write_note", lambda m: {"text": m.group(1)}),
    (re.compile(r"(굵게|볼드|bold)"), "textedit_emphasize", lambda m: {}),
]


class Agent:
    def __init__(self, driver: Driver | None = None, store: FlowStore | None = None,
                 narrate: Callable[[str], None] | None = None,
                 author: Callable[[str, Driver], Flow] | None = None):
        self.driver = driver or Driver()
        self.store = store or FlowStore()
        self.narrate = narrate or (lambda s: None)
        self.author = author   # optional model-backed flow author (spends tokens)

    # ------------------------------------------------------------ intent

    def resolve(self, intent: str) -> Match | None:
        for rx, name, extract in _INTENT_RULES:
            m = rx.search(intent)
            if m is None:
                continue
            try:
                return Match(name, extract(m))
            except KeyError:
                # The shape matched but a value did not (a folder we have no
                # menu item for). Keep looking rather than resolving to a recipe
                # we cannot supply.
                continue
        # a request may also name a stored flow directly
        for name in self.store.list():
            if name.lower() in intent.lower():
                return Match(name, {})
        return None

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
               ask: Callable[[str], bool] | None = None) -> Turn:
        match = self.resolve(intent)
        values: dict = {}
        if match is None:
            if self.author is None:
                return Turn("unknown_task",
                            "그 작업은 아직 방법을 몰라요. 한 번만 알려주시면 다음부터는 제가 알아서 할게요.")
            try:
                flow = self.author(intent, self.driver)   # the token-spending path
            except Exception as e:
                return Turn("unknown_task",
                            f"그 작업을 배우는 데 실패했어요. 조금 더 구체적으로 말씀해 주시겠어요? ({e})")
            self.store.save(flow)                          # learn once
        else:
            name, values = match.name, match.params
            if self.store.has(name):
                flow = self.store.load(name)
            else:
                from .library import builders
                builder = builders().get(name)
                if builder is None:
                    return Turn("unknown_task", "그 작업 레시피를 찾지 못했어요.")
                flow = builder()
                self.store.save(flow)                 # cache it for next time

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

        res = replay(self.driver, flow, approvals=approvals, narrate=self.narrate)
        return self._to_turn(flow, res)

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
            if value:
                say = f"다 됐어요. 결과는 {value} 입니다."
            elif len(captured) == 1:
                say = "확인했어요. " + str(next(iter(captured.values())))
            elif captured:
                say = "확인했어요. " + ", ".join(f"{k}: {v}"
                                               for k, v in captured.items())
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

    def _why(self, err: dict) -> str:
        code = err.get("error")
        detail = err.get("detail")
        if code is None and isinstance(detail, dict):
            code = detail.get("error")
            detail = detail.get("detail")
        base = self._CAUSE.get(code, "화면 상태를 다시 확인해 주세요.")
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
