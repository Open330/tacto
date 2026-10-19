# Window preparation diagnostics

Read the signed backend's original `preparation_diagnostic` when an observed
window cannot be prepared or receive input. This is a closed diagnostic about
that call; it is not a new permission or a readiness certificate.

| Reason | What the evidence establishes | Next action |
|---|---|---|
| `readiness_deadline`, `readiness_nodes` | Full modal/sheet validation did not finish within its deadline/node ceiling. | Report the bounded refusal and its progress. A window may exist. Do not activate apps, close windows or bypass the guard to force success. |
| `identity_changed` | An observation/process/document binding check failed. | Preserve `identity_cause` when present. It does not by itself prove process exit or the underlying cause of an unavailable read. Re-observe for diagnosis; the old index is not a new input authorization. |
| `readiness_attribute_failed`, `readiness_batch_failed`, `readiness_batch_malformed` | Required AX data was unavailable or malformed. | Report the closed code and original effect. Keep the signed backend; do not interpret unknown data as an empty window or clear modality. |
| `target_window_not_focused`, `target_focus_outside_window`, `target_input_not_focused` | The requested window/input does not satisfy the required routing binding. | Read the current window/focus state and report the specific mismatch. Existing task authorization does not authorize taking the user's foreground or pointer. |
| `dispatched_unverified` | The input route completed dispatch but has not established the receiving result. | Verify the exact receiving state. Do not resend to obtain a more reassuring response. |

`phase`, timing, operation counts and `readiness_progress` help locate a failure.
For example, current Finder profiling can expose11 windows yet stop on a
non-target tree. That is a preparation bottleneck, not evidence that Finder is
invisible. Progress is partial: completed windows do not prove every remaining
window is clear. A read-only preflight has `authorizes_input=false`; an evaluator
PASS can mean only that the measurement worked. `preflight_deadline` and
`preflight_nodes` remain incomplete readiness outcomes.

Read `effect` and the original event history together. `not_started` applies
to the refused call, not a prior titlebar preparation, focus click or Save.
`unknown` after attempted input is not a reason to replay it. Preserve the
original journal when reconciling uncertain consequential effects; diagnostic
metadata and sampled input preservation are not completion receipts.

For read-only Finder profiling through the installed signed launcher:

```sh
nlc eval signed_finder_readiness_profile
nlc eval signed_finder_readiness_single_profile
```

The diagnostic-only `readiness_preflight` request accepts optional
`read_strategy='batch'|'single'` (default batch). Both walk the same complete
exposed trees with the existing15-second/20,000-node/depth64 limits and fresh
identity, abort and modal/sheet checks. The field is rejected on input commands;
the reply is always `read_only=true, authorizes_input=false`. Single mode uses
the existing strict individual-attribute reader; it does not change preparation's
default batch route. A single-mode measurement requires the helper's matching
strategy acknowledgement. `readiness_attribute_costs` contains only bounded
attribute categories, operation counts and time, including outer metadata reads;
batch packets are not divided into invented per-attribute costs.

Current single/batch/batch/single measurements at11 Finder windows all left
readiness incomplete. Batch reached5,887/5,830 nodes; single reached2,741/2,674.
This rules out switching preparation to individual reads on this evidence.
It is not a stable GUI snapshot comparison or proof that New Folder works.

`nlc eval signed_readiness_scale_systemwide` runs owned preparation fixtures with
system-wide focus cross-checks instead of the application-frontmost reader.
The fixture's activation callback count and the sampled foreground/focus result
are separate observations. The receiving oracle waits for the original request's
fresh A-key state, not merely an activation callback; contradictory B input is
terminal. It never repeats preparation. Even a receiving PASS with
UNCHANGED_SAMPLED has `input_preservation_verified=false`: real concurrent human
typing, caret/selection and pointer preservation remain separately required.

`nlc eval signed_finder_cpu_profile` adds a read-only whole-process CPU snapshot
immediately before/after the full preflight RPC. It first checks the current
host's Mach-timebase conversion against its own POSIX CPU time; a failed check
refuses the measurement. PID/start/timebase changes or a regressing counter
are errors, never zero CPU. Process CPU can exceed elapsed time across threads.
It cannot attribute time to AX alone, one thread, remote serving or billable cost.
Current preflight still times out; high Finder CPU and sampled AppKit cell
resolution stacks guide further investigation, not a readiness permission.
