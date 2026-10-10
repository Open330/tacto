# 전체 목표의 완료 기준과 현재 작업

2026-10-10 감사. 기존 장기 목표 G0~G10의 최신 작업 계획이다. G0~G10의 원래 범위와 완료 기준을
유지한다. 이전의 특정 fixture 성공 또는 14/14 결과로 전체 완료를 선언하지
않는다. 비교·URL 감사 기준 소스는 401f960이며, 창 timeout 분기 검사는
883f621에서 보강됐다. 아래 상태는 기능 제공과 범용 실기 완료를 구분한다.
원래 목표 이력은 `reviews/2026-09-19/next-goal.md`에 보존한다.

| 항목 | 현재 확인 가능한 구현·증거 | 남은 완료 증거 / 다음 작업 | 우선순위 |
|---|---|---|---|
| G0 입력 보존 | 강제 활성화 제거·window-bound 입력·중단 경계 구현; 비교 cohort의 activation 표본 | 사용자 동시 typing, numeric caret/selection, pointer를 입력 중 관측하고 작업 결과도 검증. 앱 활성화 0만으로 입력 보존을 판정하지 않음 | P0 |
| G1 select | HTML/custom ARIA/native 선택 및 identity·disabled 거절 경로; 소유 fixture 결과는 기존 이력 참조 | 다양한 실제 native 앱의 선택·재열기·변경/실패 케이스와 백본별 반복. Finder 새 폴더 실패를 select 전체 성공으로 대신하지 않음 | P1 |
| G2 paging | 원본 후보 번호·참조 조회·예산 계약 및 회귀 구현 | provider tokenizer를 사용한 token 예산/큰 화면 실모델 반복. byte 예산과 provider 전체 context 한도를 구분 | P1 |
| G3 메뉴·키 | capability·menu-state·dispatch guard 구현. 0d86230에서 timeout set/reset 실패 거부, 401f960에서 Document URL raw 정책 통일 | 배경 Finder New Folder와 TextEdit Save의 실제 효과. 앱/창별 timeout 분기 회귀 유지; 거절 자체는 지원 기능 완료가 아님 | P0 |
| G4 provider | 공통 envelope·attempt usage·로컬 process-group 정리 구현 | 지원 백본별 실제 framing/usage 반복, 실패 시 비용 누락 구분. 원격 취소/비용 중단은 현재 미보장으로 기록 | P1 |
| G5 시각 | browser/native capture 및 host-authorized window 입력 계약. 자체 headless canvas 실제 Codex 이미지 읽기 3/3, 아래 범위 참조 | 다양한 실제 앱의 AX 밖 화면과 receiving 증거. 캡처·event 전송 성공을 결과 성공으로 계산하지 않음 | P1 |
| G6 장기 복구 | 시간/화면 예산·모달·detached·commit 불확실성 회귀 구현 | native 앱의 긴 작업과 끊긴 연결/중단 뒤 상태 재검증. 비멱등 저장·전송의 자동 중복 금지 유지 | P1 |
| G7 저장·전송 | event-bound receipt·정확한 파일 readback·document token·save-panel guard 구현; ExistingTextFileReceipt는 관측된 상태 증거 | 정확한 신규 본문 저장 및 안정된 disk readback, source-format/첨부/목적지 검증, 실제 수신자/본문/신규 message identity의 전송 증거. 이미 있던 정답 파일·앱 autosave를 Save 인과 증거로 계산하지 않음 | P0 |
| G8 대표 평가 | native/Web 과제와 원시 결과/manifest/oracle 기록 존재 | 위 미완료 과제와 다른 앱·백본·반복을 같은 source cohort에서 확장. 실패·SKIP·환경 미지원 보존 | P1 |
| G9 동일 조건 비교 | 2026-10-08 및 10-09 NLC/Sky 비교 원시 cohort 존재 | 성공·실패 모두 동일 초기 상태·manifest·task oracle·시간 제한을 재감사. 요청 백본과 실제 serving model을 구분 | P1 |
| G10 설치·보고 | signed launcher/helper·checkout-backed setup preview·계약 문서 | 새 Mac의 실제 drag/install/permission/skill 연결·업데이트·재시작; standalone 배포 및 notarization. 최종 보고서는 실제 검증 범위별로 작성 | P1 |

### 현재 비교 증거

- `../reviews/2026-10-08/product-comparison-20261008-211647.jsonl`: 28행,
  NLC 14/14·Sky 14/14 oracle 통과, process exit 모두0, source_changed 모두false.
  이는 해당14과제 cohort의 결과다. 명시 manifest 멤버십은 모든 의존성의 폐쇄를
  증명하지 않으며, 전체 기능 완료나 동시 사용자 입력 보존을 증명하지 않는다.
- `../reviews/2026-10-09/product-comparison-20261009-184154.jsonl`: 16행,
  NLC3/8·Sky8/8. NLC 실패는 textedit_save_owned1건, finder_new_folder2건,
  appearance_mode2건. 모든 프로세스 exit0이지만 독립 oracle 실패는 실패다.
  후속 appearance 재실행은 별도 source cohort로 다루며 이 실패를 지우지 않는다.
- 실제 전체 로그 `../reviews/2026-10-10-unit-tests-62.log`, `../reviews/2026-10-10-unit-tests-63.log`: 각각1219 tests, skipped2, 실행1217개.
  단위 테스트 통과는 위 실제 앱 성공과 별개다.

### 다음 실행

1. G7: 배경 TextEdit의 AXValue/AXSelectedText 편집·document edited 상태·
   autosave 순서를 분리해 정확한 본문과 안정된 파일을 검증한다. menu-state는
   진단이며 실제 저장 증거가 아니다. 새 문서·이미 저장된 문서·다른 경로의 같은
   basename·한글 NFD·큰 본문·중단·문서 교체를 각각 포함한다.
2. G3/G0: Finder background New Folder에 필요한 responder/window readiness를
   지원 범위 안에서 구현하고 새 실제 directory identity와 사용자 입력 보존을
   함께 검증한다. 단순 활성화 우회 또는 안전 거절만으로 완료하지 않는다.
3. G4/G2/G6 회귀와 실제 지원 백본 평가, G5 실제 앱 receiving 검증을 이어간다.
4. G8/G9: 수정 전 실패 cohort를 보존하고 새 소스 manifest로 같은 실패 과제들을
   반복 비교한다. 기능 성공과 focus/caret 보존, token 관측과 실제 비용을 분리한다.
