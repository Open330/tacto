# Tacto peer review — 2026-10-16

> 후속 수정: 같은 날 기본 빌드의 VRoid 전경 경로 분리와 서명 팀 이식성을 수정했습니다.
> 아래 내용은 수정 전 리뷰 기록입니다. [수정·검증 결과](tacto-native-release-fixes-2026-10-16.md)를 참고하세요.

## Decision and scope

An independent Claude peer reviewed the publication checkout at `a858f66`
and the separate dirty development checkout at `edc0379`. A second independent
agent checked the compatibility fixes, then rechecked the publication changes.
Neither reviewer edited files or drove native applications.

The publication corrections below passed re-review without a remaining material
finding in that bounded scope. This is **not** approval to merge the entire
unpublished runtime, certification on a fresh Mac, or a complete history audit.
Ordinary-user native distribution remains unavailable under the pinned signing
team requirement. The development VRoid route has a separate unresolved merge
blocker; it is absent from the publication checkout.

## Findings and disposition

| Finding | Disposition |
|---|---|
| Native installation instructions called nonexistent `launcher/tacto` | Fixed: run `skill/install.sh`, then the actual signed alias `~/.local/bin/tacto` |
| Recorder used authoring defaults silently ignored by the published schema-2 parser | Fixed: explicitly expand action contracts before parsing; preserve local read/transmit overrides. Seven example definitions parse without missing contracts. Published-runtime Selenium replay independently verified all four fields without submission |
| README command catalog mixed seven absent commands with published commands | Fixed: separate supported commands from development-only batch/session/repair/document-attachments/web-upload/vroid-focus/vroid-drag |
| Ten evidence hyperlinks led into an unpublished `reviews/` directory | Fixed: preserve their record names as explicitly private, unavailable references rather than public download links |
| Translation summary said `passed:true` with `flow_replay_ok:false` | Fixed: replay success is false; independently checked output is `result_verified:true`, with explicit scope and limits |
| Markdown summary could imply whole-document editing/export | Clarified: success covers the intended opening-section edit. Remaining sample content and lack of export are explicit. `exported:false` is not itself a failure because export was never requested |
| Native instructions implied any local signing certificate was sufficient | Fixed disclosure: launcher/helper require the pinned project team. No signing checks were relaxed; a supported general distribution/signing path is still required |
| Development offline evaluation had two failing fixtures | Fixed fixtures only: functional positive/negative `--learn` routing replaces source-string inspection; timeout stub now uses the real timeout/observation protocol. Runtime retry/refusal behavior is unchanged |

## Unresolved development merge blocker

The unpublished `helper/VroidDrag.swift` activates VRoid, positions the global
cursor and posts foreground HID events. Its separate CLI requires explicit
`--yes`, exact window identity and bounded input, and its diagnostic document
already describes the foreground behavior. It is not an automatic fallback in
the ordinary background route.

Nevertheless, the development source-safety suite currently fails the invariant
that the signed helper contains no global posting or pointer warp. This needs an
explicitly reviewed separation of foreground diagnostics from the normal runtime
contract before a release merge. Do not hide the failure by merely exempting the
file or weaken the general input guarantee. The development implementation and
its failing test were preserved; none of those runtime changes were published as
part of these corrections.

Likewise, batch/automatic repair and the newest skill references remain
unpublished development functionality. Installed Claude and Codex skills point to
the development checkout; a fresh main-branch installation receives the older,
internally consistent published skill. These are distinct available versions.

## Verification

| Check | Result and limits |
|---|---|
| Development repair/batch/session/feedback/skill/install/docs/runtime-contract unit tests | 173 passed |
| Publication installation checks | 18 selected, 17 passed and one opt-in wheel check initially skipped |
| Actual isolated wheel build/install/import/help | Opt-in check separately passed, no native UI or signing performed |
| Development offline evaluation after fixture corrections | 55/55 passed; 133 GUI/model scenarios skipped out of 188 selected |
| Publication offline evaluation after fixture corrections | 55/55 passed; 82 GUI/model scenarios skipped out of 137 selected |
| Documentation checks | Six passed in each checkout; command coverage accepts the actual Tacto name or supported legacy nlc alias |
| Publication native input safety checks | 18 passed, source checks and mocked dispatch; no live input |
| Development native input safety checks | 17 passed, one unresolved global-input invariant failure |
| Landing page | 83/83 passed; automated axe desktop/expanded/mobile checks reported no violations |
| Recorder compatibility | Seven schema-2 example definitions have explicit contracts; read/transmit overrides and input immutability independently checked |
| Recorded results | Shopping title/price/stock and all four form values remain independently verified; rejection of corrupted values checked by the fix reviewer |

Claude's bounded publication scan found no real personal path, private address
or searched key pattern in the tracked files; an artificial `/Users/x` test
fixture was distinguished from personal data. The second reviewer checked 27
media hashes/byte sizes and 123 applicable local landing references, and visually
inspected the cropped Finder and synthetic TextEdit screenshots. These checks
do not establish that every historical commit or every frame is free of all
possible sensitive information.

Private test logs are retained under the local review directory
`tacto-peer-review-20261016`; they are not linked as public downloads. Published
media and summaries remain available from the [landing assets](../sites/tacto/dist/assets/demos/).
