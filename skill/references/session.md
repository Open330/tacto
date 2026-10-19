# One-flow session

`tacto session <bundle-id> --helper [--guide] [--timings] [--idle-timeout 1..600]`
observes one app, reads one newline-terminated Flow JSON record from stdin,
replays it on the same signed connection, and exits. The default input wait is
90 seconds. The input record is bounded to 1 MiB. EOF, cancellation, invalid
JSON, extra records in the input buffer, or a changed target end the session.
A failed flow is never rerun by the session.

For agent tools, start with `exec_command`, `tty: true`, and
`yield_time_ms: 1000`. Wait for both the observation and `TACTO_SESSION_READY`.
Send a minified JSON line with `write_stdin` using the exact returned session ID.
Use `yield_time_ms: 5000` when sending the flow to collect short replay completion
without an extra status round; if still running, poll the original session.
Never resend the flow to obtain status. Append an actual newline, not the two
literal characters backslash+n.
Use the original session for polling when initial observation takes longer.

The session temporarily disables canonical line limits and input echo on its
own terminal and restores its settings afterward. The initial observation does
not authorize actions. Replay observes again, resolves stable selectors, and
keeps all existing policy, binding and completion checks. Observation IDs are
not exported for use by another invocation; no daemon, shared socket, or
permission change is introduced.

This version accepts deterministic flows only; `decide` and `visual` are
refused. It does not read stdin as approval or accept `--yes`, document scope,
provider flags or a restart journal. Use the ordinary `run`/`replay` route for
those workflows. Existing user-intent authorization and host completion
receipts are still evaluated by the normal replay engine. Close or interrupt an
idle session if abandoning the task; it also expires on its input deadline.


`--guide` returns trusted minimal Flow instructions together with the observation;
no separate authoring reference is needed for ordinary clicks. For compact steps,
`{"click":"<identifier>","equals":"<result>"}` expands before the normal parser.
`skip_equals` expands to skip_if. Checks require an explicit check_defaults.field;
shorthand cannot also specify action/selector or duplicate the corresponding check.
Contracts and checks are not inferred. Full Flow JSON remains supported.

`--timings` also reports monotonic offsets for observation ready, input ready,
flow received, replay done and session done. Input waiting includes model/client
and transport delays; it is not a measure of model inference alone.


Reusable save: read [reuse](reuse.md) only when a saved parameterized template
is needed. `--reuse-guide --save /absolute/new.json` executes once and saves
the unbound template after success; do not replay a completed task to save it.
