# 자연어 컴퓨터 조작 프로토콜 (NLC Protocol) — 방법론 v0.1

> 2026-09-15. 컴퓨터 사용이 불편한 유저를 위해 자연어(대화)로 컴퓨터를 조작하는
> 인터페이스와 봇 도구를 만들기 위한 설계 방법론.
> 근거: Codex computer use 로컬 실측(리버스 엔지니어링 + codex 피어 실사용),
> OpenAI/Anthropic 공개 API 조사, 업계 프로토콜·벤치마크 서베이.

---

## 1. 조사 결과 (Evidence)

### 1.1 Codex computer use 실측 (이 머신에서 직접 확인)

**모델-facing 인터페이스는 "코드 액션"이다.** Codex는 액션마다 tool call을 하나씩
쓰지 않는다. 지속형 JavaScript 커널(`mcp__node_repl__js`) 하나를 열고, 그 안에서
`@oai/sky`(구형) 또는 통합 `cua`(신형, "tinysky-alt") 라이브러리를 import해서
관찰과 조작을 코드로 배치 실행한다. 결정적(deterministic) 액션 여러 개 + 상태
재관찰을 한 번의 tool call에 묶어 라운드트립을 줄인다.

**관찰은 접근성 트리(AX tree) 우선, 스크린샷은 fallback.** `get_app_state`가
구조화된 텍스트 트리를 반환하고(`button Description: 7, ID: Seven` 식),
토큰 절약을 위해 기본은 **이전 트리 대비 diff**만 반환한다(`disableDiff`로 전체
요청). 스크린샷은 트리가 불완전한 앱에서만 쓴다.

**시스템 아키텍처 (권한 분리형 데몬):**

```
Codex CLI/App (모델 루프)
  ↕ MCP stdio
SkyComputerUseClient.app          ← 플러그인 클라이언트
  ↕ SwiftProtobuf IPC
SkyComputerUseService.app         ← 화면기록·손쉬운사용 권한 보유, OS 조작 실행
  + CUALockScreenGuardian.app     ← 잠금화면 보호
  + 화면 오버레이: "ChatGPT is using your computer / Esc to cancel"
```

- 네이티브 코어는 ChatGPT.app 번들의 `sky.node`(N-API), `Package_ComputerUse`,
  `Package_Appshot`, `Package_SlimCore`.
- macOS SkyLight 사설 API + "focus-without-raise"로 **백그라운드 창에 입력을
  주입** → 사용자 마우스/키보드를 뺏지 않고 에이전트 여러 개가 병렬 동작.
- 실측 제약: 화면 잠금 시(`CGSSessionScreenIsLocked=true`) 창 캡처 불가
  (`cgWindowNotFound` -10005). 앱이 실행 중이어도 마찬가지.

**Sky API 표면 (공식 SKILL.md 전문 확보):**

```typescript
type Sky = {
  click({app, element_index?, x?, y?, mouse_button?, click_count?}): Promise<void>;
  drag({app, from_x, from_y, to_x, to_y}): Promise<void>;
  get_app_state({app, disableDiff?}): Promise<{app, screenshot: {url}|null, text}>;
  list_apps(): Promise<App[]>;
  perform_secondary_action({app, element_index, action}): Promise<void>;
  press_key({app, key}): Promise<void>;          // xdotool 문법: "Return", "super+c"
  scroll({app, element_index, direction, pages?}): Promise<void>;
  select_text({app, element_index, text, prefix?, suffix?, selection_type?}): Promise<void>;
  set_value({app, element_index, value}): Promise<void>;
  type_text({app, text}): Promise<void>;
};
```

**신형 통합 `cua` API**는 앱·브라우저·탭을 하나의 `Target` 인터페이스로 통합:
`getAXState / getScreenshot / click / paste / pressKey / scroll / selectText /
setValue / typeText / performSecondaryAction` + 탭 전용 `goto / back / reload /
close / markDeliverable / markHandoff`. 인벤토리는 `cua.initialize()` →
`{apps[], browsers[{tabs[]}]}`.

**루프 규칙 (공식 문서 명시):**
- UI 액션 후 **반드시 재관찰** → `element_index`를 최신 트리에서 다시 유도.
  낡은 인덱스 재사용 금지.
- 관찰 호출에 **auto-wait 내장**(기본 ~1초, 로딩 표시 감지 시 최대 +5초).
  모델이 sleep을 넣지 않는다.
- 액션 시도 ≠ 완료. **반환된 UI 상태에 결과가 보여야 완료**로 판정 (verify-after-action).
- AppleScript/osascript/JXA 대체 금지 — 단일 경로 강제.

### 1.2 OpenAI 공개 API (Responses API `computer` tool)

