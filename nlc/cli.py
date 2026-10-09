"""nlc — the single entry point tying onboarding, the agent, and the store.

Usage:
  python -m nlc.cli doctor            # check/guide permissions
  python -m nlc.cli apps              # list running apps (GUI session check)
  python -m nlc.cli list              # list saved flows (+ where the IR lives)
  python -m nlc.cli show <name>       # print a flow's IR (JSON)
  python -m nlc.cli compile "<intent>"        # STAGE 1: intent -> Flow IR (uses a model)
      (--verify runs the result once and lets the model fix what failed)
  python -m nlc.cli do "<intent>"     # STAGE 1+2+3 (add --learn to author unknown tasks)
      (--provider codex|claude|gemini|opencode|cxweb|auto selects the model provider)
      (--think [N] lets a run stop and ask when the screen disagrees; N calls.
       Unset: 0 for built-in recipes, 1 for ones promoted from a walk; --think 0 = never)
      (--strict turns off reading the request itself: only phrasings the
       patterns already know, and an honest "I did not understand" otherwise)
  python -m nlc.cli do "<intent>" --trace run.json   # …and record a reproducible trace
  python -m nlc.cli parameterize <flow> -p 12=a -p 34=b --digits   # concrete IR -> template
  python -m nlc.cli run <flow.json>   # execute a Flow IR file directly (no intent step)
      (--helper routes through the signed Swift helper that holds the permissions)
      (add --no-indicator to hide the "● NLC" menu-bar item shown while it runs)
  python -m nlc.cli replay <name>     # replay a saved flow by name
      (run/replay: --think N lets a step that declared a surprise ask N times;
       unset, a recipe promoted from a walk may ask once, a written one never)
  python -m nlc.cli candidates        # what a screen-loop run would like to
                                      # become a recipe, and why it is not yet
  python -m nlc.cli promote <name> [-p 25=n]
                                      # make one a recipe, if its gate is clean;
                                      # -p turns a value from the request into an
                                      # input, and the recipe then answers the
                                      # same sentence with any value (nlc do "…")
      (a model reviews the walk once for judgment points; --no-decisions skips it)

  A step the policy will not run on its own authority (saving a file, sending a
  message) asks first. Answer at the prompt, or pass --yes for scripts; with no
  terminal to ask at, the answer is no.

Things it already knows how to do (nlc do "…"):
  7 더하기 8 해줘                  계산기 — 사칙연산 전부
  다운로드 폴더 열어줘              Finder
  새 문서 만들어줘                  TextEdit
  “회의 준비” 라고 써줘             문서에 입력 (따옴표 안이 입력될 내용)
  굵게 해줘                        전체 선택 + 굵게
  “보고서.rtf” 로 저장해줘          이름을 정해 저장 — 확인을 묻습니다
"""

import json
import re
import sys

from .agent import Agent
from .store import FlowStore





def _make_driver(argv):
    """--helper uses the signed Swift helper (which holds the permissions);
    otherwise the in-process Python driver. Same L2 protocol either way."""
    if "--helper" in argv:
        # Fail closed: --helper asks to run with the permission boundary in
        # place, so falling back to the in-process driver would run the task
        # with exactly the privileges the flag exists to avoid.
        from .helper import InheritedHelperDriver
        return InheritedHelperDriver()
    from .driver import Driver
    return Driver()


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

    `nlc do` already worked this way; `nlc replay` and `nlc show` read the
    saved copy, and the one in flows/ for textedit_write_note dated from
    2026-09-16 and declared `write_ephemeral` where the code now says
    `write_persistent`, so every replay was refused as a contract mismatch —
    the very recipe the skill points agents to (2026-10-07).
    """
    from .library import builders
    build = builders().get(name)
    return build() if build is not None else FlowStore().load(name)


def focused_window_view(tree: str) -> str:
    """The window being worked on, and only the titles of the others.

    An agent reads this on every step. Printing every window cost NLC twice
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
    focused = [b for b in blocks if "[focused]" in b[0]]
    # Main and focused windows may both be marked (e.g. a parent and its dialog).
    # Without a unique focus, hiding every other block conceals valid controls.
    if len(focused) != 1:
        return tree
    keep = focused[0]
    others = [b[0] for b in blocks if b is not keep]
    names = []
    for head in others:
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
    if "--no-indicator" in argv:
        from contextlib import nullcontext
        return nullcontext()
    from .driver import Driver
    from .overlay import Indicator
    try:
        pid = Driver()._resolve(target, launch=False)
    except Exception:
        pid = None
    return Indicator(pid, notify=lambda m: print(f"  {m}"))


