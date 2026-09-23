# 평가 실행과 결과 읽기

실행 전에 범위를 확인한다. `--preview`는 GUI 가용성 검사, 앱 실행,
시나리오 실행, 모델 호출을 하지 않는다. 따라서 `planned`는 실행 후보라는
뜻이며 현재 화면에서 실행 가능하다는 보장은 아니다.

```bash
.venv/bin/python examples/eval.py --offline --preview
.venv/bin/python examples/eval.py --offline --report /tmp/nlc-offline.json
./launcher/nlc eval background_menu_refusal --report /tmp/nlc-menu.json
```

마지막 명령은 소유한 배경 테스트 앱에서 메뉴 동작과 거절을 확인한다.
전체 GUI 평가는 사용자 앱에 대화상자를 열 수 있으므로 작업 중에 실행하지
않는다. 기본 실행은 `foreground` 요구 시나리오를 제외하며,
`--foreground`를 명시한 경우에만 앱을 앞으로 가져오는 경로가 허용된다.

위치 인자는 시나리오 이름의 부분 문자열이며 여러 개는 OR로 선택한다.
일치하지 않는 인자나 알 수 없는 옵션은 실행 전에 오류가 된다.
`isolated` 시나리오는 기본 전체 실행에서 제외하며, 하나의 이름을 지정해
별도 런처 프로세스로 실행해야 한다. 다른 시나리오와 함께 선택하면 거절한다.

## 결과 형식

`--report PATH`는 UTF-8 JSON을 원자적으로 저장한다. 부모 디렉터리는 미리
존재해야 한다. 파일 권한은 소유자 읽기·쓰기(0600)이며 기존 파일은 교체한다.
오류 메시지에는 앱 정보나 경로가 들어갈 수 있다.

- `schema_version: 1`, `kind: plan | run`, 실행 옵션과 선택된 `cases`를 기록한다.
- 각 case는 고정 `id`, 요구 조건, 격리 여부, `status`, 이유 코드·설명,
  `duration_ms`를 가진다. 실행하지 않은 case의 시간은 `null`이다.
- 실행 중 예외는 `error`에 보존한다. 저장 플로우·helper 메뉴·transport
  검사에서 제공하는 단계별 진단은 `details`에도 기록한다. 아직 모든
  시나리오가 단계별 진단을 제공하는 것은 아니다.
- `summary.pass_rate`는 PASS / (PASS + FAIL)이다. SKIP은 분모에 넣지 않는다.
  판정한 case가 없으면 비율은 `null`이다. `scored_fraction`은 선택 범위 중
  판정한 비율이며, 건너뛴 개수와 함께 봐야 한다.
- 시나리오 시간에는 준비·검증 시간이 포함된다. `scenario_duration_ms`는
  실행된 시나리오 시간의 합이고, `mean_scored_duration_ms`는 PASS·FAIL의
  평균이다. 전체 시간에는 공통 준비도 포함된다. 모델 추론 시간과는 다르다.
- provider 호출 전체를 계측하지 않으므로 `model_usage`와 `model_cost_usd`는
  `null`이다. 재생의 무모델 회귀가 통과했다고 전체 평가 비용을 0으로 쓰지 않는다.

실행 전에 화면이 잠겨 있으면 GUI 시나리오는 SKIP이다. 실행한 시나리오가
실패하고 이후 잠금이 관측되면 FAIL을 유지하고 `environment_after_failure`를
추가한다. 그 관측만으로 실패 원인을 확정하지 않는다. 아직 시작하지 않은
GUI 시나리오만 건너뛴다.

종료 코드는 통과한 판정이 있고 실패가 없으면 **0**, 판정 실패가 있으면 **1**,
전부 SKIP 또는 선택·옵션 오류이면 **2**다. `--preview`도 같은 규칙을 따른다:
계획된 시나리오가 하나라도 있으면 **0**, 선택은 됐지만 전부 걸러져 planned가
0이면 **2**다. 계획이 비어 있는데 초록을 돌려주면 오타난 필터나 잘못된 플래그가
CI를 그대로 통과한다 — 그것이 이 규칙의 목적이다.
인자 오류는 실행 결과 파일을 만들지 않는다.

## Stagehand에서 참고한 점과 비교 범위

[Stagehand evals](https://www.stagehand.dev/evals)는 정확도·속도·비용을 함께
표시한다. [공식 평가 문서](https://github.com/browserbase/stagehand/tree/main/packages/evals)는
실행 전 `--preview`, 과제 선택, 반복 시행, 여러 harness와 모델의 비교를 제공한다.
NLC에는 우선 실행 계획과 구조화된 결과를 적용했다.

NLC의 현재 suite는 안전한 거절·계약·재생 회귀도 PASS로 센다. 따라서 이
통과율은 브라우저 작업 완료율이 아니며 Stagehand 순위와 직접 비교할 수 없다.
시나리오마다 공유 앱·flow store 상태가 남아 있어, 반복 횟수나 병렬 실행을
늘리는 기능은 추가하지 않았다. 비교 실험에는 과제별 독립 초기 상태,
동일한 성공 조건, 모델 사용량 계측이 먼저 필요하다.
