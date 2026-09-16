# NLC 시나리오 카탈로그 — 30개 실사용 태스크

> 컴퓨터 사용이 불편한 유저(고령층·디지털 저활용층·접근성 니즈)를 위한 NLC
> 제품의 "플로우" 후보 30개. 각 시나리오는 한 번 모델로 authoring하면
> `nlc/flow.py`의 `Flow`/`Step`/`Selector`로 저장되어 이후 **모델 토큰 0**으로
> 재생된다. 어휘는 `PROTOCOL.md` §3.5, §1.5와 `nlc/flow.py`를 그대로 따른다.

**어휘 레퍼런스**
- `Selector(role, identifier, title, desc, ancestor, allow_nth)` — role+identifier
  우선, 없으면 role+title/desc, `ancestor`로 스코프 한정, `allow_nth`는 "반복
  요소 중 명시적으로 n번째" (silent `find()[0]` 금지).
- `Step(action, selector, params, risk_class, intent_text, postcondition, ...)`
  — `action` ∈ {`click`, `set_value`, `type_text`, `press_key`, `scroll`,
  `secondary_action`}.
- `risk_class` ∈ {`none`, `preapproved`, `confirm`, `handoff`} (PROTOCOL.md
  §1.5 4단계 그대로): 읽기=`none`, 본인 데이터 생성/저장(내용 명시)=`preapproved`,
  삭제·제3자 커뮤니케이션·시스템 설정 변경=`confirm`, 비밀번호/보안 우회=`handoff`.
- **runnable-now**: `yes` = 오늘 macOS 내장 앱 + `nlc/driver.py`(AX)로 구현 가능,
  `partial` = 메커니즘은 있으나 실제 AX identifier/제스처 매핑이 기기에서
  미검증(계산기의 `Multiply`/`Equals`가 title이 아니라 identifier에 있었던
  것처럼, authoring 1회로 확정 필요), `needs-web` = 브라우저가 필요 — **이제
  `nlc/browser.py`(Playwright + CDP accessibility tree)로 구현 가능**하다. 동일
  L2 프로토콜·Flow·Selector 어휘를 쓰되 web는 별도 obs_id/인덱스 공간이며, 입력값
  검증은 whole-tree `contains`(값이 input 노드 하위에 안 들어감), 셀렉터는
  role+title(name)+ancestor를 쓴다.

**주의**: 아래 `identifier`/`title` 값은 앱 구조상 합리적으로 추정한 값이며,
`AXIdentifier`는 앱 버전 간 안정성이 보장되지 않는다(D3/resolver policy B).
실제 authoring 시 기기에서 1회 관찰로 확정해야 한다.

---

## 1. Communication (이메일·문자)

| # | 한국어 발화 | risk | runnable |
|---|---|---|---|
| 1 | "새로 온 메일 읽어줘" | none | yes |
| 2 | "이 메일에 답장으로 알겠다고 써줘" | confirm | yes |
| 3 | "엄마한테 문자로 '오늘 저녁에 갈게요' 보내줘" | confirm | partial |
| 4 | "엄마한테 페이스타임 걸어줘" | confirm | partial |
| 5 | "안 읽은 메일 몇 개야?" | none | yes |

### 1. 새 메일 읽어줘
- **Target**: Mail (`com.apple.mail`)
- **Steps**:
  1. `click` — `Selector(role="Row", ancestor="MessageList", allow_nth=0)` — intent: "가장 최근 메일을 엽니다"
  2. `scroll` — `Selector(role="ScrollArea", identifier="MessageBodyWebView")`, `params={"direction":"down","pages":1}` — intent: "본문을 마저 보여드릴게요"
- **Postcondition**: 본문 영역(`MessageBodyWebView`)에 발신자/제목 텍스트가 렌더링됨.
- **Risk**: `none` — 열람뿐, 데이터 변경 없음.
- **Runnable-now**: yes.

### 2. 메일 답장 보내줘
- **Target**: Mail (`com.apple.mail`)
- **Steps**:
  1. `click` — `Selector(role="Button", identifier="ReplyButton")` — intent: "회신 버튼을 누릅니다"
  2. `type_text` — selector 없음, `params={"text":"네, 알겠습니다."}` — intent: "답장 내용을 입력합니다"
  3. `click` — `Selector(role="Button", identifier="SendButton", ancestor="ComposeWindow")`, `is_submit=true` — intent: "메일을 전송합니다"
