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
import math
import socket
import threading
import os
import subprocess
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Prefer the .app bundle: TCC identifies it by signature and lists it by name,
# so the grant belongs to "NLC Helper" rather than to whatever the bare
# executable happens to be called, and survives rebuilds of the same identity.
_BUNDLED = os.path.join(_ROOT, "helper", "NLC Helper.app",
                        "Contents", "MacOS", "NLCHelper")
_BARE = os.path.join(_ROOT, "helper", "nlc-helper")
HELPER = _BUNDLED if os.path.exists(_BUNDLED) else _BARE


class HelperUnavailable(RuntimeError):
    pass


# What this client sends and therefore needs honoured. A helper that does not
# name all of these is one whose refusals and preconditions cannot be relied
# on, and silence is not agreement.
# Every risk class this transport knows how to honour. A value outside it is
# not a weaker one — it is one this code has no rule for, and passing it
# through let `blocked` act as though it had said `none` (peer request).
# Module level rather than a class attribute, because the L2 methods are copied
# onto the other transports and a class attribute would not travel with them.
RISK_CLASSES = ("none", "preapproved", "confirm", "handoff", "blocked")

REQUIRED_CAPABILITIES = {
    "observe", "act", "probe", "selection", "effect",
    "action.click.deadline_uptime",
    "expect.role", "expect.label", "expect.attribute", "expect.enabled",
    "expect.no_modal", "expect.selection",
}


