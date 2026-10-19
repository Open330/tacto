# 실제 모델의 후보 paging·provider 검증

2026-10-18 개발 checkout의 자동 selector 수리를 Codex·Claude 명시 선택으로 확장했다.
아래는 **실제 모델＋시뮬레이션 driver** 검증이며 실제 앱 복구 성공률이 아니다.
화면 잠금으로 Finder 평가는 실행 전 SKIP했다.

## 실제 시행

| 입력 형식 | provider | 모델 요청 | 결과 |
|---|---|---:|---|
|후보 번호를 세는 기존 형식|Codex|3|세 번째 페이지 후보를 선택해 완료|
|후보 번호를 세는 기존 형식|Claude|3|설명문·잘못 센 후보 번호를 반환해 suffix 실행 전 거절|
|후보별 index＋그 값을 복사하는 안내|Codex|3|세 번째 페이지의 index23을 선택해 완료|
|동일한 index 형식|Claude|3|세 번째 페이지의 index23을 선택해 완료|

새 형식은87개 후보를 offset0/34/63의3페이지로 나누고 JSON 패킷 전체를 각8192
UTF-8 byte 안에 유지했다. 완료한 앞 입력을 재송신하지 않고 필요한 suffix만 실행해
기대값12를 확인했다. provider별1회이므로 index 추가만의 인과 효과나 provider 간
속도·토큰·비용 우열을 확정하지 않는다. index 표시와 안내 문구를 함께 변경했다.

[Codex 원본](../reviews/tacto-live-repair-paging-20261018/codex-indexed/report.json) ·
[Claude 원본](../reviews/tacto-live-repair-paging-20261018/claude-indexed/report.json) ·
[보존한 Claude 실패](../reviews/tacto-live-repair-paging-20261018/claude/report.json)

## 구현 범위

`--auto-repair codex` / `--auto-repair claude`는 같은 typed provider 경로·paging·호출
상한·checkpoint·최신 전체 tree 검증을 사용한다. 다른 provider로 자동 전환하지 않는다.
관측 provider가 요청과 다르면 metadata를 보존하고 `provider_mismatch`로 멈춘다.
provider가 미관측이면 unknown을 유지한다. 페이지 요청마다 기존 model-call 한도를
소비하며 보류·잘못된 답·예산 소진 시 완료한 prefix를 재송신하지 않는다.
번호는 페이지 내 번호이고 원래 node 대응은 호스트가 유지한다.

설명문을 섞은 응답을 엄격한 JSON으로 수용하지 않는다. 후속 수정은 이 거절을
`invalid_candidate_response`로 명시해 checkpoint 실패와 구분한다.
이전 실패 기록의 generic STOP 코드를 소급해서 바꾸지 않는다.

기존 benchmark의 `checks.original_prefix_not_repeated`는 사실 성공 시 전체 배열[2,3]을
검사했다. 실패 시 false는 prefix 반복의 증거가 아니다. 새 benchmark는 prefix1회 여부와
성공 배열을 별도로 검사하고 simulation의 action index 배열도 저장한다. 이전 원본은 보존한다.

## 사용량·비용 해석

새 형식 Codex의 보고 입력 합계22,680·출력29, Claude는20,298·31이다. provider별
토크나이저·cache 정의·모델이 다르므로 절감률로 비교하지 않는다. Codex 모델명과
실제 청구액은 미관측이다. Claude의 CLI 보고 모델은claude-opus-5-5이고 비용은
CLI list estimate 0.16298 USD이며 실제 청구액이 아니다.

Codex에는 모델 요청 전 로컬 Muxa 거절3개가 있어 CLI 모델 요청3개와 구분한다.
공통 집계 `unknown_records`에는 이 로컬 거절도 포함되므로 unknown3개를 원격 모델
요청3개의 사용량 누락으로 해석하지 않는다. route invocations6개가 remote requests6개는 아니다.
8192 byte 예산은 JSON 패킷이며 provider system 지침·message framing 전체가 아니다.

## 서명 경로·실제 앱

서명 launcher 경유 단일 JSON 응답 검사도 Codex·Claude 각1회 PASS했다.
[Codex 평가](../reviews/tacto-live-repair-paging-20261018/signed-codex-evaluation.json) ·
[Claude 평가](../reviews/tacto-live-repair-paging-20261018/signed-claude-evaluation.json).
UI 없는 typed 응답·metadata 검사이며 원격 취소·과금 중단의 증거가 아니다.