- **Postcondition**: 편지함(Sent) 목록에 새 항목이 나타나고 작성창(`ComposeWindow`)이 사라짐.
- **Risk**: `confirm` (3단계) — 제3자 대상 커뮤니케이션(발송)은 §1.5 Always Confirm 항목. 발송 직전 1회 확인.
- **Runnable-now**: yes.

### 3. 엄마한테 문자 보내줘
- **Target**: Messages (`com.apple.MobileSMS`)
- **Steps**:
  1. `click` — `Selector(role="Row", title="엄마", ancestor="ConversationList")` — intent: "엄마와의 대화를 엽니다"
  2. `type_text` — `params={"text":"오늘 저녁에 갈게요"}` — intent: "메시지를 입력합니다"
  3. `press_key` — `params={"key":"Return"}` — intent: "전송합니다"
- **Postcondition**: 대화 말풍선 목록의 마지막 항목이 보낸 텍스트와 일치하고 "전송됨" 상태.
- **Risk**: `confirm` (3단계) — 제3자 커뮤니케이션, 발송 후 취소 불가.
- **Runnable-now**: partial — Messages.app의 AX 트리는 대화 목록/검색 구간에서 노출이 불안정한 사례가 보고됨; identifier 확정 필요.

### 4. 엄마한테 페이스타임 걸어줘
- **Target**: FaceTime (`com.apple.FaceTime`)
- **Steps**:
  1. `click` — `Selector(role="Button", identifier="NewFaceTimeButton")` — intent: "새 통화를 시작합니다"
  2. `type_text` — `params={"text":"엄마"}` — intent: "받는 사람을 검색합니다"
  3. `click` — `Selector(role="Row", ancestor="ContactSuggestions", allow_nth=0)` — intent: "검색된 연락처를 선택합니다"
  4. `click` — `Selector(role="Button", identifier="FaceTimeCallButton")`, `is_submit=true` — intent: "전화를 겁니다"
- **Postcondition**: 통화 화면에 "Calling…" 또는 "Connected" 상태 텍스트가 나타남.
- **Risk**: `confirm` (4단계) — 실시간으로 제3자에게 연결을 시도하는 행위는 발송과 동급으로 취급.
- **Runnable-now**: partial — 연락처 검색 결과가 이름만으로 유일 매칭되지 않을 수 있음(`allow_nth=0`가 동명이인에 취약); 실기기 검증 필요.

### 5. 안 읽은 메일 몇 개야?
- **Target**: Mail (`com.apple.mail`)
- **Steps**:
  1. `click` — `Selector(role="Row", title="받은편지함", ancestor="MailboxSidebar")` — intent: "받은편지함을 확인합니다"
- **Postcondition**: `Postcondition(field=Selector(role="Row", title="받은편지함"), contains=<숫자>)` — 사이드바 안읽음 배지 숫자를 읽어 내레이션.
- **Risk**: `none` — 순수 열람.
- **Runnable-now**: yes.

---

## 2. Calendar / Reminders (일정·할일)

| # | 한국어 발화 | risk | runnable |
|---|---|---|---|
| 6 | "오늘 일정 뭐 있어?" | none | yes |
| 7 | "내일 오후 3시에 병원 예약 추가해줘" | preapproved | yes |
| 8 | "이번 주에 약속 있는 날 알려줘" | none | yes |
| 9 | "매일 아침 9시에 약 먹으라고 알려줘" | preapproved | yes |

### 6. 오늘 일정 확인
- **Target**: Calendar (`com.apple.iCal`)
- **Steps**:
  1. `click` — `Selector(role="Button", identifier="TodayButton", title="오늘")` — intent: "오늘 날짜로 이동합니다"
  2. `click` — `Selector(role="Button", identifier="DayViewButton")` — intent: "하루 보기로 전환합니다"
- **Postcondition**: `Postcondition(contains=<오늘 날짜 문자열>)` — 일간 뷰 헤더에 오늘 날짜 표시, 이벤트 목록 낭독.
- **Risk**: `none` — 열람뿐.
- **Runnable-now**: yes.

