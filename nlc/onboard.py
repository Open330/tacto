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
        Check("accessibility", ax, "앱 조작 권한(손쉬운 사용)",
              "목록에서 이 앱을 켜주세요. 그러면 버튼을 대신 눌러드릴 수 있어요.",
              "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"),
        Check("screen_recording", sr, "화면 읽기 권한(화면 기록)",
              "목록에서 이 앱을 켜주세요. 스크린샷이 필요한 앱에서만 씁니다.",
              "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"),
    ]


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
    all_ok = True
    for c in check_all():
        if c.ok:
            narrate(f"✓ {c.title} — 이미 켜져 있어요.")
            continue
        required = c.key == "accessibility"
        mark = "✕" if required else "○"
        narrate(f"{mark} {c.title} — {c.fix_hint}")
        if interactive:
            if c.key == "accessibility":
                request_accessibility(prompt=True)   # system dialog
            open_settings(c.settings_url)            # jump straight to the pane
        if required:
            all_ok = False
    if not all_ok:
        narrate("설정에서 권한을 켜신 뒤, 이 앱을 한 번 다시 열어주세요.")
    else:
        narrate("준비 끝! 이제 무엇을 도와드릴까요?")
    return all_ok


if __name__ == "__main__":
    import sys
    ok = run(interactive="--check" not in sys.argv)
    sys.exit(0 if ok else 1)
