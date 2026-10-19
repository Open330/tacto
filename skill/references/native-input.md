**Background pointer clicks can be ignored.** An app-focused window need not
be an AppKit key window. In the owned signed fixture, controls that declined
first-mouse input received no content click even though the helper posted both
events. When planning a pointer action that needs a prepared window, use an
explicit `prepare_window` step, verify its declared postcondition, and observe
again before choosing the click. Keep the click's actual task postcondition;
`acted`/posted counts do not prove receipt. If a click was already possibly
sent, inspect its effect instead of preparing and blindly clicking again.
AXPress and pointer fallback are different routes; this measurement does not
establish the same failure for AXPress. See [window input](../docs/native-window-input.md).

**Is a menu command available right now?** `tacto menu-state <bundle-id> <Menu>
<Item> --helper` reads it without pressing anything. A background app often
reports items disabled that work in front (Save, New Folder) — that is the
app's current state. Keep an already-dispatched preparation single-use.
After an already-dispatched preparation, `--wait-enabled 1` polls only valid
disabled menu reads, bounded by time and 64 reads. Errors, cancellation and
late enabled replies cannot produce ready. An in-flight native read may
outlast the timeout. It sends no input or repeated
preparation; ready is availability only. Reobserve the bound document/control
and retain approval and task/receipt verification before acting.
See [read-only menu settling](../docs/menu-readiness.md).

**Markers on a tree line:** `[disabled]`; `[focused]` (the focused window);
`[selected]` — the chosen one of a set of buttons or rows (e.g. System
Settings > Appearance shows `Description: Light, … [selected]`). Read
"which option is set" from `[selected]`, not from the labels alone.

**Actions:** `click`, `set_value`, `type_text`, `scroll`, `secondary_action`,
`menu`, `read` (look and change nothing, for a step that only captures). Prefer clicking a named control over typing — a button press is
verifiable through the control itself. Python and the signed helper support
only window/element-bound background `press_key` navigation. Inspect the backend
capabilities and provide an input selector, ContextBinding and an explicit
attribute postcondition; `unverified_ok` cannot waive this contract. Tab,
shift+Tab and arrows are allowed; Escape and shortcuts are not.