### 7. 내일 병원 예약 추가
- **Target**: Calendar (`com.apple.iCal`)
- **Steps**:
  1. `click` — `Selector(role="Button", identifier="AddEventButton", ancestor="CalendarToolbar")` — intent: "새 일정을 만듭니다"
  2. `set_value` — `Selector(role="TextField", identifier="EventTitleField")`, `params={"value":"병원"}` — intent: "제목을 '병원'으로 입력합니다"
  3. `set_value` — `Selector(role="TextField", identifier="EventStartTimeField")`, `params={"value":"내일 오후 3:00"}` — intent: "시간을 내일 오후 3시로 설정합니다"
  4. `click` — `Selector(role="Button", title="완료", ancestor="EventEditPopover")` — intent: "저장합니다"
- **Postcondition**: `Postcondition(field=Selector(role="Button", title="병원"), contains="병원")` — 캘린더 그리드 내일 3시 칸에 "병원" 블록 표시.
- **Risk**: `preapproved` — 본인 캘린더에만 반영, 제목/날짜/시간이 발화에 전부 명시됨(§1.5 Pre-approval OK 취지와 동일한 "내용 명시된 본인 데이터 생성").
- **Runnable-now**: yes.

### 8. 이번 주 약속 있는 날
- **Target**: Calendar (`com.apple.iCal`)
- **Steps**:
  1. `click` — `Selector(role="Button", identifier="WeekViewButton")` — intent: "주간 보기로 전환합니다"
- **Postcondition**: `Postcondition(contains="Week")` — 주간 뷰 렌더링 확인 후 이벤트 있는 요일 낭독.
- **Risk**: `none`.
- **Runnable-now**: yes.

### 9. 매일 9시 약 알림
- **Target**: Reminders (`com.apple.reminders`)
- **Steps**:
  1. `click` — `Selector(role="Button", identifier="NewReminderButton", ancestor="ReminderListView")` — intent: "새 할일을 만듭니다"
  2. `type_text` — `params={"text":"약 먹기"}` — intent: "'약 먹기'라고 입력합니다"
  3. `click` — `Selector(role="Checkbox", identifier="RepeatDaily", ancestor="ReminderDetailPopover")` — intent: "매일 반복으로 설정합니다"
  4. `set_value` — `Selector(role="TextField", identifier="ReminderTimeField")`, `params={"value":"09:00"}` — intent: "시간을 오전 9시로 맞춥니다"
  5. `click` — `Selector(role="Button", title="완료", ancestor="ReminderDetailPopover")` — intent: "저장합니다"
- **Postcondition**: `Postcondition(contains="9:00 AM")` — 목록에 "약 먹기" 행과 "매일 오전 9:00" 표시.
- **Risk**: `preapproved` — 본인 데이터, 내용/시간/반복 조건이 발화에 전부 명시.
- **Runnable-now**: yes.

---

## 3. Media (음악·볼륨·사진)

| # | 한국어 발화 | risk | runnable |
|---|---|---|---|
| 10 | "음악 틀어줘" | none | yes |
| 11 | "소리 좀 키워줘" | none | yes |
| 12 | "최근 가족 사진 보여줘" | none | yes |
| 13 | "이 사진 확대해서 보여줘" | none | partial |

### 10. 음악 재생
- **Target**: Music (`com.apple.Music`)
- **Steps**:
  1. `click` — `Selector(role="Button", identifier="PlayButton", ancestor="PlayerControls")` — intent: "재생 버튼을 누릅니다"
- **Postcondition**: `Postcondition(field=Selector(identifier="NowPlayingTitle"), contains=<곡 제목>)` — 재생 버튼이 일시정지 아이콘으로 바뀌고 곡 제목이 표시됨.
- **Risk**: `none` — 재생/정지는 즉시 되돌릴 수 있는 비파괴 조작.
- **Runnable-now**: yes.

### 11. 볼륨 올리기
- **Target**: 시스템 전역(미디어 키), 앱 지정 불필요
- **Steps**:
  1. `press_key` — 선택자 없음, `params={"key":"VolumeUp"}` — intent: "소리를 키웁니다"
- **Postcondition**: `Postcondition(field=Selector(role="Slider", identifier="SystemVolumeSlider"), contains=<증가된 값>)` — 볼륨 HUD 값 상승.
- **Risk**: `none`.
- **Runnable-now**: yes.

