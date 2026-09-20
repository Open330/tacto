# 네이티브 창별 클릭

Python `Driver.act(..., {"name":"click", "element_index":...})`는 관측한 요소가
AXPress를 지원하면 기존 AX 경로를 사용한다. 성공적으로 읽은 action 목록에
AXPress가 없거나 AXPress가 정확히 `actionUnsupported`를 반환했을 때만
창별 마우스 이벤트를 시도한다. 불확실한 AX 오류나 timeout을 재클릭으로 바꾸지 않는다.

이 경로의 좌표는 모델 입력이 아니라 관측된 AX 요소의 현재 영역에서 얻는다.
프로세스 시작 시각은 공개 libproc에서 읽으며, AX window ID는 비공개
`_AXUIElementGetWindow`, 창 내부 이벤트 좌표는 비공개
`CGEventSetWindowLocation`/`CGEventGetWindowLocation`을 사용한다.
필요한 API가 없거나 검증이 실패하면 전역 입력으로 전환하지 않고 거절한다.

입력 직전에 다음을 확인한다.

- 최신 관측과 그 관측의 PID·프로세스 시작 시각
- 동일 AX 요소와 부모 창, 정확한 CG window ID·소유 PID·표시 상태
- 창 및 요소 영역, 중심점 hit-test, 명시적인 enabled 상태
- 호출자의 expect, 남은 시간, 중단 상태

대상 앱이 내부적으로 비활성일 때는 해당 PID에 activation 이벤트를 보낸다.
그 후 클릭 전에 관측·창·기대값·중단·시간을 다시 확인한다. 이미 activation을
시도한 후 실패하면 `unknown`이며, mouse-down 이후에는 준비한 mouse-up을
같은 대상에 보낸다. 결과의 `input_route: window_bound_mouse`는 전달 경로다.
작업 성공은 새 관측과 기존 완료 증거로 판정해야 한다.

전역 HID post, 포인터 이동·복원, 앱을 실제 전경으로 가져오는 호출은 없다.
하지만 앱 자체의 입력 반응이나 검사 직후 변경까지 원자적으로 통제하지는 않는다.
Python과 signed Swift helper 모두 소유 AppKit 시험 버튼에서 실제 클릭을 검증했다.
helper 시험은 stale 관측·잘못된 role 거절과 정상 클릭 후 카운터0→1을 포함한다.
**이 새 경로의 실제 Esc 중단·caret·동시 사용자 입력 검증은 아직 남아 있다.** helper는 기존
연결별 Esc latch와 로컬 5초 준비 제한을 검사하며, AX 읽기가 지연된 뒤에도
중단·기한을 다시 확인한다. 현재 클릭에는 아래 절대 기한도 전달한다. 기한 검사가 원격 작업 취소를 보장하지는 않는다.
Python 직접 실행에는 signed helper의 실제 Esc 감시 채널이 없다.

일반 메뉴의 자동 창 활성화, screenshot 좌표를 받는 native 시각 액션,
저장 패널의 자동 조작과 최종 저장 검증은 이 변경의 완료 범위가 아니다.
[실제 드라이버 검증](../reviews/2026-09-19/window-dispatch-progress.md)을 참고한다.


## 클릭 시작 기한

Python helper transport는 클릭에 `click_deadline_uptime`을 전달한다. 남은 모델/작업 기한과 5초 중 짧은 값을 사용하며, 전송 전에 이미 만료됐으면 `not_started`로 거절한다. 새 helper capability `action.click.deadline_uptime`을 요구하므로 이 필드를 무시하는 구형 helper에는 연결하지 않는다.

값은 같은 Mac의 단조 시계 기준 절대 초다. 현재 환경의 Python `mach_absolute_time` 기반 monotonic과 Swift systemUptime은 [별도 실측](../reviews/2026-09-19/click-deadline-clock.json)에서 같은 시간축을 확인했다. 다른 호스트나 서로 다른 시계 기준의 transport에는 그대로 쓸 수 없다. launcher와 helper 모두 boolean·비유한 값·비양수·다른 action의 필드를 거절한다. 필드 생략 시 helper 자체의 5초 상한을 사용한다.

