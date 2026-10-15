"""Permission onboarding wizard.

For the target user (someone uncomfortable with computers), granting macOS
Accessibility permission is the single biggest drop-off point. This wizard
checks each capability, and for a missing one it opens the exact System Settings
pane and gives one plain-language instruction — never a wall of steps.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from ApplicationServices import AXIsProcessTrusted, AXIsProcessTrustedWithOptions
import Quartz


@dataclass
class Check:
    key: str
    ok: bool
    title: str          # plain-language name
    fix_hint: str       # one sentence, what the user does
    settings_url: str   # the exact pane to open


def _screen_recording_ok() -> bool:
    # CGPreflightScreenCaptureAccess reports without prompting.
    try:
        return bool(Quartz.CGPreflightScreenCaptureAccess())
    except Exception:
        return False


def check_all() -> list[Check]:
    ax = AXIsProcessTrusted()
    sr = _screen_recording_ok()
    return [
        # macOS 27 renamed this pane: Privacy & Security > Accessibility is now
        # "Device Control and Data Access". The deep link below still resolves,
        # but a person reading only the old name cannot find it (measured on
        # 26A428), so both are said.
        Check("accessibility", ax, "앱 조작 권한(손쉬운 사용 / Device Control and Data Access)",
              "목록에서 이 앱을 켜주세요. 그러면 버튼을 대신 눌러드릴 수 있어요.",
              "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"),
        Check("screen_recording", sr, "화면 읽기 권한(화면 기록)",
              "목록에서 이 앱을 켜주세요. 스크린샷이 필요한 앱에서만 씁니다.",
              "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"),
    ]


def _screen_recording_for(c: "Check", helper: dict) -> "Check":
    """The screen-recording line, about the process that will actually capture.

    When the signed helper does the work, the grant that matters is the
    helper's, and only the helper can say whether it has it. Otherwise it is
    this interpreter's — named, because "enable this app" left people looking
    for an app called "this app".
    """
    from dataclasses import replace
    if helper.get("built"):
        return replace(c, ok=helper.get("screen_capture") is True,
                       title="화면 읽기 권한(화면 기록) — 'NLC Helper'",
                       fix_hint="목록에서 'NLC Helper' 를 켜주세요. 창 이미지가 필요한 기능에서만 씁니다.")
    import os, sys
    name = os.path.basename(os.path.realpath(sys.executable))
    return replace(c, fix_hint=f"목록에서 '{name}' 을(를) 켜주세요. 창 이미지가 필요한 기능에서만 씁니다.")


def request_accessibility(prompt: bool = True) -> bool:
    """Trigger the system's own Accessibility prompt (one-time)."""
    opts = {"AXTrustedCheckOptionPrompt": True} if prompt else {}
    return bool(AXIsProcessTrustedWithOptions(opts))


def open_settings(url: str) -> None:
    subprocess.run(["open", url], check=False)


