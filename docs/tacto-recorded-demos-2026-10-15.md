# Actual app-window recordings — 2026-10-15

The landing page now presents real macOS application captures instead of its
former reconstructed TextEdit scene. These assets contain actual application
pixels captured through the signed Tacto helper. No desktop, personal browser
profile, unrelated window or audio is included. macOS's purple recording badge
is visible in the original captures.

## Recordings

| Clip | What ran | Result |
|---|---|---|
| [Apple Calculator](../sites/tacto/dist/assets/demos/calculator.mp4) | A saved six-step flow; input button parameters changed for the second calculation | 7 + 8 = 15; 3 + 9 = 12 |
| [Native workflow](../sites/tacto/dist/assets/demos/workflow-replay.mp4) | The same saved 11-step flow replayed with five input sets | 89, 34, 62, 79, 108; all five finished with one completion each |

Original screenshots:
[Calculator](../sites/tacto/dist/assets/demos/calculator-result.png) ·
[Native workflow](../sites/tacto/dist/assets/demos/workflow-result.png).

The workflow app is an owned AppKit test fixture, **not a commercial business
application**. Its source derives from `NativeComplexBenchmark.swift`; its
window title is changed to Tacto Workflow Demo. Inputs are disposable test data.
The host resets the fixture between jobs; Tacto performs the field inputs,
checkbox selection, Review, Compute, audit input, Audit, Finish and final read.
An independent fixture record checks the result, phase, one completion and the
ordered operations. All five recorded replays passed those checks.

## Capture and editing

- Timestamped PNG captures of one observed window, approximately 1–2 fps.
- PNGs are connected into H.264 MP4 using their original wall-clock intervals;
  encoded frames repeat captured pixels, with no synthesized intermediate UI.
- Calculator clip: two operation segments; waiting between jobs is removed.
  The execution segments retain their original speed.
- Workflow clip: continuous captured sequence, unchanged speed.
- No audio; local Korean WebVTT captions and native video controls.
- Videos use `preload="none"`, no autoplay, and no external player or CDN.
- These are executions of authored flows, not recordings of natural-language
  interpretation or autonomous flow authoring. Replay used `--think 0`; the
  flows contain no model decision steps. Authoring token cost was not measured.

## Scope limits

TextEdit did not produce new successful footage. The scoped observation
returned `document_scope_unavailable (pin_unavailable)` and a whole-app capture
refused multiple windows. No alternative window was guessed or captured. The
previous TextEdit reconstructed scene is removed; its older one-run validation
is retained separately in the historical benchmark notes.

These recordings establish the shown runs, not general compatibility, token
savings or performance superiority. The small capture rate may omit transient
visual states even though action outcomes are independently checked.

See [machine-readable conditions and results](../sites/tacto/dist/assets/demos/recording-summary.json)
for per-job values, durations, edit intervals and video SHA-256 hashes.

## Pointer-guided editions

The default players now use `calculator-guided.mp4` and `workflow-guided.mp4`.
A white arrow moves between action targets, blue rings indicate clicks, outlines
identify input fields and result checks, and labels identify the current step.
`GUIDE POINTER` remains visible in each frame. Posters include the same guide.

These marks are explanatory overlays, **not recorded system mouse movement**.
The underlying actions use accessibility APIs and need not move the mouse.
Target identifiers follow the executed action order; annotation times are
approximately reconstructed from captured frame transitions. They are not
precise dispatch timestamps. No additional UI execution is claimed.

Original recordings and screenshots remain unchanged and have separate links.
Playback duration and original app pixels are preserved beneath the overlays.
The overlay source is [annotate_recordings.py](../sites/tacto/annotate_recordings.py)
and its target/time schedule is [pointer-guide.json](../sites/tacto/dist/assets/demos/pointer-guide.json).
Only development-time `ffmpeg` and `rsvg-convert` are needed to regenerate them;
the shipped players add no JavaScript or external dependency.
