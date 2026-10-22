"""Tacto — reusable computer use.

Usage:
  tacto doctor            # check/guide permissions
  tacto apps              # list running apps (GUI session check)
  tacto logs [--json|--open]  # automatic access receipts (no screen/input contents)
  tacto launch <bundle-id> [--helper]  # explicitly start an installed app in background
  tacto activate <bundle-id> [--window TEXT] [--helper]  # on a person's request: bring the app (and that window) forward
  tacto open-url <URL> [--browser <bundle-id>] [--helper]  # on a person's request: open a web page as a link click does
  tacto keep-awake [--minutes N] | --stop  # opt-in: no idle display/system sleep during long remote work (no screen-saver or policy lock guarantee)
  tacto capture <bundle-id> --helper --output window.png [--window TITLE]
  tacto drag <bundle-id> --helper --from X Y --to X Y [--window TITLE]
  tacto first-run --helper --yes      # new TextEdit document, fixed text, fresh verification
  tacto document-attachments <canonical path> --helper
      (read live attachment positions/references; does not verify payload bytes)
  tacto list              # list saved flows (+ where the IR lives)
  tacto show <name>       # print a flow's IR (JSON)
  tacto compile "<intent>"        # STAGE 1: intent -> Flow IR (uses a model)
      (--verify runs the result once and lets the model fix what failed)
  tacto do "<intent>"     # STAGE 1+2+3 (add --learn to author unknown tasks)
      (--provider codex|claude|gemini|opencode|cxweb|auto selects the model provider)
      (--think [N] lets a run stop and ask when the screen disagrees; N calls.
       Unset: 0 for built-in recipes, 1 for ones promoted from a walk; --think 0 = never)
      (--strict turns off reading the request itself: only phrasings the
       patterns already know, and an honest "I did not understand" otherwise)
  tacto do "<intent>" --trace run.json   # …and record a reproducible trace
  tacto run <flow.json> --journal-dir /absolute/private/run-directory
      (do/run/replay: reserve input before dispatch; reuse this directory on
       restart to refuse duplicate input. This does not automatically resume.)
  tacto web-upload /absolute/private/config.json --scope name --check
      (register immutable browser files; omit --check to review/approve and
       verify independent receiving evidence; supports --journal-dir and --report)
  tacto parameterize <flow> -p 12=a -p 34=b --digits   # concrete IR -> template
  tacto run <flow.json>   # execute a Flow IR file directly (no intent step)
      (--helper routes through the signed Swift helper that holds the permissions)
      (signed macOS tasks use the existing Tacto menu; configure the task banner in app Settings)
  tacto replay <name>     # replay a saved flow by name
      (run/replay: --think N lets a step that declared a surprise ask N times;
       unset, a recipe promoted from a walk may ask once, a written one never)
  tacto candidates        # what a screen-loop run would like to
                                      # become a recipe, and why it is not yet
  tacto promote <name> [-p 25=n]
                                      # make one a recipe, if its gate is clean;
                                      # -p turns a value from the request into an
                                      # input, and the recipe then answers the
                                      # same sentence with any value (tacto do "…")
      (a model reviews the walk once for judgment points; --no-decisions skips it)

  A step the policy will not run on its own authority (saving a file, sending a
  message) asks first. Answer at the prompt, or pass --yes for scripts; with no
  terminal to ask at, the answer is no.

Things it already knows how to do (tacto do "…"):
  7 더하기 8 해줘                  계산기 — 사칙연산 전부
  다운로드 폴더 열어줘              Finder
  새 문서 만들어줘                  TextEdit
  “회의 준비” 라고 써줘             문서에 입력 (따옴표 안이 입력될 내용)
  굵게 해줘                        전체 선택 + 굵게
  “보고서.rtf” 로 저장해줘          이름을 정해 저장 — 확인을 묻습니다
"""

# Detect accidental co-installation with the unrelated PyPI tacto simulator.
import tacto as _runtime_package
if getattr(_runtime_package, "__tacto_runtime__", False) is not True:
    raise ImportError("Tacto runtime namespace conflict; install tacto-runtime in an isolated environment")

import json
import re
import sys
from contextlib import ExitStack
from contextvars import ContextVar

from .agent import Agent
from .store import FlowStore
from .helper import HelperUnavailable, HelperLeaseRefused

_execution_journal = ContextVar('tacto_execution_journal', default=None)


class _JournalUnavailable(Exception):
    pass


def _journal_from(argv):
    """Strip one explicit run directory before intent/recipe processing."""
    import os
    directory, rest, i = None, [], 0
    while i < len(argv):
        token = argv[i]
        if token == '--journal-dir' or token.startswith('--journal-dir='):
            if directory is not None:
                raise ValueError('--journal-dir may be supplied only once')
            if token == '--journal-dir':
                i += 1
                if i >= len(argv):
                    raise ValueError('--journal-dir requires an absolute path')
                value = argv[i]
            else:
                value = token.split('=', 1)[1]
            if not os.path.isabs(value) or '\0' in value:
                raise ValueError('--journal-dir requires an absolute path')
            directory = value
        else:
            rest.append(token)
        i += 1
    return directory, rest




def _make_driver(argv):
    """--helper uses the signed Swift helper (which holds the permissions);
    otherwise the in-process Python driver. Same L2 protocol either way."""
    if sys.platform == "win32":
        # UI Automation needs no OS permission grant, so there is no signed
        # helper to hold one; --helper cannot be honoured and is refused.
        if "--helper" in argv:
            raise SystemExit("--helper is macOS-only; on Windows run without it")
        from .windows_driver import WindowsDriver
        driver = WindowsDriver()
    elif "--helper" in argv:
        # Fail closed: --helper asks to run with the permission boundary in
        # place, so falling back to the in-process driver would run the task
        # with exactly the privileges the flag exists to avoid.
        from .helper import InheritedHelperDriver
        driver = InheritedHelperDriver()
    else:
        from .driver import Driver
        driver = Driver()
    return _with_journal(driver)


def _with_journal(driver):
    from .access_log import current, AccessLoggingDriver
    log = current.get()
    if log is not None and not isinstance(driver, AccessLoggingDriver):
        driver = AccessLoggingDriver(driver, log)
    scope = _execution_journal.get()
    if scope is None:
        return driver
    from .dispatch_journal import DurableDispatchDriver
    directory, stack = scope
    try:
        return stack.enter_context(DurableDispatchDriver(driver, directory))
    except (OSError, ValueError):
        raise _JournalUnavailable from None


def _confirmer(argv):
    """How a consequential step gets the user's yes.

    A gate nobody can answer is the same as a task that cannot run, which is
    what saving a file did before this existed. `--yes` is for scripts, and
    when there is no terminal to ask at, the answer is no — silence is not
    consent.
    """
    import sys as _sys
    if "--yes" in argv or "-y" in argv:
        return lambda what: True
    if not _sys.stdin.isatty():
        return lambda what: False
    def ask(what: str) -> bool:
        print(f"\n  ⚠  {what}")
        try:
            return input("     진행할까요? [y/N] ").strip().lower() in ("y", "yes", "네")
        except (EOFError, KeyboardInterrupt):
            return False
    return ask


def _provider_from(argv):
    """Keep provider configuration out of the user's task text."""
    provider, rest, seen = "auto", [], False
    i = 0
    while i < len(argv):
        token = argv[i]
        if token == "--provider" or token.startswith("--provider="):
            if seen:
                raise ValueError("--provider may be supplied only once")
            seen = True
            if token == "--provider":
                i += 1
                if i >= len(argv):
                    raise ValueError("--provider requires a value")
                provider = argv[i]
            else:
                provider = token.split("=", 1)[1]
            if provider not in ("auto", "claude", "codex", "gemini", "opencode", "cxweb"):
                raise ValueError(f"unsupported provider: {provider!r}")
        else:
            rest.append(token)
        i += 1
    return provider, rest


