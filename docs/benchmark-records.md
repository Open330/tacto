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

## TextEdit 저장 비교의 정확한 바이트 판정

`examples/product_comparison.py`의 `textedit_save_owned`는 새 결과부터
`oracle_contract: textedit-save-exact-utf8-v2`를 기록한다. 파일 바이트와
소유 문서 본문을 요청한 문자열에 정확히 대조하며, 공백·줄바꿈·BOM·본문의
Unicode 정규화를 허용하지 않는다. 경로 표시의 NFC 정규화와 본문 비교는
서로 다른 계약이다. 일반 읽기 과제의 기존 공백 정규화는 별도로 유지한다.

파일 판정은 NLC receipt를 재사용하지 않는 독립 oracle다. 현재 사용자 소유의
일반 파일·단일 링크·4MiB 상한·no-follow/non-blocking open과 읽기 전후
descriptor/path metadata, canonical path, harness directory identity를 확인한다.
본문 미관측은 빈 문자열로 바꾸지 않는다. 이 readback은 관측된 저장 상태이며,
Save가 직접 파일을 썼다는 생산자 인과 증거나 향후 파일 불변성을 제공하지 않는다.

이전 cohort의 oracle는 공백을 정규화했다. 기존 원시 결과를 새 oracle의 성공으로
재분류하거나 덮어쓰지 않는다. 같은 소스·초기 상태에서 양쪽을 새 contract로
다시 실행해야 정확한 저장 성공률을 비교할 수 있다. 새 저장 oracle의 회귀 검사는
임시 파일과 주입된 문서 응답만 사용하며 실제 TextEdit GUI 실행이 아니다.

## 비교 실행의 환경과 종료 상태

Runner는 fixture 준비 전에 화면 잠금/세션 미관측을 확인하며, 이 경우
setup·모델·cleanup을 실행하지 않는다. setup 뒤 환경이 달라져도 모델을 시작하지
않고 소유 fixture를 보존한다. 실행 후 잠금 또는 세션 미관측이 보이면 cleanup
입력을 보내지 않으며 `comparison_eligible: false`로 기록한다.
이미 시도한 작업의 `failed`/`timeout`/`passed` 상태는 유지한다. 환경의 전후
관측은 실패 원인이나 실행 중 잠금이 없었다는 증거가 아니다.

알 수 없는 과제·빈 `--only`·0 이하 reps는 실행 전에 오류다. 새 report는
0600으로 배타적으로 생성한다. CLI는 모든 결과가 eligible PASS이면0,
FAIL/TIMEOUT/INVALID가 있으면1, 그 밖의 미실행·비교 제외가 있으면2로 끝난다.
`--list`는 실행하지 않고0으로 끝난다. 기존 기록에는 새 필드를 소급 추가하지 않는다.

## 외형 설정 읽기 실제 재실행

`reviews/2026-10-10/product-comparison-20261010-155619.jsonl`: NLC1/1·Sky1/1
oracle PASS, 양쪽 Light. NLC42.3초/입력149159/캐시126848/출력458,
Sky53.9초/입력118828/캐시104960/출력366. 이는 원시 usage이며 비용으로
환산하지 않는다. Codex CLI0.161.0을 사용했으며 실제 serving model은 미관측이다.

양쪽 before/after manifest SHA256은
`41ebde9ff344b8f999d1ef193d5d906992e2bc5779db52cf9e7bd33ed20a2c2b`로
동일하다. source_changed=false, process exit0, comparison_eligible=true,
잠금 전후 관측false, activation0건, cleanup은 요청하지 않았다. layer/shortcut
검사는 기록된 호출의 휴리스틱이며 전체 실행 경로의 인증이 아니다.
전경 앱/캐럿/사용자 동시 typing의 동일 초기 상태·보존은 입증하지 않는다.

이 과제는 `legacy-ui-state-v1` 읽기 oracle로 실행했다. 새 정확한 저장 oracle,
Dark/Auto 설정, 반복 성공률, 지연·비용 우위나 전체 제품 동등성의 증거로
확대하지 않는다. 이전 실패 cohort는 보존한다.

