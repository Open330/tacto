"""Drive the signed Swift helper instead of holding permissions ourselves.

Same L2 protocol, different side of a process boundary: `observe` and `act` go
out as one JSON line each and come back the same way. Because the protocol was
designed for that boundary, switching between this and the in-process driver is
a constructor change and nothing else.

The point is not speed — AX round-trips dominate either way — it is that the
Accessibility grant then belongs to one signed, single-purpose binary instead of
a shared Python interpreter that every other script on the machine also uses
(PROTOCOL.md §3.8).
"""

from __future__ import annotations

import json
import os
import subprocess
import time

HELPER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "helper", "nlc-helper")


class HelperUnavailable(RuntimeError):
    pass


class HelperDriver:
    """L2 endpoint backed by the Swift helper process."""

    def __init__(self, path: str = HELPER):
        if not os.path.exists(path):
            raise HelperUnavailable(
                f"{path} 이 없습니다. helper/build.sh 로 빌드하세요.")
        # A secret generated per launch and handed to the helper through its
        # environment: a process that cannot read that environment cannot drive
        # the helper even if it can execute the binary.
        import secrets
        self._token = secrets.token_hex(16)
        env = dict(os.environ, NLC_HELPER_TOKEN=self._token)
        self._proc = subprocess.Popen(
            [path], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1, env=env)
        self._last_action_at = 0.0
        try:
            pong = self._send({"type": "ping", "token": self._token})
        except HelperUnavailable:
            err = (self._proc.stderr.read() if self._proc.stderr else "") or ""
            self._proc.poll()
            if self._proc.returncode == 3:
                raise HelperUnavailable(
                    "도우미가 이 클라이언트를 거부했습니다. 서명된 런처를 통해 "
                    "실행하세요:  ./launcher/nlc <명령> --helper\n"
                    "  (개발 중이라면 NLC_HELPER_REQUIREMENT 로 요구조건을 바꿀 수 "
                    "있습니다)") from None
            raise HelperUnavailable(f"도우미가 시작되지 못했습니다: {err.strip()[:200]}")
        if pong.get("type") != "pong":
            raise HelperUnavailable(f"도우미가 응답하지 않습니다: {pong}")
        if not pong.get("trusted"):
            raise HelperUnavailable(
                "도우미에 접근성 권한이 없습니다 — 시스템 설정 > 개인정보 보호 및 "
                "보안 > 손쉬운 사용 에서 nlc-helper 를 켜주세요")

    def close(self):
        try:
            self._proc.stdin.close()
            self._proc.wait(timeout=2)
        except Exception:
            self._proc.kill()

    def _send(self, msg: dict) -> dict:
        self._proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self._proc.stdin.flush()
        line = self._proc.stdout.readline()
        if not line:
            raise HelperUnavailable("도우미 프로세스가 종료되었습니다")
        return json.loads(line)

    # ---------------------------------------------------------- L2 surface

    def identity(self, target: str) -> str:
        return target

    def list_targets(self):
        return self._send({"type": "list_targets"})

    def observe(self, target: str, mode: str = "tree_full"):
        # Same settle contract as the in-process driver: an action's effect may
        # land a moment after the call returns, so wait before capturing.
        settle = self._last_action_at + 1.0 - time.time()
        if settle > 0:
            time.sleep(settle)
        return self._send({"type": "observe", "target": target})

    def act(self, obs_id, target, action, risk_class="none", intent_text="",
            approved=False):
        if risk_class == "handoff":
            return {"type": "handoff_request", "reason": intent_text}
        if risk_class in ("confirm", "preapproved") and not approved:
            return {"type": "confirm_request", "what": intent_text,
                    "risk_class": risk_class}
        r = self._send({"type": "act", "obs_id": obs_id, "target": target,
                        "action": action})
        if r.get("type") == "acted":
            self._last_action_at = time.time()
        return r

    def wait_until(self, target, predicate, timeout_ms=4000, poll_ms=250):
        deadline = time.monotonic() + timeout_ms / 1000
        last = None
        while True:
            obs = self.observe(target)
            last = obs
            if obs.get("type") == "observation" and predicate(obs["tree"]):
                return obs
            if time.monotonic() >= deadline:
                return {"type": "timeout", "last_obs": last}
            time.sleep(poll_ms / 1000)