### 12. 최근 가족 사진 보기
- **Target**: Photos (`com.apple.Photos`)
- **Steps**:
  1. `click` — `Selector(role="Button", title="Recents", ancestor="PhotosSidebar")` — intent: "최근 항목으로 이동합니다"
  2. `click` — `Selector(role="Cell", ancestor="PhotoGrid", allow_nth=0)` — intent: "가장 최근 사진을 엽니다"
  3. `secondary_action` — `Selector(role="Cell", ancestor="PhotoGrid", allow_nth=0)`, `params={"action":"AddToFavorites"}` — intent: "즐겨찾기에 추가할까요? (선택 시)"
- **Postcondition**: 사진 상세 뷰에 확대된 이미지와 촬영일 메타데이터 표시.
- **Risk**: `none` — 즐겨찾기 토글까지 포함해도 본인 데이터 내 가역적 조작.
- **Runnable-now**: yes.

### 13. 사진 확대
- **Target**: Photos (`com.apple.Photos`)
- **Steps**:
  1. `click` — `Selector(role="Cell", ancestor="PhotoGrid", allow_nth=0)` — intent: "사진을 엽니다"
  2. `scroll` — `Selector(role="Image", identifier="PhotoDetailView")`, `params={"direction":"up","pages":2}` — intent: "확대합니다"
- **Postcondition**: 이미지 뷰의 배율 속성이 증가(예: 100%→150%) — 정확한 AX 배율 노출 여부는 앱 버전에 따라 다름.
- **Risk**: `none`.
- **Runnable-now**: partial — scroll을 핀치줌으로 매핑하는 제스처가 Photos에서 신뢰성 있게 동작하는지 기기 검증 필요.

---

## 4. Everyday utilities (계산기·날씨·메모·시계)

| # | 한국어 발화 | risk | runnable |
|---|---|---|---|
| 14 | "7 곱하기 8은 얼마야?" | none | yes ✅ 실증 완료 |
| 15 | "오늘 날씨 어때?" | none | yes |
| 16 | "장 볼 목록 적어줘: 우유, 계란, 빵" | preapproved | yes |
| 17 | "아침 7시에 알람 맞춰줘" | preapproved | partial |
| 18 | "'자애'가 무슨 뜻이야?" | none | yes |

### 14. 계산기 (7×8)
- **Target**: Calculator (`com.apple.calculator`)
- **Steps** (`CalculatorKeypadView`를 ancestor로 스코프, PROTOCOL.md P0에서 실기기 실증됨):
  1. `click` — `Selector(role="Button", identifier=["AllClear","Clear"], ancestor="CalculatorKeypadView")` — intent: "초기화"
  2. `click` — `Selector(role="Button", identifier="Seven", ancestor="CalculatorKeypadView")` — intent: "7"
  3. `click` — `Selector(role="Button", identifier="Multiply", ancestor="CalculatorKeypadView")` — intent: "곱하기"
  4. `click` — `Selector(role="Button", identifier="Eight", ancestor="CalculatorKeypadView")` — intent: "8"
  5. `click` — `Selector(role="Button", identifier="Equals", ancestor="CalculatorKeypadView")` — intent: "계산"
- **Postcondition**: `Postcondition(field=Selector(role="ScrollArea", identifier="StandardInputView"), equals="56")`.
- **Risk**: `none` — 계산은 데이터 변경/전송이 전혀 없음.
- **Runnable-now**: yes — 동일 패턴(12×34)으로 실기기 E2E PASS 이미 완료(`nlc/flow.py::calculator_12x34`).

### 15. 오늘 날씨
- **Target**: Weather (`com.apple.weather`)
- **Steps**:
  1. `click` — `Selector(role="Row", ancestor="LocationSidebar", allow_nth=0)` — intent: "현재 위치 날씨를 확인합니다"
- **Postcondition**: `Postcondition(field=Selector(role="StaticText", identifier="CurrentTemperatureLabel"), contains="°")`.
- **Risk**: `none`.
- **Runnable-now**: yes.

