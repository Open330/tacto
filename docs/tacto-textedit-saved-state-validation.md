# Finder 배경 준비와 실제 TextEdit 저장 검증 — 2026-10-18

개발 checkout과 서명된 개발 helper의 결과다. 설치 앱을 바꾸지 않았다.
전체 G0~G10, Finder 폴더 생성, 파일 생산자 인과 증거의 완료 보고서가 아니다.

## Finder 사전 조건과 실제 준비

`signed-finder-preparation-1792328019643518000.json`에서는 시작부터 Finder가
전경이었다. 폴더를 열고 foreground 준비 분기를 수행하며 포커스가 바뀌었고,
입력 audit이 CHANGED였다. 기존 FAIL을 덮어쓰지 않고 배경 초기 조건이 성립하지
않은 실행으로 구분한다. 사람의 앱을 임의로 전환해 조건을 만들지 않았다.

probe에 사전 조건을 추가했다. 전경 pid·bundle을 안정적으로 읽지 못하거나
대상이 이미 전경이면 fixture와 입력을 시작하지 않는다. 폴더 열기·창 준비 직전과
읽기 전용 preflight 뒤에도 다시 확인한다. 최초 미실행만 SKIP하고, 이미 일부
동작을 수행한 뒤 조건이 달라지면 실패를 유지한다. 이는 sampled precondition이며
확인과 동작 사이의 변화를 원자적으로 막는 장치는 아니다.

전경이 TextEdit인 상태에서 `signed-finder-preparation-1792329580512021000.json`은
2944ms, task_result_ok=true, input audit UNCHANGED_SAMPLED, source subset 안정으로
통과했다. 폴더 열기와 prepare 요청, 최종 focused Window의 제목 조건을 확인했다.
두 번째 event state는 unknown으로 남아 있으므로 입력의 인과적 완료 receipt라고
설명하지 않는다. 실제 New Folder 생성·여러 창 폭 반복·사람의 typing/caret 보존은
이 결과로 증명하지 않는다.

통합 helper의 후속 `signed-finder-preparation-1792331957333788000.json`은
source subset 안정·배경 사전 조건 충족·입력 audit UNCHANGED_SAMPLED였으나,
readiness가 대상 트리37개 노드째 읽을 수 없는 노드를 만나
`preflight_unavailable`로 실패했다(1526ms). open_folder 요청 후 prepare 입력은
보내지 않았다. 기존 통과를 이 실패로 대체하거나 반대로 실패를 지우지 않는다.

## 실제 TextEdit 저장

`signed_textedit_saved_state`는 기존 소유 문서 fixture를 독립 실행한다.
새 private 임시 디렉터리와 평가기 자신의 `.txt` 문서만 사용하며, exact document
text·디스크 바이트·Save 응답·종료/정리·입력 audit을 구분한다. 원래 문서나
불확실한 종료 상태의 파일을 지우지 않는다. 모델 호출은 없다.

| 원본 | 저장 전/후 | 종료/정리 | 입력 보존 | 전체 판정 |
|---|---|---|---|---|
| `signed-textedit-saved-state-1792328824727772000.json` | Save 전 불일치 → acted 후 정확한 바이트 일치 | 상태 읽기 실패로 보존 | sampled unchanged, activation0 | FAIL |
| `signed-textedit-saved-state-1792329136802337000.json` | Save 전 불일치 → acted 후 정확한 바이트 일치 | close 요청1회 뒤 global_windows_unavailable 반복 | sampled unchanged, activation0 | FAIL |
| `signed-textedit-saved-state-1792329453239872000.json` | 수정 후에도 정확한 바이트 일치 | 전경이 TextEdit로 바뀌어 close 미요청, 파일 보존 | CHANGED, activation1 | FAIL |
| `signed-textedit-saved-state-1792331901347518000.json` | Save 전 불일치 → acted 후 정확한 바이트 일치 | close 요청 뒤 closed 확인·artifact 제거 | sampled unchanged, activation0 | PASS, 7326ms |