- 액션 스키마: `click(x,y,button) / double_click / drag(path[]) / move / scroll
  (x,y,scroll_x,scroll_y) / keypress(keys[]) / type(text) / wait / screenshot`.
- 루프: 스크린샷 → 모델 → `computer_call{action}` → 실행 후
  `computer_call_output{screenshot}` 반환.
- 안전: `pending_safety_checks`(malicious_instructions / irrelevant_domain /
  sensitive_domain)를 **사람에게 표시하고 `acknowledged_safety_checks`로
  반환해야** 다음 스텝 진행. GPT-5.4부터 범용 모델에 native 통합.
- Codex 앱 승인 모드 3단계: Ask for approval(기본) / Approve for me(위험 액션만
  확인) / Full access.

### 1.3 Anthropic computer use

- 2026-08 GA: `computer_toolset_20260801`(17개 툴) + **별도의 AX-tree 기반
  `browser_toolset_20260801`**. 구형 `computer_20250124`는 screenshot/click류/
  drag/type/key/hold_key/scroll/wait/cursor_position.
- 신형에서 `zoom` 추가, 하드코딩 `display_size` 제거(좌표 오클릭의 최대 원인 수정).
- 레퍼런스 구현: anthropics/claude-quickstarts computer-use-demo (Docker/Ubuntu/
  xdotool/VNC).

### 1.4 업계 합의 (서베이: "The Click Layer")

- **관찰 방식 승자는 하이브리드**: 트리 우선 + 비전 fallback (browser-use,
  Playwright MCP, UFO2 모두 이 방향). 순수 비전(OmniParser)은 grounding 정확도
  73~89%에서 아이콘이 최난.
- OSWorld SOTA ~72.5% ≈ 인간 베이스라인(~72%). 2024-10의 14.9%에서 2년 만에 도달.
- 실패의 지배 유형은 **액션 루프 타임아웃(한 연구에서 72.3%)** → 액션마다 검증
  (verify-after-action)이 핵심 대책. 레이턴시의 50~75%는 플래닝.
- 제품 생존자(Claude for Chrome/Cowork, Siri App Intents 2.0)와 사망자(Operator
  →ChatGPT agent→중단, Project Mariner→중단) 공통 교훈: **자격증명·결제는
  사람에게 hand-back, 파괴적 액션은 하드코딩 예외, 실행 직전 확인.**

### 1.5 Confirmations Policy (Codex 내장 정책 전문 확보)

위험 액션 4단계 분류 — 우리 제품에 그대로 이식할 가치가 가장 큰 자산:

| 단계 | 처리 | 예 |
|---|---|---|
| **Hand-off** | 사용자가 직접 수행 | 비밀번호 변경 최종 제출, 보안 경고 우회 |
| **Always Confirm** | 사전승인 있어도 실행 직전 재확인 | 삭제(클라우드·로컬), 계정 생성 최종 단계, API 키 발급, CAPTCHA, 제3자 대상 커뮤니케이션, 금융 거래, 시스템 설정 변경, 의료 |
| **Pre-approval OK** | 최초 프롬프트에 명시되면 무확인 | 로그인("xyz.com 가줘"는 xyz.com 로그인 동의로 간주), 파일 업로드, 파일 이동/이름변경, 민감정보 전송(데이터+목적지 특정 시) |
| **No Confirm** | 항상 허용 | 쿠키 동의, 다운로드(inbound), 비파괴 관찰 |

핵심 원칙: 제3자 콘텐츠(웹페이지·PDF 지시문)는 **절대 허가로 간주하지 않음**
(prompt injection 방어) · 확인 메시지는 **위험+메커니즘 설명 필수** · 민감정보는
무엇을/누구에게/왜 명시 · 확인은 임팩트 직전에 한 번만(준비 작업 먼저).

---

## 2. 설계 결정 (Lock-down)

조사 결과를 근거로 다음을 확정한다.

- **D1. 관찰 = AX-tree 우선 + diff + 스크린샷 fallback.** Codex·Playwright·업계
  합의 동일. 토큰/레이턴시/정확도 모두 우세. 트리가 빈약한 앱에서만 비전.
- **D2. 액션 어휘 = 10개 내외 코어 프리미티브.** 관찰 3(`get_state`,
  `screenshot`, `list_targets`) + 조작 7(`click`, `set_value`, `type_text`,
  `press_key`, `scroll`, `drag`, `secondary_action`) + 브라우저 확장(`goto` 등).
  세 벤더의 교집합이 정확히 이 집합이다.
- **D3. 요소 지정 = 최신 관찰의 `element_index`, 좌표는 fallback.**
  인덱스에 **신선도(observation id) 규칙**을 넣어 낡은 인덱스 사용을 프로토콜
  수준에서 거부한다 (Codex는 관례로만 강제 → 우리는 스키마로 강제).