class InheritedHelperDriver:
    """Speak L2 over the socket the signed launcher handed us.

    Python never connects to the helper itself — a shared interpreter cannot be
    authenticated, since it looks the same whoever runs it — and it never learns
    the helper's address. The launcher is the peer the helper authenticates, it
    vets every message, and it passes this end of a socketpair as fd 3. There is
    no way to obtain this channel other than being the launcher's child."""

    FD_ENV = "NLC_HELPER_FD"
    # One inherited descriptor means one channel. Wrapping it twice would give
    # two objects that each close the same fd, so the second user finds it gone.
    _shared = None

    @classmethod
    def available(cls) -> bool:
        return os.environ.get(cls.FD_ENV) is not None

    def __new__(cls):
        if cls._shared is None:
            cls._shared = super().__new__(cls)
            cls._shared._ready = False
        return cls._shared

    def __init__(self):
        if getattr(self, "_poisoned", False):
            raise HelperUnavailable("모델 시간 초과로 권한 채널이 종료되었습니다")
        if getattr(self, "_ready", False):
            return                      # already connected on this channel
        fd = os.environ.get(self.FD_ENV)
        if fd is None:
            raise HelperUnavailable(
                "권한 채널이 없습니다 — 서명된 런처로 실행하세요:  ./launcher/nlc <명령>")
        import socket as _s
        self._sock = _s.socket(fileno=int(fd))
        self._sock.setblocking(True)
        self._f = self._sock.makefile("rw", encoding="utf-8", newline="\n")
        self._last_action_at = 0.0
        # `_ready` is set at the END, and only on the way out of a successful
        # handshake. Setting it before the checks made the refusal last exactly
        # one construction: this class shares one instance, `__init__` returns
        # early when `_ready` is set, and so the SECOND `InheritedHelperDriver()`
        # got a driver whose handshake had failed — with the failure already
        # reported and forgotten (peer finding, with a regression that proves
        # it). A check that can be retried past is not a check.
        try:
            pong = self._send({"type": "ping"})
            if pong.get("type") != "pong":
                raise HelperUnavailable(f"도우미가 응답하지 않습니다: {pong}")
            if not pong.get("trusted"):
                raise HelperUnavailable(
                    "도우미에 접근성 권한이 없습니다 — "
                    "시스템 설정에서 'NLC Helper' 를 켜주세요")
            caps = set(pong.get("capabilities") or ())
            missing = REQUIRED_CAPABILITIES - caps
            if missing:
                # An ignored precondition looks exactly like a satisfied one,
                # which is how `expect` held in-process and did nothing here for
                # several commits. Refusing is the only honest answer: the
                # alternative is a client that thinks its checks ran.
                raise HelperUnavailable(
                    "도우미가 오래된 판입니다 — 다시 빌드해 주세요 "
                    f"(./helper/build.sh). 빠진 기능: {', '.join(sorted(missing))}")
        except Exception:
            # Leave nothing a later construction could mistake for a working
            # channel, and drop the shared instance so the next attempt starts
            # over rather than inheriting this one.
            for attr in ("_sock", "_f", "capabilities"):
                if hasattr(self, attr):
                    delattr(self, attr)
            self._ready = False
            type(self)._shared = None
            raise
        self.capabilities = caps
        self._ready = True

    def _ask_launcher_response(self, prompt: str, want_json: bool = False,
                               provider: str = "auto", *, timeout_seconds=420) -> dict:
        """Put a question to a model through the launcher, which is outside.

        The same channel, a different destination: the launcher answers this
        one itself rather than passing it to the helper, because asking needs
        the user's PATH and touching the screen must not have it.
        """
        if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
                or not 0 < timeout_seconds <= 420):
            raise ValueError("timeout_seconds must be finite, positive, and at most 420")
        if getattr(self, "_poisoned", False):
            raise HelperUnavailable("model channel is permanently closed")
        expired = threading.Event()
        deadline = time.monotonic() + timeout_seconds
        def expire():
            expired.set()
            self._poisoned = True
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._sock.close()
        timer = threading.Timer(timeout_seconds, expire)
        timer.daemon = True
        timer.start()
        r = None
        def timeout_error():
            error = TimeoutError("model channel deadline exceeded")
            if isinstance(r, dict) and r.get("error") == "model_timeout":
                error.provider_response = r
            return error
        try:
            r = self._send({"type": "ask", "prompt": prompt, "json": want_json,
                            "provider": provider, "timeout_seconds": timeout_seconds})
            if r.get("error") == "model_timeout":
                expire()
                raise timeout_error()
            return r
        finally:
            timer.cancel()
            timer.join()
            if expired.is_set() or time.monotonic() >= deadline:
                expire()
                try:
                    self._f.close()
                except (OSError, ValueError):
                    pass
                raise timeout_error()

    def ask_launcher(self, prompt: str, want_json: bool = False,
                     provider: str = "auto", *, timeout_seconds=420) -> str | None:
        response = self._ask_launcher_response(prompt, want_json, provider,
                                               timeout_seconds=timeout_seconds)
        # The launcher's code for a refusal, kept for the caller's message.
        self.last_ask_error = response.get("error")
        return response.get("text") if response.get("type") == "answer" else None

    def ask_launcher_result(self, prompt: str, want_json: bool = False,
                            provider: str = "auto", *, timeout_seconds=420):
        import uuid
        from dataclasses import replace
        from .provider_result import ProviderError, ProviderResult, parse_provider_result
        started = time.monotonic()
        try:
            response = self._ask_launcher_response(prompt, want_json, provider,
                                                   timeout_seconds=timeout_seconds)
        except TimeoutError as exc:
            response = getattr(exc, "provider_response", None)
            if not isinstance(response, dict) or "provider_result_v1" not in response:
                raise
        if "provider_result_v1" in response:
            result = parse_provider_result(response["provider_result_v1"])
            if result.requested_provider != provider:
                raise ValueError("provider request mismatch")
            if result.status == "ok" and (response.get("type") != "answer" or response.get("text") != result.text):
                raise ValueError("provider response text mismatch")
            if result.status != "ok":
                expected_error = "model_timeout" if result.status == "timeout" else "no_answer"
                if (response.get("type") != "error" or response.get("error") != expected_error
                        or response.get("text") is not None):
                    raise ValueError("provider error response mismatch")
            return replace(result, transport="launcher", elapsed_seconds=max(0, time.monotonic()-started))
        # Old launchers expose text only. Do not invent actual model/usage IDs.
        text = response.get("text") if response.get("type") == "answer" else None
        ok = isinstance(text, str) and bool(text)
        return ProviderResult(str(uuid.uuid4()), provider, None if provider == "auto" else provider,
            "launcher", "ok" if ok else "error", text if ok else None,
            None if ok else ProviderError("no_answer", "Model request failed"),
            max(0, time.monotonic()-started))

    def close(self):
        # Deliberately does not close the descriptor: the channel belongs to the
        # process, not to one driver object, and the launcher tears it down when
        # the command ends.
        try:
            self._f.flush()
        except Exception:
            pass

    def _send(self, msg: dict) -> dict:
        if getattr(self, "_poisoned", False):
            raise HelperUnavailable("model channel is permanently closed")
        self._f.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self._f.flush()
        line = self._f.readline()
        if not line:
            raise HelperUnavailable("권한 채널이 닫혔습니다")
        return json.loads(line)