def _general_limits_from(argv):
    flags = {"--general-timeout": "timeout_seconds", "--max-screens": "max_screens",
             "--prompt-budget": "budget", "--max-pages": "max_pages"}
    limits = {"timeout_seconds": None, "max_screens": 8, "budget": 16000, "max_pages": 8}
    rest, seen, i = [], set(), 0
    while i < len(argv):
        token = argv[i]
        name, equals, value = token.partition("=")
        if name not in flags:
            rest.append(token)
        else:
            if name in seen:
                raise ValueError(f"{name} may be supplied only once")
            seen.add(name)
            if not equals:
                i += 1
                if i >= len(argv):
                    raise ValueError(f"{name} requires a positive integer")
                value = argv[i]
            if not value.isascii() or not value.isdecimal() or int(value) <= 0:
                raise ValueError(f"{name} requires a positive integer")
            limits[flags[name]] = int(value)
        i += 1
    return limits, rest


class _Refused(Exception):
    pass


def _promote_decisions(rec: dict, flow, provider: str, taken_names=frozenset()):
    """The recipe with the walk's judgment points turned into decide steps."""
    from .contract import Envelope
    from .promote import (Candidate, checked_decisions, decision_prompt,
                          replays_the_walk, with_decisions)
    if not isinstance(rec.get("candidate"), dict):
        return flow
    c = Candidate(**{k: v for k, v in rec["candidate"].items() if k != "envelope"},
                  envelope=Envelope.minimal())
    # Only a click made while something was visible to base it on can have
    # been a judgment; a walk with none is not worth a model call.
    if not any(a.get("do") == "click" and a.get("screen_reads") for a in c.actions):
        return flow
    from .authoring import _extract_json, reach_model
    print("  🧠 판단이 필요했던 단계가 있는지 기록을 검토합니다…")
    try:
        answer = _extract_json(reach_model(decision_prompt(c), provider, expect_json=True))
    except Exception as exc:
        raise _Refused(f"기록을 검토하지 못했습니다 ({type(exc).__name__}) — "
                       "판단 단계 없이 만들려면 --no-decisions") from None
    proposed = answer.get("decisions") if isinstance(answer, dict) else None
    decisions, refused = checked_decisions(c, proposed, rec.get("request") or c.goal)
    for w in refused:
        print(f"    · 받지 않은 제안: {w}")
    if not decisions:
        print("    판단 단계 없음 — 기록대로 재생합니다")
        return flow
    # A branch is filed under the answer the walk had. If the decider that
    # will answer at replay, shown what the walk saw, would not say the same,
    # the branch would replay for the wrong cases — so it is not kept.
    from .judge import model_decider
    decider = model_decider(provider=provider)
    for d in [x for x in decisions if x.get("scope") == "rest"]:
        said = decider(d["question"], d["choices"], {"seen_case": d["basis"].get("value")})
        if said != d["taken"]:
            print(f"    · 받지 않은 제안: {d['step']}번 — 기록의 화면을 보고 다시 물었더니 "
                  f"'{said}' 라고 합니다 (기록은 '{d['taken']}')")
            decisions = [x for x in decisions if x is not d]
    if not decisions:
        print("    판단 단계 없음 — 기록대로 재생합니다")
        return flow
    # The recipe may already read some typed values afresh (lifted), which
    # put steps in front of the walk's; decisions are counted in walk steps.
    from .promote import lift_screen_values, shifted, to_flow
    _, lifted = lift_screen_values(c, to_flow(c, "walk"), rec.get("request") or c.goal)
    decisions = shifted(decisions, lifted)
    decided = with_decisions(flow, decisions, goal=c.goal, taken_names=taken_names)
    if not replays_the_walk(decided, flow, decisions):
        raise _Refused("판단 단계를 넣은 레시피가 원래 실행을 재현하지 않습니다 — 만들지 않았습니다")
    for d in decisions:
        if d.get("scope") == "rest":
            print(f"    · {d['step']}번 단계부터 경우가 나뉩니다: {d['question']}"
                  f"  ({' / '.join(d['choices'])}) — '{d['taken']}' 은 이번에 한 대로, "
                  "나머지는 처음 만날 때 화면을 보며 하고 배웁니다")
        else:
            print(f"    · {d['step']}번 단계는 재생할 때 묻습니다: {d['question']}"
                  f"  ({' / '.join(d['choices'])})")
    return decided


def _load_recipe(name: str):
    """A recipe by name — a built-in always from the code, never a stale copy.

    `tacto do` already worked this way; `tacto replay` and `tacto show` read the
    saved copy, and the one in flows/ for textedit_write_note dated from
    2026-09-16 and declared `write_ephemeral` where the code now says
    `write_persistent`, so every replay was refused as a contract mismatch —
    the very recipe the skill points agents to (2026-10-07).
    """
    from .library import builders
    build = builders().get(name)
    return build() if build is not None else FlowStore().load(name)


from .document_scope import document_observation as _document_observation


def document_window_view(tree: str, path: str) -> str | None:
    """Only the window whose `Document:` is exactly this file, or None.

    "The document at this path" is not "the focused window": the person may be
    working in another document of the same app (2026-10-10, an agent could
    not tell which TextEdit window held the evaluator's file). Indices are the
    observation's own, so a recipe can act on what is printed; select inside
    it with `"ancestor": "<the same path>"`.
    """
    from .flow import Selector
    import unicodedata
    want = unicodedata.normalize('NFC',path)
    blocks, current = [], None
    for line in tree.splitlines():
        if line and not line[0].isspace():
            current = [line]
            blocks.append(current)
        elif current is not None:
            current.append(line)
    found = [b for b in blocks
             if Selector._attr(b[0], "Document") == want]
    return "\n".join(found[0]) if len(found) == 1 else None


def focused_window_view(tree: str) -> str:
    """The window being worked on, and only the titles of the others.

    An agent reads this on every step. Printing every window cost Tacto twice
    Sky's tokens on the same TextEdit task — 182 lines across 4 windows per
    look, three of them unrelated documents (2026-10-07). Element indices are
    unchanged, so nothing that refers to them breaks; `--all-windows` prints
    everything.
    """
    blocks, current = [], None
    for line in tree.splitlines():
        if line and not line[0].isspace():
            current = [line]
            blocks.append(current)
        elif current is not None:
            current.append(line)
    if len(blocks) <= 1:
        return tree
    from .document_label import window_flag, window_attributes
    focused = [b for b in blocks if window_flag(b[0],"focused")]
    # Main and focused windows may both be marked (e.g. a parent and its dialog).
    # Without a unique focus, hiding every other block conceals valid controls.
    if len(focused) != 1:
        return tree
    keep = focused[0]
    others = [b[0] for b in blocks if b is not keep]
    names = []
    for head in others:
        attrs=window_attributes(head)
        if attrs is not None:
            names.append(attrs.get("Title","?"))
            continue
        m = re.search(r"Title: (.*?)(?:, \w+:|\s*\[|$)", head)
        names.append(m.group(1) if m else head.split()[1] if len(head.split()) > 1 else "?")
    return ("\n".join(keep) + f"\n… 다른 창 {len(others)}개: " + ", ".join(names[:8])
            + (" …" if len(names) > 8 else "") + "  (모두 보려면 --all-windows)")


def _same_steps(a, b) -> bool:
    """Do two flows run the same actions against the same elements?"""
    def shape(flow):
        return [(st.action, json.dumps(st.selector.__dict__ if st.selector else None,
                                       sort_keys=True, default=str),
                 json.dumps(st.params, sort_keys=True, default=str)) for st in flow.steps]
    return shape(a) == shape(b)


def _params_from(argv):
    """Collect -p key=value pairs into a dict (and return the rest)."""
    out, rest = {}, []
    i = 0
    while i < len(argv):
        if argv[i] in ("-p", "--param") and i + 1 < len(argv):
            k, _, v = argv[i + 1].partition("=")
            out[k] = v
            i += 2
        else:
            rest.append(argv[i]); i += 1
    return out, rest


def _indicator_for(target, argv):
    """Show a 'being controlled' outline on the target window during a run,
    unless the caller opted out. Never blocks the task if it can't draw."""
    if "--no-indicator" in argv or sys.platform == "win32":
        # Windows has no indicator yet; the macOS overlay must not load there.
        from contextlib import nullcontext
        return nullcontext()
    from .driver import Driver
    from .overlay import Indicator
    try:
        pid = Driver()._resolve(target, launch=False)
    except Exception:
        pid = None
    return Indicator(pid, notify=lambda m: print(f"  {m}"))


