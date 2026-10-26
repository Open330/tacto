# Tacto access records

Tacto automatically records supported computer-use CLI invocations. Open
**실행 기록 보기** from the app or menu bar to read them inside Tacto: search
by app/task, filter failed or incomplete runs, select a run and follow its
operation timeline, duration and result. New records refresh while the history
window is active; closing/minimizing it or switching apps pauses refresh work.

**설정** (also Command–comma) controls automatic logging, retention (7/30/90
days), and **작업 중 배너 표시**. The fixed first task always brings its
verified result document forward; it has no presentation setting. Disabling the banner hides only the upper-right panel; the existing
Tacto menu shows active tasks and **현재 작업 중단**, and Esc still stops input.
When the banner is on but the target's windows leave no free corner on any
screen (a maximized browser), the task continues the same way without the
panel instead of being refused; a panel that exists but cannot be seen still
stops the task.
Helpers do not create a second menu bar icon. Status is transient metadata
(app name and stop state), never screen contents or input values. Changes
are saved immediately and apply to the next invocation; disabling logging does
not erase existing records. **저장된 기록 모두 삭제…** asks for confirmation and
removes recognized local access receipts, preserving saved workflows and other
files. Mac permissions and agent connection remain available through
**Mac 권한·에이전트**.

For file-level diagnostics, `tacto logs` lists recent filenames and `tacto logs
--json` returns metadata. `tacto logs --open` opens the underlying macOS folder,
`~/Library/Application Support/Tacto/Logs/`. The app's normal record viewer does
not require navigating that folder.

Each CLI invocation has one JSONL file with ordered command/access start and end
records, target bundle identifiers, operation/action names, outcome codes and
elapsed time. A failed `run` or `session` adds a `task_result` with a closed
failure code and stage (for example `invalid_flow`/`load` or
`step_not_authorized`/`authorize`), so the history names why it failed instead
of only showing the exit code. First-task results also include the stage, failure document counts,
and whether foreground presentation was verified. A missing end record means an
interrupted or unknown outcome; it never authorizes repeating an input.

These are Tacto's observable accesses, not the outer agent's full conversation
or private reasoning. Screen trees, screenshots, field values, document titles,
paths from input arguments, prompts and provider replies are not stored in the
automatic access receipts. There is no automatic upload. Detailed screen/action
traces remain an explicit separate `--trace` option on do/run/replay; review a
trace before sharing it because it contains screen data.

The `auth fill`/`auth login` route records credential input and
submit operation outcomes, never Vault values or response bodies. Full-screen
tracing is unavailable on this credential route. Delegation files contain only
selectors, provider/item references, scope and expiry; local credential values
are stored in macOS Keychain, not in these JSON files. See
[credential delegation](../skill/references/credentials.md).

The Tacto/Logs directories are owner-only on POSIX, with 0600 files. Receipts
are capped at approximately 1 MiB per invocation, 200 files, 20 MiB total and
30 days by default (adjustable in the app). Cleanup occurs at the next logged invocation and only removes owned
files matching the receipt naming scheme. A capped invocation records
`log_truncated` and still attempts to record command termination. Storage failure
prints one short diagnostic and does not interrupt the computer-use operation.
Files can be deleted locally; the next invocation creates a fresh receipt.
