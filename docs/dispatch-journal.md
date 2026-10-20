# Opt-in process-restart dispatch barrier

`tacto.dispatch_journal.DurableDispatchDriver` wraps a trusted driver’s `act`
method. It reserves an ordinal slot using exclusive creation, flushes and
fsyncs the reservation file and directory, then delegates input. A second
worker using the **same directory for the same logical run** refuses the first
existing slot, regardless of whether the payload changed or a reply was logged.
The refusal is sticky: retrying in that wrapper cannot advance past the slot.

Programmatic use:

```python
from tacto.dispatch_journal import DurableDispatchDriver

# underlying_driver and the run's absolute private path are host-owned.
with DurableDispatchDriver(underlying_driver, journal_directory) as guarded:
    result = guarded.act(observation_id, target, action)
```

CLI execution can opt in with the same absolute run directory:

```sh
tacto run owned-flow.json --helper --journal-dir /absolute/private/run-directory
tacto replay owned-recipe --helper --journal-dir /absolute/private/run-directory
tacto do "owned task" --helper --journal-dir /absolute/private/run-directory
```

The option is supported by `do`, `run`, `replay`, and `web-upload`. It is removed before
intent matching, and `do <IR file>` retains it through nested dispatch. Existing
consent and typed receipt requirements still apply; journaling grants no
approval or completion authority. The descriptor is closed on command return
or exception. The default path remains unchanged when the option is absent.
No directory is chosen automatically. Retain the same directory after worker
termination and inspect receiving state when execution refuses reconciliation.

The directory must be owned by the current user, mode0700, and not a final
symlink. Files use mode0600. Records contain an operation SHA256 and closed
states/categories, without plaintext target/action/screen content. A hash is
not encryption and may disclose guessable payloads. The directory is pinned
by an open descriptor; this is not a sandbox against the same user or trusted
host. The wrapper closes its journal descriptor, not the underlying driver.

`read` actions bypass the journal under the trusted driver contract. Other
methods are delegated unchanged. Native visual executors are covered when the
host passes this same wrapped driver: their click/key/text dispatch calls its
`act`. Executors given the underlying driver, browser page methods, or any code
calling the underlying driver directly bypass this barrier. One wrapper/ordered run
per directory is required. Different directories do not coordinate. There is
no automatic run-ID selection, migration of existing traces, or pruning.

Pre-dispatch journal failure sends no input. An exception, unknown/unverified
reply or reply-log failure stops further input. A log failure after dispatch
has **unknown** effect. Even an `acted` reply is not receiving-state completion
proof. Confirmation/handoff replies conservatively block; this initial API
does not implement approval resumption. Even when a host receipt later resolves
a lost response, this barrier does not automatically unblock later input.
The bounded10,000-slot catalog refuses
additional input rather than evicting records.

On restart, retain the directory and independently read the actual receiving
state using the task's causal evidence contract. Do not delete old slots,
choose a fresh directory, or skip to a later ordinal merely to retry an
uncertain operation. This barrier alone provides no safe resume cursor, reconciliation
workflow, exactly-once external delivery, or power-loss/filesystem durability
attestation. In particular, newly created parent directories are not all
ancestor-synced. The tested failure is worker termination and restart.

## Verification

`tests/test_dispatch_journal.py` starts a real local HTTP receiver. A child
posts once, receives200, and terminates before logging its reply. A second
child using the same directory refuses dispatch; the receiver has exactly one
POST. This is a loopback transport test, not browser/native GUI automation.
A separate Agent recipe integration uses in-memory TextEdit/evidence fixtures;
replaying from the beginning cannot save twice. Other cases check pre-write
failure, post-input log failure, unknown replies, private directory policy,
and payload omission.

Together with replay commit evidence, general-menu commit, recovery event and
malformed response regression tests: **34 tests / 2.774s / OK, no skips**.
The report preserves a terminal summary and post-test source hashes, not a raw
log or a before/after source freeze. Native multi-app restart and causal
continuation remain unverified.