5. G10: setup preview를 실제 설치로 검증하고 전체 G0~G10을 재감사한 뒤
   최종 보고서를 작성한다. 아직 미검증인 항목은 완료로 승격하지 않는다.

권한·잠금·captcha·사용자 입력 협조가 필요한 경우는 구체적 환경 조건을 남긴다.
그 환경에서 실행하지 못한 과제를0%/100%로 환산하거나 결과를 만들어내지 않는다.

## 이번 독립 회귀 검증

401f960 기준 provider_result, provider_attempts, candidate_paging,
text_paging, recovery_integration의40개 테스트가21.546초에 통과했다.
provider는 mocked 결과/로컬 subprocess를 사용했으며 실제 유료 모델 요청은
아니다. recovery_integration은 격리된 Chromium 페이지의12단계 wizard,
모달·detached·불확실 commit 경계를 검증한다. macOS 실앱 저장·전송이나
사용자 동시 typing을 검증한 수치가 아니다.

재현 명령:

```sh
.venv/bin/python -m unittest tests.test_provider_result tests.test_provider_attempts tests.test_candidate_paging tests.test_text_paging tests.test_recovery_integration
```

관련 계약과 추가 검증 범위: [capability](capabilities.md),
[paging](context-paging.md), [provider](provider-envelope.md),
[저장](textedit-save.md), [평가](evaluation.md), [비교 기록](benchmark-records.md),
[설치](installation.md). 이 계획은 최종 완료 보고서가 아니며 모든 행의
실기·반복·실패 증거가 확보될 때 전체 목표를 재감사한다.

## G7 후속: 읽는 도중 바뀐 파일의 완료 증거 거부

기존 `_file_state`는 열린 descriptor의 바이트만 읽어 반환했다. EOF 직후
이름의 파일을 다른 내용으로 교체하면 이전 바이트가 정상 반환됐고, 실제
경로의 내용은 달랐다. 이는 실제 임시 파일로 재현했다.

후속 구현은 읽기 전후 descriptor와 경로의 dev/ino/mode/uid/nlink/size/
mtime/ctime, 읽은 길이, canonical path를 대조한다. 변경 시 닫힌 코드
`file_changed_during_read`로 거부한다. 사라진 파일은 `file_unreadable`로
거부한다. 실패한 after_commit을 다시 호출해 완료로 바꾸지 않는다.

`tests/test_existing_file_read_stability.py`는 정상 파일, path 교체,
같은 크기 덮어쓰기 후 mtime 복원, final symlink 교체, 삭제, parent symlink,
receipt 발급 및 재사용 금지의7개 검사를 수행한다. 수정 전 코드에서는
6개가 실패하고, 수정 후 기존 receipt 검사와 합친30개가 통과했다.
추가로 replay commit evidence·typed evidence·fresh-file receipt34개도 통과했다.
검사는 실제 임시 파일과 주입된 문서 응답을 사용하며 실제 TextEdit 저장을
수행하지 않는다. 바이트 일치가 어떤 앱의 Save가 썼다는 생산자 인과 증거를
제공하거나, 마지막 검사 이후 변경·동일 사용자 ABA 경쟁을 배제하지 않는다.

## G8/G9 후속: 저장 oracle 계약 강화

기존 비교는 공백·줄바꿈을 정규화해 정확한 저장 요청을 느슨하게 채점했다.
새 `textedit-save-exact-utf8-v2`는 정확한 UTF-8 바이트와 소유 문서의 정확한
본문을 비교하고 파일 교체·symlink·FIFO·초과 크기를 거부한다. 이전 원시
cohort는 그대로 보존하며 새 oracle 성공으로 승격하지 않는다.
자세한 계약은 [비교 기록](benchmark-records.md)에 있다.

실제 signed `doctor --check`에서 AX·screen 권한 및 GUI 세션 연결을 확인했다.
읽기 전용 menu-state는 배경 TextEdit Save disabled와 전경 Finder New Folder
enabled를 반환했다. 이 조회는 작업 효과·사용자 caret 보존 검증이 아니다.

## 최신 비교 실행과 환경 gate

준비 전/후 화면 잠금·세션 미관측 gate, 시도한 실패의 보존,
comparison_eligible 분리, 잠금 뒤 cleanup 금지, filter/reps 검증 및 종료
코드를 구현했다. 관련 readiness7개+저장 oracle9개+provenance36개,
총52개 회귀 통과. 새 report는0600으로 생성한다.

`reviews/2026-10-10/product-comparison-20261010-155619.jsonl`에서 외형 읽기를
NLC와 Sky 각각 실제1회 실행해 양쪽 PASS(Light)를 확인했다. NLC42.3초,
Sky53.9초, 동일 manifest이며 activation0건이다. 이 기록은 실제 모델·GUI
실행이다. 정확한 저장·Finder 생성·Dark/Auto·동시 사용자 입력은 검증하지 않았다.
시행1회로 속도/비용 우위나 범용 성공률을 주장하지 않는다.
원시 usage·full SHA256·관측 한계는 [비교 기록](benchmark-records.md)에 기록한다.

### G7 정확한 저장 상태 실제 시행

`reviews/2026-10-10/product-comparison-20261010-160030.jsonl`에서 새 소유
문서를 사용해 NLC1/1·Sky1/1의 exact UTF-8 파일/본문 일치와 자체 문서
정리를 확인했다. NLC41.2초, Sky49.5초, 동일 frozen manifest다. 처음의
배경 Save disabled 관측이나 이전 저장 실패를 삭제하지 않는다. 이번 성공은
정확한 최종 상태이며 명시 Save 인과 증명/반복 안정성 완료가 아니다.

NLC activation0, Sky2건의 원인은 미확정이다. 초기 전경 상태를 보존하는
후속 runner 변경은52개 회귀로 검증했으나 위 시행에는 포함되지 않는다.
다음은 초기 상태/receiving witness를 고정한 반복 저장과 Finder 생성,
기존 파일과 새 파일·문서 교체·실패 후 복구, 저장·전송 인과 증거다.

### G3 현재 실패와 다음 구현

`reviews/2026-10-10/product-comparison-20261010-160542.jsonl`에서 Finder 새
폴더는 Sky1/1, NLC0/1이었다. NLC는165.7초/실제 input603060 후 빈 directory로
끝났다. 모델은 메뉴 disabled·창 준비 timeout을 보고했지만 native phase별
실패 trace는 보존되지 않아 원인을 확정하지 않는다. 자기 시험 폴더는 정리했다.

