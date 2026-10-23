## When a run fails

A compiler correction may stop with a model deadline error after a long
`--verify` run: verification time consumes the shared authoring deadline.
Inspect the verified/unverified result before requesting another compile.

| error | what it means |
|---|---|
| `runtime_value_missing` | a required captured/runtime value is unavailable; resume from a verified checkpoint that produces it |
| `target_missing` / `target_ambiguous` | selector matched 0 / >1 — re-observe and tighten it |
| `unverifiable_input_step` | a typing step has no `expect` |
| `contract_invalidated` | the recorded facts changed; re-author, don't force it |
| `contract_required` | a step that acts has no `safety` contract |
| `invariant_unverifiable` | the binding pins something this run cannot re-observe — fail closed |
| `unverifiable_action` | the policy blocks this effect outright (not a confirmation) |
| `precondition_failed` | the target is `[disabled]` |
| `postcondition_timeout` | reports the value actually read — usually the wrong field or format. The action itself was delivered, so this never means "nothing happened" |
| `postcondition_already_true` | a `Return` step whose `equals` was already true before the press — nothing was pressed. Return commits, so a result that proves nothing is not taken; check the step's postcondition names what Return should change |
| `commit_unverified` with `destination_not_shown` | the loaded Safari page did not match the pinned URL. Observe without resending; for a newly authorized navigation, use the address-editing preparation in [Safari](safari.md) |
| `target_uniqueness_unverified` | the target was found past a list's cap, but the scan did not reach the list's end, so another element of that name may exist; a step with consequences (delete, send, save…) was not run. Nothing was pressed. Narrow the list (a smaller folder, a search, a filter) or select the item by a path the app offers, then run again |
| `target_seen_in_front` | an earlier step's app was observed in front afterwards (e.g. Finder after Go > Go to Folder…), so nothing more was sent to it — the person may be using it now. What the earlier step did stays done; use a path that keeps the app behind (for Finder folders, `open_folder`) |
| `focused_document_guard_unavailable` | a File > Save bound to one file (`focused_document`, e.g. `textedit_save`) on a backend that cannot check the focused file at the press — nothing was sent. Use `--helper` with a helper that announces `action.menu.focused_document` |
| `expect_not_met` | the step's `expect` text never appeared |
| `retry_refused` | after a recovery, the one allowed re-run of an idempotent step was refused |
| `capture_failed` | a non-optional `capture` found nothing to read |
| `invalid_action` | unknown action, parameters it does not take, or a malformed `decide` |
| `selection_label_required` | a select step has no `option_label` to classify its effect by |
| `navigation_contract_required` | Python window-bound keys without the capability, input selector/binding or explicit observed result; no input was sent |
| `coordinate_authorization_required` | direct/signed `click_point` requires host-approved confirm/preapproved scope and helper support; no input was sent. This raw API is not a flow action or an AX click fallback. |
| `window_preparation_contract_required` / `window_preparation_target_required` | `prepare_window` without the capability, a window selector and an exact observed result |
| `unsupported_schema` | this runtime does not support the flow schema; schema 4 carries literal data and must not be downgraded |
| `decision_unavailable` / `decision_invalid` / `decision_failed` | a `decide` step had no model, got an answer outside what it allows, or the call failed |
| `decision_drives_consent` / `composed_text_aims` | a decided value would reach a consent step with no way to ask, or written text would aim at an element |
| `branch_unwalked` / `branch_walk_failed` | a branch answer has no steps and no walker, or the walk did not get there |
| `window_list_unavailable` | AX window-list or role data could not be read reliably; this does not establish that a window is absent. Use the diagnostic guidance below |
| `no_window_content` | no accessible window was observed; inspect the reported `reason` and follow the window diagnostics below. This alone does not prove screen lock or require a user handoff |
| `screen_locked` | the Mac's screen is locked: nothing behind it can be read, launched or brought forward. Stop screen steps and ask the person to unlock (it needs their password); never try to unlock, and do not ask them to show a window instead. Before long unattended or remote work, with their agreement, `tacto keep-awake --minutes N` prevents idle display and system sleep, which commonly leads to the lock; it does not guarantee against a screen-saver, manual or policy lock (`--stop` ends it) |
| `gui_session_unavailable` | this process can't see the GUI (tmux without a user session) |

### When a menu needs the app in front

`needs_foreground` with `effect: not_started` means the app disabled that menu
in the background. Bring the requested app forward with
`tacto activate <bundle-id> [--window "<title part>"] --helper` without asking
for a separate confirmation. Then re-observe the exact document and
resume the bound operation. A TextEdit
edit returning `commit_unverified` still needs the bound `textedit_save` receipt
and matching file bytes before reporting that it was saved.

### When the expected window is missing

Use the installed signed backend consistently. Check `tacto apps --helper` and
`tacto doctor --check` for connection or permission errors before attributing the
failure to the target window.

