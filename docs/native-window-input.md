# 네이티브 창별 입력의 현재 지원 범위

2026-10-01 현재 일반 클릭·문자·스크롤 경로는 AX 입력을 사용한다.
Python driver와 signed helper는 관측한 입력 요소와 배경 창에 결합한 탐색 키를 지원한다.
signed Swift helper와 Python driver는 추가로 배경 `prepare_window`에
PID-addressed 이벤트 경로를 제공한다. 과거 [창별 이벤트 실측](../reviews/2026-09-19/window-dispatch-progress.md)은
해당 당시 구현의 증거이며 현재 backend의 지원 보장이 아니다.

## 클릭과 좌표

관측한 요소의 클릭은 대상 앱의 AXPress를 실행한다. 지원되지 않는 요소에
합성 마우스 이벤트를 보내거나 전역 포인터를 이동하는 fallback은 없다.
`click`의 `point`는 선택한 창의 좌상단 기준 좌표다. 대상 앱의 AX hit test에서
요소를 찾고 그 요소의 PID와 창 소유 관계를 확인한 뒤 AXPress를 사용한다.
다른 전경 앱의 요소가 나오도록 system-wide hit test를 쓰지 않는다. 여러 창이면
명시적으로 창을 선택해야 하며 스크린샷 픽셀은 보고된 배율에 따라 창 좌표로
변환해야 한다. AX hit test가 누를 수 있는 요소를 제공하지 않으면 거절한다.

소유 canvas fixture에서 캡처 이미지로부터 green 좌표를 찾은 후 AXPress로
클릭하고 독립 label과 카운터 0→1을 읽었다. 2026-10-01 signed 경로에서
전경 PID·포인터·focus identity의 프로세스 표본도 동일했다. 고정 이미지 분석
검사이며 모델 계획, 전경 caret, 동시 사용자 입력이나 모든 앱 지원의 증거는 아니다.

내부 Python `window_point` binding과 `dispatch_point` 부품은 별도로 구현했다.
관측한 표준 AXWindow와 PID/start·CG ID·정확한 창 geometry·창 상대 point를
결합한다. 클릭 가능한 AX 요소를 만들어 추정하지 않고 창의 실제 routing과
modal/minimized 상태를 확인한 뒤 해당 PID/window에 mouse-down/up 하나만 보낸다.
activation 이벤트는 이 point pair에 포함하지 않으며 창 준비는 별도 동작이다.
caller의 관측/context·중단·기한과 사용자 입력 상태를 매 전송 전후 확인한다.
전송 시도 뒤 오류는 unknown이며 나머지 이벤트나 복구 입력을 자동으로 보내지 않는다.

실제 소유 opaque canvas의 hit test는 AXWindow/AXRaise만 제공했고 AXPress가 없었다.
내부 pair 뒤 receiving NSView의 down/up 각1, green1, 다른 창0, routeErrors0을
확인했다. 비선택 창은0개 이벤트로 거절했다. 292개 입력 표본은 동일했으며
최대 간격은0.200259초였다. 이 최초 시험은 내부 부품 검증이었다. 이후 direct/signed
driver의 별도 `click_point` raw action에 연결했다. 아래 공개 경로 계약을 따른다.
일반 앱·이미지/모델 계획과 flow/general 연결은 아직 증명하지 않는다.
일반 `click`의 AX fallback 거절 동작은 그대로다.

### 명시적 `click_point` raw action

`action.click_point`, `click_point.window_bound`, `click_point.host_authorization`을
제공한다. action은 `{"name":"click_point","element_index":window_index,
"point":{"x":280,"y":120}}`이고, element_index는 최신 관측 맵의 **AXWindow**다.
point는 그 창의 좌상단 기준 화면 points다. 숫자는 유한·비음수여야 하며 bool이나
다른 필드를 허용하지 않는다. 실제 창 밖 좌표는 전송 전에 거절한다.

