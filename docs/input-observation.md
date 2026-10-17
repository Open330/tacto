# 배경 입력의 포커스·캐럿 관측

전경 입력 필드를 준비하기 위한 전용 시험 앱도 빌드할 수 있습니다. 이 명령은
새 디렉터리에 앱을 컴파일하고 ad-hoc 코드 서명을 검증하며 실행하지 않습니다.

```bash
.venv/bin/python examples/probes/build-typing-witness.py /existing/parent/new-directory
```

`NLC Typing Witness.app`은 사용자 문서를 열지 않는 빈 NSTextView와 자체 window의
key down/up 횟수·UTF-16 선택 범위·본문 SHA256 상태를 제공합니다. 본문을 파일에
기록하지 않습니다. 실행 후 실제 AX caret 노출과 launch의 전경 변화는 별도로
측정해야 하며 빌드 성공이 입력 보존 증거는 아닙니다. 창은 닫으면 종료되며
60분 뒤 자동 종료합니다. 최초 빌드의10분 수명은 전체 검증을 진행하기에 짧아
후속 빌드에서 늘렸습니다. 기존 실측은 당시10분 빌드의 결과로 보존합니다.
원래 사용자 포커스를 복원하는 입력을 보내지 않습니다.
이미 존재하는 빌드 디렉터리는 수정하지 않습니다.

사용자가 창을 직접 선택하거나 전용 시험 앱의 전경 이동을 명시적으로 승인한
뒤 시험용 텍스트를 직접 입력해야 합니다. 동시 typing 중 caret 변화는 예상되는
사용자 변화이므로 단순 `UNCHANGED_SAMPLED`를 요구해 성공을 판정할 수 없습니다.
미리 정한 텍스트·자체 receiving counters·포커스 유지·배경 대상의 결과를 함께
확인해야 하며, 이 앱만으로 실제 사용자 typing을 자동 생성하거나 입증하지 않습니다.

이를 위한 별도 owned 동시 입력 시험을 준비할 수 있습니다. 준비 명령은 배경
navigation 앱을 컴파일·서명하며 실행하지 않습니다.

```bash
.venv/bin/python examples/probes/concurrent-typing.py prepare /existing/parent/new-trial
.venv/bin/python examples/probes/concurrent-typing.py run /existing/parent/new-trial \
  --witness-build-report /absolute/witness-directory/build-report.json \
  --output /absolute/new-concurrent-report.json
```

실행 전 사용자가 전용 witness의 빈 입력 필드를 선택해야 합니다. `ARMED` 안내
후 영문 입력 상태에서 `tacto typing 12345`를 약 1초에 한 글자씩 직접 입력합니다.
붙여넣기나 Enter는 사용하지 않습니다. 첫 입력을 최대 60초 기다린 뒤 배경 A 창에
Right 16회를 보내며, 각 단계에서 실제 위치와 A/B 수신 횟수를 확인합니다.
전경 본문을 읽거나 기록하지 않고 자체 상태 label의 hash·입력 횟수·선택 범위를
사용합니다. 전경 PID·포인터·포커스 변화나 불완전 관측은 원인을 단정하지 않고
중단하며 원래 포커스를 복원하지 않습니다. 불확실한 입력을 재시도하지 않습니다.
최종 텍스트 hash와 caret, 각 글자의 down/up 횟수, 배경 키 실행 중 전경 입력 횟수의
증가가 모두 있어야 통과합니다. 표본 사이의 변화와 물리 키보드 입력의 출처를
입증하는 시험은 아니며 최대 표본 간격도 보고서에 기록합니다. 실제 사람의
동시 입력이 없는 준비·컴파일 성공을 동시 입력 검증으로 해석할 수 없습니다.

Tacto의 배경 입력 검증은 목표 앱의 결과와 사용자 입력 상태를 따로 확인합니다. `UNCHANGED_SAMPLED`는 수집한 표본이 같다는 뜻입니다. 표본 사이의 변화나 관측하지 않은 속성까지 보장하지 않습니다.

기본 `InputAudit`와 `ProcessInputAudit`는 전경 PID, 전역 포인터 위치, 전경 앱의 포커스 요소 identity를 비교합니다. 같은 요소 안에서 캐럿이나 선택 영역만 움직이는 경우는 이 세 항목만으로 알 수 없습니다.

전경 PID는 매 표본에서 HIServices의 공개 `GetFrontProcess`와 `GetProcessPID`로 읽습니다. `NSWorkspace.frontmostApplication`의 캐시값을 대신 사용하지 않습니다. PID를 읽기 전후의 ProcessSerialNumber가 다르거나 API가 실패하면 해당 표본은 불완전합니다. AX 포커스 조회 후에도 전경 PID를 다시 확인하고, native 전경 PID를 읽지 못한 경우 AX 응답만으로 빈 값을 채우지 않습니다. 이 검사도 여러 API를 원자적으로 읽거나 표본 사이의 변화를 모두 검출하는 보장은 아닙니다.

Python driver와 signed helper의 창 준비 권한 판단도 이 native 전경 PID를 사용합니다. 알 수 없는 상태는 전경으로 인정하지 않습니다. API가 deprecated되어 향후 macOS에서 제공되지 않으면 명시적으로 실패하며 캐시 기반 판별로 되돌아가지 않습니다.

`require_selection=True`는 전경 포커스 요소의 `AXSelectedTextRange`도 필수 관측에 포함합니다. 위치와 길이는 UTF-16 단위이며 선택된 텍스트 자체는 수집하지 않습니다. 캐럿은 길이 0인 범위입니다. AX가 속성을 지원하지 않거나 읽기 오류·잘못된 범위·읽는 동안 포커스 변경이 있으면 보존 성공으로 처리하지 않습니다. ProcessInputAudit의 초기 필수 관측이 없으면 본문 실행 전에 중단합니다.

