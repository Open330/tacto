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


def _textedit_process():
    from AppKit import NSRunningApplication
    apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_("com.apple.TextEdit")
    if len(apps) != 1:
        return None, None
    app = apps[0]
    return app, (app.processIdentifier(), str(app.launchDate()))


def _observed_windows():
    """(process identity, windows) from one successful AX read, else None.

    Unlike `_textedit_windows`, an absent process or a failed read is None —
    never an empty list — so "the document is gone" can only come from a
    successful look at the same process (Codex review of 35e6d2e).
    """
    from ApplicationServices import AXUIElementCopyAttributeValue, AXUIElementCreateApplication
    app, identity = _textedit_process()
    if app is None:
        return None
    error, windows = AXUIElementCopyAttributeValue(
        AXUIElementCreateApplication(app.processIdentifier()), "AXWindows", None)
    if error != 0 or windows is None:
        return None
    # The read is about the process named before it; one that quit or was
    # replaced during the read answered for someone else (Codex review).
    if _textedit_process()[1] != identity:
        return None
    return identity, list(windows)


_AX_NO_VALUE = -25212


def _document_read(window):
    """One AXDocument read of the window: ("path", local path or None for a
    non-local URL), ("none", None) for a window with no document (untitled),
    or ("error", None). Every decision about a window uses ONE read: a second,
    separate read could answer differently (Codex review of 6c2c6a9)."""
    from ApplicationServices import AXUIElementCopyAttributeValue
    error, doc = AXUIElementCopyAttributeValue(window, "AXDocument", None)
    if error == _AX_NO_VALUE:
        return "none", None
    if error != 0 or not isinstance(doc, str):
        return "error", None
    return "path", _local_path(doc)


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


def _owned_path():
    return _nfc(Path(_OWNED["path"])) if _OWNED.get("path") else None


def _window_path(window):
    """The window's document as a local file path, or None."""
    from nlc.driver import _attr
    doc = _attr(window, "AXDocument")
    return _local_path(doc) if isinstance(doc, str) else None


def _local_path(doc):
    """Only `file://` URLs with no host (or localhost), no query and no
    fragment count; anything else is not a local file this harness could own."""
    from urllib.parse import unquote, urlparse
    url = urlparse(doc)
    if (url.scheme != "file" or url.netloc not in ("", "localhost")
            or url.query or url.fragment or url.params):
        return None
    return _nfc(Path(unquote(url.path)).resolve())


def _nfc(path):
    """TextEdit reports a Korean file name decomposed (NFD); compare composed."""
    import unicodedata
    return Path(unicodedata.normalize("NFC", str(path)))


def owned_window():
    """The harness's own document window, re-proved on every call, or None.

    Same TextEdit process (pid and launch date) as when it was opened, the
    same AX window, and that window still shows the harness's file. Nothing
    else in TextEdit is looked at beyond its document path.
    """
    from CoreFoundation import CFEqual
    _app, identity = _textedit_process()
    if not _OWNED or identity != _OWNED.get("process"):
        return None
    matches = [w for w in _textedit_windows() if _window_path(w) == _owned_path()]
    if len(matches) != 1 or not CFEqual(matches[0], _OWNED["window"]):
        return None
    return matches[0]


def _owned_file_is_ours():
    """The private directory is still the one mkdtemp made, holding only our file.

    Not the file's inode: TextEdit saves by writing a new file and swapping it
    in, so the inode changes on every save (measured 2026-10-07). The 0700
    directory is created by mkdtemp and nothing else writes into it.
    """
    import os
    try:
        st = os.stat(_OWNED["directory"])
        names = os.listdir(_OWNED["directory"])
    except (KeyError, OSError):
        return False
    return ((st.st_dev, st.st_ino) == _OWNED.get("inode")
            and set(names) <= {OWNED_NAME})


