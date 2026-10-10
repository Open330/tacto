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
| G5 시각 | browser/native capture 및 host-authorized window 입력 계약. 소유 canvas 결과는 capabilities 문서 참조 | 다양한 실제 앱의 AX 밖 화면과 receiving 증거. 캡처·event 전송 성공을 결과 성공으로 계산하지 않음 | P1 |
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
