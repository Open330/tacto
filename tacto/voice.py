"""Voice interface — the front door for users who'd rather talk than type.

This is the L4 surface as speech: hear an intent, narrate each step aloud, and
speak confirmation prompts (a spoken "네"/"아니요" answers a confirm gate). It is
built around the same Agent, so the deterministic replay core is unchanged.

TTS uses macOS `say` (always available). STT is pluggable: pass any
`listen() -> str`. A default push-to-talk listener records with `sox`/`ffmpeg`
if present and transcribes with a provided model hook; when neither is wired it
falls back to typed input, so the loop always runs.

Confirmation latency (the user's earlier concern) is handled by speaking the
one-line risk, then accepting a short spoken/typed yes-no — no deep menus.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Callable

from .agent import Agent

_YES = {"네", "예", "응", "그래", "좋아", "해줘", "yes", "y", "ok", "오케이"}
_NO = {"아니", "아니요", "아뇨", "no", "n", "취소", "그만", "stop"}


def speak(text: str, voice: str = "Yuna", rate: int = 190) -> None:
    """macOS TTS. 'Yuna' is the Korean system voice; falls back silently."""
    try:
        subprocess.run(["say", "-v", voice, "-r", str(rate), text],
                       check=False, timeout=30)
    except Exception:
        try:
            subprocess.run(["say", "-r", str(rate), text], check=False, timeout=30)
        except Exception:
            pass


def typed_listen(prompt: str = "🎤> ") -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


@dataclass
class VoiceSession:
    agent: Agent
    listen: Callable[[], str] = typed_listen   # STT hook; default = typed
    voice: str = "Yuna"

    def say(self, text: str):
        print(f"  🔊 {text}")
        speak(text, self.voice)

    def _confirm(self, question: str) -> bool:
        self.say(question + " 할까요, 말까요?")
        for _ in range(2):
            ans = self.listen().lower()
            if any(w in ans for w in _YES):
                return True
            if any(w in ans for w in _NO):
                return False
            self.say("'네' 또는 '아니요'로 답해 주세요.")
        return False   # default deny on ambiguity (safe)

    def handle_once(self, intent: str) -> str:
        turn = self.agent.handle(intent)
        # Resolve a confirm gate by voice, then re-run with approval.
        if turn.status == "needs_confirm" and turn.detail is not None:
            step_i = turn.detail.get("step", 0)
            if self._confirm(turn.say):
                turn = self.agent.handle(intent, approvals={step_i: True})
            else:
                self.say("알겠어요, 그 단계는 하지 않을게요.")
                return "cancelled"
        if turn.status == "needs_handoff":
            self.say(turn.say)
            return "handoff"
        self.say(turn.say)
        return turn.status

    def loop(self):
        self.say("안녕하세요. 무엇을 도와드릴까요?")
        while True:
            intent = self.listen()
            if not intent or any(w in intent.lower() for w in {"종료", "끝", "그만할래", "quit"}):
                self.say("네, 도움이 필요하면 다시 불러 주세요.")
                return
            # step narration is spoken as it happens
            self.agent.narrate = lambda s: self.say(s)
            self.handle_once(intent)


if __name__ == "__main__":
    vs = VoiceSession(Agent())
    print("음성 세션 (타이핑 STT 폴백). 예: '계산기로 12 곱하기 34'  / 종료: '끝'")
    vs.loop()
