### Rules that decide whether it works

**Selectors must match exactly one element.** Zero is `target_missing`, two is
`target_ambiguous` — there is no silent "first match". Both failures are phrased
for the person at the keyboard and list what IS on screen, so give every
selector a name a human would recognize. Prefer `identifier`; use
`ancestor` to scope a repeated label. If an identifier changes with state
(a reset button that is `AllClear` when empty and `Clear` otherwise), give the
alternatives as a list. Identifiers may also carry live state after a `;`
(`StandardInputView;value:408`) — write only the stable part.

**Scope to the front window** with `"in_focused_window": true` whenever an app
can have several windows open. Two TextEdit documents expose two elements with
the identifier `First Text View`, so an unscoped selector becomes ambiguous the
moment a second document exists.

**Point at things that are only labelled by their value** with `"value"`, and
when the label and the clickable element differ — a list row identified by its
inner text — match the label and act on the row with
`"click_ancestor": ["Row", "Cell"]`.
