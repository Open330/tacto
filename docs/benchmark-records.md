# 비교 실행 기록

`examples/task_benchmark.py`의 fixture 실행은 NLC의 알려진 page target을 사용하는 로컬 작업 평가다. 실제 앱 routing, arbitrary 저장 witness discovery, 범용 computer-use 성공률은 측정하지 않는다.

실행 결과의 `requested_provider`는 설정값이다. `provider`, `model`, `provider_calls`, `model_usage`는 관측한 값만 사용한다. 계측이 없으면 provider/model은 null이며 토큰·비용을 0으로 채우지 않는다. 공통 집계는 route attempt를 한 번만 더하며 비용의 currency/basis 및 관측 누락 개수를 유지한다.

Codex 제품 UI는 trusted node tool로 별도 실행하고 원시 결과를 보관한다. 외부 결과를 검증해 정규화하려면 다음 명령을 사용한다.

```sh
.venv/bin/python examples/task_benchmark.py --manifest task-manifest.json import-baseline raw-result.json --artifact-root raw-artifacts
```

기록은 기존 결과 필드(`task_id`, `condition_id`, `system`, `run_id`, `status`, `elapsed_seconds`, `provider`, `model`, `evidence`)와 `environment`를 포함한다. `system`은 `codex-computer-use`다. `external_observation`에는 다음 필드를 넣는다.

```json
{
  "transport": "trusted node_repl sky RPC",
  "execution_mode": "fixed-actions",
  "artifacts": [{"path": "run.json", "sha256": "실제 파일 바이트의 SHA256"}]
}
```

`execution_mode`는 `fixed-actions` 또는 `autonomous-model`이다. 성공 결과의 각 evidence 항목은 기존 `oracle`, `passed`, `observed` 외에 `artifact`로 등록된 raw 파일 경로를 참조해야 한다. 조건 hash는 요청·fixture·시간 예산·oracle을 포함하며 manifest와 일치해야 한다. artifact는 root 안의 상대 경로만 허용하고 실제 bytes hash를 다시 확인한다.

이 검사는 파일 무결성과 기록 형식을 확인한다. outcome의 `observed`가 작업 성공을 입증하는지는 operator가 원시 자료와 독립 oracle을 검토해야 한다. 형식상 통과만으로 작업 결과를 재판정하거나 양쪽 시스템의 동등성을 선언하지 않는다. `summarize`는 fixed action·자율 모델·unknown cohort를 분리하고 누락/실패/unavailable 실행을 보존한다. 외부 기록을 제공하지 않으면 baseline은 unavailable이며 실행한 척하지 않는다.

Runner의 기존 `diagnostics.model_calls`는 planner 함수 호출 수다. 계획 검증 후 재질문이 있으면 실제 모델 요청은 더 많을 수 있다. 논리 모델 요청과 route 시도 수는 `provider_calls`와 `model_usage`를 사용한다. 실패 결과의 provider protocol 오류도 작업 실패와 함께 보존하며 성공 실행으로 제외·대체하지 않는다.

## Native 메뉴 자율 비교의 범위

2026-10-01의 `reviews/2026-10-01/run-owned-autonomous-menu.py`는 시험용 실험 adapter를 비교한다. 동일한 로컬 CLI planner·goal·65초 한도·12 screen 한도와 새 NativeMenuFixture를 사용한다. 순수 AX와 공개 Sky API를 각각 click/read로 제한하고, 메뉴 루트 role을 정규화한다. AX는 app menu tree도 읽고, SDK는 반환된 indexed text를 정규화한다. 두 관측의 내용이 같다는 보장은 없으며 production Driver 전체 capability 비교가 아니다. SDK에는 narrow probe가 없어 추가 get_app_state를 사용하며 root RPC 서비스 대기시간도 작업 기한에 포함된다. 성능 동등성이나 private 제품 구현의 증거로 쓰지 않는다.

목표는 Direct Command→Responder Command를 각각 한 번 실행하는 것이다. General의 done/ok와 별도로 독립 native host 카운터가 direct=1, responder=1, make-key=0이고 기록된 command 순서가 정확히 두 항목이어야 과제 성공이다. 전경·pointer·focus 표본 감사와 기능 성공은 따로 판정한다. 과거 adapter 준비 실패/서비스 timeout/provider 거절 기록을 보존하고 같은 source cohort의 반복 결과로 덮어쓰지 않는다. 실제 Codex serving model은 CLI가 제공하지 않아 unknown이다.

이 비교에서 동일 click 재계획이 내용 변경 뒤에도 반복으로 오판되는 결함을 발견했다. General은 click/menu 계획의 반복 검사에서 관측 content fingerprint도 확인한다. 변화 없는 재시도는 계속 중단하며, dispatch 오류 뒤 자동 입력 재시도나 최종 완료 증거는 제공하지 않는다.

2026-10-01 click/read 한정 동일 source-set의 8회는 AX/Sky × Claude/Codex × 2회다. AX는 두 provider 모두 0/2, SDK는 모두 2/2였고, 8회 모두 전경·pointer·focus 표본 감사가 통과했다. 이 결과는 owned fixture의 background responder-chain 차이를 드러내며 native runtime의 해결을 뜻하지 않는다. 기록은 `reviews/2026-10-01/verification/autonomous-menu-comparison.json`에 있고, 준비·provider·서비스 실패를 포함한 전체 23회도 보존했다.


2026-10-04에는 NLC의 production Python `prepare_window`를 후보에 포함하고 AX-only
준비 힌트를 제거한 새 자율 cohort를 실행했다. 두 경로는 같은 목표·65초·12 screen·
새 fixture·독립 counter와 정확한 command 순서 oracle을 사용한다. SDK는 공개
`get_app_state`/`click`만 사용한다. SDK 서비스를 사전 준비한 8회는 NLC Claude2/2,
Codex2/2, SDK Claude1/2, Codex2/2다. SDK Claude 실패는 Direct1 이후 명시 모델
거절로 Responder0이며 FAIL로 유지한다. 이전 서비스 대기 실패 3회와 bootstrap
거절도 별도 cohort로 보존한다. 이를 NLC의 기능 우위나 전체 제품 동등성으로
해석하지 않는다.

원시 경로·SHA256·조건·40 selected sources·provider telemetry·표본 감사는
`reviews/2026-10-04/verification/autonomous-menu-preparation-comparison.json`에 있다.
선택 소스는 전체 dependency closure가 아니며 실제 사용자 caret/동시 typing,
대표 앱·장기 복구를 입증하지 않는다. SDK 서비스 대기가 기한에 포함되고 관측
형식과 adapter overhead가 달라 latency 동등성도 주장하지 않는다. 거절 진단/UX
수정은 GUI trial 이후이며 frozen trial과 후속 회귀 결과를 구분한다.
