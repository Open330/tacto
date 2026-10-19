# 지원 경로와 검증 범위

2026-10-04 개발 snapshot. 기능을 제공하는 것, 실제 결과를 확인한 것,
범용 앱에서 안정적으로 동작하는 것은 서로 다른 기준이다.

| 기능 | macOS 접근성 / signed helper | 별도 Chromium 페이지 | 시각 API |
| --- | --- | --- | --- |
| 관측 | AX tree·probe·selection | CDP AX tree·DOM identity·probe | 브라우저 CSS viewport PNG; signed helper의 소유 native 창 캡처 실측 |
| 전체 문서 본문 | TextEdit 완전 본문·문서 token·경로, direct/signed 실측; 추가 경로 선택·token 범위 관측은 주입 회귀 검증, 다중 실문서 검증은 남음; 형식·최종 저장 미검증 | 별도 API 없음 | 이미지로 전체 본문을 증명하지 않음 |
| 클릭·입력 | 접근성 요소; host 승인된 Window-bound point와 opaque window input 별도 제공 | 관측한 DOM 요소 | browser host 좌표; native 명시한 scope에서 fresh image와 창 결합·host 승인·결과 검증 |
| 스크롤 | helper capability 확인 | 대상의 scroll container, 행동 후 재관찰 | 별도 좌표 scroll API 없음 |
| 키보드 | Python/signed: 배경 입력 요소 탐색 키; host 승인 opaque Window에 여섯 탐색 키·최대64 UTF16 Unicode. Escape는 사용자 중단 | 같은 탐색 키와 Escape | host input_actions에 명시한 native key/text image proposal 지원 |
| HTML select | 해당 없음 | value·label 고정, 변경·중복·disabled 거절 | 해당 없음 |
| custom ARIA 선택 | 해당 없음 | 명시적 combobox/listbox 연결, 열기와 선택 분리 | 해당 없음 |
| native 선택 | signed helper: owned AppKit PopUpButton 및 측정된 NSComboBox의 열기→label 선택 실제 통과; ComboBox는 물리 popup 창·행을 고정한 클릭과 수신 전이를 검증. 다른 AXComboBox 구조는 unsupported | 해당 없음 | 해당 없음 |
| flow 재생 | 동일 backend·대상 binding | 동일 origin·대상 binding | 좌표 click을 자동 flow로 학습하지 않음 |
| 저장·전송 완료 | 앱별 host receipt 연결 필요 | 호스트가 제공한 event-bound receipt 필요 | 이미지나 모델의 done은 완료 증거가 아님 |

브라우저 드라이버는 기존 Chrome/Safari 사용자 세션에 자동 연결하지 않는다.
`tacto do`는 현재 macOS 경로이며, 브라우저와 시각 API는 Python에서 명시적으로
사용한다. wheel 설치만으로 서명된 macOS helper가 설치되지는 않는다.

저장·전송의 host receipt 검사는 일반 실행과 flow replay에 연결되어 있다. 기존 저장
flow가 창 제목만 보고 완료하던 경로도 수정했다. host receipt를 제공하지 않는 앱은
저장 액션을 실행했더라도 완료가 검증되지 않았다고 반환한다. 앱별 어댑터 연결은
별도 작업이며, 현재 모든 앱의 저장 결과를 검증할 수 있다는 뜻은 아니다.

native ComboBox의 signed 선택 경로는 AppKit direct-child Button1,
TextField≤1,ScrollArea≤1의 구조만 분류한다. 편집 불가 owned NSComboBox는 실제
결정론 및 Codex 모델 과제를 통과했다. editable 변형, 웹 ARIA/Electron의
다른 AX 자식 구조, 위쪽 popup·잘린 행은 별도 실제 검증이 남아 있다. 누락·잘못된
AX 데이터는 closed가 아니라 unknown/unsupported이며 열기·선택 전에 거부한다.
목록을 열 때 physical popup ID를 고정하지 못했으면 popup_id_unavailable로
선택을 제공하지 않는다. 이 경로를 direct Python native backend의 실제 지원
증거나 legacy atomic select_option ComboBox 완료로 일반화하지 않는다.

custom ARIA 선택은 같은 control이 명시한 listbox 안에서만 수행한다. 기본 완료
검사는 같은 option의 `aria-selected`와 유일한 선택 상태다. 선택 후 option DOM을
제거하는 위젯은 제한적으로 지원한다. 같은 control이 열린 상태에서 닫힌 상태로
바뀌고, 선택 전 관측한 readout이 선택한 항목의 정확한 label/value와 다른 값에서
일치하는 값으로 바뀌어야 한다. 인정하는 readout은 기존 `aria-valuetext`, 실제
readonly input의 값, 실제 button 전체 표시 문자열뿐이다. 편집 가능한 검색 입력,
변화 없는 값, 다른 Result 필드, 교체된 listbox나 중복 ID는 선택 증거로 쓰지 않는다.
이 UI 상태 검증은 파일 저장·메시지 전송의 host receipt를 대신하지 않는다.