def owned_setup():
    """Create the document in a new private directory and open it behind.

    No existing path is removed or reused: every run gets its own directory
    from mkdtemp, so there is nothing to clear and nothing to mistake for ours.
    """
    import os
    import tempfile
    _OWNED.clear()
    directory = tempfile.mkdtemp(prefix="nlc-compare-owned-")       # 0700, unique
    # Real path: mkdtemp answers /var/…, TextEdit reports /private/var/….
    directory = os.path.realpath(directory)
    path = os.path.join(directory, OWNED_NAME)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)  # never an existing file
    os.close(fd)
    st = os.stat(directory)
    _OWNED.update(directory=directory, path=path, inode=(st.st_dev, st.st_ino))
    # -g: opened behind whatever the person is using, not made frontmost.
    subprocess.run(["open", "-g", "-a", "TextEdit", path], capture_output=True, timeout=20)
    for _ in range(100):
        _app, identity = _textedit_process()
        found = [w for w in _textedit_windows() if _window_path(w) == _owned_path()]
        if identity is not None and len(found) == 1:
            _OWNED.update(process=identity, window=found[0])
            return
        time.sleep(0.1)
    raise RuntimeError("owned document did not open")


def owned_oracle(text):
    window = owned_window()
    if window is None:
        return False, "owned document not provable"
    body = _text_of(window) or ""
    return " ".join(body.split()) == " ".join(text.split()), body[:60]


def owned_cleanup():
    """Close the harness's document and remove its directory — or neither.

    Every press is preceded by proving the window is still the harness's
    (same process, one window on the owned path, the same AX window), and the
    directory is removed only once the same TextEdit process, read
    successfully, shows no window on that path, and the directory is still the
    one created at setup holding only our file. Identity checks are repeated,
    not atomic with the OS: they narrow the window for a mistake, not close it. Anything unproven is left as it
    is and reported. Returns "closed", or the reason nothing was removed.
    """
    import shutil
    from ApplicationServices import AXUIElementPerformAction
    from nlc.driver import _attr
    if not _OWNED.get("path"):
        return "nothing_owned"
    window = owned_window()
    if window is None:
        return "not_provable_left_as_is"
    close = _attr(window, "AXCloseButton")
    if close is None or owned_window() is None:
        return "not_provable_left_as_is"
    before = _OWNED.get("process")
    if AXUIElementPerformAction(close, "AXPress") != 0:
        return "close_failed_left_as_is"
    for _ in range(20):
        time.sleep(0.1)
        window = owned_window()
        if window is None:
            break
        sheet = next((c for c in _attr(window, "AXChildren") or []
                      if _attr(c, "AXRole") == "AXSheet"), None)
        if sheet is None:
            continue
        # The question is about the harness's own, still-proven document.
        for title in ("Don’t Save", "Don't Save", "Revert Changes", "Delete"):
            hit = _find_button(sheet, title)
            if hit is not None and owned_window() is not None:
                if AXUIElementPerformAction(hit, "AXPress") != 0:
                    return "sheet_press_failed_left_as_is"
                break
    # Gone means: the SAME TextEdit process, read successfully, shows no window
    # on that path. A quit, a restart, or a failed read proves nothing.
    from CoreFoundation import CFEqual
    seen = _observed_windows()
    if seen is None or seen[0] != before:
        return "not_observable_after_close_left_as_is"
    # Still listed — the same AX window — is open, whatever its path reads as;
    # and a window whose document cannot be read could be ours (Codex review
    # of 7bd61f1: a close that "succeeded", then a failed AXDocument read,
    # reached rmtree).
    reads = [(w, _document_read(w)) for w in seen[1]]
    if any(CFEqual(w, _OWNED["window"]) or read == ("path", _owned_path())
           for w, read in reads):
        return "still_open_left_as_is"
    if any(read[0] == "error" for _w, read in reads):
        return "not_observable_after_close_left_as_is"
    if not _owned_file_is_ours():
        return "file_changed_left_as_is"
    shutil.rmtree(_OWNED["directory"])
    _OWNED.clear()
    return "closed"


def _find_button(el, title, depth=0):
    from nlc.driver import _attr
    if depth > 5 or el is None:
        return None
    if _attr(el, "AXRole") == "AXButton" and _attr(el, "AXTitle") == title:
        return el
    for child in _attr(el, "AXChildren") or []:
        hit = _find_button(child, title, depth + 1)
        if hit is not None:
            return hit
    return None


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
    ask: object                 # text, or () -> text when it names a per-run path
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
OWNED_NAME = "NLC 비교 문서.txt"
# The harness's own document: a fresh 0700 directory per run (mkdtemp), the
# file created exclusively, the directory's inode remembered; plus the TextEdit process
# and AX window that showed it. Nothing is assumed from a fixed path.
_OWNED: dict = {}
FOLDER_ITEMS = 7
FOLDER_PREFIX = "nlc-compare-folder-"
# The folder task's own directory: a fresh mkdtemp per run, so its name (the
# only thing a Finder window shows) is unique to the run, and no fixed path is
# ever removed. The first version reused /tmp/nlc-compare-folder and removed it
# before checking anything (Codex review of 1bf10aa).
_FOLDER: dict = {}


