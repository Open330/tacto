# Optional fast startup

Tacto normally starts a signed helper for each command. This remains the default.
On macOS, open **Tacto → Settings → 빠른 실행(helper 유지)** to reuse a helper
between commands. Choose an idle timeout from **1–10 minutes** (default **2**).
The helper exits after that much inactivity. Settings apply on the next command;
a new warm connection refreshes the idle timeout. Turning the option off uses
per-command execution next time; an existing idle helper exits on its timer.

For a command or agent session, `TACTO_HELPER_MODE=warm` enables reuse and
`TACTO_HELPER_MODE=per_command` disables it, overriding the app setting. This does
not change permissions, flow verification or logging. Windows is unaffected.

```sh
TACTO_HELPER_MODE=warm tacto apps --helper
TACTO_HELPER_MODE=per_command tacto observe com.apple.Safari --helper --json
```

## Parallel workers (development source after rc.11)

The published rc.11 keeps its single warm connection. Development source uses
up to **four separate signed helper processes**, launched lazily. Each worker
owns one connection and its own observations, document tokens, capture callbacks
and abort state. Different apps can execute AX operations concurrently without
sharing these mutable objects. This is process isolation, not concurrent access
to the existing helper globals.

A connection owns every target app it observes until disconnect. Ownership is
keyed by the resolved PID across worker slots, builds and per-command helpers;
another connection gets `target_busy` (`effect=not_started`, `retryable=true`).
This first version deliberately serializes different windows in the same app:
app-wide menus and AX focus are shared. A disconnected or crashed helper releases
its OS-backed locks. Multi-app sessions acquire additional apps without waiting;
a conflict returns before observation/input, preventing lock-order deadlocks.

Pointer fallback and pointer/menu/key actions take a separate short-lived
cross-process input lease. Contention returns `input_busy` before dispatch; AX
reads, element edits and supported AXPress operations in different apps continue
independently. This does not coordinate with arbitrary external automation tools
or helpers from earlier releases. Activation outside the helper is not covered
by this dispatch lease; agents must retain AX-first background execution.

When all four workers are occupied, acquisition waits up to **10 seconds**, then
reports `helper_busy`. No unbounded extra worker is launched for a full pool.
A closed client cancels acquisition; active workers retain their Esc handling.
Requests already dispatched are never automatically replayed. Close finished
sessions promptly so both worker and app ownership become available.

Worker sockets live beneath macOS's private per-user temporary directory, keyed
by build and slot. Both peers still verify signatures. Workers exit after the
configured idle timeout. Missing/refused startup may fall back to the ordinary
per-command route before any application request; that route uses the same app
and input locks. Diagnostics report the effective route and helper PID.

The mode is opt-in. In a one-Mac development comparison (20-command medians,
same signed launcher), per-command apps/observe took 488/503 ms and warm took
244/281 ms. These are command startup/observation timings, not end-to-end task
or model-token savings. Ten simultaneous observation pairs subsequently passed
in both modes. An earlier isolated 32-second Safari AX stall did not reproduce;
its root cause remains unconfirmed. Preserve observation diagnostics if it recurs.

For bounded experiments only, `TACTO_HELPER_IDLE_SECONDS=1..600` overrides the
idle timeout and `TACTO_HELPER_WAIT_SECONDS=0..30` changes lease waiting. The GUI
keeps its simpler minute range. Resident XPC/SMAppService remains an unshipped
[experiment](../spike/helper-modes/README.md); no resident agent is registered by
this option.

## Parallel receiving verification (2026-10-29)

The signed development launcher/helper held four owned background AppKit sessions
ready simultaneously. Three workers then performed AXPress callbacks whose
0.8-second handler intervals overlapped; each app reported its own requested
result, the fourth app stayed unchanged, and the sampled foreground app stayed
the same. This demonstrates overlapping native execution, not just overlapping
connection lifetimes. It is not a token or end-to-end speed benchmark.

The same check confirmed pool exhaustion, same-app contention, contention from
the per-command route, and a fresh observation after the owning session ended.
A separate cross-process lease test confirms that terminating the holder
releases ownership. Tests use owned fixtures rather than user documents.

To reproduce after building the signed source launcher and helper:

```sh
TACTO_RUN_NATIVE_PARALLEL_TESTS=1 .venv/bin/python -m unittest discover -s tests -p 'test_helper_parallel_native.py' -v
```

Ordinary discovery skips this UI test. These results do not qualify rc.11 or
claim concurrent windows within one app.
