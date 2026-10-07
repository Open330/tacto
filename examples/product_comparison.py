"""Same agent, same task, two computer-use layers: NLC versus OpenAI's Sky.

The agent is Codex in both arms, so the model, its reasoning and its account
are held fixed; only the layer it drives the Mac through differs:

  sky  — Codex's own Computer Use plugin (@oai/sky through node_repl `js`)
  nlc  — the NLC skill and the `nlc` command

Each task names an independent oracle — read by this harness after the run,
from the app or the file system — and never trusts what the agent says it did.
Focus is witnessed for the whole run. Tasks use apps and data that are not
personal (Calculator, TextEdit documents this harness creates, Finder on
/Applications, the macOS version); nothing reads mail, calendars or browsing.

    python examples/product_comparison.py --reps 1             # every task, both arms
    python examples/product_comparison.py --only calc_mult --arms nlc
    python examples/product_comparison.py --list

Each run is a fresh Codex session in /tmp and spends model tokens (measured
~100–200k input tokens per run, mostly cached). Results go to
reviews/<date>/product-comparison-<stamp>.jsonl, one line per run.
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "examples" / "probes"))

CALC = "com.apple.calculator"
NO_SHORTCUTS = ("Do not use a command-line calculator, Python, AppleScript/osascript, "
                "`open`, or any other automation than the one named; work through the app.")

ARM_CLAUSE = {
    "sky": ("Use the Computer Use plugin: the `js` tool of the node_repl MCP server with "
            "the @oai/sky interface (search your deferred tools for it if it is not listed). "
            "Do not use the `nlc` command."),
    "nlc": ("Use the nlc skill and the `nlc` command to drive the app. "
            "Do not use the Computer Use plugin or node_repl."),
}


# ---------------------------------------------------------------- app state

def _driver():
    from nlc.driver import Driver
    return Driver()


def _tree(target):
    o = _driver().observe(target, "tree_full", require_window=False)
    return o.get("tree", "") if o.get("type") == "observation" else ""


def _calc_display():
    from nlc.flow import Selector
    line = next((l for l in _tree(CALC).splitlines() if "Edit field" in l), "")
    return (Selector._attr(line, "Value") or "").replace(",", "").strip("‎ ")


def _calc_mode():
    m = re.search(r"Identifier: Mode: (\w+)", _tree(CALC))
    return m.group(1) if m else None


def calc_reset(mode="basic"):
    """Basic (or given) mode, cleared display — the same start for both arms."""
    from nlc.flow import Selector
    d = _driver()
    o = d.observe(CALC, "tree_full", require_window=False)
    title = {"basic": ["Basic", "기본"], "scientific": ["Scientific", "공학용"]}[mode]
    d.act(o["obs_id"], CALC, {"name": "menu", "path": ["View", title]})
    time.sleep(0.5)
    for _ in range(3):
        o = d.observe(CALC, "tree_full", require_window=False)
        try:
            i, _ = Selector(role="Button", identifier=["AllClear", "Clear"],
                            ancestor="CalculatorKeypadView").resolve(o["tree"])
        except LookupError:
            break
        d.act(o["obs_id"], CALC, {"name": "click", "element_index": i})
        time.sleep(0.2)


def _running(bundle):
    from AppKit import NSRunningApplication
    return bool(NSRunningApplication.runningApplicationsWithBundleIdentifier_(bundle))


_TEXTEDIT_BEFORE: list = []     # TextEdit windows that existed when the task was set up
_TEXTEDIT_PROCESS: list = []    # (pid, launch date) of TextEdit at setup


def _textedit_process():
    from AppKit import NSRunningApplication
    apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_("com.apple.TextEdit")
    if len(apps) != 1:
        return None, None
    app = apps[0]
    return app, (app.processIdentifier(), str(app.launchDate()))


def _textedit_windows():
    """TextEdit's windows by AX — the windows only, never what they contain."""
    from ApplicationServices import AXUIElementCreateApplication
    from nlc.driver import _attr, _unhide_quietly
    app, _identity = _textedit_process()
    if app is None:
        return []
    if app.isHidden():
        _unhide_quietly(app.processIdentifier())
    return list(_attr(AXUIElementCreateApplication(app.processIdentifier()), "AXWindows") or [])