## 사용자 포인터와 포커스

native Python 드라이버와 signed helper는 메뉴·선택을 위해 앱을 강제로 활성화하지
않는다. Python의 전역 HID 좌표 클릭과 포인터 복원 경로도 제거했다. host가 명시한
Window-bound point 입력은 별도 승인·fresh 창 결합·입력 직전 재검증을 요구한다.
owned native canvas에서 실제 receiving 결과를 확인했으며, 범용 앱에서 AX 실패를
자동으로 좌표 클릭으로 바꾸는 기능은 아니다. [좌표 입력 범위](native-visual.md)와
[opaque 창 입력](native-window-input.md)을 참조한다.
type_text는 지정 요소가 해당 앱의 실제 입력 대상인지 확인하고,
입력 중 대상이 바뀌면 나머지 문자를 보내지 않는다.

전경 prepare_window는 실제 전경 앱 안의 AXMain/AXFocused 쓰기를 사용한다.
signed helper와 Python driver의 배경 준비는 검증된 PID/window 이벤트 경로를
제공한다. 배경 준비는 입력 상태 변화나 중단·실패 시
추가 전송을 멈추고 전송 이후 실패를 unknown으로 보고한다. owned signed 시험에서
실제 key window와 responder 메뉴 효과를 확인했지만 실제 앱·동시 입력은 미검증이다.
전경 판정은 공개 HIServices API로 읽으며, 읽기 실패와 쓰기 중 전경 변경을
허용 상태로 처리하지 않는다. 자세한 계약은 [현재 창 입력 경로](native-window-input.md)에 있다.

이 변경만으로 모든 앱에서 포커스가 유지된다고 보장하지 않는다. 앱이 AX 요청에
반응해 스스로 활성화되는 경우와 메뉴·팝업·저장의 앱별 차이를 실제로 측정해야 한다.
계산기의 숫자 입력·지우기·Scientific/Basic 모드 전환은 Python과 signed helper
양쪽에서 실제 결과와 단계당 175~196개의 포인터/포커스 표본을 확인했다. TextEdit
로컬 문서의 Python 한글·이모지 입력도 정확한 본문과 61개 무변화 표본을 확인했다.
signed helper의 텍스트 입력·스크롤·팝업·저장과 사용자 동시 입력은 후속 검증이
남아 있다. 표본 사이의 짧은 변화까지 배제하는 증거는 아니다.
G0 원시 결과와 한계（로컬 기록: `../reviews/2026-09-19/background-input.md`）를 참조한다.

owned 네이티브 키/시각 입력9개 시험의 `--require-selection` 옵션은 numeric caret/
selection 관측을 필수로 한다. 준비되지 않으면 fixture·입력·모델 호출 전에
중단한다. 현재 전경 요소의 미지원으로 실제 실행은 INCONCLUSIVE였으며, 과거
3개 항목 감사 성공을 caret 또는 동시 사용자 typing 보존으로 확대하지 않는다.
[필수 관측 옵션](input-observation.md)에 명령과 남은 검증 조건이 있다.

## 모델과 예산

서명된 헬퍼의 `evidence.save_panel`은 현재 관측의 창에 붙은 저장 패널에서
디렉터리·파일명·명시된 형식·체크박스를 읽고 실제 Save 버튼의 관측 인덱스를
결합한다. 패널 토큰은 설정·객체·프로세스가 달라지면 거절된다. 응답은
`authorizes_save: false`이며, 원본 문서 형식·첨부나 저장 완료를 증명하지 않는다.
임시 패널을 이용한 signed 경로 시험을 통과했다. 자세한 범위는
[저장 패널 관측](save-directory-observation.md)에 있다.

서명된 helper의 `action.click.save_panel_guard`는 호스트가 명시적으로 승인한
indexed click에 패널 토큰을 결합한다. 입력 직전에 native 상태를 다시 검증하며
한 번 시도한 토큰은 재사용할 수 없다. own NSSavePanel 완료 콜백을 확인했지만
TextEdit 문서 저장·서식·첨부 보존이나 원자적인 설정 검증을 증명하지 않는다.

텍스트 계획 provider는 명시한 provider를 유지한다. 실제 모델 검증은 로컬
Chromium 과제를 Codex·Claude 백본으로 수행했다. 다른 provider가 설정 목록에
있다는 사실만으로 같은 과제를 통과했다고 말하지 않는다. 이미지 adapter는 현재
Codex·Claude CLI를 지원하며 두 백본의 실제 canvas 클릭을 확인했다.