다음 P0 작업은 창 준비의 native readiness 검사/target binding/dispatch phase를
분리한 bounded 진단과 tool의 실제 실패 코드 보존이다. 원시 tree·문서 본문을
로그에 복사하지 않고 노드 예산·deadline·refusal code 등만 계측한다. 새로운
원인 증거에 따라 준비 구현을 수정한 뒤 같은 owned-directory 과제를 반복한다.
필요한 안전 조건을 제거하거나 실패를 지원 완료로 선언하지 않는다.

초기 전경 앱이 양쪽에서 달랐으므로 이 시행은 작업 결과·실패 재현 증거이며
완전한 동일 조건 비교 결과가 아니다. source/lock eligibility와 초기 상태
동등성은 별도 판정으로 유지한다.

### G3 진단 구현과 실제 전송 확인

창 준비의 phase/closed reason/elapsed/scoped AX operation counter를 구현했다.
실제 Swift 함수의 주입 실패와 기존 readiness/dispatch 회귀7개 PASS, helper
빌드/번들 서명 검증 PASS. signed owned AppKit eval1/1 PASS에서 새 진단을
수신했고 background 준비552 scoped operations/1015ms와 responder 효과를
확인했다. 사용자 앱 내용은 새 진단 필드에 넣지 않았다.
계약과 원시 artifact 위치는 [창 입력](native-window-input.md)에 기록한다.

Finder 원인 확인과 구현 개선·실제 반복은 여전히 남아 있다. 새 진단을
작업 성공/재시도 허가/사용자 동시 입력 보존으로 대신하지 않는다.

### 진단 transport와 Finder 단독 시행

`reviews/2026-10-10/product-comparison-20261010-162643.jsonl`: NLC1/1,
256초 뒤 `untitled folder` 한 개를 독립 oracle로 확인했다. source_changed=false,
cleanup folder_removed다. input1421307/cache1333120/output3800이며 raw usage다.
초기 Finder, activation9건의 원인은 미확정이다. 같은 백본의 시행1회이며
반복 안정성/비침해/비용 우위 또는 동일 조건 Sky 비교의 증거가 아니다.

실제 준비 진단 배열은0건이었다. 이후 closed marker 검증/수집과 replay의
bounded 별도 목록/CLI 출력을 연결하고 postcondition 실패 및 no-trace json
예외 경로를 보강했다. 실제 replay와 CLI를 주입 driver로 실행한 검사 등을
합쳐75개 PASS. 다음은 이 후속 transport를 사용해 실제 Finder 준비 실패를
다시 수집하고, 원인 증거에 따라 readiness/targeting 구현을 개선하는 것이다.

### G3 모델 없는 직접 재현: readiness 예산

`examples/probes/finder-preparation-diagnostic.py`는 신규 소유 빈 폴더와 명시한
Window selector로 open_folder→prepare_window를 한 번 실행한다. GUI는 설치된
signed launcher만 사용하고 모델/자동 복구를 호출하지 않는다. 전체 CLI60초
제한에서 로컬 process group을 회수한다. timeout/예외 후 자기 directory도
보존하며, 정상 반환 뒤 동일 inode의 빈 자기 directory만 제거한다. Finder
창과 사용자 파일은 닫거나 삭제하지 않는다.

실제 `reviews/2026-10-10/finder-preparation-directed.json`은 binding 단계에서
readiness_deadline,4956ms, scoped AX2452회로 거부됐다. 이어 현재 source
manifest를 기록한 `finder-preparation-directed-manifest.json`은 같은 단계의
readiness_nodes,1058ms, scoped AX542회로 거부됐다. 후자는 source_changed=false,
report0600이다. 각 시행의 자기 빈 directory는 제거됐다. 둘 다 model_calls0이다.

따라서 원인은 한 가지 timeout만이 아니다. 전체 앱 readiness 순회에서 시간과
노드 예산 모두 실제 차단 조건이다. 이는 시점별 앱 트리 상태가 다른 두 시행이며
동일 트리의 성능 비교는 아니다. 다음 구현은 우선 안전 조건을 보존한 다중 속성
조회로 AX 왕복을 줄이고, 별도로 대규모 다른 창의 modal 검증을 완전하게 수행할
방법을 검토한다. 단순 role prune/예산 확대를 지원 완료의 대체물로 사용하지 않는다.

### G3 attribute batching 구현

Readiness의 node-local Role/Modal/Children packet을 구현했다. 전체 tree 검사와
budget을 유지하고 사후 top role/modal/sheets를 다시 읽는다. 기존 adversarial
corpus의 단건·batch 거부 결과 일치, packet 실패/late modal/role change 및
기존 dispatch/diagnostic 회귀9개 PASS. helper/bundle 빌드·서명 검증 PASS다.

배치 이후 Finder 직접 시행은 readiness_nodes로455ms/193 scoped AX 작업에서
거부됐다. 같은 tree의 전후 실험이 아니므로 성능 비율을 계산하지 않는다.
AppKit receiving 두 시행은 기존 사용자 focus guard에서 입력 전에 거부됐고
실패를 보존했다. 포커스/노드 예산 해결 완료가 아니다. [구현과 실기 한계](native-window-input.md)에
원시 artifact와 실행이 겹친 trial, 최종 role guard 보강의 소스 범위를 기록한다.

### 고정 소스 통합 검사와 Python 회귀 수정

공유 트리 실행 중 소스가 바뀐 검사는 최종 통과 증거로 쓰지 않는다.
소스·테스트를 독립 임시 디렉터리에 복사하고 복사 전후 및 검사 종료 후
파일 SHA256 일치를 확인했다. 이 사본에는 진행 중이던 double_click의
Python 분기도 포함됐다. 분기 안의 중복 `AXUIElementPerformAction` import가
기존 click/menu/secondary_action을 UnboundLocalError로 막는 실제 회귀를
확인했다(최초1259개 검사에서 failure7/error1). 전역 import는 유지하고
분기 import 한 줄을 제거했다. 공유 트리의 같은 수정도39개 회귀로 확인했다.