`driver.act(..., risk_class="preapproved", approved=True)` 또는 승인된 confirm
범위를 호스트가 명시해야 한다. risk none이나 truthy integer/string을 승인으로
간주하지 않는다. 이는 호출자의 작업 승인 계약이지 pixels의 의미/변경 부재나
결과를 증명하는 API가 아니다. helper client는 새 capability가 없는 이전 helper에
요청을 보내지 않는다. launcher/helper도 genuine boolean coordinate_authorized를
요구하고 다른 action의 해당 필드는 거절한다. 같은 caller-bound 5초 입력 기한을
전달하고, target routing·관측·프로세스·창 geometry를 각 이벤트에서 확인한다.

사용자 전경 앱에는 이 경로를 보내지 않는다. 창 준비를 암묵적으로 실행하지 않으며
선택된 배경 창의 down/up pair2만 보낸다. 반환은 `unverified: observe_effect`,
`reobserve_required: true`다. caller가 새 관측으로 실제 결과를 확인해야 한다.
document_token 범위 관측에서는 아직 허용하지 않는다. Flow/general의 모델 action
목록에도 연결하지 않았으므로 이 raw API의 성공을 자율 시각 자동화로 합산하지 않는다.

## 클릭 시작 기한

Python helper transport는 클릭에 `click_deadline_uptime`을 전달한다. 남은
작업 예산과 5초 중 짧은 값을 사용하고, 이미 만료됐으면 실행 전에 거절한다.
launcher와 helper는 boolean·비유한 값·비양수와 지원되지 않는 action의 필드를 거절한다.
공유 `click_deadline_uptime` 필드는 click/prepare_window/press_key/window_input만 받으며 탐색 키에도
호출자 기한을 전달한다. helper는 `action.press_key.deadline_uptime`를 선언한다.
`action.click.deadline_uptime` capability가 없는 구형 helper는 연결을 거절한다.

값은 같은 Mac의 단조 시계 기준 절대 초다. 현재 환경의 monotonic과 Swift
systemUptime은 [실측](../reviews/2026-09-19/click-deadline-clock.json)에서 같은
시간축을 확인했다. 다른 호스트에 그대로 전달할 수 없다. 이는 새 입력의
시작 기한이며 진행 중인 AX 호출이나 OS 작업의 강제 취소를 보장하지 않는다.
general 루프는 driver.act 호출에도 남은 작업 기한을 전달한다.

## 명시적 창 준비 (`prepare_window`)

전경 앱 안에서는 관측된 창의 settable `AXMain`과 `AXFocused`에 True를 쓴다.
전경 PID는 NSWorkspace 캐시 대신 공개 HIServices API로 읽고 실패하면 거절한다.
각 AX 쓰기 직전과 완료 판정 전에 전경·중단·작업 기한을 다시 확인한다.
이미 쓰기를 시도한 후 오류가 나면 `unknown`으로 보고하며 재시도가 안전하다고
주장하지 않는다. 이 전후 확인도 마지막 검사와 AX 쓰기를 원자적으로 만들지는 않는다.

`window_reports_key`는 AXMain/AXFocused의 관측 결과다. AppKit의 실제
isKeyWindow나 메뉴 활성화의 증명이 아니므로 새 observe와 menu_state로
필요한 결과를 확인해야 한다.

두 backend의 `action.prepare_window.background`는 관측된 표준 창과
실행 인스턴스·CG window ID·geometry·빈 titlebar hit-test를 결합한다.
AppKit activation 이벤트와 검증된 mouse-down/up pair를 해당 PID로만 전송한다.
각 backend의 단일 post 경계에서 매 이벤트 전후 전경 PID·포인터·focus AX
identity를 비교하고 창·관측·프로세스를 다시 확인한다. 입력 상태 변화, 중단,
기한 초과 또는 재검증 실패 시 추가 전송을 멈춘다. 전송 시도 이후 오류는
`effect: unknown`이며 재시도가 안전하다고 주장하지 않는다.

