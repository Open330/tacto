# 복잡한 11단계 작업과 실패 시나리오 검증

> 개발 checkout의 검증 기록입니다. 기본 브랜치의 배포 기능을 의미하지 않으며, 원본 실행 기록은 공개 저장소에 포함하지 않습니다.

소유 AppKit fixture에서 입력5개, 조건부 checkbox, 계산·audit·완료·결과
capture를 포함한11단계 Flow를 검증했다. 정상 작업13/13이 완료됐고,
중단이 필요한5/5는 추가 요청 없이 멈췄다. 시나리오 판정은9/9 통과했다.
최종 원본（개발 checkout의 로컬 기록: `../reviews/complex-scenarios-20261014-normalized/report.json`）.

| 시나리오 | 기대한 결과 | 실측 |
|---|---|---|
|11단계 Flow10회, 서로 다른 값·보너스·할인·한글 참조|모델 없이 각 작업1회 완료|10/10, 모델0회|
|한 작업의 Compute·Audit 버튼이 모두 변경|각 실패에서 수리하고 남은 단계만 실행|완료, 실제 모델2회|
|Review·Compute의 지연 응답|결과가 나타난 뒤 다음 단계|완료, 모델0회|
|preview capture를 후속 audit input에 사용|실제 읽은 값으로 입력·검증|완료, 모델0회|
|버튼3개 변경, 수리 예산2회|세 번째 실패에서 추가 호출·완료 금지|audited에서 중단, 완료0개|
|같은 이름·ID의 Audit 후보2개|추측 실행 금지|audit 입력0회, 완료0개|
|실제 완료 뒤 done 확인 실패|완료 동작 재전송·대기 작업 금지|완료 기록1개, 재시도0회|
|완료 뒤 필수 capture 실패|전체 작업 재실행 금지|완료 기록1개, 재시도0회|
|이전 capture가 있는 작업의 selector 변경|데이터를 잃는 자동 slice 금지|review에서 중단, 모델0회|

시간·토큰은 host가 작성한 template 비용을 포함하지 않는다. 모델 사용량은
실제 수리 호출만이다. 기대한 중단은 업무 완료 성공으로 집계하지 않는다.
각 action handler의 진입을 독립 oracle에 기록해 Review·Compute·Audit·Finish의
중복 호출도 검사했다. 정상 완료는 필드·수식 결과·참조·단계·처리 기록까지
일치해야 통과한다. oracle/control은 자동화 인터페이스나 모델 입력으로 사용하지 않았다.
최종 측정 중 source manifest는 동일했고, 종료 oracle에서 앱은 비활성이었다.
관련259개 검사와 스킬 구조 검증도 통과했다.

## 발견한 문제와 개선

초기에는 한 작업의 두 번째 수리가 checkpoint_changed_during_repair로
중단됐다. 실제 값과 checkpoint는 유지됐지만 AX의 익명 Group 줄이 사라져
chrome의 index가 바뀌었다. 초기 실패（개발 checkout의 로컬 기록: `../reviews/complex-scenarios-20261014/failed-run.json`）,
진단（개발 checkout의 로컬 기록: `../reviews/complex-scenarios-20261014-diagnostic/progress.json`）,
보강 전 전체 검사（개발 checkout의 로컬 기록: `../reviews/complex-scenarios-20261014-full/report.json`）를 보존했다.

비교에서 정확히 속성·flag 없는 Group만 구분했다. 숫자 AX index는 버리고,
나머지 body·값·flags·순서·보존된 조상 경로·잘림/비node행은 유지한다.
Group의 위치/경로 Counter 차이는 비교마다2개까지다. 속성 있는 Group,
숨김/선택/포커스 표식, 실제 제어·값·계층 변경, modal은 여전히 거절된다.
선택한 node의 canonical ordinal을 fresh raw index로 대응해 raw selector가
그 요소에 유일하게 일치하는지 확인한다. 결과 조건과 native readiness는
raw tree를 그대로 검사하며 승인·effects·capture·호출 한도는 완화하지 않았다.

Muxa peer 설계 검토（개발 checkout의 로컬 기록: `../reviews/complex-scenarios-20261014-normalized/peer-design.json`）와
구현 재검토 승인（개발 checkout의 로컬 기록: `../reviews/complex-scenarios-20261014-normalized/peer-approval.json`）을
받았다. 테스트에는 hidden marker, named ancestor 이동, Group 변경 상한,
node index 이동과 후보 일치, 값·modal 변경 거절을 포함했다.

한 controlled native 앱의 결과이며 실제 브라우저·문서 저장·외부 전송·계정
과제 전체의 복잡도나 성공률로 일반화하지 않는다. 이전 capture를 가진 자동
복구는 이번에도 명시적으로 중단했고, 해당 상태의 복원·일반 crash 재개는 별도다.

```sh
.venv/bin/python examples/benchmark_complex_scenarios.py \
  --output reviews/<new-private-directory>
```