원본은 `reviews/2026-10-18/`, command/evaluation/build/test 로그는
`reviews/tacto-finder-windowless-20261018/`에 있다. 세 번째 전환을 앱·사람·다른
조작 중 어느 원인이라고 단정할 수 없다. 추가 입력·강제 복원·같은 Save 재전송은
수행하지 않았다. fixture는 다른 새 자원을 사용한 각각의 코호트다.

## 닫힘 검증의 캡처 권한 의존성 수정

첫 두 실행에서 Save와 바이트 확인은 성공했으나 닫힘을 확인하지 못했다.
추가 진단은 open1회, close 요청 뒤 `global_windows_unavailable` 반복을 확인했다.
doctor는 개발 helper의 AX 권한 켜짐과 Screen Recording 꺼짐을 보고했다.
기존 lifecycle handler는 AX 목록에서 창이 사라진 경우 숫자 창 메타데이터를
읽기 전 `CGPreflightScreenCaptureAccess()`를 요구했다.

해당 선행 권한 검사를 제거했다. [CGWindowListCopyWindowInfo](https://developer.apple.com/documentation/coregraphics/cgwindowlistcopywindowinfo%28_%3A_%3A%29)는
현재 GUI 세션의 창 메타데이터를 반환하는 API이며, 이 경로에서는 제목이나 픽셀을
사용하지 않는다. 권한 상태를 optional key의 존재로 추정하지도 않는다.
API나 OS가 완전한 숫자 정보를 주지 못하면 성공을 추측하지 않고 계속 거절한다.

동일 프로세스·시작 identity, 원래 AX window/path, 두 번의 AX 목록 일치,
양수 lifecycle window ID, 유효한 CG 목록과 모든 row의 숫자 PID/ID 검증을 유지했다.
동일 window ID가 다른 Space 등에서 남아 있으면 closed가 아니다. nil·비정상 목록,
프로세스 교체, 창 교체, path 변경, 읽기 실패도 closed로 취급하지 않는다.

실제 Swift handler 테스트는 캡처 권한 확인 호출 자체를 금지한 상태에서
성공한 부재·남아 있는 창·nil/비정상 메타데이터를 구분했다. 관련54개 회귀가
통과했고 기존 signing teamYOURTEAMID로 helper를 빌드·번들링했다.
첫 수정 후 native 시도는 전경 전환 때문에 종료 입력을 보내지 않았다. 이후
통합 helper 코호트 `1792331901347518000`에서는 `closed_observed=true`,
`cleanup_status=closed`, `artifact_removed=true`, source subset 안정,
deadline 미초과를 확인했다. 이1회의 실앱 성공을 모든 앱·OS에서의 종료 보장으로
확장하지 않는다.

기존 activation witness는 임시 로그에 window 정보를 기록한다. 새 wrapper의
최종 report에는 제목 문자열을 넣지 않고 개수만 남긴다. 이는 모든 임시 로그가
한 번도 저장되지 않았다는 보장이나 crash 후 삭제 보장이 아니다.

## 남은 작업

전경 대상의 기존 prepareWindow 분기는 별도 제품 문제다. 배경 probe가 이를
건너뛴 것이 제품 수정은 아니다. 피어에 no-op 준비와 사용 중인 다른 텍스트 창의
포커스 보호를 위임했다(`req_18dfa2e9ad339bb8_46`). 이후 구현 완료 회신과
루트 리뷰(`req_18dfa3ec8c0630a0_49`), 실제 판단 함수 회귀8개와 기존 입력
harness3개 통과를 확인했다. 통합 helper도 teamYOURTEAMID로 빌드·번들링했다
(`foreground-integrated-build.log`, `foreground-integrated-bundle.log`). 설치본은
바꾸지 않았다. 통합 TextEdit 실기는 화면 잠금으로 조작 전 SKIP이었다
(`foreground-integrated-textedit-evaluation.json`). 이를 닫힘 성공으로 계산하지 않는다.

피어 전체 suite는 harness 수정 전2546개, failures12·errors18·skipped3으로
실패했다(`peer-full-suite-before-harness-fix.log`). 개별 수정 뒤의 선택 회귀와
이 전체 실행을 합쳐 전체 통과라고 표시하지 않는다.

후속 probe는 편집·Save 직전에 환경을 다시 확인하고, helper가 Save 뒤 전경 전환을
관측했다고 보고하면 빈 activation witness 결과로 이를 덮어쓰지 않는다.
그 경우 후속 close 입력도 중단한다. 관련36개 선택 회귀가 통과했다
(`textedit-interference-final-regression.log`). 54개 lifecycle 회귀와 중복되므로
둘을 합산한 새로운 독립 테스트 수로 표시하지 않는다.

TextEdit 배경 조건의 닫힘·정리1회는 통과했으며 반복·간섭 케이스는 남아 있다. 남은 파일은
과거 소유 증거와 종료 미확정 상태를 보존하며 임의 삭제하지 않는다.
저장 바이트 확인은 Save 응답과 결합된 saved-state 증거다. 다른 writer나 autosave를
배제한 생산자 인과 증거, RTF/RTFD·첨부 원본 보존, 실제 전송 수신 증거는 아직 아니다.
다른 앱·백본·반복과 같은 조건 Sky 비교도 별도다.

## 평가 기록의 후속 수정

namespace 이전 뒤12개 native/provider probe가 `nlc/*.py`만 해시해 실제
`tacto/*.py` 구현 변경을 놓치는 문제를 수정했다. 새 측정부터 두 경로를
모두 기록하며, 기존 결과의 manifest나 판정을 소급 수정하지 않는다.
각 production 경로 표현식을 실행해 실제 runtime 변경을 감지하는 검사와
관련 회귀75개가2.124초에 통과했다(`probe-manifest-related-regression.log`).
이 목록도 명시된 소스 부분집합이며 모든 의존성·OS가 고정됐다는 보장은 아니다.

동일 조건 비교 감사기의 미정의 변수도 수정했다. 역사 arm/schema는 보존했다.
관련10개 회귀와 역사16행/8쌍의 읽기 전용 재감사가 완료됐지만, 완전한 조건
동등성을 검증한 쌍은0이며 비교율을 계산하지 않는다(`historical-pair-audit.json`).
이는 새 Tacto/Sky 실행이나 새 속도·토큰 결과가 아니다. signing 테스트의
호출명도 현재 함수명으로 바로잡아7개 중6개 통과·선택적 실서명 probe1개 SKIP을
확인했다. 기존 Apple bundle 식별자는 권한·서명 연속성을 위해 유지한다.

피어 리뷰 `req_18dfa415a3cbb2b8_4a`는 probe와 중첩 분기 수정은 승인했으나,
설치 runtime을 다루는 `release_identity.py`와 `benchmark_runtime_latency.py`에도
같은 Python 소스 누락을 지적했다. 두 수집기가 이제 실제 `tacto` 구현·하위 모듈과
`nlc` 호환 shim을 함께 해시한다. legacy-only runtime은 지원하며, 패키지가
없거나 여러 개이거나 잘못된 구조이면 유효한 증거로 취급하지 않는다.
실제 수집 함수를 실행하는 회귀3개가 통과했다
(`installed-runtime-manifest-regression.log`). 설치본의71개·개발본의109개 Python
소스도 각각 읽어 두 경로의 구현 파일이 포함됨을 확인했다
(`runtime-python-source-evidence.json`). 파일 개수는 실행 과제 수나 성공률이 아니다.
이 후속 변경도 피어 리뷰 `req_18dfa449be95b970_4b`에서 승인받았다. 이 수집만으로
동명 패키지의 진위나 전체 앱 서명을 검증했다고 판단하지 않는다.

README에서 새 검증 기록으로 연결하고 `runtime_value_missing`의 미실행 의미를
문서에 추가해 문서 회귀6개도 통과했다. 후속 원인 확인에서 upload CLI 실패는
fixture import가 아니라 전역 별칭이 선택한 이전 설치본의 `unknown command
'web-upload'`였다. 테스트가 현재 checkout의 서명 launcher를 실행하도록 고쳐
전송1회·독립 수신 확인·재시작 뒤 중복 전송 차단을3.429초에 확인했다
(`web-upload-signed-restart-regression.log`). 설치본 업데이트 검증은 아니다.

없는 역사 커밋에 의존하던 observation walker 테스트도 저장소에 포함된 scalar
reference로 교체했다. 실제 Swift batch walker와의 출력·AX 읽기 수 비교가
1.151초에 통과했다(`scalar-observation-oracle-regression.log`). 이는 독립적으로
유지하는 scalar 테스트 oracle이며 역사 빌드를 복원한 실측이라고 주장하지 않는다.
전체 회귀를 다시 실행하기 전까지 전체 통과로 보고하지 않는다.

## Finder 폴더 경로 증거의 별도 격차

후속 소유 창의 읽기 전용 검사에서 AXDocument는 noValue, AXURL은 unsupported였다.
AXProxy는 있었지만 URL·Value·Document·Filename은 unsupported이며 제목은
폴더 leaf 이름뿐이었다. 하위86개 노드에서 URL 속성7개를 보았지만 소유 폴더
경로와 일치하지 않았다. 경로·제목 문자열은 이 요약에 기록하지 않는다.

피어 검토 `req_18dfa46f0ffb47b0_4d`, `req_18dfa4912bb32668_50`에서 항목 URL의
parent만으로 current folder를 증명하는 설계를 반례 검토했다. Recents·검색·태그가
같은 부모의 항목 하나만 표시할 수 있으므로 sentinel을 추가해도 충분하지 않다.
이 근거만으로 folder token을 구현하거나 New Folder를 실행하지 않았다. 일반 메뉴
기능을 모두 거절한다는 의미가 아니라, 이 과제의 정확한 목적지 증거가 남았다는 뜻이다.
후속 과제는 보기 모드별 위치 증거와 New Folder 활성 상태를 검증하고, 명시적
권한 경로가 필요하면 그 추가 권한·사용자 간섭을 구분하는 것이다. 사후 소유 폴더의
새 child 영수증도 사전 목적지 확인을 대신하지 않는다.

## 실패한 readiness 읽기의 구분

피어 분석 `req_18dfa5ed8db8daa8_58`에서 기존 원본의 `unreadable`1개만으로는
batch 호출 실패와 AXRole 필드 실패·잘못된 타입을 구분할 수 없음을 확인했다.
피어가 격리 scratch에서 구현한 패치 `req_18dfa6089fd4e2e8_59`를 루트가 검토·적용했다.
첫 fatal 읽기 실패에 한해 단계·속성·출처(scalar/batch_field/batch_call)·kind·depth·visit·
AX 오류 번호를 기록한다. 제목·값·경로·pid는 넣지 않는다. 정상적으로 허용한 optional
noValue를 먼저 실패로 고정하지 않으며, 진단 필드는 Python에서도 닫힌 집합으로 검사한다.

모달·sheet 검증, 거절 메시지, deadline·identity·트리 검사를 유지했고 재시도나
scalar fallback을 추가하지 않았다. 실제 scope·batch·traversal 함수에 오류를 주입한
테스트와 관련28개 모듈86개 회귀가53.578초에 통과했다
(`readiness-diagnostic-native-regression.log`). 동일 signing team으로 helper를
빌드·번들링했다(`readiness-diagnostic-build.log`, `readiness-diagnostic-bundle.log`).
scratch에서 실패하던 Finder 사전 조건 검사는 루트 통합에서는 통과했다.

처음 통합 명령은 존재하지 않는 테스트 모듈명을 포함해 import error1건이었다
(`readiness-diagnostic-integrated-regression.log`). 정확한 실제 모듈 집합으로 실행한
후속86개 통과와 구분하며 원본은 보존한다. 이 패치와 첫 pair budget 후속 수정은
앞선 전체2560개 통과 코호트 이후이므로 그 결과를 그대로 전이하지 않는다.
현재 세션은 다시 잠겨 있어 실제 Finder의 실패 kind를 새 helper로 재측정하지 못했다.
