# 네이티브 창별 입력의 현재 지원 범위

2026-10-01 현재 일반 클릭·문자·스크롤 경로는 AX 입력을 사용한다.
Python driver와 signed helper는 관측한 입력 요소와 배경 창에 결합한 탐색 키를 지원한다.
signed Swift helper와 Python driver는 추가로 배경 `prepare_window`에
PID-addressed 이벤트 경로를 제공한다. 과거 창별 이벤트 실측(비공개 로컬 기록: `reviews/2026-09-19/window-dispatch-progress.md`; 공개 원본 미포함)은
해당 당시 구현의 증거이며 현재 backend의 지원 보장이 아니다.

## 클릭과 좌표

관측한 요소의 클릭은 대상 앱의 AXPress를 먼저 실행한다. signed helper에서
AXPress가 없는 행·버튼(`click.window_bound_element`)과 `double_click`(AXOpen이
없을 때)은 그 요소의 중심을 대상 앱 AX hit test로 다시 확인한 뒤 해당 PID·창에만
mouse 이벤트를 보낸다. 전역 포인터는 이동하지 않으며 save panel 토큰이 붙은 클릭은
거절한다. 실측 범위는 아래 signed_element_click 항목을 참고한다.
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
일반 `click`의 창 단위 요소 fallback은 위 요소 hit test 경로만 사용한다.

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
systemUptime은 실측(비공개 로컬 기록: `reviews/2026-09-19/click-deadline-clock.json`; 공개 원본 미포함)에서 같은
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
시트의 경로 필드(`GoToWindow` 안의 `PathTextField`, 보안 필드 아님)에만 보낸다. 이것은
위 탐색 키 경로가 아니라 별도 경로(`submitReturnInSheet`)다. 탐색·window_input·point는
여전히 "어느 창에도 sheet 없음"을 요구하고 Return을 받지 않는다. 이 경로의 준비 조건은
앱 전체 노출 트리에 sheet가 정확히 하나(그 필드의 조상 sheet, CFEqual)이고 modal이 없으며
읽기 실패가 없는 것이다. 보내기 직전에 앱의 focused window가 그 sheet, focused element가
그 필드인지 다시 확인하고 keyDown 하나만 보낸다(소유 fixture 실측: keyDown만으로 확정되고,
AppKit은 창 번호와 무관하게 키 창인 sheet에 전달한다 — `reviews/2026-10-08/return-key/`).
정상 Return은 sheet를 닫으므로 keyUp은 보내지 않는다. 영수증은 `input_route:
window_submit_key`, `posted_events: 1`, `reobserve_required`이며 완료 판정은 재생의 정확한
`equals` postcondition(예: 이동한 폴더의 창 제목)으로만 한다.
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

응답 정지 실측(비공개 로컬 기록: `reviews/2026-09-19/menu-unresponsive-direct-20260920-a/report.json`; 공개 원본 미포함): 소유 AppKit 자식 프로세스만 SIGSTOP한 상태에서 Python direct 메뉴 조회가1.008초에 `menu_read_failed`로 반환되고, SIGCONT 후 정상 조회로 복구됐다. 비상8초 복구 타이머는 사용되지 않았다. exact child 정리와 독립 입력 감사를 포함한다. 이 검사는 signed helper의 응답 정지 실측으로 합산하지 않는다.


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

## 창 준비의 bounded 진단

서명 helper의 창 준비 응답은 `preparation_diagnostic`을 포함한다.
`phase`는 identity/context/binding/dispatch 또는 foreground_write/revalidate다.
background `reason`은 readiness_deadline/readiness_nodes/observation_deadline,
ax_timeout_unavailable/ax_timeout_reset_failed/titlebar_point_unavailable,
modal_or_unverifiable/identity_changed, dispatched_unverified/unclassified_refusal
중 하나다. 전경 거절은 foreground_refusal로 분류한다.

`elapsed_ms`는 해당 handler 안의 경과 시간이며0~600000으로 제한한다.
background `scoped_ax_operations`는 timeout 설정 이후 실제 시작한 read scope
작업 수다. scope 밖 PID/AX 검사나 모든 AX 호출·고유 노드 수를 세는 값은 아니다.
새 진단 필드에는 창 제목/경로/문서 본문/임의 exception 문자열을 복사하지 않는다.
기존 detail 응답과 별개이므로 외부 report에는 이 닫힌 진단만 보존해야 한다.

이 필드는 실행 권한·재시도 승인·완료 증거가 아니다. 기존 readiness/modal/
identity/deadline 검사와 attempted=0의 not_started, attempted>0의 unknown
분류를 유지한다. scoped read counter도 수행 여부를 바꾸지 않는다.