수정된 고정 사본의 전체 검사는1259 tests, skipped2, 실행1257개,
실패0,223.504초로 통과했다. 사본 manifest SHA256은
`3f5c4ee7e957dd4260d97c84833d41b13f72b9ee150263a054d79d1ef31f4d52`다.
이는 비교 runner의 manifest와 다른 전체 소스/테스트 사본 계약이다.
원시 통과/실패 로그와 manifest는
`reviews/2026-10-10/isolated-integration-*`에0600으로 보존했다.
후속 peer 변경이나 통합 helper 빌드는 별도 검증이 필요하다.
실앱 receiving, Finder 노드 예산 해결, 전체 목표 완료의 증거는 아니다.

### G7 리뷰 반영: safe-save 교체 중 읽기 재관측

Muxa read-only 리뷰에서 정상 safe-save의 rename/swap 도중 읽기 실패가
즉시 종결되는 liveness 문제를 확인했다. after_commit은 실제 ENOENT 또는
metadata/inode 변경을 내부 typed transient로 분리해 마감까지 파일만 다시
관측한다. public error 문자열만으로 재시도를 허용하지 않는다. 매번 동일
문서 token·경로·정확한 본문을 재검증한다. 초기 prepare는 안정된 파일을
요구하며 재관측하지 않는다. Save 입력은 반복하지 않고 after_commit은
여전히 한 번만 호출할 수 있다.

권한·EIO·nonregular·다중 hardlink와 문서 변경은 즉시 거부한다. 계속
교체되거나 이름이 사라지면 마감에 마지막 closed code로 거부한다. 정상
파일의 exact bytes도 마감 후 반환되면 file_readback_deadline으로 거부한다.
OS/AX 호출 강제 취소, writer attribution 또는 autosave와 명시 Save의 인과
구분을 구현했다고 주장하지 않는다.

새15개 검사는 실제 임시 파일의 EOF 교체·open 직전 교체·이름 공백·
반복 교체·다른 내용·문서 변경·권한/EIO·FIFO·hardlink·마감 초과를 다룬다.
최초12개를 수정 전 cfa9aa2에 실행하면 failure5/error4, 수정 후 최종15개와
기존 receipt/replay/evidence 검사를 합친93개는2.130초에 통과했다.
이는 실제 TextEdit UI safe-save나 전송 수신 증거의 검증은 아니다.

후속 Muxa 리뷰는 P1 없음·범위 적합으로 판단했다. 늦은 exact read 거부는
FreshFileReceipt와 같은 receipt 수용 마감 계약으로 유지한다. 이는 Save
실패나 파일 불일치를 뜻하지 않으며 OS/AX 호출을 취소하는 것도 아니다.
큰 문서/느린 backend는 호스트가 timeout을 늘려야 하고, 자동으로 수용
기간을 늘리지 않는다. 첫 poll이 느리면 정상 저장 상태도 마감으로 거부될
수 있다는 liveness 제약을 API docstring에 명시했다.

### G7 새 요청과 독립 수신함의 전송 증거

`DeliveredMessageReceipt`는 호스트의 실제 control/payload 관측으로 결합된
request_id와 authoritative 수신함의 새 message_id·account·recipient·정확한
본문 digest를 대조한다. 기존 수신 record, queued/accepted, 모델 JSON,
송신 Sent 표시는 완료 증거가 아니다. prepare/after 각각 한 번만 허용하고
수신함 조회만 반복한다. 이 API는 native 메신저의 자동 bridge가 아니며
서비스 request binding·인증된 수신 조회·독점 요청 identity가 전제다.
첨부·동일 UID의 다른 writer·서비스별 atomic precondition은 아직 별도 과제다.
[서비스 계약과 실기 범위](delivery-evidence.md)에 상세 조건을 기록했다.

실제 headless Chromium의 NLC click→owned loopback HTTP POST→인증된
독립 HTTP inbox read를 검증했다. 서비스는 수신 본문을 보관하고 조회 시
그 본문을 hash한다. 정확한 새 수신, no-op Sent, 수신 본문 손상, 응답 유실,
기존 요청 재사용 거부, 승인 거절, 실제 form 수신인 변경, dispatch 전 거절의
8개 통합 케이스다. 실제 외부 사람이나 native GUI에 메시지를 보내지 않았다.

응답 유실 케이스에서 기존 replay는 host receipt로 event를 confirmed한 뒤에도
원래 driver error를 반환해 실패했다. receipt가 검증된 consequential unknown
응답만 원래 transport uncertainty를 event에 남기고 진행하도록 수정했다.
이 hunk는 peer 통합 커밋 ce4074a에 포함됐다. 영수증이 없거나 not_started면
완료로 바꾸지 않으며 재전송하지 않는다. 후속 UI postcondition은 별도다.

새 adapter unit14 + receiving integration8 + 기존 저장/receipt/replay/evidence
검사를 합친115개가13.096초에 통과했다. 초기 approval fixture 오류4건과
그 수정 후 발견한 실제 response-loss 오류1건의 로그도 보존한다. 전체
실제 native 전송·첨부·다양한 백본·NLC/Sky 동일 조건 비교는 아직 남아 있다.

고정된 exact ce4074a archive + root receipt8개 overlay의 전체 검사는
1301 tests/skipped2/230.193초, 문서 링크 검사1건만 실패했다. 새 문서의
README 링크를 추가하고 관련 문서 검사6개를0.025초에 통과했다. 원시 실패
및 manifest는 `reviews/2026-10-10/receipt-stable-full-*`, 수정 검사 로그는
`receipt-docs-recheck.log`에 보존했다. 이 결과는 후속 f339633의 bound Return과
진행 중인 TranscriptEvidence를 검증하지 않는다. 앞선 일부 WIP가 섞인
사본의1301개 검사/실패3개도 `receipt-partial-wip-*`에 별도로 보존했다.

G4 실제 framing 교정은 Codex CLI0.161.0 direct adapter에서 JSON oracle
PASS(5.337초,input16149/cache12160/output9)를 확인했다. 실제 serving model과
달러 비용은 미관측이다. Claude CLI2.1.296 common/tools-disabled route는
nonzero error로 실패하고 usage는 미관측이다. 각 시행1회·서로 다른 route의
순수 산술 요청으로, UI 성공률/동일 조건 비교/비용 우위가 아니다. 원시
envelope와 source checkpoint는 `provider-framing-codex.json`,
`provider-framing-claude.json`에0600으로 보존했다. Claude 실패 원인과
closed provider reason 분류는 추가 진단 중이다.

### G4 실제 조직 접근 제약과 오류 분류