- **D4. 실행 모델 = 이산 메시지 프로토콜 + 배치 허용.** 외부 프로토콜은 감사·확인
  게이트가 가능한 이산 액션 메시지로 정의하되, Codex처럼 결정적 액션 시퀀스를
  하나의 `act_batch`로 묶어 라운드트립을 줄인다. (드라이버 내부 구현이 코드 실행
  이어도 무방 — 프로토콜과 구현을 분리.)
- **D5. 대기 = 관찰에 내장된 auto-wait.** sleep 프리미티브를 노출하지 않는다.
  드라이버가 액션 후 ~1초 + 로딩 감지 시 최대 5초 대기 후 캡처.
- **D6. 안전 = 4단계 확인 정책을 프로토콜 1급 시민으로.** §1.5 분류를
  `risk_class`로 스키마화. 실행 중 화면 오버레이 + 즉시 취소(Esc) + 잠금 보호.
- **D7. 검증 = verify-after-action을 루프에 내장.** 액션 결과가 다음 관찰에서
  확인되지 않으면 성공 보고 금지. 최대 재시도 후 사용자에게 상황 보고.
- **D8. 권한 분리 = 조작 데몬과 모델 루프 분리.** OS 권한(화면기록·손쉬운사용)은
  서명된 데몬만 보유, 봇/모델은 IPC로만 접근. Codex의
  Client↔Service 구조를 따른다.

---

## 3. NLC Protocol v0.1 스펙 초안

### 3.1 계층

```
[L4] 대화 레이어   : 자연어 의도 ↔ 진행 내레이션 ↔ 확인/핸드오프 대화
[L3] 태스크 레이어 : 의도 → 플랜(스텝) → 스텝별 실행/검증/복구
[L2] 액션 프로토콜 : 본 스펙. observe / act / confirm / handoff 메시지
[L1] 드라이버      : OS별 구현 (macOS AX, Windows UIA, 브라우저 CDP/AX)
```

### 3.2 메시지 타입 (L2)

```jsonc
// 관찰
{"type": "observe", "target": "com.apple.calculator",
 "mode": "tree_diff" | "tree_full" | "screenshot" | "both"}
→ {"type": "observation", "obs_id": "o17", "target": "...",
   "tree": "...", "screenshot_ref": "...", "waited_ms": 1200}

// 조작 (obs_id로 인덱스 신선도 강제)
{"type": "act", "obs_id": "o17", "target": "...",
 "action": {"name": "click", "element_index": 12},
 "risk_class": "none" | "preapproved" | "confirm" | "handoff",
 "intent_text": "곱하기 버튼을 누릅니다"}   // 사용자 내레이션용

// 배치 (결정적 시퀀스 + 자동 재관찰)
{"type": "act_batch", "obs_id": "o17", "target": "...",
 "actions": [...], "then_observe": "tree_diff"}

// 확인 게이트 (risk_class가 confirm 이상이면 드라이버가 강제 발행)
{"type": "confirm_request", "action_ref": "...",
 "what": "이메일 3통 삭제", "to_whom": null,
 "risk": "삭제하면 복구할 수 없습니다", "mechanism": "Gmail 휴지통 비우기 버튼 클릭"}
→ {"type": "confirm_response", "approved": true|false, "remember": false}

// 핸드오프 (자격증명·결제 등)
{"type": "handoff_request", "reason": "password_entry",
 "resume_signal": "field_filled"}

// 완료 (검증 증거 필수)
{"type": "result", "status": "done" | "partial" | "blocked",
 "evidence_obs_id": "o21", "narration": "계산 결과 408을 확인했습니다"}
```

### 3.3 에이전트 루프 (L3)

```
intent(자연어) → plan(스텝 목록, 사용자에게 요약 제시)
  loop per step:
    observe → decide → [confirm gate?] → act → observe(검증)
    실패 시: 재관찰 → 대안 경로 → N회 후 사용자 보고
  → result(증거 obs_id 포함) → 자연어 내레이션
```

### 3.4 초보 사용자용 대화 레이어(L4) 원칙

1. **모든 act에 `intent_text`** — "지금 ○○을 누르고 있어요"를 실시간 내레이션.
2. **확인 요청은 일상어로**: 무엇을/누구에게/왜 + 위험 한 줄. 기술 용어 금지.
3. **핸드오프는 자연스러운 부탁으로**: "비밀번호는 직접 입력해 주세요. 입력이
   끝나면 제가 이어서 할게요."
