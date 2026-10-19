# First-run quality review — 2026-10-19

Claude peer and Codex agreed on the following split and implemented it in the
shared source tree. Claude's Windows work stayed in its separate worktree.
This source update follows `v0.1.0-preview.6-first-run.5` and is the source
for the `v0.1.0-preview.7` release candidate. Final artifact and installation
results are recorded with the published release.

| Owner | Concrete issue | Change |
| --- | --- | --- |
| Codex | Failed Helper connection prints a traceback | One JSON result: `connect/helper_unavailable`, no input or direct-backend fallback, short diagnostic hint |
| Codex | Read exceptions or malformed observations escape the first task | Reuse normal replay's read normalization at all three observation stages; report the failed stage without repeating input |
| Codex | Native launch lookup/dispatch raises; missing dispatch receipt can cause a repeat | Short native errors; distinguish not-started from unknown effects; mark dispatch before sending and send at most once |
| Claude | Terminal opens and the button immediately enables another start | Shared controller state blocks button and method reentry for a bounded 20-second hold; expired pending launchers are removed |
| Claude | nvm/fnm CLIs are missed by the GUI; `env node` cannot find adjacent Node | Inspect version directories without executing a shell; prepend the selected CLI's bin while retaining the existing PATH |
| Claude | A selected CLI disappears and starting silently does nothing | Restore a valid default selection or show a short message |

Claude reviewed the Python changes with no blocking findings. Codex reviewed the
Swift changes and requested the shared method guard. The final controller check
renders real AppKit controls, exercises ready/opening/handed-off/expired states,
and directly reenters the start method during both holds. A test-only external
Terminal stub ensures a broken guard cannot start an actual agent.

## Verification

- **92 affected regression checks passed**, including generated shell argument
  boundaries, nvm execution with a fake adjacent Node and a minimal PATH, fnm
  version-path ordering, CLI failures, normal replay routes and GUI state changes.
- The actual unsigned Python invocation without an inherited channel returned
  short JSON, exit 1, no traceback and no UI input.
- One actual signed first task on the development Mac verified its new document's
  visible text: 5.479 seconds, zero internal model calls, no Save command. This
  single run is a functional check, not evidence of a speed gain or external
  agent token usage.
- The unmodified GUI sources compiled and rendered ready/light, ready/dark and
  missing-permission snapshots. Existing layout assertions passed; the ready
  content height remained 420 points. Prompt-copy verification passed and
  restored the previous clipboard contents.

Targeted verification command:

```sh
.venv/bin/python -m unittest \
  tests.test_first_run_gui tests.test_first_run_agent \
  tests.test_standalone_preview tests.test_release_onboarding tests.test_drag_preview \
  tests.test_first_run tests.test_app_launch tests.test_first_run_cli \
  tests.test_cli_read_routes tests.test_cli_provider tests.test_cli_document_scope \
  tests.test_cli_dispatch_journal tests.test_cli_event_timing \
  tests.test_cli_authoring_rounds tests.test_flow_session
```

## Limits

The hold mitigates rapid repeated starts. Script deletion means the agent session
started, not that the task completed; a later intentional start is still possible.
This is not a cross-process idempotency or agent-completion protocol.

Version-manager fixtures use a fake Node process. Actual user Terminal execution
with nvm/fnm remains to be checked in a new signed app. asdf/nodenv and installations
where Node is outside both the CLI bin and inherited PATH are not added here.
No initial-run wait, new approval screen or permission reset was introduced.
Private screenshots and detailed logs are retained separately from tracked docs.
