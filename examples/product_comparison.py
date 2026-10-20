"""Same agent, same task, two computer-use layers: Tacto versus OpenAI's Sky.

Both arms invoke the same local Codex CLI route. This does not verify identical
serving models, reasoning, account state, or initial app state; those require
separate recorded evidence. The requested computer-use layers are:

  sky  — Codex's own Computer Use plugin (@oai/sky through node_repl `js`)
  nlc  — the Tacto skill and the `tacto` command (historical arm key retained)

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

_AGENT_EXECUTABLE = "codex"
_AGENT_REQUESTED_EXECUTABLE = "codex"
_SKY_SKILL_FILE = None
_COLD_START = False

CALC = "com.apple.calculator"
NO_SHORTCUTS = ("Do not use a command-line calculator, Python, AppleScript/osascript, "
                "`open`, or any other automation than the one named; work through the app.")

RUNTIME_POLICY = "background-v2"

BACKGROUND_CONSTRAINT = (
    "Keep the user's foreground app, pointer, and typing unchanged. "
    "Do not activate, raise, switch Spaces, minimize, or restore any app/window. "
    "Do not change permissions, system settings, or user documents. "
    "You may navigate the target app in the background using the named layer. "
    "If that cannot be done, stop and report the limitation. "
    "Inspect only the target app; do not read other apps or personal documents. "
)

ARM_CLAUSE = {
    "sky": ("Use the Computer Use plugin: the `js` tool of the node_repl MCP server with "
            "the @oai/sky interface (search your deferred tools for it if it is not listed). "
            "Do not use the `tacto` or legacy `nlc` command."),
    "nlc": ("Use the tacto skill and the `tacto` command to drive the app. "
            "Do not use the Computer Use plugin or node_repl. "
            "Use the installed signed tacto (typically ~/.local/bin/tacto) with --helper for GUI operations."),
}


# ---------------------------------------------------------------- app state

def _driver():
    from tacto.helper import InheritedHelperDriver
    if not InheritedHelperDriver.available():
        raise RuntimeError('comparison_requires_signed_launcher')
    return InheritedHelperDriver()


def _agent_env():
    from tacto.authoring import _provider_env
    env = _provider_env()
    for key in ('TACTO_HELPER_FD', 'TACTO_LAUNCHER', 'TACTO_HELPER_TOKEN'):
        env.pop(key, None)
    return env


def _agent_executable_available():
    """Check the exact spawn environment without executing a CLI or opening UI."""
    import os,shutil
    return shutil.which(_AGENT_EXECUTABLE,path=_agent_env().get('PATH',os.defpath)) is not None


def _run_agent(argv, prompt, timeout, diagnostics):
    """Same owned process-group lifecycle for both arms; tool events stay raw.

    This is execution, not the planning-only codex_result acceptance parser.
    Process diagnostics grant neither GUI authority nor comparison eligibility.
    """
    from tacto.codex_result import _run_codex
    if not _COLD_START:
        return _run_codex([_AGENT_EXECUTABLE, *argv[1:]], prompt, timeout, diagnostics, cwd="/tmp")
    if len(argv) < 2 or argv[1] != 'exec' or any(arg in ('resume','fork','--last') for arg in argv[2:]):
        raise ValueError('cold_start_requires_new_exec')
    import hashlib
    with tempfile.TemporaryDirectory(prefix='nlc-cold-agent-') as directory:
        work = Path(directory).resolve()
        arguments = [_AGENT_EXECUTABLE, *argv[1:]]
        if '-C' in arguments:
            arguments[arguments.index('-C')+1] = str(work)
        else:
            arguments[2:2] = ['-C',str(work)]
        if '--ephemeral' not in arguments:
            arguments.insert(2,'--ephemeral')
        evidence = {'mode':'fresh_exec_empty_workspace_v1','workspace':str(work),
            'workspace_empty_before_launch':not any(work.iterdir()),'ephemeral_requested':True,
            'resume_or_fork_requested':False,'prior_conversation_supplied_by_harness':False,
            'task_prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),
            'task_prompt_utf8_bytes':len(prompt.encode()),
            'ancestor_instruction_files':[str(parent/'AGENTS.md') for parent in work.parents if (parent/'AGENTS.md').is_file()],
            'global_instructions_absent_verified':False,'server_cache_cleared_verified':False,
            'hidden_provider_context_verified':False}
        try:
            result = _run_codex(arguments, prompt, timeout, diagnostics, cwd=str(work))
        except Exception as error:
            error.comparison_context = evidence
            raise
        result.comparison_context = evidence
        return result


def _preserve_agent_trace(stdout, stderr):
    """Private raw CLI evidence; never a claim of actual serving model/TCC."""
    import hashlib, os, uuid
    directory = ROOT / 'reviews' / datetime.date.today().isoformat()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    prefix = 'comparison-agent-' + uuid.uuid4().hex
    result = {'scope': 'raw_cli_stdout_and_stderr', 'streams': {}}
    for name, value, suffix in (('stdout', stdout, '.jsonl'), ('stderr', stderr, '.txt')):
        data = value.encode('utf-8') if isinstance(value, str) else value or b''
        path = directory / (prefix + '-' + name + suffix)
        with open(path, 'xb', opener=lambda p, f: os.open(p, f, 0o600)) as output:
            output.write(data)
        result['streams'][name] = {'path': str(path), 'bytes': len(data),
                                   'sha256': hashlib.sha256(data).hexdigest()}
    return result


def _sky_skill_evidence():
    if _SKY_SKILL_FILE is None:
        return None
    import hashlib
    try:
        with open(_SKY_SKILL_FILE, 'rb') as source:
            data = source.read(65537)
        if len(data) > 65536:
            raise ValueError('guide_over_budget')
        data.decode('utf-8')
        return {'path': _SKY_SKILL_FILE, 'sha256': hashlib.sha256(data).hexdigest(),
                'bytes': len(data), 'scope': 'host_selected_local_skill_not_runtime_attestation'}
    except (OSError, ValueError):
        return {'path': _SKY_SKILL_FILE, 'error': 'guide_unavailable'}


def _instruction_profile():
    import hashlib
    guide = _sky_skill_evidence()
    return {'schema': 'comparison-instructions-v1',
            'nlc_template_sha256': hashlib.sha256(_arm_prompt('{TASK}', 'nlc').encode()).hexdigest(),
            'sky_template_sha256': hashlib.sha256(_arm_prompt('{TASK}', 'sky').encode()).hexdigest(),
            'sky_guide_sha256': guide.get('sha256') if guide else None,
            'sky_guide_provided': _SKY_SKILL_FILE is not None}


def _arm_prompt(ask, arm):
    clause = ARM_CLAUSE[arm]
    if arm == 'sky' and _SKY_SKILL_FILE is not None:
        clause += (' Read the host-selected bundled Computer Use skill at ' +
                   json.dumps(_SKY_SKILL_FILE) +
                   ' before invoking UI methods. Use its public API signatures; do not guess arguments. '
                   'The task scope and background constraints below remain binding.')
    cold = (" Start from this task only; do not reuse prior task conversations, saved task-specific "
            "flows, or earlier execution traces. Built-in product instructions are allowed. "
            "All instruction discovery and tool calls count toward this run." if _COLD_START else "")
    return f"{ask}\n\n{clause} {NO_SHORTCUTS} {BACKGROUND_CONSTRAINT}{cold}"


def _live_agent_probe():
    """Actual model request through this signed host, without a native task."""
    import os
    import secrets
    from tacto.codex_result import parse_codex_events
    from tacto.process_responsibility import observe_responsibility
    if not _agent_executable_available():
        manifest=source_manifest()
        return {'scope':'agent_executable_preflight_no_model_or_UI','oracle_passed':False,
                'reason':'agent_executable_unavailable_in_spawn_environment',
                'agent_requested_executable':_AGENT_REQUESTED_EXECUTABLE,
                'agent_executable':_AGENT_EXECUTABLE,'agent_executed':False,
                'native_task_executed':False,'comparison_eligible':False,
                **_source_evidence(manifest,source_manifest())}
    a, b = secrets.randbelow(10000), secrets.randbelow(10000)
    records = []
    controller = observe_responsibility(os.getpid())
    before = source_manifest()
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='nlc-agent-probe-') as folder:
        final = Path(folder) / 'answer.txt'
        fd = os.open(final, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        proc = _run_agent(['codex', 'exec', '--skip-git-repo-check', '--ephemeral',
                           '--sandbox', 'read-only', '-C', '/tmp', '--json', '-o', str(final), '-'],
                          f'Without using any tools compute {a}+{b}. Return only JSON with the integer field value.',
                          90, records)
        if proc.returncode != 0:
            raise RuntimeError('agent_probe_failed')
        parsed = parse_codex_events(proc.stdout, final.read_text())
        answer = json.loads(parsed['text'])
        oracle = (type(answer) is dict and set(answer) == {'value'}
                  and type(answer['value']) is int and answer['value'] == a+b)
    return {'scope':'signed_controller_actual_codex_primary_process_only',
            'controller':controller, 'agent_primary_process_diagnostics':records,
            'agent_requested_executable':_AGENT_REQUESTED_EXECUTABLE,
            'agent_executable':_AGENT_EXECUTABLE, 'agent_cli_cwd':'/tmp',
            'agent_started_in_new_session':True,
            'oracle_passed':oracle, 'process_exit':proc.returncode,
            'elapsed_seconds':time.monotonic()-started,
            'observed_usage':{k:v for k,v in parsed.items() if k != 'text'},
            'observed_model':None, 'observed_cost':None,
            'native_task_executed':False, 'sky_process_observed':False,
            'descendants_observed':False, 'tcc_permission_verified':False,
            'comparison_eligible':False, **_source_evidence(before, source_manifest())}


_SIGNED_OWNED = {}
_FIXTURE_WORKER = None
_WORKER_SETUP = None
_WORKER_EVIDENCE = None
_WORKER_METADATA = None


def signed_owned_prepare():
    from signed_owned_textedit import SignedOwnedTextEdit
    if _SIGNED_OWNED:raise RuntimeError('previous_owned_fixture_not_cleaned')
    fixture=SignedOwnedTextEdit(_driver(),OWNED_NAME)
    _SIGNED_OWNED['fixture']=fixture
    fixture.prepare()
    return signed_fixture_evidence(fixture)


def signed_owned_setup():
    global _WORKER_SETUP, _WORKER_EVIDENCE
    if _FIXTURE_WORKER is not None:
        _WORKER_SETUP = _WORKER_EVIDENCE = None
        # Receive creator identity before setup may open UI or lose its reply.
        # After a failure, retain this evidence without any follow-up RPC.
        _WORKER_EVIDENCE = _FIXTURE_WORKER.request('prepare')
        _WORKER_SETUP = _FIXTURE_WORKER.request('setup')
        _WORKER_EVIDENCE = _WORKER_SETUP['evidence']
        return {'fixture_worker_setup': _WORKER_EVIDENCE,
                'fixture_worker_task_setup': _WORKER_SETUP.get('setup_evidence')}
    from signed_owned_textedit import SignedOwnedTextEdit
    fixture=_SIGNED_OWNED.get('fixture')
    if fixture is None:
        fixture = SignedOwnedTextEdit(_driver(), OWNED_NAME)
        _SIGNED_OWNED['fixture'] = fixture
    elif getattr(fixture,'setup_started',None) is not False:
        raise RuntimeError('previous_owned_fixture_not_cleaned')
    fixture.setup()


def signed_owned_oracle(text):
    if _FIXTURE_WORKER is not None:
        if text != TEXT:
            raise RuntimeError('unsupported_worker_oracle')
        return tuple(_FIXTURE_WORKER.request('oracle'))
    fixture = _SIGNED_OWNED.get('fixture')
    body = fixture.read() if fixture is not None else None
    if body is None:
        return False, 'owned_document_text_unobservable'
    return body == text, body[:60]


def signed_owned_cleanup():
    global _WORKER_EVIDENCE
    if _FIXTURE_WORKER is not None:
        result = _FIXTURE_WORKER.request('cleanup')
        _WORKER_EVIDENCE = result['evidence']
        return result['status']
    fixture = _SIGNED_OWNED.get('fixture')
    if fixture is None:
        return 'nothing_owned'
    status = fixture.cleanup()
    if status == 'closed':
        _SIGNED_OWNED.clear()
    return status


def signed_fixture_evidence(fixture=None):
    if _FIXTURE_WORKER is not None:
        return _WORKER_EVIDENCE
    fixture = fixture or _SIGNED_OWNED.get('fixture')
    if fixture is None:
        return None
    return {'path':str(fixture.path) if fixture.path is not None else None,
            'directory_identity':fixture.directory_identity,
            'prepared_file_identity':getattr(fixture,'prepared_file_identity',None),
            'setup_started':getattr(fixture,'setup_started',None),
            'initial_empty_text_verified':getattr(fixture,'initial_empty_text_verified',False),
            'initial_expected_text_verified':getattr(fixture,'initial_expected_text_verified',False),
            'status_observations':[dict(record) for record in fixture.status_observations],
            'status_observations_truncated':fixture.status_observations_truncated,
            'close_requested':fixture.close_requested,
            'closed_observed':fixture.closed_observed,
            'artifact_removed':fixture.directory is not None and not fixture.directory.exists()}


def signed_owned_edit_ask():
    if _FIXTURE_WORKER is not None:
        return _WORKER_SETUP['ask']
    fixture = _SIGNED_OWNED.get('fixture')
    if fixture is None or fixture.path is None:
        raise RuntimeError('owned_fixture_not_prepared')
    return (f'In TextEdit, the document at the exact owned path {fixture.path} is already open. '
            f'Type exactly this text into only that document: {TEXT}. '
            'Do not create, rename or close any document.')


def signed_owned_save_ask():
    fixture=_SIGNED_OWNED.get('fixture')
    if fixture is None or fixture.path is None:raise RuntimeError('owned_fixture_not_prepared')
    return (f'In TextEdit, the document at the exact owned path {fixture.path} is already open. '
            f'Type exactly this text into only that document: {SAVE_TEXT}. Then save that document. '
            'Do not create, rename or close any document.')


def signed_chain_ask():
    if _FIXTURE_WORKER is not None:
        return _WORKER_SETUP['ask']
    fixture=_SIGNED_OWNED.get('fixture')
    if fixture is None or fixture.path is None:raise RuntimeError('owned_fixture_not_prepared')
    return ('Compute 23 × 19 in the macOS Calculator app, then type the result (digits only) '
            f'into only the TextEdit document at the exact owned path {fixture.path}, which is already open. '
            'Do not create, rename or close any document.')


def _owned_lifecycle_probe(resume_directory=None, resume_identity=None, fixture_text=None, resume_text=""):
    from focus_witness import FocusWitness
    from signed_owned_textedit import SignedOwnedTextEdit
    fixture = SignedOwnedTextEdit(_driver(), OWNED_NAME)
    before = source_manifest()
    with FocusWitness() as witness:
        if resume_directory is None:
            fixture.setup()
        else:
            fixture.resume(resume_directory, resume_identity, expected_text=resume_text)
        opened = fixture.status()
        initial_text=fixture.read()
        empty = initial_text == ''
        initial_verified=initial_text==(resume_text if resume_directory is not None else '')
        if not initial_verified or opened!='open':
            raise RuntimeError('owned_fixture_initial_state_unverified')
        receiving = fixture.write(fixture_text) if fixture_text is not None else None
        cleanup = fixture.cleanup()
    return {'scope':('signed_owned_resumed_textedit_window_lifecycle' if resume_directory is not None
                     else 'signed_owned_empty_textedit_window_lifecycle' if fixture_text is None
                     else 'signed_owned_edited_textedit_window_lifecycle'),
            'receiving_text_verified':receiving,'status_observations':fixture.status_observations,
            'status_observations_truncated':fixture.status_observations_truncated,
            'owned_fixture_evidence':signed_fixture_evidence(fixture),
            'opened_state':opened, 'initial_empty_text_verified':empty,
            'initial_expected_text_verified':initial_verified,
            'cleanup_status':cleanup, 'closed_observed':fixture.closed_observed,
            'artifact_removed':not fixture.directory.exists(),
            'focus':witness.summary(), 'model_requests':0,
            'comparison_eligible':False, **_source_evidence(before, source_manifest())}


def _responsibility_probe():
    """Observe this controller and one owned, idle non-model child."""
    import os
    from tacto.process_responsibility import observe_responsibility
    parent = observe_responsibility(os.getpid())
    cleanup_status = 'completed'
    # The child waits for one stdin byte. No GUI, network, model or file writes.
    with subprocess.Popen([sys.executable, '-c', 'import sys;sys.stdin.buffer.read(1)'],
                          stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, env=_agent_env(), close_fds=True,
                          start_new_session=True) as child:
        try:
            if child.poll() is not None:
                raise RuntimeError('owned_probe_child_exited')
            observed = observe_responsibility(child.pid)
            if child.poll() is not None:
                observed = {'pid':child.pid, 'status':'unavailable',
                            'error_code':'owned_child_exited_during_read'}
        finally:
            try:
                child.communicate(input=b'x', timeout=3)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
                cleanup_status = 'owned_child_cleanup_timeout'
            except BaseException:
                child.kill()
                child.wait()
                raise
    return {'controller':parent, 'owned_idle_python_child':observed,
            'model_or_sky_process_observed':False, 'comparison_eligible':False,
            'tcc_permission_verified':False, 'child_started_in_new_session':True,
            'cleanup_status':cleanup_status}


def _tree(target):
    o = _driver().observe(target, "tree_full", require_window=False)
    return o.get("tree", "") if o.get("type") == "observation" else ""


def _calc_display():
    from tacto.flow import Selector
    line = next((l for l in _tree(CALC).splitlines() if "Edit field" in l), "")
    return (Selector._attr(line, "Value") or "").replace(",", "").strip("‎ ")


def _calc_mode():
    m = re.search(r"Identifier: Mode: (\w+)", _tree(CALC))
    return m.group(1) if m else None


def calc_reset(mode="basic"):
    """Basic (or given) mode, cleared display — the same start for both arms."""
    from tacto.flow import Selector
    d = _driver()
    o = d.observe(CALC, "tree_full", require_window=False)
    bootstrap='not_needed'
    bootstrap_focus=None
    if o.get('error')=='target_not_found':
        from focus_witness import FocusWitness
        if _locked() is not False:raise RuntimeError('calculator_bootstrap_environment_unavailable')
        with FocusWitness() as witness:
            opened=subprocess.run(['open','-g','-b',CALC],capture_output=True,timeout=20)
            if opened.returncode!=0:raise RuntimeError('calculator_background_launch_failed')
            deadline=time.monotonic()+8
            while time.monotonic()<deadline:
                if _locked() is not False:raise RuntimeError('calculator_bootstrap_environment_unavailable')
                o=d.observe(CALC,'tree_full',require_window=False)
                if o.get('type')=='observation':break
                time.sleep(.1)
            else:raise RuntimeError('calculator_observation_unavailable')
        bootstrap_focus=witness.summary()
        if bootstrap_focus['activations']:raise RuntimeError('calculator_bootstrap_activated_app')
        bootstrap='background_launch'
    if o.get('type')!='observation':raise RuntimeError('calculator_observation_unavailable')
    title = {"basic": ["Basic", "기본"], "scientific": ["Scientific", "공학용"]}[mode]
    response = d.act(o["obs_id"], CALC, {"name": "menu", "path": ["View", title]})
    if not isinstance(response, dict) or response.get('type') != 'acted':
        raise RuntimeError('comparison_setup_menu_unverified')
    time.sleep(0.5)
    for _ in range(3):
        o = d.observe(CALC, "tree_full", require_window=False)
        try:
            i, _ = Selector(role="Button", identifier=["AllClear", "Clear"],
                            ancestor="CalculatorKeypadView").resolve(o["tree"])
        except LookupError:
            break
        response = d.act(o["obs_id"], CALC, {"name": "click", "element_index": i})
        if not isinstance(response, dict) or response.get('type') != 'acted':
            raise RuntimeError('comparison_setup_clear_unverified')
        time.sleep(0.2)
    display,observed_mode=_calc_display(),_calc_mode()
    if display!='0' or observed_mode!=mode:raise RuntimeError('calculator_initial_state_unverified')
    return {'calculator_bootstrap':bootstrap,'calculator_bootstrap_focus':bootstrap_focus,
            'calculator_initial_display':display,'calculator_initial_mode':observed_mode,
            'harness_launched_app_left_running':bootstrap=='background_launch'}


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
    from tacto.driver import _attr, _unhide_quietly
    app, _identity = _textedit_process()
    if app is None:
        return []
    if app.isHidden():
        _unhide_quietly(app.processIdentifier())
    return list(_attr(AXUIElementCreateApplication(app.processIdentifier()), "AXWindows") or [])


def _text_of(window):
    from tacto.driver import _attr

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
    from tacto.driver import _attr
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


def owned_oracle(text, *, exact=False):
    window = owned_window()
    if window is None:
        return False, "owned document not provable"
    body = _text_of(window)
    if not isinstance(body, str):
        return False, "owned document text unobservable"
    matches = body == text if exact else " ".join(body.split()) == " ".join(text.split())
    return matches, body[:60]


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
    from tacto.driver import _attr
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
    from tacto.driver import _attr
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
    from tacto.session import lock_state
    state = lock_state()
    if state['locked'] is True:
        return True
    # Key absence is interpreted only with logged-in console indicators. An
    # off-console or incomplete login cannot support this interactive comparison.
    return state['locked'] if state['on_console'] is True and state['login_done'] is True else None


# ---------------------------------------------------------------- tasks

@dataclass
class Task:
    name: str
    ask: object                 # text, or () -> text when it names a per-run path
    oracle: object              # (answer) -> (passed, observed)
    setup: object = None
    cleanup: object = None
    notes: str = ""
    oracle_contract: str = "legacy-ui-state-v1"
    preview: str | None = None


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
    from tacto.driver import _attr
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
    from tacto.driver import _attr
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


def _read_owned_file_bytes(path, is_ours):
    """Independent bounded file oracle; never use NLC's receipt as its judge."""
    import os
    import stat
    if not path or not is_ours() or os.path.realpath(path) != path:
        return None
    fd = None
    limit = 4 * 1024 * 1024
    def identity(info):
        return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
                info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    try:
        named_before = os.lstat(path)
        if not stat.S_ISREG(named_before.st_mode):
            return None
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        before = os.fstat(fd)
        if (identity(before) != identity(named_before) or before.st_uid != os.getuid()
                or before.st_nlink != 1 or before.st_size > limit):
            return None
        parts, count = [], 0
        while True:
            part = os.read(fd, min(65536, limit + 1 - count))
            if not part:
                break
            parts.append(part); count += len(part)
            if count > limit:
                return None
        data = b"".join(parts)
        if (identity(before) != identity(os.fstat(fd))
                or identity(before) != identity(os.lstat(path))
                or len(data) != before.st_size or os.path.realpath(path) != path
                or not is_ours()):
            return None
        return data
    except OSError:
        return None
    finally:
        if fd is not None:
            os.close(fd)


