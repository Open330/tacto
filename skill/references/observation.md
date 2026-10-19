## Reading the tree

Each line is `<indent><index> <Role>  Key: value, Key2: value`:

```
4 Group  Identifier: CalculatorKeypadView
  10 Button  Description: All Clear, Identifier: AllClear
  21 Button  Description: 1, Identifier: One
  8 ScrollArea  Identifier: StandardInputView
    9 StaticText  Value: 408
```

Indices are **volatile** — they change on every observation. Never put one in a
recipe; select by meaning instead.

`tacto observe` prints the uniquely focused window and summarizes the others.
If focus is absent or more than one window is marked, it prints all windows.
When a file chooser, attachment dialog or expected control is missing, first
read `tacto observe <app> --helper --all-windows`; indices remain unchanged.
A condensed tree is not proof that another window does not exist.

For the backend's complete structured observation, use
`tacto observe <app> --helper --json`. This returns the full observed tree and
metadata such as `custom_selects`; it does not apply the focused-window text
summary. `--document <path>` keeps its document scope in JSON mode too.
An observation error is JSON with exit code 1; connection failures still need
the signed backend/`doctor --check` diagnostics above.

On an open ComboBox, options can include `visible` and `scroll_direction`.
When the desired option has `visible: false`, scroll a named row or text inside
that list in the reported direction, then observe again before selecting.
The ComboBox itself is not the scroll area. Missing visibility metadata is
unknown; do not invent a visible state. A hint never replaces the runtime's
fresh popup/row checks. Keep selectors based on names; JSON indices and
`obs_id` belong to that observation and must not be saved in a recipe.
Keep `open_select` and its subsequent scroll/selection in one `run` or `do`
invocation: the opened-list pin belongs to that helper connection. A separate
CLI invocation cannot resume that pin. For live option planning, use
`tacto do '<selection goal>' --helper`, which observes between actions on the same
channel. JSON is a diagnostic snapshot, not a session or input authorization.