Claude CLI2.1.296의 실패는 옵션 호환·quota·모델 refusal이 아닌 조직의
Claude Code 구독 사용 제한이라는 명시적 API 오류였다. 초기 일반nonzero
실패 기록을 보존하고, 완전한 isolated failed stream만
organization_access_disabled로 분류하도록 보강했다. 성공 parser는 완화하지
않고 본문·모델·usage·cost를 검증된 성공 값으로 승격하지 않는다. 설정이나
인증 경로를 바꾸지 않았다. 실제 common 호출에서도 새 닫힌 attempt code와
사용자 설명이 반환됐고 usage는null이었다(1.797초). 원시 envelope는
`provider-organization-access-classified.json`에0600으로 보존했다.

조직 제한을 설명하는 정상 답변, 도구 포함/불완전/다른 세션/다른 API 실패,
잘못된 JSON/예산, fallback 뒤 다른 실패와 timeout 및 primary error code의
일관성을 검증했다. 관련 provider/CLI/envelope 검사66개가6.384초에 통과했다.
이것은 Claude 모델의 실제 UI 작업 성공이나 비용0을 증명하지 않는다.
현재 Claude 구독 경로의 대표 실제 백본 평가는 접근 제약으로 남아 있으며
Codex 등 독립적으로 사용할 수 있는 경로의 작업은 계속한다.

### G7 수신 영수증 후속 리뷰 반영

Muxa 리뷰의 P1 없음·계약 적합 판단을 확인했다. 호스트의 기대 body digest를
commit_expected에 내보내고 공통 DeliveryEvidence matcher가 독립 대조하도록
보강했다. 승인 대기 실행에서도 prepare가 소모되는 수명을 문서화하고,
실제 gated→동일 adapter 재사용 거부→새 request/adapter 승인 실행을 추가했다.
message_id의 colon/dot/@/Unicode는 보존하고 control·공백·초과 길이는 거부한다.
해당 변경을 포함한 delivery/replay/typed evidence/transcript 검사54개는
12.267초에 통과했다. 송신 transcript와 수신함 증거의 품질 구분은 유지하며
실제 native bridge·첨부·독점 요청 조건이 없는 서비스에는 성공을 주장하지 않는다.

## G4/G5 후속: 계획 응답의 도구 기록 거부와 실제 이미지 읽기

Codex 계획 adapter는 reasoning/agent_message 이외의 turn item을 거부한다.
성공한 명령 실행이나 MCP 호출 뒤 정답 final이 나와도 계획 응답 성공으로
인정하지 않는다. 이는 실행 후 수용 조건이며 도구 실행 차단의 증거가 아니다.
관련 provider/visual/process 회귀 34개가 통과했다.

`examples/probes/browser-visual-score.py`로 자체 headless Chromium의 canvas에
무작위 네 자리 게임 점수를 그려 실제 Codex 이미지 요청을 세 번 실행했다.
점수는 AX tree와 DOM 문자열에 없음을 각 사례에서 검사했고, 위치·배경 대비·
글꼴을 바꾼 세 이미지 모두 정확한 점수를 반환했다(3/3).
원시 이미지·답변·provider metadata는
`../reviews/2026-10-10/visual-score-codex-acceptance/`에 보존한다.
관측 input 합계 48,784 tokens, output 합계 48 tokens이며 이는 CLI telemetry다.
실제 serving model과 비용은 null이고 요청은 기본 Codex image 경로를 사용했다.

이 실행은 HEAD 3351584에 미커밋 adapter/probe 변경을 포함했다. 최초 실행은
HEAD checkpoint만 기록해 전체 dependency freeze 또는 source 불변성을
증명하지 않는다. 후속 probe는 제한된 여섯 소스의 전후 hash도 기록하지만
전체 의존성 폐쇄를 증명하지 않는다. 사용자의 실제 브라우저·macOS 창,
시각 좌표 행동, 저장·전송 receiving 증거, NLC/Sky 동일 조건 비교를 수행한
결과가 아니다. 전체 G5 완료 기준은 유지한다.

## G2/G1 후속: 실제 token 예산·재요청 한도·웹 옵션 선택

`ask_here_paged`의 최초 prompt 검사 뒤 `ask`가 검증 재요청 안내문을 붙여
예산보다 큰 요청을 보낼 수 있었고, 재요청은 max_pages 호출 상한에도
포함되지 않았다. 실제 reach_model 직전 guard가 최종 prompt와 논리 요청 수를
함께 검사한다. validation retry가 한도를 소진하면 추가 페이지를 요청하지
않으며, 기존의 예산 안 재요청과 원래 후보 선택은 유지한다.
새 테스트4개를 HEAD 2207886의 ask 소스로 실행하면3개가 실패했다.
수정 후 paging/validation/provider-envelope/docs 검사33개가 통과했다.
기준 실패 로그는 `../reviews/2026-10-10/paging-request-limit-baseline.log`다.

실제 tiktoken 0.14.0의 o200k_base·cl100k_base로 한글·emoji·결합 문자·
JSON escape·특수 토큰처럼 보이는 문자열이 있는211개 후보를 각각29페이지로
손실 없이 재조립했다. 전체 renderer prompt는350 token 예산 안에 있었다.
같은 원문의 text paging은180 token 예산 안에서 각각23·25페이지로 재조립했다.
기록: `../reviews/2026-10-10/tokenizer-lossless-paging.json`.
선택한 tokenizer의 문자열 예산을 검증한 것이며 실제 serving model tokenizer,
숨겨진 provider system/tool/framing 토큰 또는 전체 context 한도를 증명하지 않는다.

자체 headless Chromium에36개·48개 select control을 만들고 실제 AX tree에서
후보와 disabled option을 추출했다. 기본 direct Codex CLI 모델에 전체 task
prompt의 o200k_base 예산2500을 적용해 뒤쪽 후보를 찾고 Priority를 선택했다.
호스트는 목표 control·단일 select_option·정확한 value를 독립 대조한 뒤
동작을 적용했고, DOM에서 목표 하나만 변경되고 나머지 선택값은 모두 그대로임을
검사했다. 모델 요청은 각각3·5회, 최대 측정 task prompt는2420 tokens,
최종 DOM oracle은2/2 통과했다. actual model·비용은 미관측이며 native 앱이나
Sky 동일 조건 비교 결과가 아니다. 직접 adapter를 주입했으므로 Muxa 우선
라우팅 검증으로 취급하지 않는다.

