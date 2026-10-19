# Repeat one saved Flow

`tacto batch /absolute/template.json --helper --max-jobs 10` keeps one signed
connection for a bounded batch. It does not load the authoring guide or send
full Flow JSON each time. For interactive tools use tty:true; wait for
`TACTO_BATCH_READY`, then send `{"params":{"amount":"17","result":"85"}}` plus
a newline with write_stdin. All values are text; defaults fill omitted params.
For interactive/dependent work, wait for `TACTO_BATCH_RESULT` with exit:0 before starting the next job. EOF or the
job limit ends the batch. The default limit is10 jobs and idle deadline90s;
`--idle-timeout` allows1..600s. Records are bounded to64KiB.

Each job binds the immutable template and runs the ordinary replay engine with
fresh observations, authorization and completion checks. Each successful result
is separate; it does not authorize the next task. Requests cannot change the
Flow, target, contracts or checks. Interactive approval is not accepted. Model selection is disabled unless
`--auto-repair codex` or `--auto-repair claude` is explicitly enabled. Use run for document scope, approval, judgment or a dispatch journal.

Without automatic repair, failure stops the batch. Unknown completion, invalid
input or observed activation always stops it. Inspect state before deciding what to do next; do not resend completed
or uncertain jobs. This is connection reuse, not automatic journal resume or
exactly-once delivery. Pass --timings only when diagnosing timing; normal output
is completion/result markers, not repeated full observations.


When all parameter rows and transitions are already known, use one JSONL file
or deterministic caller instead of involving the model for every row:

```sh
tacto batch /absolute/template.json --helper --max-jobs 10 < /absolute/jobs.jsonl
```

Buffered requests are processed in order and stop at the first failure. Read
result markers to distinguish completed jobs from the stopped job; do not
restart the file after a failure. The Flow itself must prepare each new task.
Batch does not reset application state or synthesize a resume checkpoint.

After an action passes its result predicate, the next job still reads a fresh
tree but avoids repeating the helper's fixed post-action settle delay. Without
that verification, the ordinary delay remains. Observations are never reused.

For selector drift, opt in to `--repair-packet /absolute/new-packet.json`.
A stopped job's result points to the narrow packet; completed jobs do not
write it. Read `selector-repair.md` only at this failure. The batch closes at
failure; queued jobs never run. Keep the request rows outside model context
and use result ordinals to track which jobs completed.

For automatic intervention use:

```sh
tacto batch /absolute/template.json --helper --auto-repair codex < /absolute/jobs.jsonl
```

Read [automatic repair](auto-repair.md) only when enabling this mode. Successful
jobs use no model. A missing/ambiguous selector may invoke a text-only agent,
verify a stable checkpoint and replay only the failed suffix. The repaired
selector is reused for later jobs in this batch. The original file is unchanged.
