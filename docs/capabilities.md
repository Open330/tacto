# 지원 경로와 검증 범위

2026-09-20 개발 snapshot. 기능을 제공하는 것, 실제 결과를 확인한 것,
범용 앱에서 안정적으로 동작하는 것은 서로 다른 기준이다.

| 기능 | macOS 접근성 / signed helper | 별도 Chromium 페이지 | 시각 API |
| --- | --- | --- | --- |
| 관측 | AX tree·probe·selection | CDP AX tree·DOM identity·probe | 브라우저 CSS viewport PNG; native 창 캡처 모듈은 실측 대기 |
| 전체 문서 본문 | TextEdit 완전 본문·문서 token·경로, direct/signed 실측; 형식·저장 패널은 미검증 | 별도 API 없음 | 이미지로 전체 본문을 증명하지 않음 |
| 클릭·입력 | 접근성 요소 | 관측한 DOM 요소 | host 승인된 좌표 클릭만 |
| 스크롤 | helper capability 확인 | 대상의 scroll container, 행동 후 재관찰 | 별도 좌표 scroll API 없음 |
| 키보드 | Tab·shift+Tab·방향키, Escape는 사용자 중단 | 같은 탐색 키와 Escape | 별도 키보드 API 없음 |
| HTML select | 해당 없음 | value·label 고정, 변경·중복·disabled 거절 | 해당 없음 |
| custom ARIA 선택 | 해당 없음 | 명시적 combobox/listbox 연결, 열기와 선택 분리 | 해당 없음 |
| native 선택 | PopUpButton/ComboBox label 선택 구현; 조건 미실행 검사 발견으로 실앱 재검증 중 | 해당 없음 | 해당 없음 |
| flow 재생 | 동일 backend·대상 binding | 동일 origin·대상 binding | 좌표 click을 자동 flow로 학습하지 않음 |
| 저장·전송 완료 | 앱별 host receipt 연결 필요 | 호스트가 제공한 event-bound receipt 필요 | 이미지나 모델의 done은 완료 증거가 아님 |

브라우저 드라이버는 기존 Chrome/Safari 사용자 세션에 자동 연결하지 않는다.
`nlc do`는 현재 macOS 경로이며, 브라우저와 시각 API는 Python에서 명시적으로
사용한다. wheel 설치만으로 서명된 macOS helper가 설치되지는 않는다.

저장·전송의 host receipt 검사는 일반 실행과 flow replay에 연결되어 있다. 기존 저장
flow가 창 제목만 보고 완료하던 경로도 수정했다. host receipt를 제공하지 않는 앱은
저장 액션을 실행했더라도 완료가 검증되지 않았다고 반환한다. 앱별 어댑터 연결은
별도 작업이며, 현재 모든 앱의 저장 결과를 검증할 수 있다는 뜻은 아니다.

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
않는다. Python의 전역 HID 좌표 클릭과 포인터 복원 경로도 제거했다. AX 클릭을
지원하지 않는 요소는 현재 거절하며, 검증된 백그라운드 좌표 클릭으로 대체하는
작업은 남아 있다. type_text는 지정 요소가 해당 앱의 실제 입력 대상인지 확인하고,
입력 중 대상이 바뀌면 나머지 문자를 보내지 않는다.

이 변경만으로 모든 앱에서 포커스가 유지된다고 보장하지 않는다. 앱이 AX 요청에
반응해 스스로 활성화되는 경우와 메뉴·팝업·저장의 앱별 차이를 실제로 측정해야 한다.
계산기의 숫자 입력·지우기·Scientific/Basic 모드 전환은 Python과 signed helper
양쪽에서 실제 결과와 단계당 175~196개의 포인터/포커스 표본을 확인했다. TextEdit
로컬 문서의 Python 한글·이모지 입력도 정확한 본문과 61개 무변화 표본을 확인했다.
signed helper의 텍스트 입력·스크롤·팝업·저장과 사용자 동시 입력은 후속 검증이
남아 있다. 표본 사이의 짧은 변화까지 배제하는 증거는 아니다.
[G0 원시 결과와 한계](../reviews/2026-09-19/background-input.md)를 참조한다.

## 모델과 예산

텍스트 계획 provider는 명시한 provider를 유지한다. 실제 모델 검증은 로컬
Chromium 과제를 Codex·Claude 백본으로 수행했다. 다른 provider가 설정 목록에
있다는 사실만으로 같은 과제를 통과했다고 말하지 않는다. 이미지 adapter는 현재
Codex·Claude CLI를 지원하며 두 백본의 실제 canvas 클릭을 확인했다.

후보 paging은 모든 후보의 원래 번호를 유지하며, 기본 prompt 예산은 UTF-8 byte를
보수적으로 센다. 정확한 token 계수가 필요하면 provider tokenizer를 전달한다. 이 예산은 NLC 요청 본문에 적용되며 provider CLI의 숨은 시스템 지침·도구 정의까지 포함한 전체 context 상한은 아니다.
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
- [전체 goal](../reviews/2026-09-19/next-goal.md)
- [최신 단계별 결과](../reviews/2026-09-19/goal-progress-4.md)

파일 receipt는 파일 쓰기만, 전송 receipt는 전송만 증명한다. 다른 중요한 효과가 함께 선언되면 같은 receipt로 완료 처리하지 않는다. 실제 저장 항목 삭제용 receipt는 아직 지원하지 않는다.
