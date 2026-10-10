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

현재 비교 실행기는 `nlc.session.lock_state`의 잠금·on-console·login-done
지표를 사용한다. 이 플랫폼에서는 로그인한 콘솔의 잠금 key가 생략될 수 있어,
key 부재에 console=true와 login_done=true가 함께 있을 때만 unlocked로
해석한다. 이 해석은 best-effort platform inference이며 앱별 입력 승인이나
원자적 잠금 확인이 아니다. 실제 key가 malformed이거나 세션/지표가 없고,
off-console·로그인 미완료인 경우는 GUI 준비·모델·cleanup 전에 중단한다.
명시적으로 잠긴 경우도 중단한다. Apple의
[console-user 상태 구현](https://github.com/apple-oss-distributions/xnu/blob/main/iokit/Kernel/IOService.cpp)의
positive console/login 지표와 private lock 비교, 현재 플랫폼의 읽기 전용
CGSession/IOConsole 지표 및 peer의 자체 signed fixture 실측을 참고했다.
동일한 Codex CLI를 호출했다는 사실만으로 실제 serving model·reasoning·
계정 상태·앱 초기 상태가 같다고 주장하지 않는다.

### 기존 기록의 비교 전제 감사

`examples/audit_product_pairs.py`는 GUI나 모델 호출 없이 기존 JSONL을 읽는다.
task/rep마다 각 arm이 정확히 한 번 있어야 하며, 잠금 관측·안정된 source
manifest의 실제 digest·oracle·layer 준수·초기 전경을 확인한다. 성공·실패
과제 결과는 그대로 보존하고, 중복 중 성공 행만 골라 짝짓지 않는다.

`recorded_prerequisites_match`는 기록된 전제 부분집합의 일치만 뜻한다.
이 감사는 독립적인 초기 task state와 실제 serving model/configuration을
추가로 요구하며 `same_conditions_verified=false`를 유지한다. 성공률이나
성능 비교 수치를 계산하지 않고 입력 원본과 SHA256을 새0600 보고서에 기록한다.
기존 JSONL에는 새 필드를 소급 추가하지 않는다.

```sh
PYTHONPATH=. .venv/bin/python examples/audit_product_pairs.py \
  reviews/2026-10-10/product-comparison-20261010-160542.jsonl \
  --output /tmp/nlc-pair-audit.json
```

2026-10-10의 네 cohort를 감사한 결과는
`reviews/2026-10-10/product-pair-prerequisites-audit.json`에 있다.
appearance/save cohort는 초기 전경이 행에 없었고, Finder 두-arm cohort는
초기 전경이 달랐으며, Finder 후속은 NLC 한쪽만 실행했다. 원래 task oracle
결과는 보존하지만 이들 기록만으로 동일 조건 비교가 증명됐다고 선언하지 않는다.

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

### signed 비교 host와 모델 프로세스의 경계

새 native 비교 host는 `nlc eval --product-comparison`으로 signed launcher 아래
실행한다. `_driver`는 inherited helper만 사용하며 Python Driver로 대체하지
않는다. `--check-backend`는 helper의 앱 metadata와 capability만 읽고, task
setup·모델·입력을 실행하지 않는다. `--list`는 경로와 무관하게 목록만 표시한다.

```sh
nlc eval --product-comparison --check-backend
nlc eval --product-comparison --list
```

Calculator setup의 menu/clear가 error·unknown·미지원이면 후속 입력이나 모델을
진행하지 않는다. 아직 직접 AXPress를 사용하는 owned TextEdit cleanup 과제는
signed window/document binding을 구현할 때까지 setup 전에 not_run으로 남긴다.
사용자 문서나 출처가 불명인 창을 닫는 대체 경로를 쓰지 않는다.

모델 자식에서는 NLC_HELPER_FD·NLC_LAUNCHER·NLC_HELPER_TOKEN을 제거한다.
이 환경 정리가 macOS TCC responsible identity를 분리하는 것은 아니다.
현재 signed host에서 spawn된 Codex/Sky의 responsible process는 관측하지
않았으므로 결과의 `agent_responsible_process_observed`는 null이다. task
oracle 결과는 보존하지만 `comparison_eligible=false`와
`agent_responsibility_not_isolated_or_observed`로 제품 비교에서 제외한다.
외부 agent controller 또는 실제 책임 신원 관측과 동일 조건 감사를 구현해야
새 native 제품 비교를 증명할 수 있다. 예전 launcher 밖 cohort와 섞지 않는다.

### 책임 프로세스의 읽기 전용 관측

`nlc.process_responsibility.observe_responsibility(pid)`는 현재 플랫폼의 private
libSystem responsibility SPI와 libproc의 실행 경로를 두 번 읽는다. PID·경로가
달라지거나 API/경로가 없으면 닫힌 unavailable/null 결과를 반환한다.
PPID를 대신 쓰거나 관리자 권한을 요청하지 않으며 argv·환경·문서 내용은
읽지 않는다. ABI는 Chromium의
[macOS process 구현](https://github.com/chromium/chromium/blob/main/base/process/process_info_mac.mm)의
pid_t 반환/인자를 참고했다. private API이므로 플랫폼에 따라 제공되지 않을 수 있다.

```sh
nlc eval --product-comparison --check-responsibility
```

이 명령은 controller와 자체 대기 중인 Python 자식 하나만 관측하고 자식을
종료·회수한다. GUI·모델·원격 요청은 없다. 실제 signed 경로의 두 프로세스는
각각 self-responsible PID와 Python 실행 경로를 반환했다. 기록은
`reviews/2026-10-10/signed-controller-responsibility-probe.json`에 있다.
이는 실제 Codex/Sky의 책임 그래프나 TCC 서비스의 권한 결정이 아니며
comparison_eligible=false를 유지한다. 프로세스 경로의 반복 일치로 실행 중
코드 신원·PID 재사용·후손 책임·동일 모델을 증명하지 않는다.

이 Python probe를 근거로 '실제 model이 NLC launcher 책임을 상속한다'거나
'실제 model이 Python 귀속이다'라고 단정하지 않는다. 이전 launcher 상속
설명은 가능한 혼입에 대한 가설이었으며 실제 model/Sky 관측은 아직 없다.
probe의 start_new_session=True는 소유 자식의 세션/프로세스 그룹 설정으로
기록하며 TCC 책임 분리로 해석하지 않는다. 자식 정리 timeout은 kill/wait 후
owned_child_cleanup_timeout과 exit2이며 일반 실패의 예외 본문은 숨긴다.


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

[실제 기록](../reviews/2026-10-10/live-codex-process-observation.json)은
Codex CLI의 도구 없는 산술 요청이 성공하고 독립 정답 검사가 통과한 결과다.
주 프로세스의 실행 경로와 책임 경로는 Codex CLI였으며31회 관측했고
observer 종료·오류 없음·sample 한도 미도달을 확인했다. 경과6.241초,
입력16139/cache12160/출력9 token은 CLI telemetry다. 실제 모델명·비용은
null로 유지했다. owner-only 보고서의 source hash는 호출 전후 일치했다.
이는 signed comparison에서 실행한 native task나 Sky 관측이 아니며
기존 `comparison_eligible=false`를 변경하지 않는다.


## G9 후속: signed 비교의 실제 모델 주 프로세스 관측

두 arm은 공통 `_run_agent`에서 owned process group을 시작하고 timeout·호출자
중단 시 kill/wait한다. prompt는 stdin이며 실제 tool event를 planning-only
ProviderResult로 수용하지 않는다. 주 PID의 선택적 diagnostics를
`agent_primary_process_diagnostics`로 기록하고 기존 null 책임 그래프 및
comparison 제외를 유지한다. 주 PID sample로 후손/Sky/TCC 권한을 추론하지 않는다.
실행 파일 부재는 닫힌 agent_launch_unavailable/not_run으로 보존하며 oracle을
읽지 않는다. 이미 발생한 setup을 취소했다고 주장하지 않는다.

signed launcher는 PATH를 고정하므로 default codex 경로가 실제 미발견이었다.
실패 기록은 `../reviews/2026-10-10/signed-agent-probe-path-unavailable.json`이다.
`--agent-executable ABSOLUTE_PATH`는 평가 호스트가 선택한 실행 파일을 두 arm에
공통 적용하며 PATH/권한/profile을 바꾸지 않는다. 경로 존재·실행 가능 여부가
코드 서명이나 실제 모델 신원 증거는 아니다.

```sh
nlc eval --product-comparison --agent-executable /absolute/path/to/codex --check-agent-responsibility
```

이 명령은 실제 모델 tokens를 쓰는 tool-free 산술 probe다. native 앱 입력이나
Sky 과제는 없다. owner-only 새 report
`../reviews/2026-10-10/signed-live-codex-process-observation.json`에서 oracle PASS,
exit0, source 전후 일치,32 samples/observer stopped/error 없음, Codex 주 PID와
책임 PID 일치를 관측했다. elapsed6.523초이며 실제 model/cost는 null이다.
이는 signed host의 실제 Codex 주 프로세스 관측이고 Sky/후손 책임 그래프나
TCC 판정이 아니다. 전체 G0~G10, 동일 초기 상태/serving model 감사, 대표 앱
반복과 설치 검증을 완료했다고 계산하지 않는다.

후속 Muxa read-only 리뷰 req_18dd258a65ab9a68_5c는 P1/P2 없음이었다.
실측 probe 당시 CLI의 cwd는 controller 저장소 경로를 상속했다. 이후 기존
비교 조건을 유지하도록 transport의 cwd를 /tmp로 명시했다. 두 arm과 probe는
요청 실행 경로·해석 경로·cwd·new-session을 기록한다. 앞선 실측 report는
그 후속 변경 이전 source manifest이며 새 상태 실측이라고 소급하지 않는다.
new session은 POSIX 그룹 정리 범위이며 TCC/asid 분리 증거가 아니다.


## G8/G9 실제 외관 모드 읽기 pair와 정책 구분

2026-10-10 signed doctor는 AX/화면 권한과8개 앱을 확인했다. Calculator는
미실행이었으며 System Settings 창은 관측됐다. 기존 installed signed launcher,
명시적인 Codex 실행 경로, 두 arm 공통120초 timeout으로 appearance_mode를
실행했다. 원시 owner-only 기록은
`../reviews/2026-10-10/product-comparison-20261010-195603.jsonl`이다.
두 arm 모두 독립 defaults 기반 oracle의 Light와 일치하고 process exit0,
source manifest 전후·두 arm 사이 일치, activations[]였다. NLC46.3초,
Sky40.8초다. CLI telemetry 입력/캐시/출력은 NLC214245/194048/611,
Sky159423/150016/620이다. 실제 serving model/비용은 관측하지 않았다.
`calls`는 인식한 tool item event 수이며 고유 invocation 수가 아니다.
NLC shell:nlc12 및 Sky node_repl:js14를 실행 횟수로 해석하지 않는다.

두 arm의 소유 Codex 주 프로세스는64회 observed/self-responsible였으며
observer stopped/error 없음이다. sample_limit_reached=true라 전체 실행
시간 관측은 아니다. Sky 도구 실행 event가 있어도 @oai/sky 후손 책임이나
TCC 판정이 아니다. 첫 foreground는 모두 System Settings였으므로 이 과제를
배경 앱 성공으로 계산하지 않는다. activation0은 pointer/typing 보존을
증명하지 않으며 전체 초기 panel·serving model 동일성은 미검증이다.
전체 command exit2는 oracle 실패가 아니라 비교 제외를 유지한 결과다.

공통 prompt에 활성화·권한/설정/사용자 문서 변경 금지, target-only 읽기,
foreground/pointer/typing 보존과 불가능하면 중단을 명시했다. prompt는
강제 경계나 실제 준수 증거가 아니다. target-only와 pointer/typing 준수를
현재 계측으로 주장하지 않는다. Sky의 과제 의미도 제약이 추가됐으므로
이전 자유 활성화 cohort와 같은 정책으로 묶지 않는다.

Muxa read-only 리뷰 req_18dd25dd65817ed8_60의 P2 두 건을 반영했다.
NLC 전용 signed --helper 안내를 NLC ARM_CLAUSE로 옮겨 공통 제약은 계층
중립으로 유지하고 새 runtime_policy=background-v2를 추가했다. auditor는
policy version·제약 원문·positive integer timeout의 기록과 두 arm 일치를
확인한다. 동일 버전의 다른 문구나 같은 문구의 다른 timeout도 제외한다.
기존 원시 결과에 새 버전을 소급하지 않는다. 신규 감사
`../reviews/2026-10-10/appearance-pair-prerequisites-audit.json`은
run_not_eligible/runtime_policy_not_recorded를 보존하며 비교율을 계산하지
않는다. 실기 source는 후속 정책/auditor 수정 이전 cohort다.
관련 회귀81개가1.017초에 통과했고 추가 실제 model 요청은 없었다.

전체 G0~G10 완료 기준은 유지한다. 다음 실제 검증은 초기 상태와 입력
보존 관측, Sky 후손 책임/외부 실행 경계, signed owned TextEdit cleanup 및
다양한 앱·백본 반복이다. 이 읽기 pair로 select·저장·전송·장기 복구·설치
완료를 대신하지 않는다.


## G7/G8 후속: signed 문서 창의 부재 관측

TextEdit owned cleanup의 direct AXPress와 부재 검증을 signed 경로로 옮기기
위해 `document_window_status` 읽기 전용 API를 추가했다. 요청은 정확히
`type`, `target=com.apple.TextEdit`, `document_token`이다. launcher·helper가
다른 필드/대상·미지/만료 token을 거절하며 Python은 capability 및 응답의
정확한 field set/token/state/same_process_verified=true를 검증한다.
응답은 open/closed 또는 닫힌 unavailable 오류이며 경로/본문을 포함하지 않는다.

pin은 PID+launch stamp+CFEqual window+selectedPath와 초기 CGWindowID를
기록한다. 동일 프로세스의 성공한 AXWindows 열거 두 번과 선택 경로/창 일치를
검사한다. 경로 변경·중복·재열림·프로세스 재시작·읽기 오류는 closed가 아니다.
최소화돼 AX에 있는 창은 open이다. AX에서 빠진 창은 optionAll의 CG 목록에서
같은 PID/ID가 남아 있지 않은지 두 번 확인한다. 다른 Space나 ordered-out
창이 CG에 남으면 unavailable이다. window ID 없음·CG 목록 읽기 실패/과대/
malformed·화면 기록 권한 없음도 unavailable이며 권한을 바꾸거나 요청하지 않는다.
이 구현은 CGPreflightScreenCaptureAccess를 closed의 보수적 전제로 사용하므로
그 권한이 없는 환경에서는 closed를 제공하지 않는다. 무관한 malformed CG
항목도 결과를 거절한다. 상태는 관측 시점의 증거이며 이후 변경을 막지 않는다.
6초 logical deadline/abort를 읽기 사이 확인하고 이전 deadline을 복원한다.
이미 실행 중인 AX/CG native read를 강제 취소하는 보장은 없다.

닫힘 관측은 token을 유지한다. 다른 호출이 stale로 token을 폐기했으면 미지
token을 closed로 추론하지 않는다. TracingDriver는 live 결과를 전달하되
상태/token을 trace에 저장하지 않는다. TraceDriver는 capability를 제거하고
unavailable을 반환한다. 과거 closed를 현재 파일 정리 권한으로 재생할 수 없다.
본문·UI 입력·활성화·파일 삭제·저장 receipt를 이 API가 수행하거나 승인하지 않는다.

Muxa read-only 리뷰 req_18dd263a18033720_64의 P1 '다른 Space의 창을 closed로
오판'을 위 CG pin/전체 Space 확인으로 수정했다. 후속
req_18dd26516919dad0_65는 P1 해소/P2 없음이었다. 실제 Swift handler 주입
테스트는 off-Space/권한 없음/CG 실패·malformed/ID 없음과 기존 경로/프로세스/
읽기·deadline/abort/재열림을 검증한다. 기존 document text/RTF 경로도 유지했다.

전체 build.sh와 launcher/bundle strict 서명 검증은 성공했다. installed signed
backend는 새 evidence.document_window_status와8개 앱을 보고했고 doctor는
기존 AX/화면 권한·세션 준비 완료였다. 해당 owner-only report는
`../reviews/2026-10-10/document-window-status-backend.json`이며 source 전후 일치다.
이 report는 build/capability 확인이며 실제 문서 open→close나 file cleanup
실행 결과가 아니다. native 닫힘 실측과 signed owned cleanup 통합이 다음 작업이며
현재 comparison의 TextEdit 미실행 gate를 해제하지 않았다.
전체 G0~G10의 대표 앱/백본·인과적 저장/전송·동일 조건 비교/설치 완료 기준은 유지한다.


## G3/G6/G7/G8 후속: signed owned TextEdit 편집과 닫기 실측

`examples/probes/signed_owned_textedit.py`는 private0700 새 디렉터리·exclusive
파일을 만드는 평가 fixture다. `open -g`는 fixture bootstrap에만 사용하고
pin·전체 본문 readback·scoped observe·close는 inherited signed helper를 사용한다.
필요 capability가 없으면 파일 생성 전 거절한다. 성공한 pin은 빈 본문·완전
읽기·선택 경로와 연결한다. helper의 NFD 한글 경로는 NFC로 비교하고 두 경로의
실제 regular-file inode 일치도 확인한다. 이름 비교만으로 소속을 추정하지 않는다.

첫 pin 실패 뒤 현재 소유 문서를 조회해 빈 본문/NFD 경로를 확인했다.
`owned-document-initial-pin-failure.json`의 cause_verified는 이 문자열 비교
결함의 확인 범위다. 첫 reply stream 전체를 기록하지 않았으므로 모든 초기
실패 원인을 그 결함 하나로 확정하지 않는다. 보강 범위는
`owned-edit-file-state-and-diagnostic-scope.json`에도 명시했다. 해당 소유
디렉터리의 dev/ino를 host가 명시해 같은 문서를 다시 pin했고 새 파일을 만들지 않았다.
그 다음 닫기 control의 Description이 없어 close_control_unavailable로 중단한
원시 `signed-owned-document-lifecycle.json`도 그대로 보존했다. 두 실패에서
닫기 입력은 없었다.

이름 없는 AXButton에 Subrole을 표시하고 `expect.attribute=Subrole`로 실제
AXSubrole을 클릭 직전에 확인한다. 관측 및 expectation capability를 추가했다.
기존 label이 있는 요소·창·필드의 렌더링은 유지하며 parser의 attribute 경계에
Subrole을 포함했다. Python driver도 renderer와 독립적으로 AXSubrole을 읽는다.
Muxa 리뷰 req_18dd273aaa2acea0_68의 렌더링 범위·direct 검증 P2를 반영했고
후속 req_18dd27662a8b82f8_69는 P1/P2 없음이었다. 두 transport의 label 처리,
changed/missing/disabled/nil close-control 거절을 회귀로 검증했다.

`nlc eval --product-comparison --check-owned-document-lifecycle` 실기는
소유 빈 문서 open/완전한 empty text→window-bound close→같은 process의 AX/CG
closed 관측→artifact 제거에 성공했다. 기록은
`../reviews/2026-10-10/signed-owned-document-lifecycle-closed.json`이다.
이 probe의 FocusWitness는 setup부터 cleanup까지 포함했고 초기 전경 System
Settings/activations[]/source 전후 일치였다. 이 source는 이후 renderer 호환성,
평가 통합 및 read-only retry 후속 이전이다. 과거 보고서를 새 코드 실측으로
바꾸지 않는다. 모델 요청은0이다. pointer/typing 보존은 관측하지 않았다.

`textedit_edit_owned`만 signed setup/readback/cleanup으로 연결했다. ask는
fixture의 정확한 경로를 사용하며 offline --list는 준비 없이 preview를 출력한다.
잘못된 scope/token/truncated/중복 control/unknown effect는 동작 또는 삭제를
진행하지 않는다. close 요청을 반복하지 않으며 post-close의 일시적 unknown은
최대3초 logical poll 동안 읽기만 재검증한다. helper native read 하나가 deadline을
넘길 수 있다. dirty dialog나 deadline까지 미확정 상태는 남겨둔다.
파일 정리는 원래 directory fd에 unlink를 고정하고 UID/mode/dev/ino·단일
regular-file/nlink·예상 entry를 검사하며 recursive delete를 사용하지 않는다.
최종 rmdir 신원 확인은 같은 사용자의 rename과 atomic하지 않다. 소유 artifact
변경/미확정은 보존하며 실패한 fixture를 다음 setup으로 덮어쓰지 않는다.

실제 NLC model arm은66.1초에 본문 oracle PASS/exit0/source stable였다.
원시는 `../reviews/2026-10-10/product-comparison-20261010-202730.jsonl`이며
initial-empty=true, agent 실행 중 activation0, top CLI 입력306674/캐시267648/
출력1350 tokens다. 이 usage는 nested provider 전체 비용이 아니다.
닫기 요청 후 상태 미확정으로 cleanup=document_status_unavailable_left_as_is,
closed=false/artifact_removed=false를 보존했다. pointer/typing·setup/cleanup
활성화는 그 모델 witness의 관측 범위가 아니다. state oracle 및 tool-event
검사는 actual input producer/layer attestation이 아니며 comparison 제외는 유지한다.

후속 안정된 disk readback에서25 bytes가 정확한 기대 UTF-8과 일치했다.
`owned-edit-file-state-and-diagnostic-scope.json`은 parent identity와 안정된
읽기를 확인한다. 이는 app autosave 또는 close와 구분된 explicit Save 인과
증거가 아니다. UI oracle의 원시 관측은60자 미만의 전체 본문이고 기대 문자열과
정확히 일치했다. 향후 oracle은 whitespace 정규화를 제거한
signed-owned-document-exact-text-v2로 변경했다. 원시 model 실행은 앞선 v1이며
v2의 새 실기라고 소급하지 않는다.

Save/연쇄 TextEdit 과제의 legacy cleanup gate는 유지한다. 실제 편집 뒤
cleanup 미확정의 원인과 개선한 readonly poll 실측, causal Save receipt 연결,
다른 앱·backbone 반복/동일 초기 상태·serving model·Sky 후손/입력 보존/설치 등
전체 G0~G10 완료 기준은 계속 남는다. 관련120개 테스트가5.556초에 통과했다.


## G6/G7 후속: 편집 후 닫힘의 과도 상태 실증

새로운 소유 fixture에만 signed document-token scoped set_value로 host-authored
본문을 입력하고 complete text와 정확히 비교하는 lifecycle probe를 추가했다.
`--owned-fixture-text`는 --check-owned-document-lifecycle에만 허용하고 추가
모델 호출은 없다. write 결과가 unknown이면 입력을 반복하지 않는다.

실제 `../reviews/2026-10-10/signed-edited-document-lifecycle-diagnostic.json`은
open/초기 empty/입력 receiving 검증 뒤 single close에 성공했다. 닫기 직후
native_read_failed, global_window_still_present 두 번을 거쳐 closed가 관측됐고
그때만 artifact를 제거했다. setup~cleanup FocusWitness의 초기 전경은 System
Settings, activations[]였고 source 전후 일치/model requests0이다. 이는 과도
상태에서 읽기만 재검증하는 경로의 실측이며 창 잔존을 closed로 앞당기거나
재클릭하지 않는다. 이전 모델 run의 첫 실패 reply 전체가 없으므로 이 새로운
케이스를 모든 과거 실패의 유일한 원인으로 소급하지 않는다. 과거 미확정
fixture/report를 새 성공으로 덮어쓰거나 그 문서에 입력·닫기를 재전송하지 않았다.

상태 오류의 reason_code는 helper의 고정 매핑과 Python의 정확한 field set/
화이트리스트를 통과한 코드만 보존한다. 알 수 없는 detail은 native_read_failed다.
원시 error/detail·본문·경로가 진단에 들어가지 않는다. effect=not_started는
읽기 전용 상태 query에 관한 값이며 앞선 close가 미실행이라는 뜻이 아니다.
reason_code 자체로 open/closed 또는 permission 원인을 추정하지 않는다.
fixture diagnostics는 token/same_process가 유효한 state만 기록하며 최대64개다.
후속 code는 잘림 표지를 남기고 failure 문자열/매핑 누락 회귀를 추가했다.
원시 실기는 그 telemetry 후속 전 source다. 기존 renderer/close 판정은 그대로다.
Muxa read-only req_18dd2919fa7a3a68_6d는 P1/P2 없음이었다.

전체 build.sh 및 bundle/launcher 서명 확인은 성공했고 서명 식별자·권한 설정은
바꾸지 않았다. 실제 model-agent 편집 후 새 코드 cleanup 반복, causal Save
receipt와 legacy Save/chain task 통합, Sky/다른 backbone 및 대표 앱 반복,
같은 초기 상태/serving model/입력 보존·Sky 후손·설치 검증 등 G0~G10의
완료 기준은 유지한다. 이 host fixture 실기를 모델·전체 비교 완료로 계산하지 않는다.