실제 background helper 함수와 read scope를 추출해 실패 경계를 주입한 Swift
검사 및 기존 window readiness/dispatch 검사7개가 통과했다. 새 helper 빌드와
번들의 기존 designated requirement 서명 검증도 통과했다. signed owned AppKit
eval1/1 PASS에서 background dispatch 진단(scoped AX552회,1015ms)을 수신했고,
독립 responder/menu counter와 소유 자식 회수도 확인했다.
`reviews/2026-10-01/signed-background-window-1791616831661391000.json`이
원시 probe다. 폴더 날짜는 기존 scenario의 고정 경로이며 시행은2026-10-10이다.
상위 eval report는 `reviews/2026-10-10/window-preparation-diagnostics-eval.json`이다.
전경 입력 표본 UNCHANGED_SAMPLED는 caret/동시 typing 보존이나 범용 Finder
지원 완료의 증거가 아니다. Finder 실패를 새 진단으로 재현하는 작업은 남아 있다.

CLI replay는 검증된 진단을 `preparation-diagnostic: <JSON>` 줄로 출력한다.
ReplayResult의 별도 bounded 목록에64건까지 보존하므로 후속 postcondition
timeout이 원래 준비 응답의 진단을 덮어쓰지 않는다. 진단만 보존된 거절을
dispatch event로 바꾸지 않으며 결과 판정에 사용하지 않는다.
`tacto.preparation_diagnostic`은 닫힌 field set/phase/reason과 숫자 범위를 검사한다.
외부 문자열·bool counter·추가 필드는 버린다. 비교 runner는 completed Tacto
command output의 marker만 수집하고 전체 output/줄/건수 상한을 적용한다.
reported_preparation_diagnostics는 tool 보고이며 native provenance 인증이 아니다.

실제 Finder 단독 시행 `reviews/2026-10-10/product-comparison-20261010-162643.jsonl`
은256초 뒤 새 directory 한 개로 PASS했으나 진단 배열은 비어 있었다.
따라서 어떤 실패를 극복했는지나 특정 원인 해결을 이 시행으로 확정하지 않는다.
후속 코드 감사에서 replay의 postcondition timeout 시 진단 누락 및 `--trace`
밖에서의 조건부 지역 json import 예외 경로를 수정했다. no-trace CLI를 실제
replay+주입 driver로 실행하는 회귀를 포함해 관련75개 PASS다. 이 후속 수정은
위 실제 시행의 frozen source에 포함되지 않는다.

## Readiness 속성 batch와 node-local 수명

각 DFS 노드의 AXRole/AXModal/AXChildren은 scoped CopyMultiple 한 번으로 읽는다.
개별 AXError는 유지한다. unsupported/notImplemented batch만 기존 strict 단건
조회로 fallback하며 timeout/기타 실패·잘못된 길이·error wrapper는 거부한다.
임시 packet은 해당 노드 iteration이 끝나거나 throw하면 버린다. 전체 tree·nodes/
depth/deadline 검사와 순회 후 top의 role/modal/sheets 재조회는 유지한다.
Finder desktop의 역할이 바뀌면 이전 AXModal 예외를 재사용하지 않는다.

기존 adversarial tree corpus를 실제 batch decoder/DFS로 실행해 단건 경로와
동일한 거부 결과를 확인했다. packet 오류·leaf top의 late modal·Finder desktop
role change 회귀와 기존 readiness/dispatch/진단 검사9개가 통과했다.
helper와 bundle의 기존 서명 requirement 검증도 통과했다.

실제 Finder `reviews/2026-10-10/finder-preparation-batch-directed.json`은 binding의
readiness_nodes,455ms/scoped AX193회로 거부됐다. source_changed=false다.
이 프로파일은 초기 단건 검사와 앱 tree 상태가 다르므로 속도 향상률이 아니다.
별도 signed AppKit eval 두 건은 binding을 통과했지만 dispatch의 기존 사용자
포커스 guard에서 not_started/attempted0으로 거부됐다. 실패 artifact는
`reviews/2026-10-10/window-preparation-batch-eval.json`과
`window-preparation-batch-eval-retry.json`에 보존한다. 두 번째 eval은 Finder
probe의 실행 시간과 겹쳤으므로 독립 환경 성능 비교로 사용하지 않는다.
소유 fixture 자식은 모두 회수했다. 후속 role 재조회 보강은 이 GUI trial 뒤
추가됐으며 최종 소스와 바이너리에 반영했다. 이번 변경만으로 receiving 성공,
대규모 트리의 node budget 또는 Finder 지원 완료를 주장하지 않는다.

## 큰 트리의 용량과 첫 입력 전 시간 여유 (10/11)

readiness 기본 노드 상한은 전체 창을 합쳐 20,000개다. 각 노드의 전체
AXRole/AXModal/AXChildren 검사, 깊이64, shared input deadline 최대5초,
순회 뒤 windows/role/modal/sheets 재조회는 유지한다. enqueue 시 남은 예산에서
대기 중인 sibling도 빼므로 pending 노드 전체가 상한을 넘지 않는다.
Swift 함수에서 명시적으로 더 작은 maxNodes를 지정하는 호출도 유지한다.
이 용량 확대는 가시 행이나 Finder desktop의 깊은 검사를 생략하지 않는다.

