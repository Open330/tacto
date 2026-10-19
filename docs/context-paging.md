# 큰 관측값과 이력 조회

`ask_here_paged`는 큰 후보 상세와 실행 이력을 호출 단위의 참조로 보관한다. 모델은 `{"read_context":{"ref":"ctx_…","offset":0,"length":256}}`으로 원문을 조회한다. `offset`과 선택적인 `length`는 Unicode 코드 포인트 단위다. 길이는 양의 정수여야 한다. 참조는 다른 planner 호출에 재사용할 수 없으며, 실제 행동 검증은 축약 표시가 아닌 원래 후보를 사용한다.

문자 위치를 모르면 `{"read_context":{"ref":"ctx_…","find":"Choice127"}}`으로
같은 원문에서 첫 literal 일치 주변을 조회할 수 있다. `find`와 `offset`은 함께
사용하지 않으며, 검색 문자열은 1~1024 Unicode 문자다. 정규식·대소문자 변환·
정규화는 하지 않는다. 응답의 `search.found`와 `match_start`는 그 원문에서의
검색 결과다. 없으면 `found=false`, `match_start=null`, 빈 조각을 반환한다.
일치 앞 최대128문자부터 기존 예산과 선택적 `length` 안에서 읽으므로 매우 짧은
length에서는 일치 자체가 아직 표시되지 않을 수 있다. 검색은 UI 재읽기나 입력이
아니며 전체 원문을 읽었거나 옵션이 유일하고 선택 가능하다는 증거도 아니다.
관측 snapshot·원래 후보 검증·모델 요청 횟수·전체 prompt 예산은 그대로 적용한다.

큰 선택 후보의 전체 옵션을 참조로 옮길 때도 현재 `visible=true`인 옵션의
이름을 최대8개와 전체 가시 옵션 수로 표시한다. 이는 생략된 원문 전체나 활성
상태를 대신하지 않는 부분 표시다. `visible`이 없는 옵션을 보인다고 추정하지
않으며, 실제 행동은 원래 후보의 disabled·visible·정확한 label/value 검사와
실행 시의 최신 binding 검사를 통과해야 한다. 이 표시도 같은 prompt 예산에 포함된다.

현재 요청에 들어가는 지시문, 후보, 참조 설명, 이전 조회 기록과 새 조각을 모두 합쳐 예산을 검사한다. 기본 계측은 UTF-8 바이트 기반 상한 추정이다. 이는 provider 메시지 포장이나 출력 토큰을 포함한 실제 청구 토큰 수가 아니다. 사용자 지정 계측기는 음이 아닌 정수를 반환해야 한다.

호스트 검증에 실패한 계획을 재요청할 때 붙는 안내문도 실제 요청 직전에
계측한다. 전체 요청이 예산을 초과하면 `context_budget_overflow`로 중단하고
그 요청을 보내지 않는다. `max_pages`는 후보·참조 조회와 검증 재요청을 포함한
논리 모델 요청 시도 수의 상한이다. 실패한 시도도 소비하며, `max_pages=1`이면
검증 재요청이 없다. 이 한도는 각 `ask_here_paged` 호출에 적용된다.
재요청 전에 한도가 소진되면
`model_request_limit`로 중단한다. 한 논리 요청 내부의 provider fallback,
CLI 도구 호출 또는 원격 요청 수를 제한하는 계약은 아니다.
거부 metadata의 `phase=validation_retry`는 재요청 단계임을 나타낸다.
요청 직전 계측기 자체가 실패하면 `token_counter_failed`로 중단하고,
미측정 token_count는 null이다. 이 오류의 원문을 결과에 복사하지 않는다.

실제 tokenizer를 사용할 때는 이름을 명시적으로 선택한다. 예를 들어 선택한
`o200k_base`의 task prompt 예산은 아래처럼 적용한다. 이 선택을 실제
serving model의 tokenizer 확인으로 취급하지 않는다.

```python
import tiktoken
from tacto.ask import ask_here_paged

encoding = tiktoken.get_encoding("o200k_base")
result = ask_here_paged(
    goal, candidates, provider="codex", budget=2500,
    token_counter=lambda text: len(encoding.encode_ordinary(text)),
)
```

