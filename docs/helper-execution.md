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

## Parallel workers (rc.12 and later)

rc.11 keeps its single warm connection. rc.12 and later use
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

The signed source launcher/helper held four owned background AppKit sessions
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

The rc.12 release subsequently repeated this check through the installed signed
launcher/helper. Exact-bundle installation/audit also passed on all three Macs.
See [rc.12 validation](https://github.com/Open330/tacto/releases/tag/v1.0.0-rc.12)
for the installed receiving result and scope. Earlier startup medians above
remain historical measurements and are not relabeled as rc.12 performance.


## Window-scoped coordination (development after rc.12)

Use `tacto windows APP --helper` to discover live IDs without a document tree.
It remains available while other connections own windows in that app. Start
`session APP --window-id ID --helper --guide`, or pass `--window-id ID` to
`run`/`replay`/`observe`. IDs belong to the current OS windows and are never saved
inside a Flow. `--document` remains a separate app-wide TextEdit route.

A window session takes shared app ownership and exclusive ownership of one
process-start/window-ID/AX-window-object identity. Different windows may have
independent connections. Scoped observations contain only the selected window.
Direct AX reads, text values/selected-text edits and supported AXPress are
available; menus, popup selection, credentials that need app focus, keys and
pointer fallback require an ordinary exclusive app session. The helper never
silently broadens a window session. The app may still serialize AX callbacks
on its own main thread; concurrent sessions are not a promise of parallel UI
threads inside every app.

Conflicts wait inside the helper for up to ten seconds, bounded by an existing
input deadline and cancelled by connection loss or user interruption. Waiting
occurs before dispatch; no input or uncertain effect is retransmitted. Old
rc.12 helpers do not provide this automatic resource waiting.

Signed native activation, first-result presentation and URL opening now reserve
app/global input ownership through the helper around presentation. Unrelated
external automation does not join these locks. Scope loss or unsupported window
actions return bounded errors rather than selecting a different window.

The reproducible native check is `TACTO_RUN_NATIVE_PARALLEL_TESTS=1` with
`tests/test_window_parallel_native.py`. The runtime-only benchmark lives in
`examples/benchmark_parallel_windows.py`; it runs four fixed jobs at concurrency
1/2/4, with a sequential installed baseline, repeated rounds and sampled worker
RSS/CPU. It does not measure agent input/output tokens or compare provider
models, and must not be advertised as an agent token reduction result.


Control labels containing line breaks, backslashes, quotes, comma delimiters or
status-like text use an explicit `LabelsV1:` packet of JSON-quoted attributes
(`observe.labels_v1`). Clients decode exact values before constructing selectors
or native expectations. A literal backslash-n remains distinct from an actual
line break; malformed marked packets do not fall back to legacy extraction.
Simple controls retain their compact legacy format. Window/Link quoted identities
retain their existing format. No native identity guard is relaxed.

The optional `action.credential_paste` path supports approved `set_value` with
`input_method: paste`, including Vault-referenced fields. It holds app/global
input ownership, verifies exact focus/origin, selects existing input for replacement,
and invokes native Edit/Paste. Clipboard item/type data are preserved in memory
(up to 8 MiB), with conditional restoration under an unchanged change count.
Clipboard restoration is best effort under external clipboard races; no atomic
cross-application clipboard transaction is claimed. Native dispatch requires
fresh receiving-state observation and is never automatically repeated.
