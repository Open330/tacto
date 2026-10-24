# 평가 실행과 결과 읽기

실행 전에 범위를 확인한다. `--preview`는 GUI 가용성 검사, 앱 실행,
시나리오 실행, 모델 호출을 하지 않는다. 따라서 `planned`는 실행 후보라는
뜻이며 현재 화면에서 실행 가능하다는 보장은 아니다.

```bash
.venv/bin/python examples/eval.py --offline --preview
.venv/bin/python examples/eval.py --offline --report /tmp/tacto-offline.json
./launcher/tacto eval background_menu_refusal --report /tmp/tacto-menu.json
```

마지막 명령은 소유한 배경 테스트 앱에서 메뉴 동작과 거절을 확인한다.
전체 GUI 평가는 사용자 앱에 대화상자를 열 수 있으므로 작업 중에 실행하지
않는다. 기본 실행은 `foreground` 요구 시나리오를 제외하며,
`--foreground`를 명시한 경우에만 앱을 앞으로 가져오는 경로가 허용된다.

위치 인자는 시나리오 이름의 부분 문자열이며 여러 개는 OR로 선택한다.
일치하지 않는 인자나 알 수 없는 옵션은 실행 전에 오류가 된다.
`isolated` 시나리오는 기본 전체 실행에서 제외하며, 하나의 이름을 지정해
별도 런처 프로세스로 실행해야 한다. 다른 시나리오와 함께 선택하면 거절한다.

소유 fixture supervisor와 연결하는 `native_coordinate_fixture`는
`--coordinate-control DIRECTORY`를 받는다. 디렉터리는 저장소 `reviews` 안에
이미 존재해야 하며 이 격리 시나리오 하나만 선택해야 한다. 창 준비는
`--prepare-window`, 일반 계획 루프는 추가로 `--general-prepare`, 실제 모델은
추가로 `--coordinate-model=codex|claude`를 지정한다. 모델 옵션은 실행 계획에
`model` 요구사항을 추가하므로 `--offline`에서 실행되지 않는다.
`--coordinate-abort`와 `--menu-unresponsive`는 준비 평가와 별도로 실행한다.
옵션만으로 supervisor나 임의 앱을 실행하지 않는다.

owned navigation/opaque 키·시각 입력 시나리오 하나에는 `--require-selection`을
추가할 수 있다. 전경 요소의 numeric caret/selection 초기 관측이 없으면 새 fixture
프로세스·입력·모델 호출 전에 중단한다. 자체 fixture 컴파일은 수행될 수 있다.
요구 옵션은 `options.require_selection`에 기록하며, 생략한 과거 기본 감사는
caret 검증으로 해석하지 않는다. 지원 시나리오와 실제 readiness 실패 범위는
[입력 관측 문서](input-observation.md)에 있다. 실제 사용자의 동시 typing 검증은
별도로 필요하다.

## 결과 형식

`--report PATH`는 UTF-8 JSON을 원자적으로 저장한다. 부모 디렉터리는 미리
존재해야 한다. 파일 권한은 소유자 읽기·쓰기(0600)이며 기존 파일은 교체한다.
오류 메시지에는 앱 정보나 경로가 들어갈 수 있다.

- `schema_version: 1`, `kind: plan | run`, 실행 옵션과 선택된 `cases`를 기록한다.
- 각 case는 고정 `id`, 요구 조건, 격리 여부, `status`, 이유 코드·설명,
  `duration_ms`를 가진다. 실행하지 않은 case의 시간은 `null`이다.
- 실행 중 예외는 `error`에 보존한다. 저장 플로우·helper 메뉴·transport
  검사에서 제공하는 단계별 진단은 `details`에도 기록한다. 아직 모든
  시나리오가 단계별 진단을 제공하는 것은 아니다.
- `summary.pass_rate`는 PASS / (PASS + FAIL)이다. SKIP은 분모에 넣지 않는다.
  판정한 case가 없으면 비율은 `null`이다. `scored_fraction`은 선택 범위 중
  판정한 비율이며, 건너뛴 개수와 함께 봐야 한다.
