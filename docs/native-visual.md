# Native window image reads

`nlc.native_visual.capture_native` provides an optional, read-only ScreenCaptureKit path for one macOS application window. It uses `SCScreenshotManager` on macOS 14 or later. It does not use the obsolete CoreGraphics desktop-capture API, send input, activate a window, or request screen-recording permission.

This path currently consumes the in-process `nlc.driver.Driver` observation registry. It is not connected to the signed helper/launcher transport. Native coordinate actions are not provided, and native captures must not be passed to the browser coordinate executor.

## Installation and permissions

From the project directory:

```sh
uv sync --extra native-visual
```

The extra installs `pyobjc-framework-ScreenCaptureKit>=10.0` on macOS. The project's base macOS dependencies already include the Cocoa, Quartz, and ApplicationServices bindings. Importing `nlc.native_visual` itself remains portable: framework imports are deferred until a native operation is requested.

Both the in-process AX driver and screen capture need their respective existing Accessibility and Screen Recording permissions. `capture_native` calls `CGPreflightScreenCaptureAccess` only. If access is missing it raises `NativeVisualError("screen_capture_permission_missing")` before enumerating shareable windows or submitting a screenshot. It does not call `CGRequestScreenCaptureAccess` or present a permission dialog. Permissions granted to a different process, including a signed helper, do not establish that this Python process can capture.

## Read API

Create an ordinary in-process observation for an application that is already running and has a visible window. The AX driver's own observation behavior remains separate from capture; its `observe` method can launch a missing target, so choose the target deliberately.

```python
from nlc.driver import Driver
from nlc.native_visual import capture_native, NativeVisualError
from nlc.visual import query_visual

driver = Driver()
target = "com.apple.TextEdit"  # An application you intend to inspect.
obs = driver.observe(target, "tree_full", require_window=True)
if obs.get("type") != "observation":
    raise RuntimeError(obs.get("error", "observation_failed"))

try:
    capture = capture_native(driver, obs["obs_id"], target,
                             timeout_seconds=5)
except NativeVisualError as exc:
    print("Capture unavailable:", exc)
else:
    print(capture.to_dict())  # Metadata only, no image bytes.
    # Optional explicit disclosure to the Codex image adapter:
    answer = query_visual(capture, "What is visibly displayed?", timeout=30)
    print(answer["answer"])
    # Optional local artifact; creates a new file and refuses overwrite:
    # capture.save("/private/path/window.png")
```

If the observation contains multiple AX windows, provide `window_index=` with the index of the intended `AXWindow` in that observation. The module does not silently choose the front window. A missing title, absent frame, mismatch, duplicate candidate, unsupported framework, stale observation, or expired deadline prevents a result.

Images remain in memory unless explicitly saved or passed to a model adapter. `save(path)` creates a new PNG with mode `0600`; `to_dict()` and object representations omit PNG bytes. A native capture exposes `.png`, `.capture_id`, `.sha256`, `.viewport`, and `.save()` for read-only image adapters. Its metadata declares `capture_kind="native_window"` and `coordinate_space="window_pixels"`; these pixels are not browser CSS coordinates.

`query_visual` currently supports only Codex. Its `read-only` CLI sandbox is not a guarantee that tools or network are disabled. Tool abstention is requested in the prompt, not enforced. Image content and model answers do not authorize actions or supply typed commit evidence. See [browser visual documentation](visual.md) for that adapter's boundaries.

## Window selection and consistency

The module selects an observed AX window and verifies the AX element's PID. It matches that window's PID, exact title, and frame to one on-screen `SCWindow`, then pins the matched window ID. Only a desktop-independent content filter for that window is submitted to ScreenshotManager. There is no whole-screen, display, or region-capture fallback.

This is **best-effort unique-window correlation**, not a proof that AX and ScreenCaptureKit describe the same object. It can refuse legitimate windows when the frameworks report different frames or titles. A trusted host may optionally implement `native_window_binding(obs_id, target)` and return `NativeWindowBinding(obs_id, target, pid, window_id)` to constrain matching to an already established window ID. Title/frame/PID checks still apply.

Before and after screenshot submission the module rechecks the observation, retained AX handle, PID, title/frame, and SCK window record. Changes or new ambiguity reject the result. These checks do not make rendering atomic, establish that pixels were unchanged throughout capture, or guarantee visibility of protected content.

Callbacks are awaited using a lock and short Foundation runloop iterations rather than blocking the main runloop on an event. The deadline bounds the wait; late callback images are discarded. A timeout cannot cancel a screenshot already submitted to ScreenCaptureKit. Blocking framework calls and AX queries may exceed the cooperative deadline before the next check runs.

## Validation status

On 2026-09-19 an isolated environment used PyObjC 12.2.2 on macOS 27.0 to verify actual framework imports and the ScreenshotManager, content-filter, stream-configuration, and bitmap-conversion method signatures. A generated in-memory CGImage converted to PNG successfully; this was **not a screenshot**. Native CGPoint/CGSize AXValue representations were also checked without inspecting a live application's windows.

That inspection found and fixed a compatibility issue: this PyObjC build exposes `NSOperatingSystemVersion` as a tuple. Both tuple and structured representations are now supported.

Screen capture preflight returned `False`, and the public capture API correctly returned `screen_capture_permission_missing`. No screenshot API, permission request, foreground UI, or native input was executed. The native contract suite passed **16 mock tests**, including ambiguity, stale identities, callback timeout/late delivery, and the window-only filter configuration.

Actual permitted-window capture, rendered image correctness, AX/SCK geometry agreement across applications, and signed-helper integration remain unverified. The framework and mock checks do not establish those capabilities.