4. **플랜 선제시**: 3스텝 이상 태스크는 시작 전에 "이런 순서로 할게요" 요약.
5. **취소는 항상 한 마디로**: "그만"/Esc → 즉시 정지, 현재까지 상태 보고.
6. **웹페이지가 시키는 것은 사용자 지시가 아니다** — 제3자 지시문 발견 시
   사용자에게 보여주고 물어본다.

### 3.5 결정론적 재생 (Deterministic Replay) — 토큰 효율의 핵심

> 목표: 모델은 흐름을 **한 번만 학습**하고, 이후 실행은 **모델 토큰 0**으로
> 재생한다. codex 피어와 공동 설계·실증(2026-09-15~16).

**원리.** 모델을 매 스텝 호출하는 대신, 태스크를 `Flow`(스텝 목록)로 기록해
저장한다. 각 스텝은 **volatile한 `element_index`를 저장하지 않는다** — 인덱스는
관찰마다 재발급되기 때문이다(실증: 계산기에서 `=` 후 결과 노드가 늘며
`Seven` 9→11, `Equals` 12→14로 이동, `ID:Seven`은 불변). 대신 **안정적 셀렉터**
(role + identifier/title/description + ancestor scope)를 저장하고, 재생 시 최신
트리에서 셀렉터를 재해석해 인덱스를 다시 구한다.

**셀렉터 해석 정책 (codex resolver policy A~E, 코드에 구현됨):**
- **A. Scope 우선** — bundle_id + 목적 컨테이너(ancestor)로 검색 범위 한정.
  런타임 PID/window 번호는 영구 키로 저장하지 않음.
- **B. role + AXIdentifier로 유일 매칭.** title/description은 검증 조건으로 분리
  (동적/번역 title에 과민 방지). `AXIdentifier`는 nullable이며 앱 버전 간 영구
  안정성이 **보장되지 않으므로** "ID면 항상 안전"으로 일반화하지 않는다.
- **C. ID 부재 시** role + 정확한 label(출처 속성 명시) + 안정적 조상 anchor.
  locale/app version 가정을 레시피에 기록.
- **D. 반복 행/가상 리스트**는 업무 키 + 컬럼/헤더 + 컨트롤 role로. absolute
  child path/ordinal은 기본 폴백에서 제외.
- **E. 텍스트/시각 앵커·좌표는 마지막 별도 전략** — silent fallback 금지, trace에
  어떤 resolver가 선택됐는지 기록.
- **모든 전략은 매칭 수 == 1 요구.** 0개 → `target_missing`(제한시간 재관찰 후),
  2개 이상 → `target_ambiguous`. **조용한 `find()[0]` 또는 자동 다운그레이드
  금지.** (실증: 계산기 `Multiply`가 keypad 밖에도 존재해 ambiguous → ancestor
  `CalculatorKeypadView`로 유일화.)

**실행 계약 (매 재생마다):** 관찰 → 유일 타깃 해석 → 신선도·권한·앱·창·활성
상태 확인 → **1회** 액션 → 의미 있는 postcondition 대기 → 성공 기록. **성공은
입력 이벤트 발행이 아니라 상태 검증으로 정의**(D7). wall-clock sleep 대신
monotonic deadline + 조건 폴링(가능하면 AX 알림). 타임아웃으로 실행 여부 불명확
시 맹목 재시도 금지 → `outcome_unknown`/handoff (결제·전송·토글은 중복 실행 특히
주의). **D6 risk gate는 기록 시점뿐 아니라 재생 매 실행마다 유지.**

**TOCTOU.** `obs_id` 일치만으로는 observe~act 사이 UI 변경을 막지 못한다. 매
스텝은 fresh observe 직후 resolve→즉시 act로 창을 최소화하고, precondition
(`enabled==true` 등)을 실행 직전 재검증한다(P1).

**용어 범위(정확성).**
- *결정론적* = "고정된 관찰 + 레시피에서 **동일한 타깃/실패 결정**을 내린다".
  외부 앱·네트워크·UI 타이밍 전체가 결정론적이라는 뜻이 아니다.
- *모델 토큰 0* = 기록된 경로가 정상 매칭되는 **순수 로컬 재생 경로에 한정**.
  예외 복구·재학습·시각 모델 호출은 별도 경로로 표기.

**실기기 실증 (2026-09-16, PASS).** 계산기 12×34를 NLC 드라이버로 모델 토큰 0
재생 → 7스텝 전부 `id@CalculatorKeypadView`로 유일 매칭, postcondition `408` 검증.
재생이 실전에서 잡아낸 두 동적 케이스: (1) `Multiply`/`Equals`는 title이 아니라
Identifier에 존재 → 셀렉터 수정. (2) 리셋 버튼이 상태 의존적으로
`AllClear`↔`Clear`로 identifier가 바뀜 → silent downgrade 대신 **명시적 any-of
identifier**(`["AllClear","Clear"]`)로 표현, cardinality==1 유지. resolver policy가
설계대로 작동함을 확인.