첫 이벤트를 보내기 직전에 남은 모든 이벤트의 검사 시간을 추정한다.
validate/routing/user input read/context의 최대 관측 시간을 사용해
남은 validate와 routing 각각2N-1회, input read3N-1회, context12N회를 계산하고,
합계의2배와50ms를 남길 수 없으면 validation_headroom/not_started로 거절한다.
현재 첫 post 이후 ready 호출은4회와 후속 이벤트당8회로8N-4회이며12N은
여유를 포함한 상한이다. 단일 Return submit은 post 뒤 validate/routing을 하지
않으므로 input read1회/context1회만 남긴다. 이벤트별 기존 검사는 모두 실행한다.
이 수치는 미래 지연의 상한을 증명하지 않는다. 이후 급격한 slowdown·context
변경·중단·전송 오류는 기존처럼 attempted>0이면 unknown이며 자동 재시도하지 않는다.

실제 Swift 단건/batch 경로에서6,000개 child와 마지막 modal/sheet/deadline,
20,000개 경계, 더 작은 명시적 cap, cycle/depth, pending sibling 예산을 검증했다.
고정 지연 validation/routing/input/context에서는 첫 post0, 빠른 double click은
4이벤트, 첫 post 뒤 latency spike는 attempted1을 확인했다. 최종 관련47개/
37.142초 OK는 `reviews/2026-10-11/large-tree-headroom-final-tests.log`에 보존했다.
Muxa e8의 부분 입력 위험 지적을 admission으로 반영했고 ea 리뷰 P1/P2 없음이다.

실제 signed Finder 시행 `finder-owned-preparation-cap-headroom.json`은 여전히
FAIL이었다. binding/readiness_deadline4976ms/scoped AX1752/context2354ms/
AX2559ms이며 effect not_started, 미보고 attempted counter는 null이다.
source manifest 전후 동일, 소유 empty directory만 제거하고 창은 남겼다.
이전 nodes 실패를 성공으로 바꾸거나 다른 tree 상태의 latency 개선으로 환산하지 않는다.

같은 runtime의 signed_element_click 시행은24.207초 PASS였다. 원시는
`signed-element-click-1791651524246422000.json`, 상위 평가는
`native-element-cap-headroom-eval.json`이다. Profile 수신1/routeErrors0,
double click과 replay 및 row Return의 opens3, ephemeral Return의 sent0,
승인된 transmit의 sent1/lastSent second, 메뉴 취소 picks0, reopens1을 확인했다.
이는 소유 AppKit fixture의 수신 상태이며 실제 메시징 서비스 delivery가 아니다.
source subset stable/owned child reaped, dependencies frozen=false다.
입력 audit는 UNCHANGED_SAMPLED/require_selection=false이며 fixture active=true
자기 보고를 전경 불변 증거로 쓰지 않는다. 동시 typing·caret·표본 사이 무변화 및
Finder 지원 완료는 아직 미검증이다.

### 관측한 프로세스에 고정하는 context 검사

명시한 번들 ID가 트랜잭션 시작 시 관측 PID의 번들과 일치하면, 입력 context는
그 PID와 창에 고정한다. 매 AX 조회 전후 OS의 프로세스 시작 신원을 확인한다.
일반 binding/dispatch, 이름 별칭과 문서 token은 새
NSRunningApplication(processIdentifier:) wrapper의 PID·번들·terminated
메타데이터를 매 조회마다 보조 확인한다. 정확한 번들·문서 token 없는 readiness
순회의 보조 확인 빈도는 아래 읽기 구간 검사 항목과 같다. SDK 속성은 비동기로 갱신될 수 있으므로
종료·PID 재사용의 근거로 단독 사용하지 않는다. observation ID, target, element
generation/count, abort와 문서 범위 검사는 계속 적용한다.

이 의미는 번들로 앱 목록을 매번 다시 해석하는 것과 다르다. 같은 번들의 다른
프로세스가 나타나도 이미 관측한 창을 그 프로세스로 바꾸지 않는다. 기존
프로세스가 재시작했으면 시작 신원 불일치로 거부한다. 이름 별칭은 기존
namespace resolver의 우선순위와 재조회 경로를 유지한다. 전체 modal/sheet
순회,20,000 노드/depth64/최대5초 deadline와 입력 headroom은 완화하지 않는다.

signed_finder_preparation은 모델 없이 owned 폴더 열기/준비를 실행하고
observe·open_folder·wait_until·prepare_window의 시작과 종료를 즉시
fsync하는0600/O_EXCL stage journal에 기록한다. tree·label·오류 본문은 기록하지
않는다.90초에 응답이 없으면 자신의 권한 채널을 폐기하고 실패를 남긴다.
이 로컬 중단은 원격 동작 취소나 not_started의 증명이 아니며 재시도 허가가
아니다. 타임아웃·예외에서는 폴더를 그대로 두고, 정상 종료 뒤에도 같은 inode의
빈 owned 폴더만 제거한다. Finder 창은 닫지 않는다.

