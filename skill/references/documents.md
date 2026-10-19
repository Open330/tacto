**To save a TextEdit document that already has a file, replay the built-in
`textedit_save`** — `tacto replay textedit_save -p document=/abs/path/name.txt
-p name=name.txt --helper --document /abs/path/name.txt --yes`. The absolute path is on the window's line in
`tacto observe`: `Window  Title: "name.txt", …, Document: "/abs/path/name.txt"`. The focused TextEdit window must be that
document (its title is checked right before the press), and the run ends
`done` only when the file on disk holds exactly the document's text (plain
`.txt` only; it certifies the saved state — macOS autosave may have written it
— not which writer did). Its event is `state_verified`, not a causal Save
confirmation. If the Save dispatch reply is lost or marked `unverified`, matching disk bytes cannot
resolve it: replay stops `commit_unverified`, preserves the state receipt, and
does not retry the Save. General execution requiring exactly one completed
Save does not accept this state-only evidence as completion.
A recipe of your own with File > Save ends
`commit_unverified`: nothing witnessed the file.
A `set_value` or `type_text` step declared `write_persistent` also requires a
host commit receipt. `missing_typed_host_commit_evidence` does not mean a file
is absent or a Save failed: the flow may contain no Save at all. A fresh exact
text read verifies the document body, not persistence or which writer saved it.
Do not lower the declared effect or repeat the edit to remove this uncertainty.
Report the observed body separately from the unverified persistent effect.

**To work in one exact document** when an app has several open (TextEdit),
read just that window: `tacto observe com.apple.TextEdit --helper --document
/abs/path/name.txt` (it fails rather than guess when no window, or more than
one, shows that file). This pins by path and reads the selected window through
a connection-local native token. Pinning reads only that document's complete
text within the backend's 1,000,000 UTF-16 unit limit; it is not a body-free
metadata operation. Tokens and indices cannot be carried to a
separate CLI invocation. In a recipe, select inside its window using the path as the ancestor:
`{"role": "TextArea", "identifier": "First Text View", "ancestor":
"/abs/path/name.txt"}` — titles can repeat across windows, paths cannot.
Document paths compare by NFC while retaining case and format characters.
Window fields are JSON-quoted, so literal commas, quotes and status markers
in filenames remain part of the value. Legacy unquoted Document fields are
refused as file identity. Use the decoded path, without JSON quotes, in selectors.
For window-title checks, use a `field` selector with `attribute: "Title"` and
`equals`; raw `contains`/`absent` patterns written for unquoted window labels
must be updated. Other control labels keep their existing format.
To retain this read scope throughout execution, use
`tacto run flow.json --helper --document /abs/path/name.txt` or
`tacto replay recipe --helper --document /abs/path/name.txt`.
Each observation, polling read and document-text witness retains the same
connection-local token. Failed or changed observations invalidate actions;
the runtime never repins another window or falls back to the whole app.
This mode supports read and existing native element actions. With
`action.menu.document_token_save` and `action.menu.focused_document`, the signed
helper additionally accepts the exact built-in Save path
`["File", ["Save", "저장"]]` bound to this same file. It rechecks the pinned
window and the focused/main file before pressing. `textedit_save` supplies its
existing .txt saved-state receipt; another recipe needs host receipt hooks.
Other app-wide menus, double click, and image operations remain unavailable.
The top menu name is currently `File`; a localized `파일` menu is not covered
by this fixed recipe. Neither a successful Save dispatch nor its saved-state
receipt proves which writer wrote the bytes (macOS autosave may do so).
Without `--document`, ordinary `run`/`replay` still observes the app's windows.
An ancestor selector alone restricts the element acted on, not the read scope.


For attachment checks in an already-open TextEdit document, use
`tacto document-attachments /canonical/document/path --helper`. It reads the
selected document through the signed channel and reports UTF16 replacement
positions and AX attachment/reference presence. It creates no document or
input. The command does not read attachment payload bytes or certify a save;
an AX element reference is not a payload receipt. Read the
[document API](../docs/api.md)
when the task needs to distinguish live metadata from persisted RTFD payloads.