def run(interactive: bool = True, narrate=print) -> bool:
    """Walk the user through any missing permission. Returns True when all pass.

    Accessibility is required; screen recording is optional (AX-tree first design
    means most tasks never need pixels), so a missing SR is reported, not blocked.
    """
    # When the signed helper holds the grant, this interpreter does not need it:
    # the whole point of the helper is that permission lives with a named,
    # single-purpose binary instead of a shared Python.
    helper = {}
    try:
        helper = helper_status()
    except Exception:
        helper = {}
    helper_covers = bool(helper.get("built") and helper.get("trusted"))

    all_ok = True
    for c in check_all():
        if c.key == "accessibility" and helper.get("built"):
            from dataclasses import replace
            c = replace(c, ok=helper_covers, title="앱 조작 권한 — 'NLC Helper'",
                        fix_hint="목록에서 'NLC Helper'를 켜주세요. 파이썬에는 권한이 필요 없습니다.")
        if c.key == "screen_recording":
            c = _screen_recording_for(c, helper)
        if c.ok:
            narrate(f"✓ {c.title} — 이미 켜져 있어요.")
            continue
        required = c.key == "accessibility" and not helper_covers
        mark = "✕" if required else "○"
        narrate(f"{mark} {c.title} — {c.fix_hint}")
        if interactive and required and not helper.get("built"):
            if c.key == "accessibility":
                request_accessibility(prompt=True)   # system dialog
            open_settings(c.settings_url)            # jump straight to the pane
        if required:
            all_ok = False
    # Over-broad grant check. TCC identifies a client by binary, and a venv's
    # python is usually a symlink to a SHARED interpreter — so granting NLC
    # accessibility grants it to every script run with that interpreter.
    # Verified on this machine: an unrelated /tmp script could read other apps'
    # windows. Users deserve to know that before they click Allow.
    try:
        import os as _os, sys as _sys
        real = _os.path.realpath(_sys.executable)
        shared = ("/uv/python/" in real or "/Cellar/" in real
                  or real.startswith("/usr/bin/") or "/Frameworks/Python" in real)
        if shared and not helper_covers:
            narrate(f"⚠ 권한이 공유 파이썬({_os.path.basename(real)})에 부여됩니다 — "
                    f"같은 인터프리터로 실행되는 다른 스크립트도 앱을 조작할 수 있어요. "
                    f"배포 시에는 서명된 전용 도우미로 분리할 예정입니다.")
    except Exception:
        pass

    # A GUI-session check catches the tmux/ssh "app looks not-running" trap
    # before the user hits it mid-task (real user report).
    try:
        from AppKit import NSWorkspace
        gui = [a for a in NSWorkspace.sharedWorkspace().runningApplications()
               if a.activationPolicy() == 0]
        if not gui:
            narrate("✕ 화면(GUI) 세션에 연결돼 있지 않아요 — tmux/스크린 밖에서 "
                    "실행하거나 `reattach-to-user-namespace` 를 쓰세요.")
            all_ok = False
        else:
            narrate(f"✓ 화면 세션 연결됨 (앱 {len(gui)}개 보임).")
    except Exception:
        pass

    # The helper is the path we want people on: a named, signed binary holding
    # the grant instead of a shared interpreter.
    try:
        st = helper_status()
        if st["built"]:
            narrate("— 권한 경계 —")
            if run_helper_onboarding(narrate, interactive=interactive):
                narrate("   실행:  tacto <명령> --helper   (설치 전이면 Tacto checkout에서 ./launcher/nlc)")
            else:
                all_ok = False
        else:
            narrate("○ 서명된 도우미를 빌드하면 권한을 파이썬 대신 도우미에만 "
                    "줄 수 있어요 (helper/build.sh && helper/bundle.sh).")
    except Exception:
        pass

    if not all_ok:
        narrate("설정에서 권한을 켜신 뒤, 이 앱을 한 번 다시 열어주세요.")
    else:
        narrate("준비 끝! 이제 무엇을 도와드릴까요?")
    return all_ok


if __name__ == "__main__":
    import sys
    ok = run(interactive="--check" not in sys.argv)
    sys.exit(0 if ok else 1)


# ---------------------------------------------------------------- helper path

def helper_status() -> dict:
    """Is the signed helper built, and does it hold the Accessibility grant?

    This is the permission story we actually want users to follow: grant the
    named, signed helper rather than a shared Python interpreter that every
    other script on the machine also uses.
    """
    import os
    from .helper import HELPER
    out = {"built": os.path.exists(HELPER), "path": HELPER, "trusted": None,
           "bundled": HELPER.endswith("Contents/MacOS/NLCHelper")}
    if not out["built"]:
        return out
    try:
        from .helper import HelperDriver, InheritedHelperDriver
        driver = None
        if InheritedHelperDriver.available():
            # Under the signed launcher, the channel it handed down is the
            # only one the helper answers. Dialling the helper directly, as
            # this did, is refused BY DESIGN (peer authentication) — so the
            # check reported "grant NLC Helper" to people whose helper was
            # granted and working, and told them to use the launcher they
            # were already using (2026-09-28).
            driver = InheritedHelperDriver()    # its handshake checks AX trust
            out["channel"] = "launcher"
        else:
            driver = HelperDriver()             # its ping reports AX trust
            driver.close()
            out["channel"] = "direct"
        out["trusted"] = True
        # Screen capture is the helper's grant too, and only it can report it.
        shake = getattr(driver, "handshake", None) or {}
        if isinstance(shake.get("screen_capture"), bool):
            out["screen_capture"] = shake["screen_capture"]
    except Exception as e:
        out["trusted"] = False
        out["why"] = str(e)
    return out


def run_helper_onboarding(narrate=print, *, interactive=True) -> bool:
    """Walk the user through granting the HELPER, not this interpreter."""
    st = helper_status()
    if not st["built"]:
        narrate("✕ 도우미가 아직 빌드되지 않았어요:")
        narrate("    helper/build.sh && helper/bundle.sh && launcher/build.sh")
        return False
    name = "NLC Helper" if st["bundled"] else os.path.basename(st["path"])
    if st["trusted"]:
        narrate(f"✓ '{name}' 에 권한이 있어요. 파이썬에는 권한이 필요 없습니다.")
        return True
    narrate(f"✕ '{name}' 에 앱 조작 권한이 필요해요.")
    narrate("   시스템 설정 > 개인정보 보호 및 보안 > 손쉬운 사용 에서 켜주세요.")
    narrate("   (macOS 27 에서는 같은 자리가 'Device Control and Data Access' 입니다.)")
    if st.get("why"):
        narrate(f"   ({st['why'].splitlines()[0]})")
    if interactive:
        open_settings("x-apple.systempreferences:com.apple.preference.security"
                      "?Privacy_Accessibility")
    return False