### 노드별 관측 attribute 배치

화면을 설명하는 tree walker는 각 노드의 Role·7개 label attribute·Enabled·
Selected·Children·Document를 AXUIElementCopyMultipleAttributeValues 한 번으로
읽는다. 전체 요청 실패, 반환 배열 없음 또는 길이 불일치에서는 기존 개별
읽기와 label 배치로 돌아간다. 배열 안의 개별 AX error는 기존 descriptive
읽기의 속성 부재 처리와 같다. 숫자 label, Boolean 제외, 빈 값, 버튼 Subrole,
창의 JSON 인용·Document NFC 표현, disabled/selected, 무제목 Sheet와 순서를
유지한다. Secondary Actions는 별도 AXCopyActionNames 조회를 계속 사용한다.

이 packet은 해당 노드의 이번 관측에만 쓰며 원자적 snapshot이 아니다. probe·
입력·readiness가 재사용하지 않는다. 창별 공정 예산·2500 nodes·자식60·depth25,
전체 준비 검사와 거절 기준은 유지한다. observation_metrics는 packet_attempts,
packet_successes, packet_fallbacks, secondary_action_reads만 제공한다. successes는
전체 요청과 배열 형태가 성공했다는 뜻이며 모든 attribute의 성공을 뜻하지
않는다. 시간 비중·정합성·입력 권한 증거로 사용하지 않는다. Finder stage journal은
정확한 네 키·0~2500 정수·성공+fallback=시도 조건을 만족하는 값만 보존한다.

historical 0a2dda2 walker와 실제 Swift slice의 비교 fixture는 정상/unsupported/
짧은/nil 배치를 검사한다. 정상5노드에서 배치5회·개별 조회0회이고 렌더링과
secondary action 조회가 같다. 전체 historical checkout나 실제 앱 속도 비교가
아니다. 실측 결과와 실패 cohort는 completion-plan.md에 구분하여 보존한다.

### 창 목록 실패와 실제 빈 관측 구분

signed observe는 AXWindows의 transport 실패·잘못된 배열/원소·읽지 못한 Role을
window_list_unavailable로 반환한다. require_window=false도 이 상태를 빈 관측으로
사용하지 않는다. 따라서 창 읽기 실패를 근거로 자동 unhide/reopen/new 명령에
진입하지 않는다. 실제 빈 배열, 명시적 AX NoValue 또는 성공 응답의 없는 값은
no_window_content 경로를 유지한다. NoValue는 해당 AX 값의 부재이며 화면 전체에
창이 없다는 증명은 아니다. 문서 token 관측은 기존 별도 검증 경로를 유지한다.

window_listing_diagnostic v1은 status(ok/ax_error/no_value/malformed_array/
role_unavailable), AX error 숫자, raw_count/role_window_count/role_unavailable_count/
role_transient_count/filtered_count/badge_count만 담는다. 제목·Role 문자열·좌표·
원소 identity는 담지 않는다. SDK wait 분류와 journal의 닫힌 validator는 정확한 키,
정수 타입·범위·개수 관계를 확인하며 진단은 입력 권한 증거가 아니다.

read-only wait는 valid diagnostic의 AXWindows cannotComplete(-25204), 또는 모든
실패 Role이 cannotComplete/invalidUIElement인 경우에만 원래 deadline 내에서
재관측한다. 마감 뒤에도 실패면 같은 오류를 돌려준다. malformed·분류되지 않은
실패·진단 누락과 잠금/hidden/minimized 보고는 즉시 종료한다. 입력·프로세스
재시작·강제 활성화로 회복을 시도하지 않는다. 기존 action callers의 realWindows
wrapper는 목록/role 필터 의미를 유지한다. 배지 geometry는 CF/AXValue 타입,
GetValue 성공과 유한 좌표를 확인해 잘못된 geometry에서 crash하거나 배지로
판정하지 않는다.

기존 one-unhide 후 재관측에서도 최신 응답을 유지한다. 재관측이 다른 읽기 실패로
끝나면 이전 app_hidden 응답으로 바꾸지 않는다. 다시 no_window_content이면 원인을
한 번 새로 설명하며 추가 unhide는 보내지 않는다.

### 읽기 전용 readiness의 보조 검사 주기

정확한 번들로 관측 PID에 고정되고 문서 token이 없을 때만 전체 readiness 순회
안에서 보조 메타데이터 조회를 묶는다. OS 시작 신원·관측 ID·대상 PID/이름·
element generation/개수·문서 token·abort와 deadline은 각 AX 조회 전후 계속
확인한다. 전체 context는 순회 진입/종료, 최대16 AX operations마다, 모든 top
창 before/after 비교 경계에서 새로 검사한다. 성공 결과도 마지막 full checkpoint
이후에만 반환한다. 실패 시 모드를 복원하고 오류를 전달한다.