### 16. 장보기 메모
- **Target**: Notes (`com.apple.Notes`)
- **Steps**:
  1. `click` — `Selector(role="Button", identifier="NewNoteButton", ancestor="NotesToolbar")` — intent: "새 메모를 만듭니다"
  2. `type_text` — `params={"text":"장보기 목록\n우유\n계란\n빵"}` — intent: "목록을 적습니다"
- **Postcondition**: `Postcondition(field=Selector(role="TextArea", identifier="NoteBodyTextView"), contains="계란")`.
- **Risk**: `preapproved` — 본인 기기 로컬 콘텐츠 생성, 내용이 발화에 전부 명시(파일 생성은 §1.5 "파일 이동/이름변경"과 동급 취급).
- **Runnable-now**: yes.

### 17. 아침 7시 알람
- **Target**: Clock (`com.apple.clock`)
- **Steps**:
  1. `click` — `Selector(role="Button", title="Alarm", ancestor="ClockSidebar")` — intent: "알람 탭으로 이동합니다"
  2. `click` — `Selector(role="Button", identifier="AddAlarmButton")` — intent: "새 알람을 추가합니다"
  3. `set_value` — `Selector(role="TextField", identifier="AlarmTimeField")`, `params={"value":"07:00 AM"}` — intent: "오전 7시로 설정합니다"
  4. `click` — `Selector(role="Button", title="Save", ancestor="AlarmEditSheet")` — intent: "저장합니다"
- **Postcondition**: `Postcondition(field=Selector(role="Row", ancestor="AlarmList", allow_nth=0), contains="7:00 AM")`.
- **Risk**: `preapproved` — 본인 기기 설정, 시간이 발화에 명시, 파괴적/제3자 영향 없음.
- **Runnable-now**: partial — Clock.app은 macOS Sonoma에서 추가된 비교적 신규 앱이라 AX identifier가 이 카탈로그만큼 검증되지 않음.

### 18. 사전 검색
- **Target**: Dictionary (`com.apple.Dictionary`)
- **Steps**:
  1. `set_value` — `Selector(role="SearchField", identifier="DictionarySearchField")`, `params={"value":"자애"}` — intent: "'자애'를 검색합니다"
  2. `press_key` — `params={"key":"Return"}` — intent: "검색을 실행합니다"
- **Postcondition**: `Postcondition(field=Selector(role="TextArea", identifier="DefinitionView"), contains="자애")`.
- **Risk**: `none`.
- **Runnable-now**: yes.

---

## 5. System (와이파이·밝기·다크모드·접근성)

| # | 한국어 발화 | risk | runnable |
|---|---|---|---|
| 19 | "와이파이 꺼줘" | confirm | yes |
| 20 | "화면 좀 밝게 해줘" | none | yes |
| 21 | "다크모드로 바꿔줘" | preapproved | yes |
| 22 | "글씨가 안 보여, 화면 확대 기능 켜줘" | confirm | yes |
| 23 | "우리 집 와이파이 'home_5G'에 연결해줘" | handoff | partial |

### 19. 와이파이 끄기/켜기
- **Target**: System Settings (`com.apple.systempreferences`, Wi-Fi 패널)
- **Steps**:
  1. `click` — `Selector(role="Row", title="Wi-Fi", ancestor="SettingsSidebar")` — intent: "Wi-Fi 설정으로 이동합니다"
  2. `click` — `Selector(role="Switch", identifier="WiFiPowerToggle")` — intent: "Wi-Fi를 끕니다"
- **Postcondition**: `Postcondition(field=Selector(role="Switch", identifier="WiFiPowerToggle"), equals="0")` — 토글 값 변경 확인.
- **Risk**: `confirm` — §1.5 "시스템 설정 변경"에 명시된 Always Confirm 항목. 사용자 본인의 인터넷 연결이 즉시 끊길 수 있어 실행 직전 재확인.
- **Runnable-now**: yes.

### 20. 화면 밝게
- **Target**: 시스템 전역(미디어 키)
- **Steps**:
  1. `press_key` — `params={"key":"BrightnessUp"}` — intent: "화면을 밝게 합니다"
- **Postcondition**: 밝기 HUD 오버레이의 막대 수/값이 증가.
- **Risk**: `none` — 볼륨과 동일하게 즉시 가역적이고 파괴적이지 않은 편의 조정. 설정 앱 진입 없이 되돌릴 수 있어 Wi-Fi/접근성 토글과 구분.
- **Runnable-now**: yes.