helper는 동일 기한을 AXPress 직전 및 창별 클릭 준비/activation 전후 검사에 사용한다. 이것은 **새 클릭 입력의 시작 기한**이며, 이미 시작한 AX 호출·IPC 대기·OS 작업을 강제로 취소하거나 종료 시각을 보장하지 않는다. mouse-down을 보냈다면 기한이 지나더라도 mouse-up 정리는 수행한다. 다른 액션 전체의 timeout을 구현했다고 해석하지 않는다.


## general 루프의 재관찰

`acted + observe_effect + window_bound_mouse + reobserve_required:true`인 비영속적 클릭은 현재 batch의 나머지 행동과 기대값을 버리고 새 관측으로 planner를 다시 호출한다. 이전 화면에서 계획한 읽기나 추가 클릭을 그대로 실행하지 않는다. `cannot_complete`, 다른 경로의 불확실한 결과, 저장·전송 같은 consequential 효과는 이 예외로 계속 진행하지 않는다.

이 재관찰은 전송 이력을 자동으로 confirmed로 바꾸지 않는다. 실제 모델 시험에서는 버튼 클릭 후 새 카운터1을 읽었지만 click event는 dispatched, 실행 certainty는 unknown이었다. 실측된 결과 읽기와 Agent의 전체 완료 판정을 구분한다.


general의 작업 기한은 planner뿐 아니라 `driver.act`를 호출하는 동안에도 활성화한다. 따라서 클릭 transport가 자체 5초를 새로 부여하지 않고 남은 general 예산을 사용한다. 상위 문맥에 더 짧은 기한이 있으면 그대로 유지하며 호출 후 문맥을 복원한다. 이 연결은 입력 시작 기한을 위한 것이며 blocking 호출 강제 중단과는 다르다.


## 명시적 창 준비 (`prepare_window`, 실험 단계)

Python `capture_binding(point_kind="titlebar")`와 Swift `captureNativeWindowBinding(pointKind: .titlebar)`는 표준 창과 동일한 창에 속한 AXCloseButton의 위치로 후보점을 계산한다. 상단 밴드 안의 버튼 중심 높이에서 창 너비의 50%, 75%, 25% 지점을 순서대로 검사하며, 앱 범위 hit-test가 창 자체를 반환하는 지점만 사용한다. 자식 버튼이나 다른 요소는 이 모드에서 허용하지 않는다. 기존 요소 중심 클릭과 별도의 모드이며 재검증도 모드를 유지한다.

이 바인딩은 읽기 전용이다. 다른 앱 창에 의한 가림 여부를 검증하지 않으며, 메뉴 활성화나 사용자 입력 보존을 증명하지 않는다. 실행 소비처는 기존 `buildNativeWindowClick` / `build_window_click`와 같은 창 ID·창 상대 좌표를 검증하는 PID 전용 전송 경로다. 전역 HID 좌표 클릭의 근거로 쓰지 않는다. 현재 direct/signed helper는 `action.prepare_window`를 광고하며 `{name: "prepare_window", element_index: 관측된 창 인덱스}`를 받는다. general planner는 이 capability가 있는 관측 Window에만 prepare_window를 노출한다. 자동 메뉴 경로 발견과 선택은 아직 별도 후속이다.

실행 경로는 관측 ID, PID/시작 시각, 모달·시트 상태, 중단 latch, 남은 기한을 확인하고, 활성화 시도 뒤와 마지막 AX 검사 뒤에도 재확인한다. AXSheets 속성 미지원 자체는 시트가 없다는 증거가 아니다. 재검증 직후의 변화까지 원자적으로 막는 기능은 아니다.


제목 표시줄 바인딩은 이제 준비 상태도 검사한다. 대상 창의 유일성·비최소화와 앱 창들의 명시적인 비모달 상태를 요구하며, 전체 노출 자식 트리를 예산 내에서 읽어 AXSheet가 있는지 확인한다. `AXSheets` 미지원은 빈 목록으로 간주하지 않는다. 조회 오류·타입 오류·예산 초과·창 목록 변경은 거절하고, 조회 끝에서 modal/minimized 상태를 다시 확인한다. Python은 실제 TextEdit 정상 창과 열린 저장 시트에서 양쪽 판정을 검증했다. Swift signed action도 정상 시험 창에서 이 검사를 거쳐 입력을 전송했다. signed action의 실제 열린 시트 거절 검증은 아직 남아 있다.


