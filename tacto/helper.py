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
from .environment import value as _environment_value

def _runtime_root(module_file, prefix):
    """Locate either a source package or the fixed standalone .venv layout."""
    from pathlib import Path
    package_root = Path(module_file).resolve().parent.parent
    environment = Path(prefix).resolve()
    runtime = environment.parent
    helper = runtime / "helper/Tacto Helper.app/Contents/MacOS/TactoHelper"
    if (environment.name == ".venv" and package_root.is_relative_to(environment)
            and helper.is_file() and helper.resolve().is_relative_to(runtime)):
        return str(runtime)
    return str(package_root)


import sys
_ROOT = _runtime_root(__file__, sys.prefix)

# Prefer the .app bundle: TCC identifies it by signature and lists it by name,
# so the grant belongs to "Tacto Helper" rather than to whatever the bare
# executable happens to be called, and survives rebuilds of the same identity.
_BUNDLED = os.path.join(_ROOT, "helper", "Tacto Helper.app",
                        "Contents", "MacOS", "TactoHelper")
_BARE = os.path.join(_ROOT, "helper", "tacto-helper")
def helper_candidates():
    """Existing builds, ordered exactly as launcher selection (bundle first)."""
    return [path for path in (_BUNDLED, _BARE) if os.path.isfile(path)]


HELPER = next((path for path in (_BUNDLED, _BARE) if os.path.isfile(path)), _BARE)


def helper_display_name(path=None) -> str:
    """Name the installed permission holder as macOS lists it."""
    import plistlib
    from pathlib import Path
    binary = Path(HELPER if path is None else path)
    if binary.is_file() and binary.parent.name == "MacOS":
        try:
            from Foundation import NSFileManager
            name = str(NSFileManager.defaultManager().displayNameAtPath_(str(binary.parent.parent.parent)))
            return name[:-4] if name.endswith(".app") else name
        except ImportError:
            pass
    if binary.parent.name == "MacOS" and binary.parent.parent.name == "Contents":
        try:
            contents = binary.parent.parent
            with (contents / "Info.plist").open("rb") as stream:
                info = plistlib.load(stream)
            # Both shipped locales use the same product name. Keep the base
            # name matching the filename so Finder also localizes it.
            localized = contents / "Resources/en.lproj/InfoPlist.strings"
            if localized.is_file():
                with localized.open("rb") as stream:
                    names = plistlib.load(stream)
                if isinstance(names, dict):
                    info = {**info, **names}
            if not isinstance(info, dict):
                return "Tacto Helper"
            for key in ("CFBundleDisplayName", "CFBundleName"):
                name = info.get(key)
                if isinstance(name, str) and name.strip():
                    return name
        except (OSError, ValueError, TypeError):
            pass
    return "Tacto Helper"


def _unhide_target(target: str) -> bool:
    """Unhide `target` without activating it; True if its windows came back."""
    try:
        from AppKit import NSWorkspace
        from .driver import _unhide_quietly
        pid = next((int(a.processIdentifier())
                    for a in NSWorkspace.sharedWorkspace().runningApplications()
                    if target in (a.bundleIdentifier(), a.localizedName())), None)
        return pid is not None and _unhide_quietly(pid)
    except Exception:
        return False


def _explain_no_window(target: str) -> dict:
    """Lock state, reason and detail for a helper's bare no_window_content."""
    try:
        from AppKit import NSWorkspace
        from .driver import _why_no_window
        from .session import lock_state
        lock = lock_state()
        pid = next((int(a.processIdentifier())
                    for a in NSWorkspace.sharedWorkspace().runningApplications()
                    if target in (a.bundleIdentifier(), a.localizedName())), None)
        if pid is None:
            return {"lock": lock}
        reason, detail = _why_no_window(pid, lock)
        return {"lock": lock, "reason": reason, "detail": detail}
    except Exception:
        return {}


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


DOCUMENT_WINDOW_STATUS_REASONS = frozenset({
    'native_read_failed','request_invalid_or_pin_missing','process_changed','process_unavailable',
    'window_enumeration_unavailable','window_role_unavailable','window_changed',
    'document_path_changed','document_path_ambiguous_or_reopened','global_windows_unavailable',
    'global_window_identity_malformed','global_window_still_present','window_snapshot_changed','deadline_or_abort',
})


