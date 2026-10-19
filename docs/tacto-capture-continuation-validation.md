# Capture를 보존하는 자동 재개 — 2026-10-18

이 문서는 당시298개 회귀 cohort의 기록이다. 이후 bound export 거절·UI label 거절은
[원문 보존과 schema4 저장](tacto-literal-learning-validation.md)으로 대체했다.

개발 checkout의 자동 선택자 수리가 앞 단계에서 읽은 capture를 보존한다.
실패 단계의 입력값과 결과 조건은 원래 값을 사용하고, 성공한 prefix는 실행하지 않는다.
설치된 Preview2 및 이전에 빌드한 Development.app의 기능을 검증한 결과가 아니다.

## 변경

- 실행 직전 선택자를 찾지 못한 경우에만 private 메모리 snapshot을 만든다.
  자동 재개용 callback 경로가 요청하며 일반 replay는 snapshot을 보관하지 않는다.
- snapshot은 capture, 보호할 값과 이름, 직전 단계에서 실제 검증한 checkpoint,
  실패 단계의 실제 렌더된 결과 조건을 보존한다. capture를 덮어쓴 뒤 checkpoint를
  새 값으로 잘못 다시 렌더하지 않는다.
- suffix에서 값을 새로 읽으면 누적해 다음 재개로 넘긴다. 민감한 이름에 새 값을
  읽어도 민감정보 표시를 유지한다. 모델용 후보 페이지는 바이트 예산 계산 전에
  가리고, 민감한 값이 identity에 포함된 후보를 learned selector로 저장하지 않는다.
- snapshot은 직렬화를 거절하고 종료·예외 시 참조를 해제한다. 보안 메모리 삭제를
  보장하는 것은 아니며 driver가 원래 보유하는 보호 값의 수명까지 변경하지 않는다.
- 필요한 runtime 값이 없으면 `runtime_value_missing`/`effect=not_started`로
  해당 단계의 입력 전에 중단한다. 건너뛴 capture의 `{{name}}` 원문을 입력하지 않는다.
  반대로 읽은 데이터 자체에 포함된 `{{...}}`는 명령으로 다시 해석하지 않는다.
- 최종 리뷰에서 parameter 값의 이중 렌더를 재현했다. bound 문자열에 literal과
  authored runtime reference의 provenance를 보존해, parameter·table 값에 든
  `{{...}}`는 재해석하지 않는다. runtime table key도 실행 시 조회한다.
  deepcopy·재bind에서도 구분을 유지한다. JSON은 이 private 구분을 표현하지
  못하므로, 의미가 달라질 bound export는 원본 template 저장을 안내하며 거절한다.
  원본 template의 저장·재로드는 원문 의미를 유지한다.
- 자동 수리가 고른 UI label에 `{{`/`}}`가 있으면
  `template_syntax_in_ui_value`로 bind·학습·입력 전에 거절한다. 현재 schema에
  literal selector escape가 없어 해당 label의 자동 수리는 지원하지 않는다.
- 피어가 발견한 일반 decide 분기의 별도 유출 경로도 수정했다. 하위 replay로
  보호 값·이름을 전달하고 부모 로그를 가리며, 분기 모델 입력에서 민감한 이름을
  제외한다. 다른 이름으로 읽은 alias 값에 보호 값이 들어 있어도 제외한다.
  보호 값이 든 질문은 모델 호출 전에 거절하고 narration도 가린다.

## 검증 증거

`reviews/tacto-capture-continuation-20261018/`에 명령 로그와 파일 해시를 보존한다.

| 검증 | 결과 | 범위 |
|---|---|---|
| 최종 선택 회귀 | 298 tests 실행, 통과 | capture/수리/paging/provider mock/일반 replay/분기/batch/session/복구/스킬 설치 |
| capture 집중 회귀 | 18 tests 실행, 통과 | capture-reviewed.log; 브라우저 검사는 최종 선택 회귀에 포함 |
| 실제 headless Chromium | 12단계, 2회 선택자 수리 성공 | 로컬 합성 폼, 고정 chooser, 실제 DOM 값 별도 확인 |
| 스킬 구조 validator | 통과 | 임시 offline uv 환경의 PyYAML 사용; 실제 판단 품질 증명이 아님 |
| 네이티브 GUI | 미실행 | 재확인 시 `screen_locked=true`; 입력·잠금 해제 시도 없음 |

핵심 테스트는 `tests/test_auto_repair_captures.py`와
`tests/test_auto_repair_capture_browser.py`다. 민감값의 실제 입력과
stdout·로그·모델 패킷·learned Flow의 비노출, 두 번 수리와 capture 덮어쓰기,
미보존 snapshot 거절, 누락 capture 차단, 원문 template 기호 보존,
분기 보호 전파와 모델 질문 거절을 확인한다. Chromium 테스트는 두 입력란을
각각 한 번만 입력했는지 확인한다. 실제 모델 호출이나 외부 서비스 연동은 없다.

초기 회귀 명령에는 존재하지 않는 테스트 모듈 이름이 포함되어 ImportError가
한 건 발생했다. `regression.log`를 보존하고 올바른 모듈 목록으로 재실행했다.
추가한 UI label 거절 테스트의 최초 단언은 원본 템플릿에 있던 참조까지 금지해
실패했다. 거절 후 원본 recipe 전체가 그대로인지 확인하도록 바로잡았고,
최종 `final-confirmed.log`는298개가27.484초에 통과했다. 앞 실패 로그는 보존한다.
validator의 첫 실행은 개발 venv에 PyYAML이 없어 실패했다. 소스 의존성을
변경하지 않고 cached offline uv 환경에서 validator를 실행해 통과했다.

## 남은 범위

capture를 참조하는 selector 자체와 매개변수 selector, 동적 단계/decide/
`on_uncertain`, 첫 단계 실패 또는 검증한 직전 checkpoint가 없는 경우는 자동
선택자 수리에서 제외한다. 영속 쓰기·전송에 새 identity로 권한을 이전하지 않는다.
프로세스 재시작·journal·trace·대화형 승인은 이 경로에 결합하지 않는다.
offline `repair --remaining`은 capture를 파일로 운반하지 않는다.

이번 수정의 실제 Codex·Claude 모델 선택, 네이티브 장기 작업, 단절/재시작,
일반 앱의 저장·전송 인과 증거, Sky 동일 조건 비교는 미검증이다.
`promote` 등 다른 UI→template 학습 경로의 literal 경계는 후속 감사 대상이며,
이번 자동 수리 수정으로 모든 학습 경로의 주입 문제가 해결됐다고 주장하지 않는다.
전체 G0~G10 완료로 계산하지 않는다. [전체 계획](completion-plan.md)을 유지한다.

## 피어 검토

Muxa Claude peer가 설계 검토 후 분기 유출, 매개변수 이중 렌더,
UI label의 템플릿 승격을 차례로 지적했다. 각각 재현·수정·회귀를 추가했다.
최종 요청 `req_18df9c5e1781bc80_1c`는 자동 수리의 차단 위치를 확인하고,
테스트 단언 수정 조건으로 이번 capture continuation 범위를 승인했다.
해당 단언을 원본 recipe와의 동등성으로 수정한 뒤 최종298개 검사를 통과했다.
전체 제품 출시 승인이나 promote 경로의 보안 승인으로 해석하지 않는다.
