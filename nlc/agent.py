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


# Intent resolution: map a natural-language request to a stored flow name.
# Deterministic and tokenless. A real deployment extends this with the model
# only when nothing matches (the `author` path below).
_INTENT_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"12\s*(곱하기|[x×*])\s*34|12.*(곱|multiply).*34"), "calc_12x34"),
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

    def resolve(self, intent: str) -> str | None:
        for rx, name in _INTENT_RULES:
            if rx.search(intent):
                return name
        # a request may also name a stored flow directly
        for name in self.store.list():
            if name.lower() in intent.lower():
                return name
        return None

    # ------------------------------------------------------------ run

    def handle(self, intent: str, approvals: dict[int, bool] | None = None) -> Turn:
        name = self.resolve(intent)
        if name is None:
            if self.author is None:
                return Turn("unknown_task",
                            "그 작업은 아직 방법을 몰라요. 한 번만 알려주시면 다음부터는 제가 알아서 할게요.")
            try:
                flow = self.author(intent, self.driver)   # the token-spending path
            except Exception as e:
                return Turn("unknown_task",
                            f"그 작업을 배우는 데 실패했어요. 조금 더 구체적으로 말씀해 주시겠어요? ({e})")
            self.store.save(flow)                          # learn once
            name = flow.name
        elif self.store.has(name):
            flow = self.store.load(name)
        else:
            from .flow import calculator_12x34        # built-in example flows
            builtins = {"calc_12x34": calculator_12x34}
            builder = builtins.get(name)
            if builder is None:
                return Turn("unknown_task", "그 작업 레시피를 찾지 못했어요.")
            flow = builder()
            self.store.save(flow)                     # cache it for next time

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
            return Turn("done", "다 됐어요. 결과를 확인했습니다.", flow.name, last)

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
        return Turn("failed", f"'{where}' 단계에서 막혔어요. {self._why(err)}",
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