별칭/문서 token과 readiness 밖 binding/dispatch/routing은 기존 full per-RPC
검사를 사용한다. 전체20k/depth64 순회, top/window 목록과 role/modal/sheets
재검사,5초 공유 기한·입력 직전 revalidation/headroom·객체별 timeout set/reset를
유지한다. 병렬 AX 조회나 다른 창/파일 목록 가지치기, node packet 재사용은 없다.
보조 번들 검사의 빈도는 의도적으로 바뀌었으며 모든 guard가 이전과 같은
빈도라고 주장하지 않는다. API 관측은 원자적이지 않으며 중간의 짧은 보조
메타데이터 변화까지 검출한다는 보장도 없다.

preparation_diagnostic의 full_context_checks/incremental_context_checks는 검사
시도 횟수이며 context_elapsed_ms는 둘의 합계 시간이다. 각각의 평균·시간
비중은 이 값만으로 분리 계산하지 않는다. readonly AX batch 전체 통신 실패,
잘못된 packet과 개별 attribute 조회 실패를 readiness_batch_failed/
readiness_batch_malformed/readiness_attribute_failed로 구분하고, 관측된 실패의
AX status 숫자만 readiness_ax_error에 보존한다. cannotComplete는 통신 실패
코드이며 그 자체가 timeout/프로세스 종료의 증명은 아니다. 완료·입력 권한이나
자동 재시도 근거로 이 진단을 쓰지 않는다.

`readiness_progress`는 마지막 readiness 호출의 진단 전용 카운터다.
`pass`는 같은 scope에서 호출한 횟수, `windows`는 읽은 top 목록 수,
`completed`는 DFS를 끝낸 top 수다. 모든 top의 재검사까지 끝났다는 뜻은
아니다. `index`는0부터 시작하는 현재 top 순번이며 대상 미지정은-1,
`target`은 그 top과 요청 창의 AX identity 비교 결과다. `nodes`는 현재 top의
DFS에 진입한 노드 수, `total_nodes`는 이번 호출의 합계다. 실패한 attribute
읽기의 노드도 포함하므로 모두 검증된 노드 수가 아니다. `stage`는
entry/windows/top/tree/recheck/complete 중 하나다. 재검사에서는 현재 top을
갱신하고nodes를0으로 둔다. 최초 window-list/문맥 검사 실패도 새 호출로
초기화하여 앞선 성공의 카운터를 남기지 않는다.

정수·Boolean·닫힌 stage만 기록하며 창 제목·경로·본문·AX node는 포함하지
않는다. Python은 정확한 중첩 키/타입/수치 범위를 검사하고 복사한다.
CLI marker는1024자·출력1MiB·최대64개로 제한한다. 이 카운터는 순회 순서,
모달·시트 검사,20k/depth64 예산과5초 기한 또는 입력 허가를 바꾸지 않는다.

큰 트리의 소유 실기 평가:

```sh
~/.local/bin/tacto eval signed_readiness_scale --report /absolute/new-report.json
```

별도 signed launcher 프로세스에서만 실행한다. 앱 A는 작은 창이고 B에만
0/512/6000개의 합성 AX leaf를 만든다. 새 프로세스마다 실제 AX group 배열의
leaf 수를 독립적으로 확인한 뒤 A의prepare_window를 한 번 요청한다.
준비 뒤 최초 수신 스냅샷보다 큰seq의 A/B key·mouseDown·routeErrors를 읽고,
표본 입력 감사와 소유 child 회수를 검증한다. 거절된 준비는 실패 과제로
남긴다. 실제 helper는 준비/dispatch 재검증을 여러 번 수행하므로 요청 시간과
scoped_ax_operations를 단일 DFS의 노드당 비용으로 계산하지 않는다.