[Finder 평가](../reviews/tacto-real-apps-20261018/finder-evaluation.json)는 화면 잠금으로
실행 전 SKIP, scored0, pass_rate=null이다. 폴더 생성·사용자 입력 보존을 시험하지 않았다.
잠금 해제 전 GUI 조작은 하지 않으며 실제 앱·저장/전송 인과 증거·이전 capture를 보존한
장기 자동 재개는 미완료다. 이번 변경은 checkout 코드이며 설치 Preview2나 이전 개발.app에
자동 배포하지 않았다.

## 후속 네이티브131옵션 과제와 왕복 비용

`reviews/2026-10-18/native-select-model-inherited-helper-1792332086198650000.json`은
실제 Codex 백본·서명 helper·소유 AppKit ComboBox의131개 옵션 중 `Choice127`을
선택하는 과제다. 제한180초, 요청당 표시 예산2500(o200k_base), 목록 표시8행이며
FAIL186682ms였다. 완료한 동작은 open_select1회와 scoped scroll2회다. 스크롤의
행 이동 수신은 확인했으나 선택 완료는 없었다. source subset은 안정이었고,
입력 audit은 INCONCLUSIVE(원본 CHANGED)이며 평가 후 session_locked였다.
잠금과 deadline이 겹쳤으므로 순수 모델 성능 실패로 인과를 단정하지 않는다.

22개의 승인된 prompt 제출 경계가 기록됐다. o200k_base 표시 prompt 합계37852,
최대1985였지만 serving tokenizer는 검증하지 않았다. 이는 provider 전체 입력 토큰이나
과금 합계가 아니다. 최종 readback 예외가 report.update 인자 평가에서 발생해
provider_calls와 loop 종료 기록을 누락했다. 원본 누락값은 미상으로 보존한다.

피어 분석 `req_18dfa4e07b8f2cc8_53`과 원본 재확인에서 열린 ComboBox의131개 옵션
메타데이터는 유지됨을 확인했다(초기3개+추가128개).
병목은 옵션과 중복된 행 후보, 뒤 페이지에 있는 scroll 후보, 매 스크롤 후 전체
재페이징이었다. 닫힌 상태의 options=[]를 옵션 유실로 오해하지 않는다.
참조 find로 목표를 찾은 뒤에도 scroll 후보를 보기 위해 페이지를 순회했다.

실패 계측을 먼저 수정했다. run 전반의 context-local provider collector를 finally에
보존하고, loop 결과를 먼저 기록한 뒤 final readback을 수행한다. 읽기 실패는
`final_readback_unavailable`와 예외 class로 구분하며 입력 재전송·채널 재연결은 없다.
실제 provider record를 발생시킨 뒤 run/읽기 실패를 주입하는 회귀4개가 통과했다.
피어 리뷰 `req_18dfa4cd3c4ad1b0_51`도 받았다.

helper의 기존 전체 행 geometry로 `scroll_pages_hint`를 추가했다. 기존 scroll의
1page는 scrollbar 정규화 범위의0.1이므로 행 gap과 전체 행 span에서1~10의
추정값을 계산한다. geometry가 없거나 비정상·가로 잘림이면 hint를 내지 않는다.
provider padding이나 비선형 동작 때문에 실제 이동량과 다를 수 있으며, 선택 전
재관측의 visible=true 검증은 그대로다. 관련16개 회귀와 서명 빌드·번들링을
완료했다. 이 hint는 signed helper의 ComboBox popup metadata에 제공하며,
직접 Python driver의 일반 AX 메뉴 경로에는 추가하지 않았다.

페이지 순서 최적화도 피어 `req_18dfa4f9780badb0_54`에서 구현했다. 열린 ComboBox와
같은 container의 유일한 visible option에 대응하는 scroll 후보를 먼저 보여주되,
모든 원래 후보·pick·필드는 유지한다. metadata가 없거나 모호하면 원순서다.
페이지 cursor는 순서에도 묶이고, 출력 계획은 계속 현재 페이지의 원래 후보로
검증한다. synthetic planner 회귀에서는 find 뒤 같은 페이지의 scroll을2요청으로
답할 수 있음을 확인했으며, 숨은 항목 선택과 페이지 밖 pick은 계속 거절했다.
실제 모델2회 호출이나131옵션 선택 완료의 증거는 아니다. 페이지·예산·실패 계측
통합 회귀29개가0.361초에 통과했고, 루트 읽기 전용 검토와 피어 통합 리뷰
`req_18dfa56f267d8948_56`에서도 blocking 문제는 없었다. 작은 예산에서는 두 후보가
같은 페이지에 들어가지 않을 수 있다. timeout·요청 상한을 늘리는 변경이 아니며
아직 새 실측의 속도·토큰 절감률은 없다.

