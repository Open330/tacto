"""nlc — the single entry point tying onboarding, the agent, and the store.

Usage:
  python -m nlc.cli doctor            # check/guide permissions
  python -m nlc.cli apps              # list running apps (GUI session check)
  python -m nlc.cli list              # list saved flows (+ where the IR lives)
  python -m nlc.cli show <name>       # print a flow's IR (JSON)
  python -m nlc.cli compile "<intent>"        # STAGE 1: intent -> Flow IR (uses a model)
  python -m nlc.cli do "<intent>"     # STAGE 1+2+3 (add --learn to author unknown tasks)
  python -m nlc.cli do "<intent>" --trace run.json   # …and record a reproducible trace
  python -m nlc.cli run <flow.json>   # execute a Flow IR file directly (no intent step)
      (add --no-indicator to suppress the on-screen "being controlled" outline)
  python -m nlc.cli replay <name>     # replay a saved flow by name
"""

import sys

from . import onboard
from .agent import Agent
from .store import FlowStore




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
            print('usage: compile "<intent>" [-o <flow.json>] [--provider cxweb]')
            return 2
        out = provider = None
        words = list(rest)
        for flag, setter in (("-o", "out"), ("--provider", "provider")):
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
        print(f"  🧠 '{intent}' 를 배우는 중… (모델 사용)")
        flow = _author(intent, _D(), provider=provider or "cxweb")
        if out:
            with open(out, "w", encoding="utf-8") as fh:
                fh.write(flow.to_json())
            path = out
        else:
            path = _FS().save(flow)
        print(f"  ✓ IR 저장: {path}  ({len(flow.steps)}단계, 앱 {flow.target})")
        print(f"  실행:  nlc run {path}")
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
            r = _replay(_D(), flow, narrate=lambda s: print(f"  … {s}"))
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
            r = replay(Driver(), flow, narrate=lambda s: print(f"  … {s}"))
        if r.ok:
            v = r.log[-1].get("result")
            print("done" + (f" — 결과: {v}" if v else ""))
        else:
            print(f"failed: {r.log[-1]}")
        return 0 if r.ok else 1

    if cmd == "do":
        intent = " ".join(rest)
        # gate on permissions first — the beginner path
        if not onboard.run(interactive=False, narrate=lambda s: None):
            print("먼저 권한이 필요해요:  python -m nlc.cli doctor")
            return 1
        from .driver import Driver
        from .session import preflight, KeepAwake
        pf = preflight()
        if pf["verdict"] == "session_locked":
            print("화면이 잠겨 있어요. 잠금을 풀어주시면 바로 이어서 할게요.")
            return 1
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

        driver = Driver()
        tracer = None
        if trace_path:
            from .trace import TracingDriver, Trace
            tracer = TracingDriver(driver, Trace(intent=intent))
            driver = tracer
        # STAGE 1+2+3. Learning an unknown task spends model tokens, so it is
        # opt-in: without --learn an unknown request is reported, not billed.
        author_hook = None
        if "--learn" in rest:
            from .authoring import author as _author
            def author_hook(intent_text, drv):        # noqa: E306
                print("  🧠 처음 보는 작업이라 배우는 중… (모델 사용)")
                return _author(intent_text, drv, provider="cxweb")
        agent = Agent(driver=driver, narrate=lambda s: print(f"  🗣  {s}"),
                      author=author_hook)
        # The person should be able to see their computer being driven, and
        # stop it: outline the window while the task runs.
        from .store import FlowStore as _S
        guess = None
        try:
            nm = agent.resolve(intent)
            guess = _S().load(nm).target if nm and _S().has(nm) else None
        except Exception:
            guess = None
        with KeepAwake(), _indicator_for(guess or "", rest):
            turn = agent.handle(intent)
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
