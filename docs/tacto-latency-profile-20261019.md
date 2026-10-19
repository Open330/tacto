# Tacto 지연 프로파일 — 2026-10-19

이번 두 표본에서는 주된 지연이 UI 입력 자체보다 **Flow 작성·입력 대기와
에이전트 왕복**, 그리고 실패 시 검증 timeout·복구에 있었다. 앞선30.579초
실행에는 이벤트별 시각이 없어 그5.087초 차이의 원인을 소급 확정할 수 없다.

## 측정 방법

현재 개발 launcher, 요청 모델 `gpt-6.1-sol`/medium,120초 제한으로 bonus_on과
bonus_off를 수행했다. 각각 새 에이전트·새 fixture 프로세스·빈 제출 이력으로
시작했다. 순서는 Tacto→Sky와 Sky→Tacto로 바꿨다. 모두 정확한 결과·단일
Finish·비활성 앱을 oracle로 확인했고 source/config 전후 해시도 일치했다.

`benchmark_matched_conditions.py --profile`이 CLI stdout 이벤트의 host 수신
시각을 기록한다. Tacto에는 기존 `session --timings`를 요청해 초기 관측,
Flow 입력 수신, 재생 및 driver 호출의 단조 시계 시간을 수집했다. 이 기능은
벤치마크에서 opt-in이며 기본 실행에는 observer를 추가하지 않는다.

## 결과

| 과제·도구 | 전체 | 도구 밖 구간 | Flow 입력·파싱 대기 | 실제 run_flow 구간 |
|---|---:|---:|---:|---:|
| bonus_on · Tacto | 21.039초 | 13.303초 | 5.341초 | 1.826초 |
| bonus_on · Sky | 27.339초 | 23.382초 | 별도 계측 없음 | 별도 계측 없음 |
| bonus_off · Sky | 26.485초 | 22.874초 | 별도 계측 없음 | 별도 계측 없음 |
| bonus_off · Tacto | 39.900초 | 17.035초 | 16.167초(두 세션 합) | 5.701초(두 실행 합) |

도구 밖 구간에는 모델 계획·응답, 서버 대기, 통신·스케줄링·프로세스 종료 등이
섞여 있다. **순수 추론 시간으로 읽으면 안 된다.** 도구 실행 span도 interactive
session의 stdin 대기를 포함한다. 중첩 span은 합이 아닌 union으로 계산했다.
stdout 버퍼링 때문에 아주 짧은 도구 구간의 정밀도는 낮다. Sky의 도구 span
합집합은3.956초·3.610초이며 내부 UI 시간만으로 분리하지는 못한다.

첫 Tacto의 초기 관측은0.350초였다. run_flow1.826초 중 driver 집계는
act6회0.478초, observe2회1.093초, wait_until5회0.148초다. session 총7.519초와
host 도구 span7.736초 사이0.217초에는 launcher·프로세스·도구 전달 등이
포함되므로, 이를 순수 Python 시작 시간이라고 단정하지 않는다.

## 느린 실행에서 확인한 실패 경로

bonus_off의 첫 set_value는 실행돼 수신 값12를 읽었지만 검증에서 실패했다.
보고된 오류는 `postcondition_timeout`, expected는 null, observed는12였다.
해당 wait_until이4.032초를 소비했다. 에이전트는 recovery 안내를 읽고 새
session에서 현재 상태를 확인해 남은 동작을 수행했고 Finish는 한 번이었다.

첫 session의 Flow 입력 대기는11.174초, 두 번째는4.993초였다.
첫 run_flow4.151초 중4.032초는 검증 대기였고, 후속 성공 run_flow는1.550초다.
즉 잘못된 검증 작성은 timeout뿐 아니라 추가 안내·계획·연결·입력 대기를 만든다.
복구 안내 읽기 한 번이9,745문자를 추가 반환했다. 두 Tacto 실행의 총 입력은
69,684/132,956, 출력은488/1,168이었다. 다른 과제이므로 증가분을 전부 안내
파일에 귀속할 수는 없지만, 실패가 추가 세션·문서 읽기·모델 왕복으로 이어지는
경로를 실제 기록에서 확인했다.
실제 입력 JSON 원문은 저장하지 않아 어떤 잘못된 키를 보냈는지는 확정하지 않는다.
이전39.9초와 정상21초는 과제·순서도 달라 모든 차이를 이 오류에 귀속하지 않는다.

## 확인한 작성 오류 방지 개선

별도 파서 점검에서 nonempty postcondition에 비교 predicate가 전혀 없어도
파싱을 통과하고, 입력한 뒤 항상 false로 확인되는 문제를 재현했다.
새 session 입력에서 `field`만 있거나 `value`를 넣은 조건은 이제 입력 전에
짧은 필드 오류로 거절한다. 기존 저장본의 로드·수리·skip_if 의미는 유지한다.
`equals`, `contains`, `absent`, `absent_role`, nonempty `all_of`는
유지하며, 기존 empty object의 의미도 그대로다. 중첩 조건의 경로도 표시한다.
안내의 모호한 field/value 표현을 `params.value`와 `postcondition.equals`로
구분했다. 실제 실패 JSON을 보존하지 않았으므로 이 수정이 이번 특정 오류의
정확한 원인을 고쳤다고 확정하지 않는다.

계측 회귀33개, 이후 파서·session·grouped 조건·작성 수리·benchmark를 포함한
최종108개 회귀를 통과했다(`profile-compatible-final-regression.log`).
잘못된 새 조건은 입력0회·짧은 오류·값 미노출을
검사했다. 수정 후 실앱 반복 시간의 감소는 아직 측정하지 않았다.

## 다음 최적화 순서

1. 첫 관측과 필요한 작성 안내를 한 번에 받아 안내 읽기→session 시작 사이의
   에이전트 왕복을 줄인다. 필요한 안내를 늘리거나 한꺼번에 로드하지 않는다.
2. 작성 오류를 입력 전 검증하고 필요한 필드만 수리한다. 모델이 이미 쓴
   값을 다시 입력하거나 완료한 prefix를 재전송하지 않는다.
3. 같은 작업의 반복은 검증된 Flow 재생으로 모델 작성·입력 대기를 제거한다.
   이번 측정은 최초 작성이므로 재사용 성능과 혼합하지 않는다.
4. 그 뒤 관측 비용을 줄인다. 첫 성공 표본의 실제 act는0.478초이므로 클릭
   자체 최적화만으로 전체21초를 크게 단축하기는 어렵다.

원본은 `reviews/tacto-native-resumption-20261019/profile-two-pairs/report.json`,
해시를 포함한 파생 집계는 같은 상위 폴더의 `profile-summary.json`이다.
실제 serving 모델·전체 context·billing·typing/caret 동등성은 미검증이며,
전체 비교 적격성은 false다. 계측 출력이 모델에 추가되므로 이전 비계측 결과와
단순 전후 효과 비교도 하지 않는다.