# `SocketHelperDriver` was removed here. It spawned the helper with `--listen`
# and connected to it, and the helper does not listen any more: it is opened
# through LaunchServices, so that TCC attributes its screen-recording grant to
# the helper's own identity rather than to whatever spawned it, and it connects
# back to the launcher. Rewriting it around the new direction would not have
# given it back, either — the helper authenticates its peer against a compiled-in
# code requirement naming the signed launcher, which no Python process can
# satisfy. What is left would have been a transport that cannot connect,
# offered as though it could.


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
                "보안 > 손쉬운 사용 에서 'NLC Helper' 를 켜주세요 "
                "(macOS 27 에서는 'Device Control and Data Access')")

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

    def observe(self, target: str, mode: str = "tree_full",
                require_window: bool = True):
        # Same settle contract as the in-process driver: an action's effect may
        # land a moment after the call returns, so wait before capturing.
        settle = self._last_action_at + 1.0 - time.time()
        if settle > 0:
            time.sleep(settle)
        # `require_window` travels on the wire because the two drivers have to
        # return the same tree for the same app: a recipe that starts from an
        # app with nothing open must behave identically whichever one runs it.
        return self._send({"type": "observe", "target": target,
                           "require_window": require_window})

    def act(self, obs_id, target, action, expect=None, risk_class="none",
            intent_text="", approved=False):
        if risk_class not in RISK_CLASSES:
            return {"type": "error", "error": "unknown_risk_class",
                    "effect": "not_started",
                    "detail": f"모르는 위험 등급이라 실행하지 않았습니다: "
                              f"{risk_class!r}"}
        if risk_class == "blocked":
            # Blocked means nobody could say what this would do. There is no
            # approval that fixes that, so there is nothing to ask.
            return {"type": "error", "error": "blocked",
                    "effect": "not_started",
                    "detail": intent_text or "무슨 일이 일어날지 확정할 수 없습니다"}
        if risk_class == "handoff":
            return {"type": "handoff_request", "reason": intent_text}
        if risk_class in ("confirm", "preapproved") and not approved:
            return {"type": "confirm_request", "what": intent_text,
                    "risk_class": risk_class}
        msg = {"type": "act", "obs_id": obs_id, "target": target,
               "action": action}
        if action.get("name") in ("click", "prepare_window"):
            from .model_budget import remaining_model_seconds
            # Capture before computing remaining time; never extend the budget
            # by serialization or time spent waiting for the helper.
            started = time.monotonic()
            try:
                msg["click_deadline_uptime"] = started + remaining_model_seconds(5)
            except TimeoutError:
                return {"type": "error", "error": "input_deadline_expired",
                        "effect": "not_started"}
        if expect:
            msg["expect"] = expect
        r = self._send(msg)
        if r.get("type") == "acted":
            self._last_action_at = time.time()
        return r

    def probe(self, obs_id, target, element_index):
        return self._send({"type": "probe", "obs_id": obs_id, "target": target,
                           "element_index": element_index})

    def menu_items(self, target, path=None):
        return self._send({"type": "menu_items", "target": target, "path": [] if path is None else path})

    def menu_state(self, target, path):
        """Read a running target's menu over the signed channel; never invoke it."""
        return self._send({"type": "menu_state", "target": target, "path": path})

    def selection(self, target):
        return self._send({"type": "selection", "target": target})

    def capture(self, target, *, obs_id=None, window_index=None, path=None,
                timeout_seconds=5):
        """A picture of one observed window, taken by the side that holds the grant.

        The screen-recording permission belongs here for the same reason the
        accessibility one does: on a shared Python interpreter it would be a
        permission every script on that interpreter holds, and a screenshot
        reaches further than a click.

        The helper writes the PNG to a file it owns and answers with the path,
        so the pixels never travel through the frame. `path` copies it where
        the caller asked and says so; the original stays until the caller
        removes it.
        """
        if obs_id is None:
            return {"type": "error", "error": "stale_observation",
                    "effect": "not_started",
                    "detail": "observe first: a capture is of an OBSERVED window"}
        message = {"type": "capture", "target": target, "obs_id": obs_id}
        if window_index is not None:
            message["window_index"] = window_index
        answer = self._send(message)
        if answer.get("type") == "capture" and path is not None:
            import shutil
            try:
                shutil.copyfile(answer["path"], path)
                answer = {**answer, "path": str(path), "helper_path": answer["path"]}
            except OSError as exc:
                return {"type": "error", "error": "visual_read_failed",
                        "effect": "not_started", "detail": str(exc)}
        return answer

    def document_text(self, target, document_token=None, *, max_utf16_units=1000000):
        """A pinned document's complete text, read over the signed channel.

        The same shape `Driver.document_text` answers with, and the same thing
        it refuses to claim: this witnesses what a document contains and where
        it lives, never its source format or where a save panel would put it.

        Malformed arguments are refused here as well as at the launcher and in
        the helper. Sending them on would make this end the one place a caller
        could get a different answer from the same request.
        """
        if (type(max_utf16_units) is not int or isinstance(max_utf16_units, bool)
                or not 0 <= max_utf16_units <= 1000000):
            return {"type": "error", "error": "document_snapshot_unavailable",
                    "effect": "not_started", "detail": "invalid max_utf16_units"}
        message = {"type": "document_text", "target": target,
                   "max_utf16_units": max_utf16_units}
        if document_token is not None:
            if not isinstance(document_token, str) or not document_token:
                return {"type": "error", "error": "document_snapshot_unavailable",
                        "effect": "not_started", "detail": "invalid document token"}
            message["document_token"] = document_token
        return self._send(message)

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


# The L2 surface is transport-independent: observe/act/wait_until only need a
# `_send`, so the inherited-socket driver borrows the spawn driver's
# implementations rather than keeping a second copy in step with the first.
for _name in ("identity", "list_targets", "observe", "act", "probe",
              "selection", "menu_state", "menu_items", "document_text", "capture",
              "wait_until"):
    impl = getattr(HelperDriver, _name)
    setattr(InheritedHelperDriver, _name, impl)
