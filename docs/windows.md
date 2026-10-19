# Windows support (experimental)

Tacto on Windows drives applications through **UI Automation** (UIA), the
Windows accessibility API. The flow engine, recipes, consent rules and the
`observe`/`act` protocol are the same as on macOS; only the driver differs.

## Status

Experimental. The driver logic is unit-tested on every platform against a fake
UIA tree. Real UIA behaviour is covered by an opt-in test against an owned
Notepad instance (`TACTO_WINDOWS_LIVE=1`, see the `windows` workflow); results
on a real Windows desktop have not yet been recorded in this repository.

## How it behaves

- **No synthetic input.** Every action uses the element's own UIA pattern:
  click → Invoke, Toggle, SelectionItem or ExpandCollapse; `set_value` → Value;
  `scroll` → Scroll on the nearest scrollable ancestor; `select_option` →
  expand the combo box, select the one exactly named item, collapse it. The
  pointer is not moved and no window is activated.
- **Refused before anything is sent** (`effect: not_started`):
  - an element with no suitable pattern (`needs_pointer`);
  - `press_key` and `type_text`, which would need keyboard input to the
    foreground window (`needs_foreground`);
  - disabled or read-only targets, stale observations, and an element whose
    UIA runtime id or process changed since it was observed.
- `select_option` that opened the list but could not select exactly one item
  reports `effect: unknown`, because opening the list was already input.
- Consent (`confirm`, `preapproved`, `handoff`) is gated exactly as on macOS.
- A step's expectations are checked against the live element before dispatch,
  as on macOS: role, label (optionally one attribute), enabled state, and no
  modal window in the target process (UIA `WindowIsModal`). An expectation the
  driver cannot check — selection digests, or a modal state it cannot read —
  refuses the step (`target_changed`, `not_started`) instead of passing it.
- Each element's properties are read in one cached UIA round trip.

## Targets

Name an app by its executable (`notepad` or `notepad.exe`) or by process id
(`pid:1234`). Two running copies of the same executable are ambiguous and are
refused; use `pid:N`. Recipes bind to the identity `win:<executable>`.

## Not available on Windows yet

- The signed helper and `--helper` (refused on Windows), indicator/Stop panel,
  menus (`menu`), document receipts, visual (screenshot) reads and window
  preparation. `tacto doctor`, onboarding and the setup app are macOS-only.
- Applications running elevated (as administrator) cannot be automated from a
  non-elevated process (Windows UIPI).
- Packaged (UWP) apps such as Calculator: their top window belongs to
  `ApplicationFrameHost.exe` while the content belongs to the app process, so
  the driver's same-process check refuses those elements. This fails closed.
- Recipes using `in_focused_window` selectors do not resolve yet: the tree
  does not mark the focused window.

## Install from source

```
uv sync
uv run tacto observe notepad
```

`comtypes` is installed only on Windows.