각 이벤트 직전에는 현재 상태를 그 직전/직후뿐 아니라 작업 최초 입력 상태와도
비교한다. 두 이벤트 사이에 관측 가능한 포인터·focus 변경이 생겼으면 다음 이벤트를
보내지 않는다. Python/signed의 실제 dispatch 함수에 이런 변경을 주입해 첫 시도
후 추가 전송0을 확인했다. 검사 사이에 변화했다가 돌아오는 상황은 여전히 배제하지 못한다.

private window-location SPI와 AX window-ID ABI가 필요하며 지원되지 않으면
입력 전에 거절한다. 관측 AX 호출은 개별 timeout을 남은 기한 내 최대0.5초로
설정·복원하고 사용자 focus 조회는 최대0.25초를 사용한다. 모든 시스템 호출의
강제 취소나 표본 사이 변화 부재를 보장하지 않는다. 사용자 포인터·focus가
관측 사이 달라져도 보수적으로 중단하며 그 원인을 사용자/헬퍼로 추정하지 않는다.

반환은 항상 `unverified: observe_effect`, `reobserve_required: true`다.
전송 성공은 key window나 메뉴 효과의 성공이 아니다. owned signed 시험에서
실제 AppKit key window와 Direct1/Responder1/MakeKey0을 독립 호스트 AX 값으로
확인했고 입력 표본은 유지됐다. 첫 실패에서는 이벤트 하나 후 입력 상태 변화로
중단했으며 원시 실패 기록을 보존했다. 실제 앱·캐럿·동시 입력 검증은 남아 있다.

## 창·입력 요소에 결합한 탐색 키 (`press_key`)

`action.press_key`, `press_key.no_escape`,
`press_key.element_bound`를 제공한다. Tab·shift+Tab·Up·Down·Left·Right만 허용한다.
signed helper는 추가로 `press_key.return_in_go_to_folder`에서 Return을 "폴더로 이동"
시트의 경로 필드(`GoToWindow` 안의 `PathTextField`, 보안 필드 아님)에만 보내며,
이 대상 확인을 첫 이벤트 직전에 다시 한다. window_input 경로는 Return을 받지 않는다.
Escape·문자·단축키는 거절하며 사용자 전경 앱에 이 경로의 키를 보내지 않는다.
관측의 element_index를 필수로 유지한다. general도 이 capability에서는 후보와
role/label 기대값을 키 전송 전 검사에 사용한다. 읽기 label 같은 비입력 후보에는
탐색 키를 제공하지 않는다.

표준 창의 PID/start·CG ID·geometry·요소 중심 hit-test를 결합하고 앱의 실제
AXFocusedWindow와 AXFocusedUIElement identity·창 ancestry를 읽는다. 키를 받기
전 요소는 관측한 바로 그 입력 요소여야 한다. 창 준비나 focus 설정을 암묵적으로
실행하지 않는다. AXFocusedWindow가 맞아도 배경 창의 AXFocused flag는 false일
수 있으므로 그 flag를 실제 key window/입력 대상 판정으로 대신 쓰지 않는다.
Tab 뒤 key-up은 같은 창의 새 입력 요소로 갈 수 있다. 다른 창으로 바뀌면 거절한다.