def _emit_pin_diagnostic(reason):
    from .pin_diagnostic import format_pin_diagnostic
    line = format_pin_diagnostic(reason)
    if line is not None:print(line)


def _run_flow(flow, argv, walked: bool = False, *, _driver=None, _confirm=None, _on_result=None, _auto_params=None, _replay_seed=None, _replay_secrets=(), _replay_sensitive_names=frozenset()):
    """All direct replay commands use the same consent and trace path.

    And the same judgment as `tacto do`: a recipe promoted from a walk may ask
    once where a step declared a surprise, `--think N` sets how often, and
    `--think 0` turns it off. Before this, the same recipe re-aimed its way
    past Calculator's `Clear`/`All Clear` under `do` and failed under `replay`.
    """
    if '--auto-repair' in argv or '--max-repairs' in argv or '--repair-timeout' in argv:
        from .auto_repair import parse_options, RepairRefused
        try:clean,automatic=parse_options(argv)
        except RepairRefused as exc:
            print(str(exc));return 2
        try:flow.bind(**(_auto_params or {}))
        except (ValueError,TypeError,KeyError,ArithmeticError):
            print('invalid_parameters: provide defaults in params or -p name=value');return 2
        from .flow_batch import BatchDriver
        driver=_driver if isinstance(_driver,BatchDriver) else BatchDriver(_driver or _make_driver(clean))
        try:
            code,_=automatic.run(flow,_auto_params or {},clean,driver=driver,run_flow=_run_flow,emit=print)
            return code
        finally:
            if _driver is None:driver.close()

    from .flow import replay
    import time
    timing_started = time.monotonic() if "--timings" in argv else None
    think = 1 if walked else 0
    if "--think" in argv:
        k = argv.index("--think")
        nxt = argv[k + 1] if k + 1 < len(argv) else ""
        think = int(nxt) if nxt.isdigit() else 1
    document = None
    if "--document" in argv:
        k=argv.index("--document")
        if argv.count("--document")!=1 or k+1>=len(argv) or not argv[k+1].startswith("/") or "--all-windows" in argv:
            print("--document requires one canonical absolute path")
            return 2
        document=argv[k+1]
        if flow.target!='com.apple.TextEdit':
            print("document_scope_unavailable: --document requires com.apple.TextEdit")
            return 2
    repair_path = None
    if '--repair-packet' in argv:
        k=argv.index('--repair-packet')
        import os
        if argv.count('--repair-packet')!=1 or k+1>=len(argv) or not os.path.isabs(argv[k+1]) or os.path.lexists(argv[k+1]):
            print('repair_packet: use one absolute new output path');return 2
        repair_path=argv[k+1]
    driver = _driver if _driver is not None else _make_driver(argv)
    timed_driver = None
    if document is not None:
        from .document_scope import DocumentScopedDriver, DocumentScopeUnavailable
        try:driver=DocumentScopedDriver(driver,flow.target,document)
        except DocumentScopeUnavailable as exc:
            _emit_pin_diagnostic(exc.pin_reason)
            print(f"document_scope_unavailable: {exc}")
            return 1
    if timing_started is not None:
        from .runtime_timing import TimedDriver
        timed_driver = TimedDriver(driver)
        driver = timed_driver
    trace_path = None
    tracer = None
    if "--trace" in argv:
        k = argv.index("--trace")
        if k + 1 == len(argv) or argv[k + 1].startswith("--"):
            print("--trace requires a path")
            return 2
        trace_path = argv[k + 1]
        import os
        if os.path.exists(trace_path):
            try:
                with open(trace_path, encoding="utf-8") as fh:
                    if "steps" in json.load(fh):
                        print("트레이스로 레시피를 덮어쓸 수 없습니다")
                        return 2
            except (ValueError, TypeError):
                pass
        from .trace import Trace, TracingDriver
        tracer = TracingDriver(driver, Trace(flow=flow.name, target=flow.target))
        driver = tracer
    agent = Agent(driver=driver)
    confirmer = _confirmer(argv) if _confirm is None else _confirm
    approvals, blocked = agent.authorize(flow, ask=confirmer)
    if blocked:
        print("\n".join(blocked))
        return 1
    try:
        hooks = agent.commit_adapter_hooks(agent.commit_adapter_for(flow))
    except Exception as exc:
        print(f"저장 결과 검증 준비 실패: {exc}")
        return 1
    if _on_result is not None:
        hooks = {**hooks, '_retain_continuation':True}
    if _replay_seed is not None:
        hooks = {**hooks, '_seed':_replay_seed, '_seed_secrets':_replay_secrets,
                           '_seed_sensitive_names':_replay_sensitive_names}
    # A plain replay stays model-free. Only a recipe that PLANS a judgment
    # gets something to ask, and only for those steps.
    decider = None
    if any(st.action == "decide" for st in flow.steps):
        from .judge import model_decider
        try:
            provider, _ = _provider_from(argv)
        except ValueError as exc:
            print(str(exc))
            return 2
        decider = model_decider(provider=provider, narrate=lambda s: print(f"  {s}"))
    judge = None
    if think:
        from .judge import model_judge
        try:
            provider, _ = _provider_from(argv)
        except ValueError as exc:
            print(str(exc))
            return 2
        judge = model_judge(target=flow.target, provider=provider,
                            narrate=lambda s: print(f"  {s}"))
    with _indicator_for(flow.target, argv):
        replay_started = time.monotonic()
        try:
            result = replay(driver, flow, approvals=approvals,
                            narrate=lambda text: print(f"  … {text}"), decide=decider,
                            confirm=confirmer, escalate=judge, budget=think, **hooks)
        finally:
            if timed_driver is not None:
                report = timed_driver.snapshot()
                report["replay_seconds"] = round(time.monotonic() - replay_started, 6)
                report["run_flow_seconds"] = round(time.monotonic() - timing_started, 6)
                print("TACTO_TIMINGS " + json.dumps(report, separators=(',', ':')), file=sys.stderr)
    if _on_result is not None:
        _on_result(result)
    if repair_path is not None:
        from .repair_packet import packet_for
        packet=packet_for(flow,result)
        if packet is not None:
            from .storage import write_private
            try:
                write_private(repair_path,json.dumps(packet,ensure_ascii=False,separators=(',',':')),overwrite=False)
                print('TACTO_REPAIR_PACKET ' + repair_path)
            except OSError:
                print('repair_packet_write_failed; task remains failed, do not resend')
    if tracer:
        print(f"트레이스 저장: {tracer.trace.save(trace_path)}")
    from .preparation_diagnostic import MARKER, preparation_diagnostic
    for candidate in getattr(result, 'preparation_diagnostics', ()):
        diagnostic = preparation_diagnostic(candidate)
        if diagnostic is not None:
            print(MARKER + json.dumps(diagnostic, ensure_ascii=True, separators=(',', ':')))
    if result.ok:
        value = result.log[-1].get("result") if result.log else None
        # A recipe that ANSWERS says its answer: the run's closing entry holds
        # every named capture (the last read of each), already redacted.
        captured = result.log[-1].get("captured") if result.log else None
        if not isinstance(captured, dict):
            captured = {}
        shown = value or (next(iter(captured.values())) if len(captured) == 1
                          else "; ".join(f"{k}: {v}" for k, v in captured.items()))
        print("done" + (f" — 결과: {shown}" if shown else ""))
    else:
        last = result.log[-1] if result.log else {}
        saved_state = any(e.get('saved_state_verified') for e in getattr(result,'events',()))
        if last.get('detail') == 'saved_state_does_not_confirm_dispatch':
            print('unverified — 저장된 파일 내용은 확인했지만 저장 요청의 실행 여부는 확인하지 못했습니다.\n'
                  f'  detail: {last}')
        elif last.get("stage") in ("verify_observe", "recovery_observe", "visual_step") and saved_state:
            print('failed — 저장된 파일 내용은 확인했지만 이후 화면을 읽지 못해 작업을 중단했습니다.\n'
                  f'  detail: {last}')
        elif last.get("stage") in ("verify_observe", "recovery_observe", "visual_step") and result.commit_evidence:
            print(f"failed — 변경 {len(result.commit_evidence)}건은 영수증으로 확인했지만, "
                  "이후 화면을 읽지 못해 작업을 중단했습니다.\n"
                  f"  detail: {last}")
        elif last.get("error") == "commit_unverified" and last.get("effect") == "unknown":
            # Missing receipt says nothing about whether a file exists, which
            # action wrote it, or whether this was a Save at all. Persistent
            # set_value and other consequential actions use this branch too.
            print("unverified — 변경 완료를 확인할 호스트 영수증이 없습니다. 실행 효과는 미확정입니다.\n"
                  "  같은 동작을 다시 실행하지 말고 해당 대상의 현재 상태를 읽어 확인하세요.\n"
                  f"  detail: {last}")
        else:
            print(f"failed: {last}")
    return 0 if result.ok else 1


