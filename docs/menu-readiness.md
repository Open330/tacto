# Read-only menu settling

After a dispatched window preparation, a menu may still be disabled when first
read. `wait_menu_enabled` repeats valid disabled reads within a deadline and read
limit. It never repeats preparation, presses a menu, activates an app, or grants
input authority. Each read uses the trusted backend; this wrapper does not pin
a document or process across polls. Reobserve and bind those before acting.

```python
from tacto.menu_readiness import wait_menu_enabled

state = wait_menu_enabled(driver, 'com.apple.TextEdit', ['File', ['Save', '저장']],
                          timeout_seconds=1, cancelled=host_stopped)
if state.state != 'ready':
    # Stop the task; do not replay a possibly dispatched preparation.
    raise RuntimeError(state.reason)
# Fresh scoped observation, native guard, approval and receipt still required.
```

The default budget is one second, a 50ms polling interval and 64 reads. A slow
in-flight read may outlast the deadline; its late enabled result is refused.
Errors and malformed responses are never converted into disabled state or
retried. Host cancellation and the signed backend's user-stop response end the
wait. The result contains only closed reasons, counts and observed booleans.

```sh
tacto menu-state com.apple.TextEdit File Save --helper --wait-enabled 1
```

With `--wait-enabled`, exit 0 means ready; other results exit 1. Invalid durations
exit 2 before Python driver construction. CLI durations are finite 0..60 seconds;
the 64-read cap still applies, so a large timeout does not promise that many
seconds of polling. Zero issues no menu reads. Without this option, the existing
single-read command and exit semantics are unchanged. Ready means availability,
not completion, an atomic UI lease, or a Save/delivery receipt.

`tacto eval signed_menu_readiness` is an explicit isolated GUI/native evaluation.
Its owned app enables a menu after the first signed disabled read. Independent
receiving state must remain `invoked=0`; no command is pressed. Offline skips the
fixture and model. This controlled transition does not prove that a real
TextEdit/Finder responder will settle after preparation.
