"""Agent acceptance: does NLC work for an agent that only has the skill?

Every problem reported so far was invisible to the unit tests and the eval
suite, because both run NLC from inside this checkout: the `tacto` command was
never installed (2026-09-28), a sandboxed agent was told to grant a permission
it already had, and an agent reading Safari on a locked screen had to guess
why it saw nothing. Each was found by an agent that had nothing but the skill.

So this starts fresh Codex sessions — no memory, no checkout, working in /tmp —
and gives each a task. It judges the outcome from what the agent reports and
what NLC printed. It spends model tokens (roughly 10–20k per case).

    python examples/agent_acceptance.py            # every case the machine's state allows
    python examples/agent_acceptance.py --list     # what would run, and why not

Cases that need the screen unlocked (or locked) run only in that state and say
so otherwise; nothing is counted as passed that did not run.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@dataclass
class Case:
    name: str
    sandbox: str            # workspace-write | danger-full-access
    needs: str              # any | unlocked | locked
    task: str
    passes: str             # regex the agent's final message must contain
    fails: str = ""         # regex it must NOT contain
    agents: tuple = ("codex", "claude")


CASES = [
    Case("finds_nlc", "workspace-write", "any",
         "Use the tacto skill. Check whether the tacto command is installed and report its path. "
         "Do not install anything.",
         passes=r"\.local/bin/tacto"),
    Case("names_the_sandbox", "workspace-write", "any",
         "Use the tacto skill. Run `tacto apps` and report what happened and why.",
         passes=r"샌드박스|sandbox",
         fails=r"(grant|enable|켜)[^\n]{0,40}(Accessibility|손쉬운 사용)",
         agents=("codex",)),       # the seatbelt sandbox is Codex's
    Case("names_the_lock", "danger-full-access", "locked",
         "Use the tacto skill. Read the title of the frontmost Safari window with NLC and report it. "
         "If you cannot, say exactly why.",
         passes=r"잠겨|잠금|locked",
         fails=r"(grant|enable|켜)[^\n]{0,40}(Accessibility|손쉬운 사용)"),
    Case("does_a_task", "danger-full-access", "unlocked",
         "Use the tacto skill. With NLC, compute 12 times 12 in the Calculator app and report the "
         "result NLC verified.",
         passes=r"\b144\b"),
]


def locked() -> bool:
    import Quartz
    d = Quartz.CGSessionCopyCurrentDictionary() or {}
    return bool(d.get("CGSSessionScreenIsLocked"))


def runnable(case: Case, is_locked: bool) -> str | None:
    """None if it can run now, else why not."""
    if case.needs == "unlocked" and is_locked:
        return "화면이 잠겨 있음 — 잠금을 풀고 다시 실행"
    if case.needs == "locked" and not is_locked:
        return "화면이 잠겨 있지 않음 — 잠긴 상태에서만 확인 가능"
    return None


# The provider could not run the agent at all. That is not a result about NLC,
# so it is reported as such — never as a pass or a fail.
PROVIDER_DOWN = re.compile(r"usage limit|weekly limit|rate limit|not logged in|"
                           r"please run /login|stream disconnected|429", re.I)


def command(agent: str, case: Case, last: str) -> list:
    if agent == "claude":
        # Claude Code, non-interactive, allowed to use a shell and files the
        # way an agent following the skill would. Its stdout IS the final message.
        return ["claude", "-p", case.task, "--output-format", "text",
                "--allowedTools", "Bash", "Read", "Write", "Edit"]
    return ["codex", "exec", "--skip-git-repo-check", "--sandbox", case.sandbox, "-C", "/tmp",
            "-o", last, case.task]


def run(case: Case, agent: str = "codex", timeout: int = 420) -> tuple[str, str]:
    """(passed | failed | not_run, summary).

    Judged on the agent's FINAL MESSAGE only. The first version matched the
    whole transcript, which contains Codex's own header ("sandbox:
    workspace-write") and the task text — so a case "passed" in a run where
    the agent never started (2026-09-28).
    """
    import tempfile
    started = time.monotonic()
    with tempfile.NamedTemporaryFile("r", suffix=".txt") as last:
        # The same environment NLC gives its own provider calls: a shell's
        # leftovers made `claude -p` answer "Not logged in" while logged in.
        from tacto.authoring import _provider_env
        proc = subprocess.run(command(agent, case, last.name), stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=timeout, cwd="/tmp",
                              env=_provider_env())
        answer = (proc.stdout if agent == "claude"
                  else Path(last.name).read_text(errors="replace")).strip()
        if agent == "claude" and (proc.returncode != 0 or PROVIDER_DOWN.search(answer[:300])):
            answer = "" if PROVIDER_DOWN.search(proc.stdout + proc.stderr) else answer
    took = f"{time.monotonic() - started:.0f}s"
    if not answer:
        noise = proc.stdout + proc.stderr
        down = PROVIDER_DOWN.search(noise)
        return "not_run", f"{took} | " + (f"provider: {down.group()}" if down
                                          else "no final message from the agent")
    ok = bool(re.search(case.passes, answer)) and not (
        case.fails and re.search(case.fails, answer, re.I))
    return ("passed" if ok else "failed"), f"{took} | " + " ".join(answer.split())[:300]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--agent", choices=("codex", "claude"), default="codex",
                    help="which agent to drive NLC with (both read the same skill)")
    ap.add_argument("only", nargs="*")
    args = ap.parse_args()
    is_locked = locked()
    print(f"agent: {args.agent} | screen locked: {is_locked}")
    results = {}
    for case in CASES:
        if args.only and case.name not in args.only:
            continue
        why_not = runnable(case, is_locked)
        if args.agent not in case.agents:
            why_not = f"{args.agent} 에는 해당 없음"
        if why_not or args.list:
            print(f"  {'SKIP' if why_not else 'WOULD RUN'}  {case.name}  {why_not or ''}")
            results[case.name] = "skipped" if why_not else "listed"
            continue
        outcome, summary = run(case, args.agent)
        label = {"passed": "PASS", "failed": "FAIL", "not_run": "NOT RUN"}[outcome]
        print(f"  {label}  {case.name}  ({summary})")
        results[case.name] = outcome
    ran = [v for v in results.values() if v in ("passed", "failed")]
    print(json.dumps(results, ensure_ascii=False))
    if args.list:
        return 0
    # Nothing judged is not a pass: exit 2, as the eval suite does.
    return 1 if "failed" in ran else (0 if ran else 2)


if __name__ == "__main__":
    raise SystemExit(main())
