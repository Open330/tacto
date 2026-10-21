# Python API

CLI `tacto observe <app> --helper --json`은 backend 응답 전체를 JSON으로 출력한다.
기본 text 출력의 focused-window 요약을 적용하지 않으며 `custom_selects`의
state/options/visible/scroll_direction 같은 관측 metadata를 보존한다.
`--document <path>`는 JSON에서도 기존 문서 scope를 유지한다. observation
error는 원래 reason/diagnostic을 JSON으로 출력하고 exit1이다. 연결 자체의
실패는 기존 helper/doctor 진단을 따른다. JSON의 obs_id/index는 그 관측의
값이며 별도 CLI 호출의 입력 허가나 선택 목록 pin을 이어 주지 않는다.

CLI(`tacto …`)가 하는 일은 모두 Python에서도 할 수 있습니다. 이 문서는 공개 진입점과 그 약속을
정리합니다. 흐름 전체의 설명은 [`recipes.md`](recipes.md), IR 작성 가이드는
[`../skill/SKILL.md`](../skill/SKILL.md)에 있습니다.

`tests/test_docs_follow_code.py`가 이 문서의 시그니처와 코드를 대조합니다. 코드가 바뀌면 이 문서도 바꿔야 테스트가 통과합니다.

TextEdit의 direct/signed 읽기 API는 `driver.document_text(target,
document_token=None, max_utf16_units=1000000, document_path=None)`이다. keyword로
canonical 절대 `document_path`를 지정하면 해당 문서의 token을 발급한다. 이후
`driver.observe(target, document_token=token)`으로 그 문서 창만 관측할 수 있다.
경로와 token은 동시에 지정하지 않는다. 명시한 경로로 발급한 token은 경로 변경을
거절한다. 경로 없이 발급한 token은 같은 창·본문·프로세스가 유지되면 Save As 등으로
바뀐 현재 경로를 읽을 수 있다. 모든 token은 프로세스·창·본문 교체를 거절하고,
한 번의 읽기 도중 경로가 바뀌면 본문을 반환하지 않는다. 이 증거는 경로 변경을
승인하거나 원본 형식·저장 완료를 보장하지 않는다.
세부 지원 범위와 검증 제한은 [TextEdit 저장 증거](textedit-save.md)에 있다.
Python direct 문서 본문 읽기와 문서 token의 창 확인·저장 디렉터리/패널 조회·재검증은
각 작업의 lock 대기를 포함해3초와 caller의 남은 model 기한 중
작은 값을 공유한다. AX attribute/range마다 남은 기한과0.5초 중 작은 messaging
timeout을 설정하고 reset한다. 만료 후에는 본문·새 token을 반환하지 않는다.
늦게 반환한 창이나 저장 패널 witness도 거절한다.
OS 호출이나 lock 대기를 강제로 중단하는 보장은 아니다. 이 경계는 direct
문서 reader 작업에 적용하며 저장 dispatch나 scoped observe의 나머지 트리 순회를
포함한 전체 작업의 시간 상한으로 확대하지 않는다.

서명된 helper의 `driver.document_rtf_export(target, document_token,
max_utf16_units=1000000, timeout_seconds=3)`는 이미 발급한 TextEdit 문서 토큰의
전체 AX range를 RTF 바이트로 읽는다. 응답에는 `rtf_base64`, `rtf_sha256`,
별도로 읽은 본문의 `text_sha256`, UTF-16 길이와 현재 문서 경로가 있다.
본문과 RTF를 반복해서 비교하고 native identity·경로를 재검증한다. RTF 출력은
2 MiB까지이며 초과·변경·중단·기한 만료를 거절한다. 이는 RTF로 내보낸 range이며
원본 저장 형식이나 첨부 완전성 증거가 아니다. `authorizes_save`와 두 보존 검증
flag는 false다. live trace는 이를 기록하지 않고 trace 재생도 지원하지 않는다.

direct/signed `driver.act`의 `click_point` raw action은 최신 관측의 Window
element_index와 `point={"x":..., "y":...}`를 받는다. 호스트가
`risk_class="preapproved"` 또는 승인된 confirm과 `approved=True`를 명시해야 한다.
새 관측으로 결과를 확인해야 한다. raw 좌표는 Flow에 저장하지 않는다.
배경의 앱 내부 focused 창도 실제 key 창이 아닐 수 있어 first-mouse를 거절한
내용 영역은 이벤트를 무시할 수 있다. 필요한 창 준비는 명시적 prepare_window와
검증·새 관측으로 먼저 수행하고, 이미 전송한 불확실한 클릭은 효과 확인 없이
반복하지 않는다. 전송 횟수와 실제 내용 수신을 구분한다.
일반 시각 루프와 호스트에 등록한 `visual` Flow 단계에서 사용할 수 있다.
[창별 입력 계약](native-window-input.md)에 좌표·창 결합·기한·부분 실패 범위를 설명한다.

서명된 helper의 `driver.act(..., save_panel_token=token)`은 승인된
`risk_class="confirm"` 또는 `"preapproved"`, 정확한 `approved=True`와
`action.click.save_panel_guard` capability를 요구한다. 최신 관측의 정확한
`{"name":"click", "element_index":...}`에만 사용할 수 있다. 저장 패널 읽기로
얻은 token의 버튼·설정·디렉터리·프로세스와 선택적 문서 결합을 실제 AXPress
직전에 다시 검증하며, 한 입력 시도 전에 token을 소모한다. 이것은 저장 완료
증거나 `document_snapshot`이 아니다. [패널 검증 범위](save-directory-observation.md)를 참조한다.

## 레시피 (IR) — `tacto.flow`

```python
from tacto.flow import Flow, Step, Selector, Capture, Postcondition, replay
from tacto.contract import SafetyContract, Resource

flow = Flow.from_json(open("flows/sqrt.json").read())   # 검사하며 읽음 (잘못된 IR은 ValueError)
concrete = flow.bind(n="49")                              # 입력 채우기 · for_each 펼치기 (분기 안까지)
print(concrete.to_json())
```

| 타입 | 필드 |
|---|---|
| `Flow` | `name, target, steps, schema, params, tables, derived, intents` |
| `Step` | `action, selector, params, intent_text, expect, postcondition, safety, binding, unverified_ok, for_each, captures, skip_if, on_uncertain, answers, …` |
| `Selector` | `role, identifier, title, desc, value, ancestor, allow_nth, in_focused_window, click_ancestor` |
| `Capture` | `name, field, attribute, sensitive, optional, empty_ok, tail, many, limit` |
| `Postcondition` | `field, equals, contains, absent, absent_role, attribute` |

### Compact JSON contracts — `safety_defaults`

`Flow.from_json` accepts a top-level `safety_defaults` object. Declare shared
`effects`, `reversibility`, `resource`, `destination`, `data_classes`, or
`unknowns` once. Acting steps inherit them; a step's `safety` overrides only the
changed fields. Lists replace rather than concatenate; `resource` and
`destination` merge one level. Explicit `safety: null` disables inheritance.
`decide` does not inherit a contract; its branch actions do.