`tiktoken`은 이 예시와 실험의 선택 의존성이다. `encode_ordinary`는 관측에
있는 특수 토큰처럼 보이는 문자열도 일반 텍스트로 계측한다.
공식 사용법은 [tiktoken 저장소](https://github.com/openai/tiktoken)에 있다.
provider의 숨겨진 system/tool 메시지, 메시지 포장 및 출력은 이 문자열 예산에
들어있지 않으므로 CLI의 전체 input telemetry와 직접 같다고 주장하지 않는다.

재현 가능한 실제 웹 선택 시험은 `examples/probes/token-budget-planning.py`에
있다. 자체 headless Chromium의 AX 후보·option 목록을 paging하고, 실제 모델이
고른 단일 select 옵션을 적용한 뒤 DOM에서 모든 선택값을 독립 검사한다.
시험에서는 telemetry를 얻기 위해 Muxa 우선 경로 대신 direct CLI adapter를
명시적으로 주입한다. 모델 답변은 실제 요청의 결과이며 가짜 답변은 사용하지
않는다. 실행에는 Playwright/Chromium과 `tiktoken`이 필요하다.

`--case COUNT:INDEX`를 반복해 큰 화면과 목표의 위치를 지정하고 `--locale ko`로
한글 컨트롤 이름을 검사할 수 있다. INDEX는0부터 시작하며 COUNT는1..256이다.
예를 들어 다음은64개 중 마지막 컨트롤,80개 중72번째 컨트롤만 변경한다.

```sh
PYTHONPATH=. .venv/bin/python examples/probes/token-budget-planning.py NEW_OUTPUT \
  --provider codex --case 64:63 --case 80:71 --locale ko --budget 2500
```

CLI 스냅샷은 실제 provider와 같은 환경에서 진입 파일과 version을 확인한다.
진입 파일의 hash는 인터프리터·라우팅·의존성 전체를 고정했다는 증거가 아니다.
모든 응답이 알려진 provider 접근 제한이고 계획·UI 실행이 없으면
`outcome.status=provider_unavailable`, `task_execution=not_attempted`로 분리한다.
일부 정상 응답이나 UI 시도가 있었던 실패를 이 상태로 숨기지 않는다.
원시 DOM oracle의 passed=false는 보존하지만 unavailable을 성공률 분모로
사용하지 않는다. 이 probe는 제품 간 비교율을 계산하지 않는다.

이전에 제공한 조각은 예산 안에서 다음 요청에도 유지한다. 제외된 조각은 `provided_ranges`에 `text_in_prompt=false`로 표시하고 다시 조회할 수 있다. 제공 기록은 원문 전체를 읽었거나 의미를 검증했다는 증거가 아니다. 필요한 내용을 담을 공간이 없거나 호출 횟수 한도에 도달하면 명시적으로 중단한다.

참조가 있으면 후보 표시 예산의 1/3을 조회용으로 미리 남긴다. 조회 시점에 후보 페이지를 축소해서 요청한 후보가 사라지는 문제를 피하기 위한 선택이며, 후보 표시 밀도는 낮아진다. 대규모 자료 전체를 동시에 비교하는 추론과 지속적인 작업 간 기억은 별도 과제다.

실제 백본 확인에서는 약 6만 문자 후보와 5만 문자 이력의 끝부분을 각각 요청했다. Claude 첫 실행은 이전 조각이 다음 요청에서 빠져 6회 한도로 실패했다. 조각 유지 수정 후 Claude와 Codex는 각각 3회 요청으로 두 끝부분을 조회하고 원래 후보를 선택했다. 이는 텍스트 참조 지시 수행 검사이며 UI 실행, 포커스 비간섭, 제품 간 동등성 검사가 아니다. 이후 추가한 길이 지정 및 계측 정정은 별도 회귀 테스트로 확인했다.


## 후보 페이지의 독립된 상태

`page_candidates`는 JSON으로 표현할 수 있는 후보 데이터를 검증하고 중첩 필드도
복사한 뒤 fingerprint와 페이지를 만든다. 호출자가 원본 후보를 나중에 바꿔도
기존 페이지는 바뀌지 않으며, 변경된 후보로 다음 cursor를 쓰면 StaleCursor가 된다.
페이지의 딕셔너리는 호출자 전용 사본이며 중첩 필드까지 불변인 타입은 아니다.

사용자 renderer는 후보 snapshot을 변경하면 안 된다. measure 전후의 JSON이
다르면 ValueError로 거절한다. 기본 renderer에는 이 추가 비교를 하지 않는다.
JSON으로 표현할 수 없는 값은 복사 전에 거절한다. 이 복사·검증은 UI의 원자적
snapshot이나 실행 허가가 아니므로 행동 시 최신 observation/binding 검증은 유지한다.


실제 owned HTML select probe의 `--locale ko`는 컨트롤 이름을 한글로 한다.
`--option-locale ko`를 함께 지정하면 옵션 이름과 실제 DOM 값도 한글로 검증한다.
다음은 한 명령으로 실행한다.

```bash
PYTHONPATH=. .venv/bin/python examples/probes/token-budget-planning.py NEW_DIR --provider codex --locale ko --option-locale ko --case 128:127 --budget 2500 --max-requests 16
```

이미 존재하는 출력 directory는 거부하며
실제 모델 요청 비용이 발생할 수 있다. 호출별 요청 한도를 소진하면 입력 없이
중단할 수 있다. `o200k_base` task prompt 예산이며 provider 전체 context/tokenizer
확인이나 동일 조건 제품 비교를 뜻하지 않는다.


### Isolated planning cwd and a live registered-upload cohort

The Codex text adapter now starts in its private request directory, with matching
CLI root and subprocess cwd. Text-mode user configuration remains active. In
the live 64-registration upload (local evidence: `../reviews/codex-planning-directory-paging-a.json`),
five task prompts measured by o200k_base used1437/1855/2081/2500/2500 tokens.
One exact selected file produced one POST and one independently observed message;
outcome done/confirmed, source subset and executable snapshots stable.
Provider-reported total input was17616/17337/17565/17980/17982. These are distinct
measurements: the2500 budget bounds the rendered task prompt, while CLI framing/
configuration/other context is not covered by that counter. Serving tokenizer
is unverified, comparison_eligible=false, external_destination=false.
A separate signed one-answer probe reported16194 input/12160 cached/11 output,
with model and cost null. Working-directory separation did not demonstrate a
reduction against the earlier16132-input one-answer cohort.


### Option reference ownership

A projected complete_candidate reference includes candidate_pick (the original
planner candidate number) and option_count when the original options field is a
list. The count covers the full referenced list, not the visible label excerpt.
The prompt distinguishes UI candidate pages from a control's complete options:
find the requested label in that control's ref, inspect visible/disabled/direction,
then use observed scrolling and a fresh observation before selecting.
Search still proves neither uniqueness nor current visibility. Original-candidate
validation and hidden/disabled-option refusals remain in force. These descriptive
fields grant no input authority and never substitute an element_index.
