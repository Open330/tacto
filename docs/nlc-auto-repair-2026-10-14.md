# Flow의 자동 에이전트 개입

> 개발 checkout의 검증 기록입니다. 기본 브랜치의 배포 기능을 의미하지 않으며, 원본 실행 기록은 공개 저장소에 포함하지 않습니다.

`tacto batch template.json --helper --auto-repair codex`와
`tacto run flow.json --helper --auto-repair codex`에 자동 개입을 연결했다.
성공 시 모델을 호출하지 않는다. 실행 전 선택자 실패에만 짧은 패킷을
전달하고, 에이전트가 관찰 후보를 선택하면 호스트가 검증 후 남은 단계만
자동 실행한다. batch의 후속 작업은 수리한 선택자를 메모리에서 재사용한다.
원본 Flow 파일은 변경하지 않는다.

```sh
tacto batch /absolute/template.json --helper --auto-repair codex \
  --max-repairs 2 --repair-timeout 60 < /absolute/jobs.jsonl
```

에이전트의 답은 후보 번호 또는 보류뿐이다. action·params·계약·결과 검증을
변경하거나 직접 UI를 조작할 수 없다. 기존 서명 런처의 모델 요청 경로를
사용하며, 모델 CLI를 helper 내부에서 직접 실행하지 않는다.

바로 이전 단계가 명시한 결과 조건을 status ok로 성공 검증한 기록이 있어야 한다.
건너뛴 단계의 skip_if 검증은 이 재개 checkpoint로 사용하지 않는다.
실패 시점, 호출 직전, 호출 이후 제어·값·상태·보존된 계층이 같고 checkpoint도 유지돼야
재개한다. 수리 단계의 명시적 결과 조건은 수리 전 false여야 하고 재생 후 통과해야 한다.
자동 identity 재결합은 read/write_ephemeral 계약에 한정한다. 전체 fresh tree에서
후보의 유일성과 실제 선택한 node와의 일치를 확인하며 구분 불가능한 후보는 거절한다. 기존 nth 선택은
수리 후보의 유일성 검사를 우회하지 못한다. 기존 scope와 작업·매개변수
binding은 보존하고 결과는 일반 replay 엔진이 다시 검증한다.

이전 capture, dynamic steps, on_uncertain 처리, 매개변수 selector, 첫 단계 실패, checkpoint 부재, 관찰 실패,
대상 활성화, 잘못된 답, 보류, 후보 중복 또는 화면 변경은 중단한다.
시간 초과나 완료 여부 불확실한 동작은 자동으로 다시 보내지 않는다.
문서·journal·trace·대화형 승인은 이 단축 경로에 혼합하지 않는다.

패킷은 최대8KiB, 후보는 최대6개, 모델 호출은 기본 batch 전체2회,
호출 제한 시간은 기본60초다. `--max-repairs`는1..10,
`--repair-timeout`은1..120초를 허용한다. 모델 답을 받지 못한 호출도
횟수 제한에 포함한다. Provider marker는 보고된 사용량을 기록하고,
미확인 수치와 실제 모델 이름은 null로 남긴다.

## 실제 검증

최종 원본（개발 checkout의 로컬 기록: `../reviews/auto-repair-20261014-final/report.json`）.
소유 AppKit fixture의 10개 작업 중 7번째 버튼의 이름·식별자를 변경했다.
이번에는 수리 선택자를 host가 미리 주지 않고 실제 Codex가 선택했다.
수리·checkpoint 검증·남은2단계 실행·이후3개 작업이 자동 진행됐다.

10/10 통과, 에이전트 개입1회, 나머지 작업의 모델 호출0회,
각 작업의 최종 처리1회, oracle 시점에 대상 창 비활성 상태를 확인했다.
별도 검증 실패 테스트에서는 실제 완료1개를 관찰했지만 완료 응답을
확정하지 못했으므로 다음 대기 작업을 실행하지 않았고 모델 재시도도 없었다.
관련229개 검사와 스킬 구조 검증이 통과했다.

최종 배치 실행 비용은 시작·요청/결과·종료 합계12.68초였다. fixture 준비와
idle은 제외하며 최초 Flow 작성은 포함하지 않는다. 수리 모델 요청은4.77초,
전체입력6,405토큰(캐시0), 출력9토큰으로 보고됐다. 실제 모델 이름·청구비용은
확인되지 않았다. Sky나 이전 작성 포함 비용과 직접 비교하는 수치는 아니다.

첫 시도는 helper 내부 PATH에서 Codex를 찾지 못해 안전하게 중단됐으며
실패 기록（개발 checkout의 로컬 기록: `../reviews/auto-repair-20261014/progress.json`）을 보존했다.
그 뒤 기존 launcher 요청 경로로 수정했다. 최종 측정 중 source manifest는
동일했다. 단일 fixture의 선택자 실패 복구이며 모든 앱·오류를 복구하거나
crash/journal 재개를 보장하는 결과는 아니다. Sky 비교는 별도다.

## Peer 검토 후 보강

독립 검토·수정 합의（개발 checkout의 로컬 기록: `nlc-peer-review-2026-10-14.md`）에서 패킷 저장 수명,
결과 검증 없는 오학습, 원본 반복 단계 검사, 활성화 관찰, 선택 후보 node 일치,
consequential binding 이전을 보강했다. 성공한 자동 수리의 중간 패킷은 저장하지
않고 terminal 실패만 저장한다. suffix 실패의 index는 원본 template 기준이다.
위 10/10 원본은 이 보강 이전 실행 증거이며 최신 보강 검증은 검토 문서에서
별도로 확인한다.

행·조상 클릭(click_ancestor)과 value-only 라벨은 이 자동 후보 수리에서
지원하지 않는다. 시계·커서 등 AX tree가 변하는 UI도 정확한 tree 일치
조건 때문에 중단될 수 있다. 지원 범위를 넓힌다는 의미로 해석하지 않는다.

후속 [복잡한 시나리오 검증](nlc-complex-scenarios-2026-10-14.md)에서는
속성 없는 빈 Group과 volatile index의 변화만 좁게 구분해 연속 수리를 보강했다.
현재 checkpoint 비교 규칙과 추가 peer 승인은 해당 문서에 기록했다.