통합 전체 회귀1차는2560개/422.932초, failures3·errors1·skipped3이었다
(`full-integrated-regression.log`).118개 소스/산출물 부분집합은 전후 동일했다.
네 건 모두 테스트가 의도한 공개 디렉터리를 생성하는 fixture에서 실행 환경의
umask077이0700을 적용한 것이 원인이었다. 정상 mask022로 해당17개 테스트는
통과했다. 로그는0600으로 예약하고 테스트 child만 mask022로 실행하는 전체
재검사를 시작했다(`full-integrated-normal-mask-regression.log`). 종료 결과 전에는
전체 통과라고 보고하지 않는다. 시스템 권한 설정이나 설치 앱은 변경하지 않았다.

정상 mask 재검사는2560개/416.558초, **OK(skipped=3)**로 종료했다.
실행2557개가 통과했고 동일한118개 소스/산출물 부분집합은 전후 일치했다
(`full-normal-mask-source-before.json`, `full-normal-mask-source-after.json`).
첫 mask077 실행과 관련17개 재검사는 별도 실행으로 보존한다. 이 전체 회귀는
현재 순서·hint·계측 변경을 포함하지만, 새 실앱 성공·원격 모델 비용·Sky와의
조건 동등성을 증명하지 않는다. 후속 isolated readiness 진단 패치는 이 통과
코호트에 포함되지 않는다.

## 실제 백본으로 기록된 후보의 계획 단계 재측정

화면 잠금 상태에서 UI를 조작하지 않고, 위131옵션 과제의 같은 기록 후보
스냅샷(index1)을 실제 Codex·Claude에 각각 제공했다. 입력 보고서 SHA는 네 행
모두 같다. 새 프로브는 `examples/probes/native_snapshot_planning.py`이며,
원래 owned AppKit snapshot만 받는다. 원본을 수정하지 않고 모델 호출·표시 prompt
경계·실패·소스 해시를 새0600 보고서에 기록한다.
원본의 `ui_actions_executed=0`은 host 프로브의 UI dispatch가 없었다는 뜻이다.
provider CLI 내부의 도구·부작용을 별도로 관측한 증거는 아니다. 후속 프로브는
이를 `host_ui_actions_executed=0`, `provider_side_effects_verified=false`로 명확히 했다.

| 백본 | 페이지 여유분 | 요청 수 | 시간 | 관측 입력 합계(그중 cached) | 관측 출력 합계 | 비용 USD | 반환 계획 |
|---|---|---:|---:|---:|---:|---|---|
| Codex | 기존 여유분, 후보 순서 개선만 | 8 | 38.208초 | 63,549(29,824) | 240 | 미상 | inspect_menu, 목표 scroll 아님 |
| Codex | 첫 select·scroll 쌍이 분리될 때만 여유분 조정 | 2 | 11.815초 | 15,973(6,912) | 165 | 미상 | find 후 scroll, pick3·pages10 |
| Claude | 기존 여유분, 후보 순서 개선만 | 8 | 26.396초 | 30,531(0) | 632 | 0.256824 추정 | inspect_menu, 목표 scroll 아님 |
| Claude | 첫 쌍의 여유분 조정 | 3 | 16.962초 | 12,141(0) | 889 | 0.114884 추정 | find·다음 페이지 후 scroll |

valid_scroll_plan은 UI 완료가 아니다. 현재 session_locked이므로 이 후속 단계에서
native 동작을 새로 실행하지 않았다. 실행 순서는 Codex 기존 → Claude 기존 →
Codex 여유분 조정 → Claude 여유분 조정이었다.