## 정확한 TextEdit 저장 상태의 실제 비교

`reviews/2026-10-10/product-comparison-20261010-160030.jsonl`: 신규 private
directory의 소유 `.txt` 문서로 `textedit-save-exact-utf8-v2`를 양쪽1회 실행했다.
NLC1/1·Sky1/1 PASS, 각 행에서 disk_exact=true/window_exact=true이며 자체
문서 cleanup은 completed다. NLC41.2초/입력168253/캐시152704/출력450,
Sky49.5초/입력204135/캐시195968/출력804. 입력/cache를 비용으로 환산하지 않는다.

양쪽 manifest SHA256은
`41ebde9ff344b8f999d1ef193d5d906992e2bc5779db52cf9e7bd33ed20a2c2b`로
동일하다. source_changed=false, process exit0, comparison_eligible=true,
전후 lock=false, TextEdit hidden=false, layer/shortcut 휴리스틱 false다.
파일은0600이다. NLC activation0건, Sky2건(cmuxterm·Blender)이 관측됐으나
이 이벤트를 agent가 유발했는지 사용자의 앱 전환인지 판별하지 못했다.
포커스 침해의 인과 증거나 성능 우위로 사용하지 않는다.

이번 기록은 정확한 최종 저장 상태를 증명하며 명시 Save와 autosave의
생산자 기여를 분리하지 못한다. source-format/첨부 보존, 사용자 caret,
동시 typing, 여러 앱·백본·반복의 완료 증거도 아니다. 이전 저장 실패 기록을
삭제하거나 이번1회 성공으로 안정성 완료를 선언하지 않는다.

이 시행에는 초기 전경 앱 bundle 기록이 없으므로 양쪽 전경 상태가 같았다고
주장하지 않는다. 시행 후 runner는 witness의 `initial_frontmost_bundle`을
보존하도록 보강했다. 이 후속 변경은 위 frozen manifest에 포함되지 않으며
기존 행에 값을 추정해 추가하지 않는다. 이 메타데이터만으로 caret/selection
또는 실행 중 사용자의 입력 수신이 증명되는 것도 아니다.

## Finder 새 폴더 재실행: 실패 유지

`reviews/2026-10-10/product-comparison-20261010-160542.jsonl`은 NLC0/1,
Sky1/1이다. Sky44.9초에 `untitled folder` 한 개가 실제 directory로 확인됐고,
NLC165.7초 뒤 `children=[]`로 실패했다. 양쪽 process exit0이며 CLI도
전체 실패를 exit1로 반환했다. 자기 시험 폴더 cleanup은 양쪽 folder_removed다.

NLC 최종 응답은 New Folder disabled와 창 준비 timeout을 보고했다. 이는 모델의
보고이며 원시 native 실패 코드/phase별 trace를 보존한 진단은 아니다. 따라서
readiness 전체-tree 예산, key/responder 상태 또는 다른 원인 중 하나로 확정하지
않는다. 다음 수정 전에 bounded 준비 진단과 실제 tool 실패 근거를 확보해야 한다.

Full SHA256은 양쪽 before/after 모두
`a2241b5073e3c2a2f7727dfe5fe062f5da95122e2d376b1b1e0a2d2acfa764cf`다.
source_changed=false, comparison_eligible=true, 전후 lock=false다.
이 flag는 환경/소스 검사를 뜻하며 완전한 초기 상태 동등성을 인증하지 않는다.
초기 전경은 Sky `godot#75802`, NLC Finder로 달랐으므로 동일 초기 상태의
성능 비교라고 주장하지 않는다. activation은 Sky3건, NLC1건으로 원인 미확정이다.

NLC 원시 input603060/cache555776/output2139, Sky input205396/cache186624/
output668이다. 반복 준비에 든 실제 요청량을 보존하며 비용으로 환산하지 않는다.
이번 실패를 이전 저장/외형 읽기 성공으로 덮어쓰지 않는다.