def _owned_saved_bytes():
    return _read_owned_file_bytes(_OWNED.get('path'),_owned_file_is_ours)


def signed_owned_saved_oracle(text):
    import os
    import stat
    fixture=_SIGNED_OWNED.get('fixture')
    if fixture is None or fixture.path is None:return False,'no_owned_fixture'
    def is_ours():
        try:
            st=fixture.directory.lstat()
            return (fixture.directory.resolve()==fixture.directory and stat.S_ISDIR(st.st_mode)
                    and (st.st_dev,st.st_ino)==fixture.directory_identity and st.st_uid==os.getuid()
                    and stat.S_IMODE(st.st_mode)==0o700 and os.listdir(fixture.directory)==[fixture.name])
        except OSError:return False
    disk=_read_owned_file_bytes(str(fixture.path),is_ours)
    shown,_=signed_owned_oracle(text)
    return disk==text.encode('utf-8') and shown, f'disk_exact={disk==text.encode("utf-8")} window_exact={shown}; saved_state_only'


def signed_calc_then_owned_setup():
    if _FIXTURE_WORKER is not None:
        return signed_owned_setup()
    evidence=calc_reset()
    signed_owned_setup()
    return evidence


def signed_chain_oracle(text):
    if _FIXTURE_WORKER is not None:
        if text != '437':raise RuntimeError('unsupported_worker_oracle')
        return tuple(_FIXTURE_WORKER.request('oracle'))
    shown,_=signed_owned_oracle(text)
    calculated=_calc_display()
    return shown and calculated==text, f'calculator_exact={calculated==text} document_exact={shown}; no_explicit_save_required'