첫 시험은 probe의 driver.act 인자 순서 오류로 target_mismatch/not_started,
DOM oracle0/2였다. 모델이 올바른 후보를 고른 것만으로 성공 처리하지 않았으며
그 실패 기록 `../reviews/2026-10-10/token-budget-select-codex/`를 보존했다.
인자 순서를 수정한 후의 실제 재시험은
`../reviews/2026-10-10/token-budget-select-codex-corrected/`에 별도 보존했다.
두 실행 모두 측정한 Python source subset의 전후 hash는 같았으나 전체 의존성
freeze를 증명하지 않는다. 전체 G1/G2의 실제 native 앱·백본별 완료 기준은 유지한다.

Muxa read-only 리뷰는 P1 없음으로 판단했다. 요청 상한의 의미 변화는 의도이며
README의 CLI 옵션과 API docstring에 실패·검증 재요청을 포함하는 시도 기준으로
명시했다. 재요청 거부 metadata에 phase를 남기고, 재요청 직전 계측 실패는
모델 실패(None)로 숨기지 않고 token_counter_failed·미측정 count null로 반환한다.
이 추가 경로와 CLI를 포함한 후속38개 테스트가 통과했다. 새 실제 모델 요청을
보내지 않는 진단 변경이므로 기존 실제 모델/DOM 시험 기록은 별도 소스 범위로
유지한다. 리뷰 request는 req_18dd223a04ea7418_3d다.

## G10 후속: 새 사용자 설치·스킬 연결의 실패 경계

skill/install.sh는 signed launcher 경로가 없으면 Python console script를
연결하던 fallback을 제거했다. 새 사용자·relocated Claude/Codex 환경의
없는 skills 폴더를 생성하고 최신 checkout의 skill 링크를 만든다.
확인 모드는 변경 없이 누락/충돌을 exit1로 보고하며, 잘못된 옵션은
설치를 시작하기 전에 exit2로 거부한다. 이름만 같은 다른 명령/스킬의
파일·정상/깨진 symlink는 보존한다. 기존 NLC 스킬 복사본과 그 안의 로컬
추가 파일은 삭제 대신 private backup에 보관한 후 연결한다.
기존 NLC checkout의 launcher/console 링크는 NLC marker를 확인해 갱신한다.

실제 bash installer를 임시 checkout·새 사용자 경로(공백 포함)에 실행한
9개 케이스와 기존 설치5개 케이스는14/14 통과했다. 이 fixture의 launcher는
실행하지 않는 placeholder이며 서명·TCC·실제 앱 검증을 대신하지 않는다.
원래8개 새 케이스를 수정 전 HEAD 8c37eca의 installer로 실행한 결과는
unittest failures8/errors2(하위 subtest 포함)였고, 전체 로그를
`../reviews/2026-10-10/skill-installer-baseline.log`에 보존했다.
회귀 runner의 wrapper exit는0이므로 그 exit를 테스트 성공으로 해석하지 않는다.

현재 실제 설치에는 `skill/install.sh --check`만 실행했다. launcher와
Claude/Codex skill 링크는 모두 맞고 exit0이었다. 실제 사용자 디렉터리의
설치·권한 변경이나 앱 실행은 하지 않았다. 시작 앱의 연결 버튼은 installer의
nonzero 상태를 실패로 표시하는 기존 계약을 그대로 사용한다. 새 Mac의
드래그·Gatekeeper·권한 부여, standalone runtime·배포 서명·공증은 여전히
남아 있으며, 이 연결 검증만으로 G10 전체 완료를 선언하지 않는다.

후속 Muxa read-only 리뷰(req_18dd22a8bf3e80c0_41)는 P1 clobber 없음으로
판단했다. 중복 스킬 탐색 가능성을 줄이기 위해 백업은 skills discovery root
밖의 상위 폴더로 옮겼고 회귀에서 이를 검사한다. 기본 두 agent 폴더 생성은
installer가 두 도구를 함께 준비하는 명시 목적이므로 유지하며 문서에 적었다.
존재하는 이전 checkout 링크 갱신은 회귀로 검증됐지만, 이전 checkout 자체가
없어진 dangling link는 출처를 확인할 수 없어 자동 교체하지 않는다.
구체적인 확인 안내를 추가했고 이 이동 복구 제약도 유지한다.

현재 설치의 launcher·Codex skill·.venv의 Python CLI import 경로가 이 checkout과
일치함을 읽기 전용으로 확인했다(`../reviews/2026-10-10/installed-runtime-paths.json`).
현재 launcher와 helper bundle의 codesign strict 검증은 모두 exit0이었다
(`../reviews/2026-10-10/installed-signature-checks.json`). 이는 현재 로컬 signature
유효성 확인이며 새 Mac의 권한·배포 identity·notarization 검증이 아니다.

실제 설치된 skill 링크를 통해 install.sh --check를 실행하는 추가 회귀에서
Bash의 논리적 '..' 처리 때문에 repo가 ~/.codex/skills로 잘못 계산되고,
launcher가 없다는 exit1이 재현됐다. 먼저 installer 디렉터리를 pwd -P로
확정한 뒤 그 부모를 repo로 계산하도록 수정했다. Codex/Claude의 설치된
skill 경로 모두 같은 checkout을 찾는 검사를 추가했다.
최종 installer10개+기존설치5개+docs6개는21/21 통과했다.

## G6/G7 후속: 일시적인 수신 조회 장애 뒤 단일 전송 확인

DeliveredMessageReceipt는 호스트가 명시적으로 InboxTemporarilyUnavailable을
발생시킨 경우에만 after_commit의 read-only 수신함 조회를 마감 안에서
재관측한다. prepare에서 조회불가는 이전 수신 부재를 확인하지 못한 것이므로
입력 전에 거부한다. 일반 예외·인증 오류·수신 field 불일치는 즉시 거부한다.
마감이 이미 지났으면 새로운 읽기를 시작하지 않으며, 진행 중인 조회를
강제 취소하거나 늦은 수신 record를 수용하지 않는다. 같은 request_id로만
조회하고 bind/click/POST를 다시 실행하지 않는다.

