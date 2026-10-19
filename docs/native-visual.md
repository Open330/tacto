# Native window image reads

`tacto.native_visual.capture_native` provides an optional, read-only ScreenCaptureKit path for one macOS application window. It uses `SCScreenshotManager` on macOS 14 or later. It does not use the obsolete CoreGraphics desktop-capture API, send input, activate a window, or request screen-recording permission.

The in-process path consumes the `tacto.driver.Driver` observation registry and is also exposed by `Driver.capture`. The signed helper/launcher provides its own window capture through `InheritedHelperDriver.capture`; it does not bypass the helper with Python framework calls. Native coordinates must not be passed to the browser coordinate executor.

Native `act(..., {"name": "click", "point": {"x": ..., "y": ...}})` resolves a window-relative point through the target application's AX hit test and presses the returned AX element. It does not dispatch a mouse event. A surface with neither an accessible press target nor a hit-test target requires the separate explicitly authorized `click_point` mouse route; ordinary `click` never falls back to it. Capturing an image does not itself authorize this action or certify completion.

## Installation and permissions

From the project directory:

```sh
uv sync --extra native-visual
```

The extra installs `pyobjc-framework-ScreenCaptureKit>=10.0` on macOS. The project's base macOS dependencies already include the Cocoa, Quartz, and ApplicationServices bindings. Importing `tacto.native_visual` itself remains portable: framework imports are deferred until a native operation is requested.

Both the in-process AX driver and screen capture need their respective existing Accessibility and Screen Recording permissions. `capture_native` calls `CGPreflightScreenCaptureAccess` only. If access is missing it raises `NativeVisualError("screen_capture_permission_missing")` before enumerating shareable windows or submitting a screenshot. It does not call `CGRequestScreenCaptureAccess` or present a permission dialog. Permissions granted to a different process, including a signed helper, do not establish that this Python process can capture.

## Read API

Create an ordinary in-process observation for an application that is already running and has a visible window. The AX driver's own observation behavior remains separate from capture; its `observe` method can launch a missing target, so choose the target deliberately.