def _run_flow(flow, argv, walked: bool = False):
    """All direct replay commands use the same consent and trace path.

    And the same judgment as `nlc do`: a recipe promoted from a walk may ask
    once where a step declared a surprise, `--think N` sets how often, and
    `--think 0` turns it off. Before this, the same recipe re-aimed its way
    past Calculator's `Clear`/`All Clear` under `do` and failed under `replay`.
    """
    from .flow import replay
    think = 1 if walked else 0
    if "--think" in argv:
        k = argv.index("--think")
        nxt = argv[k + 1] if k + 1 < len(argv) else ""
        think = int(nxt) if nxt.isdigit() else 1
    driver = _make_driver(argv)
    trace_path = None
    tracer = None
    if "--trace" in argv:
        k = argv.index("--trace")
        if k + 1 == len(argv) or argv[k + 1].startswith("--"):
            print("--trace requires a path")
            return 2
        trace_path = argv[k + 1]
        import json, os
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
    approvals, blocked = agent.authorize(flow, ask=_confirmer(argv))
    if blocked:
        print("\n".join(blocked))
        return 1
    try:
        hooks = agent.commit_adapter_hooks(agent.commit_adapter_for(flow))
    except Exception as exc:
        print(f"저장 결과 검증 준비 실패: {exc}")
        return 1
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
        result = replay(driver, flow, approvals=approvals,
                        narrate=lambda text: print(f"  … {text}"), decide=decider,
                        confirm=_confirmer(argv), escalate=judge, budget=think, **hooks)
    if tracer:
        print(f"트레이스 저장: {tracer.trace.save(trace_path)}")
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
        if last.get("error") == "commit_unverified" and last.get("effect") == "unknown":
            # Not "it failed": the input was sent, and nothing can prove it
            # was KEPT — an unsaved document has no file to read back. Said
            # plainly, because the raw record sent an agent round three more
            # times looking for what went wrong (2026-10-07).
            print("unverified — 입력은 보냈지만 저장된 파일이 없어 확인 영수증이 없습니다 "
                  "(저장하지 않은 문서라면 정상). 내용은 `nlc observe <앱>` 으로 확인하세요.\n"
                  "  TextEdit 의 .txt 문서를 저장·확인하려면: nlc replay textedit_save "
                  "-p document=<절대 경로> -p name=<파일 이름> --helper --yes\n"
                  f"  detail: {last}")
        else:
            print(f"failed: {last}")
    return 0 if result.ok else 1


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("--help", "-h", "help"):
        print(__doc__)
        return 0
    cmd, *rest = argv

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
    if cmd in ("doctor", "apps", "observe", "run", "replay", "do", "eval"):
        from .session import SANDBOXED, sandboxed
        if sandboxed():
            print(SANDBOXED)
            return 3
        # Hold the display awake for as long as this command runs. Only `do`
        # did, but an agent drives NLC one command at a time — observe, run,
        # replay — and with this Mac's 3-minute screensaver lock the screen
        # locked in the middle of its work (2026-09-28). Release the assertion
        # when this invocation ends, even when main is called in a long-lived
        # process or command dispatch raises. -w also covers abrupt exit.
        from .session import KeepAwake
        with KeepAwake():
            return _dispatch(cmd, rest)

    return _dispatch(cmd, rest)