새 unit6개는 준비 조회불가/재사용 거부, 일시 장애 복구, 지속 장애의 정확한
deadline 종료, generic 권한 오류의 즉시 거부, 복구 후 본문 불일치와 느린
조회 실패를 검사한다. 새 headless Chromium/loopback HTTP4개는 실제503
두 번 뒤 정확한 새 수신, dispatch 응답 유실+503 뒤 단일 전송 확인,
지속503 미검증, 수신403 즉시 거부를 검사한다. 원래 검사와 합계37개가
18.365초에 통과했다. 성공한 복구에서도 전송 POST는1회이며 새로운 message
identity와 수신 본문을 확인한다. 장애 사례에는 실제 수신 메시지가 있어도
조회 증거가 없으면 commit_unverified를 유지한다.

수정 전 HEAD c0cc88d 구현에 새 호스트 신호 타입만 제공해 동일한10개 새
검사를 실행하면 failures5/errors1, exit1이었다. baseline polling 로직은
변경하지 않았다. 로그는 `../reviews/2026-10-10/delivery-read-outage-baseline.log`다.
이 검증은 자체 계정·본문·로컬 서버만 사용했으며 실제 원격 메신저, 첨부,
native 앱과 여러 모델 백본 또는 NLC/Sky 동일 조건 비교의 완료 증거가 아니다.
이 범위 밖 항목은 G0~G10 원래 완료 기준으로 계속 진행한다.

Muxa read-only 리뷰(req_18dd231e9140bb18_46)는 P1/P2 없음으로 판단했다.
명시 신호의 하위 클래스도 읽기 재관측에 해당함을 문서화했다. 표시 계층이
private exception 클래스명만 보여 주는 문제는 별도 replay 소유 경로에서
peer가 다루며, delivery 모듈에는 닫힌 PUBLIC_CODES와 내부 code 검증을
제공한다. 현재 범위의 완료·오류 수용 조건은 변경하지 않는다.
추가 typed commit/replay/observation-binding/transcript 회귀21개도 통과했다.

## G0/G9 후속: 세션 지표 결합과 기존 비교 전제 감사

초기 WIP에서는 CGSession lock key 부재를 항상 unknown으로 막았고,
Finder 두-arm CLI가 setup/모델 없이 exit2를 반환했다. 그 실행 기록
`../reviews/2026-10-10/comparison-session-unobservable.json`은 보존하지만,
이를 현재 환경이 접근 불가라는 근거로 쓰지 않는다. Muxa 리뷰는 정상 로그인한
콘솔에서도 그 private key가 생략되는 플랫폼 동작 때문에 거짓 차단이라고
지적했다. root는 공식 Apple XNU의 console/login positive 지표와 lock 비교,
현재 CGSession·IOConsole의 boolean 지표를 읽기 전용으로 확인해 교정했다.

최종 lock_state는 key가 생략됐을 때 console=true+login_done=true가 함께
있으면 unlocked로 해석한다. malformed lock flag, 없는 세션·지표는unknown이다.
비교 실행기는 off-console·로그인 미완료·unknown과 명시적인 잠금을 차단한다.
이는 best-effort platform inference이며 atomic 입력 승인이나 사용자 입력
보존의 증거가 아니다. peer의 잠긴 때 raw dictionary 원본은 없다는 한계도
유지하며 원래 평가의 task별 guard는 계속 필요하다.

실제 installed signed launcher의 doctor --check는 exit0으로 손쉬운 사용·
화면 기록 권한과 보이는 앱8개를 보고했다. 권한 요청·설정 열기·비교 task 실행은
없었다. 현재 readiness는 잠금 key 부재만으로 차단할 상태가 아니다.
확인 결과는 `../reviews/2026-10-10/comparison-session-calibrated.json`에 별도 보존한다.

새 audit_product_pairs는 기존 JSONL의 task/rep를 정확한 arm 한 쌍으로
그룹화하고, 중복·누락·초기 전경·oracle·layer 준수·source manifest checksum 및
전후 안정성·잠금 관측을 검사한다. task outcomes를 보존하고 입력을 수정하지
않는다. 기록된 부분 전제가 같아도 전체 초기 task 상태나 실제 serving
model/configuration은 증명하지 못하므로 same_conditions_verified=false를
유지하고 비교 성공률·성능 수치를 계산하지 않는다.

실제 기존 네 cohort를 감사해 appearance/save의 초기 전경 누락, Finder의
초기 전경 차이, NLC-only 후속을 확인했다. 결과와 각 입력 SHA256은
`../reviews/2026-10-10/product-pair-prerequisites-audit.json`에 보존한다.
새 pair 감사7개와 비교 provenance/소유 oracle/세션 검사를 합친 초기47개는
0.236초에 통과했다. 교정한 로그인·malformed 지표와 기존 eval 환경 검사를
포함한 후속70개는0.428초에 통과했다. 비교 harness의 setup/oracle은 기존
Python Driver 경로도 사용하므로 signed helper 실행 경계로 맞추는 다음 작업이
필요하다. 새로운 native task 실행과 완전한 동일 조건/backbone/초기상태
cohort 구축은 여전히 남아 있다. 리뷰는 req_18dd2386b10fe078_4b 및
req_18dd239ebba8bc40_4c다.

## G9 후속: signed 비교 host 진입과 책임 신원 제약

comparison의 `_driver`를 InheritedHelperDriver로 바꾸고 signed launcher의
`nlc eval --product-comparison` runpy 진입을 추가했다. helper 채널이 없으면
setup 전에 not_run이며 Python direct fallback이 없다. backend 확인은 앱
metadata/capability만 읽는다. 실제 installed signed command는 exit0으로
대상8개와 observe/act/background prepare/document text 등 capability를
보고했다. 기록은 `../reviews/2026-10-10/signed-comparison-backend.json`이며
source hash는 smoke 뒤 followup 진단 변경 후의 것이라고 구분했다.
이 확인은 task input·모델·사용자 앱 정리·권한 변경을 수행하지 않았다.

Calculator menu/clear 준비가 거부되거나 불확실하면 준비를 중단하고 후속
입력을 반복하지 않는다. 직접 AXPress cleanup이 남은 owned TextEdit 과제는
signed window binding을 구현할 때까지 fixture 생성 전 미실행이다.

Muxa read-only 리뷰(req_18dd2431bbd4bbe0_51)의 P1을 반영했다. 공통
_provider_env는 NLC helper 채널을 제거하지 않으므로 비교 전용 _agent_env에서
FD/launcher/token 표지를 명시적으로 제거한다. 부모 환경은 바꾸지 않는다.
환경 제거만으로 TCC responsible identity가 분리되지는 않으며, signed host가
spawn한 model/Sky의 실제 책임 신원은 미관측이다. 현재 경로의 task outcome은
보존하되 모든 실행을 제품 비교에서는 제외한다. 외부 agent controller와
signed host를 분리하거나 실제 responsible identity를 관측·감사하는 다음
구현이 필요하다. 이 제한을 동일 조건 비교 완료로 승격하지 않는다.