후보 paging은 모든 후보의 원래 번호를 유지하며, 기본 prompt 예산은 UTF-8 byte를
보수적으로 센다. 정확한 token 계수가 필요하면 provider tokenizer를 전달한다. 이 예산은 Tacto 요청 본문에 적용되며 provider CLI의 숨은 시스템 지침·도구 정의까지 포함한 전체 context 상한은 아니다.
넘치는 후보를 조용히 자르지 않으며 읽지 못한 페이지는 명시적으로 남긴다.

일반 실행의 시간·취소 검사는 호출 사이에서 확인하는 방식이다. 실행 중인 driver나
모델 호출을 즉시 끊는 보장은 없다. provider timeout은 로컬 프로세스 그룹을
종료하지만 원격 작업 취소까지 보장하지 않는다. 결과를 모르는 저장·전송은
성공으로 바꾸거나 자동으로 재시도하지 않는다.

## 결과 해석

현재 실험은 로컬 시험 페이지·임시 파일·일부 네이티브 앱 경로에 한정된다.
새 과제의 완료율, 앱별 장기 복구, Codex computer use와의 동일 조건 성능 비교는
별도 평가가 필요하다. 기준 시스템을 실행할 도구가 없는 항목은 `unavailable`로
기록하며 0%나 100%로 환산하지 않는다.

- [설치](installation.md)
- [시각 API](visual.md)
- [네이티브 캡처와 권한 한계](native-visual.md)
- 전체 goal（로컬 기록: `../reviews/2026-09-19/next-goal.md`）
- 최신 단계별 결과（로컬 기록: `../reviews/2026-09-19/goal-progress-4.md`）

파일 receipt는 파일 쓰기만, 전송 receipt는 전송만 증명한다. 다른 중요한 효과가 함께 선언되면 같은 receipt로 완료 처리하지 않는다. 실제 저장 항목 삭제용 receipt는 아직 지원하지 않는다.


서명된 helper의 `evidence.document_rtf_export`는 기존 문서 token의 full AX
range RTF bytes를 읽는다. 반복 text/export·identity/path·기한·2MiB 출력 제한과
클라이언트 digest 검증을 실행한다. 원본 형식이나 첨부 완전성·저장 승인을
보장하지 않으며 document_snapshot은 OFF다. native handler의 주입 시험을
통과했지만 첫 실제 TextEdit 시험은 process 부재로 문서 open 전에 거절됐다.
[RTF range export 범위](textedit-save.md)를 참조한다.


Opaque window input now has a raw host-authorized Python/signed path. Owned
inaccessible responders must expose independent receiving evidence; an event
pair never proves completion. See [window input](native-window-input.md). General visual and registered Flow key/text proposals require explicit host
input_actions and receiving evidence. Real app IME/typing and caret preservation
still require actual-user evidence.

### Signed channel failure and recovery

The inherited launcher channel is permanently invalidated after an IO error,
EOF, malformed JSON, or a response without an object and nonempty `type`.
The SDK closes the write half and stream; a later request on that channel is
refused before sending. Buffered replies cannot be reused as a later action's
acknowledgment. A valid `type: error` response remains a protocol response and
does not invalidate the channel by itself. A serialization error before IO
does not send anything or invalidate the channel.

A lost reply does not prove the action was cancelled or never started. Flow
and general execution retain `effect: unknown` after a driver exception and
stop consequential work rather than retry Save/Send. A fresh launcher starts a
new observation scope; inspect the receiving state before authoring further
work. Channel invalidation supplies no disk, producer, or recipient receipt.

Flow 관측 실패 처리도 실행 기록과 분리한다(2026-10-11). observe/wait 예외와
잘못된 응답은 `observation_unavailable`로 반환하고 이전 event·commit_evidence를
보존한다. 공개 exception_class 집합 밖 클래스명과 예외 메시지는 기록하지 않는다.
visual 호출 바깥 예외도 parent 결과에 이미 붙은 기록을 보존한다. 아직 parent에
합쳐지지 않은 image/general child의 모든 부분 효과까지 보장하는 것은 아니다.
KeyboardInterrupt/SystemExit은 전파한다. 확인된 커밋이 있어도 필수 후속 관측이
실패하면 replay.ok=false이고 CLI는 두 사실을 구분해 출력하며 exit1을 반환한다.

Helper/direct wait는 사용자 중단·대상 소멸·권한/연결/identity 오류와 모르는 오류를
즉시 반환한다. `no_window_content`의 reason 미첨부/no_visible_window/window_offscreen만
잠깐의 창 전환으로 관측을 계속할 수 있다. lock이 있으면 locked=false여야 하며,
숨김/최소화/잠금/모르는 reason은 반복하지 않는다. 같은 기한 안의 read-only poll이고
활성화·Space 이동·reopen·재입력을 승인하지 않는다. 마감까지 오류면 마지막 오류를
반환한다. Flow는 error 및 legacy timeout.last_obs.error를 판단·복구로 넘기지 않는다.
실제 관측은 성공했으나 조건이 불충족인 timeout만 기존 복구 대상이다.

