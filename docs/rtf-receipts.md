# Native RTF save receipts

`FreshRTFReceipt(directory, filename, expected_text, document_path=callback)`
provides `prepare_commit`, `after_commit`, and `commit_expected` for native/eval
TextEdit save flows. It does not drive UI, save a document, approve an action,
or delete files. UI ownership and cleanup remain with the caller.

Choose the canonical absolute destination and exact text before execution.
Use a single nonempty `.rtf` basename, including user-chosen names such as
`보고서.rtf`. Path separators, NUL, dot/dotdot names and other extensions are
rejected. Existing destinations are never overwritten. For repeatable evals,
`nlc-eval-<uuid.uuid4().hex>.rtf` remains a useful collision-avoidance convention;
a UUID is not a security guarantee and is not required for normal use. Construct the adapter before entering a cleanup
block that could remove that destination: construction rejects an existing
file, and preparation checks absence again. The callback must independently
read the saved document's absolute path from the app, such as decoded TextEdit
`AXDocument`. Its return value is compared with the predetermined path; it
never supplies or changes the expected destination.

```python
receipt = FreshRTFReceipt(downloads.resolve(), name, body,
                         document_path=lambda: saved_document_path(driver, name))
result = replay(driver, flow, approvals=approvals,
                prepare_commit=receipt.prepare_commit,
                after_commit=receipt.after_commit,
                commit_expected=receipt.commit_expected)
```

The adapter reuses the single-use, event/digest-bound lifecycle of
`FreshFileReceipt` without changing that class. It pins the directory identity
and opens the artifact relative to a no-follow directory descriptor. Symlinks,
nonregular files, hard links, other owners, directory changes and readback-time
file changes are rejected. If only ctime changes (for example, a Spotlight
extended-attribute update), the entire readback is discarded and retried within
the original deadline. ctime is still part of the identity: a receipt requires
one complete stable read, decode, application-path check, and final stat. Inode,
size, mtime, mode, owner/group and link-count changes remain hard failures.
Continuous ctime changes cannot produce a receipt. This also applies to the
last stat performed by the parent adapter via its strict-by-default protected
hook; normal `FreshFileReceipt` behavior is unchanged. Input is bounded to 2 MiB. Stable bytes are passed on
stdin to `/usr/bin/textutil -convert txt -format rtf -encoding UTF-8 -stdin -stdout`
with the remaining receipt deadline. Decoder output over 4 MiB is rejected.
No output file is requested from textutil, and raw output/errors are not logged.
The decoder process group is killed on timeout.

Decoded UTF-8 text must equal `expected_text` exactly. There is no trimming,
newline removal, substring matching, or expectation inferred from the resulting
file. Missing, incomplete, corrupt, different-content and wrong-document-path
artifacts may be observed again within the deadline; the save action is never
repeated. A receipt is issued only when both file readback and application path
match in time. A second callback use is refused.

`ArtifactEvidence.sha256` remains the hash of **raw RTF bytes**. It is not
replaced by a plain-text digest. Consequently `commit_expected` contains only
canonical location and filename: the generic evidence checker currently
interprets `다시 읽은 내용` as literal UTF-8 artifact bytes, which RTF is not.
Predetermined decoded text is checked inside this trusted host adapter instead.
This adapter is intended for replay/eval integration; passing a generic
`save_file` outcome expecting a plain-text artifact hash will still fail. A
future common formatted-document contract should represent raw and decoded
content digests separately.

`FreshFileReceipt` still requires an owner-only 0700 directory and exact UTF-8
file bytes. The RTF adapter deliberately accepts an owned directory such as
Downloads with 0755 permissions, while rejecting group/other write bits. This
allows other users to read a normally public-readable directory; it does not
provide the private-directory guarantee of `FreshFileReceipt`. Both adapters
lack application-level writer attribution: another process with the same UID
can race a file into the expected path. Stable metadata and AXDocument agreement improve correlation, not cryptographic
provenance. UUID eval names reduce accidental collisions; arbitrary user-chosen
basenames have the same trusted-host and same-UID-writer limitation. ACL-based grants,
filesystem behavior and same-user processes remain host trust assumptions.

The deadline bounds polling and decoder execution. OS filesystem calls and the
host's document-path callback are not forcibly interruptible; a callback that
finishes late cannot produce a receipt. Callers should supply a bounded local
AX lookup and should not automatically retry an uncertain save.

Tests use temporary synthetic files without running TextEdit or touching UI.
Actual textutil checks cover Korean text, empty documents and exact trailing
newlines. Boundary tests cover existing destinations, wrong paths, corruption,
missing artifacts, changed file/directory identity, symlinks/hardlinks, timeout,
late results, event mismatch, repeated use and permissive directory modes.

The ctime boundary tests perform real extended-attribute writes during decoding,
during raw readback, and immediately before the parent's final check. On macOS
builds without Python `os.setxattr`, the tests call the native libc `setxattr`
entry point. A one-time update requires a complete second readback; continuous
updates exhaust the deadline. Content changes still fail.