CLI wiring regression additionally covers direct run/replay, strict intent
matching, nested IR dispatch, signed-helper factory selection, trace/receipt
delegation, invalid options before onboarding, and descriptor cleanup on
return/exception. With the surrounding regression modules: **64 tests /
2.994s / OK, no skips**.
Raw log (local evidence: `../reviews/2026-10-11/cli-dispatch-journal-regression-20261011-a.log`)
and explicit source subset report (local evidence: `../reviews/2026-10-11/cli-dispatch-journal-regression-20261011-a.json`)
are preserved; declared CLI/journal/test sources were unchanged before/after.
These CLI tests use owned in-memory app/evidence fixtures. Installed signed
launcher `nlc run --help` also returned0 and displayed the new option; it did
not launch a task or attest GUI recovery.

## Chromium + CLI worker termination

`tests/test_cli_journal_browser_restart.py` injects the optional BrowserDriver
into the CLI factory and uses actual headless Chromium to press an owned page's
Save button. The page POSTs to a loopback server, which writes an owned temporary
file. The worker observes the Saved UI and independently reads the receiving
inbox, closes its browser, then terminates with17 before reply logging.
A fresh worker loads the page and runs the same `cli.main` command/directory;
the CLI refuses reconciliation with execution status1 and the test worker
exits0. Receiving POST count stays1, file bytes equal `owned body`, and the
journal has only its reserved line. Thus an apparently fresh page cannot cause
this CLI invocation to send the operation again.

With the existing Chromium recovery and CLI/journal regression modules:
**18 tests / 20.761s / OK / no skips**.
Raw log (local evidence: `../reviews/2026-10-11/cli-browser-worker-restart-20261011-a.log`) and
manifest/report (local evidence: `../reviews/2026-10-11/cli-browser-worker-restart-20261011-a.json`)
are preserved with declared source subset unchanged before/after. This proves
a real browser/HTTP/file effect and CLI barrier, not the shipped CLI selecting
a web backend, signed native GUI, an external recipient, automatic
continuation, long multi-app recovery, or exactly-once delivery under every
failure. Browser dependency absence is an explicit test skip and cannot count
as this effect having occurred.

## Signed native producer reply loss

`tacto eval signed_producer_file_reply_loss` is an isolated, model-free owned
AppKit save producer evaluation through the installed signed helper. It records
a reservation before the guarded native Save click. After the helper returns
`acted`, an owned wrapper injects a TimeoutError before journal reply logging.
The caller preserves an unknown-effect response. A newly constructed barrier
using the same directory refuses before any second helper call.

Native producer report (local evidence: `../reviews/2026-10-11/signed-producer-file-1791691604910595000.json`)
and evaluator report (local evidence: `../reviews/2026-10-11/signed-producer-journal-reply-loss-20261011-a.json`):
**1/1 PASS / 12.895s / no skips**. Underlying commit dispatch1, reservation1 line,
`ProducerArtifactEvidence` accepted against the original event, one exact new
producer completion plus exact fresh RTF bytes, producer exit0/owned child
reaped, explicit source subset stable. Input audit is UNCHANGED_SAMPLED, not
continuous human-input preservation. Reports use mode0600. Owned fixture
preferences were removed and the domain was empty afterward. Related receipt,
journal and evaluator unit tests:27 tests/0.909s/OK/no skips (terminal result,
not a separately preserved raw test log).

This test reconstructs the barrier in the same Python process;
`worker_process_restarted=false` is explicit. The lost response is injected
after a real native helper response, rather than an actual helper transport
disconnect. Preparation actions remain outside this particular journal.
The app cooperates through a host-bound callback under an exclusive owned
directory assumption. This is not general TextEdit/application writer
attribution, true native worker restart, or safe automatic continuation.

## Actual signed native worker restart

`tacto eval signed_producer_worker_restart` keeps the owned Save producer under
its parent evaluator. A separate signed launcher runs the internal
`signed_native_journal_worker` with a parent-created mode0600 configuration.
The worker uses its own authenticated helper channel, fresh observation and
strict Save panel token for the declared directory/name/RTF settings. It
prepares and validates the actual event's ProducerArtifactEvidence, persists
that owned evaluation proof, then `os._exit(17)` before journal reply logging.
The next signed launcher starts a different Python PID, reads the one written
callback and exact bytes, and refuses its journal reservation before any
underlying input. No channel descriptor is transferred between workers.