### 21. 다크모드 전환
- **Target**: System Settings (`com.apple.systempreferences`, Appearance 패널)
- **Steps**:
  1. `click` — `Selector(role="Row", title="Appearance", ancestor="SettingsSidebar")` — intent: "화면 모드 설정으로 이동합니다"
  2. `click` — `Selector(role="RadioButton", identifier="AppearanceDark")` — intent: "어두운 모드로 바꿉니다"
- **Postcondition**: `Postcondition(field=Selector(role="RadioButton", identifier="AppearanceDark"), contains="selected")`.
- **Risk**: `preapproved` — 순수 외형 변경이며 "다시 밝게 해줘" 한마디로 즉시 복구 가능. Wi-Fi처럼 연결성·보안에 영향을 주지 않아 §1.5 "시스템 설정 변경" 중에서도 가장 낮은 리스크로 판단.
- **Runnable-now**: yes.

### 22. 손쉬운 사용 화면 확대 켜기
- **Target**: System Settings (`com.apple.systempreferences`, Accessibility > Zoom 패널)
- **Steps**:
  1. `click` — `Selector(role="Row", title="Zoom", ancestor="AccessibilitySidebar")` — intent: "화면 확대 설정으로 이동합니다"
  2. `click` — `Selector(role="Checkbox", identifier="EnableZoomToggle")` — intent: "화면 확대 기능을 켭니다"
- **Postcondition**: `Postcondition(field=Selector(role="Checkbox", identifier="EnableZoomToggle"), contains="checked")`.
- **Risk**: `confirm` — 다크모드와 달리 화면 전체 확대/스크롤 동작을 바꿔 저활용층 사용자가 예상치 못하게 혼란을 겪을 수 있음. §1.5 "시스템 설정 변경" 중 실사용 영향이 큰 쪽으로 판단해 확인 유지.
- **Runnable-now**: yes.

### 23. 비밀번호 있는 와이파이 연결
- **Target**: System Settings (`com.apple.systempreferences`, Wi-Fi 패널)
- **Steps**:
  1. `click` — `Selector(role="Row", title="Wi-Fi", ancestor="SettingsSidebar")` — intent: "Wi-Fi 목록을 엽니다"
  2. `click` — `Selector(role="Row", title="home_5G", ancestor="WiFiNetworkList")` — intent: "'home_5G'를 선택합니다"
  3. **handoff** — `{"reason": "password_entry", "resume_signal": "field_filled"}` — "비밀번호는 직접 입력해 주세요. 입력이 끝나면 이어서 연결할게요."
  4. `click` — `Selector(role="Button", title="Join", ancestor="WiFiPasswordSheet")` — intent: "연결합니다" (handoff resume 이후에만 실행)
- **Postcondition**: `Postcondition(contains="Connected")` — Wi-Fi 상태 행이 "home_5G / Connected"로 표시.
- **Risk**: `handoff` — 비밀번호 입력은 §1.5·본 요청 예시 모두에서 Hand-off로 명시. 봇은 절대 비밀번호 필드를 대신 채우지 않음.
- **Runnable-now**: partial — handoff 메커니즘 자체는 `nlc/session.py`에 구현되어 있으나, 이 구체 플로우(Join 시트 identifier, resume 이후 재관찰)는 기기에서 authoring/검증되지 않음.

---

## 6. Web basics (검색·읽기·간단한 양식)

| # | 한국어 발화 | risk | runnable |
|---|---|---|---|
| 24 | "오늘 뉴스 검색해줘" | none | needs-web |
| 25 | "이 페이지 내용 읽어줘" | none | needs-web |
| 26 | "이름이랑 전화번호 넣어서 설문 제출해줘" | confirm | needs-web |
| 27 | "주문한 물건 배송 언제 오는지 봐줘" | none | needs-web |

### 24. 웹 검색
- **Target**: Safari (`com.apple.Safari`)
- **Steps**:
  1. `press_key` — `params={"key":"cmd+l"}` — intent: "주소창으로 이동합니다"
  2. `type_text` — `params={"text":"오늘 뉴스"}` — intent: "검색어를 입력합니다"
  3. `press_key` — `params={"key":"Return"}` — intent: "검색합니다"
