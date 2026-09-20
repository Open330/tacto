# Signed provider 응답

`ask_model_result`와 `reach_model_result`는 `ProviderResult`를 반환한다. signed 경로는 `ask_provider --result-envelope`의 구조화된 결과를 launcher의 `provider_result_v1` 필드로 전달한다. 기존 `answer.text`와 `ask_launcher` 텍스트 API는 유지한다. metadata를 얻기 위한 두 번째 모델 호출은 하지 않는다.

응답은 로컬 request_id, 요청한 provider와 실제 관측 provider, transport/status/text/error/elapsed_seconds 및 관측된 model·원격 ID·token·cost를 담는다. 미관측 값은 null이다. NLC request_id를 provider_request_id로 복사하지 않는다. cost_basis가 CLI list estimate이면 실제 청구금액을 뜻하지 않는다.

`ask_launcher_result`는 필드 타입, 음수/비유한 수치, boolean token 수, 상태와 text/error의 모순, 외부 응답과 metadata의 불일치를 거절한다. 구형 launcher의 text-only 응답은 metadata를 합성하지 않는다. 기존 --json 옵션은 모델 답변 형식이며 --result-envelope와 독립적이다.

timeout 후 채널은 닫힌 상태로 유지된다. 구조화된 timeout이 도착했다면 typed API에 그 ID/metadata를 보존한다. 응답 없이 외부 watchdog이 만료됐다면 관측하지 못한 원격 ID나 사용량을 채울 수 없다. 기존 text API는 timeout 예외를 계속 던진다. 원격 작업 취소는 보장하지 않는다.

[실제 검증](../reviews/2026-09-19/signed-provider-claude-1789847025788032000.json): signed 단일 호출에서 모델명과 입력462/출력17 token, CLI list estimate 0.003706 USD가 보존됐다. 이 수치를 이전 computer-use 실험의 비용으로 소급하지 않는다. general planner와 witness의 논리 요청은 아래 collector로 자동 수집한다.


## 작업별 수집

`GeneralResult.provider_calls`에는 planner 및 witness의 논리 모델 요청 metadata가 들어간다. 다른 텍스트 API 호출자는 `capture_provider_calls()` 문맥을 사용할 수 있다. 프롬프트와 답변 본문은 이 목록에 저장하지 않는다. 중첩 문맥은 각각 복사본을 받으며 종료 후 복구된다. 요청이 실패해도 metadata를 수집하며, 호출 전에 기한이 만료되면 실제 요청이 없으므로 기록하지 않는다.

목록은 논리 요청 단위다. 내부 fallback의 각 실패 시도나 원격의 모든 추론을 각각 기록하지 않으므로 목록 길이를 backend 총 호출 수로, cost 합을 모든 시도 청구액으로 표현하지 않는다. 미관측 항목은 null이다. Claude input_tokens는 CLI의 uncached input 원시 값으로 유지한다. 아래 필드로 전체 입력량과 구분한다.

[실제 general 수집](../reviews/2026-09-19/native-coordinate-model-20260920-telemetry-a/report.json): 클릭0→1과 새값읽기에 성공했고 판단3회/서로 다른 요청ID3개를 수집했다. 추가 계측용 모델 호출은 없다. 과거 실험의 null metadata를 소급해 채우지 않는다.


## 입력 토큰 의미

- `input_tokens`: 기존 provider 원시 값이다. 값을 바꾸어 과거 기록과의 의미를 섞지 않는다.
- `input_tokens_basis`: Claude는 `uncached`, Codex adapter는 `total`이다. 미관측/legacy는 null이다.
- `cache_creation_input_tokens`: Claude의 cache_creation_input_tokens, Codex의 cache_write_input_tokens를 보존한다. 없는 필드는 null이다.
- `total_input_tokens`: Claude는 일반 입력+캐시 읽기+캐시 생성 모두 관측됐을 때만 합산한다. 하나라도 빠지면 null이다. Codex는 수신한 input_tokens를 사용하며 cached_input_tokens를 다시 더하지 않는다.

Claude 계산은 [공식 prompt caching 문서](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)의 정의를 따른다. Codex의 원시 usage 필드는 [공식 SDK](https://github.com/openai/codex/blob/main/sdk/typescript/src/events.ts)에 정의돼 있다. 이 합계는 관측된 논리 요청의 입력량이며 청구 비용 계산식이나 fallback 전체 사용량을 대신하지 않는다.

[실제 signed 검증](../reviews/2026-09-19/signed-provider-claude-1789847596144085000.json): 462+0+0=462, 출력17. 과거 cache-creation 누락 기록의 total을 소급 계산하지 않는다.