**레시피 스키마 (예, `nlc/flow.py`):**
```jsonc
{"name": "calc_12x34", "target": "com.apple.calculator",
 "steps": [
   {"action": "click",
    "selector": {"role": "Button", "identifier": "Seven",
                 "ancestor": "CalculatorKeypadView"},  // 매칭 수 1 강제
    "risk_class": "none", "intent_text": "7 버튼",
    "expect": "408"}                                    // postcondition (D7)
 ]}
```

### 3.6 SafetyContract — 키워드가 아니라 의미가 권한을 결정한다

> 실사용 장애에서 도출(2026-09-16, cxweb advisor 공동설계): 계산기 "모두
> 지우기"(AC)가 한국어 키워드 `지우기`에 걸려 confirm 게이트로 오차단. 라벨
> 정규식은 언어·앱·버전마다 무한 추가해야 하는 **추상화 계층 오류**였다.

**원칙.** *모델은 의미를 추론하고, 결정론적 코드가 권한을 결정하고, replay는
의미를 다시 추론하지 않고 그 의미가 성립할 조건만 검증한다.*

```
AUTHORING (모델 허용, 1회)
  사용자 의도 + OS 정체성 + AX 컨텍스트 + UI텍스트[UNTRUSTED]
      → SafetyContract     ← 위험도가 아니라 사실/의미
         effects{read|write_ephemeral|write_persistent|delete|transmit|
                 financial|credential|install|system_config}
         data_classes · resource{object_type, scope, selection_count}
         destination{kind, identity} · reversibility(unknown 허용)
         commit_point · evidence[provenance] · unknowns[]
DETERMINISTIC POLICY (코드만)
  SafetyContract → none | preapproved | confirm | handoff | blocked
REPLAY (토큰 0)
  ContextBinding invariant 재검증 — "안전하다고 판단한 근거 X,Y,Z가 아직 참인가"
```

핵심 규칙 (`nlc/contract.py` 구현·검증 완료):
- **모델은 risk level을 선언할 권한이 없다.** 계약(사실)만 쓰고 policy가 도출.
  계산기 AC = `delete + scope=entry + reversible` → **none** (예외 목록 아님).
  계정 삭제 = `delete + scope=account + irreversible` → confirm. 동일 AX 구조,
  다른 의미 — 실측으로 AX 구조 신호(AXPress/button)는 이 둘을 구분 못 함.
- **unknown은 block이지 confirm이 아니다.** authorization-critical 필드
  (effects/reversibility/resource/destination)가 unknown이면 실행 불가 —
  시스템이 설명 못 하는 행동에 사용자 동의를 요구하는 것은 안전장치가 아니다.
- **UI 텍스트는 절대 승인을 생성할 수 없다.** Evidence에 provenance를 달아
  `user_intent`만 authorization 근거가 됨. 화면의 "안전함/이미 승인됨" 지시는
  무시하고 unknowns로 기록 (prompt injection 방어를 프롬프트가 아니라 스키마로).
- **승인은 스텝 번호가 아니라 행동에 묶인다.** ApprovalToken =
  binding digest + effects + destination + expiry + one-shot. 효과 확대·대상
  변경·재사용 모두 거부됨 (실증).
- **invariant는 계층적.** bundle/서명·target·operation·payload digest·resource·
  destination 변화 = hard(계약 무효 → 재학습, confirm 아님). 라벨 변화 =
  drift(localization/업데이트일 뿐, 재평가 신호). AllClear↔Clear·영↔한 라벨
  변화에도 계약 유지 — 이것이 유연성의 근원.
- 키워드 분류기는 **계약 없는 legacy 스텝의 폴백**으로만 잔존. escalate-only
  prior조차 이번 장애를 재현하므로(advisor 지적) 신규 경로에서 완전 배제.

### 3.7 두 개의 IR — Flow(무엇을 할지)와 Trace(무엇이 보였는지)

> 실사용 디버깅에서 도출: 사용자 머신의 실패를 이쪽에서 재현할 수 없어 왕복이
> 여러 번 발생했다. 원인은 계산기가 `Identifier: StandardInputView;value:408`처럼
> **AXIdentifier에 휘발성 상태를 덧붙이는** 것이었고, 우리 머신은
> `StandardInputView`만 노출해 같은 코드가 다르게 동작했다.

Flow(레시피)는 이미 이식 가능한 IR이었지만, **실행 당시 화면이 어떻게 보였는지**의
기록이 없었다. 이를 두 번째 IR로 분리한다 (`nlc/trace.py`):