For example, keypad steps can share
`{"effects":["write_ephemeral"],"reversibility":"reversible","resource":{"object_type":"calculator entry","scope":"entry"}}`;
the clear step needs only `"safety":{"effects":["delete"]}`.
Use `"safety":{"effects":["read"]}` for a read-only step in that flow.
`evidence` and `commit_point` are step-specific and rejected in defaults.

This is a JSON authoring shorthand, not a new policy or implicit authorization.
Loading expands independent per-step contracts before binding, policy, dispatch
and result checks. `Flow.to_json()` emits expanded contracts for existing
consumers. Missing meaning remains a runtime refusal. `tacto run --help` includes
a complete Calculator example; ordinary authors need not read this manual.

`tacto run flow.json --helper --think 0 --timings` prints aggregate driver-call
and replay durations as `TACTO_TIMINGS` on stderr, without UI values or action
parameters. Polling duration includes the driver's internal observations.
This measures local replay, not an outer agent's model latency or billed cost.

### Shared selectors and checks

JSON accepts `selector_defaults` (Selector fields) and `check_defaults`
(`field` and `attribute` only). A nonempty step selector inherits shared
fields; local selector fields override them. Explicit `postcondition` and
`skip_if` objects inherit shared check fields. A local `field` replaces the
entire shared field. Absent/null/empty selectors and checks remain absent;
defaults do not create an action target or a predicate. Branch steps expand
under the same rules. Loaded flows and `to_json()` retain the ordinary expanded
representation, so policy, bindings and result verification are unchanged.

### One-flow signed session

`tacto session <bundle-id> --helper [--timings] [--idle-timeout 1..600]` observes,
prints `TACTO_SESSION_READY`, accepts one minified newline-terminated Flow JSON
record on stdin, replays on that signed connection, and exits. Input is bounded
to1 MiB and the default input deadline is90 seconds. Replay refreshes the
observation; it never acts on a borrowed index from another invocation.

For agent tools use `exec_command` with `tty:true`, `yield_time_ms:3000`, then
`write_stdin` with the returned session ID and JSON plus an actual newline.
Poll the same session if readiness has not arrived. Input echo and canonical
line limits are temporarily disabled on this invocation's terminal and restored
on success or cancellation. Invalid flows, target mismatches, model-judgment
steps and idle expiry end the session before input. The session does not read
stdin as approval; ordinary user-intent policy and host receipts still apply.
Use ordinary `run` for document scope, explicit/interactive approval, provider
judgment and restart journals. No daemon or shared external socket is created.

`absent_role="Sheet"`는 관측한 노드의 정확한 역할 토큰을 비교합니다(`AXSheet`도 허용).
`Sheet1`·`Spreadsheet` 같은 제목이나 값은 시트로 계산하지 않습니다. 기존 `absent`는
문자열 부분 일치이며 호환성을 유지합니다. 역할 부재는 제공된 트리의 관측 조건이고,
생략된 노드·다른 창의 부재나 입력 권한을 증명하지 않습니다.

- 화면 동작(`action`): `click, read, set_value, type_text, select_option, open_select,
  select_custom_option, press_key, menu, scroll, secondary_action, prepare_window, goto, open_folder`.
  `open_folder`(signed helper, `action.open_folder`)는 Finder 전용으로 존재하는 일반 폴더의
  절대 경로를 LaunchServices로 활성화 없이 연다(패키지·앱 거부). 결과는 postcondition으로 확인한다.
