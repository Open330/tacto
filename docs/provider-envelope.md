# Signed provider 응답

`ask_model_result`와 `reach_model_result`는 `ProviderResult`를 반환한다. signed 경로는 `ask_provider --result-envelope`의 구조화된 결과를 launcher의 `provider_result_v1` 필드로 전달한다. 기존 `answer.text`와 `ask_launcher` 텍스트 API는 유지한다. metadata를 얻기 위한 두 번째 모델 호출은 하지 않는다.

응답은 로컬 request_id, 요청한 provider와 실제 관측 provider, transport/status/text/error/elapsed_seconds 및 관측된 model·원격 ID·token·cost를 담는다. 미관측 값은 null이다. NLC request_id를 provider_request_id로 복사하지 않는다. cost_basis가 CLI list estimate이면 실제 청구금액을 뜻하지 않는다.

`ask_launcher_result`는 필드 타입, 음수/비유한 수치, boolean token 수, 상태와 text/error의 모순, 외부 응답과 metadata의 불일치를 거절한다. 구형 launcher의 text-only 응답은 metadata를 합성하지 않는다. 기존 --json 옵션은 모델 답변 형식이며 --result-envelope와 독립적이다.

timeout 후 채널은 닫힌 상태로 유지된다. 구조화된 timeout이 도착했다면 typed API에 그 ID/metadata를 보존한다. 응답 없이 외부 watchdog이 만료됐다면 관측하지 못한 원격 ID나 사용량을 채울 수 없다. 기존 text API는 timeout 예외를 계속 던진다. 원격 작업 취소는 보장하지 않는다.

[실제 검증](../reviews/2026-09-19/signed-provider-claude-1789847025788032000.json): signed 단일 호출에서 모델명과 입력462/출력17 token, CLI list estimate 0.003706 USD가 보존됐다. 이 수치를 이전 computer-use 실험의 비용으로 소급하지 않는다. general planner와 witness의 논리 요청은 아래 collector로 자동 수집한다.


## 작업별 수집

`GeneralResult.provider_calls`에는 planner 및 witness의 논리 모델 요청 metadata가 들어간다. 다른 텍스트 API 호출자는 `capture_provider_calls()` 문맥을 사용할 수 있다. 프롬프트와 답변 본문은 이 목록에 저장하지 않는다. 중첩 문맥은 각각 복사본을 받으며 종료 후 복구된다. 요청이 실패해도 metadata를 수집하며, 호출 전에 기한이 만료되면 실제 요청이 없으므로 기록하지 않는다.

Paged planner는 유효 JSON이지만 호스트 검증에서 거절된 계획을 한 번 재질문한다. 두 요청은 최초 모델 기한을 공유하고 모두 계측된다. 거절된 답 본문은 새 프롬프트에 복사하지 않는다. Provider 오류, 잘못된 JSON, 이미 실행한 UI 동작을 이 경로에서 재시도하지 않는다. 일반 `ask` 호출자는 `validation_retries=0`이 기본이며 0~2를 명시할 수 있다.