Producer report (local evidence: `../reviews/2026-10-11/signed-producer-file-1791692205073945000.json`)
and evaluator report (local evidence: `../reviews/2026-10-11/signed-producer-worker-restart-20261011-f.json`):
**1/1 PASS/20.018s/no skips**. Worker exits17/0, distinct recorded PIDs,
restart underlying input calls0/receiving sequence1/fresh bytes match, one
journal reservation line, event-bound ProducerArtifactEvidence accepted,
producer exit0/reaped, explicit source subset stable. Input preservation is
only UNCHANGED_SAMPLED. Related48 unit/integration tests/0.899s/OK/no skips
were confirmed in the terminal; no separate raw unit log is claimed.

Worker configuration/proof handling is fixture-only. It accepts a private
owned `nlc-save-writer-*` directory under Library/Caches, a fixed producer
bundle, bounded private single-link records and the parent-bound callback
inode/nonce/process identity. Configuration options apply only to the isolated
internal worker scenario. The runtime launcher environment allowlist and
signing requirements were unchanged. Read stability excludes access time
while retaining identity, ownership, mode, link, size, modification and change
time checks. New observations have different volatile AX indices: the parent
validates the worker's actual event digest and operation semantics, while the
worker independently validates its physical Save panel; it never reuses the
parent channel's index/token.

Earlier failed cohorts remain preserved: a/b could not pass custom environment
configuration through the signed launcher (a lacks detailed child diagnostics),
c rejected a read-induced access-time change, d completed both workers but
failed the parent index comparison, and e failed report serialization because
a mutable event acquired a typed proof. Its partial producer JSON
` signed-producer-file-1791692161526790000.json ` is retained as invalid evidence;
the corresponding evaluator report records failure. Successful f uses explicit
scoped arguments, content/identity checks, actual worker event validation and
an event snapshot for JSON reporting. These failures are not silently replaced
or counted as passes.

This proves worker termination after real native receipt and fresh-worker
readback/refusal in a cooperating owned app. Preparation is outside this
commit journal. The second worker is a fixture readback/check, not a resumed
user task; the receipt record is not a public reusable resume authority.
Automatic causal continuation, multi-app checkpoints, arbitrary native app
writer attribution and power-loss/exactly-once guarantees remain unimplemented
or unverified.


### 2026-10-11: native visual journal 적용 경계 실기

NativeVisualClickExecutor는 전달받은 driver.act로 click/key/text를 보내므로
host가 같은 DurableDispatchDriver를 전달하면 journal이 적용된다. 기존 문서의
native visual 전부 제외 표현을 수정했다. underlying driver를 따로 전달하거나
browser page API를 직접 호출하면 여전히 보호 밖이다. 일반 CLI에 visual scope
등록/자동 causal resume를 추가한 변경은 아니다.

새 isolated visual_input_journal은 준비 click 뒤 visual Flow에만 wrapper를
연결한다. 실제 signed 평가 (local evidence: `../reviews/2026-10-11/visual-flow-window-input-fixed-1791696415047711000.json`):
barrier 과제1/1PASS7.776s/no skips, 그러나 사용자 Flow 자체는ok=false다.
첫 Right의 실제 keyDown/keyUp1/rights1을 수신했고 text는 비어 있었으며 창B는
unchanged다. native helper의 unverified/observe-effect 응답은 journal unknown으로
기록돼 두 번째 text dispatch가 not_started/dispatch_journal_unavailable로
막혔다. host의 receiving proof가 있음에도 장벽을 자동으로 해제하지 않는다.
같은 디렉터리로 새 wrapper를 만들자 slot0 requires_reconciliation,
underlying calls0/상태 unchanged였다. reservation+unknown reply2줄을 보존했다.
worker_process_restarted=false이며 같은 Python process의 wrapper 재구성이다.
서로 다른 실제 native worker 증거는 기존 producer Save 평가의 별도 범위다.