- 엔진 동작: `decide` — 화면에 아무것도 하지 않고 판단만 합니다. 매개변수
  `question, choices, into, extract_from, compose, branches, goal` 의 뜻은 [`recipes.md`](recipes.md#판단-단계-decide)에.
- `Flow.intents` 는 레시피가 답하는 문장입니다(`"계산기에서 {{n}}의 제곱근 구해줘"`).
- `Flow.runtime_names()` 는 실행 중에만 생기는 이름(capture, 판단)입니다. `bind()`는 이 이름을 채우지 않고 남겨 둡니다.
- `tacto.flow.every_step(steps)` 는 분기 안까지 포함한 모든 단계를 순서대로 돌려줍니다.

## 재실행 — `replay`

```python
replay(driver, flow, approvals=None, narrate=None, escalate=None, budget=0, *,
       prepare_commit=None, after_commit=None, commit_expected=None,
       decide=None, walk=None, confirm=None) -> ReplayResult
```

기본값 그대로면 **모델을 한 번도 부르지 않습니다.** 판단은 콜백을 준 곳에서만 일어납니다.

| 인자 | 형태 | 언제 불리나 |
|---|---|---|
| `approvals` | `{step: ApprovalToken}` | 승인이 필요한 단계를 실행 전에 승인. `Agent.authorize()` 가 만듭니다 |
| `narrate` | `(str) -> None` | 사람에게 보여줄 진행 설명 |
| `escalate`, `budget` | `(Uncertainty) -> Resolution \| None`, `int` | 예상 못 한 상황(`on_uncertain` 에 선언된 종류만). `budget` 회까지 |
| `decide` | `(question, choices, inputs) -> str \| None` | `decide` 단계. `choices` 가 `None` 이면 뽑기(`extract_from`) |
| `decide.compose` | `(question, inputs) -> str \| None` | 쓰기 판단(`compose`). 이 속성이 없으면 쓰기 판단은 `decision_unavailable` |
| `walk` | `(goal, question, choice) -> {"ok", "said", "steps"}` | 분기 판단이 단계가 없는 답을 골랐을 때 |
| `confirm` | `(description) -> bool` | 판단 값을 쓰는 승인 필요 단계를, 값이 정해진 시점에 |
| `prepare_commit`, `after_commit`, `commit_expected` | 호스트 증거 훅 | 저장·전송의 실제 결과를 확인 |

`prepare_commit(event)`가 반환하는 호스트 전용 `SavePanelCommitPreparation`은
event ID·action digest·obs ID에 결합한 패널 token과 receipt context를 담는다.
`replay`와 일반 실행은 이를 검증한 뒤 승인된 클릭에 `save_panel_token`을 전달한다.
모델이나 Flow JSON의 dictionary는 이 typed dispatch attachment를 대신할 수 없다.
이 객체 자체는 승인 또는 완료 증거가 아니며, `after_commit`의 정확한
`CommitEvidence`가 여전히 필요하다. AX의 불확실한 acted 응답 뒤에도 consequential
입력의 독립 영수증은 확인할 수 있지만, 유효한 증거가 없으면 추가 실행을 중단한다.

파일 상태와 이번 저장 요청의 완료는 구분한다. `ExistingTextFileReceipt`의
`SavedFileStateEvidence`는 `commit_expected['artifact_evidence_scope']='saved_state'`
가 필요하다. 정상 응답 뒤에는 이벤트가 `state_verified`가 된다. 저장 응답이
사라졌거나 `acted`에 `unverified`가 있으면 파일 내용이 맞아도 이벤트는 `unknown`이고 재실행은
`commit_unverified`로 중단한다. 상태 영수증은 남지만 재시도하지 않는다.
일반 실행의 `exactly_once` 저장 조건에는 이 증거만으로 성공할 수 없다.

`tacto.producer_file_receipt.ProducerFileReceipt`는 협력하는 앱의 완료 콜백을
호스트가 신뢰할 수 있게 읽는 경우에만 쓴다. 준비할 때 앱·PID·프로세스 시작
시각·nonce와 콜백 순서를 고정하고, 한 번의 새 완료와 신규 파일의 정확한
UTF-8 바이트를 대조하여 `ProducerArtifactEvidence`를 만든다. 기대값의
`artifact_evidence_scope='producer'` 및 producer identity도 일치해야 한다.
콜백 대기와 파일 readback은 같은 마감을 쓰고 어댑터는 한 번만 소비한다.
준비 시에도 신규 파일 경계 확인 전후로 콜백을 읽어 같은 ready 기록인지
확인한다. 준비 중 순번·상태·경로·해시가 바뀌면
`producer_preparation_changed`, 신원·읽기 오류는 기존 invalid/unavailable
코드로 거절한다. 이때 입력 가능한 context를 반환하지 않는다. 준비는 성공
여부와 관계없이 한 번만 시도하며 실패 후 같은 객체를 다시 준비하면
`producer_already_prepared`다. 이 재확인은 최종 입력 시점과의 원자적 결합이나
다른 작업의 callback을 일반 앱의 특정 UI 입력에 귀속시키는 기능은 아니다.
파일 검증 뒤에도 같은 마감 안에서 콜백을 다시 읽어 전체 완료 기록이 같은지
확인한다. 순번·상태·경로·해시가 바뀌면 producer_callback_changed로 거절하고,
신원·읽기 오류는 기존 invalid/unavailable 코드로 거절한다. 늦은 읽기는 완료
증거 없이 끝나며 어댑터를 재사용할 수 없다. 완료 순번은 heartbeat나 관측
횟수가 아니라 해당 생산 작업의 순번이어야 한다. 후속 heartbeat가 있다면
호스트 reader는 불변 완료 기록과 분리해 해석해야 한다. 이 재확인도 콜백과
파일의 원자적 snapshot, ABA 또는 마지막 검사 이후 변경 부재를 증명하지 않는다.
콜백 reader의 인증은 호스트 책임이며 이 타입 자체는 커널의 writer 증명이나
실행 승인이 아니다. TextEdit에 자동 적용되지 않는다. 이미 있던 파일 내용만
맞거나 콜백의 nonce·순서·경로·해시가 다르면 완료 증거를 발급하지 않는다.

네이티브 저장창 관측의 `observation_budget`는 내용 없는 비용 메타데이터다.
`accounting_version=1`은 속성 조회·탐색 작업 최대5000, 서로 다른 AX 항목
최대5000, 배열 하나 최대5000항목, 반복 배열 검사 합계 최대20000항목을
별도로 제한한다. 합계는 `read`·`revalidate`·`validateDocument`의 각 예산
단위이며, 반환한 `peak_*`는 같은 reader의 예산 단위 중 최댓값이다.
같은 AX 항목이라도 속성을 다시 읽고 모든 배열 항목을 다시 검사한다.
관측의 기본3초와 helper 요청의 최대6초 제한은 유지한다. 이 계약은 이전의
단일5000 풀과 다르며, 더 큰 목록을 무제한 지원하지 않는다. 오류와 성공
응답 모두 가능한 경우 비용 메타데이터를 포함한다.

`ReplayResult`:

| 속성 | 뜻 |
|---|---|
| `ok` | 끝까지 갔고 검증을 통과했는가 |
| `log` | 단계별 기록. `status` 는 `ok, done, error, gated, unverified, skipped, resolved, decided, drift`. 마지막 `done` 에 `result`, `captured` |
| `learned` | `{(판단 이름, 답): [단계]}` — 이번 실행에서 새로 조작한 분기. **저장은 호출자의 몫** |
| `events`, `commit_evidence` | 커밋 이벤트와 그 증거 |

## 에이전트 — `tacto.agent.Agent`

```python
from tacto.agent import Agent
agent = Agent(driver=None, store=None, narrate=None, author=None, judge=None,
              interpret=None, general=None, outcome_context=None,
              commit_adapter_factory=None, decide=None, review=None)
turn = agent.handle("계산기에서 49의 제곱근 구해줘", ask=lambda text: True, think=None)
print(turn.status, turn.say)          # Turn(status, say, flow, detail)
```

- `handle()` 순서: 레시피 찾기(문장 → 핵심 단어 → `interpret`) → 없으면 `general` 로 화면을 보며 조작
  → 검증되면 후보 저장 → 있으면 입력을 채워 `replay`.
- `think=None` 이면 에이전트 실행에서 승격된 레시피는 1, 나머지는 0.
- `ask` 는 승인 질문에 답합니다(실행 전 `authorize`, 실행 중 `confirm` 모두).
- 성공한 실행 뒤: 요청 읽기로 찾았다면 그 말투를, 새 분기를 조작했다면 그 분기를 저장합니다.
- 콜백을 주지 않으면 해당 기능은 꺼집니다. `tacto do` 가 붙이는 콜백은 `tacto/cli.py` 의 `do` 명령을 보면 됩니다.

## 모델에 묻는 콜백 — `tacto.judge`

```python
from tacto.judge import model_judge, model_decider
escalate = model_judge(target="com.apple.calculator", provider="auto", narrate=print)
decide = model_decider(provider="auto", narrate=print)      # decide.compose 포함
```

둘 다 서명된 런처 안에서는 런처의 중계로 모델을 부릅니다(`authoring.reach_model`). 런처 안에서는
모델 CLI가 PATH에 없기 때문입니다.

## 실행 기록 → 레시피 — `tacto.promote`

```python
from tacto.promote import (candidate_from, CandidateStore, parameters, deployable,
                         lift_screen_values, decision_prompt, checked_decisions,
                         with_decisions, replays_the_walk)

c = candidate_from(result, target, goal, quote)   # 검증된 walk 만 후보가 됨 (아니면 None)
parameters(c)                                     # {"25": [1, 2]} — 요청에서 온 값과 위치
flow, why = deployable(c, "sqrt", request)        # 레시피, 또는 안 되는 이유 목록
CandidateStore().record(c, request, name)         # 레시피 옆 _candidates/ 에 저장
```

창에 결합된 탐색 키 후보는 명시적인 결과 조건을 받으면 재생 레시피로 변환할 수 있다.
`deployable(c, name, request, navigation_postconditions={step_index: Postcondition(...)})`에
관측할 필드·attribute·equals 또는 contains를 지정한다. 변환은 실행 응답으로 확인한
입력 결합과 같은 키·selector·binding을 유지한다. 결과 조건이 없거나 key/input binding이
변경되면 거절한다. 재생은 키를 한 번만 보내고 새 관측으로 결과를 확인하며, 결과가
맞지 않으면 다음 단계로 진행하지 않는다. 지원 키는 Tab·shift+Tab·방향키이고 Escape는
제외한다. 이 조건은 저장·제출·단축키의 권한이나 완료 증거가 아니다.

판단 지점 검토(`tacto promote` 가 하는 것):

```python
from tacto.authoring import reach_model, _extract_json
proposed = _extract_json(reach_model(decision_prompt(c), "auto", expect_json=True))["decisions"]
ok, refused = checked_decisions(c, proposed, request)     # 기록이 뒷받침하는 것만
flow = with_decisions(flow, ok, goal=c.goal, taken_names=frozenset())
assert replays_the_walk(flow, concrete, ok)               # 기록 당시 답으로 원래 실행을 재현
```

`lift_screen_values(c, flow, request)` 는 화면에서 읽은 값을 입력한 단계를 복사/추출/쓰기로 바꾸고
`(flow, {단계: 끼워 넣은 단계 수})` 를 돌려줍니다.

## 화면 단위 루프 — `tacto.general.run`

```python
from tacto.general import run
result = run(driver, target, goal, ask_here, *, max_screens=8, approve=None, narrate=None,
             envelope=None, witnesses=None, ask_where=None, spec=None,
             timeout_seconds=None, cancelled=None, prepare_commit=None, after_commit=None)
```

`ask_here(goal, candidates, done_so_far)` 가 화면마다 불립니다(`tacto.ask.ask_here_paged` 가 모델 구현).
결과의 `ok`, `verified`, `said`, `visits`, `effects` 를 보세요.

## 저장소

- `tacto.store.FlowStore(path=None)` — `list()`, `has(name)`, `load(name)`, `save(flow)`. 기본 위치는
  소스 체크아웃이면 `flows/`, 설치본이면 `$XDG_DATA_HOME/tacto/flows` (없으면 `~/.local/share/tacto/flows`).
- `tacto.promote.CandidateStore(path=None)` — `list()`, `get(name)`, `record(c, request, name)`. 기본 위치는
  레시피 옆의 `_candidates/`. 여기 있는 것은 재실행되지 않습니다.


Python native/signed helper `press_key`는 `press_key.element_bound` capability에서 관측한
`element_index`가 필수다. 앱의 현재 입력 요소와 배경 창을 검증하며 Tab·shift+Tab·
방향키만 보낸다. 예외로 signed helper는 `press_key.return_in_go_to_folder` capability에서
`Return`을 "폴더로 이동" 시트(`GoToWindow`)의 경로 필드(`PathTextField`)에만, 보내기 직전에
다시 확인한 뒤 보낸다. 재생은 그 단계가 `is_submit: true`이고 `equals` postcondition을 가지며
누르기 전에 그 결과가 참이 아닐 때만 허용한다. 완전한 pair 전송도 `unverified: observe_effect`와
`reobserve_required: true`를 반환한다. 재생에는 입력 selector/binding과 명시적
attribute postcondition이 필요하다. signed helper도 같은 binding과 공유 입력 기한을 검증한다. [창 입력 계약](native-window-input.md)을 참고한다.


For native screenshot proposals, `tacto.native_visual_actions` exposes
`capture_window`, `NativeVisualClickExecutor`, and `run_native_visual`. Window
image pixels are mapped explicitly to native points and rechecked before a
host-authorized public `click_point`. See [native visual contracts](native-visual.md)
for one-use captures, explicit window preparation, replacement rejection,
remaining deadlines, host verification and the bounded signed image-model
relay. Model output does not authorize input or verify document completion.


`tacto.general.run` accepts optional `visual_scope`, a trusted
`tacto.general_visual.NativeVisualScope` built with `window_selector`, `planner`,
`authorize`, `verify`, and known `effects`. Optional `when` switches between AX
and visual reasoning, while `verify_step` must independently confirm a pending
visual input before another action or a return to AX. Both paths share the same
run budget and provider collection. Visual effects cannot widen the request
Envelope or replace consequential completion evidence. Results expose
`visual_used` and `visual_history`, and pixel runs are excluded from automatic
AX recipe promotion. See [native image scope contracts](native-visual.md).


`tacto.flow.replay(..., visual_scopes={"green": scope})` supports an explicit
`Step("visual", params={"scope":"green", "max_screens":2,
"timeout_seconds":10}, intent_text="green once", safety=contract)`.
`scope` is a host-registered `NativeVisualScope`; JSON carries its name, never
callbacks or pixel coordinates. The contract must have exactly the scope's
known reversible effects and no approval requirement. The host supplies the
planner, permitted region, receiving-result verification and Window selector.
`max_screens` is an integer 1–20; `timeout_seconds` is finite, positive and at
most 120, bounded further by the inherited model deadline. `when` is unsupported
for this explicit step. No selector, capture, skip condition or unverified
opt-out belongs on a visual step. An uncertain dispatch or missing host receipt
stops the flow before another step. Selected branches retain events, visual
history and `provider_calls`; later AX steps observe again. Host registration is
fixed for the replay. CLI replay has no registry and refuses these steps before
observing. Explicit image steps may call a model; ordinary AX replay keeps its
existing behavior. These manually authored steps do not enable automatic pixel
recipe promotion, persistent edits, or save verification.


`driver.act(obs_id, target, {"name":"window_input", "element_index":index,
"text":"A😀한글"}, risk_class="preapproved", approved=True)` addresses an
observed standard Window with an inaccessible input view. Use exactly one
`text` (1–64 UTF-16 units, no controls or navigation characters) or one `key`
(Tab, shift+Tab, arrows). The host must know and authorize the reversible
operation and independently verify the receiving view. The helper client sends
literal `window_input_authorized:true` and a bounded shared input deadline.
The complete two-event receipt is still unverified. Trace replay and scoped
document tokens refuse this operation. This raw API is available to host-registered visual scopes with explicit
input_actions; ordinary AX planning does not acquire an opaque input target; see [native input contracts](native-window-input.md).


A NativeVisualScope may set `input_actions=frozenset({"key","text"})` to permit
image proposals of those kinds. Defaults remain click only. The host planner
may use `tacto.visual.plan_native_input(capture, goal, history,
input_actions=frozenset({"key","text"}), provider="codex", model=None, timeout=60)`.
The mapped capture and host callback do not identify or focus an opaque first
responder; prepare and verify it explicitly. `NativeVisualClickExecutor.input`
executes the bounded proposal after fresh capture validation and literal host
authorization. General and registered visual Flow runs independently verify
receiving events; incomplete proof stops before later input. The shared one-use
registry forbids spending the same image as both a click and a keyboard input.

Trusted hosts can explicitly continue causal commit journals with
`tacto.journal_reconciliation.CommitReconciliation` and `reconcile_journal`.
`reconcile_and_replay(guard, commits, read_evidence, continuation, **replay_options)`
connects that proof gate to an explicitly authored new Flow, preserving ordinary
replay approval and typed receipt requirements. It freezes the Flow and static
approvals/expectations before evidence reads and uses the same wrapper/directory.
See [contracts and limitations](journal-reconciliation.md); this does not grant
approval, recover a task plan, reconnect a helper, or automatically replay a
recipe from a cursor.

## Browser file chooser

Optional headless/CDP `BrowserDriver.upload(obs_id, target, open_selector, paths,
approved=False, *, chooser_selector=None)` selects host-supplied files. It is
not itself a Flow action or general planner operation. `approved` must be exactly
`True`. Both indices must come from the same latest observation; defaults bind
the clicked control itself as the file input. A separate button opener requires
an explicit observed file-input `chooser_selector`. Hidden/unobserved inputs
cannot be named by inventing a DOM selector or observation index.

The driver verifies that the expected element is a connected, enabled file
input before clicking, subscribes before clicking, then checks the original
document and exact chooser element before passing files. An incompatible input
returns `upload_target_required`/`not_started`. A redirected chooser returns
`upload_target_changed`/`unknown`: the opener click occurred, but files were not
provided. Do not retry merely because this response is an error. Transport
exceptions return closed `upload_failed` without raw exception text that could
contain private file paths. `acted` means file selection only; receiving evidence
must separately confirm the expected bytes, account and destination. These
checks do not make page callbacks atomic or authorize arbitrary external upload.

### Host-registered upload flows

A Python host can register `BrowserUploadFile(filename, content: bytes,
mime_type='application/octet-stream')` values in a
`BrowserUploadScope(origin, opener: Selector, chooser: Selector, files: tuple)`.
`BrowserUploadDriver(browser_driver, scopes)` copies the registrations and adds
`action.upload`. Payloads contain immutable bytes, not paths read later. Limits
are16 files,16 MiB/file,64 MiB total; names must be unique basenames and MIME
values use a bounded type/subtype syntax. Hosts remain responsible for choosing
the authorized bytes, account and destination. Registration does not approve an
operation and cannot create access to an external receiving service.

Use `adapter.parameters(scope_name)` as a step's params. It returns only
`scope` and `attachment_digest`; the digest binds origin, both selectors,
filename, MIME, size and byte hash. A Flow `upload` step needs that adapter
capability, an opener selector, exactly `transmit` effects and a ContextBinding
with operation='upload' and params_digest=pin_params(params). Native backends
and an unwrapped BrowserDriver refuse this contract before observation/input.
Files and callbacks are never serialized into recipes. A recipe still needs
its matching host registrations after restart; registration is not durable
resume authority.

The adapter resolves opener and chooser from the same original observed tree,
checks the registered origin and live role/name/enabled state, then uses the
raw chooser identity checks above. The original approval, event and existing
prepare_commit/after_commit receipt path apply. Pages may send as soon as files
are selected, so upload intrinsically means transmit; it cannot be declared as
an ephemeral write to avoid approval or receiving proof. `acted` still means
selection only. A typed host receipt must verify the independently received
exact bytes and account/destination before replay can complete or continue.
DeliveredMessageReceipt accepts the closed registered-upload action shape;
host bind_request must pin the planned manifest and live destination before
selection. A compose-only form with a later Send has no receiving proof yet
and stops as unverified; no staged-upload receipt type is provided. The CLI registration workflow is described below. The general planner supports
registered candidates through the adapter as described below.

### Live process CPU metadata

`tacto.process_cpu.sample_process_cpu(pid)` returns a frozen ProcessCpuSample with
PID/start identity, raw user/system CPU and Mach timebase. `cpu_delta_ns(before,
after)` rejects changed identity/clock or either regressing counter and converts
the integer delta without float loss. `verify_cpu_clock()` compares a short,
bounded own-process workload to POSIX CPU; the optional Finder CPU probe requires
this check. The Darwin64 RUSAGE_INFO_V2 layout was checked against the installed
SDK and live host. Unsupported platforms, failed/exit reads and invalid samples
raise ProcessCpuError; they are not zero usage. This is whole local-process CPU,
not remote model cost or atomic liveness.

### CLI browser upload registration

`tacto web-upload /absolute/private/config.json --scope owned --check` validates
the selected registration and snapshots its files without browser/network/input.
Omit `--check` to open a fresh headless Chromium session, review the exact
account/recipient/body/file manifest and approve upload. Noninteractive execution
requires `--yes`; this is explicit transmit approval. Existing native browser
cookies/profile and signed native-helper permissions are not used. The site must
already expose the expected fields and request identity in this fresh session.

Install the optional web dependencies with `uv sync --extra web`. Both navigation
and inbox URLs are canonicalized locally with [Ada's WHATWG URL parser](https://github.com/ada-url/ada-python)
before use, including host case, default ports, internationalized hosts, IP
notation and escaped paths. The scope binds the canonical browser origin; the
original document, chooser, request and receiving checks still apply. Only
explicit HTTP(S) URLs with an authority are accepted. Credentials (including
empty userinfo), any fragment, backslashes, surrounding whitespace, control
characters and invalid ports are refused. Raw and canonical URLs are each limited
to4096 characters. Missing URL dependencies refuse execution; `--check` parses
locally without launching a browser or contacting either endpoint.

The version1 configuration is exact-key JSON (duplicate keys refused):

```json
{
  "version": 1,
  "url": "http://127.0.0.1:8000/",
  "scopes": {
    "owned": {
      "opener": {"title": "attachments"},
      "chooser": {"title": "attachments"},
      "files": [{"path": "/absolute/owned/검토.txt", "filename": "검토.txt", "mime_type": "text/plain"}],
      "receipt": {
        "account": "fixture-sender",
        "recipient": "fixture-recipient",
        "text": "expected exact body",
        "request_id": "expected-new-request-id",
        "request_control": {"role": "button", "title": "Send"},
        "request_attribute": "data-request",
        "fields": {"account": {"title": "account"}, "recipient": {"title": "recipient"}, "body": {"title": "body"}},
        "inbox_url": "http://127.0.0.1:8000/inbox/expected-new-request-id",
        "headers": {},
        "timeout_seconds": 2
      }
    }
  }
}
```

Configure the URLs, selectors and expected identities for the authorized site;
this example is not a universal upload adapter. The configuration must be an
absolute, owner600, single-link regular file (maximum1 MiB). Selected attachment
paths must be absolute owned single-link regular files. Leaf symlinks/FIFOs are
refused; before/after descriptor and path identities must match. Immutable bytes
are captured once, so later file changes cannot silently alter the registration.
Only the chosen scope's files are read. The BrowserUploadScope limits above apply.

The independent inbox endpoint returns404 before delivery and then an exact
InboxDelivery JSON object with request_id/message_id/account/recipient/
body_sha256/state='delivered'/attachments. Attachments contain filename/sha256/
size_bytes. The host reader credentials stay outside the page and printed output.
Redirects are refused;502/503/504 alone are transient. Reader responses are bounded
to1 MiB, use a socket timeout and check elapsed time between reads; this does not
prove remote cancellation or bound DNS resolution. Authentication/malformed
records are closed failures. Live UI field values and the specified data-*
attribute must match before selection, and a new independent exact receipt is
required afterward. A select-only compose form remains commit_unverified.

`--report /private/new-report.json` creates an exclusive600 JSON result with
events, logs and confirmed receiving proofs; an existing report refuses execution.
`--journal-dir /absolute/private/run-directory` uses the same durable pre-input
barrier as do/run/replay. Reuse the original directory after interruption;
existing slots refuse input even if receiving proof disappears. There is no
automatic resume/reconciliation or automatic resend. The receipt endpoint is a
trusted host integration: a JSON claim from an untrusted site is not independent
receiving evidence. Actual external account access and staged/native chooser
workflows still require separate integration and validation.
An interruption leaves an available report as web_upload_interrupted/unknown
and preserves the interrupt; this is not permission to replay the operation.

### General planner upload candidates

With BrowserUploadDriver, `general.run` offers upload only on each observed,
enabled registered opener at its registered origin. Candidate uploads contain
registered scope/digest and file filename/size/hash manifests; no content bytes
or local paths enter the model prompt. Multiple registrations on one opener are
explicit choices. `what_to_do_here` accepts only a displayed scope for upload,
copies its original digest and rejects path/content/digest overrides. Upload
ends the batch and needs a fresh observed plan afterward.

Before dispatch, the general executor checks the selected registration again
against fresh candidates both before and after approval. The Envelope must
admit transmit. Approval shows the selected manifest; the same independent
receiving hooks and expected account/destination/body/attachment checks used by
Flow apply. A valid confirmed DeliveryEvidence or ProducerArtifactEvidence can
verify a finished general run even without a cosmetic UI expectation. Model
done, dispatch acknowledgement, a bare boolean, sender transcript or saved-state
alone do not supply this causal verification. Existing read/UI verification
routes remain separately supported. Records preserve the chosen scope and
digest so a promoted recipe retains that exact registration reference; host
registration and receiving hooks are still needed on replay. This adds no
native chooser, external account access or automatic staged-upload proof.

Large registered upload manifests use the same lossless reference paging as
large option lists. The initial projection shows at most eight bounded scope
names and the total count; complete filenames/sizes/hashes and registrations
remain in the snapshot-bound complete_candidate reference. Upload validation
uses the original full candidate, not the excerpt. When the default context
reserve would reject a base prompt that fits the caller budget, the pager tries
a smaller reserve on the same cursor and references. Actual planning, context
reads and validation retries still pass the original request budget guard.
Insufficient chunk space or exhausted requests stops without selecting files.
This meters the chosen task-prompt encoding, not the provider's entire serving
context or its unknown tokenizer.

The owned opt-in probe is `.venv/bin/python
examples/probes/registered-upload-paging.py <private-report.json>
--registrations 64 --budget 2500 --max-pages 16`. It uses a live Codex planner,
headless Chromium and the test's own HTTP receiver; host verification and final
completion control are explicit. It creates an exclusive0600 report and records
source/CLI-entrypoint hashes, request token counts, provider metadata and exact
receiving evidence. It does not drive the user's native browser or compare Sky.

After `set_files`, the raw upload API rechecks the original document and that
its observed chooser element is still a connected file input. Nonempty input
requires a nonempty current FileList; an explicit empty/None selection requires
an empty FileList and reports `files cleared`. If a page clears/replaces the
input or navigates during the change callback, `upload_selection_unverified`/
`unknown` preserves the possible disclosure. It does not authorize another
selection. This checks selection continuity, not a byte manifest or an atomic
page transaction. File data may already have been read/sent. A valid original
event-bound receiving receipt can settle that uncertain reply in Flow/general;
without it the executor stops before replan/resend. Exact bytes/account/recipient
checks remain with the independent receiving adapter.

### Direct AX metadata transport experiment

`python -m examples.probes.finder_children_read_cost NEW_PRIVATE_REPORT.json`
is a source-checkout, macOS **read-only metadata** experiment. It is not a
signed GUI input backend, a substitute for the helper, or a readiness permit.
The direct reader needs its own existing AX metadata access; it does not open
permission settings or modify signing/TCC. The report is exclusive600 and
contains only closed roles, counters, AX statuses and source hashes, not window
titles, paths from the UI, element handles or raw exception text.

It selects at most24 nodes, two per known role, from at most512 visits with
bounded sampling queues. Selection truncation is explicit and does not prove
the full tree was read. Two alternating-order rounds compare full AXChildren
with count/range (64 per page, count cap4096, count recheck), plus three-attribute
batch with attribute-names followed by a supported subset batch. Only matching
ordered child identities or exact decoded attribute replies enter the respective
timing medians. Absent values and count0 are not silently declared equivalent.

Every RPC is guarded by a15s shared acceptance deadline, PID/start/owner checks
and a bounded AX messaging timeout. Timing includes those reader checks and
RPCs; it is not pure provider CPU or the signed helper's full traversal timing.
The optional calibrated CPU interval is whole Finder process usage only.
`passed` means the measurement completed with a sample; readiness_verified and
authorizes_input stay false. Repeated sampled handles may be warm, while a full
scan reaches other virtual nodes. Neither a fast sample nor a changed API proves
a faster or complete input preparation.

### Native selection parameter boundary

For native `select_option` and `select_custom_option`, supply a nonempty,
UTF-8-encodable string `option_label`. Omit `value`, or supply the identical
string: equality includes the exact UTF-8 representation. Unpaired surrogates
in the Python label, explicit JSON null, numbers, booleans, arrays,
objects, a different string and a canonically equivalent but differently encoded
string are refused before native element lookup/menu input. No trimming, coercion
or Unicode normalization is applied to this parameter equality. HTML selects
retain their separate label/value semantics.

`tacto.protocol.native_selection_parameters_problem(action)` returns a closed
reason or None. Both Python native paths and the helper client use it; the signed
helper independently enforces the same rule at act entry and advertises
`select_option.strict_parameters`. Wrapper/helper refusal is
invalid_selection_parameters/not_started without echoing the supplied label/value.
The signed launcher already rejects non-string payload fields; the helper guard
also covers matching-type semantic mismatches. Ordinary observation, menu pin,
selection identity, approval and receiving checks still apply to valid input.

`tacto eval signed_native_selection_parameters --report NEW.json` checks 40
parameter/observation refusal cases through the installed signed channel. It
addresses no valid observed control and does not prove an option-selection task.
Use the separate actual owned ComboBox scenario for receiving-effect verification.

### Partial provider usage summaries

`tacto.provider_observation.summarize_provider_calls(records)` retains observed
route attempts without counting the logical final result twice. Its
`unaggregated_costs` lists reported amounts with unknown currency or basis as
individual observations, including request/attempt indices and missing_fields.
It never adds those values to currency/basis cost buckets or invents USD.
Explicit0 remains an observed amount; absent amounts remain unknown.

`input_token_components` groups input/cache-read/cache-creation observations by
input_tokens_basis (total/uncached/null), each with observed_sum/unknown_records.
These components must not be added to the existing total_input_tokens summary: a
total-basis input count already includes cache reads. Unknown components do not
become0 or a derived total. See [usage scope and partial fields](provider-envelope.md#불완전-비용과-입력-토큰-구성요소).


Native `open_select` accepts an optional literal `opening_action` of `AXShowMenu`
or `AXPress`. Omission preserves the existing AXShowMenu path. Explicit AXPress
uses the same opening checks and menu pinning; it makes one bounded AXPress call
and never falls back on unsupported, timeout, or an uncertain result. It does
not activate the app, dismiss menus, or restore focus. No route guarantees that
an app will preserve another app's focus.

Flow IR may carry `params: {"opening_action": "AXPress"}` with the usual selector
and safety contract. A literal must be one of the two names; dynamic templates
for this mechanism are not supported. Replay requires
`action.open_select.explicit_ax` before input. Native direct and signed helpers
advertise it; BrowserDriver rejects this native option rather than ignoring it.
After opening, observe and choose only from the pinned menu using the existing
`select_custom_option` contract. This route was checked on an owned AppKit popup
with AXWebArea focus in the other app; ordinary app/AXTextArea/human typing parity
remains unverified. The opt-in `signed_native_select_press_flow_replay` scenario
also checks one new owned popup through serialized Flow IR parsing and actual
signed replay: four Steps, Beta1 then Alpha2, final menu closed, and zero observed
model requests. Each dispatch's receiving value/count/menu and sampled input audit
must match before the next dispatch. That actual replay used AXOutline focus in
the other app; it does not establish ordinary app or continuous typing parity.


### Captured attachment payload inventory (trusted Python host)

`tacto.document_attachments.attachment_inventory(body, ...)` inspects an already
decoded native attributed string. The host must obtain the complete intended
source/export strings independently; a partial AX RTF range cannot establish
that missing attachment payloads were preserved. No Flow/CLI attachment or
consequential save/send permission is registered by this API.

Defaults: 10,000 UTF16 units, 256 FileWrapper entries including repeated
attachment occurrences, 8MiB payload per inventory pass, depth16, 3s soft
deadline. Supported payloads are regular files and directories; links, missing
wrappers/names, unknown types, inconsistent replacement characters, changes or
resource-limit failures raise `AttachmentEvidenceError`. Bounded native
substring/data extraction precedes Python conversion. Framework calls and
native backing storage have no hard cancellation/allocation guarantee; late
results are refused.

Names and relative paths are returned as SHA256 identifiers alongside occurrence
positions, byte sizes and payload hashes. Two complete matching passes establish
sampled stability only. `compare_attachment_payloads(source, exported, ...)`
shares its soft deadline across both inventories and requires exact equality of
text, positions, names, topology and file bytes. Its
`captured_attachment_payloads_equal` does not attest full source format, all
original attachments, producer attribution or save authority; those flags stay
false. No GUI input, paths/URLs selected by a model, or filesystem writes occur.


TextEdit AXDocument directory URLs (for example RTFD packages) may end with one
slash. The authoritative native document readers normalize it only when the
remaining path is an existing canonical directory. Syntax-only parsing and
user-supplied document selectors remain unchanged; selectors use the canonical
path without a terminal slash. This normalization grants no save/export authority.
The isolated signed evaluator `tacto eval signed_document_attachments` opens one
new private owned RTFD fixture in already-running background TextEdit, retains the
window, and records actual signed RTF export payload comparison and sampled input
audit. It never saves, edits or sends. Missing attachments or changed input keep
the scenario failed. See the actual partial result (local evidence: `../reviews/directory-document-url-partial-audit-a.json`).


#### Captured private RTFD package reader (trusted host only)

`tacto.document_packages.capture_rtfd_package(path, *, max_entries=256,
max_depth=16, max_payload_bytes=8*1024*1024, max_utf16_units=10000,
timeout_seconds=3)` returns `CapturedRTFDPackage`: a decoded native body, bounded
attachment inventory, captured entry/byte counts and sampled stability. Paths
must already be canonical absolute `.rtfd` directories; package and immediate
parent must be current-user-owned0700, all nested directories0700 and regular
files0600/single-link. Selectors are not normalized. Reads use directory
descriptors/O_NOFOLLOW and bracket identities/names. Three matching snapshots
include empty folders and root identity, with native decode/inventory between
the second and third. Wrappers are reconstructed solely from captured bytes.

Input storage is bounded per snapshot; native decoding has no hard CPU/memory
or cancellation guarantee. The shared deadline rejects late results. This is
a trusted-host evidence utility without CLI/Flow authority registration. Returned
body/inventory may be mutable; `sampled_stable` does not prove continuous stability.
General source format, attachment completeness, producer attribution and save
authority remain false. Compare `.body` to a trusted expected attributed string
using `compare_attachment_payloads`; equality verifies captured payloads only.
See the actual owned package result (local evidence: `../reviews/rtfd-package-partial-audit-a.json`).


### Session guide and compact click input

`tacto session <bundle-id> --helper --guide` returns a trusted minimal authoring
guide and the initial observation in the same invocation. Normal session framing
and policy still apply. No guide is emitted without the flag. With --timings,
TACTO_SESSION_TIMINGS gives monotonic offsets for observation_ready_seconds,
input_ready_seconds, flow_received_seconds, replay_done_seconds and
session_done_seconds. Input waiting is not pure model inference.

Compact step {"click":"stable-id","equals":"result","skip_equals":"initial"}
expands to an ordinary click selector and explicit postcondition/skip_if before
existing parsing, contract checks, binding and replay. Identifier alternatives
can be a nonempty text list. Check aliases require check_defaults.field. Mixing
click with action/selector, or aliases with their expanded checks, is refused.
No safety semantics or predicate is added automatically. to_json emits expanded
steps. This format works in run and session, including nested branches.

### Focused Flow authoring and selector repair

`tacto session <bundle-id> --helper --reuse-guide --save /absolute/new.json`
adds parameter/template instructions only on request. A successful replay saves
the unbound Flow privately; current parameter defaults are bound only for replay.
Existing output paths refuse before UI work. Failed replay never saves a Flow.
A save failure after successful UI work reports that distinction; do not resend.
Ordinary `--guide` omits reusable-template details.

`invalid_flow` feedback identifies known malformed fields with zero-based step
paths and short corrections without echoing supplied values. Examples: compact
click/action-selector conflicts, unsupported actions, action parameter shape,
and top-level params object shape. Other schema errors receive a generic short
hint; not every schema error has a specialized correction yet.

`tacto run <flow.json> --helper --think 0 --repair-packet /absolute/new-packet.json`
writes a private packet only for a selector-resolution failure before dispatch.
It contains the failed action/selector, result check and up to six scoped
candidate selectors. It adds no driver observations or model calls. Success,
unknown completion and nested/dynamic flows do not produce selector packets.

`tacto repair <template.json> --packet <packet.json> --selector '<JSON>' --out <new.json>`
is an offline transformation. It requires matching target, action, step index
and old selector, a unique candidate in the packet, and a new output path. It
changes only the selector, preserving the rest of the expanded Flow. Candidate
lists are historical and bounded; uniqueness in the packet is not a guarantee
of uniqueness in the current full UI. Reobserve and verify the current checkpoint
before execution. The command never dispatches, approves, or resumes a Flow.

### Ready hints after unverified steps

A step with no postcondition is followed by a settle of up to one second,
because nothing says when its effect has landed. `ready_when` names one
element and the exact value that, in that app, means the step has landed;
replay moves on as soon as the element shows it:

```json
{"action": "click", "selector": {"identifier": "One"},
 "ready_when": {"field": {"role": "ScrollArea", "identifier": "StandardInputView"},
                "attribute": "Value", "equals": "1"}}
```

All three keys are required and the field is checked like a selector before
any input is sent. A hint is used only when false on the screen the step acted
on, is never recorded as verification, and unmet within the settle the step
settles as without one. A stop, lock or unreadable screen while waiting ends
the run. Older engines ignore the key.

A screen value is not a completion signal by itself: an app may show a value,
even the final one, before its own state has caught up, and moving on then
sends the next input too early. So hints are written only where the app's
meaning is known, and are not learned from what a run happened to see. The
built-in Calculator recipes carry its exact display after each press, read on
macOS 27 (operators `+ − × ÷`, the minus being U+2212). Inside a `for_each`
loop a step's `ready_when` may use `{{items_so_far}}`, what the loop has
entered so far ("1", then "12"; available to the hint only, and it takes the
place of a param of that name there), so `calc_arith` and `calc_multiply` can
name the display for any operands.
Exact only for plain integers below 1000: Calculator groups thousands and
spells decimals out, and those presses settle as before. In process on this
Mac, `12 × 34` went from 6.8 s to 0.35 s and `75 − 8` from 5.6 s to 0.22 s;
`1234 + 5` went from 7.9 s to 3.4 s and `0.5 × 2` stayed near its settle time.

### Multiple results at one checkpoint

A postcondition may include `all_of`, an array of other postconditions. The
primary condition and every child must pass on the same observed tree. A
primary field/equals plus `answers:true` still reports that field. An all_of-only
object also works; an empty unqualified condition never passes. Maximum32
children per object, depth8; nested selectors/values bind as normal.

Attach field/value postconditions to set_value steps to use polling rather than
an unconditional settled observe. Group checks that belong to the same
checkpoint; keep reads for different checkpoints or new data. Existing Flows
are not automatically rewritten. Example and measured behavior:
Grouped checks (internal measurement record; not published).

Grouped checks are saved as Flow schema3; older engines reject that version
rather than silently ignoring all_of. Plain flows retain schema2. Current
parsing promotes a grouped schema2 input to schema3 before saving.

Development runtimes also support schema4 for observed literal text that contains
`{{...}}`. Such text is stored in `literal_params`, separate from overridable task
`params`; callers cannot override these data values. Serialization preserves
literal/reference boundaries and never downgrades schema4 for grouped checks.
Older runtimes reject schema4. This behavior is not in installed Preview2.
Literal learning and validation (internal validation record; not published).

### Bounded saved-Flow batch

`tacto batch <flow.json | saved name> --helper [--max-jobs 1..1000] [--idle-timeout 1..600] [--timings]`
loads one immutable, fixed-target deterministic Flow and keeps one signed
connection. A saved name loads the recipe `tacto replay <name>` would run (a
built-in from the code); a path, or anything ending in `.json`, is a file. After TACTO_BATCH_READY, send newline-terminated objects containing
only params, with string values. Per-job normal replay authorization, fresh
observation and completion checks remain. By default no model is called.
Opt-in selector repair is described below; interactive approval, document scope
and dispatch journals use ordinary run instead.

Each result reports job index/exit/time. Failure or unknown completion stops;
queued requests are not executed. EOF or the job limit closes the connection.
The default is10 jobs/90s idle and records are bounded to64KiB. No observation
IDs or approvals survive into the next replay. Only a verified last-action
result allows the next fresh initial observation to bypass the redundant
helper settle delay. The foreground activation latch spans the whole batch.
Usage, verification and measured timings are recorded in an internal, unpublished batch validation report.

### Automatic selector repair

`run` and `batch` accept `--auto-repair codex`, `--max-repairs 1..10` (default2)
and `--repair-timeout 1..120` seconds (default60). Successful jobs call no model.
An undispatched selector failure can trigger candidate choice and checked suffix
continuation: [contract and verified scope](../skill/references/auto-repair.md).
The immediately previous step must have a verified postcondition, the repaired
step must have a new explicit result check, and its effects must be read or
write_ephemeral. Candidates must identify the chosen full-tree node uniquely.
A stable checkpoint and all meaningful tree content are required before and after the call. Captures,
parameterized selectors, dynamic/on_uncertain flows, activation and unknown
completion stop this shortcut. No journal/crash resume or approval transfer is
provided. Learned selectors stay in batch memory. Optional --repair-packet writes
only a terminal unrepaired failure, with original-template indexes.

Checkpoint comparison ignores numeric indexes and at most two completely bare
Group wrapper changes per comparison. It retains every other raw body, flag,
value, node order and kept ancestor path. Raw selector uniqueness and selected
node correspondence, result checks and native readiness guards are unchanged.
[Complex scenario verification](tacto-complex-scenarios-2026-10-14.md).


### Window-bound drag (signed macOS helper)

`tacto capture <bundle-id> --helper --window TITLE --output window.png` writes
a private PNG (refusing existing output files) and reports `pixel_to_point` scale
factors. Multiply image pixel coordinates by these factors for drag coordinates.
It uses the existing Screen Recording grant of the installed signed app.

`tacto drag <bundle-id> --helper --from X Y --to X Y [--window TITLE]`
sends one continuous left-button stroke. `--via X Y` can be repeated up to 12
times; duration is 0.1–2 seconds (default 0.6). Coordinates are window-relative
points from the top-left, including the titlebar. `--prepare` prepares a background
AppKit window that ignores first-mouse input. Default output is compact; `--json`
includes the complete fresh observation and preparation diagnostics.

Native action: `{"name":"drag","element_index":0,"from":{"x":20,"y":40},"to":{"x":220,"y":80},"duration":0.6}`;
optional `via` is an array of points. Requires `action.drag`, `drag.window_bound`
and the existing host coordinate authorization. Flow steps use a Window selector,
the same params and a receiving postcondition, with the task's normal contract.
Ephemeral gestures reuse that authorization without a second prompt.

The target is a background app's focused window. This route preserves the global
cursor and foreground. Cancellation/failure attempts mouse-up at the last sent
position, bound to the same process/window; partial effects remain unknown and
are not retried. A completed send returns `unverified: observe_effect` and
`reobserve_required: true`; verify the receiving control/canvas effect before
reporting success. It does not establish Finder file transfers, browser HTML
DragEvent semantics, window moving/resizing, or cross-app drop support.
