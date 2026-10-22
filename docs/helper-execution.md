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

One warm helper drives one signed connection at a time. Concurrent commands
wait up to **10 seconds**, then report **`helper_busy`** (`effect=not_started`,
`retryable=true`) with retry guidance. Esc held while waiting or a closed client
cancels acquisition. A request already dispatched is never automatically replayed.
A session or batch keeps its lease until it ends; leave it promptly when finished.

The warm socket is private to the user and signed helper build. Launcher and
helper authenticate each other; a new build uses a new socket, and older helpers
exit after their idle time. Missing/refused startup can fall back to the usual
per-command route before any application request. A busy lease never bypasses
its owner through fallback. Optional mode diagnostics on stderr report the
requested/effective route and helper PID, so a fallback must not be counted as
a fast-mode benchmark sample.

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
