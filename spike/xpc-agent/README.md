# Spike: launchd-managed XPC agent instead of a helper per command

Not shipped. This measures whether the helper can be started on demand by
launchd (registered with `SMAppService.agent`) and reached over XPC, instead of
being launched through LaunchServices for every command.

Built with `TACTO_SIGN_IDENTITY=<Developer ID hash> ./build.sh <out>`. The agent
is signed as the installed helper's identity (`ai.callabo.tacto.helper`, team
728FW73BS8); the client as the launcher (`ai.callabo.tacto`).

## Measured on macOS 27.0.1 (2026-10-21)

| | Result |
|---|---|
| Registration | `SMAppService.agent.register()` → `enabled`, no approval prompt |
| Accessibility grant | carried over: `AXIsProcessTrusted() == true` in the launchd-started agent (parent pid 1), same identity as the granted helper |
| Screen Recording | `false`, as for the current helper (that grant belongs to the setup app) |
| First connection, agent not running | 67–102 ms (launchd starts it) |
| New connection, agent running | 6–8 ms; 51–54 ms including the client process start |
| Today's helper launch per command | about 350 ms (`apps --helper` 0.43 s vs `list` 0.09 s) |
| Per-connection state | every connection gets its own session object; counters start at 1 |
| Client with another signing identifier | refused (`setCodeSigningRequirement`, checked by the system per message) |
| Idle | exits after 10 s without connections; launchd starts it again on demand |
| From an SSH session | connects and is trusted (88 ms) |

## What production would need

1. Helper `--xpc-agent` mode: `NSXPCListener(machServiceName:)`, the client
   requirement pinned with `setCodeSigningRequirement`, and one session object
   per connection owning what is global today (latest observation, elements,
   process start, document and save-panel tokens, pending menus, abort epoch,
   control panel and every async callback, tagged with the connection).
2. One driving connection at a time: a second one is refused or waits, since two
   runs must not send input to the screen at once.
3. The setup app registers the agent; its plist points at the embedded
   `Tacto Helper.app` executable, so the existing grant applies (measured).
4. The launcher tries the XPC agent first and falls back to today's
   LaunchServices launch when it is not registered, disabled in Login Items,
   or refuses the connection. The Python protocol is unchanged.
5. launchd owns the lifetime: no launcher terminates a helper another
   connection uses, and the agent leaves when idle.

## Before adoption (review, 2026-10-21)

The numbers above are ping round trips, not an end-to-end task speedup. A
production change must first settle:

1. One driving lease, enforced by a serial executor. A dropped connection
   discards its pending input, callbacks and tokens.
2. No automatic replay of a write after an agent restart, interruption or
   invalidation; an uncertain effect stays `unknown`. Falling back to the
   LaunchServices helper is allowed only when the connection was refused
   before the first request.
3. Atomic app update and rollback: after unregistering or replacing the app,
   measure that no old launchd process or plist path survives and that the
   grants still apply.
4. A protocol, version and capability handshake, since another version of a
   client with the same identity can connect; per-session size and deadline
   limits.
5. Every piece of global state (observation, documents, menus, abort, banner)
   moved into the connection's session, with regression tests for concurrent
   connections, reconnection and Esc.

This is follow-up work. It is not part of the current preview release.

Open items: registration posts a one-time "background item added" notice and
lists the agent under Login Items, where a person can turn it off (the
fallback covers that); an Esc in one connection must stop only that run.
