**English** · [한국어](README.ko.md)

<div align="center">

![Tacto — Agents use apps. Tacto repeats.](docs/assets/tacto-banner.svg)

### Let your agent use your apps. Save the steps. Run them again.

**Tacto lets agents operate browsers and Mac apps on your behalf.**<br>
Turn clicks, typing, and checks into a reusable **Flow**.<br>
Replay it with new inputs. Bring the agent back when judgment is needed.

[![macOS](docs/assets/badge-macos.svg)](docs/installation.md)
[![Claude + Codex](docs/assets/badge-agents.svg)](skill/SKILL.md)
[![Status](docs/assets/badge-status.svg)](#getting-started)

[Download preview](https://github.com/Open330/tacto/releases/tag/v0.1.0-preview.1) · [How it works](#how-it-works) · [Benchmarks](#benchmarks) · [Documentation](#documentation)

</div>

## See it work in real apps

[![Tacto searches Wikipedia and follows a page link in a real browser](docs/assets/tacto-wikipedia-demo.gif)](sites/tacto/dist/assets/demos/browser-wikipedia.mp4)

An animated preview from an actual recorded run. Click for the full MP4. GitHub's accessibility settings can disable animated-image autoplay.

**Ten recorded scenarios: eight real apps or websites, plus two public test environments.**

| App or site | Task and observed result | Recording |
| :--- | :--- | :--- |
| TextEdit | Write workshop notes; separately verify saved disk contents against the document | [10 sec](sites/tacto/dist/assets/demos/native-textedit.mp4) |
| Finder | Sort five files by kind, then by name | [8 sec](sites/tacto/dist/assets/demos/native-finder.mp4) |
| Google Translate | Enter a synthetic English example; separately check Korean output | [7 sec](sites/tacto/dist/assets/demos/browser-translate.mp4) |
| GitHub | Filter open bugs and sort by comment count | [9 sec](sites/tacto/dist/assets/demos/browser-github.mp4) |
| UnitConverters.net | Convert 3.5 m to 11.482939633 ft; compare with a separate calculation | [9 sec](sites/tacto/dist/assets/demos/browser-units.mp4) |
| Markdown Live Preview | Edit the opening section; check heading, list, and bold text | [18 sec](sites/tacto/dist/assets/demos/browser-markdown.mp4) |
| Wikipedia | Search Seoul and navigate to History | [14 sec](sites/tacto/dist/assets/demos/browser-wikipedia.mp4) |
| Mac Calculator | Replay the same Flow with different inputs; verify 15 and 12 | [14 sec](sites/tacto/dist/assets/demos/calculator-guided.mp4) |

<a href="sites/tacto/dist/assets/demos/native-textedit.mp4"><img src="sites/tacto/dist/assets/demos/native-textedit-poster.png" alt="Actual TextEdit document editing and save verification" width="48%"></a>
<a href="sites/tacto/dist/assets/demos/native-finder.mp4"><img src="sites/tacto/dist/assets/demos/native-finder-poster.png" alt="Sorting files in an actual Finder window" width="48%"></a>

Also available: [product lookup](sites/tacto/dist/assets/demos/browser-shopping.mp4) and [form filling](sites/tacto/dist/assets/demos/browser-form.mp4) in public test environments. No purchase or registration was submitted.

Browser demos use **Python BrowserDriver + Flow in a separate Chromium instance**, rather than your existing Chrome or Safari tabs. TextEdit stopped when save evidence was insufficient; a separate Save verification then passed. Google Translate's output check is separate from proof of input delivery. The Markdown demo edits only the opening section and leaves other sample content intact.

Recordings include illustrative pointers and capture delays. They show authored Flows executing, not natural-language authoring, and are not speed or token benchmarks. Not every demo completed without intervention. [Conditions, results, and limits](docs/tacto-everyday-examples-2026-10-15.md).

<a id="how-it-works"></a>

## Make it once. Use it again.

```text
First time    Claude / Codex → observe the app → author and save a Flow
Next time     saved Flow + new inputs → Tacto executes and checks results
Repeat        same Flow + different inputs → Tacto executes and checks results
```

This is **computer use**: agents read app interfaces, click controls, and enter information for you. Tacto stores the procedure so the model does not have to reason through every routine step again.

| Author once | Replay with new inputs | Intervene when needed |
| :--- | :--- | :--- |
| Create a Flow with Claude or Codex | Resolve targets from fresh observations and check outcomes | Use a model for judgment or supported repairs |
| Model-independent execution format | **Zero model calls during normal deterministic replay** | Supported repair resumes at the affected step |

Authoring, judgment, and repair still cost model tokens. Automatic selector repair currently belongs to the **development checkout's Codex provider**; it is not included in the main-branch preview.

<details>
<summary>What if the interface changes or completion is uncertain?</summary>

The development repair path passes only relevant candidates for supported static-selector failures. It resumes after verified checkpoints without blindly resending actions that may already have completed. It does not recover every UI change or crash. [Repair scope](docs/tacto-auto-repair-2026-10-14.md).

During native execution, the menu bar shows `● Tacto`; press Esc to stop. The persistent GUI settings item, **Tacto 설정**, reopens settings and does not indicate a running task.

</details>

## Reuse in a dedicated test app

[![Recorded execution of repeated workflows in the Tacto macOS test app](sites/tacto/dist/assets/demos/workflow-guided-poster.png)](sites/tacto/dist/assets/demos/workflow-guided.mp4)

The same 11-step Flow ran five times with different inputs in our own macOS test app, producing 89, 34, 62, 79, and 108, with one completion per job. Pointers and click effects were added for explanation; they are not recordings of system cursor movement.

[17-second walkthrough](sites/tacto/dist/assets/demos/workflow-guided.mp4) · [Unannotated recording](sites/tacto/dist/assets/demos/workflow-replay.mp4) · [Capture conditions](docs/tacto-recorded-demos-2026-10-15.md)

A separate development test completed ten jobs with zero replay-time model calls. Another repaired two changed buttons in one job using two model calls. These are dedicated test-app results, not a reliability guarantee for every business app. [Complex scenarios](docs/tacto-complex-scenarios-2026-10-14.md) · [TextEdit comparison](docs/benchmark-records.md#정확한-textedit-저장-상태의-실제-비교).

<a id="getting-started"></a>

## Get started

**Developer preview for Apple Silicon Macs.**

[Download Preview 1](https://github.com/Open330/tacto/releases/tag/v0.1.0-preview.1). Both **Tacto.app and the DMG passed Developer ID signing, Apple notarization, ticket validation, and Gatekeeper checks**. Download the DMG, drag `Tacto.app` into `Applications`, and open it to follow setup instructions. You do not need your own signing certificate for this packaged app.

This is an early preview, **not a stable release**. It was prepared on macOS 27.0.1. Fresh-Mac installation, other macOS versions, and real GUI/permission checks with the new distribution-signing team are not yet verified. The bundled onboarding UI and detailed guides are currently primarily Korean; this README is available in both languages.

Under the previous development signature, Finder and Safari completed 40 tasks. The first 20 TextEdit saves required the user to bring the app forward; all 20 retries then passed. Those initial failures remain in the record. These results are not unattended 60/60 success or approval of the newly signed DMG. [Release evidence and remaining checks](docs/release-readiness.md) · [Installation details](docs/installation.md).

### Build from source

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/), Xcode Command Line Tools, and an Apple-issued code-signing certificate. The launcher and helper must be signed by the same team.

```sh
git clone https://github.com/Open330/tacto.git
cd tacto
uv sync
./build.sh
./skill/install.sh
~/.local/bin/tacto doctor
```

Follow `doctor` and the app’s “permission holder” label to grant macOS permissions to **Tacto Helper** (Preview 1 and older builds: **NLC Helper**). Then ask Claude or Codex:

> Use the Tacto skill to calculate 12 × 34 in Calculator and verify the result.<br>
> Save it as a parameterized Flow so I can run it again with different inputs.

Replay a saved Flow:

```sh
~/.local/bin/tacto run flow.json --helper
```

[Agent skill](skill/SKILL.md) · [Flow authoring guide](docs/recipes.md)

<details>
<summary>Installation notes and development-only commands</summary>

If your agent cannot find the skill, restart its session and check `./skill/install.sh --check`. The Python distribution is `tacto-runtime`; internal imports remain `nlc`. The Python wheel alone does not supply the signed native launcher/helper. Legacy `nlc` commands and Flow paths remain supported; new builds display **Tacto Helper**, while Preview 1 and older installs may display **NLC Helper**. The signed identity and legacy internal paths remain compatible.

These commands belong to the development checkout, not the main-branch release:

```sh
~/.local/bin/tacto batch template.json --helper < jobs.jsonl
~/.local/bin/tacto batch template.json --helper --auto-repair codex < jobs.jsonl
```

</details>

<a id="benchmarks"></a>

## Less repeated reasoning

**A development-version comparison observed 53.5% fewer total input tokens with Tacto than Sky.** Three independent fresh-conversation pairs matched the requested model, reasoning effort, and initial state of the test app.

| Metric | Tacto | Sky |
| :--- | ---: | ---: |
| Successful tasks | 3/3 | 3/3 |
| Total input tokens | **201,447** | 433,312 |
| Mean time | **42.10 sec** | 46.17 sec |

This is a small sample. Tacto was slower in one case; it is not universally faster. [Full methodology and results](docs/tacto-matched-comparison-2026-10-14.md).

<details>
<summary>Cache, output, and repeated-Flow results</summary>

2026-10-14 · Codex CLI 0.161.0 · requested model gpt-6.1-sol · medium. Includes initial authoring, execution, and the final response; excludes test setup. Actual serving model, server cache behavior, and billing were not verified.

- Input excluding cache reads: Tacto 22,119 / Sky 50,592 (**56.3% reduction**).
- Output: Tacto 1,605 / Sky 1,604.
- Total time: **8.8% reduction**. This was not a ten-replay comparison.
- Separate 11-step test-app scenarios: **13/13 normal completions**, **5/5 expected stops**, two button repairs in one job.
- Ten normal repeats in that separate test used **zero model calls**.

[Complex scenario results](docs/tacto-complex-scenarios-2026-10-14.md)

</details>

## Explore the onboarding site

The [local landing page](sites/tacto/README.md) introduces replay, repair, and stopping. Its interactive walkthrough does not execute real apps or models.

```sh
python3 sites/tacto/serve.py
```

Open `http://127.0.0.1:8787`.

<a id="documentation"></a>

## Documentation

Detailed guides are primarily Korean, with code examples and some English references.

[Installation](docs/installation.md) · [Flows](docs/recipes.md) · [API](docs/api.md) · [Capabilities](docs/capabilities.md) · [Agent skill](skill/SKILL.md)

<details>
<summary>CLI reference and legacy nlc compatibility</summary>

Available on the main branch:

| Purpose | Commands |
| :--- | :--- |
| Diagnose setup | `tacto doctor` |
| Observe apps and controls | `tacto apps`, `tacto observe`, `tacto menu-state` |
| Execute requests and Flows | `tacto do`, `tacto run`, `tacto replay` |
| Compile and parameterize | `tacto compile`, `tacto parameterize` |
| Manage Flows and candidates | `tacto list`, `tacto show`, `tacto candidates`, `tacto promote` |
| Evaluate | `tacto eval` |

Development-only commands, absent from main:

| Purpose | Commands |
| :--- | :--- |
| Batch, session, repair | `tacto batch`, `tacto session`, `tacto repair` |
| Document attachments | `tacto document-attachments` |
| Browser file registration | `tacto web-upload` |
| Experimental VRoid diagnostics | `tacto vroid-focus`, `tacto vroid-drag` — separate build with `TACTO_ENABLE_EXPERIMENTAL_FOREGROUND=1`; excluded from the default build |

Check `tacto --help` for your checkout. Use the signed launcher for native work and `--helper` where required.

</details>

<details>
<summary>All guides and validation records</summary>

- [GUI, branding, and independent review](docs/tacto-gui-review-2026-10-17.md)
- [tacto visual identity](docs/tacto-visual-identity.md)
- [tacto landing engineering 2026 10 15](docs/tacto-landing-engineering-2026-10-15.md)
- [tacto recorded demos 2026 10 15](docs/tacto-recorded-demos-2026-10-15.md)
- [tacto everyday examples 2026 10 15](docs/tacto-everyday-examples-2026-10-15.md)
- [tacto browser examples 2026 10 15](docs/tacto-browser-examples-2026-10-15.md)
- [release readiness](docs/release-readiness.md)
- [tacto native release fixes 2026 10 16](docs/tacto-native-release-fixes-2026-10-16.md)
- [tacto peer review 2026 10 16](docs/tacto-peer-review-2026-10-16.md)
- [tacto branding](docs/tacto-branding.md)
- [api](docs/api.md)
- [artifact receipts](docs/artifact-receipts.md)
- [benchmark records](docs/benchmark-records.md)
- [capabilities](docs/capabilities.md)
- [claude adapter](docs/claude-adapter.md)
- [completion plan](docs/completion-plan.md)
- [context paging](docs/context-paging.md)
- [delivery evidence](docs/delivery-evidence.md)
- [evaluation](docs/evaluation.md)
- [input observation](docs/input-observation.md)
- [permission onboarding](docs/permission-onboarding.md)
- [installation](docs/installation.md)
- [menu discovery](docs/menu-discovery.md)
- [model deadlines](docs/model-deadlines.md)
- [native visual](docs/native-visual.md)
- [native window input](docs/native-window-input.md)
- [Tacto automatic repair — scope and limits](docs/tacto-auto-repair-2026-10-14.md)
- [Tacto complex scenario validation](docs/tacto-complex-scenarios-2026-10-14.md)
- [Tacto and Sky matched comparison](docs/tacto-matched-comparison-2026-10-14.md)
- [provider envelope](docs/provider-envelope.md)
- [recipes](docs/recipes.md)
- [rtf receipts](docs/rtf-receipts.md)
- [save directory observation](docs/save-directory-observation.md)
- [textedit save](docs/textedit-save.md)
- [visual](docs/visual.md)

</details>

---

**Tacto** · Local execution. Reusable computer use.<br>
Developer preview · Proprietary license
