# Python API

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
| `Postcondition` | `field, equals, contains, absent, attribute` |

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