- **Postcondition**: `Postcondition(field=Selector(role="StaticText", identifier="TabTitle"), contains="뉴스")`.
- **Risk**: `none` — 검색 자체는 비파괴 열람.
- **Runnable-now**: needs-web — PROTOCOL.md §4 로드맵상 브라우저 자동화는 Playwright MCP 재사용 예정이며 `nlc/driver.py`(macOS AX)에는 아직 통합되지 않음.

### 25. 페이지 읽어주기
- **Target**: Safari (`com.apple.Safari`)
- **Steps**:
  1. `click` — `Selector(role="Button", identifier="ReaderViewButton", ancestor="AddressToolbar")` — intent: "읽기 모드로 전환합니다"
- **Postcondition**: `Postcondition(field=Selector(role="TextArea", identifier="ReaderContentView"), contains=<본문 일부>)`.
- **Risk**: `none`.
- **Runnable-now**: needs-web.

### 26. 온라인 설문 제출
- **Target**: Safari (`com.apple.Safari`, 일반 웹 양식)
- **Steps**:
  1. `set_value` — `Selector(role="TextField", identifier="name-input", ancestor="FormContainer")`, `params={"value":"홍길동"}` — intent: "이름을 입력합니다"
  2. `set_value` — `Selector(role="TextField", identifier="phone-input", ancestor="FormContainer")`, `params={"value":"010-1234-5678"}` — intent: "전화번호를 입력합니다"
  3. `click` — `Selector(role="Button", identifier="submit-button", ancestor="FormContainer")`, `is_submit=true, into_sensitive_field=true` — intent: "제출합니다"
- **Postcondition**: `Postcondition(contains="제출이 완료되었습니다")`.
- **Risk**: `confirm` — 개인정보(이름·전화번호)를 제3자 서비스로 전송하는 제출 액션. 데이터·목적지가 발화에 명시되어 있어도, 실제 전송(발송과 동급)은 §1.5 Always Confirm 취지에 맞춰 확인. `is_submit=true`가 `nlc/safety.py`의 안전 분류기를 실행 시점에 강제로 상향(escalate)시키는 지점.
- **Runnable-now**: needs-web.

### 27. 배송 조회
- **Target**: Safari (`com.apple.Safari`, 로그인 상태 가정)
- **Steps**:
  1. `click` — `Selector(role="Link", title="주문 내역", ancestor="MyPageNav")` — intent: "주문 내역으로 이동합니다"
  2. `click` — `Selector(role="Row", ancestor="OrderList", allow_nth=0)` — intent: "가장 최근 주문을 엽니다"
- **Postcondition**: `Postcondition(contains="배송")`.
- **Risk**: `none` — 이미 로그인된 계정 정보를 읽기만 함(별도 로그인이 필요하면 그 단계는 §1.5 Pre-approval OK "로그인"에 해당하여 `preapproved`).
- **Runnable-now**: needs-web.

---

## 7. Documents (문서 열기·읽어주기·저장)

| # | 한국어 발화 | risk | runnable |
|---|---|---|---|
| 28 | "'계약서'라는 파일 열어줘" | none | yes |
| 29 | "이 문서 읽어줘" | none | partial |
| 30 | "이 메모를 '오늘회의록'으로 저장해줘" | preapproved | yes |

### 28. 문서 열기
- **Target**: Spotlight (`com.apple.Spotlight`) → 결과 앱(Preview/`com.apple.Preview`)
- **Steps**:
  1. `press_key` — `params={"key":"cmd+space"}` — intent: "검색을 엽니다"
  2. `type_text` — `params={"text":"계약서"}` — intent: "'계약서'를 검색합니다"
  3. `press_key` — `params={"key":"Return"}` — intent: "가장 위 결과를 엽니다"
- **Postcondition**: `Postcondition(field=Selector(role="Window", identifier="DocumentWindow"), contains="계약서")`.
- **Risk**: `none` — 기존 파일 열람/보기, 삭제·이동·이름변경 없음.
- **Runnable-now**: yes.