가상 요소는 [AppKit NSAccessibilityElement](https://developer.apple.com/documentation/appkit/nsaccessibilityelement-swift.class)를
사용한다. 이 fixture는 합성 hierarchy의 순회/IPC/검사 비용을 측정하며 Finder의
아이콘·파일 이름 계산이나 노드별 AX 제공 비용을 재현하지 않는다. Finder의
실패를 이 평가의 성공으로 대신하지 않는다. 앱 내부activation callback과
사용자 frontmost 변화는 별개로 기록하며, 표본 감사는 중간의 무변화나 동시
사람 typing/caret 보존의 증명이 아니다.

Finder 읽기 비용의 진단 평가:

```sh
~/.local/bin/tacto eval signed_finder_read_cost --report /absolute/new-report.json
```

`diagnostic.readiness_read_cost`를 제공하는 signed helper와 런처가 필요하다.
관측된 Finder 요소의 Role/Modal/Children만 배치1회와 개별3회로 읽으며,
각 요소에서 두 번 순서를 교차한다. 요청은 Finder 고정·최신obs_id·문서
token 없음·중복 없는1~16개 정수 인덱스로 제한한다. 런처의0..<2500 상한은
helper 관측의maxTreeNodes와 같은 값이며, 관측 상한 변경 시 함께 검토한다.
다른 요청에element_indices를 섞으면 거부한다. helper는 각 RPC 전후 PID/
OS 시작 identity/관측 generation/번들/중단/2초 deadline과timeout set/reset을
검사한다. 기존 표시 경계를 적용하며 입력·활성화 요청은 보내지 않는다.

AX 호출 내부의microsecond 시간만 기록한다. context 검사·timeout 설정/복원·
packet 해석 비용은 그 값에서 제외한다. 반환 데이터는 AX 상태 코드·자료형·
배열 크기이며 실제 값/제목/파일명/본문은 반환하지 않는다. 배열 크기도 사용자
메타데이터이므로 raw report는0600으로 보존하고 외부 공유 시 크기는 구간으로
표현한다. 실패 시 완성된 행과 현재 부분 행을 남기며 누락된 측정은unknown이다.
unsupported batch도0비용이나 단건과 동등한 지원으로 계산하지 않는다.

평가는2500-node/prefix60 관측에 노출된 role별 첫/마지막 요소만 사용한다.
`attribute_shapes_equal`은 상태/자료형/배열 크기의 일치이며 Boolean·문자열
값 자체의 일치나 원자적 snapshot을 뜻하지 않는다. 반복 읽기의 캐시 영향이
있고 차가운 미관측 노드 전체를 대표하지 않는다. 이 진단의 PASS는 읽기 측정과
표본 감사의 완료이며 Finder 창 준비/폴더 생성 또는 입력 허가를 증명하지 않는다.


읽기 전용 사전 점검 실험은 다음처럼 별도 프로세스에서 실행한다.

```sh
~/.local/bin/tacto eval signed_finder_preflight --report /absolute/new-report.json
```

Finder의 최신 Window를15초/20k/depth64로 읽으며 입력·활성화 API를 호출하지
않는다. 결과는read_only=true/authorizes_input=false이고 입력 binding/permit으로
저장하지 않는다. 실패하면 준비 요청을 전달하지 않는다. 완료해도 원래
prepare_window의 새5초 기한/전체 native pre/post 검사를 그대로 수행한다.
일반 실행에는 자동 적용하지 않는다. 이 읽기 단계의 시간은 기존 입력 기한의
연장이 아니며 전체 작업에는 추가 비용이다. 현재 실제18-top 상태에서는
15초 사전 점검도 실패했으므로 Finder 해결책으로 권하지 않는다.

Finder 시험 도구는 창 생성/닫힘 소유권을 입증하지 못한다. 폴더 열기를 요청한
뒤 빈 디렉터리를 삭제하면 Finder가 큰 부모 폴더로 이동할 수 있어 이후 환경이
달라진다. staged/standalone 준비 probe는 창 닫힘이 입증되지 않은 디렉터리를
보존한다. cleanup=owned_directory_retained_without_window_close_proof이며,
잔여 디렉터리·창의 정리는 미완료다. 기존 창을 제목만으로 닫거나 이전 실패를
깨끗한 초기 상태의 평가로 승격하지 않는다.


소유 앱의 모달 표현을 읽기 전용으로 비교하는 진단:

```sh
~/.local/bin/tacto eval signed_modality_inventory --report /absolute/new-report.json
```

signed helper의 preflight 대상은 Finder와 예약된 modality fixture로 제한한다.
평가는 clear·창 시트·깊은 합성 AXSheet·앱 모달·floating panel·popover 2종·
중첩 시트의 실제 AppKit 상태 callback과 전체/경계 판정을 기록한다.
PASS는 측정 완료이고 입력 차단 안전성이나 Finder 작업 성공을 뜻하지 않는다.
깊은 합성 AXSheet는 실제 비교에서 경계 clear/전체 거절로 달랐다. 기존 전체
검사를 유지하며 입력·활성화를 보내지 않고 생성한 자식 프로세스만 회수한다.
누락 callback/알 수 없는 응답/감사 실패는 실패로 남기며 raw report는0600이다.


preflight의refusal_cause는 닫힌 원인 코드다. native guard가 Sheet 또는 잘못된
role, Modal 또는 미관측 상태를 함께 거절하므로 sheet_or_malformed_role/list,
modal_or_unknown_top/element는 확정 모달 감지와 구분한다. deadline/nodes/
identity_or_abort/malformed/unavailable도 별도로 남긴다. 팝오버 열림은 이번
두 검사에서 clear였으며, 첫 클릭이 팝오버 닫힘에 소비되는지 실제 수신으로
검증하지 않았다. clear를 팝오버가 없는 상태나 입력 전달 보장으로 해석하지 않는다.


## 첫 클릭의 실제 내용 수신과 명시적 준비

새 진단은 다음 두 조건을 별도로 실행한다. 둘 다 소유 앱만 사용하며 기존
사용자 창을 닫지 않는다. 실패한 unprepared 결과를 prepared 결과로 덮어쓰지 않는다.

```sh
~/.local/bin/tacto eval signed_popover_receipt --report /absolute/new-unprepared.json
~/.local/bin/tacto eval signed_popover_prepared_receipt --report /absolute/new-prepared.json
```

clear/transient/semitransient × A/B 창 × acceptsFirstMouse true/false의12개
조건이다. 대상 창을 먼저 생성하고 소유 PID/시작 identity/AXFocusedWindow를
독립적으로 확인한다. 앱 내부 AXFocusedWindow와 AppKit isKeyWindow는 다르다.
팝오버 표시와 실제 팝오버 창 가시성도 확인한다. NSWindow 이벤트 도착과
내용 NSView의mouseDown/up 카운터를 분리하며 새 nonce/sequence로 검증한다.
각 자식에게 내용 클릭은 한 번만 보내고 불확실한 입력을 재시도하지 않는다.

현재 signed click_point는 암묵적인 제목 표시줄 준비 없이 down/up2개만 보낸다.
미준비 fixture에서는 first-mouse를 거절한6개 조건이 acted/posted2와 올바른
창 이벤트2를 보고했지만 내용 수신은0이었다. first-mouse를 허용한6개는정확히
1쌍을 받았다. clear에서도 실패했으므로 팝오버에만 해당하는 문제가 아니다.

명시적 prepare_window를 먼저 보내고 새 sequence/소유 key-window 상태와
준비 중 내용 수신0을 확인한 뒤, 새 observe에서 클릭한12개 조건은모두
정확히1쌍을 수신했다. 준비가 generic 앱의실제 key 상태를 보장한다고 주장하지
않는다. 실제 앱에서는 선언한 준비 후조건과 클릭의실제 작업 후조건을 검증한다.
준비 없이 이미 보낸 클릭을 효과 확인 없이 반복하지 않는다. 이 절차는
호스트가 승인한 pointer action의명시적 단계이며 자동 준비 입력은 추가하지 않았다.

prepared transient는준비 단계에서 팝오버가닫혔다. 그러므로 그4개 조건은
열린 팝오버 클릭 소비 측정이아니다. semitransient는내용 dispatch 진입에도
열린 채로수신했다. before/preparation/dispatch/after를 나눠 보고하며 사용자
직접 클릭·다른 앱·AXPress·일반 double_click로 이결과를 확대하지 않는다.
AppKit 내부 활성화 알림1회와 전역 전경 보존은별도로 기록한다. 표본 감사는
동시 사람 입력/caret 유지나 표본 사이의변화 부재를증명하지 않는다.


입력 대상 포커스 거절은 preparation_diagnostic의 닫힌 reason으로 구분한다:
`target_window_not_focused`, `target_input_changed`, `target_focus_outside_window`,
`target_input_not_focused`, `target_row_not_in_focused_list`. 앞의세 종류는
창/요소/소속 창, 뒤의두 종류는bound Return의키보드 입력/선택 행 포커스다.
이 metadata는권한이나 준비 완료 상태가 아니다. 같은 코드라도 입력 시도 이후
실패면 effect는unknown이다. not_started를 실제 응답에서확인한 뒤에만 새
관측으로 다음 단계를 계획하며, unknown을 코드만보고 재시도하지 않는다.

```sh
~/.local/bin/tacto eval signed_popover_routing_refusal --report /absolute/new-diagnostic.json
```

이 진단은 소유 A가앱 내부 focused 창인 상태에서 B를요청한다. 팝오버3종과
first-mouse2종에서 입력 전거절·닫힌 코드·전송 시도0·새 callback의내용/창
이벤트 변화0을 검증한다. passed는진단 검증만 뜻하고task_succeeded는false다.
일반 앱의준비나 실제 Return 전송 성공으로계산하지 않는다. `[focused]` marker는
현재 AXMain 또는AXFocused이며 전역 전경/실제 AppKit key창의증거가아니다.

### 정확한 문서 pin의 거절 원인

`observe --document`와 document-scoped replay가 `pin_unavailable`로 거절되면,
가능한 경우 `pin_reason=<닫힌 코드>`를 함께 표시한다. 예를 들어
`selection_ambiguous_or_absent`, `window_subrole_unreadable`, `read_deadline`,
`traversal_budget`, `text_range_unreadable`은 알려진 helper 거절의 분류다.
알 수 없는 오류/앱 문자열은 출력하지 않으므로 원인 코드가 없을 수도 있다.
이 metadata는 추가 입력 권한이나 준비 완료 증거가 아니다.

`pin_unavailable`만으로 권한 부족, 창 숨김 또는 foreground 필요를 판단하지
않는다. 특히 `pin_reason=user_aborted`는 사용자 중단이며 일반 실패와 구분한다.
pin 거절 뒤 app 전체 tree로 넓히거나 다른 문서로 대체하지 않는다. 이 진단은
재-pin 또는 자동 재시도를 추가하지 않는다.

CLI는 알려진 pin 원인에만 `document-pin-diagnostic: {JSON}` 줄을 함께 출력한다.
이 줄은 schema/phase/reason/effect로 제한되며 경로나 본문을 담지 않는다.
비교 report의 reported_document_pin_diagnostics는 completed CLI item에서
읽은 도구 보고 값이다. 완료 영수증이나 실행 출처 증명으로 사용하지 않는다.


`reason="identity_changed"`의 optional `identity_cause`는 실패 검사의 닫힌
종류다: initial_binding, observation_id, process_id, target_name, process_start,
element_map, document_token, document_pin, document_live, target_resolution.
원문 UI·경로·PID·document token을 담지 않으며 다른 reason에서 사용하면 validator가
거부한다. initial_binding은 여러 초기 조건을 묶은 분류다. 이 metadata는 추가
입력·재바인딩·재전송을 승인하지 않고 OS 원인 귀속이나 과거 실패의 원인을
증명하지 않는다. 필드가 없는 과거 응답은 구체 원인이 미관측인 상태로 유지한다.


### Python readiness의 중첩 modal과 검사 후 상태 재확인

Python direct의 `require_window_ready`도 하위 요소의 AXModal이 제공되면
정확한 False만 허용하고 읽기 실패·malformed·True를 거부한다. AXModal의
attributeUnsupported/noValue는 기존 Swift 계약처럼 선택적 부재로 처리한다.
전체 순회 후 top role과 AXSheets를 다시 읽어 늦게 붙은 sheet·역할 변경을
거부한다. 자식을 추가하기 전에 대기 중인 sibling 수까지 node 예산에 포함한다.
Python의 기존5000 nodes/depth64와 signed helper의 기존 예산은 변경하지 않았다.
검사는 AX 표본이며 UI 전체에 대한 atomic lock이 아니다.

수정 전(비공개 로컬 기록: `reviews/readiness-parity-before-a.log`; 공개 원본 미포함)13 tests/9 failures에는
late sheet·late role·nested modal 및 unreadable/malformed 값·pending budget의
반례를 보존했다. 준비 검사 회귀(비공개 로컬 기록: `reviews/readiness-parity-regression-a.log`; 공개 원본 미포함)는
46 tests/23.948s/OK, 실제 Swift traversal과 주입 AX의 비교를 포함한다.
입력/시각 회귀(비공개 로컬 기록: `reviews/readiness-parity-integration-b.log`; 공개 원본 미포함)는69 tests/
4.124s/OK이며 production capture→validate→dispatch에 늦은 상태 변화를
주입해 attempted0/read_input0/event construction0/post0를 확인했다.
두 명시 source subset은 전후 stable이며 두 suite의 겹치는 검사를 합쳐 고유
검사 수로 주장하지 않는다. 실제 native GUI 입력이나 Finder 성능·과제 성공,
사람 typing 보존을 검증한 결과는 아니다.

## Safari URL confirmation

The signed helper advertises `press_key.safari_address_confirm`. For an
already authorized `press_key Return` on Safari's native
`WEB_BROWSER_ADDRESS_AND_SEARCH_FIELD`, replay pins the intended URL from the submit binding or declared destination in
`expect.value` in addition to the element's existing role/identifier claim.
The helper rechecks the exact value, process instance, observation generation,
owner, enabled state and native chrome ancestry. Only full HTTP(S) URLs and an
advertised `AXConfirm` action qualify; web content, sheets/dialogs and other
apps keep their existing routes. URLs without a scheme use the existing key
path and may still be refused if its readiness cannot be established.
The comparison is exact: a URL the observation renders differently (surrounding
spaces, another Unicode normalization, or text that looks like a tree attribute
such as `, Title:`) is refused before any input rather than confirmed.

The qualifying route invokes the field's semantic `AXConfirm` once and sends
zero keyboard events. It therefore does not need to route a Return through
Safari's address suggestions window, which may omit `AXModal`. The generic
keyboard readiness check is unchanged. A confirmation failure or timeout never
falls back to a key press; uncertain outcomes still require a fresh observation.
`acted` with `unverified: observe_effect` proves neither navigation nor server
receipt. Verify the destination page before reporting a successful navigation.

Safari address confirmation performs the bound navigation; it does not supply a
remote-service commit receipt. When the host supplies no receipt of its own,
replay witnesses the navigation with `NavigationReceipt`
(`tacto/navigation_receipt.py`). Safari leaves a window's `AXDocument` empty
(measured 2026-10-21); the loaded page is the window's `AXWebArea`, whose
`AXURL` the helper reports as `URL:` on a fully quoted WebArea line, so a page
title cannot spell that field. Only the shallowest WebArea of the one
`[focused]` window counts, and it must be unique; frames are deeper. Before the
press, from the observation the press is addressed to, that page must be another
URL; after it, fresh observations must show the pinned destination (scheme and
host lowercased, default port and an empty path made explicit). The event is
then `browser_navigation` evidence. The address-bar value is never used. A
redirect to another URL, or no load within the receipt's deadline, ends as
`commit_unverified` with `destination_not_shown`; inspect the page without
repeating the confirmation. The evidence says this window loaded the URL, not
what the site did with the request. That typing alone leaves `AXURL` unchanged
is pending a live check in an owned Safari window.
