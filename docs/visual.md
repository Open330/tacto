# Browser visual reads and clicks

Tacto can capture a browser viewport, ask the Codex or Claude CLI about the image, and execute a single host-authorized coordinate click before observing again. This is an optional browser path. It does not provide native macOS screenshot control, arbitrary app automation, or a general computer-use completion guarantee.

Install the project's optional Playwright browser dependencies and Chromium before using this path. The image adapter requires an authenticated local `codex` CLI with `exec --image` support. The `claude` provider is also supported through the image stdin adapter described in [claude-adapter.md](claude-adapter.md). Unsupported providers raise an error instead of silently falling back to text.

## Explicit host policy and verification

`run_visual` takes three callbacks:

- `planner(capture, goal, history)` returns `{"action": "click", "x": 50, "y": 50}`, `{"action": "done"}`, or `{"action": "stop"}`. Coordinates are CSS viewport pixels, with the origin at the top left.
- `authorize(proposal, capture)` must return the literal boolean `True` for each click. The application supplies this policy; image content and model output cannot grant permission.
- `verify(capture, history)` must independently inspect the actual task result and return the literal boolean `True` before the loop reports `DONE`. A model's `done` proposal alone produces `UNVERIFIED`.

A local, disposable canvas example is available as `examples/probe_visual_task.py`. This equivalent API sketch uses its fixture:

```python
from urllib.parse import quote
from examples.probe_visual_task import PAGE
from nlc.browser import BrowserDriver
from nlc.visual import plan_visual
from nlc.visual_actions import run_visual

driver = BrowserDriver()
url = "data:text/html," + quote(PAGE)
try:
    driver.goto(url)

    def planner(capture, goal, history):
        return plan_visual(capture, goal, history,
                           provider="codex", timeout=30)

    def authorize(proposal, capture):
        # This host policy permits at most one click in our disposable fixture.
        return (driver._page.url == url
                and proposal.get("action") == "click"
                and len(driver._page.evaluate("window.fixtureClicks")) == 0)

    def verify(capture, history):
        clicks = driver._page.evaluate("window.fixtureClicks")
        return (driver._page.url == url and len(clicks) == 1
                and clicks[0]["correct"] is True)

    result = run_visual(
        driver, "page", "Click the blue rectangle exactly once.",
        planner, authorize, verify,
        max_steps=1, timeout_seconds=45,
    )
    print(result.to_dict())
finally:
    driver.close()
```

The fixture counter verifies a local canvas event only. It does not establish that a file was saved or a message delivered. Persistent/external operation verification requires the separate typed host commit-evidence mechanism. Do not replace application-specific authorization or result verification with a generic `lambda: True`.

## Capture and dispatch boundaries

`capture_browser(driver, obs_id, target="page")` binds a PNG to the current observation and document. It checks DOM, form values, viewport, and scroll state across capture. `VisualClickExecutor.click` asks the host for permission and then captures again; changed image bytes, DOM state, viewport, document, or observation prevent dispatch. Invalid, nonfinite, boolean, and out-of-bounds coordinates are refused.

Captures are single-use in process while the capture object remains live, including across executor instances. A denied attempt also spends the capture. This protects the intended loop from accidental reuse; capture IDs are not cryptographic authorization tokens, and the Python host itself remains trusted.

After a successful dispatch the loop observes and captures again. Events record `not_started`, `dispatched`, or `unknown`; a successful browser call alone is not completion. A dispatch exception records `unknown` and stops without retrying, since the click may already have happened.

`max_steps` bounds dispatched attempts. The loop checks its deadline and cancellation callback before and after cooperative boundaries, including planning and authorization. Blocking browser or host callbacks are not forcibly interrupted. The image adapter kills its local CLI process group on timeout, but cannot guarantee cancellation of remote model computation or detached descendants. An action already dispatched is not rolled back.

## Privacy and limitations

`VisualCapture.to_dict()` and loop history contain metadata, not screenshot bytes. `capture.save(path)` explicitly creates a new PNG with mode `0600` and refuses overwrite. `query_visual` and `plan_visual` explicitly disclose the screenshot to the configured provider. Codex receives a temporary image file, removed after the call; Claude receives base64 image bytes through stdin. Requested model aliases and observed model identifiers are separate fields; unavailable observed metadata stays unknown.

The Codex CLI is invoked with `--sandbox read-only`, `--ignore-user-config`, and `--ephemeral`. **Read-only is a filesystem sandbox, not a guarantee that tools or network access are unavailable.** The prompt requests tool abstention, but does not enforce it. The adapter is not an isolation boundary against hostile instructions embedded in an image.

Freshness is best effort, not atomic. Rendering can change between validation and input. DOM fingerprinting cannot establish that canvas/video pixels stayed fixed during an individual screenshot, although the later screenshot comparison catches visible differences between captures. Animations, video, blinking carets, or asynchronously changing pages may therefore cause conservative stale-capture refusals. No retry or tolerance heuristic currently relaxes that comparison.

Claude is invoked with CLI tools and MCP servers disabled, using a fresh nonpersistent session. This does not imply remote provider data is never retained. Its stream parser accepts observed thinking-progress counters while retaining session and terminal boundaries.

The current action vocabulary is a viewport click. Dragging, coordinate scrolling, visual typing, and native pixel input are not implemented here. An optional native window capture module is separate and still requires actual capture validation; typed mapped native captures can use the shared planner with image-pixel proposals and the separate [native executor](native-visual.md). Browser execution still uses CSS pixels only. Synthetic canvas success demonstrates this limited path, not equivalence to Codex computer use across real applications.

A real Claude canvas probe initially failed on an unrecognized progress event, then passed after the parser fix (one model call, one host-verified click). Both attempts are retained in `reviews/2026-09-19/visual-action-claude-probe.jsonl`; this is development evidence, not a frozen cross-provider benchmark.


The signed transport forwards explicit PNG bytes through the authenticated
launcher model relay, keeping provider CLIs outside the child's fixed PATH.
Image requests pin Codex or Claude, reject paths/executables and noncanonical
base64, and limit PNG bytes to 5 MiB. Both providers use structured adapters;
Codex now requires completed JSONL usage plus matching final output, while
Claude supplies observed model and token components. `query_visual` records
success/error metadata through `capture_provider_calls`, without prompts,
pixels or response text, and never silently falls back to another provider.
Outer model timeouts do not guarantee cancellation of detached or remote work.
