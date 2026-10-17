# Native Save panel directory observation

`SaveDirectoryReader` observes an expanded column-view Save sheet. It retains
native references and directory resource identities; it performs no app launch,
activation, input, save, or model call. It is internal observation support,
not an advertised `evidence.document_snapshot` capability or a commit witness.

The supported structure starts at a pinned document window, its direct
`save-panel` sheet, a unique `ColumnView` browser, and its shared scroll area.
Ordered direct scroll columns each contain one direct AXList. Every list except
the terminal content list must select exactly one direct AXGroup. Exactly one
direct TextField of that selected group supplies its own AXURL. The terminal
list must explicitly return an empty AXSelectedChildren array. Missing or
unsupported selection attributes are errors, not empty selection.

The adapter resolves local file-reference URLs while the panel is live. Each
resource must be a canonical existing directory; symlinks, unresolved references,
files, remote URLs, malformed URLs and nonadjacent folder chains are rejected.
Directory URLs may have one trailing slash. Resource identity includes device
and inode so replacement at the same path differs from the earlier observation.

Two captures compare document-to-browser ancestry, column order, list/selected
row/URL-field identity and directory resource identity. `revalidate` compares a
retained witness against another capture. Native AX identity is used rather than
labels or row numbers. A selected TextField's AXSelected flag is not selection
authority: the parent group's membership in AXList.AXSelectedChildren is. Remote
panel service PIDs may differ from the document app PID, so ancestry establishes
ownership. The TextEdit reader additionally validates its document token and
process launch identity before and after the observation.

Node/reference, depth, column and elapsed budgets reject incomplete observation.
The elapsed budget is checked between synchronous system calls; it cannot cancel
a stalled AX call. The reads are not atomic and cannot prove absence of an
intermediate change that returns to the original state. Unsupported collapsed,
root-only, icon/list or file-selected terminal layouts fail explicitly.

The implementation has eight focused tests covering supported selection, errors,
reparenting, column/element replacement, directory identity drift, duplicate URLs,
nonadjacent paths, mid-read change, budgets and real native URL resolution. The
document suite also tests stale process identity around the directory read.

In the owned background NSSavePanel experiment, the initial selected chain agreed
with the host's in-process destination. Cancelling and reopening a second panel
at another folder caused the old witness to be rejected and the new folder to be
read correctly. Save was never pressed and no document was written. This covers
panel replacement; the later navigation experiment below covers one unchanged
sheet. Neither establishes TextEdit Save
dispatch, source-format evidence, rich-text preservation or causal file receipt.

