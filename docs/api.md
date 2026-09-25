# Python API

CLI(`nlc …`)가 하는 일은 모두 Python에서도 할 수 있습니다. 이 문서는 공개 진입점과 그 약속을
정리합니다. 흐름 전체의 설명은 [`recipes.md`](recipes.md), IR 작성 가이드는
[`../skill/SKILL.md`](../skill/SKILL.md)에 있습니다.

`tests/test_docs_follow_code.py`가 이 문서의 시그니처와 코드를 대조합니다. 코드가 바뀌면 이 문서도 바꿔야 테스트가 통과합니다.

## 레시피 (IR) — `nlc.flow`

```python
from nlc.flow import Flow, Step, Selector, Capture, Postcondition, replay
from nlc.contract import SafetyContract, Resource

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
  select_custom_option, press_key, menu, scroll, secondary_action, prepare_window, goto`.
- 엔진 동작: `decide` — 화면에 아무것도 하지 않고 판단만 합니다. 매개변수
  `question, choices, into, extract_from, compose, branches, goal` 의 뜻은 [`recipes.md`](recipes.md#판단-단계-decide)에.
- `Flow.intents` 는 레시피가 답하는 문장입니다(`"계산기에서 {{n}}의 제곱근 구해줘"`).
- `Flow.runtime_names()` 는 실행 중에만 생기는 이름(capture, 판단)입니다. `bind()`는 이 이름을 채우지 않고 남겨 둡니다.
- `nlc.flow.every_step(steps)` 는 분기 안까지 포함한 모든 단계를 순서대로 돌려줍니다.

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

`ReplayResult`:

| 속성 | 뜻 |
|---|---|
| `ok` | 끝까지 갔고 검증을 통과했는가 |
| `log` | 단계별 기록. `status` 는 `ok, done, error, gated, unverified, skipped, resolved, decided, drift`. 마지막 `done` 에 `result`, `captured` |
| `learned` | `{(판단 이름, 답): [단계]}` — 이번 실행에서 새로 조작한 분기. **저장은 호출자의 몫** |
| `events`, `commit_evidence` | 커밋 이벤트와 그 증거 |

## 에이전트 — `nlc.agent.Agent`

```python
from nlc.agent import Agent
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
- 콜백을 주지 않으면 해당 기능은 꺼집니다. `nlc do` 가 붙이는 콜백은 `nlc/cli.py` 의 `do` 명령을 보면 됩니다.

## 모델에 묻는 콜백 — `nlc.judge`

```python
from nlc.judge import model_judge, model_decider
escalate = model_judge(target="com.apple.calculator", provider="auto", narrate=print)
decide = model_decider(provider="auto", narrate=print)      # decide.compose 포함
```

둘 다 서명된 런처 안에서는 런처의 중계로 모델을 부릅니다(`authoring.reach_model`). 런처 안에서는
모델 CLI가 PATH에 없기 때문입니다.

## 실행 기록 → 레시피 — `nlc.promote`

```python
from nlc.promote import (candidate_from, CandidateStore, parameters, deployable,
                         lift_screen_values, decision_prompt, checked_decisions,
                         with_decisions, replays_the_walk)

c = candidate_from(result, target, goal, quote)   # 검증된 walk 만 후보가 됨 (아니면 None)
parameters(c)                                     # {"25": [1, 2]} — 요청에서 온 값과 위치
flow, why = deployable(c, "sqrt", request)        # 레시피, 또는 안 되는 이유 목록
CandidateStore().record(c, request, name)         # 레시피 옆 _candidates/ 에 저장
```

판단 지점 검토(`nlc promote` 가 하는 것):

```python
from nlc.authoring import reach_model, _extract_json
proposed = _extract_json(reach_model(decision_prompt(c), "auto", expect_json=True))["decisions"]
ok, refused = checked_decisions(c, proposed, request)     # 기록이 뒷받침하는 것만
flow = with_decisions(flow, ok, goal=c.goal, taken_names=frozenset())
assert replays_the_walk(flow, concrete, ok)               # 기록 당시 답으로 원래 실행을 재현
```

`lift_screen_values(c, flow, request)` 는 화면에서 읽은 값을 입력한 단계를 복사/추출/쓰기로 바꾸고
`(flow, {단계: 끼워 넣은 단계 수})` 를 돌려줍니다.

## 화면 단위 루프 — `nlc.general.run`

```python
from nlc.general import run
result = run(driver, target, goal, ask_here, *, max_screens=8, approve=None, narrate=None,
             envelope=None, witnesses=None, ask_where=None, spec=None,
             timeout_seconds=None, cancelled=None, prepare_commit=None, after_commit=None)
```

`ask_here(goal, candidates, done_so_far)` 가 화면마다 불립니다(`nlc.ask.ask_here_paged` 가 모델 구현).
결과의 `ok`, `verified`, `said`, `visits`, `effects` 를 보세요.

## 저장소

- `nlc.store.FlowStore(path=None)` — `list()`, `has(name)`, `load(name)`, `save(flow)`. 기본 위치는
  소스 체크아웃이면 `flows/`, 설치본이면 `$XDG_DATA_HOME/nlc/flows` (없으면 `~/.local/share/nlc/flows`).
- `nlc.promote.CandidateStore(path=None)` — `list()`, `get(name)`, `record(c, request, name)`. 기본 위치는
  레시피 옆의 `_candidates/`. 여기 있는 것은 재실행되지 않습니다.