각 행은1회이며 원본과 요약은 `native-snapshot-{codex,claude}-{ordered,pair-budget}.json`,
`native-snapshot-planning-summary.json`이다. 위치는
`reviews/tacto-finder-windowless-20261018/`이다. 캐시·serving tokenizer·모델 설정의
완전한 동등성은 검증하지 않았으므로 통계적·인과적 개선률이나 백본 우열을 주장하지
않는다. Codex의 미상 토큰 레코드8개·2개는 각각 Muxa 로컬 경로 거절과1:1이고,
원격 호출 레코드의 입력·출력은 전부 관측됐다. cached 구성 요소는 입력 합계에
이미 포함되며 다시 더하지 않는다. 이 합계를 전체 청구량으로 부르지 않는다.
Codex 비용은 미상이고 Claude 비용은 `cli_reported_list_estimate`이며 청구액이
아니다. 반복 순서에 따른 cache 온도도 다를 수 있다. Claude 출력 토큰은 이 표에서 증가했다.

첫 실측에서 모델은 첫 페이지의 참조를 조회하지 않고 목록 끝까지 넘겼다.
o200k_base2500 조건에서 select 단독 prompt는1623토큰이지만 기존 표시 예산은
1667이어서 scroll 행이 다른 페이지로 밀렸다. 합성 counter2800 테스트가 이 경계를
놓쳤다. 이제 **첫 페이지가 select 하나만 담을 때만** 표시 예산을1875로 한 번
재계산하고, 실제 scroll 행까지 들어오는 경우에 채택한다. 이후 페이지와 일반
후보의 기존 여유분은 유지한다. 전체 prompt2500·요청 상한8·visible 재검증은 같다.
실제 action 필드를 포함한 o200k2500 회귀와 관련28개 검사가 통과했고,
피어 리뷰 `req_18dfa6b01f3dea28_5b`도 승인됐다.

계획 성공은 화면에서의 스크롤 수신·선택·입력 보존·전체 업무 성공이 아니다.
이 측정에는 새 scroll_pages_hint도 들어 있지 않다(과거 snapshot을 그대로 사용).
새 native 실행과 Sky 비교는 환경이 준비된 뒤 별도로 해야 한다.
피어 보고 리뷰 `req_18dfa702d833a070_5c`가 원본 네 행의 수치를 대조했고,
cache·비용·실행 순서의 범위 보완을 반영했다.

후속 다회 참조 조회도 전체 prompt 예산을 넘겨 제출하지 않는지 추가 확인해,
관련29개 회귀가0.396초에 통과했다(`initial-pair-budget-final-regression.log`).
여유분이 부족하면 조회 내용을 숨긴 채 행동하거나 예산을 늘리지 않고
`context_budget_overflow`와 빈 actions로 멈춘다.

## 효과가 없었던 CLI host 설정 가설

로컬 codex-cli0.163.0에서 `features.code_mode_host=false`를 text-only 호출에만
추가하는 pilot를 production 변경 없이 실행했다. 같은 tiny JSON 질문의 기본
호출은6124 input/9 output·5.288초, 후보 호출은6126 input/9 output·4.693초였고
둘 다 정확한 JSON을 반환했다(`codex-text-host-pilot.json`). 토큰 감소가 없어
이 flag는 제품에 추가하지 않았다. 단회 시간 차이를 변경 효과로 주장하지 않는다.

[공식 기능 설정 문서](https://learn.chatgpt.com/docs/config-file/config-basic#feature-flags)는
기능 토글의 일반 계약을 설명하지만 이 내부 key의 호환성을 보장하지는 않는다.
키 존재와 값 변화는 로컬 CLI의 features list에서만 확인했다. 사용자 전역 설정,
선택한 모델/effort와 image 경로는 바꾸지 않았다. CLI 기본 입력 비용 전체를 이
키 하나로 설명할 수 없으며, 호출 횟수 감소와 flow 재사용을 우선한다.

후속 실제 TextEdit Save·닫힘1회 통과와 Finder 준비 실패는
[별도 실앱 기록](tacto-textedit-saved-state-validation.md)에 있다. 이 native fixture의
실패를 다른 과제의 성공으로 대체하거나 새 Sky 비교로 부르지 않는다.

## 재현

```sh
.venv/bin/python examples/benchmark_live_repair_paging.py --provider codex --output reviews/<new-directory>
.venv/bin/python examples/benchmark_live_repair_paging.py --provider claude --output reviews/<another-new-directory>
```

실제 모델 사용량이 발생한다. GUI 입력은 없으며 출력은 새 디렉터리에만 기록한다.
