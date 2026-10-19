# 새 플로우 작성의 literal·브라우저 바인딩·수정 경로 검증

2026-10-18 개발 checkout 기준이다. 설치 앱과 글로벌 설치 skill은 변경하지 않았다.
전체 G0~G10 완료나 native 앱·Sky 비교 결과가 아니다.

## 재현한 문제와 변경

초기 작성 검사는 화면의 `{{field}}` 요소를 그대로 찾았지만, 실행 시 task parameter
`field=Different`를 적용하면 다른 요소로 바뀌었다. 최초 selector의 해석과 실제
실행 selector의 해석이 달랐다. 관측한 원문을 재사용하는 promotion/repair와 달리
최초 모델 authoring에는 literal을 선언하는 경로가 없었다.

모델 응답의 `literals` 맵을 기존 `Flow.literal_params`로 변환한다. saved Flow는
schema4와 읽기 전용 literal 데이터를 사용한다. task params·tables·derived·capture와
이름이 겹치거나 호출자가 literal을 덮어쓰면 거절한다. 보통의 `{{param}}` 입력은
계속 재사용할 수 있다. 관련 안내는 intent/tree에 `{{`가 있을 때만 추가한다.

첫 관측에서 literal과 참조가 충돌하면 짧은 작성 오류로 거절한다. 선언한 literal
alias는 허용한다. 고정 provenance인 `step.safety.evidence`는 실행 참조 스캔에서
제외한다. 이 검사는 최초 관측만 보므로 나중에 나타나는 dialog의 원문까지
판별하지는 못한다. 이후 selector의 해석과 효과는 replay에서 별도 검증한다.

작성 검사는 알려진 defaults와 literal을 적용한 selector로 수행하고 원본 Flow는
변형하지 않는다. 필수 입력이나 runtime capture가 아직 없으면 값을 추측하지 않고
해당 selector를 unchecked로 표시한다. 그 값에 의존하는 derived와 loop item도
같은 방식으로 미확인 상태를 보존한다.

실제 headless 시험에서는 별도 브라우저 authoring 버그도 확인했다. symbolic target
`page`를 binding에 넣었지만 live identity는 `about:` 같은 origin이었다.
host의 `driver.identity(target)`로 binding을 고정하고, 모델이 다른 target이나
모순되는 binding identity를 주면 거절하도록 수정했다. 이는 literal에 한정되지
않는 browser authoring 수정이다. 기존에 `page`로 잘못 pin한 플로우를 자동 완화하거나
마이그레이션하지 않는다. 해당 페이지에서 다시 author해야 한다. author는 한 개의
구체적인 관측 대상을 사용하며 target의 템플릿 일반화는 별도 작업이다.

## 작성 오류 자동 수정과 비용 경계

이전에는 작성 오류의 수정도 `verify=True`에 묶여 있었다. 이제 무효 레시피만
기본 최대1회 추가 모델 요청으로 고친다. 대상이 정해진 레시피의 정상 생성은1회다.
별도 대상 선택 모델 요청이 필요하면 그 요청도 같은 deadline을 사용한다. 일반 compile은
UI를 실행하지 않으며 `--verify`가 별도로 실행 검증을 켠다.
`compile --repair-rounds 0`은 추가 수정 호출을 끄고 UI 검증도 켜지 않는다.

모델 요청은 author 진입 시 한 번 정한 local deadline을 공유한다. 기본 최대420초이며
호출자가 더 짧은 deadline을 설정하면 그 한도를 따른다. 수정 전에 만료 여부를
확인해 추가 요청을 보내지 않는다. 이는 UI 실행 전체 시간이나 원격 생성 중단의
보장이 아니다. 관련 안내를 개발 skill과 CLI help에도 반영했다.
벽시계 기준이므로 `--verify`에 쓴 시간도 이후 모델 수정의 남은 예산을 줄인다.

## 실제 provider + headless browser 결과

원본은 `reviews/tacto-authoring-literals-20261018/`에 있다. 매번 새 Chromium 페이지에
literal label `{{field}}`와 미끼 label `Different`를 제공한다. 실제 모델로 author한
플로우를 저장·재로딩하고 runtime seed `field=Different`를 넣어 replay한다.
`result=correct`의 정확한 capture와 두 input의 DOM 값 불변을 각각 확인한다.

