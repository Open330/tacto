**To show a folder in Finder, use `open_folder`** (with `--helper`):
`{"action": "open_folder", "params": {"path": "/abs/path"}}` — no selector.
Finder shows the folder without coming to the front (no keys, no sheet).
Only an existing plain folder; packages and apps are refused. Verify with a
postcondition on the window, e.g. `{"field": {"role": "Window", "title":
"<folder name>"}, "attribute": "Title", "equals": "<folder name>"}`, then
read `statusBarText` with `"in_focused_window": true` for the item count.
Prefer this over Go > Go to Folder…: that menu brings Finder to the front
(Tacto reports the app observed in front and stops sending after that step).

`Return` is allowed in one place only, with `--helper`: the Go to Folder
sheet's path field (`"identifier": "PathTextField"` inside the `GoToWindow`
sheet), to confirm a path you put there with `set_value`. The step must say
`"is_submit": true`, carry the same binding, and verify with an exact
`equals` (e.g. the Finder window's `Title` is the folder's name) — a
postcondition already true before the press is refused. Return anywhere else
is refused by the helper.

**Use `menu` for commands that live in the menu bar** — save, copy, open a
folder, new window. It takes a title path and **no selector at all** — a menu
step that carries one is refused. Write only the selector fields you know, and never a selector that
carries just a role — "click a Button" is not an instruction. Each level
may list alternatives, because menu titles are localized *and* state-dependent
(an unsaved document shows `Save…`, a saved one shows `Save`):

```jsonc
{ "action": "menu",
  "params": { "path": ["File", ["Save As…", "Save…", "다른 이름으로 저장…"]] },
  "postcondition": { "contains": "save-panel" } }
```

Menu items are not in the ordinary observation tree (an app's menu bar can be
large), so they are addressed by title. Neither backend raises the app to the
user's foreground. A disabled responder command may require explicit observed
window preparation and fresh menu-state verification; never infer its effect
from successful dispatch alone.
