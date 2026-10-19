# Signed provider 응답

`ask_model_result`와 `reach_model_result`는 `ProviderResult`를 반환한다. signed 경로는 `ask_provider --result-envelope`의 구조화된 결과를 launcher의 `provider_result_v1` 필드로 전달한다. 기존 `answer.text`와 `ask_launcher` 텍스트 API는 유지한다. metadata를 얻기 위한 두 번째 모델 호출은 하지 않는다.

응답은 로컬 request_id, 요청한 provider와 실제 관측 provider, transport/status/text/error/elapsed_seconds 및 관측된 model·원격 ID·token·cost를 담는다. 미관측 값은 null이다. Tacto request_id를 provider_request_id로 복사하지 않는다. cost_basis가 CLI list estimate이면 실제 청구금액을 뜻하지 않는다.

`ask_launcher_result`는 필드 타입, 음수/비유한 수치, boolean token 수, 상태와 text/error의 모순, 외부 응답과 metadata의 불일치를 거절한다. 구형 launcher의 text-only 응답은 metadata를 합성하지 않는다. 기존 --json 옵션은 모델 답변 형식이며 --result-envelope와 독립적이다.

timeout 후 채널은 닫힌 상태로 유지된다. 구조화된 timeout이 도착했다면 typed API에 그 ID/metadata를 보존한다. 응답 없이 외부 watchdog이 만료됐다면 관측하지 못한 원격 ID나 사용량을 채울 수 없다. 기존 text API는 timeout 예외를 계속 던진다. 원격 작업 취소는 보장하지 않는다.

실제 검증(비공개 로컬 기록: `reviews/2026-09-19/signed-provider-claude-1789847025788032000.json`; 공개 원본 미포함): signed 단일 호출에서 모델명과 입력462/출력17 token, CLI list estimate 0.003706 USD가 보존됐다. 이 수치를 이전 computer-use 실험의 비용으로 소급하지 않는다. general planner와 witness의 논리 요청은 아래 collector로 자동 수집한다.


## 작업별 수집

`GeneralResult.provider_calls`에는 planner 및 witness의 논리 모델 요청 metadata가 들어간다. 다른 텍스트 API 호출자는 `capture_provider_calls()` 문맥을 사용할 수 있다. 프롬프트와 답변 본문은 이 목록에 저장하지 않는다. 중첩 문맥은 각각 복사본을 받으며 종료 후 복구된다. 요청이 실패해도 metadata를 수집하며, 호출 전에 기한이 만료되면 실제 요청이 없으므로 기록하지 않는다.

Paged planner는 유효 JSON이지만 호스트 검증에서 거절된 계획을 한 번 재질문한다. 두 요청은 최초 모델 기한을 공유하고 모두 계측된다. 거절된 답 본문은 새 프롬프트에 복사하지 않는다. Provider 오류, 잘못된 JSON, 이미 실행한 UI 동작을 이 경로에서 재시도하지 않는다. 일반 `ask` 호출자는 `validation_retries=0`이 기본이며 0~2를 명시할 수 있다.