source subset stable/owned child reaped/audit UNCHANGED_SAMPLED873표본,
최대 간격0.118588s다. 모델 호출0/require_selection=false/comparison_eligible=false.
관련41 tests/1.032s/OK/no skips; 잘못된 journal mode는 출력 파일/Driver 생성 전
거부한다. offline 실제실행exit2/0scored/1skip도 확인했다. 이 결과는 시각 journal
입력 장벽을 입증하며 자동 이어 실행, worker restart와 일반 앱 완료는 미입증이다.
G6의 host receiving proof와 durable causal cursor를 연결하는 복구 계약은 남아 있다.

### 2026-10-11: 24단계 native 반복 복구와 예산 소진

소유 AppKit fixture에 opt-in 24단계/5·11·17단계 직후의 세 sheet를 추가했다.
기존 12단계/한 sheet 시나리오는 유지한다. 각 중단에서 signed helper의
no_modal 거부가 receiving state를 바꾸지 않는지 검사하고, 이름으로 정한
Dismiss recovery 뒤 새 관측·idempotent set_value 재시도로 진행한다.
이는 새 자동 checkpoint/resume API가 아니라 기존 replay 복구의 실제 확장 평가다.

실제 반복 복구 b (local evidence: `../reviews/2026-10-11/signed-native-recovery-1791717563034721000.json`):
PASS45.955s/stages24/budget3/accepted24/blockedWrites3/cancels3/sheet=false/
otherClicks0. escalation step5·11·17/effect unknown, events30/고유 event IDs30,
세 receiving 쌍 각각 이전 stage 유지→기대 stage로 증가했다.
예산 소진 c (local evidence: `../reviews/2026-10-11/signed-native-recovery-1791717672748017000.json`):
expected-stop PASS24.709s/stages24/budget1. 내부 replay.ok=false/
task_oracle_passed=false를 유지했다. terminal step11/set_value/
postcondition_timeout/effect unknown, events14/고유 IDs14, accepted11/
blockedWrites2/cancels1/sheet=true/otherClicks0. requests6·6·12 이후 추가
stage 입력·두 번째 복구가 없음을 independent receiving counters와 attempts로
검사했다. 종료 error code·step·effect도 검사하므로 다른 오류를 예상 중단으로
채점하지 않는다. 앞선 b25.100s도 같은 수신 상태를 확인했으며 c에서 종료
로그 oracle을 강화했다. 둘을 동일 source cohort라고 주장하지 않는다.
기존 12단계 a (local evidence: `../reviews/2026-10-11/signed-native-recovery-1791717511077405000.json`):
PASS21.088s/accepted12/blockedWrites1/cancels1/events14. 모든 실행은
signed InheritedHelperDriver/model calls0/owned child reaped/source subset stable/
UNCHANGED_SAMPLED다. continuous numeric caret/selection 보존은 증명하지 않는다.

최초 확장 a의 두 outer PASS는 wrapper가 옵션을 전달하지 않아 실제 stages12/
budget1만 실행했다. 원본을 보존하고
scope mismatch record (local evidence: `../reviews/2026-10-11/native-recovery-option-mismatch-20261011-a.json`)에서
확장 증거로 제외했다. 옵션 전달을 수정하고 eval에서 report stages/budget/
expected exhaustion의 요청 일치도 검사한다. 관련 unit/integration23 tests/
17.463s/OK/no skips를 terminal에서 확인했다(별도 raw unit log 없음).
offline은 fixture/model을 실행하지 않고 GUI/native isolated로 SKIP한다.
helper binary/signing/TCC는 변경하지 않았다.

여러 일반 native 앱/백본의 긴 작업, helper/network 단절 뒤 causal continuation,
비멱등 Save/Send의 신규 receipt와 중복 방지, durable checkpoint의 공개 재개
권한은 여전히 남아 있다. 소유 fixture24단계/host scripted recovery를 일반
모델 복구·여러 앱·전체 G0~G10 완료 또는 동일 조건 Sky 비교로 확대하지 않는다.

A separate explicit [host reconciliation API](journal-reconciliation.md) can
advance a fresh wrapper only after every reserved causal commit is matched to
its original host plan and fresh producer/receiver evidence. Default restart
behavior remains unchanged; this does not enable ephemeral or saved-state-only
workflow resumption.
