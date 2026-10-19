# Windows support (experimental)

Tacto on Windows drives applications through **UI Automation** (UIA), the
Windows accessibility API. The flow engine, recipes, consent rules and the
`observe`/`act` protocol are the same as on macOS; only the driver differs.

## Status

Experimental. The driver logic is unit-tested on every platform against a fake
UIA tree. Real UIA behaviour is covered by opt-in live tests
(`TACTO_WINDOWS_LIVE=1`, see the `windows` workflow) that use only instances
they start: Notepad (set a value, read it back, front-window mark; open the
File menu, Save as, detect the dialog as modal, cancel it), Calculator
(a packaged app through its frame host), an owned message box (modal
detection) and the notice (never takes the foreground).

On Windows 11 (build 26300, 4K at 150 %, 2026-10-20) all of them passed,
observing Notepad took about 0.06 s, scrolling moved the editor in the
requested direction, and a screen capture showed the notice drawn sharply at
the top right. (Measure the screen from a DPI-aware process: an unaware one
reads scaled coordinates and misses the notice entirely.)

Live tests need the signed-in desktop session. Over SSH, commands run in
session 0, where no app windows exist; start them through a scheduled task
with an interactive logon instead.

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
- **Esc stops a run.** The key is polled (no hook, no permission); after it
  is pressed nothing more is dispatched and waits end with `aborted_by_user`.
  A small "Tacto 조작 중 — Esc로 중지" notice sits at the top right. It is a
  plain non-activating Win32 popup, so it never takes focus, even briefly,
  and it is drawn at the display's own scale;
  `--no-indicator` hides it. If the notice cannot be drawn
  the run continues, and Esc still works.
- A step's expectations are checked against the live element before dispatch,
  as on macOS: role, label (optionally one attribute), enabled state, and no
  modal window in the target process (UIA `WindowIsModal`). An expectation the
  driver cannot check — selection digests, or a modal state it cannot read —
  refuses the step (`target_changed`, `not_started`) instead of passing it.
- Each window's whole subtree, with every property the tree shows, is read
  in one cached UIA call (falling back to a live walk if the app refuses the
  cache), and the app's windows are found with `EnumWindows` rather than by
  asking every app's UIA provider, which a single slow app turned into
  seconds. Settings observes in about 0.03 s.
- A window made of thousands of native child windows is slow however it is
  read: a test form with 1,500 Win32 buttons took about 3 s, almost all of it
  the system proxy listing those windows. At most 400 children of one element
  and 5,000 elements in all are shown, and the tree says when it is cut.
- Windows 11 menus open as separate popup windows that UIA also shows inside
  their owner; each popup is shown once, inside its window.
- A UIA call that fails while observing returns `observation_unavailable`
  instead of raising.
- The app's front window — the highest of its windows in z-order, even while
  the person works in another app — is marked `[focused]`, so recipes using
  `in_focused_window` resolve as on macOS. When the front window cannot be
  read, nothing is marked and such a selector finds nothing.

## Targets

Name an app by its executable (`notepad` or `notepad.exe`) or by process id
(`pid:1234`). Packaged (UWP) apps are named by their own process
(`calculatorapp`, not `ApplicationFrameHost`): their frame window belongs to
the frame host, so it is observed, but its title bar stays in the frame host's
process and is refused; only the app's own content is acted on. Two running copies of the same executable are ambiguous and are
refused; use `pid:N`. Recipes bind to the identity `win:<executable>`.

## Permissions

UI Automation needs no permission grant, so Tacto asks for nothing and adds
no confirmation of its own beyond the per-step consent every platform uses.
There is also no separate permission holder: the Python process running Tacto
can automate any app at the same integrity level, exactly as the person who
started it can. Private files Tacto writes use the user profile's default
access rights.

## Not available on Windows yet

- The signed helper and `--helper` (refused on Windows), menus (`menu`),
  document receipts, visual (screenshot) reads and window preparation.
  `tacto doctor`, onboarding and the setup app are macOS-only.
- Applications running elevated (as administrator) cannot be automated from a
  non-elevated process (Windows UIPI).

## Install from source

```
uv sync
uv run tacto observe notepad
```

`comtypes` is installed only on Windows. `tacto session` reads its one flow
line through a background reader, since `select()` on Windows accepts only
sockets. The skill installer is a bash script; on Windows copy `skill/` to the
agent's skills folder (for Claude Code, `%USERPROFILE%\.claude\skills\tacto`).
