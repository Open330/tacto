# 모델 호출의 시간 예산

`general.run(timeout_seconds=...)`의 남은 시간을 내장 모델 어댑터에 전달합니다. 계획 질문과 실행 전·후 witness 질문이 같은 deadline을 공유합니다. 기존 사용자 planner callback의 인자 수는 바꾸지 않았습니다. context-local deadline은 callback이 끝나면 복원되며 중첩 실행으로 더 넓어지지 않습니다.

`ask_here_paged(..., timeout_seconds=...)`는 페이지마다 예산을 새로 시작하지 않습니다. 일반 실행 안에서는 일반 실행의 남은 예산도 함께 적용합니다. `reach_model(..., timeout_seconds=...)`와 `reach_model_result(...)`에서도 호출별 예산을 지정할 수 있습니다. 한 모델 요청의 상한은 420초입니다. provider fallback은 하나의 총 예산을 공유하고 1초 미만 예산도 로컬 대기에 반영합니다.

일반 실행 deadline을 넘으면 `deadline_exceeded`, 그 전에 모델 요청 자체가 timeout으로 끝나면 `model_timeout`으로 중단합니다. 늦은 판단 뒤에 UI 행동을 시작하지 않습니다. 모델의 malformed JSON과 timeout은 별도로 처리합니다.

signed 경로는 양수·유한값·420초 이하인 `timeout_seconds`를 검증하고 provider 프로세스에 전달합니다. 런처는 stdin 쓰기, stdout 읽기, 종료 대기를 제한합니다. 수신 측도 모델 IPC의 쓰기·읽기 시간을 제한하고, timeout 후 채널을 폐기합니다. 폐기된 채널에서 다른 UI 요청을 하거나 다시 객체를 만들어 늦은 답을 소비할 수 없습니다. 늦은 런처 응답의 쓰기는 SIGPIPE로 런처를 종료시키지 않습니다.

이 기능은 내장 어댑터의 로컬 요청 대기를 제한합니다. 임의의 동기 planner callback, 드라이버 AX 호출, 초기 helper handshake 자체를 선점 중단하는 기능은 아닙니다. 런처의 외부 watchdog은 모델 wrapper PID를 종료하지만 별도 세션의 모든 하위 프로세스와 원격 추론 작업 종료를 보장하지 않습니다. 내부 provider에도 같은 timeout을 전달하지만 원격 취소 확인은 여전히 별도 요구사항입니다. 다른 스레드로 모델 호출을 직접 옮기는 custom callback은 context 전달도 직접 책임져야 합니다.

검증: 관련15개 테스트는 예산 감소, 1초 미만 fallback, witness timeout 후 UI0회, 실제 가짜 지연 프로세스 종료, 막힌 socket write/read, 늦은 응답 뒤 채널 폐기, 실제 소스에서 추출한 Swift의 stdin/stdout/프로세스 종료 경계를 검사합니다. 실제 provider 호출이나 UI 동작은 사용하지 않았습니다. 전체 기본 unittest449개(381 PASS/68 SKIP), offline54/54 PASS. Swift 타입 검사·런처 재빌드·strict 서명검사·signed help 실행을 확인했습니다. 전체 웹 suite와 실제 provider의 signed timeout은 이번 변경 후 재실행하지 않았습니다.

후속 정상 경로 확인: Claude+signed launcher+helper의 실제 AppKit 선택 시험1회가25.2초에 성공했습니다. 이는 새 IPC 필드를 포함한 정상 호출의 통합 검사이며 실제 provider timeout/원격 취소 검증은 아닙니다. 원시 기록은 선택 검증 보고서(비공개 로컬 기록: `reviews/2026-09-19/native-select-preservation.md`; 공개 원본 미포함)에 있습니다.

2026-10-19 후속: 브라우저 `run_visual`도 planner를 같은 context-local
`model_deadline`으로 실행한다. 이전에는 native visual과 달리 전체 남은 시간보다
긴 기본 모델 제한을 사용할 수 있었다. 더 짧은 바깥 deadline은 유지하며,
planner 예외 뒤 이미 deadline·취소 조건이 성립하면 STOPPED 사유를 우선한다.
실제 run_visual→plan_visual→query_visual 경로에서 CLI만 대역으로 바꿔 남은6초·
바깥 제한1초 전달, 만료 뒤 클릭0·재시도0·context 복원을 검사했다. 기존 browser/
native visual을 포함한28개 회귀가12.109초에 통과했고 피어
`req_18dfa78f690ac088_5e`에서 승인받았다. 원격 추론 취소 보장은 여전히 아니다.