- 시나리오 시간에는 준비·검증 시간이 포함된다. `scenario_duration_ms`는
  실행된 시나리오 시간의 합이고, `mean_scored_duration_ms`는 PASS·FAIL의
  평균이다. 전체 시간에는 공통 준비도 포함된다. 모델 추론 시간과는 다르다.
- provider 호출 전체를 계측하지 않으므로 `model_usage`와 `model_cost_usd`는
  `null`이다. 재생의 무모델 회귀가 통과했다고 전체 평가 비용을 0으로 쓰지 않는다.

실행 전에 화면이 잠겨 있으면 GUI 시나리오는 SKIP이다. 실행한 시나리오가
실패하고 이후 잠금이 관측되면 FAIL을 유지하고 `environment_after_failure`를
추가한다. 그 관측만으로 실패 원인을 확정하지 않는다. 아직 시작하지 않은
GUI 시나리오만 건너뛴다.

종료 코드는 통과한 판정이 있고 실패가 없으면 **0**, 판정 실패가 있으면 **1**,
전부 SKIP 또는 선택·옵션 오류이면 **2**다. `--preview`도 같은 규칙을 따른다:
계획된 시나리오가 하나라도 있으면 **0**, 선택은 됐지만 전부 걸러져 planned가
0이면 **2**다. 계획이 비어 있는데 초록을 돌려주면 오타난 필터나 잘못된 플래그가
CI를 그대로 통과한다 — 그것이 이 규칙의 목적이다.
인자 오류는 실행 결과 파일을 만들지 않는다.

## Stagehand에서 참고한 점과 비교 범위