Claude adapter는 호출별 `--settings '{"switchModelsOnFlag":false}'`로 숨은 자동 모델 전환을 끈다. 사용자 settings 파일을 수정하지 않는다. [공식 모델 설정 문서](https://code.claude.com/docs/en/model-config#ask-before-switching)에 따르면 이 설정의 비대화형 호출은 flagged 요청을 거절로 종료한다. 공급자가 요청을 거절해도 UI 동작을 재시도하지 않는다. CLI 2.1.286의 실제 응답에서 거절 후 모델 전환·num_turns=2를 확인했으며, 이를 단일 모델/턴 성공으로 수용하지 않는다. 완전한 동일 세션에 명시 fallback notice가 있으면 `ClaudeProtocolError_model_fallback`, 명시 no-fallback refusal notice는 `ClaudeProtocolError_model_refusal`로 구분한다. 다른 미인식 이벤트는 계속 거절하며 요청/비용 metadata가 미검증이면 null을 유지한다.

Claude 프로토콜 거절은 `ClaudeProtocolError_missing_terminal`, `ClaudeProtocolError_session_mismatch` 등의 닫힌 오류 코드로 구분한다. 원시 응답·stderr를 코드에 넣지 않는다. 기존 기록에 원인이 없으면 이후 코드를 소급해서 채우지 않으며, 계측을 위해 검증 기준을 완화하지 않는다.

목록은 논리 요청 단위다. 각 envelope의 `attempts`는 실제 실행한 로컬 route invocation 순서와 provider·transport·status·elapsed_seconds·안전한 error_code를 담는다. 구조화된 답변이 JSON 검증에 실패해 다음 provider로 넘어가더라도 그 시도의 관측 model·usage·cost를 보존한다. 프롬프트·답변·원시 오류는 시도 기록에 없다. legacy launcher 응답에는 시도 목록을 추측해 채우지 않는다.

JSON 검증 실패는 텍스트·구조화 경로 모두 `invalid_response`로 기록한다. 호출자 `KeyboardInterrupt` 등의 중단은 현재와 앞선 시도를 collector에 남기고 그대로 다시 전달한다. 중단을 fallback이나 성공으로 바꾸지 않으며, 이 기록은 원격 취소를 증명하지 않는다.

로컬 route invocation에는 실행 파일 부재나 답변 없음도 포함되므로 원격 backend 호출 수가 아니다. `summarize_provider_calls(records)`는 관측 시도만 합산하고 마지막 성공 envelope를 다시 더하지 않는다. 비용은 currency와 cost_basis별로 분리하고, 토큰·비용 미관측 기록 수 및 시도 상세가 없는 논리 요청 수를 함께 반환한다. `observed_sum`은 모든 시도의 청구액을 뜻하지 않는다. 비용 basis가 없으면 비용 합계에 포함하지 않고 미관측으로 센다. 미관측 항목은 null이다. Claude input_tokens는 CLI의 uncached input 원시 값으로 유지한다. 아래 필드로 전체 입력량과 구분한다.

[실제 general 수집](../reviews/2026-09-19/native-coordinate-model-20260920-telemetry-a/report.json): 클릭0→1과 새값읽기에 성공했고 판단3회/서로 다른 요청ID3개를 수집했다. 추가 계측용 모델 호출은 없다. 과거 실험의 null metadata를 소급해 채우지 않는다.


## 입력 토큰 의미

- `input_tokens`: 기존 provider 원시 값이다. 값을 바꾸어 과거 기록과의 의미를 섞지 않는다.
- `input_tokens_basis`: Claude는 `uncached`, Codex adapter는 `total`이다. 미관측/legacy는 null이다.
- `cache_creation_input_tokens`: Claude의 cache_creation_input_tokens, Codex의 cache_write_input_tokens를 보존한다. 없는 필드는 null이다.
- `total_input_tokens`: Claude는 일반 입력+캐시 읽기+캐시 생성 모두 관측됐을 때만 합산한다. 하나라도 빠지면 null이다. Codex는 수신한 input_tokens를 사용하며 cached_input_tokens를 다시 더하지 않는다.

Claude 계산은 [공식 prompt caching 문서](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)의 정의를 따른다. Codex의 원시 usage 필드는 [공식 SDK](https://github.com/openai/codex/blob/main/sdk/typescript/src/events.ts)에 정의돼 있다. 이 합계는 관측된 논리 요청의 입력량이며 청구 비용 계산식이나 fallback 전체 사용량을 대신하지 않는다.

[실제 signed 검증](../reviews/2026-09-19/signed-provider-claude-1789847596144085000.json): 462+0+0=462, 출력17. 과거 cache-creation 누락 기록의 total을 소급 계산하지 않는다.

## 로컬 취소 범위

일반 CLI, Claude 및 Codex structured adapter는 timeout뿐 아니라 호출자 KeyboardInterrupt 등 `communicate` 중단 시에도 로컬 프로세스 그룹을 종료하고 부모 프로세스를 회수한다. POSIX 실제 subprocess 테스트에서 호출자 중단 후 자식의 지연 파일 쓰기가 발생하지 않음을 검증한다. `cancellation_supported=false`는 계속 유지한다. 원격 생성 중단·비용 중단은 확인하지 못하며, 별도 세션으로 이탈한 자식까지 프로세스 그룹 종료가 보장하지 않는다.

Codex의 local CLI 경로도 `codex_result`의 `--json`·`--ephemeral`·명시적인 read-only sandbox를 사용한다. stdin으로 prompt를 전달하며 완전한 단일 turn과 독립 final-message 파일이 일치할 때만 답변을 인정한다. 관측한 token usage와 thread ID를 공통 결과·attempt에 보존하고 실제 모델·API request ID·비용은 미관측이면 null이다. JSONL 형식 오류를 raw-text 성공으로 바꾸지 않는다. muxa-first 순서는 유지하며 그 경로의 미관측 usage도 null이다. read-only는 도구 실행이나 네트워크 사용을 차단하는 보장이 아니다. [공식 exec JSONL 계약](https://developers.openai.com/codex/noninteractive#make-output-machine-readable)을 참고한다.

현재 CLI 0.159.2는 thread 시작 후 turn 시작 전에 비종결 진단 `item.completed`를 낼 수 있다. 알려진 error-item 모양만 최대 32개 허용하며 진단 본문은 결과에 복사하지 않는다. turn 내부의 error/failed item, 전체 error/turn.failed, 불완전 turn, 중복 terminal, final 파일 불일치는 여전히 거절한다. 이 호환성 검사는 시작 진단을 일반 작업 성공 증거로 쓰는 것이 아니다.

`tests/test_provider_attempts.py`는 거절된 유료 답변의 fallback usage 보존, 최종 성공 비용 중복 합산 방지, 미관측 시도와 legacy 구분, signed envelope parser의 시도 타입 검증, 실제 로컬 호출자 중단을 검증한다. 이 테스트에는 실제 provider 요청이나 추가 token 지출이 없다.

CLI 2.1.286은 `init` 전에 빈 `commands_changed` 알림을 보내기도 한다. 관측한 정확한 field set(type/subtype/session_id/uuid/commands), canonical UUID, 빈 commands 배열을 가진 알림을 같은 세션에서 최대 한 번 허용한다. init 없는 응답, 다른 세션, assistant 선행, 중복 알림, 추가 필드/명령, terminal 뒤 알림은 계속 거절한다. 그 알림만으로 model·usage·완료를 판정하지 않는다.

허용되지 않은 init 이전 이벤트는 `ClaudeProtocolError_before_init`로 거절하며, 오류 메시지에는 닫힌 이벤트 종류만 기록한다. 원시 이벤트와 본문은 저장하지 않는다. 이 진단을 추가하기 전의 실패 기록에는 종류를 소급해 채우지 않는다.

CLI 2.1.289의 합성 이미지 연속 요청에서 init 전에 content-free `system/ui_invalidate`
알림이 오는 것을 재현했다. 정확한 type/subtype/event/session_id/uuid field set,
`event="ui.render"`, 같은 세션과 canonical UUID를 가진 알림을 init 전에 한 번만
허용한다. instances·본문·추가 필드·중복·다른 세션, init 없이 끝난 응답과 init 이후
동일 알림은 거절한다. 이 알림을 UI 명령이나 모델 답변으로 해석하지 않으며 이후
tool-free init, 단일 모델/턴, 완전한 terminal usage·답변 일치를 계속 요구한다.
이전 실패의 원시 이벤트가 없다면 같은 원인이라고 소급해서 확정하지 않는다.

CLI가 nonzero로 끝나거나 informational 이벤트를 먼저 보낸 뒤 명시적으로
`model_refusal_no_fallback`을 알리는 경우도 있다. 완전한 동일 세션·tool-free 단일 init,
유효한 시작 알림·canonical notice identity·제한된 original_model·오류 terminal이
확인되면 실패를 `ClaudeProtocolError_model_refusal`로 분류한다. CLI가 success subtype과
is_error=true/num_turns=2를 함께 보내도 성공으로 수용하지 않는다. 본문·model·usage는
검증된 성공 값으로 반환하지 않으며 추가 UI 입력이나 자동 재시도를 하지 않는다.
General은 최신 요청의 닫힌 실패 코드로 요청 거절·로그인·사용량 한도를 알려준다.
provider 오류 본문을 사용자 메시지에 복사하거나 앞선 실패를 나중 성공의 원인으로
표시하지 않는다.

`PYTHONPATH=. .venv/bin/python examples/probes/claude-stream-framing.py NEW_REPORT.json`
은 합성 PNG 두 건을 별도 fresh CLI session에 보내며 실제 모델 호출을 한다. GUI를
사용하지 않고 닫힌 event 종류와 shape booleans·provider metadata만 owner-only 새
파일에 기록한다. 원시 응답/이미지/프롬프트/답변은 저장하지 않는다. 최대4 requests,
총120초 budget과 기존 CLI process-group cleanup을 사용하며 기존 결과 파일은
덮어쓰지 않는다. 성공은 실제 앱 입력이나 사용자 타이핑 보존의 증거가 아니다.