class InheritedHelperDriver:
    """Speak L2 over the socket the signed launcher handed us.

    Python never connects to the helper itself — a shared interpreter cannot be
    authenticated, since it looks the same whoever runs it — and it never learns
    the helper's address. The launcher is the peer the helper authenticates, it
    vets every message, and it passes this end of a socketpair as fd 3. There is
    no way to obtain this channel other than being the launcher's child."""

    FD_ENV = "TACTO_HELPER_FD"
    # One inherited descriptor means one channel. Wrapping it twice would give
    # two objects that each close the same fd, so the second user finds it gone.
    _shared = None

    @classmethod
    def available(cls) -> bool:
        return _environment_value("HELPER_FD") is not None

    def __new__(cls):
        if cls._shared is None:
            cls._shared = super().__new__(cls)
            cls._shared._ready = False
        return cls._shared

    def __init__(self):
        if getattr(self, "_poisoned", False):
            raise HelperUnavailable("오류 또는 시간 초과로 권한 채널이 종료되었습니다")
        if getattr(self, "_ready", False):
            return                      # already connected on this channel
        fd = _environment_value("HELPER_FD")
        if fd is None:
            raise HelperUnavailable(
                "권한 채널이 없습니다 — 서명된 런처로 실행하세요:  ./launcher/tacto <명령>")
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
            self.handshake = pong       # what the helper said about itself
            if not isinstance(pong, dict) or pong.get("type") != "pong":
                raise HelperUnavailable(f"도우미가 응답하지 않습니다: {pong}")
            if not pong.get("trusted"):
                raise HelperUnavailable(
                    "도우미에 접근성 권한이 없습니다 — "
                    "시스템 설정에서 'Tacto Helper' 를 켜주세요")
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
            from .helper_identity import parse_helper_identity, HelperIdentityError
            try:
                self.helper_process_identity = parse_helper_identity(pong.get('helper_process'), pong.get('helper_peer'))
            except HelperIdentityError:
                raise HelperUnavailable('도우미 프로세스 메타데이터가 올바르지 않습니다') from None
        except BaseException:
            # Leave nothing a later construction could mistake for a working
            # channel, and drop the shared instance so the next attempt starts
            # over rather than inheriting this one.
            for attr in ('_f', '_sock'):
                resource = getattr(self, attr, None)
                if resource is not None:
                    try:
                        resource.close()
                    except Exception:
                        pass
            for attr in ("_sock", "_f", "capabilities", "helper_process_identity"):
                if hasattr(self, attr):
                    delattr(self, attr)
            self._ready = False
            type(self)._shared = None
            raise
        self.capabilities = caps
        self._ready = True

    def _ask_launcher_response(self, prompt: str, want_json: bool = False,
                               provider: str = "auto", *, timeout_seconds=420, image=None, model=None) -> dict:
        """Put a question to a model through the launcher, which is outside.

        The same channel, a different destination: the launcher answers this
        one itself rather than passing it to the helper, because asking needs
        the user's PATH and touching the screen must not have it.
        """
        if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
                or not 0 < timeout_seconds <= 420):
            raise ValueError("timeout_seconds must be finite, positive, and at most 420")
        if image is not None:
            from .provider_images import decode_image, validate_model
            decode_image(image); validate_model(model)
            if provider not in ('claude', 'codex'):
                raise ValueError('image provider must be explicitly pinned')
        elif model is not None:
            raise ValueError('image model requires an image')
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
            message = {"type": "ask", "prompt": prompt, "json": want_json,
                       "provider": provider, "timeout_seconds": timeout_seconds}
            if image is not None:
                message['image'] = image
                if model is not None:
                    message['model'] = model
            r = self._send(message)
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
                            provider: str = "auto", *, timeout_seconds=420, image=None, model=None):
        import uuid
        from dataclasses import replace
        from .provider_result import ProviderError, ProviderResult, parse_provider_result
        if image is None and model is not None:
            raise ValueError('image model requires an image')
        started = time.monotonic()
        try:
            extra = {'image': image, 'model': model} if image is not None else {}
            response = self._ask_launcher_response(prompt, want_json, provider,
                                                   timeout_seconds=timeout_seconds, **extra)
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
        if image is not None:
            raise ValueError('image requests require a structured provider envelope')
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
            raise HelperUnavailable("권한 채널이 영구적으로 종료되었습니다")
        # A caller's serialization error happens before sending anything and
        # does not desynchronize the stream. Once IO starts, a lost or malformed
        # reply leaves the effect unknown; a queued reply must never answer the
        # next request, and this channel cannot be reused or silently reattached.
        payload = json.dumps(msg, ensure_ascii=False) + "\n"
        try:
            self._f.write(payload)
            self._f.flush()
            line = self._f.readline()
            if not line:
                raise HelperUnavailable("권한 채널이 닫혔습니다")
            response = json.loads(line)
            if (not isinstance(response, dict) or not isinstance(response.get('type'), str)
                    or not response['type']):
                raise HelperUnavailable("권한 채널 응답 형식이 잘못되었습니다")
            return response
        except BaseException as exc:
            self._poisoned = True
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                # macOS returns ENOTCONN for SHUT_RDWR after peer write-EOF,
                # even though our write half remains open. Close that half
                # before a buffered file close could flush pending bytes.
                try:
                    self._sock.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
            for resource in (self._f, self._sock):
                try:
                    resource.close()
                except (OSError, ValueError):
                    pass
            if isinstance(exc, (OSError, ValueError, HelperUnavailable)):
                # Never leak a malformed reply or OS diagnostic (possibly
                # containing observed document text) through the exception.
                raise HelperUnavailable("권한 채널의 응답을 확인할 수 없어 종료했습니다") from None
            raise


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
        env = dict(os.environ, TACTO_HELPER_TOKEN=self._token)
        self._proc = subprocess.Popen(
            [path], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1, env=env)
        self._last_action_at = 0.0
        try:
            try:
                pong = self._send({"type": "ping", "token": self._token})
            except HelperUnavailable:
                # Bound collection even if a broken helper closed stdout but
                # stayed alive. communicate also reaps the exited process.
                try:
                    _, err = self._proc.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    _, err = self._proc.communicate(timeout=2)
                if self._proc.returncode == 3:
                    raise HelperUnavailable(
                        "도우미가 이 클라이언트를 거부했습니다. 서명된 런처를 통해 "
                        "실행하세요:  ./launcher/tacto <명령> --helper\n"
                        "동일한 서명으로 런처와 도우미를 함께 빌드하세요.") from None
                raise HelperUnavailable(f"도우미가 시작되지 못했습니다: {(err or '').strip()[:200]}")
            self.handshake = pong           # what the helper said about itself
            if not isinstance(pong, dict) or pong.get("type") != "pong":
                raise HelperUnavailable(f"도우미가 응답하지 않습니다: {pong}")
            if not pong.get("trusted"):
                raise HelperUnavailable(
                    "도우미에 접근성 권한이 없습니다 — 시스템 설정 > 개인정보 보호 및 "
                    "보안 > 손쉬운 사용 에서 'Tacto Helper' 를 켜주세요 "
                    "(macOS 27 에서는 'Device Control and Data Access')")
            from .helper_identity import parse_helper_identity, HelperIdentityError
            try:
                self.helper_process_identity = parse_helper_identity(pong.get('helper_process'), pong.get('helper_peer'))
            except HelperIdentityError:
                raise HelperUnavailable('도우미 프로세스 메타데이터가 올바르지 않습니다') from None
        except BaseException:
            self.close()
            raise

    def close(self):
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
            self._proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait(timeout=2)
        finally:
            for stream in (self._proc.stdin, self._proc.stdout, self._proc.stderr):
                if stream:
                    stream.close()

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
                require_window: bool = True, *, document_token=None, _settle_after_action=True):
        if document_token is not None and (target != 'com.apple.TextEdit'
                or not isinstance(document_token, str) or not document_token or len(document_token) > 128):
            return {'type':'error', 'error':'document_snapshot_unavailable', 'effect':'not_started'}
        if document_token is not None and 'observe.document_token' not in getattr(self, 'capabilities', ()):
            return {'type':'error', 'error':'document_snapshot_unavailable', 'effect':'not_started',
                    'detail':'helper does not support scoped document observations'}
        # Same settle contract as the in-process driver: an action's effect may
        # land a moment after the call returns, so wait before capturing.
        settle = self._last_action_at + 1.0 - time.monotonic()
        if _settle_after_action and settle > 0:
            time.sleep(settle)
        # `require_window` travels on the wire because the two drivers have to
        # return the same tree for the same app: a recipe that starts from an
        # app with nothing open must behave identically whichever one runs it.
        message = {"type":"observe", "target":target, "require_window":require_window}
        if document_token is not None:
            message['document_token'] = document_token
        out = self._send(message)
        if out.get("error") == "no_window_content" and "reason" not in out:
            # The helper says only that there is no window. Which of the
            # causes it is — a locked screen, a hidden app, minimised windows —
            # is what tells a person what to do, and none of it needs the
            # helper's permission to find out.
            out = {**out, **_explain_no_window(target)}
            if out.get("reason") == "app_hidden" and _unhide_target(target):
                # Same as the in-process driver: a hidden app's windows come
                # back without it being made frontmost, then look once more.
                # Without this the `--helper` path still told agents to "click
                # TextEdit in the Dock" (2026-10-07 comparison, run 0).
                again = self._send(message)
                if again.get("error") == "no_window_content" and "reason" not in again:
                    again = {**again, **_explain_no_window(target)}
                # Preserve the latest refusal; a failed re-observation is not
                # evidence that the old hidden-window diagnosis still applies.
                return again
        return out

    def act(self, obs_id, target, action, expect=None, risk_class="none",
            intent_text="", approved=False, *, save_panel_token=None, submit_authorized=False):
        if action.get('name') == 'open_select':
            from .protocol import native_opening_parameters_problem
            problem = native_opening_parameters_problem(action)
            if problem:
                return {'type':'error','error':'invalid_selection_parameters',
                        'effect':'not_started','detail':problem}
            if 'opening_action' in action and 'action.open_select.explicit_ax' not in getattr(self,'capabilities',()):
                return {'type':'error','error':'unsupported_opening_action','effect':'not_started'}
        if action.get('name') in ('select_option', 'select_custom_option'):
            from .protocol import native_selection_parameters_problem
            problem = native_selection_parameters_problem(action)
            if problem:
                return {'type':'error', 'error':'invalid_selection_parameters',
                        'effect':'not_started', 'detail':problem}
        if save_panel_token is not None:
            if (not isinstance(save_panel_token, str) or not save_panel_token
                    or len(save_panel_token) > 128
                    or not isinstance(action, dict)
                    or set(action) != {'name', 'element_index'}
                    or action['name'] != 'click'
                    or type(action['element_index']) is not int
                    or action['element_index'] < 0
                    or risk_class not in ('confirm', 'preapproved') or approved is not True
                    or 'action.click.save_panel_guard' not in getattr(self, 'capabilities', ())):
                return {'type': 'error', 'error': 'save_panel_guard_unavailable',
                        'effect': 'not_started'}
        if action.get('name') == 'window_input':
            from .native_window_payload import window_input_problem, WINDOW_INPUT_CAPABILITIES
            problem = window_input_problem(action)
            if problem or not WINDOW_INPUT_CAPABILITIES <= set(getattr(self, 'capabilities', ())):
                return {'type':'error', 'error':'window_input_unavailable', 'effect':'not_started', 'detail':problem}
            if risk_class not in ('confirm','preapproved') or approved is not True:
                return {'type':'error','error':'window_input_authorization_required','effect':'not_started'}
        if action.get('name') == 'menu' and 'focused_document' in action \
                and 'action.menu.focused_document' not in getattr(self, 'capabilities', ()):
            # An older helper ignores the field and presses anyway — Save on
            # whatever is in front. Refused before anything is sent (Codex
            # review of 31955f0).
            return {'type': 'error', 'error': 'focused_document_guard_unavailable',
                    'effect': 'not_started'}
        if submit_authorized is not False:
            # Only replay sets this, after checking the step's submit contract.
            if (submit_authorized is not True or action.get('name') != 'press_key'
                    or action.get('key') != 'Return'
                    or 'press_key.return_bound' not in getattr(self, 'capabilities', ())):
                return {'type': 'error', 'error': 'submit_authorization_invalid',
                        'effect': 'not_started'}
        if action.get('name') in ('click_point', 'vroid_drag', 'vroid_focus'):
            if risk_class not in ('confirm','preapproved') or approved is not True:
                return {'type':'error','error':'coordinate_authorization_required','effect':'not_started'}
            if ('action.' + action['name'] if action['name'] in ('vroid_drag','vroid_focus') else 'action.click_point') not in getattr(self,'capabilities',()):
                return {'type':'error','error':'coordinate_authorization_required','effect':'not_started',
                        'detail':'helper does not support authorized window points'}
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
        if save_panel_token is not None:
            msg['save_panel_token'] = save_panel_token
        if action.get('name') in ('click_point', 'vroid_drag', 'vroid_focus'):
            msg['coordinate_authorized'] = True
        if action.get('name') == 'window_input':
            msg['window_input_authorized'] = True
        if submit_authorized is True:
            msg['submit_authorized'] = True
        if action.get("name") in ("click", "click_point", "vroid_drag", "vroid_focus", "prepare_window", "press_key", "window_input"):
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
            self._last_action_at = time.monotonic()
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
        if (type(timeout_seconds) not in (int, float)
                or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 10):
            raise ValueError('capture timeout must be finite, positive, and at most 10')
        if (window_index is not None
                and (type(window_index) is not int or not 0 <= window_index < 64)):
            raise ValueError('invalid capture window index')
        if obs_id is None:
            return {"type": "error", "error": "stale_observation",
                    "effect": "not_started",
                    "detail": "observe first: a capture is of an OBSERVED window"}
        message = {"type": "capture", "target": target, "obs_id": obs_id,
                   "timeout_seconds": timeout_seconds}
        if window_index is not None:
            message["window_index"] = window_index
        answer = self._send(message)
        if answer.get("type") == "capture" and path is not None:
            import hashlib
            import shutil
            created = False
            try:
                # A screenshot destination is a new private artifact. Do not
                # truncate an existing file, including one reached by a symlink.
                with open(answer["path"], 'rb') as source:
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    created = True
                    with os.fdopen(fd, 'wb') as destination:
                        shutil.copyfileobj(source, destination)
                with open(path, 'rb') as copied:
                    digest = hashlib.file_digest(copied, 'sha256').hexdigest()
                if digest != answer.get('sha256'):
                    raise OSError('capture artifact digest mismatch')
                answer = {**answer, "path": str(path), "helper_path": answer["path"]}
            except OSError as exc:
                if created:
                    os.unlink(path)
                return {"type": "error", "error": "visual_read_failed",
                        "effect": "not_started", "detail": str(exc)}
        return answer

    def save_panel(self, obs_id, target, window_element_index, *, panel_token=None,
                   document_token=None, require_format=False):
        """Read observation-bound settings; this never authorizes Save."""
        if (not isinstance(obs_id, str) or not obs_id.strip() or len(obs_id) > 128
                or not isinstance(target, str) or not target
                or type(window_element_index) is not int or not 0 <= window_element_index < 5000
                or type(require_format) is not bool
                or (panel_token is not None and (not isinstance(panel_token, str)
                    or not panel_token or len(panel_token) > 128))
                or (document_token is not None and (target != 'com.apple.TextEdit'
                    or not isinstance(document_token, str) or not document_token or len(document_token) > 128))):
            return {'type': 'error', 'error': 'save_panel_unavailable', 'effect': 'not_started',
                    'detail': 'invalid save panel observation request'}
        if 'evidence.save_panel' not in self.capabilities:
            return {'type': 'error', 'error': 'save_panel_unavailable', 'effect': 'not_started',
                    'detail': 'helper lacks save-panel observation capability'}
        message = {'type': 'save_panel', 'target': target, 'obs_id': obs_id,
                   'window_element_index': window_element_index, 'require_format': require_format}
        if panel_token is not None:
            message['panel_token'] = panel_token
        if document_token is not None:
            message['document_token'] = document_token
        return self._send(message)

    def document_rtf_export(self, target, document_token, *, max_utf16_units=1000000,
                            timeout_seconds=3):
        """Export the pinned AX text range; never certify its original format."""
        if (target != 'com.apple.TextEdit' or not isinstance(document_token, str)
                or not document_token or len(document_token) > 128
                or type(max_utf16_units) is not int or not 0 <= max_utf16_units <= 1000000
                or type(timeout_seconds) not in (int, float)
                or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 6
                or 'evidence.document_rtf_export' not in getattr(self, 'capabilities', ())):
            return {'type':'error', 'error':'document_rtf_export_unavailable', 'effect':'not_started'}
        response = self._send({'type':'document_rtf_export', 'target':target,
            'document_token':document_token, 'max_utf16_units':max_utf16_units,
            'timeout_seconds':timeout_seconds})
        if not isinstance(response, dict):
            return {'type':'error', 'error':'document_rtf_export_unavailable', 'effect':'not_started'}
        if response.get('type') != 'document_rtf_export':
            return response
        import base64
        import hashlib
        import re
        try:
            encoded = response['rtf_base64']
            count = response['utf16_length']
            if (response.get('document_token') != document_token
                    or type(count) is not int or not 0 <= count <= max_utf16_units
                    or not isinstance(encoded, str) or len(encoded) > 4 * ((2097152 + 2) // 3)
                    or any(response.get(k) is not False for k in
                        ('source_format_verified', 'attachment_completeness_verified', 'authorizes_save'))
                    or response.get('evidence_kind') != 'ax_range_rtf_export'
                    or not isinstance(response.get('text_sha256'), str)
                    or re.fullmatch('[0-9a-f]{64}', response['text_sha256']) is None
                    or 'document_path' not in response):
                raise ValueError('invalid document export metadata')
            raw = base64.b64decode(encoded, validate=True)
            if (not raw.startswith(b'{\\rtf') or len(raw) > 2097152
                    or base64.b64encode(raw).decode('ascii') != encoded
                    or hashlib.sha256(raw).hexdigest() != response.get('rtf_sha256')):
                raise ValueError('invalid document export bytes')
            if response['document_path'] is not None:
                from .native_document import validate_document_selection_path
                validate_document_selection_path(response['document_path'])
        except (KeyError, ValueError, TypeError, UnicodeError):
            return {'type':'error', 'error':'document_rtf_export_unavailable', 'effect':'not_started'}
        return response

    def document_attachment_metadata(self, target, document_token, *, max_utf16_units=100000,
                                     timeout_seconds=3):
        """Live attributed attachment references; never certify payload bytes."""
        unavailable = {'type':'error', 'error':'document_attachment_metadata_unavailable', 'effect':'not_started'}
        if (target != 'com.apple.TextEdit' or not isinstance(document_token, str)
                or not document_token or len(document_token) > 128
                or type(max_utf16_units) is not int or not 0 <= max_utf16_units <= 100000
                or type(timeout_seconds) not in (int, float)
                or not 0 < timeout_seconds <= 6
                or 'evidence.document_attachment_metadata' not in getattr(self, 'capabilities', ())):
            return unavailable
        response = self._send({'type':'document_attachment_metadata', 'target':target,
            'document_token':document_token, 'max_utf16_units':max_utf16_units,
            'timeout_seconds':timeout_seconds})
        import re
        try:
            if not isinstance(response, dict) or response.get('type') != 'document_attachment_metadata':
                return unavailable
            allowed = {'type','document_token','document_path','utf16_length','text_sha256','occurrences',
                       'evidence_kind','sampled_stable','proves_no_intermediate_change','source_format_verified',
                       'attachment_completeness_verified','attachment_payload_verified','authorizes_save'}
            count = response['utf16_length']
            rows = response['occurrences']
            if (set(response) != allowed or response['document_token'] != document_token
                    or type(count) is not int or not 0 <= count <= max_utf16_units
                    or response['evidence_kind'] != 'ax_attributed_attachment_metadata'
                    or response['sampled_stable'] is not True
                    or any(response[k] is not False for k in ('proves_no_intermediate_change','source_format_verified',
                        'attachment_completeness_verified','attachment_payload_verified','authorizes_save'))
                    or not isinstance(response['text_sha256'], str)
                    or re.fullmatch('[0-9a-f]{64}', response['text_sha256']) is None
                    or type(rows) is not list or len(rows) > 256):
                raise ValueError('invalid attachment metadata')
            previous = -1
            for row in rows:
                if (type(row) is not dict or set(row) != {'utf16_index','ax_attachment_present','ax_element_reference'}
                        or type(row['utf16_index']) is not int or not previous < row['utf16_index'] < count
                        or type(row['ax_attachment_present']) is not bool
                        or type(row['ax_element_reference']) is not bool
                        or (row['ax_element_reference'] and not row['ax_attachment_present'])):
                    raise ValueError('invalid attachment occurrence')
                previous = row['utf16_index']
            if response['document_path'] is not None:
                from .native_document import validate_document_selection_path
                validate_document_selection_path(response['document_path'])
        except (KeyError, ValueError, TypeError, UnicodeError):
            return unavailable
        return response

    def document_window_status(self, target, document_token):
        """Read a pinned window's lifecycle; errors never mean absence."""
        unavailable = {'type':'error', 'error':'document_window_status_unavailable', 'effect':'not_started'}
        if (target != 'com.apple.TextEdit' or not isinstance(document_token, str)
                or not document_token or len(document_token) > 128
                or 'evidence.document_window_status' not in getattr(self, 'capabilities', ())):
            return unavailable
        result = self._send({'type':'document_window_status', 'target':target,
                             'document_token':document_token})
        if (type(result) is dict and set(result) == {'type','error','effect','reason_code'}
                and result.get('type') == 'error' and result.get('error') == unavailable['error']
                and result.get('effect') == 'not_started' and isinstance(result.get('reason_code'),str)
                and result['reason_code'] in DOCUMENT_WINDOW_STATUS_REASONS):
            return {**unavailable, 'reason_code':result['reason_code']}
        if (type(result) is not dict or result.get('type') != 'document_window_status'
                or set(result) != {'type','document_token','state','same_process_verified'}
                or result['document_token'] != document_token
                or result['state'] not in ('open','closed')
                or result['same_process_verified'] is not True):
            return unavailable
        return result

    def document_text(self, target, document_token=None, *, max_utf16_units=1000000,
                      document_path=None):
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
        if document_path is not None:
            from .native_document import validate_document_selection_path, DocumentSnapshotError
            try:
                validate_document_selection_path(document_path)
                if document_token is not None or target != 'com.apple.TextEdit':
                    raise DocumentSnapshotError('invalid document selector combination')
            except DocumentSnapshotError as exc:
                return {'type':'error', 'error':'document_snapshot_unavailable',
                        'effect':'not_started', 'detail':str(exc)}
            if 'evidence.document_text.path_selector' not in getattr(self, 'capabilities', ()):
                return {'type':'error', 'error':'document_snapshot_unavailable', 'effect':'not_started',
                        'detail':'helper does not support document path selection'}
        message = {"type": "document_text", "target": target,
                   "max_utf16_units": max_utf16_units}
        if document_token is not None:
            if not isinstance(document_token, str) or not document_token or len(document_token)>128:
                return {"type": "error", "error": "document_snapshot_unavailable",
                        "effect": "not_started", "detail": "invalid document token"}
            message["document_token"] = document_token
        if document_path is not None:
            message['document_path'] = document_path
        return self._send(message)

    def wait_until(self, target, predicate, timeout_ms=4000, poll_ms=250):
        from .observation_state import retryable_observation_error
        deadline = time.monotonic() + timeout_ms / 1000
        last = None
        while True:
            # The predicate is the readiness witness. Check immediately;
            # sleeping first adds a second wait ahead of the polling loop.
            obs = self.observe(target, _settle_after_action=False)
            last = obs
            if obs.get("type") == "observation" and predicate(obs["tree"]):
                return obs
            if obs.get("type") != "observation" and not retryable_observation_error(obs):
                return obs
            if time.monotonic() >= deadline:
                if obs.get("type") != "observation":
                    return obs
                return {"type": "timeout", "last_obs": last}
            time.sleep(min(poll_ms / 1000, max(0, deadline - time.monotonic())))


# The L2 surface is transport-independent: observe/act/wait_until only need a
# `_send`, so the inherited-socket driver borrows the spawn driver's
# implementations rather than keeping a second copy in step with the first.
for _name in ("identity", "list_targets", "observe", "act", "probe",
              "selection", "menu_state", "menu_items", "document_text", "document_window_status", "document_rtf_export", "document_attachment_metadata", "save_panel", "capture",
              "wait_until"):
    impl = getattr(HelperDriver, _name)
    setattr(InheritedHelperDriver, _name, impl)
