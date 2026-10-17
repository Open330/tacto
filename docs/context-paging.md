# 큰 관측값과 이력 조회

`ask_here_paged`는 큰 후보 상세와 실행 이력을 호출 단위의 참조로 보관한다. 모델은 `{"read_context":{"ref":"ctx_…","offset":0,"length":256}}`으로 원문을 조회한다. `offset`과 선택적인 `length`는 Unicode 코드 포인트 단위다. 길이는 양의 정수여야 한다. 참조는 다른 planner 호출에 재사용할 수 없으며, 실제 행동 검증은 축약 표시가 아닌 원래 후보를 사용한다.

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

이전에 제공한 조각은 예산 안에서 다음 요청에도 유지한다. 제외된 조각은 `provided_ranges`에 `text_in_prompt=false`로 표시하고 다시 조회할 수 있다. 제공 기록은 원문 전체를 읽었거나 의미를 검증했다는 증거가 아니다. 필요한 내용을 담을 공간이 없거나 호출 횟수 한도에 도달하면 명시적으로 중단한다.

참조가 있으면 후보 표시 예산의 1/3을 조회용으로 미리 남긴다. 조회 시점에 후보 페이지를 축소해서 요청한 후보가 사라지는 문제를 피하기 위한 선택이며, 후보 표시 밀도는 낮아진다. 대규모 자료 전체를 동시에 비교하는 추론과 지속적인 작업 간 기억은 별도 과제다.

실제 백본 확인에서는 약 6만 문자 후보와 5만 문자 이력의 끝부분을 각각 요청했다. Claude 첫 실행은 이전 조각이 다음 요청에서 빠져 6회 한도로 실패했다. 조각 유지 수정 후 Claude와 Codex는 각각 3회 요청으로 두 끝부분을 조회하고 원래 후보를 선택했다. 이는 텍스트 참조 지시 수행 검사이며 UI 실행, 포커스 비간섭, 제품 간 동등성 검사가 아니다. 이후 추가한 길이 지정 및 계측 정정은 별도 회귀 테스트로 확인했다.
