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
      (add --no-indicator to suppress the on-screen "being controlled" outline)
  python -m nlc.cli replay <name>     # replay a saved flow by name
  python -m nlc.cli candidates        # what a screen-loop run would like to
                                      # become a recipe, and why it is not yet
  python -m nlc.cli promote <name> [-p 25=n]
                                      # make one a recipe, if its gate is clean;
                                      # -p turns a value from the request into an
                                      # input, and the recipe then answers the
                                      # same sentence with any value (nlc do "…")

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


def _run_flow(flow, argv):
    """All direct replay commands use the same consent and trace path."""
    from .flow import replay
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
    with _indicator_for(flow.target, argv):
        result = replay(driver, flow, approvals=approvals,
                        narrate=lambda text: print(f"  … {text}"), decide=decider, **hooks)
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
        print(f"failed: {result.log[-1]}")
    return 0 if result.ok else 1


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("--help", "-h", "help"):
        print(__doc__)
        return 0
    cmd, *rest = argv

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
        from .driver import Driver
        r = Driver().list_targets()
        if not r["apps"]:
            print("화면(GUI) 세션에 연결돼 있지 않습니다 — tmux 밖에서 실행하거나 "
                  "reattach-to-user-namespace 를 쓰세요.")
            return 1
        for a in sorted(r["apps"], key=lambda x: x["displayName"]):
            print(f"  {a['id']:40} {a['displayName']}")
        return 0

    if cmd == "observe":
        if not rest:
            print("usage: observe <bundle-id>")
            return 2
        from .driver import Driver
        o = Driver().observe(rest[0], "tree_full")
        if o["type"] != "observation":
            print(f"{o.get('error')}: {o.get('detail','')}")
            return 1
        print(o["tree"])
        return 0

    if cmd == "show":
        if not rest:
            print("usage: show <flow-name>")
            return 2
        print(FlowStore().load(rest[0]).to_json())
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
        # Which values become inputs is the person's call, not the run's: the
        # candidate only reports which ones came from the request.
        offered = rec.get("parameters") or {}
        chosen, _ = _params_from(rest[1:])
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
        if flow.params:
            example = " ".join(f"-p {p}=…" for p in flow.params)
            print(f"  이제 토큰 없이 재생됩니다:  nlc replay {flow.name} {example}")
        else:
            print(f"  이제 토큰 없이 재생됩니다:  nlc run {path}")
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
        if not rest:
            print('usage: run <flow.json> [-p a=12 -p b=34]')
            return 2
        from .flow import Flow as _F, replay as _replay
        with open(rest[0], encoding="utf-8") as fh:
            flow = _F.from_json(fh.read())
        if flow.params or flow.tables or flow.derived or params:
            flow = flow.bind(**params)          # parameterized recipe
        return _run_flow(flow, rest)

    if cmd == "replay":
        if not rest:
            print("usage: replay <name>"); return 2
        params, rest = _params_from(rest)
        if not rest:
            print("usage: replay <name> [-p a=12 -p b=34]"); return 2
        flow = FlowStore().load(rest[0])
        if flow.params or flow.tables or flow.derived or params:
            flow = flow.bind(**params)
        return _run_flow(flow, rest)

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
        from .session import preflight, KeepAwake
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
        agent = Agent(driver=driver, narrate=lambda s: print(f"  🗣  {s}"),
                      author=author_hook, interpret=interpret,
                      general=general_hook, judge=judge, decide=decider)
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
        with KeepAwake(), _indicator_for(guess or "", rest):
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