```
Flow JSON    무엇을 할지  — 셀렉터·계약·검증조건 (기존)
Trace JSON   무엇이 보였고 무엇이 일어났는지 — 관찰 트리·액션·결과 (신규)
```

- `TracingDriver`가 실 드라이버를 감싸 모든 관찰/액션을 기록(동작은 무변경).
- `TraceDriver`는 **앱·GUI 세션·권한 없이** 기록된 관찰로 플로우를 재실행한다.
  실패한 사용자의 run을 다른 컴퓨터에서 그대로 재현하고, 고친 뒤 회귀 테스트로
  고정할 수 있다. `uv run nlc do "..." --trace run.json`.
- 안전 게이트는 트레이스 재실행에서도 동일하게 작동한다(안전 동작 자체가 재현
  대상이므로).
- 한계를 명시한다: 트레이스는 *관찰된 세계*를 재현할 뿐 실제 UI를 다시 돌리지
  않는다. 기록에 없는 화면이 필요해지면 `trace_exhausted`로 정직하게 멈춘다.

**식별자 안정성 교훈(D3 보강).** AXIdentifier는 nullable일 뿐 아니라 **상태를
품을 수 있다**. `;` 앞을 안정적 정체성으로, 뒤를 상태로 해석한다. 이는 구조적
구분자 기반이므로 `Save`≠`SaveAs`의 정확성을 해치지 않는다.

### 3.8 네이티브 도우미(Swift) — 성능이 아니라 권한 때문에 필요하다

**측정 결과 Python은 병목이 아니다.** AX 관찰 78~172ms, AXPress 클릭 118ms인데
순수 파이썬 작업(셀렉터 해석)은 **0.12ms**다. 비용은 전적으로 AX 프로세스 간
호출이 지배하므로, Rust/Swift로 바꿔도 같은 호출을 같은 속도로 한다. 레이턴시를
줄이려면 언어가 아니라 settle 대기와 관찰 횟수를 줄여야 한다.

**그런데 권한 모델은 지금 구조로는 부적절하다(실측 확인).** macOS TCC는 클라이언트를
**바이너리로 식별**하는데, venv의 `python`은 대개 **공유 인터프리터로의 심볼릭
링크**다(`~/.local/share/uv/python/.../python3.11`). 그래서:

- NLC에 접근성 권한을 주면 **그 인터프리터로 실행되는 모든 스크립트**가 권한을
  얻는다. 실증: NLC와 무관한 `/tmp/not_nlc.py`가 다른 앱의 창을 그대로 읽었다.
- 파이썬 버전을 올리거나 재설치하면 경로가 바뀌어 권한이 끊긴다.
- 모델 루프와 OS 권한이 같은 프로세스에 있다 — **D8(권한 분리) 위반**.

**결론: 배포 시 서명된 네이티브 도우미로 분리한다.** Codex도 같은 이유로
`SkyComputerUseService`(LSUIElement, 서명됨)를 별도로 두고 MCP/IPC로만 통신한다.

```
NLC (Python)            모델 루프·플로우·IR — OS 권한 없음
   ↕ IPC (Unix socket, L2 메시지 그대로)
NLCHelper.app (Swift)   서명·공증된 LSUIElement
                        접근성/화면기록 권한 보유, AX 읽기 + 이벤트 주입
                        조작 중 오버레이 + Esc 중단도 여기서
```

이렇게 하면 (1) 권한이 **이 도우미 하나**에만 붙고, (2) 코드 서명으로 식별되어
업데이트에도 유지되며, (3) 파이썬 쪽이 손상돼도 OS 권한은 얻지 못한다. L2
프로토콜이 이미 프로세스 경계를 전제로 설계돼 있어 드라이버 교체만으로 끝난다.

**지금 당장의 완화책(구현됨):** `nlc doctor`가 공유 인터프리터에 권한이 부여되는
상황을 감지해 경고한다. 사용자가 Allow를 누르기 전에 그 범위를 알아야 한다.

**전제조건:** Swift 빌드는 Xcode 라이선스 동의(`sudo xcodebuild -license`)가 필요하고,
서명 인증서는 이미 보유 중이다.

### 3.9 일반적 상호작용을 위한 IR 확장

계산기는 데모일 뿐이므로, IR은 임의의 상호작용을 표현할 수 있어야 한다.

| 기능 | 없으면 표현 못 하는 것 |
|---|---|
| `params` / `tables` / `derived` | "12×34"가 아니라 "임의의 두 수를 곱하기" |
| `for_each` | 자릿수·항목 수가 가변인 입력 |
| **`capture`** | **화면의 값을 읽어 뒤 단계로 전달** — "청구서에서 총액을 읽어 폼에 입력" |
| `postcondition` + `derived` | 하드코딩 없이 결과 검증 |
| 재파라미터화(`nlc parameterize`) | 이미 배운 구체 레시피를 **모델 없이** 템플릿으로 승격 |