def _new_textedit_windows():
    """THE window this run created — or nothing, when that is not certain.

    The first checker read the text of EVERY open TextEdit document and deleted
    any whose text matched; the second read every window that appeared during
    the run. Appearing during the run is not ownership: a person can open a
    document meanwhile, and a restarted TextEdit can reuse window identities
    (Codex review, 2026-10-07). So: the same TextEdit process as at setup, and
    exactly one new window — otherwise nothing is read and nothing is closed.
    """
    from CoreFoundation import CFEqual
    _app, identity = _textedit_process()
    if identity is None or not _TEXTEDIT_PROCESS or identity != _TEXTEDIT_PROCESS[0]:
        return []
    new = [w for w in _textedit_windows()
           if not any(CFEqual(w, old) for old in _TEXTEDIT_BEFORE)]
    return new if len(new) == 1 else []


def _text_of(window):
    from nlc.driver import _attr

    def find(el, depth=0):
        if depth > 6:
            return None
        if _attr(el, "AXRole") == "AXTextArea":
            return el
        for child in _attr(el, "AXChildren") or []:
            hit = find(child, depth + 1)
            if hit is not None:
                return hit
        return None

    area = find(window)
    return None if area is None else str(_attr(area, "AXValue") or "")


def textedit_baseline():
    """Remember TextEdit's process and its open windows; read none of them.

    If TextEdit is not running the agent will start it — a new process — so
    the baseline is taken against nothing, and no window can be claimed.
    """
    _app, identity = _textedit_process()
    if identity is None:
        # Started here, in the background (-g: not made frontmost), so the
        # process the agent works in is the one the baseline describes.
        subprocess.run(["open", "-g", "-a", "TextEdit"], capture_output=True, timeout=20)
        for _ in range(50):
            _app, identity = _textedit_process()
            if identity is not None:
                break
            time.sleep(0.1)
        time.sleep(1.0)
    _TEXTEDIT_PROCESS[:] = [identity] if identity else []
    _TEXTEDIT_BEFORE[:] = _textedit_windows()


def textedit_texts():
    """The text of the documents this run created — nothing else."""
    return [t for t in (_text_of(w) for w in _new_textedit_windows()) if t is not None]


def textedit_has(text):
    want = " ".join(text.split())
    return any(" ".join(t.split()) == want for t in textedit_texts())


def textedit_close(text):
    """Close the window this run created, if it holds our text.

    TextEdit autosaves a new document to iCloud Drive at once, so closing asks
    "Do you want to keep this new document?"; Delete is pressed for those. A
    new window holding anything else — something a person typed during the run
    — is left open, and counted. Returns how many new windows were left.
    """
    from ApplicationServices import AXUIElementPerformAction
    from nlc.driver import _attr

    def button(el, title, depth=0):
        if depth > 5 or el is None:
            return None
        if _attr(el, "AXRole") == "AXButton" and _attr(el, "AXTitle") == title:
            return el
        for child in _attr(el, "AXChildren") or []:
            hit = button(child, title, depth + 1)
            if hit is not None:
                return hit
        return None

    want = " ".join(text.split())
    left = 0
    for window in _new_textedit_windows():
        body = _text_of(window)
        if body is None or " ".join(body.split()) != want:
            left += 1
            continue
        close = _attr(window, "AXCloseButton")
        if close is None:
            left += 1
            continue
        AXUIElementPerformAction(close, "AXPress")
        for _ in range(20):
            sheet = next((c for c in _attr(window, "AXChildren") or []
                          if _attr(c, "AXRole") == "AXSheet"), None)
            if sheet is not None:
                delete = button(sheet, "Delete")
                if delete is not None:
                    AXUIElementPerformAction(delete, "AXPress")
                break
            time.sleep(0.1)
    time.sleep(0.5)
    return left


def textedit_hidden():
    from AppKit import NSRunningApplication
    apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_("com.apple.TextEdit")
    return bool(apps and apps[0].isHidden())


def _locked():
    import Quartz
    return bool((Quartz.CGSessionCopyCurrentDictionary() or {}).get("CGSSessionScreenIsLocked"))


# ---------------------------------------------------------------- tasks

@dataclass
class Task:
    name: str
    ask: str
    oracle: object              # (answer) -> (passed, observed)
    setup: object = None
    cleanup: object = None
    notes: str = ""


def _number_in(answer, want):
    return bool(re.search(rf"(?<![\d.]){re.escape(want)}(?![\d])", answer.replace(",", "")))


def _apps_count():
    return str(len([p for p in Path("/Applications").iterdir() if not p.name.startswith(".")]))


def _macos_version():
    return subprocess.run(["sw_vers", "-productVersion"], capture_output=True,
                          text=True).stdout.strip()


TEXT = "NLC 비교 테스트 7781"
FOLDER = Path("/tmp/nlc-compare-folder")
FOLDER_ITEMS = 7