def owned_saved_oracle(text):
    """Saved means on disk: the harness's own file holds exactly the text,
    and the window still shows it (Codex/plan 2026-10-09: 'actual save')."""
    on_disk = _owned_saved_bytes()
    shown_ok, _shown = owned_oracle(text, exact=True)
    return (on_disk == text.encode("utf-8") and shown_ok,
            f"disk_exact={on_disk == text.encode('utf-8')} window_exact={shown_ok}")


def calc_then_owned_setup():
    calc_reset()
    owned_setup()


_NEWDIR: dict = {}


def newdir_setup():
    """An empty folder of the harness's own (mkdtemp), its inode remembered."""
    import os
    import tempfile
    if _NEWDIR:raise RuntimeError('previous_owned_folder_not_cleaned')
    path = os.path.realpath(tempfile.mkdtemp(prefix="nlc-compare-newdir-", dir="/tmp"))
    st = os.stat(path)
    _NEWDIR.update(path=path, inode=(st.st_dev, st.st_ino), ui_may_have_opened=False)
    return newdir_evidence()


def newdir_evidence():
    if not _NEWDIR:return None
    return {'path':_NEWDIR['path'],'directory_identity':list(_NEWDIR['inode']),
            'ui_may_have_opened':_NEWDIR.get('ui_may_have_opened',True),
            'window_close_verified':False}