def _main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("--help", "-h", "help"):
        print(__doc__)
        return 0
    cmd, *rest = argv
    from .command_help import command_help
    help_text = command_help(cmd, rest)
    if help_text is not None:
        print(help_text)
        return 0

    # Wheels do not include source-only evaluator fixtures. Report that before
    # importing native GUI dependencies, which an isolated install may lack.
    if cmd == "eval":
        import os as _os
        evaluator = _os.path.join(_os.path.dirname(_os.path.dirname(
            _os.path.abspath(__file__))), "examples", "eval.py")
        if not _os.path.isfile(evaluator):
            print("평가 fixture는 wheel에 포함되지 않습니다. 소스 checkout에서 "
                  "examples/eval.py를 실행하세요.")
            return 2

    # Everything that touches the screen, stopped before it can misreport a
    # sandbox as a missing permission. Reading and writing recipes, and
    # learning one, still work in a sandbox.
    if cmd in ("doctor", "apps", "launch", "activate", "open-url", "first-run", "observe", "session", "batch", "document-attachments", "capture", "drag", "vroid-drag", "vroid-focus", "run", "replay", "do", "eval"):
        if sys.platform == "win32":
            if cmd in ("doctor", "launch", "activate", "open-url", "first-run", "document-attachments", "capture", "drag",
                       "vroid-drag", "vroid-focus"):
                print(f"{cmd} is macOS-only; see docs/windows.md")
                return 2
            from .windows_driver import keep_awake
            from .windows_control import control
            # Esc stops the run; the notice never takes focus. No prompt.
            notice = "--no-indicator" not in rest
            # Consumed here, so every screen command accepts it as on `do`.
            rest = [arg for arg in rest if arg != "--no-indicator"]
            with keep_awake(), control(notice=notice):
                return _dispatch(cmd, rest)
        from .session import SANDBOXED, sandboxed
        if sandboxed():
            print(SANDBOXED)
            return 3
        # Hold the display awake for as long as this command runs. Only `do`
        # did, but an agent drives Tacto one command at a time — observe, run,
        # replay — and with this Mac's 3-minute screensaver lock the screen
        # locked in the middle of its work (2026-09-28). Release the assertion
        # when this invocation ends, even when main is called in a long-lived
        # process or command dispatch raises. -w also covers abrupt exit.
        from .session import KeepAwake
        with KeepAwake():
            return _dispatch(cmd, rest)

    return _dispatch(cmd, rest)


def _dispatch(cmd, rest):
    try:
        directory, rest = _journal_from(rest)
    except ValueError as exc:
        print(str(exc))
        return 2
    if directory is None:
        # Nested `do <IR file>` dispatch retains its enclosing run's barrier.
        return _dispatch_command(cmd, rest)
    if cmd not in ('do', 'run', 'replay', 'web-upload'):
        print('--journal-dir is supported only for do/run/replay/web-upload')
        return 2
    with ExitStack() as stack:
        token = _execution_journal.set((directory, stack))
        try:
            return _dispatch_command(cmd, rest)
        except _JournalUnavailable:
            print('dispatch_journal_unavailable — private owned journal directory required')
            return 2
        finally:
            _execution_journal.reset(token)