def _make_folder():
    import shutil
    shutil.rmtree(FOLDER, ignore_errors=True)
    FOLDER.mkdir()
    for i in range(FOLDER_ITEMS):
        (FOLDER / f"item-{i + 1}.txt").write_text(str(i))


def source_manifest():
    """Explicit source/artifact set, not a dependency-closure claim.

    Both arms record the same NLC state. Missing signed artifacts are recorded,
    not silently omitted; package/fixture changes and binary rebuilds matter.
    """
    import hashlib
    fixed = ["skill/SKILL.md", "launcher/nlc", "launcher/nlc.swift",
             "helper/nlc-helper", "helper/NLC Helper.app/Contents/MacOS/NLCHelper",
             "helper/NLC Helper.app/Contents/Info.plist", "signing.sh",
             "pyproject.toml", "uv.lock"]
    paths = {ROOT / name for name in fixed}
    for directory, pattern in (("nlc", "**/*.py"), ("helper", "*.swift"),
                               ("examples", "*.py"), ("examples/probes", "*.py"),
                               ("tests/fixtures", "*.swift"), ("flows", "*.json")):
        paths.update((ROOT / directory).glob(pattern))
    files = {path.relative_to(ROOT).as_posix():
             hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
             for path in sorted(paths)}
    digest = hashlib.sha256(json.dumps(files, sort_keys=True,
                                      separators=(",", ":")).encode()).hexdigest()
    return {"version": 2, "sha256": digest, "files": files}


def source_hash():
    """Legacy short identifier; new rows also retain the complete manifest."""
    return source_manifest()["sha256"][:12]


def _source_evidence(before, after):
    return {"source": before["sha256"][:12], "source_end": after["sha256"][:12],
            "source_changed": before != after,
            "source_manifest_before": before, "source_manifest_after": after}

TASKS = [
    Task("calc_mult", "In the macOS Calculator app, compute 12 × 34 and report the result.",
         lambda a: ((_calc_display() == "408"), _calc_display()),
         setup=calc_reset),
    Task("calc_sqrt", "In the macOS Calculator app, compute the square root of 144 and report it.",
         lambda a: ((_calc_display() == "12"), _calc_display()),
         setup=calc_reset, cleanup=calc_reset,
         notes="needs Scientific mode, which starts off"),
    Task("calc_chain", "In the macOS Calculator app, compute (7 + 8) × 6 − 10 and report the result.",
         lambda a: ((_calc_display() == "80"), _calc_display()),
         setup=calc_reset),
    Task("calc_mode", "Switch the macOS Calculator app to Scientific mode.",
         lambda a: ((_calc_mode() == "scientific"), _calc_mode()),
         setup=calc_reset, cleanup=calc_reset),
    Task("textedit_write", f"Create a new TextEdit document and type exactly this text into it: {TEXT}  "
                           "Leave the document open and do not save it.",
         lambda a: (textedit_has(TEXT), [t[:60] for t in textedit_texts()]),
         setup=textedit_baseline, cleanup=lambda: textedit_close(TEXT)),
    # A folder the harness makes, so the right count is known. The first
    # version used /Applications, which Finder merges with
    # /System/Applications: both arms read Finder's "101 items" correctly and
    # the oracle, counting one folder, said they were wrong.
    Task("finder_count", f"Using Finder, open the folder {FOLDER} and report how many items it contains.",
         lambda a: (_number_in(a, str(FOLDER_ITEMS)), f"want {FOLDER_ITEMS}"),
         setup=lambda: _make_folder(), notes="harness-made folder"),
    Task("macos_version", "Using the System Settings app, find this Mac's macOS version number "
                          "and report it.",
         lambda a: (_macos_version() in a, f"want {_macos_version()}")),
]


# ---------------------------------------------------------------- one run