If a window was observed but preparation or input was refused, inspect
`preparation_diagnostic` instead of treating it as an absent window.
`readiness_deadline`/`readiness_nodes` mean the full readiness check did not
finish; `identity_changed` needs its closed cause, not an assumed app exit.
See [window preparation diagnostics](../docs/window-preparation-diagnostics.md)
for interpreting these refusals and the effect of earlier actions.

- If observation succeeded but the expected dialog/control is missing, read
  `tacto observe <app> --helper --all-windows` once. File choosers can be separate
  windows; scope selectors to the intended dialog rather than another document.
- `window_list_unavailable` means the window list is unreadable or malformed,
  rather than empty. Keep the signed backend; do not use that error as grounds
  for `reopen_window`, unhide or new/open commands. The runtime's read-only
  waits can re-observe classified transient AX failures within their original
  deadline. If the error remains, report its closed diagnostic and permission
  check result; do not claim a user action will resolve an unknown cause.
  With `reason: app_accessibility_unresponsive` the cause is known: the window
  server shows the app's windows (`onscreen_window_count`) but the app answers
  accessibility with none. Restarting that app usually fixes it; ask the person
  first, since quitting can lose unsaved work.
- For `no_window_content`, use the reported `reason`. `screen_locked` requires
  the person to unlock; report it and stop screen actions. Never try to unlock.
- `app_hidden`: the current backend already attempts one bounded unhide without
  activating the app. If it still reports hidden, report that recovery failed;
  do not repeatedly retry, activate the app or restore focus yourself.
- `window_offscreen` or `no_visible_window` also covers an app whose every window
  was closed (Safari keeps the last one ordered out). When the person asks to bring
  the app forward, `tacto activate <bundle-id> --helper` asks once for a window as a
  Dock click does.
- `window_offscreen` or `no_visible_window` (a messenger whose window was closed,
  or went to another Space after a context menu): with `--helper`, a recipe may
  start with `{"action": "reopen_window"}` (no selector, no params). It asks the
  running app to reopen its window through LaunchServices without activating
  it, like a Dock click minus the activation. The reply says whether the app
  was observed in front (`activated_target`); verify the window with a
  postcondition. If the app does not reopen a window, report that.
- A context menu left open (after `secondary_action` AXShowMenu): close it with
  `{"action": "close_menu"}` (with `--helper`), which sends AXCancel to the
  app's open menus. Escape is not available and is not a substitute.
- `minimized` or `no_visible_window`: do not claim the screen is locked.
  Distinguish an app with no open document from a minimized or other-Space
  window. A user-requested new/open command can use a recipe starting with a
  menu (the runtime supports menu-only observations). If the task needs an
  existing inaccessible window, report the specific blocker to the person.
- An absent or unknown reason is a diagnostic gap. Report the exact error and
  permission check result rather than inventing a cause. `--all-windows` expands
  an existing tree; it cannot bypass missing AX access, screen lock or a Space.

A CAPTCHA, login or explicit user confirmation is a separate handoff. Window
visibility does not authorize completing these challenges or sending an
attachment. Preserve the requested attachment identity and destination when
resuming after the person acts.

`tacto run <flow> --helper --trace run.json` records what was on screen, so a failure can be
reproduced on another machine with no app and no permissions.


Native image general mode is an explicit trusted Python integration:
`tacto.general.run(..., visual_scope=NativeVisualScope(...))`. A model cannot
supply a scope, approval callback or effect declaration. The host must know the
window and reversible ephemeral operation region; the Envelope still bounds
its effects. Host verification of the receiving result is required before
completion or further input, including when switching back to AX. Consequential
save/send effects and AX recipe promotion are not enabled by a pixel plan.
See [native visual contracts](../docs/native-visual.md).


Python host integrations may explicitly author `visual` steps referencing a
registered `NativeVisualScope` via `replay(visual_scopes=...)`. Parameters are
`scope`, `max_screens` (1–20), and `timeout_seconds` (positive, at most 120).
Host callbacks and pixel coordinates must not be serialized. CLI has no scope
registry. `visual_scope_required` means registration, capability or reversible
contract validation failed before visual input. `visual_step_failed` means the
bounded image loop raised and replay stopped. `visual_step_unverified` means no
independent host receiving proof; input may already have occurred, so do not
retry. `visual_reobserve_failed` stops after a verified image step when fresh AX
observation for the following step fails. These steps may consume model tokens
and do not supply consequential save receipts or automatic pixel promotion.

For closing the final window, use the development runtime's explicit
`"postcondition":{"absent_role":"Window"}`. It accepts only a successfully
enumerated empty AX window list plus a fresh windowless observation. Missing
permissions, a locked session, a failed window-list read, or an app that exited
remain errors. Text `absent` alone cannot authorize this exception. Older helpers
without the structured evidence keep their refusal. Do not resend the close click.