def _newdir_children():
    import os,stat
    path = _NEWDIR.get("path")
    if not isinstance(path,str):return None
    try:
        st = os.lstat(path)
        if not stat.S_ISDIR(st.st_mode) or (st.st_dev, st.st_ino) != _NEWDIR.get("inode"):
            return None
        return sorted(os.listdir(path))
    except (TypeError, OSError):
        return None


def _real_folder(path):
    """A directory itself, not a link to one (Codex review of 02aff15:
    os.path.isdir follows symlinks, so a link to an outside folder passed)."""
    import os
    import stat
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)
    except OSError:
        return False


def newdir_oracle(answer):
    """Exactly one new entry, a folder, and the answer names it."""
    import os
    children = _newdir_children()
    if children is None:
        return False, "folder unobservable"
    folders = [c for c in children if _real_folder(os.path.join(_NEWDIR["path"], c))]
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
    if not all(_real_folder(os.path.join(base, c)) and not os.listdir(os.path.join(base, c))
               for c in children):
        return "unexpected_contents_left_as_is"
    if _NEWDIR.get('ui_may_have_opened',True):
        # Removing a displayed folder can redirect Finder to its parent. The
        # model's claim/exit does not prove this exact window was closed.
        return 'owned_directory_retained_without_window_close_proof'
    shutil.rmtree(base)
    _NEWDIR.clear()
    return "folder_removed"


def _appearance():
    """Light, Dark or Auto, from the user defaults System Settings writes —
    or None when a read failed. A key that does not exist is a real answer
    (Light is the default); a failed read is not (Codex review of 02aff15)."""
    def read(key):
        r = subprocess.run(["defaults", "read", "-g", key], capture_output=True, text=True)
        if r.returncode == 0:
            return r.stdout.strip()
        # macOS 27: "Could not find key …"; older: "… does not exist".
        if "Could not find key" in r.stderr or "does not exist" in r.stderr:
            return ""
        raise RuntimeError(key)
    try:
        if read("AppleInterfaceStyleSwitchesAutomatically") == "1":
            return "Auto"
        return "Dark" if read("AppleInterfaceStyle") == "Dark" else "Light"
    except (RuntimeError, OSError):
        return None


def appearance_oracle(answer):
    want = _appearance()
    if want is None:
        return False, "appearance unobservable"
    said = {w for w in ("Light", "Dark", "Auto") if re.search(rf"\b{w}\b", answer, re.I)}
    return said == {want}, f"want {want}; said {sorted(said)}"