```python
from tacto.input_audit_process import ProcessInputAudit

# spawn 방식이므로 실행 스크립트의 __main__ 가드 안에서 사용합니다.
with ProcessInputAudit(require_selection=True) as audit:
    perform_owned_fixture_action()
report = audit.report()
```

기존 Calculator 시험도 다음 옵션으로 캐럿 관측을 요구할 수 있습니다. 이미 실행 중인 시험용 Calculator가 배경의 Basic/0 상태여야 합니다. 이 명령은 Calculator에서 7 입력·지우기·Scientific 전환·Basic 전환을 수행합니다. 전경 앱은 실행 전에 사용자가 마련한 텍스트 입력 필드에서 선택 영역을 노출해야 합니다. 명령이 전경 앱을 열거나 포커스를 배치하지 않습니다.

```bash
.venv/bin/python examples/probe_background_input.py \
  --require-selection --output /absolute/new-report.json
```

이 옵션을 추가하기 전의 실앱 보고서는 캐럿 보존 검증으로 소급 해석할 수 없습니다. 현재 추가 검사는 모의 AX 응답과 별도 프로세스 회귀로 검증하며, 실제 전경 텍스트 필드와 동시 입력하는 검증은 별도로 남아 있습니다. 사용자 입력으로 캐럿이 움직인 경우도 `CHANGED`이며 원인이 Tacto라고 자동으로 단정하지 않습니다. 포인터 모양, IME 조합 상태, 여러 선택 범위 및 관측 사이의 일시 변화는 이 범위에 포함되지 않습니다.

네이티브 owned 키/시각 입력 시험에도 `--require-selection`을 사용할 수 있습니다.
한 번에 한 시나리오만 선택하며, 다음 명령은 전경 요소를 바꾸거나 앱을 활성화하지
않습니다. 전경 텍스트 필드는 사용자가 먼저 마련해야 합니다.

```bash
.venv/bin/python examples/eval.py native_navigation --require-selection
./launcher/tacto eval visual_input_codex --require-selection
./launcher/tacto eval visual_input_claude --require-selection
```

지원 시나리오는 `native_navigation`, `signed_window_keys`,
`navigation_model_claude/codex`, `python_opaque_input`, `signed_opaque_input`,
`visual_input_fixed/codex/claude`입니다. probe의 직접 Python 진입점도
`require_selection=True`를 받습니다. 생략하면 기존 전경·포인터·포커스 감사이며
caret 검증으로 해석할 수 없습니다. 선택 범위 readiness가 불완전하면 새 owned
fixture 프로세스·입력·모델 호출 전에 중단합니다. 자체 fixture 컴파일은 수행될
수 있습니다. 요구 옵션은 eval 보고서와 probe 보고서 모두에 기록합니다.

2026-10-04 실제 `native_navigation --require-selection` 실행은 전경 요소의
AX 오류 -25205로 준비 단계에서 중단됐습니다. fixture PID/입력/모델 결과는 없고
감사는 `INCONCLUSIVE`입니다. eval의 FAIL은 이 필수 환경 조건 미충족이며
키 전달 실패나 caret 보존 성공의 증거가 아닙니다. 과거 성공 보고서는 수정하지
않으며, 실제 사용자 동시 typing 검증도 여전히 남아 있습니다.

이번 검증: 모의 AX·프로세스 회귀 34개와 probe 연결 회귀 7개 통과. macOS의 실제 AXValue CFRange 생성·해석 3조건을 확인했습니다. 현재 전경 요소의 읽기 전용 관측에서는 AX 오류 -25205(속성 미지원)가 발생해 `INCONCLUSIVE`로 기록됐습니다. 이 관측 중 UI 행동은 수행하지 않았습니다. 전체 기본 unittest는 424개 중 356 PASS / 68 SKIP / 0 FAIL입니다. 웹 전체 suite는 이번 변경 후 재실행하지 않았습니다.

2026-10-05 사용자가 전용 시험 앱의 전경 이동과 입력 초점 준비를 승인했습니다.
빌드 artifact의 source/binary hash를 확인한 뒤 앱을 실행하고, 실제 전경 PID와
numeric AX caret을 확인했습니다. 이 조건에서 Python owned navigation 시험은
독립 receiving counts와 required-selection 감사에 통과했습니다: 3,804개 표본,
caret·포인터·전경·포커스의 관측 변화 없음. 실제 동시 사용자 typing은 포함하지
않으며 표본 사이의 변화도 배제하지 않습니다.

이어진 opaque 입력 시험은 click_point 전에 window validation 오류로 중단했고
시도된 point events는0입니다. 감사 마지막 두 표본의 전경/포커스 변경과 caret 읽기
실패도 관측해 INCONCLUSIVE로 유지했습니다. 원인을 사용자나 Tacto로 단정하지
않습니다. 후속 signed navigation은 caret readiness에서 실패해 fixture/입력이
없었습니다. 두 FAIL을 성공으로 바꾸지 않았고 전용 앱으로 포커스를 복원하지
않았습니다. 현재 창 순서 변경과 객체 변경 진단은 분리했지만 모두 입력을 거절하며,
이전 generic 오류에 새 분류를 소급하지 않습니다. 원시 결과는
`reviews/2026-10-05/verification`과 signed trial의10월1일 경로에 있습니다.
