"""nlc — the single entry point tying onboarding, the agent, and the store.

Usage:
  python -m nlc.cli doctor            # check/guide permissions
  python -m nlc.cli apps              # list running apps (GUI session check)
  python -m nlc.cli list              # list saved flows (+ where the IR lives)
  python -m nlc.cli show <name>       # print a flow's IR (JSON)
  python -m nlc.cli compile "<intent>"        # STAGE 1: intent -> Flow IR (uses a model)
      (--verify runs the result once and lets the model fix what failed)
  python -m nlc.cli do "<intent>"     # STAGE 1+2+3 (add --learn to author unknown tasks)
  python -m nlc.cli do "<intent>" --trace run.json   # …and record a reproducible trace
  python -m nlc.cli parameterize <flow> -p 12=a -p 34=b --digits   # concrete IR -> template
  python -m nlc.cli run <flow.json>   # execute a Flow IR file directly (no intent step)
      (--helper routes through the signed Swift helper that holds the permissions)
      (add --no-indicator to suppress the on-screen "being controlled" outline)
  python -m nlc.cli replay <name>     # replay a saved flow by name

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

import sys

from . import onboard
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
    from .driver import Driver
    from .overlay import Indicator
    if "--no-indicator" in argv:
        return Indicator(None, enabled=False)
    try:
        pid = Driver()._resolve(target, launch=False)
    except Exception:
        pid = None
    return Indicator(pid, notify=lambda m: print(f"  {m}"))


def main(argv=None):
    argv = argv or sys.argv[1:]
    if not argv:
        print(__doc__)
        return 0
    cmd, *rest = argv

    if cmd == "doctor":
        ok = onboard.run(interactive="--check" not in rest)
        return 0 if ok else 1

    if cmd == "eval":
        # Run the suite through this process, so the helper scenarios
        # see the signed launcher in their ancestry.
        import runpy, os as _os
        root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
        runpy.run_path(_os.path.join(root, "examples", "eval.py"),
                       run_name="__main__")
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
            print('  --verify: 만든 레시피를 한 번 실행해 보고, 실패하면 그 이유를 '
                  '모델에 돌려주어 한 번 더 고칩니다 (화면을 조작합니다)')
            print('  기본 auto — 시도 순서: ' + ' -> '.join(PROVIDER_ORDER))
            return 2
        out = provider = None
        # Verification RUNS the recipe, so it is opt-in: compile otherwise
        # promises not to touch the screen.
        verify = "--verify" in rest
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
        intent = " ".join(words)
        from .driver import Driver as _D
        from .authoring import author as _author
        from .store import FlowStore as _FS
        print(f"  🧠 '{intent}' 를 배우는 중… (엔진: {provider or 'auto'})")
        flow = _author(intent, _D(), provider=provider or "auto", narrate=print,
                       verify=verify)
        if out:
            with open(out, "w", encoding="utf-8") as fh:
                fh.write(flow.to_json())
            path = out
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
            open(out, "w", encoding="utf-8").write(tmpl.to_json()); path = out
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
        from .driver import Driver as _D
        from .flow import Flow as _F, replay as _replay
        with open(rest[0], encoding="utf-8") as fh:
            flow = _F.from_json(fh.read())
        if flow.params or params:
            flow = flow.bind(**params)          # parameterized recipe
        with _indicator_for(flow.target, rest):
            r = _replay(_make_driver(rest), flow, narrate=lambda s: print(f"  … {s}"))
        last = r.log[-1]
        if r.ok:
            v = last.get("result")
            print(f"done" + (f" — 결과: {v}" if v else ""))
        else:
            print(f"failed: {last}")
        return 0 if r.ok else 1

    if cmd == "replay":
        if not rest:
            print("usage: replay <name>"); return 2
        from .driver import Driver
        from .flow import replay
        params, rest = _params_from(rest)
        if not rest:
            print("usage: replay <name> [-p a=12 -p b=34]"); return 2
        flow = FlowStore().load(rest[0])
        if flow.params or params:
            flow = flow.bind(**params)
        with _indicator_for(flow.target, rest):
            r = replay(_make_driver(rest), flow, narrate=lambda s: print(f"  … {s}"))
        if r.ok:
            v = r.log[-1].get("result")
            print("done" + (f" — 결과: {v}" if v else ""))
        else:
            print(f"failed: {r.log[-1]}")
        return 0 if r.ok else 1

    if cmd == "do":
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
            trace_path = rest[k + 1] if k + 1 < len(rest) else "nlc-trace.json"
            intent = " ".join(r for j, r in enumerate(rest)
                              if j not in (k, k + 1))
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
        intent = " ".join(w for w in intent.split() if not w.startswith("--")).strip()

        driver = _make_driver(rest)
        tracer = None
        if trace_path:
            from .trace import TracingDriver, Trace
            tracer = TracingDriver(driver, Trace(intent=intent))
            driver = tracer
        # STAGE 1+2+3. Learning an unknown task spends model tokens, so it is
        # opt-in: without --learn an unknown request is reported, not billed.
        author_hook = None
        if learning and _os.environ.get("NLC_LAUNCHER"):
            # The signed launcher gives its child a fixed PATH so nobody can
            # redirect what runs — which is exactly right, and also means the
            # model CLIs (installed in user directories) are unreachable from
            # here. Learning and running are separate stages for this reason;
            # say so now instead of after four provider timeouts.
            print("  배우기는 권한 경계 밖에서 합니다 (모델 호출이 필요해서요):\n"
                  f'    uv run nlc compile "{intent}"\n'
                  "    ./launcher/nlc run flows/<이름>.json")
            learning = False
        if learning:
            from .authoring import author as _author
            def author_hook(intent_text, drv):        # noqa: E306
                print("  🧠 처음 보는 작업이라 배우는 중… (모델 사용)")
                return _author(intent_text, drv, provider="auto", narrate=print)
        agent = Agent(driver=driver, narrate=lambda s: print(f"  🗣  {s}"),
                      author=author_hook)
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
            turn = agent.handle(intent, ask=_confirmer(rest))
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
