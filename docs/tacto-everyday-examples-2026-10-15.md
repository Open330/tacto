# Everyday tasks in real applications — 2026-10-15

The landing gallery contains **10 everyday examples: eight in real applications
or websites and two in public test sites**. The previous purpose-built native
workflow fixture is kept separately in a collapsed verification section.
These are demonstrations of authored Flow execution, not natural-language
creation recordings, reliability rates, or speed/token comparisons.

| Application | Actual task and independent result | Scope |
|---|---|---|
| TextEdit | Enter workshop notes; separately replay built-in Save and compare exact document text with the `.txt` on disk | Real Mac app, synthetic owned document; edit replay stopped unverified, separate saved-state replay passed |
| Finder | Sort by Kind, then Name; verify Name ascending and five items | Real Mac app, owned sample folder; no moves/deletes |
| Google Translate | Enter `Hello. Thank you.`; independently see Korean output `안녕하세요. 감사합니다.` | Real website, signed out; synthetic sentence sent to Google, transmission replay is not delivery-certified |
| GitHub | Apply `is:issue state:open label:bug sort:comments-desc` to microsoft/vscode issues; verify query URL, field and 25 visible results | Real public repository, read only; results change over time |
| UnitConverters.net | Enter 3.5, select feet; obtain 11.482939633 and compare with `3.5 / 0.3048` within 0.00001 | Real public converter, no account |
| Markdown Live Preview | Replace the sample's opening portion with a weekend plan; inspect heading, subheading, two plan items and bold text | Real editor; remaining sample content stays. This is a partial edit, not a complete document replacement/export |
| Wikipedia | Search Seoul and reach History | Existing real public-site example |
| macOS Calculator | Replay arithmetic with different inputs, obtaining 15 and 12 | Existing real Mac-app example |
| Books to Scrape | Navigate a category and check fictional price/stock | Public test site, no purchase |
| Selenium web form | Fill four values and inspect them | Public test site, no submission |

## Browser execution and reproduction

All browser task actions use Tacto's Python `BrowserDriver` and Flow replay.
The recording host opens the initial URL in an isolated headless Chromium
context and independently reads results; it does not perform task inputs
through a separate Playwright path. Personal Chrome/Safari profiles are not
attached. Synthetic text only is used. The source sites can make their own
network requests; Google Translate receives the entered sentence.

The source [additional browser recorder](../sites/tacto/record_everyday_browser_demos.py)
uses the shared [recording utility](../sites/tacto/record_browser_demos.py).
In a development checkout with the current runtime, Playwright Chromium and
ffmpeg installed:

```sh
python3 sites/tacto/record_everyday_browser_demos.py --output /tmp/tacto-everyday-recording-new
```

The recorder authorizes only its predefined synthetic translation input when
that scenario is selected. The translation step declares its third-party
transmission and binds the action to the Google origin, control and payload.
If it returns `commit_unverified`, the host does not retry the input or relabel
the replay as successful: it independently checks the displayed result and
records `flow_replay_ok: false`. The UI output establishes the shown translation,
not a delivery receipt or a generally automatic, fully certified workflow.

The decorative pointer overlay follows freshly observed element bounds; click
rings follow actual pointerdown events. It is aria-hidden and cannot receive
input. The overlay uses DOM construction compatible with Trusted Types; no
source-site security policy is disabled. It is reinstalled after client-side
page rerenders. Deliberate pauses improve legibility. No model calls occur
inside authored replay; host validation and initial authoring costs are separate.

[Browser conditions, results and hashes](../sites/tacto/dist/assets/demos/browser-examples.json)
contain seven scenarios. Full accessibility trees, browser profiles, telemetry
logs and private local paths are not published.

## Native recording and saved-state limits

TextEdit and Finder are the installed macOS applications, not reconstructions
or custom test applications. A private development recording harness uses the
signed Tacto launcher's HelperDriver, Flow replay and the existing document
receipt adapter. Its temporary recording dispatcher was removed after filming;
no native input implementation or permission setting was changed.

TextEdit input is declared `write_persistent`, since existing documents can
autosave. The input reached the exact requested body, but replay correctly
stopped at `commit_unverified / missing_typed_host_commit_evidence`. After
inspection, a separate built-in `textedit_save` replay passed its saved-state
receipt and exact disk/document comparison. No typing was repeated. The
result does not attribute the write to a particular Save press or claim the
original edit flow completed automatically. This distinction is visible in
the case card, captions and public summary.

Finder's Kind and Name sort postconditions both passed; its `statusBarText`
reported five items. Source PNGs include the system's window recording badge.
The public video and screenshot crop away the sidebar and bottom path bar,
which otherwise show local account/device names. No pixel replacement is used.
They show a prepared folder containing synthetic files; sorting is demonstrated,
not renaming, moving, deleting, or organizing the user's existing files.

Native movies connect timestamped window PNGs in capture order and hold the
last frame for two additional seconds. Sparse capture can omit transitions.
The [renderer](../sites/tacto/render_native_examples.py) adds explanatory pointer
marks; these are not measured mouse coordinates. Unannotated movies and final
screenshots are also linked from each case.

[Native conditions, results and hashes](../sites/tacto/dist/assets/demos/native-everyday-examples.json).

## Cases excluded or narrowed after verification

- Preview's PDF window could be observed, but a whole-tree check for `Page 2`
  was already true in offscreen document content. It did not prove navigation.
  An exact window-title check and preparation attempt did not establish the
  transition; preparation refused `no blank native titlebar point`. Its blank
  background capture is not published as a successful PDF example.
- The converter updates when the output option is selected. Input is entered
  first, then the observed `foot [ft]` option is clicked. Its option label then
  includes the computed value, so independent verification checks the selected
  value as well as the numeric result. An unnamed listbox was not treated as a
  sufficient acting selector.
- Markdown's editable input represents a viewport portion of the sample.
  Observing a newly rendered heading alone would overclaim complete replacement.
  The published case explicitly describes editing the opening portion and
  retaining remaining sample content.

Original interfaces belong to their publishers. Sources:
[Google Translate](https://translate.google.com/),
[GitHub / microsoft/vscode](https://github.com/microsoft/vscode/issues),
[UnitConverters.net](https://www.unitconverters.net/length-converter.html),
[Markdown Live Preview](https://markdownlivepreview.com/).
No affiliation or endorsement is claimed. The landing page loads only its own
recorded assets, without embedding or contacting these services.