def _dispatch(cmd, rest):

    if cmd == "doctor":
        from . import onboard
        ok = onboard.run(interactive="--check" not in rest)
        return 0 if ok else 1

    if cmd == "eval":
        # Run the suite through this process, so the helper scenarios
        # see the signed launcher in their ancestry.
        import runpy, os as _os
        root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
        evaluator = _os.path.join(root, "examples", "eval.py")
        if not _os.path.isfile(evaluator):
            print("평가 fixture는 wheel에 포함되지 않습니다. 소스 checkout에서 "
                  "examples/eval.py를 실행하세요.")
            return 2
        # runpy keeps OUR argv, and the suite reads it to narrow the run — so
        # hand it the arguments meant for it, not the ones meant for us.
        argv = sys.argv
        sys.argv = ["eval.py", *rest]
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
        targets = [value for value in rest if value not in ("--helper", "--all-windows")]
        if len(targets) != 1 or targets[0].startswith("-"):
            print("usage: observe <bundle-id> [--helper] [--all-windows]")
            return 2
        o = _make_driver(rest).observe(targets[0], "tree_full")
        if o["type"] != "observation":
            reason = o.get("reason")
            suffix = f" (reason={reason})" if reason else ""
            print(f"{o.get('error')}{suffix}: {o.get('detail','')}")
            return 1
        print(o["tree"] if "--all-windows" in rest else focused_window_view(o["tree"]))
        return 0

    if cmd == "menu-state":
        # Read-only: is a menu item enabled for this app right now? A
        # background app often reports items disabled that work in front
        # (Save, New Folder — 2026-10-09); this makes that visible without
        # pressing anything.
        args = [value for value in rest if value != "--helper"]
        if len(args) < 3 or args[0].startswith("-"):
            print("usage: menu-state <bundle-id> <Menu> <Item> [<Submenu item> ...] [--helper]")
            return 2
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
        print("\n  레시피로 만들려면:  nlc promote <이름>")
        return 0

    if cmd == "promote":
        from .flow import Flow
        from .promote import CandidateStore
        if not rest:
            print("사용법: nlc promote <이름>"); return 2
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
            print(f"  {how}:  nlc replay {flow.name} {example}")
        else:
            print(f"  {how}:  nlc run {path}")
            if offered:
                print(f"  값을 바꿔 쓰려면:  nlc promote {name} "
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
        print("  전체 IR 보기:  nlc show <이름>")
        return 0

    if cmd == "compile":
        # STAGE 1 only: natural language -> Flow IR. Spends model tokens;
        # writes the recipe and stops. Nothing on screen is touched.
        if not rest:
            from .authoring import PROVIDER_ORDER
            print('usage: compile "<intent>" [-o <flow.json>] [--verify] '
                  '[--compile-engine claude|codex|opencode|cxweb]')
            print('  --verify[ --verify-rounds N]: 만든 레시피를 실행해 보고, 실패하면 '
                  '그 이유를 모델에 돌려주어 N번까지 고칩니다 (화면을 조작합니다)')
            print('  기본 auto — 시도 순서: ' + ' -> '.join(PROVIDER_ORDER))
            return 2
        out = provider = None
        # Verification RUNS the recipe, so it is opt-in: compile otherwise
        # promises not to touch the screen.
        verify = "--verify" in rest
        rounds = 1
        if "--verify-rounds" in rest:
            k = rest.index("--verify-rounds")
            rounds = int(rest[k + 1]) if k + 1 < len(rest) else 1
            verify = True
            rest = rest[:k] + rest[k + 2:]
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
        print(f"  실행:  nlc run {path}")
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
        flow = (_F.from_json(open(src, encoding="utf-8").read())
                if _os.path.exists(src) else FlowStore().load(src))
        tmpl = _par(flow, params, tables=_dt() if "--digits" in words else None,
                    derived=derived or None)
        if out:
            from .storage import write_private
            path = write_private(out, tmpl.to_json())
        else:
            path = FlowStore().save(tmpl)
        print(f"  ✓ 파라미터화: {path}")
        print(f"    params={tmpl.params} derived={tmpl.derived or {}}")
        print(f"    실행:  nlc run {path} " + " ".join(f"-p {k}=…" for k in tmpl.params))
        return 0

    if cmd == "run":
        # Execute a Flow IR straight from a file — skips intent resolution
        # entirely, which is what you want for scripting/CI.
        params, rest = _params_from(rest)
        if not rest or rest[0] in ("--help", "-h", "help"):
            # `--help` used to be opened as a file and crash with
            # FileNotFoundError — the first thing an agent tries (2026-10-07).
            print('usage: run <flow.json> [-p a=12 -p b=34] [--helper] [--yes] [--think N]\n'
                  '  built-in recipes: nlc list · nlc show <name> · nlc replay <name> -p key=value')
            return 2 if not rest else 0
        from .flow import Flow as _F, replay as _replay
        try:
            with open(rest[0], encoding="utf-8") as fh:
                flow = _F.from_json(fh.read())
        except (OSError, ValueError, TypeError, KeyError) as exc:
            # The author is a model reading this output; a traceback buries
            # the one line that says which field is wrong.
            print(f"invalid_flow: {rest[0]}: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
        walked = bool(flow.intents)
        if flow.params or flow.tables or flow.derived or params:
            flow = flow.bind(**params)          # parameterized recipe
        return _run_flow(flow, rest, walked=walked)

    if cmd == "replay":
        if not rest:
            print("usage: replay <name>"); return 2
        params, rest = _params_from(rest)
        if not rest or rest[0] in ("--help", "-h", "help"):
            print("usage: replay <name> [-p a=12 -p b=34] [--helper] [--yes] [--think N]\n"
                  "  names: nlc list · inputs and steps: nlc show <name>")
            return 2 if not rest else 0
        flow = _load_recipe(rest[0])
        walked = bool(flow.intents)      # binding drops it; decide first
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
        from . import onboard
        # A path where an intent was expected is a recipe, not a sentence —
        # run it rather than failing to resolve it as natural language.
        import os as _os
        if rest and _os.path.exists(rest[0]) and rest[0].endswith(".json"):
            print("  (" + rest[0] + " 은 IR 파일이라 바로 실행합니다)")
            return main(["run"] + rest)
        intent = " ".join(rest)
        # gate on permissions first — the beginner path
        if not onboard.run(interactive=False, narrate=lambda s: None):
            print("먼저 권한이 필요해요:  python -m nlc.cli doctor")
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
                      f"  Flow IR  = 무엇을 할지 (nlc show <이름>)\n"
                      f"  Trace IR = 실행 중 무엇이 보였는지 (--trace)")
                return 2

        # Flags are not part of what the user asked for. Left in, `nlc do "..."
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
                # A recipe that ships with NLC but has not been saved yet still
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


if __name__ == "__main__":
    sys.exit(main())
