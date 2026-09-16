"""Live E2E: deterministic, token-free replay of the calculator flow.

Waits for the screen to unlock (Codex and our AX path both need real window
content), then replays calc_12x34 with zero model calls and verifies 408.
"""

import subprocess
import sys
import time

sys.path.insert(0, __file__.rsplit("/", 2)[0])
from nlc.driver import Driver
from nlc.flow import calculator_12x34, replay


def screen_locked() -> bool:
    out = subprocess.run(["ioreg", "-n", "Root", "-d1", "-a"],
                         capture_output=True, text=True).stdout
    return "CGSSessionScreenIsLocked" in out and \
        out.split("CGSSessionScreenIsLocked", 1)[1].lstrip().startswith("</key>\n\t\t\t<true")


def main(wait_s=180):
    deadline = time.time() + wait_s
    while screen_locked():
        if time.time() > deadline:
            print("screen still locked after wait — unlock and re-run")
            sys.exit(2)
        print("  waiting for unlock…")
        time.sleep(5)

    d = Driver()
    flow = calculator_12x34()
    print(f"replaying flow {flow.name!r} — {len(flow.steps)} steps, 0 model tokens\n")
    r = replay(d, flow, narrate=lambda s: print(f"  … {s}"))
    print()
    for e in r.log:
        print(" ", e)
    print("\nRESULT:", "PASS — 408 verified" if r.ok else "FAIL")
    sys.exit(0 if r.ok else 1)


if __name__ == "__main__":
    main()