def run_one(task: Task, arm: str, timeout: int = 600) -> dict:
    from focus_witness import FocusWitness
    source_before = source_manifest()
    if task.setup:
        try:
            task.setup()
        except Exception as exc:
            return {"task": task.name, "arm": arm, "status": "not_run",
                    "observed": f"setup failed: {type(exc).__name__}", "seconds": 0,
                    "answer": "", "calls": {}, "usage": None, "activations": [],
                    **_source_evidence(source_before, source_manifest())}
    locked_before = _locked()
    hidden_before = textedit_hidden() if task.name.startswith("textedit") else None
    prompt = f"{task.ask}\n\n{ARM_CLAUSE[arm]} {NO_SHORTCUTS}"
    with tempfile.NamedTemporaryFile("r", suffix=".txt") as last, FocusWitness() as witness:
        started = time.monotonic()
        try:
            proc = subprocess.run(
                ["codex", "exec", "--skip-git-repo-check", "--sandbox", "danger-full-access",
                 "-C", "/tmp", "--json", "-o", last.name, prompt],
                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout,
                cwd="/tmp")
            stdout, timed_out = proc.stdout, False
        except subprocess.TimeoutExpired as exc:
            stdout, timed_out = (exc.stdout or b"").decode(errors="replace") if isinstance(
                exc.stdout, bytes) else (exc.stdout or ""), True
        seconds = time.monotonic() - started
        answer = Path(last.name).read_text(errors="replace").strip()
    calls, usage, layer_violation, shortcut = {}, None, False, False
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        item = event.get("item") or {}
        if item.get("type") == "mcp_tool_call":
            key = f"{item.get('server')}:{item.get('tool')}"
            calls[key] = calls.get(key, 0) + 1
            if arm == "nlc" and item.get("server") in ("node_repl", "cua_repl"):
                layer_violation = True
        elif item.get("type") == "command_execution":
            cmd = item.get("command", "")
            if re.search(r"(^|[\s'\"/])nlc\s", cmd):
                calls["shell:nlc"] = calls.get("shell:nlc", 0) + 1
                if arm == "sky":
                    layer_violation = True
            else:
                calls["shell:other"] = calls.get("shell:other", 0) + 1
            if re.search(r"osascript|\bbc\b|python3? -c|\bopen -a|System Events", cmd):
                shortcut = True
        if event.get("type") == "turn.completed":
            usage = event.get("usage")
    passed, observed = (False, "not checked")
    if answer or not timed_out:
        try:
            passed, observed = task.oracle(answer)
        except Exception as exc:
            passed, observed = False, f"oracle error: {type(exc).__name__}"
    provider_down = (not answer and re.search(r"usage limit|rate limit|not logged in",
                                              stdout, re.I))
    process_exit = None if timed_out else proc.returncode
    status = ("not_run" if provider_down else "timeout" if timed_out
              else "invalid" if (layer_violation or shortcut)
              else "failed" if process_exit != 0
              else "passed" if passed else "failed")
    result = {"task": task.name, "arm": arm, "status": status, "seconds": round(seconds, 1),
              "observed": observed, "answer": answer[-400:], "calls": calls, "usage": usage,
              "activations": witness.summary()["activations"],
              "layer_violation": layer_violation, "shortcut": shortcut,
              "process_exit": process_exit, "oracle_passed": bool(passed),
              # A lock during the run is the environment, not either arm; such
              # a run is reported, and excluded from the comparison.
              "locked": [locked_before, _locked()],
              "textedit_hidden_before": hidden_before}
    if any(result["locked"]) and status in ("failed", "timeout"):
        result["status"] = "not_run"
    result["cleanup_status"] = "not_requested"
    if task.cleanup:
        try:
            task.cleanup()
            result["cleanup_status"] = "completed"
        except Exception as error:
            result["cleanup_status"] = "failed"
            result["cleanup_error"] = type(error).__name__[:80]
    result.update(_source_evidence(source_before, source_manifest()))
    if result["source_changed"]:
        # A successful oracle cannot certify a comparison under drifting code.
        result["observed_status"] = result["status"]
        result["status"] = "invalid"
        result["invalid_reason"] = "source_changed"
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--arms", nargs="+", default=["nlc", "sky"], choices=["nlc", "sky"])
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()
    tasks = [t for t in TASKS if not args.only or t.name in args.only]
    if args.list:
        for t in tasks:
            print(f"{t.name:16} {t.ask}")
        return 0
    import Quartz
    if (Quartz.CGSessionCopyCurrentDictionary() or {}).get("CGSSessionScreenIsLocked"):
        print("화면이 잠겨 있습니다 — 잠금을 풀고 다시 실행하세요")
        return 2
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = ROOT / "reviews" / datetime.date.today().isoformat() / f"product-comparison-{stamp}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    print(f"results: {out}")
    for rep in range(args.reps):
        for task in tasks:
            # Alternate which arm goes first, so neither always meets the
            # state the other left behind.
            arms = args.arms if (rep + TASKS.index(task)) % 2 == 0 else list(reversed(args.arms))
            for arm in arms:
                r = run_one(task, arm)
                r["rep"] = rep
                with out.open("a") as f:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
                tok = (r["usage"] or {}).get("input_tokens")
                print(f"  {r['status']:8} {arm:3} {task.name:15} {r['seconds']:6.1f}s "
                      f"tok={tok} act={len(r['activations'])} obs={str(r['observed'])[:40]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