[공개 NSEvent key constructor](https://developer.apple.com/documentation/appkit/nsevent/keyevent%28with%3Alocation%3Amodifierflags%3Atimestamp%3Awindownumber%3Acontext%3Acharacters%3Acharactersignoringmodifiers%3Aisarepeat%3Akeycode%3A%29)로
window number를 지정한 down/up을 먼저 만든다. 두 이벤트의 CG/AppKit type,
window number, key code, characters, modifier flags와 non-repeat를 모두 읽어
검사한 뒤 같은 PID post 경계를 사용한다. 방향키에는 AppKit function modifier를
명시해 CG/AppKit의 readback이 일치하게 한다. 모든 이벤트 전후 사용자 foreground
PID·pointer·focused AX identity와 대상 binding/context를 다시 확인한다.

완전한 pair도 `unverified: observe_effect`, `reobserve_required: true`다. general은
정확한 window_key route와 posted_events=2일 때만 재관찰로 진행하고 전송을 결과
성공으로 확정하지 않는다. flow는 입력 selector·binding·탐색 키·명시적 값
postcondition을 실행 전에 요구한다. 결과 timeout이나 일부 전송 실패 뒤 같은
키를 다시 보내지 않는다. 중단 뒤 보정 key-up도 보내지 않으며 대상 앱의 부분
입력 상태는 unknown이다. 순차 검사·OS 호출은 원자적 취소나 표본 사이 상태
보존을 보장하지 않는다.

소유한 두 창의 custom navigation control에서 6개 탐색 키의 실제 위치/first
responder 변화, 다른 창과 잘못된 입력 요소의 전송 전 거절, window event down/up
카운터를 독립적으로 검증했다. 일반 루프의 키 뒤 실제 값 읽기와 명시적
postcondition을 둔 재생도 Python과 signed helper에서 각각 실측했다. signed launcher도
입력 요소 index를 필수로 요구하며, helper가 같은 CF identity·ancestry·이벤트와
전송 전후 상태를 검증한다. 일반 TextEdit/다른 앱·실제 사용자 caret/동시 typing은
별도 검증이 필요하다. 모델 선택 검사는 독립된 owned 과제로 기록한다.

## Signed 메뉴 상태 조회

`menu_state` capability는 `{type: "menu_state", target: 앱, path: [메뉴, 명령]}` 읽기 요청을 지원한다. 각 경로는 제목 또는 지역화된 제목 목록이며 최대 6단계, 단계당 대안 8개, 제목 200자로 제한한다. launcher와 helper가 요청 필드를 검증한다. 실행 중인 앱만 조회하며 메뉴 실행·창 준비·앱 실행을 하지 않는다. direct Python 조회도 `launch=False`를 사용한다.

helper는 AXEnabled의 실제 boolean만 반환하고, 읽을 수 없으면 `menu_state_unavailable`로 거절한다. 조회 후 Esc latch와 PID/프로세스 시작 시각을 재확인한다. 반환값은 조회 시점의 상태이므로 이후 menu act의 검증을 대체하지 않는다. 메뉴 상태 조회 자체가 이전 동작의 settle 대기를 수행하지는 않는다. prepare_window 이후 새 observe를 먼저 호출하는 순서를 유지한다.

`target_ax_frontmost`는 대상 앱이 노출한 AXFrontmost의 boolean 또는 미관측 null이다. 시스템 전경 앱 판정이 아니며, 이 값만으로 메뉴 비활성의 원인을 확정하지 않는다. 배경 앱은 메뉴 검증 시점에 따라 disabled를 반환할 수 있다. AX 메뉴 조회도 대상 앱의 코드 실행을 유발할 수 있으므로 무부작용을 보장하는 API는 아니다.

메뉴 경로의 단계·대안·제목 길이를 제한하고, 양쪽 메뉴 조회에서 각 AX 속성 읽기 전후 중단·기한을 검사한다. 조회당 속성 읽기는 5,000회, 반환된 자식 목록은 5,000개까지 허용하며 초과 시 `menu_read_budget_exceeded`로 거절한다. 로컬 준비/조회 기한은 최대 5초이며 Python direct는 더 짧은 상위 작업 기한도 따른다. helper의 로컬 5초는 IPC 도착 전 대기시간을 포함한 전체 요청 기한은 아니다. 기존 동기 AX 호출 하나를 강제로 중단하는 기능도 없다. 검사는 새 속성 읽기를 제한하고 잘못된 성공 응답을 막지만, 진행 중인 동기 AX 호출 지연의 상한을 보장하지는 않는다. 시간 초과는 `menu_read_timeout`, 중단은 `aborted_by_user`다. 이 좁은 메뉴 조회는 별도의 overlay를 생성하지 않는다; observe/act의 표시 동작과 구분한다.


AX 조회 자체의 오류도 구분한다. AttributeUnsupported/NoValue만 미관측 값으로 처리하고 다른 AX 실패는 `menu_read_failed`로 반환한다. 응답하지 않는 앱을 `menu_not_found`로 오보고하지 않는다. signed 메뉴 막대의 CF 타입도 확인해 잘못된 값의 강제 캐스트로 helper가 종료되는 경로를 막았다.

각 실제 AX 호출에 객체별 messaging timeout을 min(1초, 남은 조회 기한)으로 설정하고 호출 후 해당 객체의 override를 0으로 되돌린다. 설정/해제 실패는 각각 `menu_timeout_unavailable`/`menu_timeout_reset_failed`로 거절한다. 시스템 전체 timeout은 바꾸지 않는다. 자식 목록은 개수를 먼저 조회해 5,000개 초과를 거절하고, 지정 범위만 복사한 뒤 개수를 다시 확인한다. 개수나 반환 길이 변화, 첫 개수 조회 후 목록이 사라지는 경우는 `menu_state_changed`다. 진단 메시지를 위해 제목을 다시 읽지 않으며, 첫 메뉴 래퍼를 찾으면 나머지 형제를 탐색하지 않는다. 같은 개수를 유지한 내용 교체까지 원자적으로 감지하는 것은 아니다. enabled와 AXFrontmost는 순서대로 읽은 관측값이며 원자적 쌍이나 비활성 원인의 증거는 아니다.


[Apple timeout 계약](https://developer.apple.com/documentation/applicationservices/1459345-axuielementsetmessagingtimeout)에 따라 동등한 AX 객체라도 timeout 설정은 공유된다고 가정하지 않는다. SDK의 AXUIElement.h에서 GetAttributeValueCount/CopyAttributeValues의 범위 복사 계약도 확인했다. 개수 조회의 대상 앱 내부 구현 비용이나 OS 장애까지 제한하는 보장은 아니다.

[응답 정지 실측](../reviews/2026-09-19/menu-unresponsive-direct-20260920-a/report.json): 소유 AppKit 자식 프로세스만 SIGSTOP한 상태에서 Python direct 메뉴 조회가1.008초에 `menu_read_failed`로 반환되고, SIGCONT 후 정상 조회로 복구됐다. 비상8초 복구 타이머는 사용되지 않았다. exact child 정리와 독립 입력 감사를 포함한다. 이 검사는 signed helper의 응답 정지 실측으로 합산하지 않는다.


## General의 명시적 창 준비

2026-10-01의 추가 owned 실험에서는 프로세스에 주소를 지정한 AppKit activation
이벤트와 창별 titlebar mouse pair 뒤 실제 AppKit key window가 만들어졌고,
responder 메뉴의 독립 카운터가 0→1로 바뀌었다. 두 신규 프로세스에서 같은 결과를
확인했다. 이어 기존 native binding/event builder로 창·PID/start identity·geometry·
빈 titlebar hit-test와 event readback을 검증한 별도 실험도 통과했다. 매 post 전후
fresh foreground PID를 확인했고 ProcessInputAudit의 PID·포인터·focus 표본은
유지됐다. 실제 caret/동시 typing과 표본 사이의 변화는 검증하지 않았다.

이 실험 client 결과와 별도로 signed helper와 Python driver의 production 경로를
각각 owned fixture에서 검증했다. Python 경로도 두 신규 프로세스에서 실제 key
window와 Direct/Responder counter를 독립적으로 확인했다. window-location은 OS-dependent private CG
SPI다. 공개 [AppKit activation subtype](https://developer.apple.com/documentation/appkit/nsevent/eventsubtype/applicationactivated)
문서는 이벤트 종류를 설명하며, 사용자 입력 보존을 보장하지 않는다. 다른 앱과
캐럿·동시 입력은 검증해야 하며 일반 클릭의 AX 경로는 그대로다.

소유 fixture에서 Claude/Codex가 창 준비를 계획하고 새 관측의 실제 key-window
상태를 읽는 일반 루프를 각1회 검증했다. 이어 두 모델이 Direct → 창 준비 →
Responder 순서를 선택한 별도 복합 과제도 각1회 통과했다. 후자는 창 준비 안내를
추가한 goal과 normalized 메뉴 adapter를 사용하므로 이전 SDK 비교와 같은 조건의
backend parity 증거로 합산하지 않는다.

Window 후보는 action.prepare_window capability가 있을 때만 제공하고 클릭 후보와 함께 앞쪽에 배치한다. ask validator는 can과 마지막 행동 조건을 검사한다. general은 role/capability/can을 다시 확인하며 명시 disabled는 기존 공통 게이트로 거절한다. AXEnabled가 없는 표준 창에는 enabled=true 기대값을 보내지 않고 native 준비 상태 검사를 적용한다.

정확히 prepare_window/window_prepare + acted/observe_effect/reobserve_required 응답일 때만 이전 batch와 기대값을 폐기하고 재관찰한다. 입력 이력은 dispatched, certainty는 unknown으로 유지한다. flow replay도 창 준비와 명시적인 후속 검증을 지원한다. 검증되지 않은 준비 단계를 지워 레시피로 승격하지 않는다.

읽기 이력은 원래 값을 제공하며 큰 값과 누적 이력은 호출별 참조로 나누어 조회한다. 원래 후보 identity와 입력 예산을 유지한다. [문맥 paging 계약](context-paging.md)을 참고한다.


## AX 입력 요소가 없는 창의 키와 Unicode (`window_input`)

Python과 signed helper는 `action.window_input`, `window_input.window_bound`,
`window_input.host_authorization`을 제공한다. 최신 관측의 AXWindow index에
`{"name":"window_input", "element_index":index, "key":"Right"}` 또는
`{"name":"window_input", "element_index":index, "text":"A😀한글"}`만 전달한다.
명시적인 호스트 승인(`approved is True`, confirm/preapproved)이 필요하며 input
element가 없는 창의 의미와 가역적 효과는 호스트가 확인해야 한다. 모델 출력이나
이미지는 승인 근거가 아니다. scoped document token에서는 이 경로를 거절한다.

키는 Tab·shift+Tab·방향키 여섯 개다. 텍스트는 최대64 UTF-16 units이며 빈 값,
control 문자·줄바꿈·AppKit navigation private characters를 거절한다. Unicode
문자열을 CGEvent에 설정한 뒤 raw UTF-16과 NSEvent의 창·event type·keyCode·flags·
autorepeat를 검사한다. keyDown의 reflected characters는 본문과 정확히 같아야 한다.
keyUp의 reflected characters는 AppKit의 physical key 해석(code0)을 유지하며
raw Unicode는 양쪽 모두 검증한다. 수신 view의 실제 텍스트 반영은 별도로 확인한다.

대상 standard window의 native CF identity·CG ownership/geometry·PID/start와
앱의 AXFocusedWindow를 매 이벤트 전후 재확인한다. AXFocusedUIElement를
가짜 입력 요소로 취급하지 않고 포커스를 설정하지 않는다. 창 준비와 canvas focus
클릭은 호스트가 별도로 수행하고 receiving readiness를 확인해야 한다. 이벤트는
PID/window에만 보낸다. 같은 shared input deadline과 사용자 전경·포인터·focus
검사를 사용하며 partial/unknown 뒤 다음 이벤트나 보상 keyUp을 보내지 않는다.

완전한 pair도 `unverified: observe_effect`, `reobserve_required: true`를 반환한다.
호스트가 수신 상태를 별도로 읽어야 완료다. live trace에는 입력 text가 기본 redaction
규칙으로 보호되지만 TraceDriver는 이 capability를 제외하고 replay를 거절한다.
raw host API와 명시적 input_actions를 선언한 GeneralVisual/Flow의 image proposal에
key/text를 연결했다. 기본 시각 scope는 click만 허용한다. 일반 앱·IME composition·단축키·selection replacement·저장 결과와
사용자 동시 타이핑 보존을 이 owned fixture 검증으로 보장하지 않는다.
