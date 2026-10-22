# Hover: move the real cursor into a window

Clicks and drags are sent to the window and leave the person's cursor where it
is. An app that reacts to where the **real** cursor is — a hover highlight, a
tooltip, a desktop character that runs away from the cursor — needs the cursor
itself. `tacto hover` moves it, holds it, and puts it back. It presses no
button and does not activate the app.

```bash
tacto capture com.example.pet --helper --output /tmp/before.png
tacto hover com.example.pet --helper --to 120 80 --hold 1 --capture /tmp/during.png
```

- Points are **window points from the observed window's top-left**, as for
  drag; choose them from a current capture (`pixel_to_point`). `--via X Y`
  (up to 12) passes through points first; `--duration` (0–2 s, default 0.3)
  is the travel time, 0 jumps; `--hold` (0–2 s, default 1) is the wait at `--to`.
  `--window 'Exact title'` is needed when the app has several windows.
- `--capture NEW.png` saves the window while the cursor is still there (it
  takes about a second of the budget, so duration + hold ≤ 2.9 s with it).
- The reply reports `window_frames` sampled during the hold (`window_moved`
  is true when the window itself moved away), `hit_owner_matches` per point
  (false when another window covered the point or the target ignores the
  mouse), `pointer_restored` and `original_pointer`.
- Judge the effect from `window_frames` or the before/during captures; moving
  the cursor proves nothing by itself.

If the person moves the mouse during the hover it stops at once with
`person_moved_pointer` and leaves the cursor where they put it; Esc stops it
too. `effect: unknown` means the cursor moved before it stopped: look before
hovering again. `hover_unavailable` means the installed helper predates this
command.