| 코호트 | 백본 | 결과 | 전체 시간 | 관측 입력/출력 토큰 | 실제 logical 모델 요청 |
|---|---|---|---:|---:|---:|
| v2, 수정0회 | Codex | 저장·재생·정확한 capture·DOM 검사 통과 | 7.559초 | 8506 / 137 | 1 |
| v2, 수정0회 | Claude | 작성 ValueError, 당시 세부 원인 미수집 | 5.157초 | 원본 참조 | 1 |
| v3, 수정0회 | Claude | literal_ambiguity 작성 거절, replay 미실행 | 5.278초 | 원본 참조 | 1 |
| v4, 수정 최대1회 | Claude | 통과, 첫 응답 유효하여 수정은 미사용 | 5.975초 | 4185 / 298 | 1 |
| 주입 오류 수리 | Codex | 합성 초기 오류1회 → 실제 모델 수정 → 저장·재생 통과 | 7.505초 | 8594 / 137 | 1 |
| 주입 오류 수리 | Claude | 합성 초기 오류1회 → 실제 모델 수정 → 저장·재생 통과 | 5.656초 | 4313 / 293 | 1 |

주입 수리는 첫 무효 응답을 benchmark가 만든다. 이 합성 응답은 실제 모델 응답이나
실제 모델 호출로 계산하지 않는다. 뒤의 수정 응답만 실제 백본이 생성한다.
자연 발생 실패율·성공률이나 순수 모델 간 성능 비교와 합치지 않는다.

초기 `provider-browser.json`은 두 백본 모두 `replay_ok=true` 뒤 결과 수집 코드의
AttributeError로 종료됐다. 정확한 oracle을 완료하지 못했으므로 실패로 보존했다.
수정 후 코호트와 합쳐 성공률을 계산하지 않는다. 이후에는 saved Flow도 보존한다.

백본별 model·tokenizer·캐시·CLI 초기 비용이 다르고 각 행은1회 실증이다.
Codex의 관측 사용량에는 별도로 로컬 Muxa route 거절1건이 있으며 이 경로의 사용량은
미상으로 남긴다. 원격 요청으로 추정해 합산하지 않는다. Codex 비용은 미상이다.
Claude의 비용은 CLI list estimate이며 실제 청구서가 아니다. 캐시 생성·읽기 조건이
달라 비용/시간 차이를 변경 효과나 우열로 확정하지 않는다.

각 실측의 source subset 전후 일치는 해당 원본 manifest 범위에서만 확인했다.
전체 의존성·OS·숨은 serving context의 동일성은 증명하지 않는다. 공유 deadline과
CLI opt-out은 이 실측 이후 보강됐으므로 그 변경의 성능 효과를 이 표로 주장하지 않는다.

## 남은 검증

최신 관련 회귀80개가26.739초에 통과했다(`final-regression.log`). 별도 navigation
회귀4개도 통과했다. 기존 namespace 이전 테스트가 없는 `tacto/` 임시 디렉터리에
파일을 쓰던 문제를 수정했다. 테스트 결과가 실제 provider 실행이나 native 실기를
대신하지는 않는다.

후속 검사에서 중첩 `decide` 분기의 `safety.evidence` 원문을 템플릿 참조로
오인하던 경로도 수정했다. 실제 Step 분기에서만 이 필드를 원문으로 취급하며,
일반 매개변수 안의 같은 이름을 가진 객체는 계속 검사한다. 관련 회귀28개가
2.777초에 통과했다(`nested-branch-regression.log`). 앞선80개와 겹치는 별도
실행이며, 실제 모델 재측정이나 추가 성능 개선 수치로 계산하지 않는다.

피어 설계·구현 리뷰 `req_18dfa005c7d9ad18_3a`, 작성 오류 수정 리뷰
`req_18dfa0461550caf0_3d`, CLI·공유 deadline·skill 리뷰
`req_18dfa09adf04a4d0_3e`에서 해당 변경을 승인받았다. 이 승인은 전체 제품
완료 승인이 아니다.

단위·injected AX·headless 증거는 native 앱 전체 성공을 증명하지 않는다.
일반 앱의 나중 dialog·긴 플로우·중단 후 복구, G7의 실제 저장·전송 수신 증거,
입력 보존과 새 같은 조건 Sky 비교, 설치 runtime·skill rollout은 여전히 남아 있다.
이후 Finder 배경 창 준비는 별도 실앱 코호트에서 통과했다. 범위와 남은
전체 작업·TextEdit 정리 검증은 [실앱 기록](tacto-textedit-saved-state-validation.md)을
따르며, 이 headless 통과로 다른 코호트의 실패나 SKIP을 대체하지 않는다.
