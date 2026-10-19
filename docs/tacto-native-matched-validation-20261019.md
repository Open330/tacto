# Tacto / Sky의 독립 세션 비교 — 2026-10-19

소유 AppKit 업무 fixture에서 금액·배수·보너스를 입력하고 Review→계산→
결과 확인→Finish를 수행했다. 각 arm은 새 Codex CLI 세션이며 요청 모델은
`gpt-6.1-sol`, reasoning은 medium, 제한은120초다. Tacto는 현재 checkout의
서명 launcher와 `skill/SKILL.md`, Sky는 설치된 Computer Use 스킬과
`@oai/sky`를 사용했다. 앱 빌드·초기화 시간은 제외하고 안내 읽기·작성·실행은
측정에 포함했다. 실제 serving 모델·과금·전체 숨은 context의 동등성은 미검증이다.

## 최초3쌍과 별도 후속1쌍

| cohort·과제 | Tacto 판정·초 | Sky 판정·초 | Tacto/Sky 총 입력 | Tacto/Sky 새 입력 | Tacto/Sky 출력 |
|---|---|---|---|---|---|
| 최초·bonus_on | capacity 거절3.681 | 완료25.998 | 미관측 / 137,686 | 미관측 / 9,942 | 미관측 / 500 |
| 최초·bonus_off | 완료21.496 | 완료26.349 | 66,642 / 142,977 | 10,066 / 19,329 | 710 / 637 |
| 최초·renamed_button | 완료18.542 | 완료29.459 | 66,429 / 137,695 | 7,677 / 22,239 | 505 / 658 |
| 후속·bonus_on | 완료21.528 | 완료61.515 | 72,765 / 135,934 | 14,909 / 30,334 | 637 / 512 |

완료는 독립 oracle의 done·정확한 값·새 commit1개·앱 비활성 상태를 뜻한다.
최초 Tacto는 서버의 `Selected model is at capacity` 거절로 과제 실행 전에
종료했다. 서버 요청 자체가 없었다는 뜻은 아니며, usage는 미관측이다.
이 행을 지우거나0토큰으로 채우지 않았다. 후속은 새 fixture·새 독립 세션의
별도 실행이며 최초 실패를 대체하지 않는다.

원본의 source/config hash와 검사한 초기 필드는 일치했다. 최초 실행 순서는
Tacto/Sky, Sky/Tacto, Tacto/Sky이며 후속은 Tacto/Sky다. 실패3.681초를
포함한 Tacto 평균과 모두 완료한 Sky 평균을 비교하지 않는다. 각 과제는
cohort당1표본이고 cache 적중·실행 시점도 다르다. 비용은 검증하지 않았으며
새 입력은 CLI의 `total_input_tokens - cache_read_tokens`다.

## 피어가 발견한 초기 상태 격차

기존 harness는 같은 fixture 프로세스를 reset하면서 commit 이력을 유지했다.
따라서 `initial_states_matched=true`는 amount/factor/bonus/phase/
control_version/active의 일치만 뜻한다. sequence·누적 commits는 달랐다.
피어 요청6b가 이 제한을 찾아냈다. Swift fixture의 publish 코드를 확인하면
commits·sequence는 oracle 파일에만 기록되고 화면 레이블에는 표시되지 않는다.
각 에이전트에게 oracle/control·이전 trace·fixture 소스 읽기는 금지했지만,
이 코드 확인만으로 전체 환경 동등성이 증명되지는 않는다.

**따라서 위 결과 모두 `comparison_eligible=false`를 유지한다.** 속도·토큰의
일반적 우위나 출시 성능 보장으로 해석하지 않는다. 같은 프로세스 이력을
공유하지 않도록 각 arm을 새 fixture 프로세스로 분리한 후속 수정·실측은 아래와 같다.

## 실행별 새 프로세스로 수정한 뒤의 실측

피어 요청6c가 `benchmark_matched_conditions.py`를 수정했다. 앱 빌드는
공유하되 각 arm의 fixture 프로세스와 데이터 디렉터리는 별개다. 다음 arm을
만들기 전에 앞 프로세스 종료를 확인하고, 초기10개 필드를 전부 비교한다.
commits는 빈 배열, sequence는1이어야 한다. 실패·예외 때의 정리와 살아 있는
이전 프로세스 거절을 포함한 루트 회귀26개가 통과했다
(`matched-isolation-regression.log`). 이전 원본은 그대로 보존했다.

수정 뒤 bonus_on 새1쌍은 양쪽 모두 완료했다:

| arm | 시간 | 총 입력 | cache read | 새 입력 | 출력 |
|---|---:|---:|---:|---:|---:|
| Tacto | 30.579초 | 69,728 | 58,112 | 11,616 | 798 |
| Sky | 25.492초 | 137,693 | 120,192 | 17,501 | 512 |

이 표본에서는 **Tacto의 총 입력은 적었지만 시간은 더 길었다.** 토큰 절감이
매번 시간 단축으로 이어진다고 주장하지 않는다. 실제 입력 비용·서버 cache·
모델 대기·CLI와 UI 시간의 세부 기여는 이 보고서만으로 분리할 수 없다.

두 프로세스 PID는 다르고 harness가 SIGTERM을 보내 종료 코드-15로 소유
fixture를 정리했다. fixture가 스스로 비정상 종료했다는 판정은 아니다.
초기 상태10개 필드는 모두 같으며 commits=[], sequence=1이다.
source/config 전후 일치와 requested_conditions_matched를 확인했다.
반복 횟수1, Tacto 먼저라는 순서, 실제 serving 모델·전체 context·사용자
typing/caret 동등성 미검증 때문에 `comparison_eligible=false`는 유지한다.
다른 과제와 반대 순서의 반복도 아직 없다.
피어 요청6d가 초기 상태·다른 PID·정리·269개 source manifest 일치·수치를
독립 확인했다. 총 입력과 새 입력은 적었지만 출력은 Tacto가 더 많았다.

이 source cohort의 원본은
`reviews/tacto-native-resumption-20261019/matched-fresh-fixture/report.json`이다.

원본과 개별 stdout·사용량·oracle·source manifest는 다음 로컬 디렉터리에
보존했다. 원본 보고서는 소급 수정하지 않았다.

- `reviews/tacto-native-resumption-20261019/matched-native-three-pairs/report.json`
- `reviews/tacto-native-resumption-20261019/matched-capacity-followup/report.json`

[native 기능 재검증](tacto-native-resumption-validation.md)과 이 비교는 별도
harness이며, 131옵션 선택의 모델 사용량을 이 표에 섞지 않는다.