def source_manifest():
    """Explicit source/artifact set, not a dependency-closure claim.

    Both arms record the same NLC state. Missing signed artifacts are recorded,
    not silently omitted; package/fixture changes and binary rebuilds matter.
    """
    import hashlib
    fixed = ["skill/SKILL.md", "launcher/tacto", "launcher/tacto.swift",
             "helper/tacto-helper", "helper/Tacto Helper.app/Contents/MacOS/TactoHelper",
             "helper/Tacto Helper.app/Contents/Info.plist", "signing.sh",
             "pyproject.toml", "uv.lock"]
    paths = {ROOT / name for name in fixed}
    for directory, pattern in (("tacto", "**/*.py"), ("nlc", "**/*.py"), ("helper", "*.swift"),
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
    Task("textedit_edit_owned", signed_owned_edit_ask,
         lambda a: signed_owned_oracle(TEXT), setup=signed_owned_setup, cleanup=signed_owned_cleanup,
         oracle_contract="signed-owned-document-exact-text-v2",
         preview="Edit only the evaluator-created TextEdit document; its exact path is assigned during setup."),
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
    Task("textedit_save_owned",signed_owned_save_ask,
         lambda a: signed_owned_saved_oracle(SAVE_TEXT), setup=signed_owned_setup, cleanup=signed_owned_cleanup,
         oracle_contract="signed-owned-document-exact-utf8-saved-state-v3",
         preview="Save exact text in the evaluator-created TextEdit document; the owned path is assigned during setup."),
    Task("calc_to_textedit",signed_chain_ask,
         lambda a: signed_chain_oracle("437"), setup=signed_calc_then_owned_setup, cleanup=signed_owned_cleanup,
         oracle_contract="signed-calculator-and-owned-document-exact-text-v3",
         preview="Compute 23 × 19 in Calculator and type digits in the evaluator-created TextEdit document."),
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
    instruction_before = _instruction_profile()
    sky_skill_before = _sky_skill_evidence() if arm == 'sky' else None
    from tacto.helper import InheritedHelperDriver
    signed_channel_available = InheritedHelperDriver.available() or _FIXTURE_WORKER is not None
    if not signed_channel_available or task.cleanup is owned_cleanup:
        # Legacy owned TextEdit cleanup still sends direct AXPress. Do not create
        # that fixture until its close/document binding has a signed equivalent.
        reason = ('comparison_requires_signed_launcher' if not signed_channel_available
                  else 'owned_cleanup_requires_signed_window_binding')
        return {"task":task.name, "arm":arm, "status":"not_run", "seconds":0,
                "oracle_contract":task.oracle_contract, "oracle_passed":None,
                "observed":reason, "answer":"", "calls":{}, "usage":None,
                "activations":[], "process_exit":None, "comparison_eligible":False,
                "cleanup_status":"not_requested",
                **_source_evidence(source_before, source_manifest())}
    initial_lock = _locked()
    if initial_lock is not False:
        # Do not create fixtures or issue setup actions in an inaccessible GUI.
        return {"task": task.name, "arm": arm, "status": "not_run", "seconds": 0,
                "oracle_contract": task.oracle_contract, "oracle_passed": None,
                "observed": "screen_locked" if initial_lock is True else "session_unobservable",
                "answer": "", "calls": {}, "usage": None, "activations": [],
                "locked": [initial_lock, initial_lock], "process_exit": None,
                "comparison_eligible": False, "cleanup_status": "not_requested",
                **_source_evidence(source_before, source_manifest())}
    if not _agent_executable_available():
        return {"task":task.name,"arm":arm,"status":"not_run","seconds":0,
                "oracle_contract":task.oracle_contract,"oracle_passed":None,
                "observed":"agent_executable_unavailable_before_setup",
                "agent_launch_error":"agent_executable_unavailable",
                "agent_requested_executable":_AGENT_REQUESTED_EXECUTABLE,
                "agent_executable":_AGENT_EXECUTABLE,"timeout_seconds":timeout,
                "answer":"","calls":{},"usage":None,"activations":[],
                "process_exit":None,"comparison_eligible":False,
                "cleanup_status":"not_requested","locked":[initial_lock,initial_lock],
                **_source_evidence(source_before,source_manifest())}
    setup_evidence=None
    if task.setup:
        try:
            setup_evidence=task.setup()
        except Exception as exc:
            return {"task": task.name, "arm": arm, "status": "not_run",
                    "oracle_contract": task.oracle_contract,
                    "observed": f"setup failed: {type(exc).__name__}: {str(exc)[:120]}", "seconds": 0,
                    "owned_fixture_evidence":signed_fixture_evidence() if task.cleanup is signed_owned_cleanup else None,
                    "comparison_eligible":False, "cleanup_status":"setup_failed_left_as_is",
                    "finder_fixture_evidence":newdir_evidence() if task.cleanup is newdir_cleanup else None,
                    "setup_outcome":"unverified_after_failure",
                    "owned_fixture_evidence_scope":("prepare_reply_before_setup" if _FIXTURE_WORKER is not None
                                                    else "host_fixture_snapshot_after_setup_failure"),
                    "answer": "", "calls": {}, "usage": None, "activations": [],
                    **_source_evidence(source_before, source_manifest())}
    locked_before = _locked()
    if locked_before is not False:
        return {"task": task.name, "arm": arm, "status": "not_run", "seconds": 0,
                "oracle_contract": task.oracle_contract, "oracle_passed": None,
                "observed": "environment_unavailable_after_setup",
                "answer": "", "calls": {}, "usage": None, "activations": [],
                "locked": [initial_lock, locked_before], "process_exit": None,
                "comparison_eligible": False, "cleanup_status": "environment_unavailable_left_as_is",
                "finder_fixture_evidence":newdir_evidence() if task.cleanup is newdir_cleanup else None,
                "owned_fixture_evidence":signed_fixture_evidence() if task.cleanup is signed_owned_cleanup else None,
                **_source_evidence(source_before, source_manifest())}
    report_fixture = _SIGNED_OWNED.get('fixture') if task.cleanup is signed_owned_cleanup else None
    hidden_before = textedit_hidden() if task.name.startswith("textedit") else None
    ask = task.ask() if callable(task.ask) else task.ask
    prompt = _arm_prompt(ask, arm)
    context_evidence = None
    with tempfile.NamedTemporaryFile("r", suffix=".txt") as last, FocusWitness() as witness:
        started = time.monotonic()
        process_diagnostics = []
        proc, launch_error = None, None
        try:
            if task.cleanup is newdir_cleanup and _NEWDIR:
                _NEWDIR['ui_may_have_opened']=True
            proc = _run_agent(
                ["codex", "exec", "--skip-git-repo-check", "--sandbox", "danger-full-access",
                 "-C", "/tmp", "--json", "-o", last.name, "-"],
                prompt, timeout, process_diagnostics)
            context_evidence = getattr(proc, "comparison_context", None)
            stdout, timed_out = proc.stdout, False
        except subprocess.TimeoutExpired as exc:
            context_evidence = getattr(exc, "comparison_context", None)
            stdout, timed_out = (exc.stdout or b"").decode(errors="replace") if isinstance(
                exc.stdout, bytes) else (exc.stdout or ""), True
        except OSError:
            stdout, timed_out = '', False
            launch_error = ('agent_transport_failed' if process_diagnostics
                            else 'agent_launch_unavailable')
        seconds = time.monotonic() - started
        answer = Path(last.name).read_text(errors="replace").strip()
    try:
        trace = _preserve_agent_trace(stdout, proc.stderr if proc is not None else '')
    except OSError:
        trace = {'scope': 'raw_cli_stdout_and_stderr', 'error': 'trace_preservation_failed'}
    from tacto.tool_item_usage import ToolItemUsage
    tool_items=ToolItemUsage()
    calls, usage, layer_violation, shortcut = {}, None, False, False
    usage_events = []
    preparation_diagnostics = []
    pin_diagnostics = []
    terminal_completed=False
    terminal_failed=False
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            if line.strip():tool_items.note_unparsed()
            continue
        if not isinstance(event,dict):
            tool_items.note_unparsed()
            continue
        item = event.get("item")
        if not isinstance(item,dict):
            if event.get('type') in ('item.started','item.updated','item.completed'):
                tool_items.note_unparsed()
            item={}
        if item.get("type") == "mcp_tool_call":
            key = f"{item.get('server')}:{item.get('tool')}"
            calls[key] = calls.get(key, 0) + 1
            tool_items.record(event,key)
            if arm == "nlc" and item.get("server") in ("node_repl", "cua_repl"):
                layer_violation = True
        elif item.get("type") == "command_execution":
            cmd = item.get("command", "")
            if not isinstance(cmd,str):cmd=""
            if re.search(r"(^|[\s'\"/])nlc\s", cmd):
                if event.get('type') == 'item.completed':
                    from tacto.preparation_diagnostic import diagnostic_lines
                    # Tool-reported diagnostics, not a completion oracle or
                    # attestation of a command's execution provenance.
                    preparation_diagnostics.extend(diagnostic_lines(item.get('aggregated_output')))
                    preparation_diagnostics = preparation_diagnostics[:64]
                    from tacto.pin_diagnostic import diagnostic_lines as pin_lines
                    pin_diagnostics.extend(pin_lines(item.get('aggregated_output')))
                    pin_diagnostics = pin_diagnostics[:64]
                calls["shell:nlc"] = calls.get("shell:nlc", 0) + 1
                tool_items.record(event,"shell:nlc")
                if arm == "sky":
                    layer_violation = True
            else:
                calls["shell:other"] = calls.get("shell:other", 0) + 1
                tool_items.record(event,"shell:other")
            if re.search(r"osascript|\bbc\b|python3? -c|\bopen -a|System Events", cmd):
                shortcut = True
        if event.get("type") == "turn.completed":
            terminal_completed=True
            usage = event.get("usage")
            usage_events.append(usage)
        elif event.get("type") in ("turn.failed","error"):
            terminal_failed=True
    passed, observed = (False, "not checked")
    if launch_error is None and (answer or not timed_out):
        try:
            passed, observed = task.oracle(answer)
        except Exception as exc:
            passed, observed = False, f"oracle error: {type(exc).__name__}"
    provider_down = (not answer and re.search(r"usage limit|rate limit|not logged in",
                                              stdout, re.I))
    process_exit = None if proc is None else proc.returncode
    status = ("failed" if launch_error == "agent_transport_failed"
              else "not_run" if launch_error or provider_down else "timeout" if timed_out
              else "invalid" if (layer_violation or shortcut)
              else "failed" if process_exit != 0
              else "passed" if passed else "failed")
    focus = witness.summary()
    result = {"task": task.name, "arm": arm, "status": status, "seconds": round(seconds, 1),
              "oracle_contract": task.oracle_contract, "agent_launch_error":launch_error,
              "agent_trace": trace,
              "timeout_seconds":timeout, "runtime_policy":RUNTIME_POLICY,
              "background_constraint":BACKGROUND_CONSTRAINT,
              "reported_preparation_diagnostics": preparation_diagnostics,
              "reported_document_pin_diagnostics": pin_diagnostics,
              "observed": observed, "answer": answer[-400:], "calls": calls, "usage": usage,
              "calls_scope":"legacy_tool_event_items_including_started_completed",
              "tool_item_usage":tool_items.snapshot(stream_complete=not timed_out and launch_error is None and proc is not None and proc.returncode==0 and terminal_completed and not terminal_failed),
              "activations": focus["activations"],
              "initial_frontmost_bundle": focus.get("initial"),
              "focus_observation_scope":"agent_execution_only_excludes_setup_cleanup",
              "layer_violation": layer_violation, "shortcut": shortcut,
              "process_exit": process_exit, "oracle_passed": bool(passed),
              "locked": [locked_before, _locked()],
              "textedit_hidden_before": hidden_before}
    # Preserve the attempted outcome. An environment observation is grounds
    # to exclude a comparison, not evidence that the attempt never happened
    # or that the environment caused its failure.
    result["comparison_eligible"] = all(value is False for value in result["locked"])
    if not result["comparison_eligible"]:
        result["environment_issue"] = "screen_locked_or_session_unobservable"
    result["cleanup_status"] = "not_requested"
    if task.cleanup and not result["comparison_eligible"]:
        result["cleanup_status"] = "environment_unavailable_left_as_is"
    elif task.cleanup:
        try:
            outcome = task.cleanup()
            # A cleanup that could not prove what it owned says so instead of
            # "completed" (owned_cleanup returns why it left things as they were).
            result["cleanup_status"] = ("completed" if outcome in (None, "closed")
                                        else str(outcome)[:120])
        except Exception as error:
            result["cleanup_status"] = "failed"
            result["cleanup_error"] = type(error).__name__[:80]
    if report_fixture is not None or _FIXTURE_WORKER is not None:
        result['owned_fixture_evidence'] = signed_fixture_evidence(report_fixture)
    if task.cleanup is newdir_cleanup:result['finder_fixture_evidence']=newdir_evidence()
    if isinstance(setup_evidence,dict):result['setup_evidence']=setup_evidence
    result.update(_source_evidence(source_before, source_manifest()))
    if result["source_changed"]:
        # A successful oracle cannot certify a comparison under drifting code.
        result["observed_status"] = result["status"]
        result["status"] = "invalid"
        result["invalid_reason"] = "source_changed"
        result["comparison_eligible"] = False
    # Environment variables are not a TCC responsibility boundary. A Codex
    # spawned by this signed controller may inherit its responsible identity.
    # Even the external worker mode lacks complete descendant/Sky route and
    # initial-state evidence. Keep task outcomes but exclude product parity.
    result['agent_spawn_parent_boundary'] = ('external_python_controller' if _FIXTURE_WORKER is not None
                                              else 'signed_nlc_controller')
    if _FIXTURE_WORKER is not None:
        result['fixture_worker_metadata'] = _WORKER_METADATA
    result['agent_requested_executable'] = _AGENT_REQUESTED_EXECUTABLE
    result['agent_executable'] = _AGENT_EXECUTABLE
    result['agent_cli_cwd'] = context_evidence['workspace'] if context_evidence else '/tmp'
    result['cold_start_requested'] = _COLD_START
    result['context_evidence'] = context_evidence
    from tacto.comparison_tokens import reported_turn_tokens
    result['token_accounting'] = reported_turn_tokens(usage_events, stream_complete=not timed_out and
        launch_error is None and process_exit == 0 and terminal_completed and not terminal_failed)
    result['agent_started_in_new_session'] = True
    result['agent_responsible_process_observed'] = None
    result['agent_primary_process_diagnostics'] = process_diagnostics
    from tacto.process_responsibility import summarize_primary_responsibility
    result['agent_primary_responsibility_summary'] = summarize_primary_responsibility(process_diagnostics)
    result['comparison_eligible'] = False
    result['comparison_exclusion_reason'] = 'agent_responsibility_not_isolated_or_observed'
    instruction_after = _instruction_profile()
    result['comparison_instruction_profile'] = instruction_before
    result['instruction_profile_stable'] = instruction_before == instruction_after
    result['agent_prompt_template_sha256'] = instruction_before[arm + '_template_sha256']
    if instruction_before != instruction_after:
        result.setdefault('observed_status', result['status'])
        result['status'] = 'invalid'
        result['invalid_reason'] = 'instruction_profile_changed'
    if arm == 'sky':
        skill_after = _sky_skill_evidence()
        result['sky_skill_evidence'] = {'before': sky_skill_before, 'after': skill_after,
                                      'provided': _SKY_SKILL_FILE is not None,
                                      'stable': sky_skill_before == skill_after if _SKY_SKILL_FILE is not None else None}
        if _SKY_SKILL_FILE is not None and (sky_skill_before != skill_after or
                sky_skill_before is None or 'error' in sky_skill_before):
            result.setdefault('observed_status', result['status'])
            result['status'] = 'invalid'
            result['invalid_reason'] = 'sky_skill_unavailable_or_changed'
    return result


def main(argv=None) -> int:
    global _AGENT_EXECUTABLE, _AGENT_REQUESTED_EXECUTABLE, _FIXTURE_WORKER, _WORKER_METADATA, _SKY_SKILL_FILE, _COLD_START
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    from comparison_fixture_worker import WORKER_TASKS
    ap.add_argument('--fixture-worker-task', choices=WORKER_TASKS, default='textedit_edit_owned', help=argparse.SUPPRESS)
    ap.add_argument('--fixture-worker', action='store_true', help=argparse.SUPPRESS)
    ap.add_argument('--external-owned-worker', metavar='SIGNED_LAUNCHER',
                    help='external controller with a separate signed worker; one owned TextEdit task or Calculator-to-TextEdit')
    worker_checks = ap.add_mutually_exclusive_group()
    worker_checks.add_argument('--check-worker-lifecycle', action='store_true',
                    help='open/read/close one owned empty TextEdit document through separate worker; no model')
    worker_checks.add_argument('--check-worker-boundary', action='store_true',
                    help='check separate worker/helper metadata without GUI setup or model requests')
    ap.add_argument('--cold-start', action='store_true', help='fresh exec, ephemeral session, empty private workspace; global/system context and server cache remain unverified')
    ap.add_argument('--sky-skill-file', help='host-selected absolute bundled Computer Use SKILL.md path; no installation changes')
    ap.add_argument('--agent-executable', help='host-selected absolute Codex CLI path; does not change PATH')
    ap.add_argument('--timeout', type=int, default=600,
                    help='per-arm model timeout in seconds (same for both arms)')
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--arms", nargs="+", default=["nlc", "sky"], choices=["nlc", "sky"])
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--list", action="store_true")
    checks = ap.add_mutually_exclusive_group()
    checks.add_argument("--check-backend", action="store_true",
                    help="read signed helper metadata only; no setup, model or task input")
    checks.add_argument('--check-responsibility', action='store_true',
                        help='read controller/owned idle child process responsibility; no model or GUI')
    checks.add_argument('--check-agent-responsibility', action='store_true',
                        help='spend model tokens on a tool-free arithmetic probe; no native task')
    checks.add_argument('--check-owned-document-lifecycle', action='store_true',
                        help='create/open/close only an owned empty TextEdit fixture; no model')
    ap.add_argument('--owned-fixture-text', help='host-authored text for a newly owned lifecycle probe fixture; sends signed UI input')
    ap.add_argument('--resume-owned-fixture-directory', help='trusted host path of a prior owned fixture; lifecycle probe only')
    ap.add_argument('--resume-owned-fixture-identity', help='trusted host recorded dev:ino of that prior fixture')
    ap.add_argument('--resume-owned-fixture-text', help='non-sensitive host fixture body for read-only resume; argv is visible to local processes; defaults to empty')
    ap.add_argument('--diagnose-owned-document-pin', help='read only the explicitly host-selected owned fixture path; no input')
    checks.add_argument('--check-owned-save-receipt', action='store_true',help='owned native Save callback and fresh-file receipt; no model')
    checks.add_argument('--check-owned-textedit-save',action='store_true',help='actual owned TextEdit preparation and bound in-place Save; saved-state evidence only')
    checks.add_argument('--check-owned-document-observe',action='store_true',help='installed signed CLI scopes observation to one of two owned TextEdit documents')
    checks.add_argument('--check-owned-document-observe-escaped',action='store_true',help='same-name scoped observation with literal UI markers and quotes in the filename')
    checks.add_argument('--check-owned-document-run',action='store_true',help='installed scoped CLI replay reads one of two owned documents')
    checks.add_argument('--check-owned-scoped-save',action='store_true',help='native document-token Save and exact saved-state receipt')
    checks.add_argument('--check-owned-scoped-cli-save',action='store_true',help='installed scoped CLI built-in Save with disk-state check')
    args = ap.parse_args(argv)
    _COLD_START = args.cold_start
    if args.sky_skill_file is not None:
        guide = Path(args.sky_skill_file)
        if not guide.is_absolute() or any(ord(c) < 32 for c in str(guide)):
            ap.error('--sky-skill-file requires an absolute path without control characters')
        _SKY_SKILL_FILE = str(guide.resolve())
        if 'error' in _sky_skill_evidence():
            ap.error('--sky-skill-file requires readable UTF-8 content within 64 KiB')
    else:
        _SKY_SKILL_FILE = None
    if args.agent_executable is not None:
        import os
        agent = Path(args.agent_executable)
        if not agent.is_absolute() or not agent.is_file() or not os.access(agent, os.X_OK):
            ap.error('--agent-executable must be an absolute executable file')
        _AGENT_REQUESTED_EXECUTABLE = args.agent_executable
        _AGENT_EXECUTABLE = str(agent.resolve())
    else:
        _AGENT_REQUESTED_EXECUTABLE = 'codex'
        _AGENT_EXECUTABLE = 'codex'
    if args.owned_fixture_text is not None and not args.check_owned_document_lifecycle:
        ap.error('--owned-fixture-text requires the owned lifecycle probe')
    resume_identity = None
    if args.resume_owned_fixture_directory is not None or args.resume_owned_fixture_identity is not None:
        if not args.check_owned_document_lifecycle or not args.resume_owned_fixture_directory or not args.resume_owned_fixture_identity:
            ap.error('resume requires the lifecycle probe, owned directory and its identity')
        try:
            resume_identity = tuple(int(v) for v in args.resume_owned_fixture_identity.split(':'))
            if len(resume_identity) != 2 or any(v < 0 for v in resume_identity):
                raise ValueError
        except ValueError:
            ap.error('resume identity must be dev:ino')
    if args.resume_owned_fixture_text is not None:
        if not args.resume_owned_fixture_directory or args.owned_fixture_text is not None:
            ap.error('--resume-owned-fixture-text requires resume identity and forbids new text input')
        from signed_owned_textedit import SignedOwnedTextEdit
        try:SignedOwnedTextEdit._text_budget(args.resume_owned_fixture_text)
        except ValueError:ap.error('resume text must be valid Unicode within the pin budget')
    if args.timeout <= 0:
        ap.error('--timeout must be positive')
    if args.reps <= 0:
        ap.error("--reps must be positive")
    if args.only == []:
        ap.error("--only requires a task name")
    unknown = set(args.only or ()) - {task.name for task in TASKS}
    if unknown:
        ap.error("unknown task: " + ", ".join(sorted(unknown)))
    tasks = [t for t in TASKS if not args.only or t.name in args.only]
    if args.list:
        for t in tasks:
            print(f"{t.name:16} {t.preview if t.preview is not None else (t.ask() if callable(t.ask) else t.ask)}")
        return 0
    if args.fixture_worker:
        from tacto.helper import InheritedHelperDriver
        if not InheritedHelperDriver.available():
            print('fixture_worker_requires_signed_launcher', file=sys.stderr)
            return 2
        from comparison_fixture_worker import serve
        return serve(sys.modules[__name__], sys.stdin, sys.stdout, task=args.fixture_worker_task)
    if (args.check_worker_boundary or args.check_worker_lifecycle) and not args.external_owned_worker:
        ap.error('worker checks require --external-owned-worker')
    if args.external_owned_worker:
        import os
        from comparison_fixture_worker import FixtureWorker
        from tacto.helper import InheritedHelperDriver
        if InheritedHelperDriver.available() or any(os.environ.get(k) for k in ('TACTO_HELPER_FD', 'TACTO_LAUNCHER', 'TACTO_HELPER_TOKEN')):
            ap.error('external controller must run outside the signed launcher')
        launcher = Path(args.external_owned_worker)
        if not launcher.is_absolute() or not launcher.is_file() or not os.access(launcher, os.X_OK):
            ap.error('worker launcher must be an absolute executable path')
        if any(value for key, value in vars(args).items() if key.startswith('check_') and key not in ('check_worker_boundary', 'check_worker_lifecycle')) or args.diagnose_owned_document_pin or args.resume_owned_fixture_directory or args.owned_fixture_text:
            ap.error('external worker mode supports no other probes')
        is_worker_check = args.check_worker_boundary or args.check_worker_lifecycle
        if not is_worker_check and (not args.only or len(args.only) != 1 or args.only[0] not in WORKER_TASKS):
            ap.error('external worker requires exactly one supported owned task')
        selected_task = 'textedit_edit_owned' if is_worker_check else args.only[0]
        worker = FixtureWorker(str(launcher), task=selected_task)
        try:
            metadata = worker.request('check')
            from tacto.process_responsibility import observe_responsibility
            _WORKER_METADATA = {'worker': metadata, 'controller': observe_responsibility(os.getpid()),
                                'agent_descendants_observed': False, 'tcc_permission_verified': False}
            if args.check_worker_boundary:
                from tacto.process_responsibility import observe_responsibility
                print(json.dumps({'scope': 'external_controller_signed_worker_metadata_only',
                                  'controller': observe_responsibility(os.getpid()),
                                  'worker': metadata, 'gui_task_executed': False,
                                  'agent_executed': False, 'comparison_eligible': False}))
                return 0
            if args.check_worker_lifecycle:
                from comparison_fixture_worker import probe_lifecycle
                from focus_witness import FocusWitness
                result = probe_lifecycle(worker, _locked, FocusWitness)
                print(json.dumps(result))
                return 0 if result['status'] == 'passed' else 2
            _FIXTURE_WORKER = worker
            # Reuse the ordinary runner; the agent is spawned only here, never
            # by the worker. Anonymous pipes are non-inheritable/close_fds.
            forwarded = ['--only', selected_task, '--reps', str(args.reps),
                         '--timeout', str(args.timeout), '--arms', *args.arms]
            if args.sky_skill_file:
                forwarded += ['--sky-skill-file', _SKY_SKILL_FILE]
            if args.agent_executable:
                forwarded += ['--agent-executable', args.agent_executable]
            return main(forwarded)
        finally:
            _FIXTURE_WORKER = None
            _WORKER_METADATA = None
            worker.close()
    from tacto.helper import InheritedHelperDriver
    if not InheritedHelperDriver.available() and _FIXTURE_WORKER is None:
        print('Use the signed launcher: tacto eval --product-comparison [options]')
        return 2
    if args.diagnose_owned_document_pin:
        reply = _driver().document_text('com.apple.TextEdit', document_path=args.diagnose_owned_document_pin)
        # Diagnostics for an explicitly selected fixture; never another body.
        print(json.dumps({k:reply.get(k) for k in ('type','error','detail','document_path','text_complete')}, ensure_ascii=False))
        if reply.get('type') == 'document_text':
            print(json.dumps({'text_length':len(reply['text']), 'empty':reply['text']==''}))
            observation = _driver().observe('com.apple.TextEdit', document_token=reply['document_token'])
            print(json.dumps({'type':observation.get('type'), 'buttons':[line for line in observation.get('tree','').splitlines() if re.match(r'^\s*\d+ Button(?:\s|$)',line)]}))
        return 0 if reply.get('type') == 'document_text' else 2
    if args.check_owned_textedit_save or args.check_owned_document_observe or args.check_owned_document_observe_escaped or args.check_owned_document_run or args.check_owned_scoped_save or args.check_owned_scoped_cli_save:
        if _locked() is not False:
            print('owned_save_environment_unavailable')
            return 2
        if args.check_owned_document_observe or args.check_owned_document_observe_escaped or args.check_owned_document_run:
            from signed_document_observe import run
        else:
            from signed_textedit_save import run
        from tacto.driver import Driver
        from tacto.native_frontmost import frontmost_pid
        def can_cleanup():
            try:return _locked() is False and frontmost_pid()!=Driver()._resolve('com.apple.TextEdit',launch=False)
            except Exception:return False
        before=source_manifest()
        if args.check_owned_scoped_cli_save:
            result=run(_driver(),can_cleanup=can_cleanup,scoped_save=True,cli_save=True)
        elif args.check_owned_scoped_save:
            result=run(_driver(),can_cleanup=can_cleanup,scoped_save=True)
        elif args.check_owned_document_run:
            result=run(_driver(),can_cleanup=can_cleanup,escaped_name=True,run_read=True)
        elif args.check_owned_document_observe_escaped:
            result=run(_driver(),can_cleanup=can_cleanup,escaped_name=True)
        else:result=run(_driver(),can_cleanup=can_cleanup)
        result.update(_source_evidence(before,source_manifest()))
        print(json.dumps(result,ensure_ascii=False))
        return 0 if result['passed'] and not result['source_changed'] else 2
    if args.check_owned_save_receipt:
        if _locked() is not False:
            print('owned_save_environment_unavailable')
            return 2
        from signed_save_writer import run
        before=source_manifest()
        try:
            result=run(_driver())
        except Exception as error:
            print('owned_save_receipt_unavailable:' + type(error).__name__)
            return 2
        result.update(_source_evidence(before,source_manifest()))
        print(json.dumps(result))
        return 0 if result['passed'] and not result['source_changed'] else 2
    if args.check_owned_document_lifecycle:
        if _locked() is not False:
            print('owned_document_lifecycle_environment_unavailable')
            return 2
        try:
            result = _owned_lifecycle_probe(args.resume_owned_fixture_directory, resume_identity, args.owned_fixture_text, args.resume_owned_fixture_text or "")
        except Exception as error:
            print('owned_document_lifecycle_unavailable:' + type(error).__name__)
            return 2
        print(json.dumps(result))
        return 0 if result['opened_state'] == 'open' and result['initial_expected_text_verified'] and result['cleanup_status'] == 'closed' and result['receiving_text_verified'] is not False and result['artifact_removed'] and not result['source_changed'] else 2
    if args.check_agent_responsibility:
        try:
            result = _live_agent_probe()
        except Exception as error:
            print('agent_responsibility_probe_unavailable:' + type(error).__name__)
            return 2
        print(json.dumps(result))
        return 0 if result['oracle_passed'] and not result['source_changed'] else 2
    if args.check_responsibility:
        try:
            result = _responsibility_probe()
        except Exception:
            print('responsibility_probe_unavailable')
            return 2
        print(json.dumps(result))
        return 0 if result['cleanup_status'] == 'completed' and all(result[k]['status'] == 'observed' for k in (
            'controller', 'owned_idle_python_child')) else 2
    if args.check_backend:
        try:
            driver = _driver()
            result = driver.list_targets()
        except Exception:
            print('signed_comparison_backend_unavailable')
            return 2
        if result.get('type') == 'error' or not isinstance(result.get('apps'), list):
            print('signed_comparison_backend_unavailable')
            return 2
        print(json.dumps({'backend':'signed_helper', 'target_count':len(result['apps']),
                          'capabilities':sorted(driver.capabilities)}))
        return 0
    locked = _locked()
    if locked is not False:
        print("화면이 잠겨 있습니다" if locked is True else "화면 세션을 확인할 수 없습니다")
        return 2
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = ROOT / "reviews" / datetime.date.today().isoformat() / f"product-comparison-{stamp}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    import os
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    print(f"results: {out}")
    results = []
    for rep in range(args.reps):
        for task in tasks:
            # Alternate which arm goes first, so neither always meets the
            # state the other left behind.
            arms = args.arms if (rep + TASKS.index(task)) % 2 == 0 else list(reversed(args.arms))
            for arm in arms:
                r = run_one(task, arm, timeout=args.timeout)
                results.append(r)
                r["rep"] = rep
                with out.open("a") as f:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
                tok = (r["usage"] or {}).get("input_tokens")
                print(f"  {r['status']:8} {arm:3} {task.name:15} {r['seconds']:6.1f}s "
                      f"tok={tok} act={len(r['activations'])} obs={str(r['observed'])[:40]}")
    if any(r["status"] in ("failed", "timeout", "invalid") for r in results):
        return 1
    if not results or any(r["status"] != "passed" or not r.get("comparison_eligible", False)
                          for r in results):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