응답 `acted`, `input_route: window_prepare`, `unverified: observe_effect`, `reobserve_required: true`는 입력을 전송했다는 의미다. 메뉴가 활성화됐다는 완료 판정이 아니므로 반드시 새 관측과 메뉴 상태를 확인해야 한다. 클릭과 같은 `click_deadline_uptime` 시작 기한을 사용한다.

소유 일반 AppKit fixture에서 준비→새 관측→메뉴 활성 확인→responder 카운터 0→1이 한 번 성공했다. 전경 앱·포인터·AX focus는 649개 표본에서 동일했고 최대 표본 간격은 158.8ms였다. 앞선 재관찰 없는 시험은 메뉴 비활성으로 실패했으며 원인은 미확정이다. 캐럿·표본 사이 변화·동시 입력·다른 앱의 동작 보장은 아니다. 이 D 시험 당시 signed helper에 `menu_state` 읽기 API가 없어 Python 드라이버의 독립 읽기를 사용했다. 아래 후속 시험에서 signed 조회 경로를 검증했다. [D 원시 결과](../reviews/2026-09-19/prepare-window-signed-20260920-d/report.json)를 참고한다.

대상 앱이 AXFrontmost가 아니면 PID에 지정한 AppKit activation 이벤트를 먼저 보낸다. 대상 앱 내부 활성 상태가 바뀔 수 있으며, 시스템 전경 유지와는 별개의 상태다. 후속 창 입력 전에는 바인딩·중단·기한을 다시 검사한다. 이 이벤트도 이미 시도한 뒤 발생한 오류는 무효과로 간주하지 않는다.


## Signed 메뉴 상태 조회

`menu_state` capability는 `{type: "menu_state", target: 앱, path: [메뉴, 명령]}` 읽기 요청을 지원한다. 각 경로는 제목 또는 지역화된 제목 목록이며 최대 6단계, 단계당 대안 8개, 제목 200자로 제한한다. launcher와 helper가 요청 필드를 검증한다. 실행 중인 앱만 조회하며 메뉴 실행·창 준비·앱 실행을 하지 않는다. direct Python 조회도 `launch=False`를 사용한다.

helper는 AXEnabled의 실제 boolean만 반환하고, 읽을 수 없으면 `menu_state_unavailable`로 거절한다. 조회 후 Esc latch와 PID/프로세스 시작 시각을 재확인한다. 반환값은 조회 시점의 상태이므로 이후 menu act의 검증을 대체하지 않는다. 메뉴 상태 조회 자체가 이전 동작의 settle 대기를 수행하지는 않는다. prepare_window 이후 새 observe를 먼저 호출하는 순서를 유지한다.

[새 signed 시험](../reviews/2026-09-19/menu-state-signed-20260920-a/report.json)은 helper 메뉴 상태 조회를 사용한 준비·명령 실행·독립 카운터1 확인에 성공했다. 587개 표본에서 전경/포인터/AX focus가 동일했고 최대 간격은 157.21ms였다. 캐럿과 동시 사용자 입력은 여전히 미검증이다. general의 창 준비/재관찰 연결은 후속에서 구현했다. 일반 메뉴 경로 발견과 선택은 남아 있다.


`target_ax_frontmost`는 대상 앱이 노출한 AXFrontmost의 boolean 또는 미관측 null이다. 시스템 전경 앱 판정이 아니며, 이 값만으로 메뉴 비활성의 원인을 확정하지 않는다. 배경 앱은 메뉴 검증 시점에 따라 disabled를 반환할 수 있다. AX 메뉴 조회도 대상 앱의 코드 실행을 유발할 수 있으므로 무부작용을 보장하는 API는 아니다.

메뉴 경로의 단계·대안·제목 길이를 제한하고, 양쪽 메뉴 조회에서 각 AX 속성 읽기 전후 중단·기한을 검사한다. 조회당 속성 읽기는 5,000회, 반환된 자식 목록은 5,000개까지 허용하며 초과 시 `menu_read_budget_exceeded`로 거절한다. 로컬 준비/조회 기한은 최대 5초이며 Python direct는 더 짧은 상위 작업 기한도 따른다. helper의 로컬 5초는 IPC 도착 전 대기시간을 포함한 전체 요청 기한은 아니다. 기존 동기 AX 호출 하나를 강제로 중단하는 기능도 없다. 검사는 새 속성 읽기를 제한하고 잘못된 성공 응답을 막지만, 진행 중인 동기 AX 호출 지연의 상한을 보장하지는 않는다. 시간 초과는 `menu_read_timeout`, 중단은 `aborted_by_user`다. 이 좁은 메뉴 조회는 별도의 overlay를 생성하지 않는다; observe/act의 표시 동작과 구분한다.


