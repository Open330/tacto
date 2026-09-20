# 읽기 전용 메뉴 발견

`menu_items(target, path=[])`는 실행 중인 native 앱의 메뉴를 한 단계씩 읽는다. 서명 채널 요청은 `{"type":"menu_items","target":"bundle.id","path":[]}`이며 다른 필드를 받지 않는다. 빈 경로는 메뉴 막대, 비어 있지 않은 경로는 기존 메뉴 명령과 같은 제목/지역화 대안 목록이다. 최대 6단계, 단계당 대안 최대 8개, 제목 최대 200자이며 제어 문자는 거절한다.

성공 응답은 `type: menu_items`, 실제 해석된 `path`, `items`, `complete: true`다. 각 항목은 `title`, 전체 제목 `path`, `enabled`, `has_submenu`를 포함한다. 활성 상태와 하위 메뉴 존재를 관측하지 못하면 해당 값은 null이다. null을 활성 상태나 실행 허가로 해석해서는 안 된다. 빈 제목의 구분선은 목록에서 제외한다. 부모 자식 목록 자체를 읽지 못하면 빈 성공 목록을 반환하지 않는다.

시간·AX 호출 횟수·자식 수 제한을 기존 메뉴 상태 읽기와 공유한다. 중복 이름, 읽기 실패, 대상 PID/시작 시각 변경, 시간 초과는 전체 조회 실패다. 부분 목록을 complete로 반환하지 않는다. 조회는 앱 실행·활성화·메뉴 열기·키 입력을 하지 않는다. 결과는 연속적인 AX 읽기이며 단일 시점의 원자적 스냅샷이 아니다. 이후 실행은 메뉴 경로와 활성 상태를 다시 확인해야 한다.

TracingDriver와 TraceDriver는 경로와 결과를 기록하고 재생한다. 기록과 다른 요청은 trace_divergence로 거절한다. 일반 planner는 `inspect_menu`로 관측된 경로를 탐색하고, `menu`로 활성 leaf 명령을 선택한다. 두 동작은 단독 계획이어야 하며 이후 재관찰한다. 실행 직전 목록을 다시 확인하고 명령은 기존 효과·권한 범위·커밋 증거 검사를 거친다. 메뉴 경로는 모델 입력 문자열에서 가져오지 않는다. 메뉴 실행 이벤트는 별도 결과 증거가 없으면 dispatched/unknown으로 남는다. 메뉴 경로는 검토용 flow로 보존할 수 있다. 배포에는 `to_flow`/`deployable`의 `menu_postconditions`에 작성자가 명시한 필드·정확한 값·속성 조건이 필요하다. 후보에서 실제 관측한 경로와 params_digest가 모두 일치해야 한다. 이후 읽기 capture만으로 메뉴의 결과 조건을 대체하지 않는다. prepare_window가 포함된 경로는 `preparation_postconditions`에 명시적인 필드·값·속성 조건이 있어야 배포할 수 있다. 재생은 capability와 Window 대상을 확인하고, 정확한 window_prepare/observe_effect/reobserve_required 응답에 한해 재관찰한다. 이미 참인 결과 조건은 새 준비 전환의 증거가 아니다.
