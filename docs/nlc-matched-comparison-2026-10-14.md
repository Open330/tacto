# 같은 요청 조건에서 NLC·Sky 비교

> 개발 checkout의 검증 기록입니다. 기본 브랜치의 배포 기능을 의미하지 않으며, 원본 실행 기록은 공개 저장소에 포함하지 않습니다.

최신 구현으로 새 대화에서3쌍을 실행했다. 양쪽 모두3/3 성공했다.
NLC의 전체입력은53.5%, 캐시읽기 제외 입력은56.3% 적었다.
시간 합계는8.8% 짧았지만 한 사례에서는 Sky가 빨랐다. 출력은 거의 같았다.
원본（개발 checkout의 로컬 기록: `../reviews/matched-conditions-20261014/report.json`）,
집계（개발 checkout의 로컬 기록: `../reviews/matched-conditions-20261014/summary.json`）.

| 3회 합계 | NLC | Sky |
|---|---:|---:|
|성공|3/3|3/3|
|총 시간|126.30초|138.51초|
|평균 시간|42.10초|46.17초|
|전체 입력|201,447|433,312|
|캐시 읽기 제외 입력|22,119|50,592|
|출력|1,605|1,604|

| 사례 | NLC 시간 | Sky 시간 |
|---|---:|---:|
|보너스 켜기|30.14초|39.73초|
|보너스 끄기|48.55초|37.91초|
|버튼 이름·식별자 변경|47.60초|60.87초|

## 맞춘 조건과 검증

- 같은 Codex CLI0.161.0, 요청 모델 gpt-6.1-sol, 추론 강도 medium,
  같은 기본 도구 설정·240초 제한·독립 임시 workspace·ephemeral 새 대화.
- 같은 소유 AppKit 앱과 작업값. 각 arm 전 phase entry, amount/factor0,
  bonus off로 초기화했다. 같은 pair의 task 표시는 같은 job ID다.
  초기 oracle의 amount/factor/bonus/phase/control_version/active가 일치했다.
- 실행 순서는 NLC→Sky, Sky→NLC, NLC→Sky로 교대했다. 같은 사용자 입력·
  포커스 보존 제한과 소스/oracle/prior trace 읽기 금지 지시를 제공했다.
  레이어별 사용 지침만 다르게 했다.
- oracle을 에이전트 밖에서 읽어 값·result·done·처리1회를 확인했다.
  동일 job의 과거 commit과 새 commit을 분리했다. 종료 시 앱은 비활성이었다.
- 설정 파일 hash와 source manifest가 전후 동일했다. 에이전트의 지침 읽기,
  Flow 작성·UI 실행·최종 답까지 시간과 사용량에 포함했다.

이번 NLC는 session 안내와 한 Flow 실행을 사용했고 Sky는 node_repl의
공개 API를 사용했다. NLC는 각 arm에서 완료된 command 항목2개,
Sky는 command1개와 MCP 항목5/5/6개를 기록했다. NLC stdin 전송·poll은
별도의 완료 command 항목으로 집계되지 않으므로 이를 전체 tool roundtrip
횟수로 해석하지 않는다. 입력 차이를 특정 한 원인으로 확정하지 않는다.

## 한계와 범위

각 사례1회, 총3쌍이다. 동일 요청 모델·설정과 초기 fixture 값을 맞췄지만
실제 serving model, 서버 cache, 전체 AX focus/selection 상태와 실행 중 사용자
입력 보존을 독립적으로 완전히 검증한 것은 아니다. cache를 비웠다고 주장하지
않으며 토큰은 보고된 사용량이고 청구비용은 미확인이다.

버튼 변경 사례는 새 대화의 시작부터 변경돼 있었으며 자동 수리의 벤치마크가
아니다. 이 결과는 최초 작성·작업 수행을 포함한 cold 비교다. 작성한 Flow를
10회 재생한17.14초 결과와 직접 섞지 않는다. 재사용 script를 가진 Sky와
NLC batch의10회 동일 조건 비교, 여러 작업 종류에서의 반복 표본은 별도 과제다.
단회 관측만으로 NLC가 항상 빠르거나 통계적으로 우월하다고 결론내리지 않는다.

```sh
.venv/bin/python examples/benchmark_matched_conditions.py \
  --output reviews/<new-private-directory> --model gpt-6.1-sol --reasoning-effort medium
```
