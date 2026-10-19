# 잠금 해제 후 native 재검증 — 2026-10-19

현재 checkout의 서명된 `./launcher/tacto`로 실행했다. 설치된 Preview2나
Sky의 결과로 옮겨 해석하지 않는다. 입력 감사는 sampled 관측이며 사용자
typing/caret의 완전한 보존을 증명하지 않는다.

| 실제 실행 | 시간 | 결과와 범위 |
|---|---:|---|
| Codex · AppKit ComboBox 131항목 | 53.855초 | Choice127, 선택 횟수1, 닫힌 메뉴와 index130 확인; 모델 요청7회 |
| Claude · 같은 시나리오의 새 fixture | 48.147초 | 같은 최종 값·선택 횟수·index 확인; 요청7회 |
| Finder preflight·prepare | 3.057초 | 읽기87 nodes 완료, 배경 조건4회 통과, 최종 task 조건 통과 |
| TextEdit scoped Save·닫기 | 6.945초 | 저장 전 디스크는 목표 본문과 다름→후 정확 일치, 창 닫힘과 테스트 파일 정리 확인 |

위4회는 소스 부분집합 전후 일치, 입력 감사 `UNCHANGED_SAMPLED`다.
Finder의 prepare는 `dispatched_unverified`, event 상태에 `unknown`이 남는다.
폴더 정리는 창 닫힘 증거가 없어 보류됐다. 따라서 창 준비의 인과적 효과,
New Folder, 정리까지 완료했다고 주장하지 않는다. 과거 읽기 실패가 이번에는
재현되지 않았으며 readiness 진단이 그 오류를 실앱에서 포착했다는 증거도 없다.
TextEdit도 producer attribution은 false이며 저장 후 앱 본문 재확인을 증명하지 않는다.

## 관측한 모델 사용량

| provider | 총 입력 | 구성 | 출력 | 비용 |
|---|---:|---|---:|---|
| Codex | 53,747 | 총 입력에 cache read24,192 포함 | 356 | 미관측 |
| Claude | 24,609 | uncached14 + cache creation24,595 + cache read0 | 1,048 | USD0.217776, CLI list estimate |

총 입력은 `provider_calls`의 `total_input_tokens`로 계산했다. Claude의
`input_tokens`14만 입력 합계로 쓰면 잘못이다. Codex 집계의 unknown7개는
각 요청의 로컬 Muxa 경로 거절이며, 이어진 CLI 원격 응답7개의 사용량은
모두 관측됐다. 미관측 비용을0으로 채우지 않는다. tokenizer·cache 정의,
실행 순서가 달라 두 provider의 토큰 수를 공정한 성능 순위로 보지 않는다.

원본은 `reviews/2026-10-19/`의 각각 다음 suffix다:

- `native-select-model-inherited-helper-1792366175401450000.json`
- `native-select-model-inherited-helper-1792366442731759000.json`
- `signed-finder-preparation-1792366664602494000.json`
- `signed-textedit-saved-state-1792366742298419000.json`

원본은 수정하지 않고 SHA256을 묶은 파생 집계를
`reviews/tacto-native-resumption-20261019/native-resumption-summary.json`에 저장했다.
피어 요청65가 원본 수치·범위를 독립 검토했고, 요청66은 파생 해시와 아래
집계 수정을 승인했다. 이 자료에는 비공개 로컬 실행 정보가 있을 수 있으므로
공개 배포 자료로 자동 포함하지 않는다.

## 발견한 결함과 수정

native model probe가 호출별 원본만 저장하고 요약 `usage`를 저장하지 않았다.
기존 `summarize_provider_calls`를 finally에 연결해 실패·최종 읽기 오류에도
관측된 입력·출력·비용과 미상 개수를 보존한다. 실패 경로 회귀를 보강했고
관련10개 테스트가 통과했다(`usage-regression.log`). 원본 보고서의 성공
판정을 소급 변경하지 않았다. 전체 eval의 `model_usage=null`은 전체 호출을
계량하지 않는다는 표시이므로 부분합이나0으로 대체하지 않았다.

집계 수정 뒤 새 fixture로 다시 실행했다. runtime은 그대로지만 probe 파일이
달라 source cohort는 구분한다. 두 실행 모두 최종 Choice127·선택 횟수1,
입력 감사 `UNCHANGED_SAMPLED`, 소스 부분집합 일치, `usage` 저장을 확인했다.

