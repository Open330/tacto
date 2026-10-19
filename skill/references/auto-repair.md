# Automatic repair of a reusable Flow

`batch template.json --helper --auto-repair codex` automatically intervenes on
an undispatched missing/ambiguous selector. `run flow.json --helper
--auto-repair codex` supports the same continuation for one task. No guide or
model call is needed on success. Use `--auto-repair claude` to choose Claude
instead. Providers are explicit; an observed different provider is refused,
and missing provider telemetry stays unknown.

The agent receives only the failed action, old selector, result predicate and
a page of scoped candidates within an 8192-byte JSON packet. It selects a
page-local candidate index (copy its explicit `index` field), asks for the next page, or declines; it has
no authority to edit contracts, actions, parameters, checks or execute UI work.
Tacto requires an explicit result predicate on the repaired step, false before
repair and verified by replay afterward. Only read/write_ephemeral contracts
can be automatically repinned. It verifies full-tree uniqueness and equality
with the chosen candidate node, preserves scope/parameter pins and
replays only the failed suffix through ordinary authorization/result checks.
Indistinguishable candidate labels or a resolved node different from the choice
are refused. Later batch jobs reuse the learned selector in memory; the saved file is unchanged.

The immediately preceding step must have status ok with an explicit verified
postcondition (a skipped step is insufficient), and fresh observations must
preserve controls, values, flags and named hierarchy before and after the call. Dynamic
steps or on_uncertain handling, parameterized selectors, a first-step failure, missing checkpoints, activation, ambiguous choices
or changing UI stop continuation. Timeout/unknown completion never causes an
automatic resend. Follow the stop marker and inspect state; do not rerun the
entire batch. No automatic crash/journal recovery is implied.

Default model budget is two calls across the batch; `--max-repairs 1..10` changes
it. `--repair-timeout 1..120` bounds each call (default60s). Failure/decline counts
against the budget, including every next-page request. A page preserves all
candidate fields; oversized packets stop rather than silently truncating fields.
The packet budget excludes provider instructions/framing; it is not a token or
whole-context limit. Successful continuation preserves the latest verified prefix
answer unless the suffix produces a newer answer. Earlier captures can feed suffix
parameters/checks through a private in-memory snapshot. Sensitive names and values
stay protected across overwrites and repeated repairs; snapshots are released on
exit and never written into the learned recipe. Missing required values stop
before dispatch. A selector that itself uses captured/parameterized values still
requires review. UI labels containing {{ or }} stay literal data, including saved/reloaded learned
selectors. These exports use schema 4 and need a supporting runtime; old runtimes
refuse them. Literal/reference boundaries are preserved with read-only literal_params,
separate from caller-overridable task params.
This is not process-restart recovery, and offline --remaining
files cannot carry captures. Provider markers include actual reported usage; missing
telemetry remains null. `TACTO_BATCH_RESULT.model_requests` counts repair calls
for that job. No approval/document/journal/trace modes are combined with this
shortcut; use their explicit run handling instead.

With --repair-packet, only a terminal unrepaired static selector failure writes
the packet. A successful automatic repair does not consume the output path.
Saved step indexes refer to the original template; unsupported dynamic flows
do not export a misleading expanded-index packet.

Row/ancestor clicks (click_ancestor) and value-only labels are not supported by
this candidate repair shortcut. Comparing all meaningful AX content also means clocks, live
cursor/state text or other changing AX content can stop repair even when the
main checkpoint holds; use explicit review for those tasks.

Numeric AX indexes and at most two attribute-free bare Group changes per
comparison are ignored only for checkpoint equality. All other content, node
order and retained ancestor paths remain checked. The chosen canonical node
is mapped to its fresh raw index; selectors and result checks use the raw tree.
[Complex scenario evidence](../docs/tacto-complex-scenarios-2026-10-14.md).