def _dispatch_command(cmd, rest):
    from .command_help import command_help
    help_text = command_help(cmd, rest)
    if help_text is not None:
        print(help_text)
        return 0
    if cmd == 'logs':
        from .access_log import recent_logs, private_directory, log_directory
        if rest not in ([], ['--json'], ['--open']):
            print('usage: tacto logs [--json|--open]')
            return 2
        try:
            if rest == ['--open']:
                from AppKit import NSWorkspace
                from Foundation import NSURL
                folder = private_directory(log_directory())
                return 0 if NSWorkspace.sharedWorkspace().openURL_(NSURL.fileURLWithPath_(str(folder))) else 1
            report = recent_logs()
        except (OSError, ImportError):
            print('access_log_unavailable')
            return 1
        if rest == ['--json']:
            print(json.dumps(report, ensure_ascii=False))
        else:
            print(report['directory'])
            for row in report['files']:
                print(row['name'])
        return 0

    if cmd == 'launch':
        targets = [value for value in rest if value != '--helper']
        if len(targets) != 1 or rest.count('--helper') > 1:
            print('usage: launch <bundle-id> [--helper]')
            return 2
        from .app_launch import launch_app
        result = launch_app(targets[0])
        from .access_log import current
        if current.get() is not None:
            current.get().task_result(result)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get('type') == 'app_launch' else 1

    if cmd == 'keep-awake':
        # Opt-in: keep the screen from idle-locking during long remote work.
        if sys.platform != 'darwin':
            print('keep-awake is macOS-only; see docs/windows.md')
            return 2
        from .session import keep_awake_for, stop_keep_awake, KEEP_AWAKE_MAX_MINUTES
        usage = f'usage: keep-awake [--minutes 1..{KEEP_AWAKE_MAX_MINUTES}] | keep-awake --stop'
        if rest == ['--stop']:
            stopped = stop_keep_awake()
            print(json.dumps(stopped))
            return 1 if stopped.get('unverified') else 0
        minutes = 60
        if rest:
            if len(rest) != 2 or rest[0] != '--minutes' or not rest[1].isdigit():
                print(usage)
                return 2
            minutes = int(rest[1])
        try:
            print(json.dumps(keep_awake_for(minutes)))
        except ValueError:
            print(usage)
            return 2
        except (RuntimeError, OSError):
            print(json.dumps({'type': 'error', 'error': 'keep_awake_unverified', 'effect': 'not_started',
                              'detail': 'the previous holder could not be checked; run keep-awake --stop again'}))
            return 1
        return 0

    if cmd == 'open-url':
        # Only on a person's request: the browser comes to the front.
        args = [value for value in rest if value != '--helper']
        browser = None
        if '--browser' in args:
            at = args.index('--browser')
            browser = args[at + 1] if at + 1 < len(args) else None
            args = args[:at] + args[at + 2:]
        if len(args) != 1 or rest.count('--helper') > 1 or ('--browser' in rest and browser is None):
            print('usage: open-url <http(s) URL> [--browser <bundle-id>] [--helper]  (--helper confirms the page loaded)')
            return 2
        from .open_url import open_url, DEFAULT_BROWSER
        driver = None
        if '--helper' in rest:
            from .helper import HelperUnavailable, HelperLeaseRefused
            try:
                driver = _make_driver(rest)
            except (HelperUnavailable, OSError, ValueError) as exc:
                if isinstance(exc, HelperLeaseRefused):
                    raise
                print(json.dumps({'type': 'error', 'error': 'helper_unavailable', 'effect': 'not_started',
                                  'hint': 'tacto doctor --check'}))
                return 1
        result = open_url(args[0], browser=browser or DEFAULT_BROWSER, driver=driver)
        from .access_log import current
        if current.get() is not None:
            current.get().task_result({k: v for k, v in result.items() if k != 'url'})
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get('type') == 'url_opened' else 1

    if cmd == 'activate':
        # Only on a person's request: an agent never takes the foreground on
        # its own (skill/SKILL.md). --window needs the signed helper's AX access.
        args = [value for value in rest if value != '--helper']
        window = None
        if '--window' in args:
            at = args.index('--window')
            window = args[at + 1] if at + 1 < len(args) else None
            args = args[:at] + args[at + 2:]
        if len(args) != 1 or rest.count('--helper') > 1 or (window is not None and '--helper' not in rest) \
                or ('--window' in rest and window is None):
            print('usage: activate <bundle-id> [--window <part of its title>] [--helper]  (--window needs --helper)')
            return 2
        from .app_launch import activate_app
        driver = None
        if window is not None or '--helper' in rest:
            # With the helper the result also says a window is showing.
            from .helper import HelperUnavailable, HelperLeaseRefused
            try:
                driver = _make_driver(rest)
            except (HelperUnavailable, OSError, ValueError) as exc:
                if isinstance(exc, HelperLeaseRefused):
                    raise
                print(json.dumps({'type': 'error', 'error': 'helper_unavailable', 'target': args[0],
                                  'effect': 'not_started', 'hint': 'tacto doctor --check'}))
                return 1
        result = activate_app(args[0], window=window, driver=driver)
        from .access_log import current
        if current.get() is not None:
            current.get().task_result(result)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get('type') == 'app_activation' and result.get('window_verified', True) else 1

    if cmd == 'first-run':
        if sorted(rest) != ['--helper', '--yes']:
            print('usage: first-run --helper --yes (creates one document and writes the fixed welcome text)')
            return 2
        from .first_run import run_first_task
        from .helper import HelperUnavailable, HelperLeaseRefused
        try:
            driver = _make_driver(rest)
        except (HelperUnavailable, OSError, ValueError) as exc:
            if isinstance(exc, HelperLeaseRefused):
                raise
            result = dict(type='first_run', status='failed', stage='connect',
                          error='helper_unavailable', effect='not_started',
                          model_calls=0, save_command_sent=False, repeat_write=False,
                          hint='tacto doctor --check')
            from .access_log import current
            if current.get() is not None:
                current.get().task_result(result)
            print(json.dumps(result))
            return 1
        try:
            result = run_first_task(driver)
        finally:
            driver.close()
        from .access_log import current
        if current.get() is not None:
            current.get().task_result(result)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get('status') == 'text_verified' else 1

    if cmd == 'repair':
        from .repair_packet import main as repair_main
        return repair_main(rest)

    if cmd == 'batch':
        from .flow_batch import run_batch
        return run_batch(rest, make_driver=_make_driver, run_flow=_run_flow,
                         stdin=sys.stdin, emit=lambda text:print(text,flush=True),
                         load_recipe=_load_recipe)

    if cmd == 'session':
        from .flow_session import run_session
        return run_session(rest, make_driver=_make_driver, run_flow=_run_flow,
                           stdin=sys.stdin, emit=lambda text: print(text, flush=True))

    if cmd == 'web-upload':
        from .web_upload_cli import main as upload_main
        return upload_main(rest, wrap_driver=_with_journal)

    if cmd == "doctor":
        from . import onboard
        ok = onboard.run(interactive="--check" not in rest)
        return 0 if ok else 1

    if cmd == "eval":
        # Run the suite through this process, so the helper scenarios
        # see the signed launcher in their ancestry.
        import runpy, os as _os
        root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
        product = '--product-comparison' in rest
        if rest.count('--product-comparison') > 1:
            print('--product-comparison may be supplied only once')
            return 2
        evaluator = _os.path.join(root, "examples", "product_comparison.py" if product else "eval.py")
        if not _os.path.isfile(evaluator):
            print("평가 fixture는 wheel에 포함되지 않습니다. 소스 checkout에서 "
                  "examples/eval.py를 실행하세요.")
            return 2
        # runpy keeps OUR argv, and the suite reads it to narrow the run — so
        # hand it the arguments meant for it, not the ones meant for us.
        argv = sys.argv
        sys.argv = [_os.path.basename(evaluator), *([r for r in rest if r != '--product-comparison'] if product else rest)]
        try:
            runpy.run_path(evaluator, run_name="__main__")
        finally:
            sys.argv = argv
        return 0

    if cmd == "apps":
        r = _make_driver(rest).list_targets()
        if r.get("type") == "error":
            print(f"{r.get('error')}: {r.get('detail', '')}")
            return 1
        if not r["apps"]:
            print("화면(GUI) 세션에 연결돼 있지 않습니다 — tmux 밖에서 실행하거나 "
                  "reattach-to-user-namespace 를 쓰세요.")
            return 1
        for a in sorted(r["apps"], key=lambda x: x["displayName"]):
            print(f"  {a['id']:40} {a['displayName']}")
        return 0

    if cmd == "observe":
        app = "<app.exe | pid:N>" if sys.platform == "win32" else "<bundle-id>"
        usage = (f"usage: observe {app} [--helper] [--json] [--all-windows | --document <absolute path>]"
                 " [--reveal <exact label>]...")
        structured = "--json" in rest
        reveal = []
        while "--reveal" in rest:
            # Each one an exact name to look for past the per-container cap.
            k = rest.index("--reveal")
            if k + 1 >= len(rest):
                print(usage)
                return 2
            reveal.append(rest[k + 1])
            rest = rest[:k] + rest[k + 2:]
        from .protocol import reveal_problem
        if reveal and (reveal_problem(reveal) or "--document" in rest):
            print(usage)
            return 2
        if rest.count("--json") > 1:
            print(usage)
            return 2
        document = None
        if "--document" in rest:
            k = rest.index("--document")
            if rest.count("--document") != 1 or k + 1 >= len(rest) or "--all-windows" in rest:
                print(usage)
                return 2
            document = rest[k + 1]
            rest = rest[:k] + rest[k + 2:]
            if not document.startswith("/"):
                print(usage)
                return 2
        targets = [value for value in rest if value not in ("--helper", "--all-windows", "--json")]
        if len(targets) != 1 or targets[0].startswith("-"):
            print(usage)
            return 2
        if document is not None and targets[0]!='com.apple.TextEdit':
            print('document_scope_unavailable: --document requires com.apple.TextEdit')
            return 2
        driver=_make_driver(rest)
        o = (_document_observation(driver,targets[0],document) if document is not None
             else driver.observe(targets[0], "tree_full", reveal=reveal) if reveal
             else driver.observe(targets[0], "tree_full"))
        if structured:
            try:
                encoded=json.dumps(o,ensure_ascii=False,allow_nan=False)
            except (TypeError,ValueError,OverflowError):
                print('{"type":"error","error":"observation_serialization_failed"}')
                return 1
            print(encoded)
            return 0 if o.get("type") == "observation" else 1
        if o["type"] != "observation":
            if document is not None:_emit_pin_diagnostic(o.get('pin_reason_code'))
            reason = o.get("reason_code") if document is not None else o.get("reason")
            suffix = f" (reason={reason})" if reason else ""
            if document is not None and o.get('pin_reason_code'):
                suffix += f" (pin_reason={o['pin_reason_code']})"
            print(f"{o.get('error')}{suffix}: {o.get('detail','')}")
            return 1
        if document is not None:
            print(o['tree'])
            return 0
        print(o["tree"] if "--all-windows" in rest else focused_window_view(o["tree"]))
        return 0

    if cmd == "vroid-focus":
        from .vroid_focus import main as focus_main
        return focus_main(rest, _make_driver(rest))

    if cmd == "capture":
        from .native_drag import capture_main
        return capture_main(rest, _make_driver(rest))
    if cmd == "drag":
        from .native_drag import main as drag_main
        return drag_main(rest, _make_driver(rest))
    if cmd == "vroid-drag":
        from .vroid_drag import main as drag_main
        return drag_main(rest, _make_driver(rest))

    if cmd == "document-attachments":
        if len(rest) != 2 or rest.count('--helper') != 1:
            print('usage: document-attachments <canonical TextEdit document path> --helper')
            return 2
        path = next(value for value in rest if value != '--helper')
        try:
            from .native_document import validate_document_selection_path
            validate_document_selection_path(path)
        except (ValueError, TypeError):
            print('{"type":"error","error":"invalid_document_selector"}')
            return 2
        driver = _make_driver(rest)
        text = driver.document_text('com.apple.TextEdit', document_path=path, max_utf16_units=100000)
        unavailable = {'type':'error','error':'document_attachment_metadata_unavailable','effect':'not_started'}
        if text.get('type') != 'document_text' or text.get('document_path') != path:
            result = unavailable
        else:
            result = driver.document_attachment_metadata('com.apple.TextEdit', text['document_token'])
            import hashlib
            encoded = text['text'].encode('utf-16-le')
            positions = [i//2 for i in range(0, len(encoded), 2) if encoded[i:i+2] == b'\xfc\xff']
            if (result.get('type') != 'document_attachment_metadata' or result.get('document_path') != path
                    or result.get('text_sha256') != hashlib.sha256(text['text'].encode('utf-8')).hexdigest()
                    or result.get('utf16_length') != len(encoded)//2
                    or [row['utf16_index'] for row in result.get('occurrences', [])] != positions):
                result = unavailable
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0 if result.get('type') == 'document_attachment_metadata' else 1

    if cmd == "menu-state":
        # Read-only: is a menu item enabled for this app right now? A
        # background app often reports items disabled that work in front
        # (Save, New Folder — 2026-10-09); this makes that visible without
        # pressing anything.
        args = [value for value in rest if value != "--helper"]
        wait_seconds=None
        if '--wait-enabled' in args:
            try:
                if args.count('--wait-enabled')!=1:raise ValueError()
                index=args.index('--wait-enabled')
                wait_seconds=float(args[index+1])
                import math
                if not math.isfinite(wait_seconds) or not 0<=wait_seconds<=60:raise ValueError()
                del args[index:index+2]
            except (ValueError,IndexError):
                print('usage: --wait-enabled SECONDS (finite 0..60)');return 2
        if len(args) < 3 or args[0].startswith("-"):
            print("usage: menu-state <bundle-id> <Menu> <Item> [<Submenu item> ...] [--helper] [--wait-enabled SECONDS]")
            return 2
        if wait_seconds is not None:
            from .menu_readiness import wait_menu_enabled
            result=wait_menu_enabled(_make_driver(rest),args[0],args[1:],timeout_seconds=wait_seconds)
            print(json.dumps(result.to_dict(),ensure_ascii=False))
            return 0 if result.state=='ready' else 1
        result = _make_driver(rest).menu_state(args[0], args[1:])
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("type") == "menu_state" else 1

    if cmd == "show":
        if not rest:
            print("usage: show <flow-name>")
            return 2
        print(_load_recipe(rest[0]).to_json())
        return 0

    if cmd == "candidates":
        # What the screen loop worked out and would like to become a recipe,
        # with the reasons it is not one yet. Nothing here replays.
        from .promote import CandidateStore
        cs = CandidateStore()
        names = cs.list()
        if not names:
            print("  (아직 제안된 것이 없습니다)")
            return 0
        print(f"  제안 위치: {cs.path}\n")
        for n in names:
            rec = cs.get(n) or {}
            why = rec.get("blocked_by") or []
            mark = "준비됨" if not why else "아직"
            print(f"  [{mark}] {n}  ({rec.get('runs', 1)}회)  "
                  f"{rec.get('candidate', {}).get('goal', '')[:40]}")
            for w in why:
                print(f"           · {w}")
            params = rec.get("parameters") or {}
            if params:
                print(f"           부탁에서 온 값: {', '.join(params)}")
            explored = (rec.get("candidate") or {}).get("explored") or []
            if explored:
                print("           살펴보기만 한 단계(레시피에서 뺌): "
                      + ", ".join(str(a.get("label") or a.get("match")) for a in explored))
        print("\n  레시피로 만들려면:  tacto promote <이름>")
        return 0

    if cmd == "promote":
        from .flow import Flow
        from .promote import CandidateStore
        if not rest:
            print("사용법: tacto promote <이름>"); return 2
        name = rest[0]
        rec = CandidateStore().get(name)
        if rec is None:
            print(f"  '{name}' 제안을 찾지 못했습니다"); return 1
        why = rec.get("blocked_by") or []
        if why or not rec.get("flow"):
            print(f"  '{name}' 은 아직 레시피가 될 수 없습니다:")
            for w in why:
                print(f"    · {w}")
            return 1
        import json as _json
        flow = Flow.from_json(_json.dumps(rec["flow"]))
        chosen, words = _params_from(rest[1:])
        try:
            provider, words = _provider_from(words)
        except ValueError as exc:
            print(str(exc)); return 2
        # Where the walk chose by what it READ, replaying the choice blindly
        # is wrong the first time the screen differs. A model reviews the
        # walk once for such steps; the recording decides which proposals
        # stand. Only a click made while a value was on screen can be one.
        if "--no-decisions" not in words:
            try:
                flow = _promote_decisions(rec, flow, provider)
            except _Refused as exc:
                print(f"  {exc}"); return 1
        # Which values become inputs is the person's call, not the run's: the
        # candidate only reports which ones came from the request.
        offered = rec.get("parameters") or {}
        unknown = [lit for lit in chosen if lit not in offered]
        if unknown:
            print(f"  부탁에서 온 값이 아닙니다: {', '.join(unknown)}"
                  f"  (고를 수 있는 값: {', '.join(offered) or '없음'})")
            return 1
        if chosen:
            from .reparam import parameterize as _par, digits_table as _dt
            concrete = flow
            flow = _par(concrete, chosen, tables=_dt())
            # The template, given back this run's values, must BE this run's
            # recipe. Anything else means a value was not (or not only) where
            # the template put it, and the recipe would replay something else.
            if not _same_steps(flow.bind(**{p: lit for lit, p in chosen.items()}), concrete):
                print("  입력으로 바꾼 레시피가 원래 실행을 그대로 재현하지 않습니다 — 만들지 않았습니다")
                return 1
            # The sentences it answers: what was said, and what the reading of
            # it came to, each with the chosen values marked as inputs. Only a
            # sentence where every value appears exactly once is kept — one
            # where "25" appears twice cannot say which of them is the input.
            said = [rec.get("request") or "", (rec.get("candidate") or {}).get("goal") or ""]
            intents = []
            for sentence in dict.fromkeys(x for x in said if x):
                if all(len(re.findall(rf"(?<![0-9]){re.escape(lit)}(?![0-9])", sentence)) == 1
                       for lit in chosen):
                    for lit, p in chosen.items():
                        sentence = re.sub(rf"(?<![0-9]){re.escape(lit)}(?![0-9])",
                                          "{{%s}}" % p, sentence)
                    intents.append(sentence)
            flow.intents = intents
        else:
            flow.intents = [x for x in dict.fromkeys(
                [rec.get("request") or "", (rec.get("candidate") or {}).get("goal") or ""]) if x]
        path = FlowStore().save(flow)
        print(f"  '{name}' 을 레시피로 만들었습니다 → {path}")
        from .flow import every_step
        asks = sum(st.action == "decide" for st in every_step(flow.steps))
        how = (f"재생할 때 판단 지점 {asks}곳에서만 모델에 묻습니다" if asks
               else "이제 토큰 없이 재생됩니다")
        # A value a decision now chooses is not an input any more.
        decided = {c for st in flow.steps if st.action == "decide" for c in st.params["choices"]}
        offered = {lit: at for lit, at in offered.items() if lit not in decided}
        # Values inside a branch belong to that case, not to the request.
        if any("branches" in st.params for st in flow.steps if st.action == "decide"):
            offered = {}
        if flow.params:
            example = " ".join(f"-p {p}=…" for p in flow.params)
            print(f"  {how}:  tacto replay {flow.name} {example}")
        else:
            print(f"  {how}:  tacto run {path}")
            if offered:
                print(f"  값을 바꿔 쓰려면:  tacto promote {name} "
                      + " ".join(f"-p {lit}=이름" for lit in offered))
        return 0

    if cmd == "list":
        store = FlowStore()
        names = store.list()
        if not names:
            print("  (no saved flows)")
            return 0
        print(f"  저장 위치(Flow IR): {store.path}")
        for n in names:
            print(f"  {n}")
        print("  전체 IR 보기:  tacto show <이름>")
        return 0

    if cmd == "compile":
        # STAGE 1 only: natural language -> Flow IR. Spends model tokens;
        # writes the recipe and stops. Nothing on screen is touched.
        if not rest:
            from .authoring import PROVIDER_ORDER
            print('usage: compile "<intent>" [-o <flow.json>] [--verify] '
                  '[--repair-rounds N] [--compile-engine claude|codex|opencode|cxweb]')
            print('  무효 레시피는 실행 없이 기본 최대 1회 모델에 수정 요청합니다. '
                  '--repair-rounds 0으로 추가 호출을 끕니다.')
            print('  --verify[ --verify-rounds N]: 만든 레시피를 실행해 보고, 실패하면 '
                  '그 이유를 모델에 돌려주어 N번까지 고칩니다 (화면을 조작합니다)')
            print('  기본 auto — 시도 순서: ' + ' -> '.join(PROVIDER_ORDER))
            return 2
        out = provider = None
        # Verification RUNS the recipe, so it is opt-in: compile otherwise
        # promises not to touch the screen.
        verify = "--verify" in rest
        rounds = 1
        if '--repair-rounds' in rest and '--verify-rounds' in rest:
            print('choose --repair-rounds or --verify-rounds')
            return 2
        for flag in ('--repair-rounds', '--verify-rounds'):
            if flag in rest:
                k = rest.index(flag)
                if rest.count(flag) != 1 or k + 1 >= len(rest) or not rest[k+1].isascii() or not rest[k+1].isdigit():
                    print(f'{flag} requires a nonnegative integer')
                    return 2
                rounds = int(rest[k+1])
                verify = verify or flag == '--verify-rounds'
                rest = rest[:k] + rest[k+2:]
        words = [w for w in rest if w != "--verify"]
        for flag, setter in (("-o", "out"), ("--provider", "provider"),
                             ("--compile-engine", "provider"), ("--engine", "provider")):
            if flag in words:
                k = words.index(flag)
                val = words[k + 1] if k + 1 < len(words) else None
                if setter == "out":
                    out = val
                else:
                    provider = val
                del words[k:k + 2]
        intent = " ".join(w for w in words if w != "--helper")
        from .authoring import author as _author
        from .store import FlowStore as _FS
        print(f"  🧠 '{intent}' 를 배우는 중… (엔진: {provider or 'auto'})")
        flow = _author(intent, _make_driver(rest), provider=provider or "auto", narrate=print,
                       verify=verify, rounds=rounds)
        if out:
            from .storage import write_private
            path = write_private(out, flow.to_json())
        else:
            path = _FS().save(flow)
        print(f"  ✓ IR 저장: {path}  ({len(flow.steps)}단계, 앱 {flow.target})")
        print(f"  실행:  tacto run {path}")
        return 0

    if cmd == "parameterize":
        # Rewrite an existing recipe so chosen literals become inputs.
        # Text-level transform on the IR: no model, no app, no screen.
        params, rest = _params_from(rest)
        if not rest or not params:
            print('usage: parameterize <flow.json|name> -p 12=a -p 34=b [-p 408=result] '
                  '[--derived result="{{a}} * {{b}}"] [--digits] [-o out.json]')
            return 2
        import json as _j, os as _os
        from .flow import Flow as _F
        from .reparam import parameterize as _par, digits_table as _dt
        src, out, derived = rest[0], None, {}
        words = rest[1:]
        if "-o" in words:
            k = words.index("-o"); out = words[k+1] if k+1 < len(words) else None
        for w in words:
            if w.startswith("--derived"):
                _, _, expr = w.partition("=")
                name, _, rhs = expr.partition("=") if "=" in expr else ("result", "", expr)
                derived[name or "result"] = rhs or expr
        if _os.path.exists(src):
            with open(src, encoding="utf-8") as source:
                flow = _F.from_json(source.read())
        else:
            flow = _load_recipe(src)
        tmpl = _par(flow, params, tables=_dt() if "--digits" in words else None,
                    derived=derived or None)
        if out:
            from .storage import write_private
            path = write_private(out, tmpl.to_json())
        else:
            path = FlowStore().save(tmpl)
        print(f"  ✓ 파라미터화: {path}")
        print(f"    params={tmpl.params} derived={tmpl.derived or {}}")
        print(f"    실행:  tacto run {path} " + " ".join(f"-p {k}=…" for k in tmpl.params))
        return 0

    if cmd == "run":
        # Execute a Flow IR straight from a file — skips intent resolution
        # entirely, which is what you want for scripting/CI.
        params, rest = _params_from(rest)
        if not rest or rest[0] in ("--help", "-h", "help"):
            # `--help` used to be opened as a file and crash with
            # FileNotFoundError — the first thing an agent tries (2026-10-07).
            print('usage: run <flow.json> [-p a=12 -p b=34] [--helper] [--yes] [--think N] [--document /absolute/path] [--journal-dir /absolute/private/run]\n'
                  '  built-in recipes: tacto list · tacto show <name> · tacto replay <name> -p key=value')
            return 2 if not rest else 0
        from .flow import Flow as _F, replay as _replay
        try:
            with open(rest[0], encoding="utf-8") as fh:
                flow = _F.from_json(fh.read())
        except (OSError, ValueError, TypeError, KeyError) as exc:
            # The author is a model reading this output; a traceback buries
            # the one line that says which field is wrong.
            from .flow_errors import feedback
            print("invalid_flow: " + feedback(exc), file=sys.stderr)
            return 2
        walked = bool(flow.intents)
        if '--auto-repair' in rest:
            return _run_flow(flow,rest,walked=walked,_auto_params=params)
        if flow.params or flow.tables or flow.derived or params:
            try:
                flow = flow.bind(**params)
            except (ValueError, TypeError, KeyError, ArithmeticError):
                print('invalid_parameters: provide defaults in params or -p name=value; placeholders use {{name}}', file=sys.stderr)
                return 2
        return _run_flow(flow, rest, walked=walked)

    if cmd == "replay":
        if not rest:
            print("usage: replay <name>"); return 2
        params, rest = _params_from(rest)
        if not rest or rest[0] in ("--help", "-h", "help"):
            print("usage: replay <name> [-p a=12 -p b=34] [--helper] [--yes] [--think N] [--document /absolute/path] [--journal-dir /absolute/private/run]\n"
                  "  names: tacto list · inputs and steps: tacto show <name>")
            return 2 if not rest else 0
        flow = _load_recipe(rest[0])
        walked = bool(flow.intents)      # binding drops it; decide first
        if '--auto-repair' in rest:
            return _run_flow(flow,rest,walked=walked,_auto_params=params)
        if flow.params or flow.tables or flow.derived or params:
            flow = flow.bind(**params)
        return _run_flow(flow, rest, walked=walked)

    if cmd == "do":
        try:
            provider, rest = _provider_from(rest)
            general_limits, rest = _general_limits_from(rest)
        except ValueError as exc:
            print(str(exc))
            return 2
        # A path where an intent was expected is a recipe, not a sentence —
        # run it rather than failing to resolve it as natural language.
        import os as _os
        if rest and _os.path.exists(rest[0]) and rest[0].endswith(".json"):
            print("  (" + rest[0] + " 은 IR 파일이라 바로 실행합니다)")
            return main(["run"] + rest)
        intent = " ".join(rest)
        # gate on permissions first — the beginner path. UI Automation on
        # Windows has no permission grant or macOS session lock to check.
        if sys.platform != "win32":
            from . import onboard
            if not onboard.run(interactive=False, narrate=lambda s: None):
                print("먼저 권한이 필요해요:  tacto doctor")
                return 1
            from .session import preflight
            pf = preflight()
            if pf["verdict"] == "session_locked":
                print("화면이 잠겨 있어요. 잠금을 풀어주시면 바로 이어서 할게요.")
                return 1
        # Read the flag BEFORE stripping it. Checking `rest` afterwards, as this
        # did, could never be true — so `--learn` has never once reached the
        # authoring path and every unknown task was reported unconditionally.
        learning = "--learn" in rest
        rest = [r for r in rest if r != "--learn"]
        trace_path = None
        if "--trace" in rest:
            k = rest.index("--trace")
            if k + 1 == len(rest) or rest[k + 1].startswith("-"):
                print("--trace requires a path")
                return 2
            trace_path = rest[k + 1]
            rest = [r for j, r in enumerate(rest) if j not in (k, k + 1)]
        # Guard: a trace and a flow are different IRs. Writing a trace over a
        # saved flow destroys the recipe (a real user hit this), so refuse.
        if trace_path:
            import os
            from .store import FlowStore as _FS
            flows_dir = os.path.abspath(_FS().path)
            tp = os.path.abspath(trace_path)
            clashes = os.path.dirname(tp) == flows_dir
            if not clashes and os.path.exists(tp):
                try:
                    import json as _j
                    with open(tp, encoding="utf-8") as fh:
                        clashes = "steps" in _j.load(fh)      # looks like a Flow IR
                except Exception:
                    clashes = False
            if clashes:
                print(f"'{trace_path}' 은(는) 실행 레시피(Flow IR)입니다. "
                      f"트레이스는 다른 경로에 저장해 주세요 (예: --trace run.json).\n"
                      f"  Flow IR  = 무엇을 할지 (tacto show <이름>)\n"
                      f"  Trace IR = 실행 중 무엇이 보였는지 (--trace)")
                return 2

        # Flags are not part of what the user asked for. Left in, `tacto do "..."
        # --helper` sends "… --helper" to the intent matcher as if it were words
        # the person said.
        # `--think [N]`: let the run stop and ask when the screen disagrees with
        # the recipe. Unset, a built-in recipe asks nothing and one promoted
        # from an agent's walk may ask once (see Agent.handle); `--think 0`
        # turns that off too.
        think = None
        if "--think" in rest:
            k = rest.index("--think")
            nxt = rest[k + 1] if k + 1 < len(rest) else ""
            think = int(nxt) if nxt.isdigit() else 1
            rest = [w for j, w in enumerate(rest)
                    if j != k and not (j == k + 1 and nxt.isdigit())]
            intent = " ".join(rest)

        intent = " ".join(w for w in rest if w not in
                          ("--yes", "-y", "--helper", "--strict", "--no-indicator")).strip()

        driver = _make_driver(rest)
        tracer = None
        if trace_path:
            from .trace import TracingDriver, Trace
            tracer = TracingDriver(driver, Trace(intent=intent))
            driver = tracer
        # STAGE 1+2+3. Learning an unknown task spends model tokens, so it is
        # opt-in: without --learn an unknown request is reported, not billed.
        author_hook = None
        if learning:
            from .authoring import author as _author
            def author_hook(intent_text, drv):        # noqa: E306
                print("  🧠 처음 보는 작업이라 배우는 중… (모델 사용)")
                return _author(intent_text, drv, provider=provider, narrate=print)
        # Reading the request is on by default. A product for people who
        # cannot use a computer cannot answer only to phrasings a regex was
        # written for — and the patterns now say so themselves rather than
        # guessing. `--strict` keeps the old behaviour for scripts that want a
        # hard "I did not understand" instead of a question.
        interpret = None
        if "--strict" not in rest:
            from .ask import ask as _ask

            def interpret(q):                                  # noqa: F811
                print(f"  🧠 말씀을 읽는 중… ({q.kind})")
                return _ask(q, provider=provider, narrate=lambda m: print(f"  {m}"))

        # The other half: a task no recipe covers is done by looking at the
        # screen and asking about it — one question per screen, not per click.
        # Same boundary problem as learning, and worth saying up front rather
        # than after four provider timeouts.
        general_hook = None
        if interpret is not None:
            from .ask import ask as _ask2, ask_here_paged

            def general_hook(target, goal, confirm, env=None, need=None):  # noqa: F811
                from .ask import where_is
                from .general import run as _run
                def here(g, cands, done):
                    print(f"  👀 화면을 보고 있어요… ({len(cands)}개)")
                    return ask_here_paged(g, cands, done, provider=provider,
                                          budget=general_limits["budget"],
                                          max_pages=general_limits["max_pages"],
                                          narrate=lambda m: print(f"  {m}"))
                def where(field, cands):
                    print(f"  🔎 보내기 전에 '{field}' 를 확인합니다")
                    return _ask2(where_is(field, cands), provider=provider)
                return _run(driver, target, goal, here, envelope=env,
                            timeout_seconds=general_limits["timeout_seconds"],
                            max_screens=general_limits["max_screens"],
                            witnesses=need, ask_where=where,
                            approve=confirm, narrate=lambda m: print(f"  {m}"))

        judge = None
        if think is None or think:
            # Built lazily in effect: it costs nothing until a step that
            # declared a surprise actually meets one.
            from .judge import model_judge
            judge = model_judge(provider=provider, narrate=lambda s: print(f"  {s}"))
        decider = None
        if interpret is not None:
            from .judge import model_decider
            decider = model_decider(provider=provider, narrate=lambda s: print(f"  {s}"))
        review = None
        if interpret is not None:
            def review(rec, flow, taken):
                try:
                    return _promote_decisions(rec, flow, provider, taken_names=taken)
                except _Refused:
                    return flow
        agent = Agent(driver=driver, narrate=lambda s: print(f"  🗣  {s}"),
                      author=author_hook, interpret=interpret,
                      general=general_hook, judge=judge, decide=decider, review=review)
        # The person should be able to see their computer being driven, and
        # stop it: outline the window while the task runs.
        from .store import FlowStore as _S
        guess = None
        try:
            from .library import builders
            hit = agent.resolve(intent)
            if hit and _S().has(hit.name):
                guess = _S().load(hit.name).target
            elif hit and hit.name in builders():
                # A recipe that ships with Tacto but has not been saved yet still
                # deserves the outline — otherwise the very first run of a task
                # is the one run nobody can see or stop.
                guess = builders()[hit.name]().target
        except Exception:
            guess = None
        with _indicator_for(guess or "", rest):
            turn = agent.handle(intent, ask=_confirmer(rest), think=think)
        if tracer is not None:
            tracer.trace.flow = turn.flow or "(unresolved)"
            print(f"\n트레이스 저장: {tracer.trace.save(trace_path)}"
                  f"  (이 파일만 있으면 다른 컴퓨터에서 그대로 재현됩니다)")
        print(f"\n[{turn.status}] {turn.say}")
        return 0 if turn.status == "done" else 1

    print(f"unknown command {cmd!r}\n{__doc__}")
    return 2


def main(argv=None):
    from .access_log import current, AccessLog, COMMANDS
    arguments = sys.argv[1:] if argv is None else argv
    command = arguments[0] if arguments else ''
    log = AccessLog(command) if command in COMMANDS and current.get() is None else None
    token = current.set(log) if log is not None else None
    try:
        code = _main(argv)
        if log is not None:
            log.finish(code)
        return code
    except FlowStore.MigrationRequired as exc:
        print(f"migration_required: {exc}", file=sys.stderr)
        if log is not None:
            log.finish(2)
        return 2
    except HelperLeaseRefused as exc:
        print(json.dumps(exc.result(), ensure_ascii=False))
        if log is not None:
            log.task_result(exc.result())
            log.finish(2)
        return 2
    except HelperUnavailable:
        detail = "도우미 연결을 사용할 수 없습니다. Tacto 앱에서 권한과 연결 상태를 확인하세요."
        if '--json' in arguments:
            print(json.dumps({'type':'error','error':'helper_unavailable','effect':'unknown','detail':detail},ensure_ascii=False))
        else:
            print('helper_unavailable: '+detail,file=sys.stderr)
        if log is not None:
            log.finish(2)
        return 2
    except BaseException:
        if log is not None:
            log.finish(1)
        raise
    finally:
        if token is not None:
            current.reset(token)
        if log is not None:
            log.close()


if __name__ == "__main__":
    sys.exit(main())