`capture`가 특히 중요하다. 이것이 없으면 레시피는 데이터를 **밀어 넣기만** 할 수
있고 꺼내 올 수 없어서, 앱 사이를 오가는 실제 업무를 표현하지 못한다.

**재파라미터화의 계약 규칙:** 어떤 필드를 파라미터로 바꾸면 그 필드는 설계상
가변이므로 `ContextBinding`에서 **고정을 해제**해야 한다. 그러지 않으면 배운
값 외의 모든 입력에서 계약이 무효화된다(실측으로 잡은 버그). 불변인 것(앱·role·
컨테이너·operation)은 계속 검증한다.

---

## 4. 구현 로드맵

| 단계 | 내용 | 재료 |
|---|---|---|
| **P0 프로토타입** ✅ 실기기 E2E PASS | macOS AX 드라이버(`nlc/driver.py`) + 결정론 재생(`nlc/flow.py`). 계산기 12×34를 **모델 토큰 0**으로 재생, 7스텝 유일 매칭, postcondition 408 검증. L2 프로토콜·D3 stale·D6 gate·cardinality resolver 전부 검증 | pyobjc(AXUIElement + CGEvent). 브라우저는 Playwright MCP 재사용 예정 |
| **P1 신뢰성·안전** ✅ 구현됨 | precondition(enabled 재검증) + 구조화 postcondition(서브트리 field_equals) + monotonic deadline 조건 폴링(`wait_until`) + 안전 룰엔진(`nlc/safety.py`, §1.5 4단계 정규식 분류 + escalation-only merge). 실증: Delete 버튼 승인 없이 차단, 승인 시 진행 | §1.5 정책을 룰 엔진으로 이식 완료 |
| **P2 NL 봇** ✅ 구현됨 | 자연어 에이전트(`nlc/agent.py`: 인텐트→플로우→플랜·내레이션→복구/게이트) + 플로우 저장소(`store.py`: 학습1회·재생다회 토큰0) + 온보딩(`onboard.py`) + 병렬(`parallel.py`) + 세션/capability(`session.py`) + CLI(`cli.py`). 신규 플로우 authoring만 모델 토큰 소비 | 로컬 결정론 재생; authoring은 Claude API 훅 |
| **P3 평가** ✅ 5/5 PASS | `examples/eval.py`: task_success·determinism·safety_gate·resolver_honesty·safety_classifier. 확장 예정: 실사용 시나리오 30개 + OSWorld subset | verify-after-action 준수율, 게이트 정확도, 성공률 |

**빌드 vs 재사용 판단**: 드라이버(L1)는 기존 자산 재사용이 옳다 — 브라우저는
Playwright MCP/CDP, macOS 네이티브는 AX API 직접 구현이 얇다. 우리의 차별화는
L2~L4: **프로토콜 + 안전 정책 + 초보자용 대화 레이어**다. Codex/Claude가 이미
잘하는 "개발자용 CU"를 다시 만들지 말고, 그 위의 접근성 레이어를 만든다.

---

## 5. 화면 잠금 · 온보딩 · 병렬 조작 (codex 공동조사)