Claude adapter는 호출별 `--settings '{"switchModelsOnFlag":false}'`로 숨은 자동 모델 전환을 끈다. 사용자 settings 파일을 수정하지 않는다. [공식 모델 설정 문서](https://code.claude.com/docs/en/model-config#ask-before-switching)에 따르면 이 설정의 비대화형 호출은 flagged 요청을 거절로 종료한다. 공급자가 요청을 거절해도 UI 동작을 재시도하지 않는다. CLI 2.1.286의 실제 응답에서 거절 후 모델 전환·num_turns=2를 확인했으며, 이를 단일 모델/턴 성공으로 수용하지 않는다. 완전한 동일 세션에 명시 fallback notice가 있으면 `ClaudeProtocolError_model_fallback`, 명시 no-fallback refusal notice는 `ClaudeProtocolError_model_refusal`로 구분한다. 다른 미인식 이벤트는 계속 거절하며 요청/비용 metadata가 미검증이면 null을 유지한다.

Claude 프로토콜 거절은 `ClaudeProtocolError_missing_terminal`, `ClaudeProtocolError_session_mismatch` 등의 닫힌 오류 코드로 구분한다. 원시 응답·stderr를 코드에 넣지 않는다. 기존 기록에 원인이 없으면 이후 코드를 소급해서 채우지 않으며, 계측을 위해 검증 기준을 완화하지 않는다.

목록은 논리 요청 단위다. 각 envelope의 `attempts`는 실제 실행한 로컬 route invocation 순서와 provider·transport·status·elapsed_seconds·안전한 error_code를 담는다. 구조화된 답변이 JSON 검증에 실패해 다음 provider로 넘어가더라도 그 시도의 관측 model·usage·cost를 보존한다. 프롬프트·답변·원시 오류는 시도 기록에 없다. legacy launcher 응답에는 시도 목록을 추측해 채우지 않는다.

JSON 검증 실패는 텍스트·구조화 경로 모두 `invalid_response`로 기록한다. 호출자 `KeyboardInterrupt` 등의 중단은 현재와 앞선 시도를 collector에 남기고 그대로 다시 전달한다. 중단을 fallback이나 성공으로 바꾸지 않으며, 이 기록은 원격 취소를 증명하지 않는다.

로컬 route invocation에는 실행 파일 부재나 답변 없음도 포함되므로 원격 backend 호출 수가 아니다. `summarize_provider_calls(records)`는 관측 시도만 합산하고 마지막 성공 envelope를 다시 더하지 않는다. 비용은 currency와 cost_basis별로 분리하고, 토큰·비용 미관측 기록 수 및 시도 상세가 없는 논리 요청 수를 함께 반환한다. `observed_sum`은 모든 시도의 청구액을 뜻하지 않는다. 비용 basis가 없으면 비용 합계에 포함하지 않고 미관측으로 센다. 미관측 항목은 null이다. Claude input_tokens는 CLI의 uncached input 원시 값으로 유지한다. 아래 필드로 전체 입력량과 구분한다.

실제 general 수집(비공개 로컬 기록: `reviews/2026-09-19/native-coordinate-model-20260920-telemetry-a/report.json`; 공개 원본 미포함): 클릭0→1과 새값읽기에 성공했고 판단3회/서로 다른 요청ID3개를 수집했다. 추가 계측용 모델 호출은 없다. 과거 실험의 null metadata를 소급해 채우지 않는다.


## 입력 토큰 의미

- `input_tokens`: 기존 provider 원시 값이다. 값을 바꾸어 과거 기록과의 의미를 섞지 않는다.
- `input_tokens_basis`: Claude는 `uncached`, Codex adapter는 `total`이다. 미관측/legacy는 null이다.
- `cache_creation_input_tokens`: Claude의 cache_creation_input_tokens, Codex의 cache_write_input_tokens를 보존한다. 없는 필드는 null이다.
- `total_input_tokens`: Claude는 일반 입력+캐시 읽기+캐시 생성 모두 관측됐을 때만 합산한다. 하나라도 빠지면 null이다. Codex는 수신한 input_tokens를 사용하며 cached_input_tokens를 다시 더하지 않는다.
- `input_tokens_basis=total`이고 input/cached 값이 모두 관측되면 cached가 input을 초과하는 응답은 공통 envelope와 각 attempt에서 거절한다. 기준이 unknown/uncached이거나 input 값이 없으면 이 포함 관계를 추정하지 않는다.

Claude 계산은 [공식 prompt caching 문서](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)의 정의를 따른다. Codex의 원시 usage 필드는 [공식 SDK](https://github.com/openai/codex/blob/main/sdk/typescript/src/events.ts)에 정의돼 있다. 이 합계는 관측된 논리 요청의 입력량이며 청구 비용 계산식이나 fallback 전체 사용량을 대신하지 않는다.

실제 signed 검증(비공개 로컬 기록: `reviews/2026-09-19/signed-provider-claude-1789847596144085000.json`; 공개 원본 미포함): 462+0+0=462, 출력17. 과거 cache-creation 누락 기록의 total을 소급 계산하지 않는다.

## 로컬 취소 범위

일반 CLI, Claude 및 Codex structured adapter는 timeout뿐 아니라 호출자 KeyboardInterrupt 등 `communicate` 중단 시에도 로컬 프로세스 그룹을 종료하고 부모 프로세스를 회수한다. POSIX 실제 subprocess 테스트에서 호출자 중단 후 자식의 지연 파일 쓰기가 발생하지 않음을 검증한다. `cancellation_supported=false`는 계속 유지한다. 원격 생성 중단·비용 중단은 확인하지 못하며, 별도 세션으로 이탈한 자식까지 프로세스 그룹 종료가 보장하지 않는다.

서명 런처의 바깥 watchdog도 먼저 요청 Python에 SIGTERM을 보내 정리를
요청한다. `ask_provider.main`은 이를 BaseException으로 전달하여 위 CLI
runner의 kill/reap 경로를 실행하고 종료 코드143으로 끝난다. 원래 SIGTERM
handler는 반환·예외 후 복원한다. 런처는 relay thread 밖에서 최대0.75초
유예 뒤 남은 요청 프로세스를 SIGKILL하고 회수한다. 모델 응답 deadline을
이 정리 유예로 늘리지 않는다. 요청 프로세스가 협조하지 않거나 자식이
그룹에서 이탈하면 이 경로도 전체 자식 정리를 보장하지 않는다.
watchdog은 프로세스 실행 전에 cleanup group에 등록하고 실행 실패 시 등록을
해제한다. 런처 shutdown은 이미 등록된 작업을 최대1초 기다린다. 이는 모델
응답 deadline과 별도의 종료 정리 대기다.

`tests/test_provider_outer_cancellation.py`는 실제 런처 소스에서 추출한
watchdog 함수를 Swift로 compile하고 소유한 Python/CLI/자식만 실행한다.
수정 전 timeout·output-limit 뒤 지연 쓰기가 발생했고, 수정 후 일반 CLI·
Codex·Claude runner의6개 경우에서 쓰기 부재와 CLI 부모 회수를 확인했다.
SIGTERM을 무시하는 wrapper의 강제 종료도 확인한다. 이 검사는 live provider
취소·원격 생성·청구를 검증하지 않는다. 설치된 서명 런처의 정상 모델 호출은
별도 cohort에서 확인한다.

Codex의 local CLI 경로도 `codex_result`의 `--json`·`--ephemeral`·명시적인 read-only sandbox를 사용한다. stdin으로 prompt를 전달하며 완전한 단일 turn과 독립 final-message 파일이 일치할 때만 답변을 인정한다. 관측한 token usage와 thread ID를 공통 결과·attempt에 보존하고 실제 모델·API request ID·비용은 미관측이면 null이다. JSONL 형식 오류를 raw-text 성공으로 바꾸지 않는다. Muxa Global Ask 경로는 아래 격리 계약 부재로 로컬 거절하며 같은 provider의 CLI를 사용한다. read-only는 도구 실행이나 네트워크 사용을 차단하는 보장이 아니다. [공식 exec JSONL 계약](https://developers.openai.com/codex/noninteractive#make-output-machine-readable)을 참고한다.

현재 CLI 0.159.2는 thread 시작 후 turn 시작 전에 비종결 진단 `item.completed`를 낼 수 있다. 알려진 error-item 모양만 최대 32개 허용하며 진단 본문은 결과에 복사하지 않는다. turn 내부의 error/failed item, 전체 error/turn.failed, 불완전 turn, 중복 terminal, final 파일 불일치는 여전히 거절한다. 이 호환성 검사는 시작 진단을 일반 작업 성공 증거로 쓰는 것이 아니다.

계획용 Codex adapter는 turn 안에서 `reasoning`과 `agent_message` item만
수용한다. 명령 실행, MCP 호출, 웹 검색, 파일 변경 또는 알려지지 않은 item이
started/updated/completed 어느 단계에서든 관측되면 final 답변이 일치해도
성공으로 인정하지 않는다. 이 검사는 CLI 실행 후의 응답 수용 조건이다.
이미 발생한 도구 부작용을 방지하거나 취소하지 않으며 도구가 없는 실행 환경을
보장하지 않는다. 실패한 응답의 usage를 이 검사로 복원하지도 않는다.
도구 권한은 별도 실행 경계에서 제한해야 한다. 공식 문서는
[기능 설정](https://developers.openai.com/codex/config-basic/#feature-flags)과
shell 기능을 설명하지만 read-only 자체를 도구 실행 금지로 정의하지 않는다.

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

### 조직의 Claude Code 구독 경로 제한

CLI2.1.296에서 순수 JSON 요청도 조직의 Claude Code 구독 사용 제한으로
실패하는 실제 환경을 확인했다. 단일 same-session/tool-free init→합성
assistant API error→is_error=true result의 완전한3개 이벤트와 일치하는
명시적 오류 문구가 있을 때만 `organization_access_disabled`로 분류한다.
성공 답변에서 정책을 설명하는 경우, 다른 세션·불완전/도구 포함 응답·
다른 API 오류에는 이 코드를 부여하지 않는다. strict 성공 parser는 그대로다.

공통 envelope는 마지막 시도의 알려진 unavailable reason을 primary error
code에도 보존한다. 앞선 접근 제한으로 나중 route의 다른 실패나 timeout을
덮지 않는다. 실패 본문을 사용자 문구에 복사하지 않고 닫힌 설명만 제공한다.
그 실패의 text/model/usage/cost는 검증된 모델 턴으로 반환하지 않는다.
사용량 미관측은0이 아니다. API key/profile·조직 설정·모델 routing을 변경하지
않으며, 현재 Claude 구독 경로의 실제 백본/UI 평가는 접근 제약으로 미실행이다.


## 실제 Codex 호출의 선택적 프로세스 관측

공통 `_provider_env()`는 Claude 세션 여부와 관계없이 NLC_HELPER_FD,
NLC_LAUNCHER, NLC_HELPER_TOKEN을 모델 자식 환경에서 제거한다. 부모 환경과
사용자 profile·credential은 유지한다. 이는 채널 표지 제거이며 TCC 책임 분리를
증명하지 않는다.

`codex_result(..., process_observations=records)`에 호스트 소유 list를 넘기면
소유 Popen의 주 PID를 요청 중 관측한다. 진단은 ProviderResult와 분리되며
기본 호출에는 관측 스레드가 없다. 최대64회, 읽기 사이0.2초 간격이므로 긴 요청의
전체 구간을 관측하지 않는다. 요청 종료 시 stop 후 최대0.2초 join하고
`observer_stopped`와 `sample_limit_reached`를 기록한다. native read가 늦게
끝나도 반환 snapshot은 바뀌지 않는다. 오류 본문 대신 닫힌 코드만 남긴다.
주 프로세스 종료가 읽기 중 감지되면 그 sample을 버린다. 후손·PID 재사용·
실행 코드 신원·TCC 판정은 이 진단으로 확정하지 않는다. 경로에는 로컬 홈이
포함될 수 있으므로 외부 공유 시 홈 접두어를 치환해야 한다.

실제 기록(비공개 로컬 기록: `reviews/2026-10-10/live-codex-process-observation.json`; 공개 원본 미포함)은
Codex CLI의 도구 없는 산술 요청이 성공하고 독립 정답 검사가 통과한 결과다.
주 프로세스의 실행 경로와 책임 경로는 Codex CLI였으며31회 관측했고
observer 종료·오류 없음·sample 한도 미도달을 확인했다. 경과6.241초,
입력16139/cache12160/출력9 token은 CLI telemetry다. 실제 모델명·비용은
null로 유지했다. owner-only 보고서의 source hash는 호출 전후 일치했다.
이는 signed comparison에서 실행한 native task나 Sky 관측이 아니며
기존 `comparison_eligible=false`를 변경하지 않는다.

## Muxa Global Ask와 호출 격리

현재 Muxa AskSend/AskSendNew에는 per-call provider 필드가 없고, `--agent`는
공유 데몬의 이후 Ask 선택에도 영향을 준다. AskSend는 활성 대화를 이어 가며
AskSendNew도 활성 대화를 바꾼다. 선택을 읽어 일치할 때 호출하거나, 호출 뒤
복원하는 방식은 다른 클라이언트와의 경쟁을 막지 못한다.

Tacto의 legacy `_try_muxa`는 prompt 전송·subprocess·설정 변경 전에
`muxa_isolated_ask_unavailable`로 로컬 거절한다. 요청한 provider의 CLI fallback을
유지한다. Muxa에만 의존한 설치는 해당 모델 경로를 사용할 수 없으며, Muxa의
격리된 per-call provider/새 대화/구조화된 결과 계약 구현은 아직 남아 있다.
이 제한을 Muxa transport 지원 완료로 계산하지 않는다. peer/advisor 협업은
Muxa message 계약으로 계속 사용한다.

`attempts`는 로컬 route 함수 시도다. 거절 기록을 원격 모델 생성 횟수로 세지
않는다. summary의 `reported_local_route_rejections`는 위 닫힌 거절 코드와
미관측 telemetry를 가진 기록 수를 별도로 표시한다. 모델 요청·비용·원격 취소의
독립 증명은 아니며 다른 route가 원격에 도달했다고 추측하지 않는다. token/cost
미관측 기록은 계속 null/unknown으로 보존하고 임의로0을 넣지 않는다.

이전 Tacto는 `--agent`를 사용했으므로 `muxa ask providers`로 현재 선택을 확인할 수
있다. Tacto는 이전 선택을 추측해 복원하지 않는다. 이 동작이 데몬의 공유 메모리
선택을 바꾸는 것과 디스크 config 파일을 쓰는 것은 다르다.

남은 상류 계약은 **제안**이며 현재 사용 가능한 API가 아니다. 새 요청 종류로
provider를 atomically 고정하고, 기존 selected provider/active conversation/history를
바꾸지 않는 별도 단일 턴을 만들어야 한다. 구형 데몬이 새 필드를 무시해 다른
provider를 쓰지 않도록 지원하지 않는 요청 종류는 명시적으로 거절해야 한다.
수용된 요청의 provider/독립 request ID·상태·관측 usage를 구조화된 durable 결과로
반환하고 configured/requested model과 reported model을 구분해야 한다. Tacto에
연결하기 전 두 클라이언트의 동시 Ask에서 선택/대화가 유지되는지, 다른 provider
응답·기한·중단·usage 누락을 실제 검증해야 한다. 원격 취소는 별도 확인 전에는
지원한다고 표시하지 않는다.


## 수치 범위와 비용 합계 실패

elapsed_seconds와 cost는 문자열·bool로 강제 변환하지 않고, 정확한 int/float 중
float로 표현 가능한 유한한 비음수만 받는다. JSON의 매우 큰 정수에서 발생하는
OverflowError도 검증 실패로 처리한다. 공통 envelope는ValueError, Claude의
완료 비용은ClaudeProtocolError_invalid_cost로 거절하며 원문이나 미검증 답변을
성공으로 받아들이지 않는다. 토큰 필드는 별도로 정확한 비음수 int만 받는다.
임의의 모델별 토큰 상한을 도입하거나 토큰에 이 float 검사를 재사용하지 않는다.

개별 비용이 모두 유한해도 같은 currency/cost_basis의 합계가 표현 범위를
넘을 수 있다. 그 버킷은 observed_sum:null 및
aggregation_error:cost_sum_out_of_range를 반환한다. 뒤의 비용으로 부분 합을
복구하거나 0으로 바꾸지 않는다. 다른 버킷은 그대로 계산한다. 개별 비용이
알려진 경우 unknown_cost_records에 더하지 않으므로, 이 카운터와 합계의
미계산 상태를 별도로 확인해야 한다. 정상 유한 합계의 기존 응답 형식은 유지한다.
이 합계는 CLI가 관측한 telemetry의 집계이며 청구서·원격 취소·실제 가격 증거가 아니다.


## CLI의 미지원 인증 클라이언트 (2026-10-11)

Gemini CLI 실패 경로가 provider=gemini로 호출한 경우, 관측한 인증 오류에서
`Error authenticating: IneligibleTierError:`와
`UNSUPPORTED_CLIENT`가 모두 있을 때만 closed unavailable reason인
`unsupported_client`로 분류한다. 한 표식만 있거나 일반적인 'client unsupported'
언급은 이 분류를 만들지 않는다. 기존 CLI의 nonzero failure 처리에서 사용하는
진단이며, 성공 답변을 이 표식만으로 실패로 바꾸지 않는다. raw account/error
내용은 공통 envelope로 전달하지 않는다. 사용자 메시지는 현재 클라이언트를
모델 서비스가 지원하지 않는다는 closed 설명이다. 자동 설치·로그인·계정 전환·
마이그레이션은 하지 않는다.

실제 Gemini 호출은 모델 답변 전에 이 오류로 종료됐다. JSON 응답·usage·비용은
없었다. Tacto envelope에서도 error/unsupported_client, usage와cost null,
cancellation_supported=False를 보존했다. 이는 해당 설치/계정 조합에서 CLI가
보고한 실패이며 서비스 전체의 정책 또는 다른 계정/클라이언트의 상태를 증명하지
않는다. null을0으로 집계하거나 모델 평가 성공으로 세지 않는다.

다른 provider의 오류 출력에서 Gemini 진단을 인용했다고 같은 인증 상태로
분류하지 않는다. Gemini 전용 표식을 공용 classifier에 무조건 추가했던 초기
구현은 review 후 범위를 제한했다. Claude의 closed reason 메시지 조회에도
unsupported_client 방어 항목을 두어 오류 처리 중 KeyError로 envelope를 잃지
않게 했다. 이는 Claude에서 Gemini 진단을 인증 실패로 추론하는 기능이 아니다.


## OpenCode의 exit0 인증 오류 (2026-10-11)

설치된 OpenCode1.2.22에서 exit0이어도 JSONL event가 error일 수 있었다. 관측한
UnknownError의 data.message는 정확히 `Error: Token refresh failed: 401`이었다.
OpenCode 경로에 한해 이 framed refusal(정수 timestamp, sessionID, error 타입/
이름/정확한 message) 또는 동일한 exact plaintext 오류를 authentication_failed로
분류하고, _try_cli는 exit0에서도 답변으로 반환하지 않는다. 일반적인 model JSON
error object나 오류에 관한 설명은 같은 인증 상태로 추론하지 않는다.
다른 provider 경로에는 이 판정을 적용하지 않는다.

실제 실패 응답을 캡처한 뒤 Tacto에 재생했을 때 error/text:null, usage/cost:null로
보존됐다. 인증·usage를 확인하지 못한 것과 사용량0은 다르다. 이 초기 처리는 관측한
특정 인증 실패만 거부했다. 아래 후속 adapter에서 정상 응답 framing 검증을
추가했으며 실제 정상 모델 실행은 여전히 인증 때문에 미검증이다.
사용자 token/login/plugin 설정을 자동 변경하지 않는다.


## OpenCode의 structured planning adapter

`opencode_result`는 fresh `run --format json --agent NLCPlanner`를 사용하고
prompt를 stdin으로 전달한다. 호출별 임시 cwd/config에서 permission 전체 deny,
plugin 빈 목록, 공유/자동 업데이트/project config/external skills 비활성화를
요청한다. 사용자 auth data는 유지하며 login/token/원래 config를 수정하지 않는다.
사용자 config의 custom provider/model 설정은 임시 config로 복사하지 않으므로
그 설정에 의존하는 백본은 별도 지원·검증이 필요하다.
이 설정은 CLI/plugin에 대한 OS sandbox나 모든 부작용 방지 증명이 아니다.

동일 session/message의 한 step_start, 완료한 text parts, stop reason의
step_finish가 있어야 계획 답변을 인정한다. 다른 session/message, 중복 part,
재시작, 도구/미지원 이벤트, 잘림, 비정상 token/cost 값과 nonzero exit는 거부한다.
framed text가 인증/한도를 설명하는 것은 provider refusal과 구분한다.
[1.2.22 공식 CLI event emission](https://github.com/anomalyco/opencode/blob/v1.2.22/packages/opencode/src/cli/cmd/run.ts),
[step schema](https://github.com/anomalyco/opencode/blob/v1.2.22/packages/opencode/src/session/message-v2.ts)를 기준으로 구현했다.

step-finish의 uncached input/cache read/write/output과 cost를 공통 결과와
attempt에 보존한다. total_input_tokens는 관측한 input+cache read+cache write다.
검증된 finish 뒤 error 또는 length 제한이면 답변은 폐기하되 그 step의 사용량은
실패 attempt에 남긴다. 이를 전체 원격 요청의 최종 사용량으로 승격하지 않는다.
도구 이벤트 등으로 더 일찍 거부하면 후속 step usage는 관측하지 못할 수 있다.

cost_basis는 cli_reported_step_estimate다. JSON stream에 serving model, API
request ID, currency가 없어 null을 유지한다. sessionID는 OpenCode의 local
session이며 원격 provider thread의 증명이 아니다. 통화가 없으므로 통화별
비용 합계에 합치지 않는다. OpenCode 자체가 누락된 SDK usage/가격을0으로
처리할 수 있으므로 관측된 CLI zero는 실제 무료/무사용 증명이 아니다.
[공식 usage 계산](https://github.com/anomalyco/opencode/blob/v1.2.22/packages/opencode/src/session/index.ts)을 참고한다.

로컬 transport는 caller stdin을 이어받지 않고 timeout/interrupt에 기존
process-group 정리를 수행한다. stdout은 수신 후8MiB/1000event 한도로 검증한다.
이것은 communicate의 메모리 수신량 제한이 아니며 remote generation 취소와
과금 중단은 보장하지 않는다. 설치된1.2.22 실측은 authentication_failed였고
정상 serving model/usage/cost 및 실제 UI 과제 성공은 미검증이다.


OpenCode framing 거절 시 availability 분류는 CLI stderr와 정확한 structured
refresh401 error event를 사용한다. assistant text나 unframed stdout의 로그인·
quota 설명은 서비스 거절 근거가 아니다. 따라서 truncated/late-error 응답은
그 답변 내용을 검색해 not_logged_in/usage_limit로 바꾸지 않는다. 독립적으로
유효했던 완료 이벤트의 usage는 계속 보존하며 원시 진단은 envelope에 넣지 않는다.

## 불완전 비용과 입력 토큰 구성요소

`summarize_provider_calls`는 통화와 cost_basis가 모두 있는 비용만 기존 `costs`
버킷으로 합산한다. 금액이 보고됐지만 통화 또는 기준이 없는 관측은 이제
`unaggregated_costs`에 개별로 남긴다. 항목은 logical_request_index/attempt_index
(legacy 단일 결과는 null), provider/transport/status, observed_amount, currency,
basis, missing_fields다. 미확인 통화·기준은 null이며 추정 USD로 채우거나 서로
합산하지 않는다. 명시적으로 보고된0 금액도 남기고, 금액 자체가 없으면 항목을
만들지 않는다. `unknown_cost_records`는 금액·통화·기준 중 하나라도 미확인인
기존 카운터를 유지한다. 이 값은 청구된 요청 횟수가 아니다.

`input_token_components`는 input_tokens_basis(null/total/uncached)별 records 수와
input_tokens/cached_input_tokens/cache_creation_input_tokens의 observed_sum/
unknown_records를 별도로 제공한다. total과 uncached의 input counts를 합치지
않으며 cache 구성요소를 기존 total_input_tokens 합계에 다시 더하지 않는다.
일부만 알려져도 보존하되, missing cache 성분으로 총량을 추측하지 않는다.
실패·timeout·invalid_response attempts와 legacy 결과에도 같은 규칙이다.
최종 logical 결과와 그 attempts는 계속 중복 합산하지 않는다.

`reported_local_route_rejections`는 cache creation count와 input basis를 포함한
모든 telemetry가 없는 닫힌 Muxa rejection만 세도록 수정했다. 거절 code만으로
충돌하는 usage 관측을 no-telemetry local rejection으로 분류하지 않는다. 이것도
remote request/실제 과금/원격 취소의 증명이 아니다.

이전·현재 metadata replay(비공개 로컬 기록: `reviews/provider-partial-summary-before-after-a.json`; 공개 원본 미포함)와
관련112 tests(비공개 로컬 기록: `reviews/provider-partial-regression-a.log`; 공개 원본 미포함)는 실제 로컬 executable
fixture가 error frame 뒤 보존한 input18/output4/cache read6/write2, cost0.001/
통화 null을 공통 summary까지 확인했다. parser fixture이며 실제 OpenCode 서비스
호출이나 청구 금액이 아니다. 최종 suite는7.159s/OK/no skips/process7.284s/
source subset stable(비공개 로컬 기록: `reviews/provider-partial-regression-a.json`; 공개 원본 미포함)다.


### Codex planning working directory

Text and image Codex requests start in a fresh private temporary directory.
Both CLI `--cd` and the subprocess `cwd` point there; `--skip-git-repo-check`
permits that non-repository root. The prompt stays on stdin, the independently
exported final-message file stays private, and cleanup runs after the child is
reaped. Text requests retain user model/auth/provider configuration; the existing
image branch retains its `--ignore-user-config` setting. Requested model,
read-only sandbox, ephemeral session and structured-answer acceptance are unchanged.

This changes the inherited working directory; it does not enforce a provider
context ceiling, disable tools or authenticate which instructions were loaded.
Global configuration, ancestor instructions, skills and hidden framing may still
contribute input. The actual serving model/tokenizer and invoice remain unknown
when the CLI does not expose them.


Local POSIX provider runners now clean their fresh owned session on normal
completion as well as timeout/interrupt/output-limit failure. They retain the
leader's waitable exit status until group cleanup, then reap it, preserving the
CLI's exit code. Codex's optional observer uses a non-reaping exit check. This
requires no competing reaper for the owned handle. Detached sessions and remote
generation/billing remain outside the guarantee; cancellation_supported stays
false. Darwin support uses the installed waitid/siginfo_t/libproc ABI and was
verified on the current arm64 Mac; other OS/architecture runtime parity remains
unverified. See the current completion-plan entry and preserved failure logs.


The retained-leader cleanup now requires default SIGCHLD before launch. On
Darwin it also queries native sigaction, without changing handlers, to reject
SA_NOCLDWAIT even when Python's cached handler says SIG_DFL. Immediately before
any group signal, a non-reaping wait must confirm the child is still waitable;
ECHILD or another ownership error forbids that signal and accepting an answer.
`returncode is None` alone is insufficient. All threads, handlers and external
libraries must refrain from reaping owned children, including `waitpid(-1)`, and
from changing signal disposition during this lifetime. The check cannot eliminate
races with arbitrary competing reapers. Other OS native flag parity, detached
sessions, and remote cancellation remain unverified. See the
partial audit(비공개 로컬 기록: `reviews/provider-ownership-guard-partial-audit-a.json`; 공개 원본 미포함).
