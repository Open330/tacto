# Tacto access records

Tacto automatically records supported computer-use CLI invocations. On macOS,
open **실행 기록 보기** from the app or menu bar, or run `tacto logs --open`.
`tacto logs` lists recent filenames; `tacto logs --json` returns their metadata.
The folder is `~/Library/Application Support/Tacto/Logs/`.

Each CLI invocation has one JSONL file with ordered command/access start and end
records, target bundle identifiers, operation/action names, outcome codes and
elapsed time. First-task results also include the stage, failure document counts,
and whether foreground presentation was verified. A missing end record means an
interrupted or unknown outcome; it never authorizes repeating an input.

These are Tacto's observable accesses, not the outer agent's full conversation
or private reasoning. Screen trees, screenshots, field values, document titles,
paths from input arguments, prompts and provider replies are not stored in the
automatic access receipts. There is no automatic upload. Detailed screen/action
traces remain an explicit separate `--trace` option on do/run/replay; review a
trace before sharing it because it contains screen data.

The Tacto/Logs directories are owner-only on POSIX, with 0600 files. Receipts
are capped at approximately 1 MiB per invocation, 200 files, 20 MiB total and
30 days. Cleanup occurs at the next logged invocation and only removes owned
files matching the receipt naming scheme. A capped invocation records
`log_truncated` and still attempts to record command termination. Storage failure
prints one short diagnostic and does not interrupt the computer-use operation.
Files can be deleted locally; the next invocation creates a fresh receipt.