소유 AppKit 전송의 실제 client-side reply-loss 검증에서 sent1/commit_writes1,
독립 sender transcript receipt1, 닫힌 채널 뒤 following_writes0을 확인했다.
필수 후속 관측 실패로 전체 Flow는 실패이고 commit event는 confirmed로 보존됐다.
응답이 이미 커널 버퍼에 도착했을 수 있으며 helper EPIPE·독립 helper 종료·실제 외부
delivery는 검증하지 않았다. 실기와 첫 준비 실패 기록은 [평가](evaluation.md)에 있다.

후속 helper lifecycle 검증은 인증된 launcher의 helper 연결 audit token에서
PID/pid_version을 전달하고 helper 자기 보고 PID/start_time과 대조한다. 선택적
메타데이터이므로 구버전의 미제공은 지원 불가이며 잘못된 형식/불일치는 handshake를
닫는다. 이 정보는 프로세스 제어·입력 권한을 추가하지 않는다. 동일 libproc 시작
시각으로 same_instance/replaced를 구분하고 ESRCH만 exited, 읽기 거부는 unobservable다.
macOS의 zombie 상세 조회 실패에는 0.5초 제한의 PID/state 조회만 사용한다. 이 fallback은
현재 PID의 zombie 상태이며 시작 시각 일치를 주장하지 않는다. PID가 재사용됐다면
원래 실행은 끝났다는 의미다. 2026-10-11 소유 AppKit 실제 연결 단절에서 helper_before=
same_instance, helper_after_channel_close=exited를 관측했다. helper EPIPE나 원격 모델
취소·과금 종료를 검증한 것은 아니다.

이름으로 선택한 복구 Flow도 부모 실행의 일부로 기록한다. 성공 여부만 반환하던
경로를 보강해 자식 event ID·effects·receipt·provider/visual 기록과 진단을 유지한다.
event에는 recovery/parent_step 출처를 붙이고, 자식 log는 recovery_step을 사용한
64개 제한 중첩 기록으로 보존한다(total/truncated 포함). 복구 실패 또는 unknown
효과는 부모 재시도를 허용하지 않는다. 복구 중 activated_target을 관측하면 두
실패 경로 모두 같은 단계의 추가 입력 전에 target_seen_in_front/not_started로
멈춘다. 정상적인 복구 뒤 재시도는 유지한다. 이는 반환된 자식 ReplayResult의
보존이며, 자식 결과가 반환되기 전 예외·중단의 모든 부분 효과를 보장하지 않는다.

제목 없는 AXSheet도 native/direct tree에서 역할과 부모 경계를 유지한다. 이전에는
Sheet가 생략되고 Cancel만 남아 close_dialog가 닫을 것이 없다고 오인할 수 있었다.
기본 close_dialog는 substring absent 대신 absent_role="Sheet"를 사용한다.
Sheet1/Spreadsheet 같은 제목·값은 실제 Sheet 역할과 구분한다. IR load/bind와
model authoring 모두 이 조건을 유지한다. 이는 관측된 tree 역할 조건이며 숨겨진
노드·다른 앱의 상태나 입력 허가를 증명하지 않는다.

`expect.no_modal`의 native/direct 가드도 AXSheets 외에 모든 창의 직계 자식과
앱 포커스 요소의 조상에서 Sheet/AXModal/AXSheets를 확인한다. AppKit 모달 경계와
포커스 조상이 검증 범위이며 임의의 포커스 밖 깊은 custom subtree를 전부 조사하지
않는다. 내용 트리 전체를 매 행동마다 다시 읽지 않는다. 노드20000/포커스 깊이64/
기한2초와 AX timeout 설정·복원 검사를 적용한다. 읽기 실패·잘못된 응답·예산 초과는
unobservable로 입력을 거절한다. 선택적 AXModal/AXSheets/포커스의 NoValue 또는
attributeUnsupported는 값 부재이고, 필수 AXWindows/AXRole/AXChildren의 오류는
clear로 바꾸지 않는다. 이 조회는 시점 관측이며 입력까지의 경쟁을 배제하지 않는다.

소유 ComboBox 앱의 종료 뒤 같은 signed helper에서 fresh popup을 열고 Beta를
고르는 실제 liveness 과제도 통과했다. 슬롯 해제는 proven process instance end
및 pinned popup의 confirmed onscreen 부재를 함께 요구한다. 앱을 종료하는
런타임 복구나 자동 작업 재개 기능은 아니다. 같은 프로세스의 소유 AppKit 뷰
교체 후 새 popup 선택도 실제 통과했다. live PID reuse와 다른 앱의 뷰 수명은
별도 실제 검증이 남아 있다.