[Stagehand evals](https://www.stagehand.dev/evals)는 정확도·속도·비용을 함께
표시한다. [공식 평가 문서](https://github.com/browserbase/stagehand/tree/main/packages/evals)는
실행 전 `--preview`, 과제 선택, 반복 시행, 여러 harness와 모델의 비교를 제공한다.
Tacto에는 우선 실행 계획과 구조화된 결과를 적용했다.

Tacto의 현재 suite는 안전한 거절·계약·재생 회귀도 PASS로 센다. 따라서 이
통과율은 브라우저 작업 완료율이 아니며 Stagehand 순위와 직접 비교할 수 없다.
시나리오마다 공유 앱·flow store 상태가 남아 있어, 반복 횟수나 병렬 실행을
늘리는 기능은 추가하지 않았다. 비교 실험에는 과제별 독립 초기 상태,
동일한 성공 조건, 모델 사용량 계측이 먼저 필요하다.

## 실제 소유 커밋 이후 클라이언트 응답 유실

```sh
./launcher/tacto eval signed_commit_disconnect --report /tmp/tacto-commit-disconnect.json
```

이 시나리오는 GUI/native가 필요한 isolated 검사이고 모델을 호출하지 않는다.
소유 AppKit fixture에 승인한 본문을 signed helper로 한 번 전송한다. 호스트가
sent=1/정확한 본문을 읽은 뒤, 클라이언트가 커밋 응답을 읽기 전에 자기 socket과
stream을 닫는다. 실제 닫힌 파일 읽기에서 SDK가 채널을 poison하며, 독립된 소유
AX reader의 TranscriptReceipt가 sender 쪽 커밋을 확인한다. 확인 event/receipt를
보존하되 필수 후속 관측 실패로 replay.ok=false, 이후 요청 거절·추가 write0을
검사한다. 외부 메시징 서비스·수신자 inbox에 보내는 검사가 아니다.

PASS는 이 실패 처리 계약을 지켰다는 뜻이며 전체 Flow 작업 완료가 아니다.
응답이 이미 커널 버퍼에 있을 수 있으므로 helper의 응답 쓰기 실패나 네트워크
단절 시점을 입증하지 않는다. helper 종료를 별도 프로세스 identity로 관측하지
않았다. launcher 종료와 소유 fixture 회수는 따로 기록한다. input audit는
require_selection=false이며 동시 typing/caret나 표본 사이 무변화를 증명하지 않는다.

2026-10-11 첫 실행은3.990초 FAIL이었다. user_focus_unavailable/not_started/
attempted0으로 준비 단계에서 중단해 주입에는 도달하지 못했다. input audit
INCONCLUSIVE/source subset stable/owned child reaped였으며
`signed-commit-disconnect-1791652534869121000.json`과 `native-commit-disconnect-eval.json`
에 보존했다. 다음 별도 소스 실행은10.290초 PASS/launcher exit0이었다.
`signed-commit-disconnect-1791652947700460000.json` 및
`native-commit-disconnect-eval-repeat.json`에서 commit_writes1/reads_after_close1/
following_refusals2/following_writes0/receipt1/confirmed event1/replay.ok=false를 확인했다.
audit는 UNCHANGED_SAMPLED928표본/최대간격0.134197초였다. source subset 전후 동일,
전체 의존성 미고정, 소유 자식 회수, 보고서0600이다. 두 시행은 다른 소스이며
공정 비교나 성공률 추정으로 합치지 않는다. 이후 wait 오류 분류 보강은 별도
회귀로 검증하며 이 실제 시행의 소스에 포함됐다고 주장하지 않는다.

## 에이전트 수용 검사 — 스킬만 가진 에이전트로

단위 테스트와 평가 스위트는 이 저장소 안에서 Tacto를 돌립니다. 그래서 에이전트가 실제로 겪은
문제 — `tacto` 명령이 설치되지 않음, 샌드박스를 권한 문제로 오진, 잠긴 화면에서 원인을 추측 —
는 어느 쪽에서도 드러나지 않았습니다. `examples/agent_acceptance.py`는 기억도 저장소도 없는
**새 에이전트 세션**을 `/tmp`에서 띄워 과제를 주고, 에이전트의 **최종 답**으로 판정합니다.

```bash
python examples/agent_acceptance.py                  # Codex (기본)
python examples/agent_acceptance.py --agent claude   # Claude Code
python examples/agent_acceptance.py --list           # 지금 상태에서 무엇이 돌고, 왜 안 도는지
```

| 사례 | 확인하는 것 |
|---|---|
| `finds_tacto` | 현재 `tacto` 스킬·명령을 찾는다 |
| `names_the_sandbox` (Codex) | 샌드박스를 샌드박스라고 말하고, 권한을 켜라고 하지 않는다 |
| `names_the_lock` (잠긴 화면에서만) | 잠긴 화면을 잠긴 화면이라고 말한다 |
| `does_a_task` (풀린 화면에서만) | 스스로 흐름을 써서 계산기로 12×12=144 를 확인한다 |

- 판정은 에이전트의 최종 답만 봅니다. 처음 버전은 전체 기록을 봐서, Codex 머리말의
  `sandbox: workspace-write` 때문에 에이전트가 시작도 안 한 사례가 통과했습니다.
- 공급자가 에이전트를 돌리지 못하면(사용량 한도, 로그인) **NOT RUN** 으로 보고합니다 — 통과도 실패도 아닙니다.
  판정한 사례가 없으면 종료 코드 2 입니다.
- 모델 토큰을 씁니다(사례당 약 1–2만).


`flow_visual_fixed`, `flow_visual_codex`, `flow_visual_claude` are isolated
signed-launcher scenarios on the owned opaque canvas. They round-trip a Flow
containing a named host scope, require a confirmed receiving event and A1/B0
counters, retain metadata, and reject changed or inconclusive sampled input
state. Run each separately (`./launcher/tacto eval flow_visual_fixed`). Fixed uses
local color detection; the other two use the bounded image-provider relay.
These Flow trials are a separate cohort from standalone/general image tests
and do not establish actual-app or public-SDK parity.


`./launcher/tacto eval signed_opaque_input` and `python_opaque_input` each run one
owned background canvas with no AX editable control. A host-authorized click
selects its first responder; native window input then sends Right and three
Unicode payloads (Korean/emoji, combining/ZWJ, and 64 UTF-16 units). Independent
A/B counters and exact base64 text readback verify every pair. Missing approval
and wrong focused window must refuse before input. Trials retain sampled user
input observation and source hashes; they do not prove actual-user caret,
concurrent typing, IME behavior, model autonomy or public-SDK parity.

### User-operated typing trial through the signed evaluator

`tacto eval signed_concurrent_typing` defaults to SKIP with
`human_participation_required`; it never generates foreground typing. The bare
`examples/probes/concurrent-typing.py run` script cannot supply the signed channel
and refuses an unsigned driver. Prepare the owned navigation fixture with its
`prepare` mode and the witness with `examples/probes/build-typing-witness.py`,
using fresh private directories under this checkout's `reviews`. Preparation
builds the apps without launching them.

Only after the person agrees to participate and manually opens/focuses the fresh
witness, use the installed signed launcher in one isolated process:

```sh
tacto eval signed_concurrent_typing --typing-directory /absolute/checkout/reviews/background-trial \
  --typing-witness-report /absolute/checkout/reviews/witness-trial/build-report.json \
  --participant-confirmed
```

The flag records the caller's confirmation; it is not independent proof of human
consent or hardware origin. The evaluator validates prepared paths/source/binary
hashes, reserves a private report before native work, and sends background input
only through `InheritedHelperDriver`. Metadata-only AX reads inspect owned
counter labels and sampled input state. It never restores focus, activates the
user's apps or retries uncertain input. After `ARMED`, the person slowly types
the stated test text. Passing would establish sampled overlap and owned receiving
counters/selection/hash, not continuous preservation, IME coverage or hardware
input attestation. Same-user filesystem checks do not authenticate a hostile host.


`visual_input_fixed`, `visual_input_codex`, `visual_input_claude` are isolated
signed scenarios for host-registered Flow image key/text input on the owned
opaque canvas. They require Right once then exact Korean/emoji text, independent
receiving counts and content, two confirmed events, an untouched second window
and sampled input-state preservation. Host preparation selects the receiving
view first; these are separate from autonomous setup, actual-app/IME or SDK
parity. Each model case uses the bounded image relay and retains call metadata.

### Product comparison provenance

New `examples/product_comparison.py` results record version-2 manifests before
setup and after oracle/cleanup. The explicit set includes Tacto Python sources,
helper Swift sources, both signed helper executable paths, launcher source and
binary, skill, evaluation/probe sources, fixtures, flows and dependency lock.
Missing fixed artifacts are recorded as null. This set is not a dependency
closure or a record of the Sky plugin implementation.

`source` is the initial short identifier; `source_end` and the complete manifests
make changes visible. A changing manifest produces `status="invalid"` and
`invalid_reason="source_changed"`, preserving the original status separately.
A nonzero agent process exit cannot pass even if an app oracle sees the desired
state; `process_exit` and `oracle_passed` retain both observations. Cleanup
failure is visible in `cleanup_status` and a bounded exception-class code,
without recording exception text. Provider and
model refusals, environment failures and skipped runs remain distinct from
successful tasks. Previous result files are unchanged and their older hashes
must not be interpreted as version-2 manifests.

### Signed helper lifecycle after client reply loss (2026-10-11)

`tacto eval signed_commit_disconnect --report PATH` now checks optional helper
identity in the signed handshake. The launcher supplies audit PID/version on
its authenticated helper connection; helper PID/start must agree. The probe
independently checks exact bundle/executable and the same libproc start before
closing its own channel. It never terminates the reported helper PID. Later
exited/zombie/replaced means the original execution ended; read denial means
unobservable. Bounded `ps` fallback observes PID/state only when macOS refuses
zombie BSDINFO; it does not verify the zombie's original start time.

`reviews/2026-10-11/native-commit-helper-lifecycle-eval.json` and
`signed-commit-disconnect-1791655972998642000.json` preserve a 12.735s PASS:
same_instance before, exited after channel close, one owned send and confirmed
sender receipt, two subsequent refusals and zero further writes. Flow itself
remains failed because required observation is unavailable. Owned fixture
cleanup and explicit source subset were verified. Input state was unchanged
at 601 samples (largest gap 0.214371s), without numeric selection requirements
or a proof of no intermediate change. Files are exclusive/private; dependencies
are not fully frozen. This is not helper EPIPE, remote cancellation/billing,
external delivery or a representative app/model comparison. Regression summary
also retains the initial zombie-read failure, corrected 24 passing tests and
23 signing/handshake tests (one skipped), as tool-output summaries, not raw logs.

### Recovery child execution records (2026-10-11)

`tests/test_recovery_event_preservation.py` executes the registered
`dismiss_dialog` recipe with a scripted driver. Before the fix, three tests
produced four failures: the child's dispatched Cancel was missing and the
parent resent after the child's foreground activation. The private
`recovery-events-before.log` retains that result. The fix preserves child event
IDs and recovery/parent-step provenance, bounded nested logs with recovery-step
indices, redaction, receipts and diagnostics. A failed or unknown child effect
does not license another parent dispatch. Both recovery paths stop further
input when the child reports foreground activation. Normal recovery still
allows its existing single retry.

These driver tests and a separately injected log/redaction/budget boundary
test do not demonstrate actual macOS modal recovery or concurrent typing.
The related integration suite uses isolated Chromium, including a twelve-screen
wizard with independent readback. Native/client-disconnect evidence from the
earlier source cohort remains separately scoped.

The final related suite passed 61 tests in 18.001s with no skips or errors:
`reviews/2026-10-11/recovery-events-final.log/.json`. The JSON records before/after
hashes of `tacto/flow.py` and the eight named test modules, stable during this run;
it does not freeze all dependencies. Both artifacts were created with
O_EXCL/0600. The intermediate 60-test pass remains in recovery-events-after.log
and is not substituted for the final source run. No model or paid provider
request was made in this suite.

### Actual signed native twelve-stage modal recovery (2026-10-11)

Run explicitly with `tacto eval signed_native_recovery --report PATH`. It is
isolated/gui/native and skipped offline or in ordinary evaluator defaults.
The owned AppKit fixture exposes a custom AXValue receiving view. Stage five
opens an actual sheet; stage six ignores its first input while the sheet is
present. A scripted escalation (budget one, model calls zero) selects registered
`dismiss_dialog`. The signed helper presses the actual sheet Cancel, then
replays the idempotent value once. Independent reads are confined to the owned
PID. Before both stage-six attempts, the observer verifies main-window sheet
identity and its Cancel identifier; it does not issue an extra helper observation
that would invalidate the parent's actionable ID.

The first native run (13.003s) failed at the observation witness: AXSheets was
empty while the actual sheet appeared in AXChildren. Stage six and recovery
were not reached. The diagnostic run (10.520s) exposed the sheet but an extra
diagnostic observation invalidated the action ID; stale_observation/not_started
was preserved. Both owned children were reaped, sources stable, audits
INCONCLUSIVE. Raw files end in 1791656488726064000 and 1791656553197185000.

The product walker also omitted unlabelled Sheet rows. Python and actual Swift
walker regression reproduced two failures before the fix. The walkers now
preserve Sheet boundaries. `Postcondition.absent_role` checks the exact observed
role token, and close_dialog uses it instead of a substring that confuses
Sheet1/Spreadsheet labels with sheets. IR load/bind and subsequent model
authoring retain the optional field; legacy substring absent remains supported.

`signed-native-recovery-1791656889081644000.json` (40.958s) has Flow ok and the
required receiving counts, but its audit is CHANGED with 618 samples, largest
gap 2.987384s and eight missing samples. The overall native eval remains FAIL.
No causality is attributed to Tacto from those changes. The final role-condition
cohort `signed-native-recovery-1791657130907825000.json` and
`native-twelve-stage-recovery-eval-role-fixed.json` passed in 27.080s/exit zero:
accepted12, blockedWrites1, Cancel1, stageSixRequests2, Other0 and fourteen
unique events including the recovery event. Audit UNCHANGED_SAMPLED has 1880
samples, largest gap 0.221371s, without numeric selection requirements or a
proof of no intermediate change. Sources were stable and the owned child reaped.
Artifacts are O_EXCL/0600 with explicit runtime source/binary subsets, not a
dependency closure. These are separate source cohorts, not a success rate or
fair Tacto–Sky comparison. The later authoring-path fix is separately unit tested.

After the authoring-path update, the full suite passed 1582 tests in 355.453s
(two skipped, 1580 executed, exit zero). `native-recovery-full-regression.log`
and `.json` preserve exclusive/private raw output and stable before/after
hashes of the explicit Tacto/test/helper/binary/fixture/dependency-declaration
subset. This is not a dependency closure. Failed development checks, including
incorrect test selectors and a corrected authoring local-name error, are
retained separately as captured tool-output summaries in
`native-recovery-development-summary.json`; they are not promoted to passes.

### Native no_modal refusal and subsequent allowance (2026-10-11)

The signed native recovery probe also submits a no_modal request before the
first stage-six input. In the baseline, the real helper returned acted despite
the sheet, and independent receiving counters recorded a blocked write. Raw
`signed-native-recovery-1791658051443839000.json` and
`native-modal-guard-before-eval.json` preserve the 13.526s failure, stable source,
owned-child cleanup and INCONCLUSIVE audit.

The guard now checks AppKit window/direct-child modality and the app's focused
element ancestor chain, with bounded reads and timeout restoration. A proposed
whole-content traversal was replaced because it would refuse large apps after
the budget expired. A 6000-content-node regression verifies no content-child
scan and fewer than twelve metadata reads. Optional AXModal/AXSheets/focus
NoValue means absent; required attribute errors remain unobservable. Unfocused
arbitrary deep custom subtrees and atomicity through dispatch are not proved.

The corrected source cohort `signed-native-recovery-1791658647267151000.json`
and `native-modal-guard-after-eval.json` passed in 25.766s/exit zero: the sheet-time
request was target_changed/not_started with identical receiving counters; after
Cancel, the same no_modal condition allowed the actual single stage-six retry.
The twelve-stage oracle passed with fourteen Flow events. Audit is
UNCHANGED_SAMPLED at 2352 samples (largest gap 0.129188s), selection not required.
Sources were stable and the owned child reaped. Reports are private/exclusive
with explicit source/binary subsets, not a dependency closure or a fair
Tacto–Sky comparison. Failed baseline evidence remains separate.

The final related suite passed 93 tests in 20.063s (one skipped):
`modal-guard-final-regression.log/.json` retain O_EXCL/0600 output and stable
before/after hashes of the named source/test/binary subset. It includes actual
Swift checker extraction, direct adapter timeout/reset failures, modality
shape/budget/focus cases, general browser/menu and replay contracts. These are
unit/isolated integration checks; the earlier 1582-test full-suite result is
from the preceding source, not this update.

## 큰 native ComboBox의 실제 모델 과제

설치된 signed launcher로 한 번에 한 isolated 시나리오를 실행한다.

```sh
tacto eval native_select_combo_many_model_fixture --model-provider codex \
  --report /absolute/private/new-report.json
```

이 과제는 별도 소유 AppKit fixture에131개 항목과8행 viewport를 만들고,
Choice127를 한 번 선택해 값·index130·count1·closed를 독립 확인한다.
같은 popup의 진행하는 scroll와 single popup-bound click receipt도 필요하다.
모델이 visible row에서 진전 없는 scroll를 추가하면 결과 값이 맞아도 FAIL이다.
후보/참조 조회에는 host-selected o200k_base2500 문자열 예산을 적용하며
serving tokenizer나 provider 전체 context 예산의 증거로 사용하지 않는다.
최대16 screens/180초이며 소유 fixture는240초 후 자체 종료한다.
claude도 --model-provider로 선택할 수 있고 접근 제한은0 scored SKIP으로
분리한다. 사용자 앱을 활성화하거나 종료하지 않으며 실제 새 Mac 설치,
동시 사용자 입력 보존 또는 제품 간 동일 조건 비교를 검증하는 과제는 아니다.

## scroll 수신 실패 뒤 중단 검증

```sh
tacto eval native_select_scroll_receiving_failure_fixture \
  --report /absolute/private/new-report.json
```

이 isolated 과제는 별도 소유 ComboBox에서 host script로 목록을 열고,
아래에 잘린 Beta에 대해 잘못된 up/pages1을 한 번 보낸다. 실제 signed
helper 입력을 사용하며 모델은 호출하지 않는다. 행이 목표에서 더 멀어지면
host receiving check가 실패하고 원래 backend receipt를 보존한 채 unknown으로
중단해야 한다. Alpha/count0/index0,추가 선택/입력/계획0을 확인한다.
report의 expectation_passed는 예상한 실패와 중단을 확인한 결과이며 내부
probe.passed=false를 유지한다. 정상 작업 성공이나 model/backbone 성공률에
합치지 않는다. 일반 사용자 앱의 악성 AX나 사용자 동시 입력 보존을 시험한
것은 아니다.

### Two-app signed recovery and saved-state checks

`tacto eval signed_native_recovery_multi_app --report NEW.json` is an isolated
GUI/native scenario. It runs the owned 24-stage AppKit workflow with three real
sheet recoveries and interleaves three separately owned TextEdit saved-state
checks after stages 8, 14, and 20. After each child, the host re-reads receiving
counters and obtains a fresh parent observation before continuing. Child Save
failure or unverified readback stops continuation and does not repeat the Save.
Offline mode skips this case. It uses no model and grants no durable resume
or causal writer attribution. See the dated verification record for the actual
run, independent counters, source subset, input audit, and remaining gaps.

`tacto eval signed_native_recovery_multi_app_disconnect --report NEW.json` checks
an expected stop after the first verified TextEdit Save and cleanup. The host
shuts down only its own inherited socket; the next parent read fails and poisons
that channel. Independent counters must stay at stage 8, with one child Save
and no continuation. The evaluator may PASS this negative case while the parent
replay and task oracle remain false. It does not reconnect, repeat the Save,
restart a service, or certify automatic resume.


`tacto eval signed_producer_worker_reconcile --report NEW.json` is an isolated
native causal continuation case: a Save worker exits17 after host receipt,
then a different signed worker uses the public reconciliation API and performs
one new owned button operation. Existing restart refusal remains available as
`signed_producer_worker_restart`. Both are skipped offline; reconcile must be
requested explicitly and does not create an automatic resume cursor.


`general_visual_input_fixed`와 `general_visual_input_codex`는 owned opaque
canvas에 Right를1회 보내고 정확한 `A😀한글`을 입력하는 isolated signed native
평가다. 전자는 고정 계획, 후자는 실제 이미지 모델 호출을 사용한다. 일반 실행기의
각 단계 수신 증거·최종 exact text와 다른 창의 비변경을 확인하며 offline은 SKIP다.
Flow의 `visual_input_*`와 실행 경로를 구분하고 helper failure 때 자동 재전송하지
않는다. 소유 fixture 검증이며 사용자 동시 타이핑·일반 앱·동일 조건 제품 비교의
완료를 뜻하지 않는다. private report는 실패 뒤 소유 child가 살아 있으면 마지막
receiving 상태도 기록하며, 읽지 못한 상태를 입력 없음으로 꾸미지 않는다.


`signed_native_recovery_multi_app_parent_change`는 24-stage parent workflow의
verified stage8 뒤 owned TextEdit Save·cleanup을 완료하고, signed helper로 부모의
Other 버튼을1회 누르는 별도 fault를 주입한다. parent state가 달라지면 기존
interleave gate가 이후 입력을 중단해야 PASS다. task oracle은 false로 유지한다.
이는 child 저장·수신 확인 후 parent 변경을 다루는 negative integration case이며
실제 사용자의 앱/문서에 fault를 주입하거나 자동 resume를 승인하지 않는다.
GUI/native isolated invocation이고 offline은 SKIP다.


### Native model paging limits

The owned native-select model harness uses max_pages=8, matching the current
public paging API default. Host Python callers may explicitly supply1..32,
including a lower legacy limit. The report records planner_request_limit and
planner_call on each accepted task-prompt measurement. The token budget, screen/
time limits, input audit and receiving oracle remain separate gates.
A smaller cap or a failed receiving/dispatch check stays a failed task even if
the model found the label. Candidate/context reads and validation retries share
the request limit. An eight-request harness failure is not repaired by increasing
that limit without recording a new cohort.

### Native recovery failure summaries

A native recovery task can reach all receiving stages and verify child saved
states while its input-preservation audit reports CHANGED or INCONCLUSIVE.
The overall scenario still fails. Its bounded exception summary now includes
`task_oracle_passed`, `multi_app_saved_state_verified`, `input_audit_status`,
source-subset stability and child cleanup, so this is distinguishable from a
failed parent operation. Raw window text, input samples and provider messages
remain in private evidence, not the exception. A task oracle is not a proof of
unchanged user input or causal producer attribution.
