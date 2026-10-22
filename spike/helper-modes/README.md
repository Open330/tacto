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
connection is refused before screen work; it does not fall back and bypass that
helper's lease. This serializes the globals inside one helper, not all processes
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
The build script refuses to overwrite an existing host. Agent idle exit is
120 seconds; launchd can start it again on the next connection while registered.

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
