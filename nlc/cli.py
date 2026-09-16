"""nlc — the single entry point tying onboarding, the agent, and the store.

Usage:
  python -m nlc.cli doctor            # check/guide permissions
  python -m nlc.cli apps              # list running apps (GUI session check)
  python -m nlc.cli list              # list saved flows
  python -m nlc.cli do "<intent>"     # run a natural-language request
  python -m nlc.cli replay <name>     # replay a saved flow by name
"""

import sys

from . import onboard
from .agent import Agent
from .store import FlowStore


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

    if cmd == "list":
        names = FlowStore().list()
        print("\n".join(f"  {n}" for n in names) if names else "  (no saved flows)")
        return 0

    if cmd == "replay":
        if not rest:
            print("usage: replay <name>"); return 2
        from .driver import Driver
        from .flow import replay
        flow = FlowStore().load(rest[0])
        r = replay(Driver(), flow, narrate=lambda s: print(f"  … {s}"))
        print("done" if r.ok else f"failed: {r.log[-1]}")
        return 0 if r.ok else 1

    if cmd == "do":
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
        agent = Agent(narrate=lambda s: print(f"  🗣  {s}"))
        with KeepAwake():   # option A: hold off idle-lock while the task runs
            turn = agent.handle(intent)
        print(f"\n[{turn.status}] {turn.say}")
        return 0 if turn.status == "done" else 1

    print(f"unknown command {cmd!r}\n{__doc__}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