### 29. 문서 읽어주기
- **Target**: Preview (`com.apple.Preview`)
- **Steps**:
  1. `click` — `Selector(role="MenuBarItem", title="Edit")` — intent: "편집 메뉴를 엽니다"
  2. `click` — `Selector(role="MenuItem", title="말하기")` — intent: "말하기 메뉴로 이동합니다"
  3. `click` — `Selector(role="MenuItem", title="말하기 시작")` — intent: "문서를 소리 내어 읽습니다"
- **Postcondition**: OS 음성 합성 시작(현재 AX 트리로 직접 검증 어려움 — best-effort; 후속 관찰에서 "말하기 중지" 메뉴 항목 등장 여부로 간접 확인).
- **Risk**: `none` — 읽기 전용, 데이터 변경 없음.
- **Runnable-now**: partial — macOS 내장 "말하기" 메뉴 경로는 앱/로케일별로 문구가 달라 identifier 확정과 완료 검증 방식(postcondition)에 추가 설계가 필요.

### 30. 다른 이름으로 저장
- **Target**: TextEdit (`com.apple.TextEdit`)
- **Steps**:
  1. `press_key` — `params={"key":"cmd+shift+s"}` — intent: "다른 이름으로 저장 창을 엽니다"
  2. `set_value` — `Selector(role="TextField", identifier="SaveAsNameField", ancestor="SavePanel")`, `params={"value":"오늘회의록"}` — intent: "파일 이름을 '오늘회의록'으로 정합니다"
  3. `click` — `Selector(role="Button", title="저장", ancestor="SavePanel")` — intent: "저장합니다"
- **Postcondition**: `Postcondition(field=Selector(role="Window", identifier="DocumentWindow"), contains="오늘회의록")`.
- **Risk**: `preapproved` — §1.5 Pre-approval OK "파일 이동/이름변경"과 동일 범주. 파일명이 발화에 명시, 본인 로컬 파일.
- **Runnable-now**: yes.

---

## First 5 to build

가치(고령/저활용 사용자에게 얼마나 자주·절실한 요청인지) × 리스크(낮을수록
승인 마찰 없이 자율 실행 가능) × runnable-now(`yes`만) 를 기준으로 우선순위를
매겼다.

1. **#14 계산기 (7×8, `none`)** — 이미 `nlc/flow.py::calculator_12x34`로
   실기기 E2E PASS된 패턴을 그대로 재사용/변형만 하면 됨. 회귀 테스트 겸
   "이 제품이 실제로 동작한다"는 첫 데모로 가장 안전하고 빠른 승리.
2. **#15 오늘 날씨 (`none`)** — 하루 1회 이상 나오는 초고빈도 요청, 스텝 1개,
   승인 게이트 없음. 계산기 다음으로 가장 저비용·고빈도 조합.
3. **#6 오늘 일정 확인 (`none`)** — "오늘 뭐 있어?"는 고령 사용자가 스스로
   앱을 뒤지기 가장 힘들어하는 유형(작은 글씨의 그리드 UI)이라 대리 조작
   가치가 크고, 리스크는 순수 열람.
4. **#16 장보기 메모 (`preapproved`)** — 최초의 "쓰기" 플로우로 선택. 리스크가
   낮은 preapproved 등급에서 `type_text` + 앱 데이터 생성 경로를 검증해두면
   이후 preapproved 플로우(알람, 저장 등) 확장이 쉬워짐.
5. **#7 내일 병원 예약 추가 (`preapproved`)** — `set_value`로 구조화된
   필드(제목/시간)를 채우는 첫 멀티필드 쓰기 플로우. 일정 추가는 사용 빈도와
   실패 시 체감 손해(놓친 병원 예약)가 커서 가치가 높고, 본인 캘린더에만
   반영되어 리스크는 여전히 낮다.

**선정 원칙**: 처음 5개는 전부 `runnable-now: yes`이고 리스크가 `none` 또는
`preapproved`까지만 — `confirm`/`handoff` 게이트(발신, Wi-Fi, 비밀번호 등)는
안전 정책(`nlc/safety.py`)과 확인 대화 UX가 먼저 검증된 뒤 다음 배치로 넘긴다.
읽기 3개(계산기·날씨·일정)로 관찰·resolver·postcondition 경로를 굳히고, 쓰기
2개(메모·일정 추가)로 `set_value`/`type_text` + preapproved 승인-없이-진행
경로를 검증하는 구성이다.
