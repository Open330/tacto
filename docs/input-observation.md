# 배경 입력의 포커스·캐럿 관측

NLC의 배경 입력 검증은 목표 앱의 결과와 사용자 입력 상태를 따로 확인합니다. `UNCHANGED_SAMPLED`는 수집한 표본이 같다는 뜻입니다. 표본 사이의 변화나 관측하지 않은 속성까지 보장하지 않습니다.

기본 `InputAudit`와 `ProcessInputAudit`는 전경 PID, 전역 포인터 위치, 전경 앱의 포커스 요소 identity를 비교합니다. 같은 요소 안에서 캐럿이나 선택 영역만 움직이는 경우는 이 세 항목만으로 알 수 없습니다.

`require_selection=True`는 전경 포커스 요소의 `AXSelectedTextRange`도 필수 관측에 포함합니다. 위치와 길이는 UTF-16 단위이며 선택된 텍스트 자체는 수집하지 않습니다. 캐럿은 길이 0인 범위입니다. AX가 속성을 지원하지 않거나 읽기 오류·잘못된 범위·읽는 동안 포커스 변경이 있으면 보존 성공으로 처리하지 않습니다. ProcessInputAudit의 초기 필수 관측이 없으면 본문 실행 전에 중단합니다.

```python
from nlc.input_audit_process import ProcessInputAudit

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

이 옵션을 추가하기 전의 실앱 보고서는 캐럿 보존 검증으로 소급 해석할 수 없습니다. 현재 추가 검사는 모의 AX 응답과 별도 프로세스 회귀로 검증하며, 실제 전경 텍스트 필드와 동시 입력하는 검증은 별도로 남아 있습니다. 사용자 입력으로 캐럿이 움직인 경우도 `CHANGED`이며 원인이 NLC라고 자동으로 단정하지 않습니다. 포인터 모양, IME 조합 상태, 여러 선택 범위 및 관측 사이의 일시 변화는 이 범위에 포함되지 않습니다.

이번 검증: 모의 AX·프로세스 회귀 34개와 probe 연결 회귀 7개 통과. macOS의 실제 AXValue CFRange 생성·해석 3조건을 확인했습니다. 현재 전경 요소의 읽기 전용 관측에서는 AX 오류 -25205(속성 미지원)가 발생해 `INCONCLUSIVE`로 기록됐습니다. 이 관측 중 UI 행동은 수행하지 않았습니다. 전체 기본 unittest는 424개 중 356 PASS / 68 SKIP / 0 FAIL입니다. 웹 전체 suite는 이번 변경 후 재실행하지 않았습니다.