| 반복 실행 | 시간 | 요청 | 총 입력 | 출력 | 비용 |
|---|---:|---:|---:|---:|---|
| Codex | 47.602초 | 6 | 45,837 | 306 | 미관측 |
| Claude | 46.835초 | 8 | 28,488 | 1,108 | USD0.189277, CLI list estimate |

각 provider의 native131옵션 작업은 두 source cohort에서 각각1/1 성공했다.
일반 앱·새로운 목표의 성공률이나 안정적인 속도 차이를 추정할 표본은 아니다.
원본 suffix는 각각 `1792367329324233000`, `1792367476839874000`이며
같은 파생 집계에 원본 SHA256과 관측 비용·cache 구성을 추가했다.

## 거절·중단·참조 수명 검사

아래는 각각 새 소유 AppKit fixture에서 한 번씩 실행한 검사다. 모델의 새
업무 성공률과 합산하지 않는다. 스크롤 검사는 host가 고정 planner를 주입했고
실제 provider 호출은0이다. 예상한 실패/거절을 확인했을 때 검사 PASS가 된다.

| 검사 | 시간 | 판정 |
|---|---:|---|
| scroll receiving failure | 6.083초 | 수신 검증 실패 뒤 후속 선택 중단 |
| duplicate-label refusal | 12.304초 | 중복 라벨의 선택 거절 |
| process exit | 9.918초 | 종료한 앱의 오래된 참조와 후속 프로세스 구분 |
| same-process view replacement | 8.805초 | 같은 프로세스에서 교체한 view의 오래된 참조 구분 |

각 원본 경로는 `reviews/tacto-native-resumption-20261019/`의 해당 command
로그에서 확인할 수 있다. 여러 isolated 시나리오를 한 launcher에 넘긴 시도는
인자 검증에서 거절됐고(`native-lifecycle-command.log`), 위 실행은 별도
launcher 프로세스로 다시 수행했다. 이 인자 오류를 제품 입력 실패로 집계하지 않는다.

## 두 프로세스의 journal 재전송 거절

최초 실행은 전역 `~/.local/bin/tacto`가 checkout을 가리켜야 한다는 오래된
harness 조건으로 입력 전에 실패했다. 원본은
`reviews/2026-10-19/select-journal-restart-1792368372890476000/report.json`이다.
전역 설치를 바꾸지 않고, worker가 측정한 checkout의 launcher를 명시적으로
사용하고 그 path·SHA256을 기록하도록 고쳤다. 관련4개 테스트를 통과했다.

수정 후 원본 `select-journal-restart-1792369898611668000/report.json`은
12.741초에 검사 PASS였다. 첫 worker가 정상 종료한 뒤 다른 PID의 worker는
`dispatch_journal_requires_reconciliation`으로 거절했다. journal4개 slot의
해시와 수신 선택 횟수2가 재시작 전후 같고, 두 worker 모두 sampled 입력
감사 unchanged, 모델 호출0이다. **재전송 방지의 실증**이며 crash 중 복구,
reconciliation 수행이나 자동 재개 완료를 검증한 것은 아니다. 피어 요청6a가
launcher 해시·판정 로직·실제 보고서의 일치를 독립 검토했다.

Claude의 첫 실행에서는 cache read0이었지만, 반복 실행에서는 read7,785와
creation20,687, uncached16이 관측됐다(총28,488). 따라서 독립 CLI 실행이
cache 재사용을 항상 막는다는 설명은 맞지 않는다. cache 적중 차이의 원인은
확정하지 않았다.
현재 adapter는 요청마다 새 session ID와 임시 작업 디렉터리를 사용한다.
독립 실행을 없애거나 세션을 재사용하면 격리 조건이 바뀌므로 이 결과만으로
변경하지 않는다. 원격 cache 공유 여부와 CLI 내부 prefix는 별도 검증이 필요하다.

과거131항목 실행의22요청·시간 초과 실패와 이번 성공은 서로 다른 코드와
환경의 표본이다. 개선과 일치하는 결과지만 단일 실측으로 인과적 속도 개선을
확정하지 않는다. 새 Sky 동일 조건 비교, 일반 앱의 복잡한 장기작업,
Finder 정확한 위치·효과 증거와 설치본 반영은 남아 있다.
