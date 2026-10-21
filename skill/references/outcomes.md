**A step whose success is not self-evident must be verifiable.** `type_text`,
`press_key` and `menu` each need a `postcondition`/`expect`, or an explicit
`"unverified_ok": true`. This is measured, not cautious: `Format > Font > Bold`
with no selection returns success and changes nothing. A `menu` step with
neither is refused at authoring time rather than quietly waived.

**A postcondition that already held proves nothing.** If the value you assert
was true before the step ran, the run is recorded as `already_true` — which is
the sign that the step did not do what you thought. When the command is a
toggle, say so with `skip_if` instead.

**Name the surprises a step expects** with `"on_uncertain"`, when a step can
meet a screen no recipe can predict — a dialog left open by an earlier run, a
list whose rows are ambiguous. Given a budget (`tacto do --think`), such a step
hands the surprise back for one judgment instead of ending the run; without one
it fails with that error as usual. Values: `target_ambiguous`, `target_missing`,
`action_failed`, `postcondition_timeout`. Declare it only where judgment is
genuinely needed — a recipe that escalates on everything never fails honestly,
and each step gets one question per surprise, so a wrong answer cannot be
retried into the whole budget.

The first two are asked **before** anything ran, so the answer may be a
different target. The last two may be asked **after** the action went out:
`postcondition_timeout` means the app took the click and the screen has not said
so yet — which is not proof that nothing happened. There the engine looks again
rather than acting again, and re-runs the step only when the action means the
same thing done twice as done once (`read`, `set_value`). A `click` or `menu`
whose outcome is unclear stops and says so; nothing about it is retried, because
a Send button pressed twice sends twice.

**Toggles need `skip_if`.** Many commands toggle rather than set, so replaying
one undoes it. State the condition the step exists to bring about and it stands
down when that already holds:

```jsonc
{ "action": "menu", "params": { "path": ["Format", ["Font"], ["Bold"]] },
  "skip_if":       { "field": { "role": "CheckBox", "desc": "bold",
                                "in_focused_window": true },
                     "equals": "1", "attribute": "Value" },
  "postcondition": { "field": { "role": "CheckBox", "desc": "bold",
                                "in_focused_window": true },
                     "equals": "1", "attribute": "Value" } }
```

**Verify the outcome, not the click.** End with a `postcondition` reading the
field that holds the result. Add `"answers": true` when that value is what the
person asked for — a calculator's 408 is an answer, a bold checkbox's "1" is
only proof the step landed, and reporting the latter made "굵게 해줘" reply
"결과는 1 입니다". `equals` compares an attribute value exactly
(digit grouping aside, so `5,535` matches `5535`).

**Read values out with `captures`** when a later step, or the user, needs them.
A step may read several things at once, because a count and a list of names come
off the same screen:

```jsonc
{ "action": "menu", "params": { "path": ["Go", "Documents"] },
  "captures": [
    { "name": "목록", "many": true, "limit": 15, "optional": true, "tail": true,
      "field": { "role": "TextField", "in_focused_window": true },
      "attribute": "Value" },
    { "name": "요약", "field": { "identifier": "statusBarText" } } ] }
```

- `many` reads every match instead of requiring one. Acting still demands a
  unique element; reading asks for a **scope** instead (`ancestor` or
  `in_focused_window`), or "every TextField on screen" would pass as a
  description.
- `optional` — carry on without it. Finder names a folder's files for Documents
  but not for Downloads, and the count is still worth reporting.
- `empty_ok` — nothing matched IS the answer (a free day in the calendar). Use it
  only when a postcondition already proved the right place is on screen.
- `tail` — read the last matches. Finder's column view shows every parent folder
  beside the current one, so the first names on screen are not in the folder.

**Verify a disappearance** with `"absent"`: a dialog closing adds nothing to the
screen, so presence-only checks have nothing to assert.

**Big apps are read partially.** At most 60 children per container and 2500
visited nodes in total; the tree says where it stopped (`… (N개 더 있음)`).
The budget is shared among windows, then unused budget resumes unfinished
trees without rereading nodes. Window blocks retain their order; indices can
be non-monotonic within the output and remain valid only for that observation.
`… (창별 관측이 잘렸습니다)` marks an incomplete window, including one whose
controls were only partly read. A visible window title is not proof of complete
window content. A step whose selector names its target exactly (identifier, title, description
or value) is still found past the cap: when it is missing and the tree shows
`… (N개 더 있음)`, replay asks once more for exactly those names (`observe.reveal`)
and the matching rows appear under the same container, marked
`; 찾는 항목 K개 표시`. A role-only or substring selector cannot be looked up
this way, nor a value the app wraps in invisible marks (Calculator's digits):
the comparison is exact. If the miss says the capped list was not checked to
the end, the target may still exist; narrow the window or folder instead of
retrying. The scan reads at most 5000 nodes in 3 seconds; a Finder row is about ten
nodes, so a list view is searched to roughly its first few hundred rows past the
cap (measured 2026-10-22: row 2999 of 3000 was not reached).