AX 조회 자체의 오류도 구분한다. AttributeUnsupported/NoValue만 미관측 값으로 처리하고 다른 AX 실패는 `menu_read_failed`로 반환한다. 응답하지 않는 앱을 `menu_not_found`로 오보고하지 않는다. signed 메뉴 막대의 CF 타입도 확인해 잘못된 값의 강제 캐스트로 helper가 종료되는 경로를 막았다.

각 실제 AX 호출에 객체별 messaging timeout을 min(1초, 남은 조회 기한)으로 설정하고 호출 후 해당 객체의 override를 0으로 되돌린다. 설정/해제 실패는 각각 `menu_timeout_unavailable`/`menu_timeout_reset_failed`로 거절한다. 시스템 전체 timeout은 바꾸지 않는다. 자식 목록은 개수를 먼저 조회해 5,000개 초과를 거절하고, 지정 범위만 복사한 뒤 개수를 다시 확인한다. 개수나 반환 길이 변화, 첫 개수 조회 후 목록이 사라지는 경우는 `menu_state_changed`다. 진단 메시지를 위해 제목을 다시 읽지 않으며, 첫 메뉴 래퍼를 찾으면 나머지 형제를 탐색하지 않는다. 같은 개수를 유지한 내용 교체까지 원자적으로 감지하는 것은 아니다. enabled와 AXFrontmost는 순서대로 읽은 관측값이며 원자적 쌍이나 비활성 원인의 증거는 아니다.


[Apple timeout 계약](https://developer.apple.com/documentation/applicationservices/1459345-axuielementsetmessagingtimeout)에 따라 동등한 AX 객체라도 timeout 설정은 공유된다고 가정하지 않는다. SDK의 AXUIElement.h에서 GetAttributeValueCount/CopyAttributeValues의 범위 복사 계약도 확인했다. 개수 조회의 대상 앱 내부 구현 비용이나 OS 장애까지 제한하는 보장은 아니다.

[응답 정지 실측](../reviews/2026-09-19/menu-unresponsive-direct-20260920-a/report.json): 소유 AppKit 자식 프로세스만 SIGSTOP한 상태에서 Python direct 메뉴 조회가1.008초에 `menu_read_failed`로 반환되고, SIGCONT 후 정상 조회로 복구됐다. 비상8초 복구 타이머는 사용되지 않았다. exact child 정리와 독립 입력 감사를 포함한다. 이 검사는 signed helper의 응답 정지 실측으로 합산하지 않는다.


## General의 명시적 창 준비

Window 후보는 action.prepare_window capability가 있을 때만 제공하고 클릭 후보와 함께 앞쪽에 배치한다. ask validator는 can과 마지막 행동 조건을 검사한다. general은 role/capability/can을 다시 확인하며 명시 disabled는 기존 공통 게이트로 거절한다. AXEnabled가 없는 표준 창에는 enabled=true 기대값을 보내지 않고 native 준비 상태 검사를 적용한다.

정확히 prepare_window/window_prepare + acted/observe_effect/reobserve_required 응답일 때만 이전 batch와 기대값을 폐기하고 재관찰한다. 입력 이력은 dispatched, certainty는 unknown으로 유지한다. 아직 flow replay에는 이 준비 검증 계약이 없어 준비가 포함된 경로 전체의 자동 승격을 보류한다. 단계를 지워서 레시피를 만드는 방식은 쓰지 않는다.

읽기 이력에는 잘린 후보 이름 외에 실제 읽은 값을 JSON으로 전달한다. 실제 Claude가 잘린 이력을 불완전한 읽기로 해석해 재읽기하다 중단된 사례를 수정한 것이다. 값이 큰 경우 이력도 모델 입력 예산을 소비하며, 예산을 초과한 긴 읽기 이력의 별도 paging은 후속이다.