product flag는 argv 위치와 무관하게 선택하고 중복은 실행 전에 거부한다.
backend/provenance/CLI/install 후속66개는65 PASS/선택적 wheel 설치1 SKIP,
0.477초였다. 환경과 API 연결 검증이며 실제 새 native 과제 성공률은 없다.
전체 G0~G10과 대표 앱/백본/동일 초기 상태·모델 증거 완료 기준을 유지한다.

## G9 후속: 관리자 권한 없는 책임 PID 진단

launchctl procinfo는 현재 환경에서 root가 필요해 거부됐다. 승격하거나
권한을 변경하지 않았다. 실제 libSystem responsibility SPI가 존재하고
현재 process PID를 조회할 수 있음을 읽기 전용으로 확인해 진단 모듈을
추가했다. libproc 경로와 책임 PID를 두 번 읽고 변화·API 없음·읽기 실패는
unavailable/null이다. 기본 Python 코어에 GUI 의존성을 추가하지 않으며
비Darwin에서는 native library를 로드하지 않는다.

`nlc eval --product-comparison --check-responsibility`는 signed controller와
그 controller가 만든 idle Python 자식을 관측했다. 실행 exit0, controller
88162와 child88173은 각각 자기 PID를 책임 PID로 반환했고 경로는 동일한
Python 실행 파일이었다. 자식은 stdin byte로 종료하고 wait로 회수했다.
예외/3초 timeout에는 그 소유 자식만 종료·회수한다. 모델·Sky·native task
입력·권한 변경은 없었다. 실제 기록과 primary ABI source hash는
`../reviews/2026-10-10/signed-controller-responsibility-probe.json`에 보존했다.
source hash는 live probe 뒤 확인한 것으로 구분했다.

반복 경로 일치는 code identity, PID 재사용, descendants 책임 또는 실제
TCC permission 판정을 증명하지 않는다. 실제 Codex/Sky 책임 관측 및 외부
agent↔signed host 분리도 구현하지 않았으므로 기존 비교 제외 조건은 유지한다.
이 진단과 backend/provenance58개가0.334초에 통과했다. 전체 목표와 실제
대표 앱·백본·같은 초기 상태 비교 완료 기준은 계속 유지한다.

Muxa read-only 리뷰(req_18dd24b163cf97f0_55)는 P1 없음으로 판단했다.
앞선 'model이 NLC launcher 책임 신원을 상속한다' 설명은 가설이었고,
현재 owned Python probe는 self-responsible 관측이므로 그 가설의 직접 증거가
아니라고 정정한다. 그렇다고 실제 Codex/Sky가 Python 귀속이라고 추론하지도
않는다. 아직 그 프로세스를 관측하지 않았다.
cleanup timeout은 소유 자식을 kill/wait한 뒤 닫힌 status와 exit2로 반환하고,
일반 probe 실패의 예외 본문은 출력하지 않는다. caller interrupt는 소유 자식
정리 후 재발생시킨다. start_new_session=True도 결과에 기록하며 이를 TCC
분리로 주장하지 않는다. 추가 회귀 후60개가0.345초에 통과했다.


## G4/G9 후속: 실제 Codex 주 프로세스 관측과 provider 환경 정리

공통 provider 환경에서 private NLC 채널 표지를 항상 제거하도록 수정했다.
부모 환경·profile·credential은 보존한다. Codex structured adapter에는
호스트 소유 list로만 전달되는 선택적 OwnedProcessSampler를 추가했다.
주 PID를 최대64회 관측하고 요청 종료 시 bounded join하며 실패·timeout에도
진단을 보존한다. tool/model 결과 envelope와 분리하고 후손 및 TCC permission은
미관측으로 유지한다. 긴 요청의 전체 시간 관측은 보장하지 않는다.

실제 도구 없는 Codex 산술 요청은 성공하고 정답 oracle도 통과했다.
31회 관측에서 주 프로세스/책임 실행 경로는 Codex CLI였고 observer는 종료했다.
요청 전후 source hash가 같고 report는 owner-only 새 파일
`../reviews/2026-10-10/live-codex-process-observation.json`에 보존했다.
이 live 요청은 signed controller 비교 과제가 아니므로 앞선 비교 제외를
해제하지 않는다. actual Sky 및 후손 책임 그래프, 외부 agent↔signed host 경계,
owned TextEdit signed cleanup, 같은 초기 상태/모델 cohort와 설치 검증 등
G0~G10 완료 기준은 계속 남는다.

Muxa read-only 리뷰 req_18dd252e1646a7d0_59에서 P1/P2 없음으로 판단했다.
관측 한도·경로 공유 주의·미관측 범위를 문서에 명시했다.


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

관련 회귀105개가4.334초에 통과했다. 후속 lifecycle14개도0.668초에
통과했고 실제 소유 CLI의 cwd가 /tmp임을 추가 확인했다. 실제 소유 Python 부모가 띄운
같은 그룹 후손의 지연 파일 쓰기가 timeout 후 발생하지 않는지 확인했다.
이는 로컬 그룹 종료 증거이며 별도 세션 이탈·원격 모델 취소·Sky 실작업
완료 증거가 아니다. 실행 파일 부재·두 arm의 diagnostic 보존·기존 source
drift/cleanup/provenance 및 provider/documentation 회귀를 포함한다.

소유 프로세스의 진단이 기록된 뒤 OSError가 발생하면 not_run 대신
agent_transport_failed/failed로 남긴다. transport 실패를 미실행으로 바꾸거나
앱 oracle로 성공 판정하지 않는다.


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

최종 관련 테스트51개 중50 PASS/선택적 NLC_CONTRACT_LIVE 실기1 SKIP,
4.969초였다. 문서 변경 뒤 docs6도0.039초에 통과했다. 없는 test_trace 모듈을
잘못 지정한 첫 검증 명령은 import 오류였고, 실제 test_document_text_trace로
수정한 위 실행이 최종 결과다. 실기 SKIP을 성공으로 계산하지 않는다.


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

최종 관련123개가4.631초에 PASS, 문서 변경 뒤 docs6도0.037초에 PASS였다.
