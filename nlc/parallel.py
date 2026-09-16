"""Parallel operation — run several flows against different apps at once.

Each flow gets its own Driver (independent observation registry). AX-action
steps (click via AXPress, set_value) are per-element and need no focus, so they
run truly concurrently — the background-window injection that lets an agent work
without stealing the user's mouse. CGEvent steps (type_text, press_key,
coordinate clicks) target the globally focused app, so the driver serializes
them through a process-wide lock; they interleave safely but not simultaneously.
"""

from __future__ import annotations

import concurrent.futures as cf
from dataclasses import dataclass

from .driver import Driver
from .flow import Flow, replay, ReplayResult


@dataclass
class ParallelOutcome:
    name: str
    ok: bool
    result: ReplayResult


def run_parallel(flows: list[Flow], approvals: dict[str, dict] | None = None,
                 max_workers: int | None = None) -> list[ParallelOutcome]:
    """Replay several flows concurrently. Returns one outcome per flow.

    approvals: optional {flow_name: {step_index: True}}.
    Each worker builds its own Driver so observation state never crosses flows.
    """
    approvals = approvals or {}
    # Two flows on the SAME app would each hold their own observation registry
    # and could not see the other's mutations, so a stale index would slip past
    # each guard (review finding). Compare by RESOLVED PID, not the target string
    # — "Calculator" and "com.apple.calculator" are the same app.
    probe = Driver()
    pids = []
    for f in flows:
        pid = probe._resolve(f.target, launch=False)
        pids.append(pid if pid is not None else f.target.lower())
    dupes = {p for p in pids if pids.count(p) > 1}
    if dupes:
        raise ValueError(f"parallel flows must target distinct apps; shared: {dupes}")
    outcomes: list[ParallelOutcome] = []

    def _one(flow: Flow) -> ParallelOutcome:
        d = Driver()   # per-flow observation registry
        r = replay(d, flow, approvals=approvals.get(flow.name))
        return ParallelOutcome(flow.name, r.ok, r)

    with cf.ThreadPoolExecutor(max_workers=max_workers or len(flows)) as ex:
        futures = {ex.submit(_one, f): f for f in flows}
        for fut in cf.as_completed(futures):
            outcomes.append(fut.result())
    return outcomes


def observe_parallel(targets: list[str]) -> dict[str, dict]:
    """Concurrently read several apps' AX trees — proves focus-free parallel
    access (no app is raised, the user's foreground app is untouched)."""
    def _obs(t):
        return t, Driver().observe(t, "tree_full")
    out = {}
    with cf.ThreadPoolExecutor(max_workers=len(targets)) as ex:
        for t, r in ex.map(_obs, targets):
            out[t] = r
    return out


if __name__ == "__main__":
    import time
    targets = ["com.apple.calculator", "com.apple.finder"]
    t0 = time.monotonic()
    res = observe_parallel(targets)
    dt = (time.monotonic() - t0) * 1000
    for t, r in res.items():
        n = r.get("element_count", r.get("error"))
        print(f"  {t}: {r['type']} ({n})")
    print(f"  observed {len(targets)} apps concurrently in {dt:.0f}ms")
