# Runtime update — Preview 3

This developer preview integrates the current runtime, skill and regression suite
onto the public repository history. It preserves the existing website design and
updates its download target to Preview 3.

- Native candidate paging keeps an open selector and its scroll control together;
  native 131-option selection completed with Codex and Claude on owned fixtures.
- Foreground preparation avoids redundant focus writes and protects editable
  focus outside the target window. Readiness failures retain bounded diagnostics.
- Session input rejects checks without a comparison predicate before any input.
  Stored recipe loading and legacy skip conditions retain their existing meaning.
- Literal parameters, bounded repair, model deadlines and provider usage metadata
  are integrated with their tests. Raw prompts, screenshots and local run logs are
  not part of this source update.
- Optional benchmark profiling separates event arrival, Flow input waiting,
  runtime observation and replay. It does not measure pure provider inference.

The new latency measurements include both a fast Tacto run and an error/recovery
run. Lower input-token usage is not a guarantee of lower elapsed time. See the
[latency profile](tacto-latency-profile-20261019.md) and
[native comparison limits](tacto-native-matched-validation-20261019.md).

This remains an Apple Silicon macOS developer preview. Fresh-Mac installation,
full environment parity, actual human typing/caret preservation and broad
real-app reliability are not certified by these tests. Windows and Intel Mac
support are not introduced by this update. The public release includes the signed
and notarized app only after artifact validation; tests alone are not notarization.


## Artifact and installed validation

Preview 3 completed app/DMG notarization, stapling and Gatekeeper verification.
The exact DMG was installed locally and retained both existing grants and agent
connections. A zero-model owned native Flow smoke passed against the installed
launcher. The clean-checkout regression ran 2,605 tests with zero failures/errors
and three skips. See [release validation](release-readiness.md#2026-10-19-preview-3-update).