```python
from tacto.driver import Driver
from tacto.native_visual import capture_native, NativeVisualError
from tacto.visual import query_visual

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

`query_visual` supports Codex and Claude CLI adapters. Codex's `read-only` CLI sandbox is not a guarantee that tools or network are disabled; tool abstention is requested in the prompt. The Claude structured adapter disables tools. Image content and model answers do not authorize actions or supply typed commit evidence. See [browser visual documentation](visual.md) for those adapters' boundaries.

## Signed helper API

Run this transport only as the signed launcher's child. First observe the intended running application, then call `driver.capture(target, obs_id=observation['obs_id'], window_index=None, path=None, timeout_seconds=5)`. With several real windows, select an explicit window index. The helper correlates the AX window's native window ID and target PID to a ScreenCaptureKit window, and captures only that window. Screen Recording permission belongs to the helper's bundle identity.

The helper keeps a private PNG and returns metadata and its path. An explicit `path` creates a new file with mode `0600`, refuses existing files and symlinks, and checks the copied bytes against the helper's SHA256. A failed copy does not return capture evidence. The helper's original artifact remains until explicitly removed.

`timeout_seconds` must be finite, positive and at most 10. It is validated by Python, the launcher and the helper. The helper shares this deadline across enumeration and screenshot callbacks; expired or late responses are rejected. This is a cooperative helper-side budget, not a hard limit on IPC, synchronous AX/framework calls or artifact copying, and it cannot cancel an already submitted screenshot.

## Window selection and consistency

The module selects an observed AX window and verifies the AX element's PID. It matches that window's PID, exact title, and frame to one on-screen `SCWindow`, then pins the matched window ID. Only a desktop-independent content filter for that window is submitted to ScreenshotManager. There is no whole-screen, display, or region-capture fallback.

This is **best-effort unique-window correlation**, not a proof that AX and ScreenCaptureKit describe the same object. It can refuse legitimate windows when the frameworks report different frames or titles. A trusted host may optionally implement `native_window_binding(obs_id, target)` and return `NativeWindowBinding(obs_id, target, pid, window_id)` to constrain matching to an already established window ID. Title/frame/PID checks still apply.

Before and after screenshot submission the module rechecks the observation, retained AX handle, PID, title/frame, and SCK window record. Changes or new ambiguity reject the result. These checks do not make rendering atomic, establish that pixels were unchanged throughout capture, or guarantee visibility of protected content.

Callbacks are awaited using a lock and short Foundation runloop iterations rather than blocking the main runloop on an event. The deadline bounds the wait; late callback images are discarded. A timeout cannot cancel a screenshot already submitted to ScreenCaptureKit. Blocking framework calls and AX queries may exceed the cooperative deadline before the next check runs.

## Validation status

On 2026-09-19 an isolated environment used PyObjC 12.2.2 on macOS 27.0 to verify actual framework imports and the ScreenshotManager, content-filter, stream-configuration, and bitmap-conversion method signatures. A generated in-memory CGImage converted to PNG successfully; this was **not a screenshot**. Native CGPoint/CGSize AXValue representations were also checked without inspecting a live application's windows.

That inspection found and fixed a compatibility issue: this PyObjC build exposes `NSOperatingSystemVersion` as a tuple. Both tuple and structured representations are now supported.

Screen capture preflight returned `False`, and the public capture API correctly returned `screen_capture_permission_missing`. No screenshot API, permission request, foreground UI, or native input was executed. The native contract suite passed **16 mock tests**, including ambiguity, stale identities, callback timeout/late delivery, and the window-only filter configuration.

On 2026-10-01 the rebuilt signed helper captured an owned background AppKit window, rejected a stale observation and produced a readable 1240×504 PNG. The rendered window was visually inspected. A second owned canvas experiment located the green target from captured pixels, resolved that point through the target's AX hit test and independently read back `green` and a counter change from 0 to 1. Both runs included independent process sampling of native foreground PID, pointer and focus identity; those sampled fields stayed unchanged. Caret selection was not required, and sampling cannot rule out changes between samples. No model was called in these two experiments.

These checks establish the signed fixture paths, not arbitrary opaque native surfaces, all-app geometry consistency, autonomous native image planning or concurrent user typing. The earlier in-process permission refusal remains separate from the helper's current grant. See [input observation limits](input-observation.md) and [supported native input routes](native-window-input.md).


## Native image proposals and point dispatch

`tacto.native_visual_actions.capture_window(driver, obs_id, target, window_index=...)`
reads either public capture transport into a `NativeVisualCapture`, checks PNG
header dimensions and SHA256, and removes its requested temporary copy. The
signed helper retains its original private artifact under the capture contract.
Captures now describe `window_frame` (global macOS points), image `width` and
`height` (pixels), and `image_mapping="full_window_no_shadow"`. SCK explicitly
scales the complete single window to its configured image size and ignores
window shadows. [Apple's scalesToFit contract](https://developer.apple.com/documentation/screencapturekit/scstreamconfiguration/scalestofit)
explains this output scaling. Old helpers without this mapping are refused.

`NativeVisualClickExecutor(driver, target).click(capture, proposal, authorize)`
accepts only a typed native capture and `{action: "click", x, y}` in image pixels.
The trusted `authorize(proposal, capture)` callback must return boolean `True`.
The backend must declare all three point capabilities before input; generic
acted replies without the complete expected-window native receipt stay unknown.
Each capture ID is single use for the worker process lifetime, including refused
attempts and copied captures after object collection. An atomic bounded registry
retains IDs only (128 characters maximum), not image bytes. At 16,384 IDs, new
attempts stop with capture_ledger_full; no old ID is evicted. The registry is not
durable across process restart and is not operation completion evidence.
Before input, it takes
a fresh picture under the same observation and compares PID, native window ID,
index, frame, image size and hash. It converts x by frame width/image width and y
by frame height/image height, then calls public `click_point` with explicit host
approval. A fresh capture is a consistency check, not an atomic link between
pixels and input. Any possibly delivered failure stays unknown and is not
retried or followed by cleanup input. Successful dispatch requires reobservation.

`run_native_visual(driver, target, goal, window_selector, planner, authorize,
verify, max_steps=8, timeout_seconds=60, cancelled=None)` repeats fresh window
observation and image planning. Prepare the intended background window explicitly
first. The selector resolves the intended Window in each tree; the first native
PID/window ID is pinned and a replacement is refused. Each iteration spends at
most one click. Only trusted host `verify(capture, history) is True` says DONE;
model `done` is UNVERIFIED. Budgets and cancellation are cooperative, with the
remaining deadline forwarded to capture and input; synchronous operations are
not guaranteed forcibly cancelled. This is not a typed document/save receipt.

`plan_visual` accepts mapped `NativeVisualCapture` objects and asks for image
pixels, while browser captures retain CSS pixels. Passing native pixels to the
browser executor remains invalid. A model planner is an explicit disclosure of
that one screenshot. The signed launcher now accepts a bounded explicit PNG
image request through its existing model relay. Its fixed-PATH child forwards
base64 bytes, a pinned codex/claude provider and an optional validated model name;
it cannot nominate a file path or executable. The outer provider process gets
no helper channel. PNG signature, canonical base64 and the 5 MiB limit are checked
by Python, Swift and the outer wrapper. Direct Python still needs its own
existing screen capture permission and provider access.

Both provider paths return a structured completed-turn envelope. The shared
Codex adapter checks JSONL completion, independently exported final text and
usage. Claude keeps tools disabled and preserves the observed model and usage.
`query_visual` collects one metadata record on success, provider error or relay
failure, excluding prompt, image bytes and response text. Missing actual model,
cost or remote request IDs remain unknown. An invalid visual answer remains a
visual failure even if the provider completed its turn. No fallback provider or
UI retry is selected. Local deadlines cannot prove remote cancellation.

Actual owned opaque-canvas image testing on 2026-10-01 verified the signed route
with a deterministic image color planner, then separately one actual Claude and
one actual Codex image proposal through the signed relay. Each model made one
request and one click; two receiving windows and independent native labels
confirmed the target result and zero events in the other window. This proves
these owned fixture runs, not representative application success rates,
product SDK parity or concurrent user typing preservation. The raw comparison and failed provider
attempts are in `reviews/2026-10-01/verification/evidence-summary.json`.


## General loop host scope

`tacto.general.run(..., visual_scope=NativeVisualScope(...))` now lets the host
enter the mapped native image route at a screen boundary. Construct the scope
in trusted application code with an explicit Window selector, image `planner`,
`authorize(proposal, capture)`, and `verify(capture, history)`. Declare known
reversible effects as `frozenset({"write_ephemeral"})` (optionally also `read`).
The host must know the approved region's actual effect from the application
integration, not infer authorization from image text. The request Envelope must
admit the effects and deterministic policy must allow them. Persistent writes,
sending, credentials and other consequential effects require the separate
commit-evidence path and cannot be declared in this navigation scope.

An optional `when(observation, candidates)` host callback returns literal True
to select visual reasoning; otherwise the ordinary AX planner continues. Each
visual screen has at most one point pair, shares the general deadline and
`max_screens` budget, and collects provider metadata into `GeneralResult`.
Only the expected observation/window's complete two-event receipt with the
native route, mechanism and reobservation flags may continue. Partial, malformed,
stale or uncertain dispatch stops without another input.

`verify` must independently inspect the receiving operation and the requested
goal, not merely accept model completion or a matching pixel color. If the goal
is not yet satisfied after an input, a trusted `verify_step(capture, history)`
callback must confirm its receiving effect before another model request or
before returning to AX. Without that evidence the run stops with unknown
certainty. Switching `when` to False cannot bypass this pending-effect gate.
After verified visual progress, the AX planner's action history includes the
visual operation. `verify_step` and `verify` attest ephemeral operation results;
they are not document-format, artifact-save or delivery receipts.

The first native PID/window ID is pinned across visual screens; a replacement
stops the run. Window preparation remains explicit. `GeneralResult.visual_used`
and `visual_history` identify this path. General runs involving pixels cannot
be auto-promoted into AX selector recipes: choosing image coordinates again
requires a separate deterministic image replay contract, which is not yet
implemented. CLI agent mode cannot invent a host scope from a prompt or screenshot;
this is an explicit Python integration API.

One signed deterministic image task passed general-mode host verification and
sampled input audit. A separate actual Claude general task reached the correct
receiving counters but its outer audit observed an early focus change, so its
whole trial remains FAIL. The actual Codex general trial produced valid image
coordinates but the native input preflight could not read user focus and refused
all input; the whole trial remains FAIL/INCONCLUSIVE. These are separate from the
previous standalone signed Claude/Codex successes. Raw failures and their exact
scope are preserved in the current evidence summary.


A manually authored Flow may reference a host scope with action `visual` and
parameters `scope`, `max_screens`, `timeout_seconds`. Python integrations pass
`visual_scopes` to `replay`; the registry is copied before execution. The JSON
never embeds callbacks, coordinates or authorization. Each step uses fresh
images and the existing observation-bound point executor, requires the host's
receiving proof, retains events and provider metadata across decision branches,
and stops on uncertain input. This opt-in path can consume model tokens.
CLI replay does not register scopes. See the [Python API](api.md) for bounds.


NativeVisualScope now accepts host `input_actions`, a nonempty frozenset drawn
from `click`, `key`, `text`; the default is click only. Key/text scopes require
all three window_input capabilities and use the same one-use image registry,
fresh image/Window identity comparison, literal host authorization and bounded
input deadline. Exact proposals are `{"action":"key","key":"Right"}` or
`{"action":"text","text":"A😀한글"}`. Native payload bounds and control
refusals apply. This does not select a first responder; the host prepares and
verifies the known receiving view separately. A pending pair requires
`verify_step` before another input or a return to AX. Model done is not proof.

`tacto.visual.plan_native_input` queries the existing bounded image provider relay
for key/text proposals. It requires a mapped NativeVisualCapture, a bounded
goal, and explicit allowed kinds. It rejects shortcuts, controls, coordinates,
indices, additional fields and overlong text. JSON contains no authority.
General and registered visual Flow steps retain receiving events, payloads,
visual history and provider metadata. Automatic AX promotion stays excluded.
Standalone `run_native_visual` remains the click-only convenience loop.