def _folder_names():
    return {f"item-{i + 1}.txt" for i in range(FOLDER_ITEMS)}


def _make_folder():
    import os
    import tempfile
    _FOLDER.clear()
    directory = os.path.realpath(tempfile.mkdtemp(prefix=FOLDER_PREFIX, dir="/tmp"))
    for name in sorted(_folder_names()):
        fd = os.open(os.path.join(directory, name), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
    st = os.stat(directory)
    _FOLDER.update(path=directory, inode=(st.st_dev, st.st_ino))


def _folder_ask():
    return (f"Using Finder, open the folder {_FOLDER.get('path', '(not created)')} "
            "and report how many items it contains.")


def _ax(element, name):
    """(ok, value): a failed read is not a missing value."""
    from ApplicationServices import AXUIElementCopyAttributeValue
    error, value = AXUIElementCopyAttributeValue(element, name, None)
    if error == _AX_NO_VALUE:
        return True, None
    return error == 0, value if error == 0 else None


def _find_identified(el, identifier, depth=0):
    from nlc.driver import _attr
    if el is None or depth > 8:
        return None
    if _attr(el, "AXIdentifier") == identifier:
        return el
    for child in _attr(el, "AXChildren") or []:
        hit = _find_identified(child, identifier, depth + 1)
        if hit is not None:
            return hit
    return None


def _finder_windows():
    """Finder's AX windows, or None when Finder or the read is missing."""
    from AppKit import NSRunningApplication
    from ApplicationServices import AXUIElementCreateApplication
    apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_("com.apple.finder")
    if len(apps) != 1:
        return None
    ok, windows = _ax(AXUIElementCreateApplication(apps[0].processIdentifier()), "AXWindows")
    if not ok:
        return None
    return list(windows or [])


def finder_state():
    """Windows titled with this run's folder name, and Go to Folder sheets —
    or None when any title or child list could not be read. A failed read
    is never folded into "nothing there" (Codex review of 1bf10aa).

    Finder gives no AXDocument for its windows, so a title is evidence that a
    window shows a folder of that NAME, not proof of its path. The name is a
    fresh mkdtemp one, which is what makes the title worth something.
    """
    windows = _finder_windows()
    if windows is None or not _FOLDER.get("path"):
        return None
    name = Path(_FOLDER["path"]).name
    shown, sheets = [], []
    for window in windows:
        ok_title, title = _ax(window, "AXTitle")
        ok_kids, kids = _ax(window, "AXChildren")
        if not (ok_title and ok_kids):
            return None
        if title == name:
            shown.append(window)
        for kid in kids or []:
            ok_id, ident = _ax(kid, "AXIdentifier")
            if not ok_id:
                return None
            if ident == "GoToWindow":
                sheets.append(kid)
    return {"shown": shown, "sheets": sheets}


def finder_setup():
    """A fresh folder no Finder window can already be showing; the run's
    starting state (open Go to Folder sheets) is recorded, not tidied."""
    _make_folder()
    state = finder_state()
    problem = ("finder_unobservable" if state is None else
               "finder_not_clean: a window already shows the new folder's name"
               if state["shown"] else None)
    if problem:
        # run_one returns not_run without calling cleanup, and the next
        # _make_folder forgets this path — so the folder just made is
        # removed here, by the same proof cleanup uses (Codex follow-up).
        import shutil
        if _folder_is_ours():
            shutil.rmtree(_FOLDER["path"])
            _FOLDER.clear()
            raise RuntimeError(f"{problem}; own folder removed")
        raise RuntimeError(f"{problem}; own folder changed, left at {_FOLDER.get('path')}")
    _FOLDER["sheets_before"] = len(state["sheets"])


def finder_oracle(answer):
    """The answer names the count AND a Finder window titled with this run's
    folder name shows that count in its status bar."""
    from nlc.driver import _attr
    state = finder_state() or {"shown": [], "sheets": []}
    statuses = [_attr(_find_identified(w, "statusBarText"), "AXValue") or ""
                for w in state["shown"]]
    at_folder = any(re.match(rf"{FOLDER_ITEMS} items?\b", s) for s in statuses)
    said = _number_in(answer, str(FOLDER_ITEMS))
    # The evidence kept with the verdict (Codex review of ff2f20b): how many
    # windows carry this run's unique folder name, and what each one's status
    # bar said — a title alone is a NAME, not a path.
    return (said and at_folder,
            f"want {FOLDER_ITEMS}; answer={'yes' if said else 'no'} "
            f"finder_window_titled_with_run_folder={'yes' if at_folder else 'no'} "
            f"windows_with_run_name={len(state['shown'])} statuses={statuses}")


def _folder_is_ours():
    import os
    try:
        st = os.stat(_FOLDER["path"])
        names = set(os.listdir(_FOLDER["path"]))
    except (KeyError, OSError):
        return False
    return (st.st_dev, st.st_ino) == _FOLDER.get("inode") and names == _folder_names()


def finder_cleanup():
    """Closes nothing in Finder: no window or sheet there is provably the
    run's — the agent may have navigated a window the person had open, and
    the person may open a sheet mid-run (Codex review of 1bf10aa). What is
    left is reported. The run's own directory is removed only while it is
    still the one mkdtemp made, holding exactly the files put there."""
    import shutil
    if not _FOLDER.get("path"):
        return "nothing_owned"
    state = finder_state()
    if state is None:
        left = "finder_unobservable"
    else:
        left = (f"finder_left windows={len(state['shown'])} "
                f"sheets={len(state['sheets'])} before={_FOLDER.get('sheets_before')}")
    if not _folder_is_ours():
        return f"folder_changed_left_as_is; {left}"
    shutil.rmtree(_FOLDER["path"])
    _FOLDER.clear()
    return f"folder_removed; {left}"


SAVE_TEXT = "NLC 저장 확인 4417"


def owned_saved_oracle(text):
    """Saved means on disk: the harness's own file holds exactly the text,
    and the window still shows it (Codex/plan 2026-10-09: 'actual save')."""
    path = _OWNED.get("path")
    try:
        on_disk = Path(path).read_text(encoding="utf-8") if path else None
    except (OSError, UnicodeDecodeError):
        on_disk = None
    norm = lambda v: " ".join((v or "").split())
    shown_ok, shown = owned_oracle(text)
    return (on_disk is not None and norm(on_disk) == norm(text) and shown_ok,
            f"disk={norm(on_disk)[:40]!r} window={shown[:40]!r}")


def calc_then_owned_setup():
    calc_reset()
    owned_setup()


_NEWDIR: dict = {}


def newdir_setup():
    """An empty folder of the harness's own (mkdtemp), its inode remembered."""
    import os
    import tempfile
    _NEWDIR.clear()
    path = os.path.realpath(tempfile.mkdtemp(prefix="nlc-compare-newdir-", dir="/tmp"))
    st = os.stat(path)
    _NEWDIR.update(path=path, inode=(st.st_dev, st.st_ino))


def _newdir_children():
    import os
    path = _NEWDIR.get("path")
    try:
        st = os.stat(path)
        if (st.st_dev, st.st_ino) != _NEWDIR.get("inode"):
            return None
        return sorted(os.listdir(path))
    except (TypeError, OSError):
        return None


def newdir_oracle(answer):
    """Exactly one new entry, a folder, and the answer names it."""
    import os
    children = _newdir_children()
    if children is None:
        return False, "folder unobservable"
    folders = [c for c in children if os.path.isdir(os.path.join(_NEWDIR["path"], c))]
    ok = len(children) == 1 and len(folders) == 1 and folders[0] in answer
    return ok, f"children={children}"


def newdir_cleanup():
    """Remove the harness's folder only if it holds nothing but empty folders."""
    import os
    import shutil
    children = _newdir_children()
    if children is None:
        return "not_observable_left_as_is"
    base = _NEWDIR["path"]
    if not all(os.path.isdir(os.path.join(base, c)) and not os.listdir(os.path.join(base, c))
               for c in children):
        return "unexpected_contents_left_as_is"
    shutil.rmtree(base)
    _NEWDIR.clear()
    return "folder_removed"


def _appearance():
    """Light, Dark or Auto, from the user defaults System Settings writes."""
    def read(key):
        r = subprocess.run(["defaults", "read", "-g", key], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None
    if read("AppleInterfaceStyleSwitchesAutomatically") == "1":
        return "Auto"
    return "Dark" if read("AppleInterfaceStyle") == "Dark" else "Light"


def appearance_oracle(answer):
    want = _appearance()
    said = {w for w in ("Light", "Dark", "Auto") if re.search(rf"\b{w}\b", answer, re.I)}
    return said == {want}, f"want {want}; said {sorted(said)}"


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
    # The document is the harness's own file, opened before the agent starts,
    # and nothing else in TextEdit is read or closed. The earlier task,
    # "create a new document", could only find its result among windows
    # nobody had proven were ours (Codex review, 2026-10-07); this is a new
    # cohort, not a continuation of textedit_write's results.
    Task("textedit_edit_owned",
         f"In TextEdit, the document named \"{OWNED_NAME}\" is already open. Type exactly this text "
         f"into that document: {TEXT}  Do not create a new document, and do not close it.",
         lambda a: owned_oracle(TEXT), setup=owned_setup, cleanup=owned_cleanup),
    # A folder the harness makes, so the right count is known. The first
    # version used /Applications, which Finder merges with
    # /System/Applications: both arms read Finder's "101 items" correctly and
    # the oracle, counting one folder, said they were wrong.
    Task("finder_count", _folder_ask,
         finder_oracle, setup=finder_setup, cleanup=finder_cleanup,
         notes="harness-made folder; Finder must be showing it"),
    Task("macos_version", "Using the System Settings app, find this Mac's macOS version number "
                          "and report it.",
         lambda a: (_macos_version() in a, f"want {_macos_version()}")),
    # Added 2026-10-09: an actual save checked on disk, two apps in one task,
    # a Finder write in the harness's own folder, and a System Settings read.
    Task("textedit_save_owned",
         f"In TextEdit, the document named \"{OWNED_NAME}\" is already open. Type exactly this text "
         f"into that document: {SAVE_TEXT}  Then save the document. Do not create a new document, "
         "do not rename it, and do not close it.",
         lambda a: owned_saved_oracle(SAVE_TEXT), setup=owned_setup, cleanup=owned_cleanup),
    Task("calc_to_textedit",
         "Compute 23 × 19 in the macOS Calculator app, then type the result (digits only) into the "
         f"TextEdit document named \"{OWNED_NAME}\", which is already open. Do not create a new "
         "document, and do not close it.",
         lambda a: owned_oracle("437"), setup=calc_then_owned_setup, cleanup=owned_cleanup),
    Task("finder_new_folder",
         lambda: (f"Using Finder, create one new folder inside the folder {_NEWDIR.get('path', '(not created)')} "
                  "and report the new folder's name."),
         newdir_oracle, setup=newdir_setup, cleanup=newdir_cleanup),
    Task("appearance_mode",
         "Using the System Settings app, find whether this Mac's appearance is set to Light, Dark "
         "or Auto, and report which one.",
         appearance_oracle),
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
                    "observed": f"setup failed: {type(exc).__name__}: {str(exc)[:120]}", "seconds": 0,
                    "answer": "", "calls": {}, "usage": None, "activations": [],
                    **_source_evidence(source_before, source_manifest())}
    locked_before = _locked()
    hidden_before = textedit_hidden() if task.name.startswith("textedit") else None
    ask = task.ask() if callable(task.ask) else task.ask
    prompt = f"{ask}\n\n{ARM_CLAUSE[arm]} {NO_SHORTCUTS}"
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
            outcome = task.cleanup()
            # A cleanup that could not prove what it owned says so instead of
            # "completed" (owned_cleanup returns why it left things as they were).
            result["cleanup_status"] = ("completed" if outcome in (None, "closed")
                                        else str(outcome)[:120])
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
            print(f"{t.name:16} {t.ask() if callable(t.ask) else t.ask}")
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
