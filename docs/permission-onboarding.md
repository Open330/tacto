# 권한 받기 UX — ChatGPT(Sky) 조사와 Tacto 적용

2026-10-16. Tacto가 앱을 조작하려면 macOS 권한 두 가지가 필요합니다:
손쉬운 사용(macOS 27: Device Control and Data Access)과 화면 기록. 이 권한은
시스템 설정에서 사람이 직접 켜야 하고, 앱이 대신 켤 방법은 없습니다. 그래서
UX의 목표는 **사람이 할 일을 한 번에 하나만 보여 주고, 끝난 것을 스스로
알아차리는 것**입니다.

## ChatGPT 컴퓨터 사용 앱은 어떻게 하는가

확인한 대상: `~/.codex/computer-use/Codex Computer Use.app`
(`com.openai.sky.CUAService`, 실행 파일 `SkyComputerUseService`). 공개 문서가
아니라 설치된 번들의 Info.plist와 바이너리에 남은 클래스명·UI 문구를 읽은
결과라, 동작 순서의 세부는 추정이 섞여 있습니다.

| 관찰 | 근거(번들 안의 이름·문구) |
|---|---|
| 필요한 권한을 한 창에 행으로 보여 줌 | `PermissionWindowController`, `PermissionRowRegistry`, `PermissionViewState`; "ChatGPT Computer Use needs these permissions to use apps on your Mac." |
| 행마다 이유를 사람의 말로 씀 | "Allows ChatGPT to access app interfaces", "ChatGPT uses screenshots to know where to click", "Allows ChatGPT to see the visuals in apps" |
| 시스템 설정 옆에 붙는 보조 창에서 앱 아이콘을 끌어다 놓게 함 | `SlimCore.SystemSettingsAccessoryWindowView`, `DraggableApplicationView`, `DragHintView`, `SystemSettingsAccessoryWindowDragDelegate` |
| 설정 화면으로 가는 권한과 시스템 대화상자로 묻는 권한을 구분 | `SystemSettingsPrivacyPermission`, `TCCDialogSystemPermission`, `AppleEventsPermission` |
| 권한 전후 상태를 비교해 완료를 감지 | `_nativePermissionBefore`, `_nativePermissionAfter`, `_isAccessibilityGranted`, `_isScreenRecordingGranted` |
| 앱별로 처음 쓸 때 묻고 "항상 허용"을 둠 | "Allow ChatGPT to use …", "Always allow sending to this chat", "Computer Use is not allowed to use the app '…'" |
| 권한 흐름을 분석 이벤트로 수집 | `CodexComputerUsePermissionWindowShown`, `…PermissionRequested`, `…PermissionGrantFinished` |

## Tacto에 적용한 것 (`setup/`)

| 이전 | 지금 |
|---|---|
| 고정된 안내 문단과 버튼 5개 | 권한마다 한 줄: 이름, 이유, 설정 위치, 상태(대기 / 기다리는 중 / 완료) |
| 매번 **다시 확인**을 눌러야 상태가 바뀜 | **설정 열기**를 누른 뒤에는 자동 감지(1.5초 간격, 최대 3분). 앱으로 돌아오면 한 번 더 확인 |
| 드래그 아이콘이 시작 앱 안에만 있음 | **설정 열기**가 설정을 열고, 시스템 설정 창 옆에 아이콘 도우미 창을 붙임. 권한이 켜지면 ✓를 띄우고 스스로 닫힘 |
| 열자마자 상태를 모름 | 열 때 helper의 권한 상태를 한 번 읽어 이미 켜진 것을 완료로 표시 |
| 화면 기록이 연결 단계 뒤에 따로 있음 | 두 권한이 순서대로 놓이고, 다음에 할 줄을 강조. 둘 다 켜지면 **에이전트에 연결**이 활성화 |

지킨 원칙:

- **요청 없이 바꾸지 않습니다.** 권한 요청, 설정 열기, 스킬 설치는 각자 클릭으로
  시작합니다. 자동으로 하는 것은 helper가 스스로 보고하는 상태를 **읽는 것**뿐입니다.
  이것도 LaunchServices로 helper를 잠깐 실행해 결과 파일만 받는 방식이라, 공유
  파이썬 인터프리터에 권한을 묻지 않습니다.
- **감지는 끝이 있습니다.** 잊힌 설정 창이 helper를 계속 다시 실행하지 않도록,
  감지는 사용자가 누른 권한 하나에 대해서만 3분 안에서 동작합니다.
- **분석 이벤트를 보내지 않습니다.** Sky의 권한 이벤트 수집은 따라 하지 않았습니다.
- **문구는 실제 쓰임과 같게.** 화면 기록은 "접근성 정보만으로 알 수 없는 화면을
  확인할 때 앱 창을 캡처"하는 데 쓰입니다. 권한은 서명된 'NLC Helper' 하나에만
  주며, 목록에는 그 이름으로 보인다고 안내합니다.

## 아직 남은 것

- **실제 화면 검증.** 도우미 창의 배치와 드래그는 오프스크린 렌더와 배치 계산
  테스트로만 확인했습니다. 새 Mac에서 처음부터 권한을 켜는 실제 흐름(macOS 27
  패널 이름 포함)은 사람이 해 봐야 합니다.
- **앱별 첫 사용 동의.** Sky는 앱마다 처음 쓸 때 묻습니다. Tacto는 지금 작업별
  승인(되돌리기 어려운 동작의 확인)만 있고, 앱 단위 허용 목록은 없습니다.
- **도우미 이름.** 목록에는 여전히 'NLC Helper'로 보입니다. 이름을 바꾸면
  TCC가 새 앱으로 보고 권한을 다시 받아야 할 수 있어, 별도로 결정해야 합니다.

## 초보자용 드래그 안내 보완

- 버튼은 ‘설정 열기’로 표시해, 클릭만으로 권한이 켜진다고 오해하지 않도록 했습니다.
- 보조 창은 1/2·2/2 단계를 표시하고, 설정에서 찾을 이름 ‘NLC Helper’가
  Tacto의 권한용 앱임을 설명합니다. 기존 항목이 있으면 스위치만 켜면 됩니다.
- ‘다른 방법 보기’를 눌렀을 때만 Finder에서 앱을 선택하는 버튼과 앱 위치 복사,
  + 버튼의 파일 선택 창에서 위치로 이동하는 설명을 펼칩니다. 클립보드 변경과
  Finder 열기는 해당 버튼을 클릭할 때만 발생합니다.
- 암호·Touch ID·앱 재시작 요청이 뜨면 어떻게 진행하는지 안내합니다.
- 권한 확인이 실패하거나 시간이 지나면 안내를 유지합니다. 오래된 완료 상태로
  연결 버튼이 활성화되지 않으며, 앱 파일이 없을 때는 재설치 방법을 표시합니다.
- 연결 완료 뒤 Claude Code/Codex의 새 대화에서 무엇을 요청할지 안내합니다.

기존 권한을 유지하기 위해 helper의 이름·bundle identifier·서명 요구사항은
변경하지 않았습니다. 새 Mac에서의 최초 권한 설정과 비숙련 사용자 테스트는
별도 확인이 필요합니다. 오프스크린 확인은 실제 시스템 설정의 드롭 수락을
검증하는 테스트가 아닙니다.

읽기 전용 설치 이미지 안에서 앱을 열면, 먼저 Applications로 옮기도록 안내하고
권한 확인·명령 연결을 시작하지 않습니다. 이미지를 꺼낸 뒤 연결이 끊기는 것을 막습니다.

실제 Tacto로 설정 열기, 도움말 펼침·접기, 위치 복사 피드백, Finder의 도우미 선택과
권한 미완료 시 연결 차단을 확인했습니다. 테스트 Mac의 macOS 27에서는 화면 기록
딥 링크가 Device Control and Data Access로 열려 안내 문구를 맞췄습니다.
복사된 helper의 화면 기록 권한은 미완료였으며 이를 우회하지 않았습니다.