### 5.1 화면 잠금 조작 — 결론: 일반 권한으로는 불가, 별도 제품 필요
codex와 공동조사(Apple 문서 + learn.chatgpt.com/docs/computer-use#locked-use).
**잠긴 macOS GUI를 접근성·화면기록 권한만으로 조작하는 것은 불가능하다.** ChatGPT
"Locked use"는 Apple **authorization plug-in**(`/Library/Security/SecurityAgentPlugins/CodexComputerUseAuthorizationPlugin.bundle`)을
설치해 *신뢰된 턴에만* 잠시 unlock하고, 그동안 모든 디스플레이를 가리고 로컬 입력을
차단하며, 로컬 활동 감지 시 재잠금하는 **별도의 로그인/unlock 제품**이다. 재사용
가능한 공개 API가 아니며, 서드파티가 하려면 authorization plugin 자체를 만들어야
한다(entitlement 하나 추가 수준이 아님).
- **채택 정책(구현됨, `nlc/session.py`)**: capability를 정직하게 분리 —
  `locked_gui_access=false`(기본), `trusted_unlock=provider_specific`,
  `headless_browser=backend_specific`. `preflight()`가 verdict를 반환:
  `ready` / `session_locked`(→ handoff) / `unknown_lock`(키 부재를 unlocked로
  단정하지 않음). `no_window_content`는 독립 오류로 유지하되 `lock` 상태를 필드로
  첨부.
- **실용 대안 A(구현됨)**: 잠금과 싸우지 않고 *idle* 잠금을 방지 —
  `caffeinate -di -w <pid>`를 `KeepAwake` 컨텍스트로 감싸 태스크 수명 동안 유지.
  best-effort이며 수동 잠금·보안정책·덮개닫힘·화면보호기는 해결 못 함(문서화).
- **대안 B/C**: 가상 디스플레이는 렌더 대상 확보일 뿐 AX/TCC/auth 경계를 풀지
  않음(별도 VM의 unlocked guest는 다른 설계). 브라우저 업무는 headless
  Playwright 경로 우선 검토.

### 5.2 권한 온보딩 (구현됨, `nlc/onboard.py`)
타깃 유저에게 시스템 설정 권한 부여가 최대 이탈 지점. 마법사가 접근성(필수)·화면
기록(선택)을 점검하고, 누락 시 **정확한 설정 창을 열고 한 문장**으로 안내(단계
나열 금지). AX-tree 우선 설계라 화면기록은 선택으로 처리.

### 5.3 병렬 조작 (구현됨, `nlc/parallel.py`)
focus-without-raise의 정직한 구현: AX 액션(`AXPress`·`AXValue`)은 요소별이라
포커스 없이 **진짜 병렬**(distinct 앱 동시 조작), CGEvent(타이핑·좌표)는 전역
포커스 의존이라 프로세스 락으로 **직렬화**(안전 인터리브). 실측: 2개 앱 동시 관찰
91ms. 사설 API 없이 달성.

### 5.4 확장 구현 완료 (2026-09-16)
- **브라우저 백엔드**(`nlc/browser.py`) — Playwright + CDP `Accessibility.getFullAXTree`.
  동일 L2 프로토콜(observe/act/wait_until)·동일 Flow/Selector/Postcondition으로
  웹 폼 결정론 재생 PASS. codex 가이드 반영: native AX / browser AX / DOM /
  Playwright locator는 **별도 ID 공간** — 별도 obs_id/인덱스, Flow는 backend 간
  비이식. web 값 검증은 whole-tree `contains`(값이 input 노드 하위에 안 들어감).
- **음성 인터페이스**(`nlc/voice.py`) — macOS `say` TTS + 플러그형 STT(기본
  타이핑 폴백). 확인 게이트를 음성 네/아니요로 해결(모호하면 안전 거부), 스텝
  내레이션을 음성 출력. 레이턴시는 "위험 한 줄 + 짧은 예/아니요"로 최소화.
- **모델 기반 authoring**(`nlc/authoring.py`) — 미지의 인텐트를 만나면 대상 앱을
  관찰하고 `muxa ask`(provider-agnostic)로 인텐트+AX트리→Flow를 생성, 셀렉터가
  트리에서 유일 해석되는지 검증 후 저장. **유일한 토큰 소비 경로**, 이후 재생은
  토큰 0. `Agent`에 훅으로 연결(unknown_task 시 자동 학습).
- **평가 6/6 PASS**(`examples/eval.py`) + **30개 시나리오 카탈로그**(`examples/scenarios.md`).

### 5.5 남은 후순위
실 STT 엔진 연결(현재 플러그형), 실사용 시나리오 온디바이스 authoring 검증,
OSWorld subset 벤치마크.

---

## 부록 A. 실측 로그 요약

- codex 피어(gpt-6-astra)가 muxa msg로 API 표면 전체 보고
  (thread `comp-use-research`, req_18d57c3d7e2079b8_1).
- **계산기 클릭 데모 성공 (E2E 실증 완료)**: All Clear→1→2→Multiply→3→4→Equals를
  전부 `sky.click`으로 수행. 매 클릭 후 `get_app_state(disableDiff:true)` 재관찰
  → 최신 트리에서 다음 인덱스 재유도. 화면 텍스트 진행 0→1→12→12×→12×3→12×34→408,
  최종 트리에서 결과 408 검증. **D3(인덱스 신선도)·D7(verify-after-action)을
  그대로 실증.** 단, 화면 잠금 중에는 실패(`cgWindowNotFound`,
  `CGSSessionScreenIsLocked=true` 확인) — 잠금 해제 후 성공.
- 공식 문서 위치:
  - `/Applications/ChatGPT.app/Contents/Resources/cua_node/lib/node_modules/@oai/sky/docs/skills/oai_sky_lib/macos/SKILL.md`
  - `.../@oai/cua/docs/tinysky-alt-core-node-repl.md` (통합 cua API)
  - `.../@oai/cua/docs/tinysky-alt-confirmations.md` (확인 정책)
  - `.../@oai/browser-desktop/docs/` (브라우저 안전·가시성·업로드)
- 서베이 아티팩트: The Click Layer — https://claude.ai/artifact/TNhrhxEVTdGehisvsJWGYy
