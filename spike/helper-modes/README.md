# Optional helper transports — development prototype

Not installed or enabled by default. The existing per-command LaunchServices
helper remains A. These options reuse the same signed launcher, Python runtime,
request validation and helper handlers; they do not add an unsigned broker.

| Mode | Selection | Lifetime | Not available |
| --- | --- | --- | --- |
| A | unset / `per_command` | one helper per command | existing behavior |
| C | `TACTO_HELPER_MODE=warm` | private Unix socket, idle exit | cold LaunchServices start, then A if unavailable |
| B | `TACTO_HELPER_MODE=resident` | optional SMAppService agent and NSXPCConnection | A when unregistered, disabled, incompatible or refused |

Both reused helpers accept **one driving connection at a time**. A competing
launcher retries acquisition for up to 10 seconds before screen work; it does
not fall back and bypass that helper's lease. `TACTO_HELPER_WAIT_SECONDS=0..30`
sets that bound (0 refuses immediately). A timeout reports `helper_busy`,
`effect=not_started`, and retry guidance. Esc held during waiting or a closed
child channel cancels acquisition; application frames are never retried. This serializes the globals inside one helper, not all processes
or mixed modes across the system. Production session-object isolation remains
separate work. Esc and invalidation stop the current lease; new connections
receive fresh observation/document/menu tokens, abort epoch and runtime status
ID. Late capture callbacks are fenced by a connection generation.

## Build and compare

Use a disposable checkout and the intended Developer ID identity:

```sh
uv sync --frozen --all-extras
export TACTO_SIGN_IDENTITY=<Developer-ID-Application-fingerprint>
bash helper/build.sh
bash helper/bundle.sh
bash launcher/build.sh
TACTO_DEV="$(pwd)/launcher/tacto"
```

Run **all A/B/C cases against this same `TACTO_DEV` path**, including a refreshed
A baseline. Comparing installed Preview 20 A with a different checkout/Python
for B/C would confound the result. The supplied benchmark's `--tacto` option
selects this path; do not replace `~/.local/bin/tacto` or `/Applications/Tacto.app`.

```sh
"$TACTO_DEV" apps --helper
TACTO_HELPER_MODE=warm "$TACTO_DEV" apps --helper
TACTO_HELPER_MODE=resident "$TACTO_DEV" apps --helper
```

Requested modes emit one metadata-only JSON line on stderr naming requested and
effective mode, fallback reason, and helper PID. A fallback is **not** a resident
or warm speed sample. Observe replies also expose `helper_transport`.

### Warm

The build-specific socket lives in a checked, user-owned 0700 directory under
`/tmp`; socket and launch-lock files are 0600. Both ends authenticate the signed
peer identifier and actual signing team. Protocol/build mismatches are refused.
An idle helper exits after 120 seconds. `TACTO_HELPER_IDLE_SECONDS=1..600`
selects the timeout **at its cold start**; an already running helper keeps its
original timeout. New builds use a different socket and old helpers leave on
their original idle timeout. Small private lock directories remain; never
delete a live socket/lock directory to force an update or kill a production
helper by process name.

### Resident registration (only during the experiment)

```sh
python spike/helper-modes/build-host.py --output reviews/mode-host
MODE_HOST="$(pwd)/reviews/mode-host/TactoModes.app/Contents/MacOS/TactoModes"
"$MODE_HOST" status
"$MODE_HOST" register
# Measure B; verify effective=resident, not a fallback.
"$MODE_HOST" unregister
```

The host embeds an exact copy of the signed helper; launcher and agent check
each other's identifier/team with NSXPC signing requirements. Registration may
be unavailable or require OS approval; default mode A remains usable. Always
unregister in cleanup, including failed measurements. Before replacing a host
or changing build, unregister the old host and verify its agent departed.
The build script refuses to overwrite an existing host. Agent idle exit defaults to
120 seconds; pass `--idle-seconds 5` to build-host.py for a short experiment.
Unlike warm's cold-launch environment option, resident's timeout is embedded in
its signed host plist; rebuild a separate host after unregistering the old one.
Launchd can start it again on the next connection while registered. After a
crash, launchd throttling may temporarily refuse the XPC connection; that initial
acquisition falls back to A. This is availability recovery, not resident reuse.

## Failure boundaries and validation

Fallback is allowed only before the first application request. Once a request
was forwarded, a lost connection or deadline returns an unknown effect and
never reconnects/replays in that run. XPC's socket bridge is inside the signed
launcher. Its kernel socket peer is the launcher itself, so it deliberately
does not fabricate the Unix `helper_peer` audit-token metadata; the agent is
authenticated by the XPC signing requirement.

Development checks cover real signed warm PID reuse, warm unsigned-peer refusal,
idle exit/restart, registered resident PID reuse and AX access, resident
wrong-identifier refusal, unregistered resident fallback, private-path and frame
handling, lease exclusion and stale-callback fencing. Benchmark throughput,
real Esc, simultaneous full agent runs, update cleanup and broader app/permission
cases still require the separate experiment. No stable-release qualification
or system-wide input coordination is claimed.

### Concurrency follow-up

Warm acquisition/authentication remains separate from the serial worker. Once a
lease is acquired, session reset, all request handlers and cleanup now use the
same serial worker as resident. Busy clients never enter the worker or touch AX
state. Initial failed/slow Safari AX reads in the external experiment were not
reproduced in the three-pair pre-change check; serialization aligns execution
paths but does not by itself establish their cause. Repeat read-only concurrency
and retain raw `window_listing_diagnostic` on a failure before attributing it to
lease traffic or claiming the transient is eliminated.

Follow-up signed checks: both modes completed 10 concurrent observation pairs
(20 commands each), with no fallback, AX error or long stall; maximum command
latency was 425 ms warm and 494 ms resident in that run. A held read-only session
refused a zero-wait contender with `helper_busy`; another contender waited until
release and then succeeded. Five-second idle settings exited after 5.1 s warm
and 5.6 s resident. These are bounded development checks, not a guarantee against
all AX stalls or a substitute for repeated performance qualification.