Changing `NSSavePanel.directoryURL` after presenting the owned panel did not
provide a valid navigation-change experiment: its getter changed but AX selected
rows and the host's `panel.URL` still named the original destination after six
seconds. Failed probes are retained as failed. Apple's documentation describes
[directoryURL](https://developer.apple.com/documentation/appkit/nssavepanel/directoryurl)
as the current shown directory and the
[directory-change delegate](https://developer.apple.com/documentation/appkit/nsopensavepaneldelegate/panel(_:didchangetodirectoryurl:))
as a selected-directory notification; neither is remotely available as a trusted
third-party TextEdit property. The getter discrepancy does not by itself prove
that AX missed an actual destination change.

Before enabling save evidence, independently observe source format, filename and
format choices, bind the final Save control to the observation, revalidate all
settings at dispatch and verify the complete TextEdit/signed-helper path.

`SavePanelReader` additionally pins the filename field, enabled Save button and
their ancestry, an explicitly observed supported format popup, and the original
document's keep-changes checkbox when present. A missing format is recorded as
unavailable or rejected with `require_format=True`; a filename extension does
not establish format. Setting, identity or directory drift rejects revalidation.
`TextEditDocumentReader.save_panel` surrounds these reads with document token and
process identity checks. This Python path remains internal read-only support;
it does not establish source format or enable `evidence.document_snapshot`.
The document reader's token-to-window lookup, directory/panel reads and witness
revalidation each share a three-second soft deadline with all their identity
checks and lock wait, shortened by any caller deadline. Late native results are
refused; this does not forcibly interrupt OS calls or bound the remaining scoped
observation tree traversal or Save dispatch.

The signed helper advertises a separate `evidence.save_panel` read capability.
`save_panel(obs_id, target, window_element_index, panel_token=None,
document_token=None, require_format=False)` requires the current native observation and the exact
window it names. It reads directory resources and settings in Swift, pins
process start identity and native AX objects, and binds the exact enabled Save
button to a unique index in that observation. A returned panel token revalidates
those settings and objects; changed settings or unknown tokens are rejected.
The response always states `authorizes_save: false`. With a TextEdit
`document_token`, the helper requires that connection's pinned document window,
process launch identity and exact body AX object. It validates a unique
`First Text View` in the source window before and after the panel reads,
excluding sheet contents from source-body traversal. The returned panel token
retains that document token; replacing or omitting it on revalidation is
rejected. Source-body replacement, ambiguity, malformed ancestry and token
expiry are errors. Source format, attachments, final dispatch validation and a
causal file receipt remain outside this API.

The document-binding handler and native traversal are exercised with injected
AX/process boundaries, including expiry and stop during a read. This is not a
live TextEdit document-and-panel success claim. The live signed probe below
uses an owned generic panel without a document token.

Reads share a six-second helper operation deadline, three-second reader phase
budgets, and a maximum one-second AX messaging timeout capped by remaining
operation time. Checks run between synchronous calls; hard cancellation of
every system call is not guaranteed. The helper keeps at most 32 panel tokens
per connection. Native comparisons are repeated reads, with the same possible
intermediate-change/ABA limitation as the Python reader.

An isolated `./launcher/tacto eval signed_save_panel` probe creates its own
background NSSavePanel and never presses Save. Its initial RTF/off settings,
changed Plain Text/on settings and filename agree with independent in-process
host values. Stale observations, foreign panel tokens and old settings after
a same-panel change are rejected. The exact Save button appears in the signed
observation map. Input samples remain unchanged; caret and concurrent typing
are not established. Initial wiring failures remain in the raw reports.

An additional owned NSSavePanel with explicit format and keep-changes controls
was checked against its host's actual settings. The reader observed RTF/off,
then rejected the old witness after the same sheet changed to Plain Text/on
and its filename changed. The fresh values agreed with the host; the directory
and sheet identity stayed the same. This verifies settings drift, not directory
navigation, a real TextEdit source format, or Save dispatch. Save was never
pressed. Native integral NSNumber checkbox values are normalized in the AX
adapter; floating-point and textual values are not guessed as checkbox states.

The owned column-view navigation experiment selected one observed child-folder
row through its exact parent AXList's settable AXSelectedChildren. The host's
directoryURL, proposed file URL and directory-change delegate independently
reported the new child folder. The same sheet remained attached; the old
directory and settings witnesses were rejected and a fresh witness read the new
destination. The independent input samples stayed unchanged. This was one
owned generic panel, without Save, document writes or a caret/typing guarantee.


The signed helper additionally advertises `action.click.save_panel_guard`.
An explicitly approved host can call `act(..., save_panel_token=token)` for an
exact indexed click. The token must name the currently mapped native Save
button and panel window. Immediately before AXPress, the helper repeats the
read-only witness validation with `require_format=True` under the remaining
click deadline, including the previously pinned document token when present.
A token is consumed before one AXPress attempt; an uncertain timeout cannot
reuse it. Failed validation performs no input. This guard is optional and does
not retroactively constrain ordinary clicks made without a token.

The token is evidence of settings, not approval. The Python signed transports
require a literal `approved=True` and an approved confirm/preapproved risk
class before forwarding it. The launcher restricts its type, length and exact
click shape; it does not issue host approvals. This parameter is not yet a
Flow action. A host `SavePanelCommitPreparation` now binds it to a specific
event, digest and observation for replay/general dispatch; TextEditReceipt
requires this attachment and unwraps its original file-receipt context afterward.
The attachment is neither approval nor completion evidence. AX reads and AXPress are
not atomic, so changes followed by restoration and races after the last read
remain possible. The guard does not attest source formatting, attachments,
unchanged text, saved-file content, writer identity or save completion.

The owned `guarded_panel_commit` scenario has no file writer. It observes one
NSSavePanel completion callback with response OK after the guarded AXPress;
that independently proves the fixture callback, not a TextEdit save. The raw
AX response can still be `unverified: cannot_complete` and is retained as such.
The separate read-only `signed_save_panel` scenario also checks that changed
settings refuse the old token at dispatch before any input. Neither enables
`evidence.document_snapshot`.

Live `TracingDriver` forwards panel reads and guarded clicks without recording
connection-owned tokens as replayable events. `TraceDriver` removes both the
panel-evidence and guard capabilities and refuses these calls without consuming
trace events. An ordinary click trace does not gain live save evidence.
