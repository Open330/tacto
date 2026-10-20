# 자연어 컴퓨터 조작 프로토콜 (Tacto Protocol) — 방법론 v0.1

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
- macOS의 백그라운드 실행은 공식 문서에서 확인된다. 다만 초기 조사에서 이를
  SkyLight의 "focus-without-raise" 하나로 설명한 것은 부정확했다(§3.20).
  2026-09-20 로컬 바이너리에서 synthetic focus·focus suppression·virtual cursor
  구성의 이름을 확인했으나 내부 호출 계약은 미확정이다. Tacto의 동등한 입력 격리는
  아직 구현·검증 중이다(G0 추적(비공개 로컬 기록: `reviews/2026-09-19/background-input.md`; 공개 원본 미포함)).
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

## 3. Tacto Protocol v0.1 스펙 초안

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

**실기기 실증 (2026-09-16, PASS).** 계산기 12×34를 Tacto 드라이버로 모델 토큰 0
재생 → 7스텝 전부 `id@CalculatorKeypadView`로 유일 매칭, postcondition `408` 검증.
재생이 실전에서 잡아낸 두 동적 케이스: (1) `Multiply`/`Equals`는 title이 아니라
Identifier에 존재 → 셀렉터 수정. (2) 리셋 버튼이 상태 의존적으로
`AllClear`↔`Clear`로 identifier가 바뀜 → silent downgrade 대신 **명시적 any-of
identifier**(`["AllClear","Clear"]`)로 표현, cardinality==1 유지. resolver policy가
설계대로 작동함을 확인.

**레시피 스키마 (예, `tacto/flow.py`):**
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

핵심 규칙 (`tacto/contract.py` 구현·검증 완료):
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
기록이 없었다. 이를 두 번째 IR로 분리한다 (`tacto/trace.py`):

```
Flow JSON    무엇을 할지  — 셀렉터·계약·검증조건 (기존)
Trace JSON   무엇이 보였고 무엇이 일어났는지 — 관찰 트리·액션·결과 (신규)
```

- `TracingDriver`가 실 드라이버를 감싸 모든 관찰/액션을 기록(동작은 무변경).
- `TraceDriver`는 **앱·GUI 세션·권한 없이** 기록된 관찰로 플로우를 재실행한다.
  실패한 사용자의 run을 다른 컴퓨터에서 그대로 재현하고, 고친 뒤 회귀 테스트로
  고정할 수 있다. `uv run tacto do "..." --trace run.json`.
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

- Tacto에 접근성 권한을 주면 **그 인터프리터로 실행되는 모든 스크립트**가 권한을
  얻는다. 실증: Tacto와 무관한 `/tmp/not_tacto.py`가 다른 앱의 창을 그대로 읽었다.
- 파이썬 버전을 올리거나 재설치하면 경로가 바뀌어 권한이 끊긴다.
- 모델 루프와 OS 권한이 같은 프로세스에 있다 — **D8(권한 분리) 위반**.

**결론: 배포 시 서명된 네이티브 도우미로 분리한다.** Codex도 같은 이유로
`SkyComputerUseService`(LSUIElement, 서명됨)를 별도로 두고 MCP/IPC로만 통신한다.

```
Tacto (Python)            모델 루프·플로우·IR — OS 권한 없음
   ↕ IPC (Unix socket, L2 메시지 그대로)
TactoHelper.app (Swift)   서명·공증된 LSUIElement
                        접근성/화면기록 권한 보유, AX 읽기 + 이벤트 주입
                        조작 중 오버레이 + Esc 중단도 여기서
```

이렇게 하면 (1) 권한이 **이 도우미 하나**에만 붙고, (2) 코드 서명으로 식별되어
업데이트에도 유지되며, (3) 파이썬 쪽이 손상돼도 OS 권한은 얻지 못한다. L2
프로토콜이 이미 프로세스 경계를 전제로 설계돼 있어 드라이버 교체만으로 끝난다.

**지금 당장의 완화책(구현됨):** `tacto doctor`가 공유 인터프리터에 권한이 부여되는
상황을 감지해 경고한다. 사용자가 Allow를 누르기 전에 그 범위를 알아야 한다.

**구현 완료 (2026-09-16).** `helper/TactoHelper.swift` + `helper/build.sh`.
Command Line Tools 툴체인으로 빌드하므로 **Xcode 라이선스 동의가 불필요**하다
(`/Library/Developer/CommandLineTools/usr/bin/swiftc`, SDK 명시). 서명까지 완료
(TeamIdentifier 48H4RZNB97). 파이썬 쪽은 `tacto/helper.py`의 `HelperDriver`가
stdin/stdout JSON 한 줄씩으로 **동일한 L2 메시지**를 주고받는다 — 드라이버 교체만으로
끝나며 프로토콜·IR·안전 게이트는 그대로다.

```bash
helper/build.sh                              # 빌드 + 서명
uv run tacto replay calc_multiply -p a=3 -p b=4 --helper
```

실증: 동일 IR이 기본 드라이버와 도우미 경유에서 같은 결과(12)를 내고, 트리 출력
포맷도 36요소로 일치한다. 남은 일은 배포 형태(.app 번들 + 공증)와, 권한을 도우미
에게만 요구하도록 온보딩을 바꾸는 것이다.

### 3.9 일반적 상호작용을 위한 IR 확장

계산기는 데모일 뿐이므로, IR은 임의의 상호작용을 표현할 수 있어야 한다.

| 기능 | 없으면 표현 못 하는 것 |
|---|---|
| `params` / `tables` / `derived` | "12×34"가 아니라 "임의의 두 수를 곱하기" |
| `for_each` | 자릿수·항목 수가 가변인 입력 |
| **`capture`** | **화면의 값을 읽어 뒤 단계로 전달** — "청구서에서 총액을 읽어 폼에 입력" |
| `postcondition` + `derived` | 하드코딩 없이 결과 검증 |
| 재파라미터화(`tacto parameterize`) | 이미 배운 구체 레시피를 **모델 없이** 템플릿으로 승격 |

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
| **P0 프로토타입** ✅ 실기기 E2E PASS | macOS AX 드라이버(`tacto/driver.py`) + 결정론 재생(`tacto/flow.py`). 계산기 12×34를 **모델 토큰 0**으로 재생, 7스텝 유일 매칭, postcondition 408 검증. L2 프로토콜·D3 stale·D6 gate·cardinality resolver 전부 검증 | pyobjc(AXUIElement + CGEvent). 브라우저는 Playwright MCP 재사용 예정 |
| **P1 신뢰성·안전** ✅ 구현됨 | precondition(enabled 재검증) + 구조화 postcondition(서브트리 field_equals) + monotonic deadline 조건 폴링(`wait_until`) + 안전 룰엔진(`tacto/safety.py`, §1.5 4단계 정규식 분류 + escalation-only merge). 실증: Delete 버튼 승인 없이 차단, 승인 시 진행 | §1.5 정책을 룰 엔진으로 이식 완료 |
| **P2 NL 봇** ✅ 구현됨 | 자연어 에이전트(`tacto/agent.py`: 인텐트→플로우→플랜·내레이션→복구/게이트) + 플로우 저장소(`store.py`: 학습1회·재생다회 토큰0) + 온보딩(`onboard.py`) + 병렬(`parallel.py`) + 세션/capability(`session.py`) + CLI(`cli.py`). 신규 플로우 authoring만 모델 토큰 소비 | 로컬 결정론 재생; authoring은 Claude API 훅 |
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
- **채택 정책(구현됨, `tacto/session.py`)**: capability를 정직하게 분리 —
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

### 5.2 권한 온보딩 (구현됨, `tacto/onboard.py`)
타깃 유저에게 시스템 설정 권한 부여가 최대 이탈 지점. 마법사가 접근성(필수)·화면
기록(선택)을 점검하고, 누락 시 **정확한 설정 창을 열고 한 문장**으로 안내(단계
나열 금지). AX-tree 우선 설계라 화면기록은 선택으로 처리.

### 5.3 병렬 조작 (구현됨, `tacto/parallel.py`)
AX 액션(`AXPress`·`AXValue`)은 요소를 직접 지정하고, CGEvent 호출은 프로세스
락으로 직렬화한다. 실측된 2개 앱 동시 **관찰** 91ms는 입력 격리나 실행 중
사용자 포커스 보존의 검증이 아니다. PID 이벤트 전달도 대상 앱 내부 focus와
전역 focus의 부작용까지 보장하지 않는다. 전경 활성화·HID 클릭 경로가 남아
있어 이를 제거하고 실제 비침해 실행을 검증하는 G0 작업을 진행한다.

### 5.4 확장 구현 완료 (2026-09-16)
- **브라우저 백엔드**(`tacto/browser.py`) — Playwright + CDP `Accessibility.getFullAXTree`.
  동일 L2 프로토콜(observe/act/wait_until)·동일 Flow/Selector/Postcondition으로
  웹 폼 결정론 재생 PASS. codex 가이드 반영: native AX / browser AX / DOM /
  Playwright locator는 **별도 ID 공간** — 별도 obs_id/인덱스, Flow는 backend 간
  비이식. web 값 검증은 whole-tree `contains`(값이 input 노드 하위에 안 들어감).
- **음성 인터페이스**(`tacto/voice.py`) — macOS `say` TTS + 플러그형 STT(기본
  타이핑 폴백). 확인 게이트를 음성 네/아니요로 해결(모호하면 안전 거부), 스텝
  내레이션을 음성 출력. 레이턴시는 "위험 한 줄 + 짧은 예/아니요"로 최소화.
- **모델 기반 authoring**(`tacto/authoring.py`) — 미지의 인텐트를 만나면 대상 앱을
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

### 3.10 도우미의 한계 — 지금 있는 것과 아직 없는 것

`--helper` 경로는 **권한의 위치**를 옮겼지만, 아직 **호출자 인증**은 없다.
외부 검증에서 지적된 그대로 정직하게 적는다.

**현재 강제되는 것**
- 최신 관찰만 유효(`stale_observation`), 그리고 **관찰과 액션의 앱이 일치해야 함**
  (`target_mismatch`) — 이게 없으면 A 앱을 관찰한 obs_id로 B 앱에 타이핑할 수 있다
  (`type_text`는 요소 목록조차 쓰지 않으므로).
- 부모 프로세스가 사라지면 종료, stdio 전용(소켓 없음), 실행 토큰 필수.
- `--helper` 실패 시 일반 드라이버로 **폴백하지 않음**(fail closed).

**호출자 인증은 왜 답이 아닌가 (조사 결과 방향 수정).** 다음 단계로 XPC audit
token 검증을 계획했지만, 착수 전에 확인한 사실이 설계를 바꿨다: **audit token은
클라이언트가 고유한 서명 바이너리일 때만 의미가 있다.** 우리 클라이언트는 공유
uv 파이썬이므로, XPC로 호출자를 인증해도 "그 인터프리터를 쓰는 아무 스크립트"가
통과한다 — 도우미가 해결하려던 바로 그 문제다. 즉 XPC 배관을 깔아도 지금 클라이언트
형태에서는 얻는 보안이 거의 없다. 실행 토큰·부모 PID 검사도 마찬가지로 인증이 아니다
(로컬 프로세스는 자기 자식에게 같은 환경변수를 줄 수 있다).

**대신 채택한 것: 사용자를 권한자로 둔다.** 호출자가 누구든 성립하는 성질은
"사람이 무슨 일이 일어나는지 보고 멈출 수 있다"는 것이다. 그래서 **표시와 중단을
도우미 안으로 옮겼다** — 액션을 수행하는 바로 그 프로세스가 소유하므로 호출자가
건너뛸 수 없다.

- 첫 액션 시 도우미가 대상 창에 테두리·안내를 **직접** 그린다(파이썬이 안 그려도).
- 도우미가 Esc를 전역 감시해 `aborted` 상태로 전환하고, 이후 모든 액션을
  `aborted_by_user`로 거부한다. 실증 완료.
- 부모의 코드 서명 정체성을 `SecCode`로 기록한다(이름 비교가 아닌 OS 확인).
  PID 재사용 경합이 있으므로 이것만으로 인증이라 부르지 않는다.

**XPC 판단의 정확한 범위(외부 검증으로 보정).** "XPC는 배관일 뿐"은 과한 표현이다.
code-signature로 *공유 파이썬 스크립트를 구별할 수 없다*는 핵심은 맞지만, XPC 자체는
PID 재사용 없는 peer 정보, UID/세션 제한, launchd 수명주기, `XPCPeerRequirement`
같은 이점을 별도로 준다. 또한 **"고유 서명 바이너리"도 충분조건이 아니다** — 그
바이너리가 임의 명령을 중계하는 범용 프록시라면 공격자가 그것을 직접 실행해 다시
confused deputy로 쓸 수 있다. 다음 단계의 `tacto`는 서명될 뿐 아니라 **그 자체가 보안
주체**여야 한다.

**구현 형태(확정).** per-user **LaunchAgent + `MachServices`** (root 데몬 불필요 —
접근성과 오버레이 모두 로그인 세션 기능). app bundle 배포 시 `SMAppService.agent`.
서버는 연결 수락 **전에** Team ID + 정확한 signing identifier requirement를 걸고,
audit token을 직접 다룰 때는 PID 재조회 대신 `SecTaskCreateWithAuditToken`을 쓴다.
그리고 **stdio 실행 경로를 제거**해야 바이너리 직접 실행 우회로가 닫힌다.

### 3.12 서명된 `tacto` — 경계가 실제로 닫힌 지점

**구현 완료.** `launcher/tacto.swift`(서명, `identifier ai.callabo.tacto`, Team
`48H4RZNB97`)가 유일한 클라이언트이고, 도우미는 시작 시 **조상 체인에 이 요구조건을
만족하는 프로세스가 있는지** `SecCodeCheckValidity`로 확인한다. 없으면 즉시 종료한다.

- 실증: 임의 프로세스가 도우미를 직접 실행 → `OSStatus -67050`으로 거부.
  서명 런처를 통하면 정상 동작(`./launcher/tacto replay ... --helper` → 48).
- 조상 체인을 보는 이유: 도우미의 직속 부모는 런처가 띄운 파이썬이다. 중요한 성질은
  **서명된 런처의 자손인가**이며, 그 자손이 되려면 런처가 띄워야 하고 런처는 우리
  코드만 실행한다.

**런처가 "보안 주체"인 이유(단순 서명과의 차이).** 서명만으로는 부족하다 — 임의
명령을 중계하는 프록시라면 공격자가 그것을 실행해 다시 deputy로 쓴다. 그래서 런처는
**스크립트 경로·인터프리터 재정의·eval 플래그를 받지 않고**, 고정된 인터프리터로
고정된 모듈만 실행하며, 코드 주입에 쓰일 수 있는 환경변수(`PYTHONPATH`,
`DYLD_INSERT_LIBRARIES`, `TACTO_HELPER_TOKEN` 등)를 제거한다. 호출자가 런처를 통해
표현할 수 있는 것은 **Tacto 명령 표면(플로우)** 뿐이다.

**여전히 닫히지 않는 것(정직하게).** 런처를 실행할 수 있는 사람은 플로우를 실행할 수
있다. 플로우는 정책 심사를 받고 화면에 표시되며 Esc로 멈출 수 있지만, **플로우의
계약은 그 플로우를 쓴 사람이 적는다.** 즉 정책은 사용자를 *잘못된 레시피*로부터
보호하지, 정직하지 않은 계약을 쓴 로컬 공격자로부터 보호하지 않는다. 그 지점의
방어는 도우미가 모든 읽기와 동작을 **보이게 하고 멈출 수 있게** 만드는 것이다.

### 3.11 현재 남은 위협 (정직하게)

(§3.12로 **해소됨**) 이전의 최대 위협은 아무 로컬 프로세스나 TCC 권한을 가진
도우미를 자기 자식으로 실행할 수 있다는 점이었다. 이제 서명된 런처의 자손만
받아들인다. 아래는 그 조치 이전의 기록이며, 마지막 단락의 완화책은 계속 유효하다. 실행 토큰은 공격자가 자기 값으로 정할 수 있으므로 막지
못하고, Esc로 세운 `aborted` 상태도 **그 인스턴스 한정**이라 새로 띄우면 초기화된다.
이는 `tacto`가 서명된 보안 주체가 되고 stdio 경로가 사라져야 닫힌다.

완화로 넣은 것:
- **읽기에도 표시**한다. `observe`가 오버레이 없이 도는 동안에는 다른 앱의 트리를
  조용히 가져갈 수 있었다(무결성은 보호되는데 기밀성은 무방비였다). 이제 읽기도
  화면에 드러난다.
- **표시 실패 시 동작 거부**(`indicator_unavailable`). 사람이 볼 수 없는 상태로
  진행하는 것은 이 장치의 목적과 정면으로 어긋난다.
- **Esc를 액션과 독립 경로로.** 이전에는 `act`가 메인 스레드를 점유해 긴
  `type_text` 중에는 Esc 핸들러가 돌지 못했다 — 가장 멈추고 싶은 순간에 못 멈추는
  결함이었다. 이제 액션은 메인 밖에서 돌고, 타이핑은 글자마다 중단을 확인한다.
- 오버레이는 창을 따라 위치를 갱신하고 `.statusBar` 레벨을 쓴다.
- 부모 서명 정보는 **telemetry로만** 기록한다(유효성·팀·requirement 미검증이므로
  인증이라 부르지 않는다).

### 3.13 권한을 도우미에게 — 번들과 온보딩

**`.app` 번들(구현 완료).** `helper/bundle.sh` 가 `Tacto Helper.app`
(`ai.callabo.tacto.helper`, LSUIElement, 서명)을 만든다. 번들로 묶는 이유는 포장이
아니라 **TCC가 클라이언트를 코드 서명으로 식별하고 목록에 번들 이름으로 표시**하기
때문이다:

- 시스템 설정 목록에 `python3.11` 이 아니라 **"Tacto Helper"** 로 뜬다 — 사용자가
  무엇을 허용하는지 판단할 수 있다.
- 권한이 **재빌드에도 유지된다**(실측 확인). 경로가 아니라 서명으로 식별되므로.
- `NSAccessibilityUsageDescription` 에 "조작 중 표시가 뜨고 Esc로 멈출 수 있다"는
  약속을 적어 둔다 — 권한 요청 화면이 그 약속을 보여준다.

**온보딩 전환(구현 완료).** `tacto doctor` 가 도우미를 우선한다. 도우미가 빌드되어
권한을 가지면 **파이썬 접근성 권한을 필수에서 제외**하고, 공유 인터프리터 경고도
띄우지 않는다 — 도우미가 그 문제를 이미 해결했기 때문이다. 도우미가 없으면 빌드
방법을, 권한이 없으면 정확한 설정 창을 안내한다.

**빌드.** `./build.sh` 하나로 런처·도우미·번들을 의존 순서대로 만든다. Xcode
라이선스 동의는 필요 없다(Command Line Tools 툴체인 + 명시적 SDK 경로).

### 3.14 경계를 실제로 뚫던 것들 (3차 검증에서 수정)

외부 검증이 §3.12의 경계에서 **실제 우회로**를 찾았다. 방향은 맞았지만 구현이
새고 있었다.

1. **`TACTO_HELPER_REQUIREMENT` 환경변수 override** — 개발 편의로 둔 것이 치명적이었다.
   런처가 이를 제거하지 않고 클라이언트가 환경을 그대로 넘기므로, 공격자가 통과하는
   requirement로 바꾸면 검사가 **권고로 전락**했다. → requirement를 **컴파일에 고정**.
2. **`CommandLine.arguments[0]` 신뢰** — 이것은 실행 경로가 아니라 **호출자가 넘긴
   argv[0]** 이다. 공격자가 진짜 서명 런처를 실행하며 argv[0]만 자기 경로로 주면
   런처가 **공격자의 `.venv/bin/python`** 을 실행했다 — 그러면 서명된 런처가
   공격자 코드의 조상이 된다. → `_NSGetExecutablePath()` 로 실제 경로를 얻는다.
3. **requirement에 Team ID 없음** — `anchor apple generic` 은 "Apple이 발급한
   신원"이지 "우리 팀"이 아니다. → `certificate leaf[subject.OU] = "48H4RZNB97"` 추가.
4. **`python -m` + 호출자 cwd** — Python은 `-m` 실행 시 현재 디렉터리를 `sys.path`
   앞에 넣는다. 즉 "고정 모듈"이 "고정 코드"가 아니었다. → `-I`(격리) 사용.
5. **PATH 상속(denylist)** — 빠뜨린 변수를 계속 찾는 구조였고, PATH 앞에 둔
   바이너리가 `open`·`caffeinate` 를 가로챌 수 있었다. → **allowlist**로 전환하고
   PATH를 시스템 경로로 고정.
6. **`codesign --deep` 서명** — Apple 권장은 **안쪽부터 각각 서명 후 바깥**이며
   `--deep` 은 검증용이다. → 순서 수정, hardened runtime 유지.

회귀 테스트(`permission_boundary`)가 두 가지를 고정한다: 환경변수를 permissive /
impossible 로 바꿔도 **판정이 동일할 것**, 그리고 argv[0]을 위조해도 공격자
인터프리터가 **실행되지 않을 것**.

**구조적으로 남는 것.** 현재 정책은 정확히 말하면 *"서명된 tacto의 후손이면 허용"*
이다. `tacto → python → 도구` 체인 중 하나라도 공격자 제어가 되면 그 프로세스도 유효한
후손이 된다. 이를 좁히려면 XPC로 **peer 자체**에 requirement를 걸어야 하며, 그때
peer는 공유 파이썬이 아니라 **팀이 서명한 전용 broker** 여야 한다(그러지 않으면
원점이다). 즉 최종 형태는 `서명·hardened broker ↔ XPC helper` 이고 파이썬은
broker에 좁은 데이터 요청만 보낸다.

### 3.15 Peer 인증 + broker — 조상 추정에서 신원 확인으로

§3.14가 남긴 구조적 문제는 정책이 실제로는 *"서명된 tacto의 후손이면 허용"* 이라는
점이었다. 체인 중 하나라도 공격자 제어가 되면 그 프로세스도 유효한 후손이 된다.

**해결: 연결한 프로세스 자체를 인증한다.** launchd 없이도 가능했다 —
유닉스 소켓의 `LOCAL_PEERTOKEN` 이 **peer의 audit token**(커널의 답, 이 연결에
바인딩, PID 재사용 경합 없음)을 주고, `kSecGuestAttributeAudit` 로 코드 객체를 만든
뒤 `SecCodeCheckValidity` 로 requirement를 검사한다. XPC를 쓰는 이유가 되는 바로 그
성질을 launchd 등록 없이 얻는다.

```
Python (구별 불가)  →  broker (서명·좁음)  ═══소켓═══>  helper (TCC 권한 보유)
   stdin/stdout          ai.callabo.tacto.broker          peer audit token 검증
```

**왜 broker가 필요한가.** peer 인증은 peer가 OS로 구별 가능한 바이너리일 때만
의미가 있는데, 공유 파이썬은 누가 실행하든 동일하게 보인다. 그래서 파이썬은
도우미에 **직접 붙지 않는다**. broker가 peer가 되고, 도우미는 broker의 audit
token을 검증한다. 실증: 서명 안 된 파이썬 직접 연결 → `peer_not_authorized`,
broker 경유 → 정상 실행.

**서명만으로는 부족하다는 것도 반영했다.** broker가 받은 것을 그대로 중계하면
서명된 confused deputy일 뿐이다. 그래서 broker는 **L2 어휘만** 받아들이고 모든
메시지의 형태를 검사한다 — 알 수 없는 `type`, 허용되지 않은 `action`, 정의되지 않은
필드, 타입 불일치를 모두 거부한다(`rejected_by_broker`). 통과하는 것은
observe/act 프로토콜이지 임의 명령이 아니며, 어떤 필드도 경로·명령·URL을 싣지
못한다. 실증: `exec`, `run_shell`, `path` 필드 주입, `element_index` 타입 위조가
모두 차단됐다.

회귀 테스트 `peer_authentication` 이 두 성질을 고정한다: 이 인터프리터는 직접
연결할 수 없을 것, broker를 통하면 되지만 어휘 밖 메시지는 거부될 것.

### 3.16 broker를 런처 안으로 — 서명된 confused deputy 제거

§3.15의 broker에는 결정적 결함이 있었다: **아무 로컬 프로세스나 `tacto-broker` 를
직접 실행하고 stdin으로 허용된 JSON을 넣으면** 도우미가 통과시켰다. 도우미는
"이것이 broker 바이너리인가"만 물을 뿐 "이 broker가 Tacto로부터 권한을 받았는가"는
묻지 않았기 때문이다. 좁은 프로토콜은 피해 범위를 줄일 뿐 **인증 경계를 만들지
않는다.**

**해결: 중계를 런처 안으로 옮기고, 자식에게는 상속된 채널만 준다.**

```
./launcher/tacto …            서명된 프로세스(= 신뢰 기점)
   │  socketpair, fd 3 상속        ← 다른 방법으로는 얻을 수 없음
   ├─ python -I -m tacto.cli         권한 없음, 주소도 모름
   └─ (검증 후 중계) ══소켓══>  helper   peer audit token 검증
```

- 별도 broker 바이너리 **삭제**. 도우미 requirement도 런처 하나만 허용.
- 소켓은 매 실행마다 `mkdtemp`(0700) 안에 만든다 — 고정 `/tmp` 이름의 **선점·가짜
  서버 치환**과 `unlink→bind→chmod` 경쟁이 사라진다.
- 실증: 런처 경유 정상(63), 런처 없이 `--helper` → "권한 채널이 없습니다".

**검증 강화(지적 반영).** 최상위 필드도 화이트리스트(이전엔 `action` 내부만 검사해서
최상위 `path` 주입이 통과했다), `secondary_action` 의 AX action 이름을 열거된 6개로
제한(이전엔 임의 문자열이 `AXUIElementPerformAction` 에 그대로 갔다), `NSNumber` 가
`CFBoolean` 과 브리지되므로 `true` 가 인덱스로 통과하던 것을 차단, 프레임 크기 상한.

**정정한 주장 두 가지.** `LOCAL_PEERTOKEN` 이 "PID 재사용 경합이 없다"고 적었던 것은
틀렸다 — XNU 구현이 소켓의 `last_pid` 로 `proc_find()` 한다. 그리고 우리는
`accept()` 시점에 **한 번만** 인증하는 반면 XPC는 OS가 **매 메시지마다** peer
requirement를 적용한다. 창을 좁혀 두었을 뿐(실행당 연결 1개, 우리만 들어갈 수 있는
디렉터리) 창 자체는 있다.

**남는 것.** 런처를 실행할 수 있는 사람은 여전히 플로우를 실행할 수 있고,
`type_text` 로 터미널류 앱에 텍스트를 넣는 **의미적 명령 실행**은 어휘 검사로 막을
수 없다. 그 지점의 방어는 계약 정책과, 도우미가 모든 읽기·동작을 보이게 하고 Esc로
멈출 수 있게 한다는 성질이다.

### 3.18 도우미는 LaunchServices 가 연다 (TCC 귀속)

**측정이 먼저다.** 같은 서명 바이너리를 세 가지 방법으로 띄우고 두 권한을 동시에
물었다 (2026-09-21, macOS 27.0):

| 기동 | Accessibility | Screen Recording | responsible path |
|---|---|---|---|
| 셸에서 직접 | true | false | `/usr/libexec/sshd-keygen-wrapper` |
| `posix_spawn` | true | false | `/usr/libexec/sshd-keygen-wrapper` |
| LaunchServices | false | **true** | `.../Tacto Helper.app/.../TactoHelper` |

읽는 법은 이렇다. **TCC 는 요청한 프로세스가 아니라 그 실행이 귀속된 신원에
묻는다.** spawn 된 자식은 자기를 spawn 한 쪽에 귀속되고, 여기서는 그게 사용자가
접속해 있는 SSH 세션이다. LaunchServices 로 열면 앱이 자기 자신에 귀속된다.
`ppid` 는 판별자가 아니다 — spawn 도 `ppid=1` 이면서 false 였다.

그래서 **Accessibility 는 지금까지 도우미의 권한이 아니었다.** 터미널의 권한을
같은 귀속 규칙으로 빌려 쓰고 있었을 뿐이고, 서명된 전용 도우미로 권한을
분리한다던 성질이 실제로는 성립하지 않았다. LaunchServices 로 옮기면서 그 대여가
끊겼고, 이제 'Tacto Helper' 에 직접 부여된 권한만 쓴다 — 원래 의도한 그대로다.

**랑데부는 방향이 뒤집혔다.** LaunchServices 는 부모-자식 관계를 주지 않는다:
상속된 디스크립터도, `ppid` 도, 종료 상태도 없다. 그래서 **런처가 먼저 listen**
하고 도우미가 되돌아 연결한다. 런처가 열기 전에 bind 해 두는 것이 "도우미의 소켓이
생길 때까지 기다린다" 방식에 남아 있던 **선점 창**을 닫는다 — 예전 방향에서는 같은
UID 프로세스가 그 경로를 먼저 bind 해 도우미 행세를 할 수 있었다.

받아들이는 조건은 둘이다: **코드 요구사항**(서명된 우리 도우미인가)과
**`NSRunningApplication.processIdentifier` 일치**(우리가 띄운 그 인스턴스인가).
pid 는 재사용되므로 보조일 뿐이고, 수명은 pid 가 아니라 인스턴스 객체가 쥔다.

**감사 세션 일치 검사는 넣었다가 뺐다.** 사용자가 원격 셸에서 화면을 조작하는 것은
이 프로젝트가 하려는 일이고, 그때 두 세션은 **설계상 다르다**: 측정값으로 런처는
asid 100228, 그를 위해 열린 도우미는 100018(콘솔 세션)이었다. 도우미는 화면을 가진
세션에 있어야 하므로 이 검사는 애초에 성립할 수 없었다.

**수명은 연결이 쥔다.** 부모가 없으니 `getppid()` 감시는 의미가 없다 — 그 값은
부모가 죽었을 때와 launchd 가 의도적으로 띄웠을 때가 구분되지 않는다. 도우미는
클라이언트 하나를 섬기고 그 연결이 끊기면 종료한다.

**진단은 통합 로그로 나간다.** LaunchServices 가 띄운 앱에는 stdout·stderr 가
없어서, 도우미가 거절 이유를 말해도 아무데도 닿지 않았다.

    log show --last 5m --predicate 'subsystem == "ai.callabo.tacto"'

### 3.17 남은 항목 일괄 구현 (advisor 우선순위대로)

**P0 — legacy stdio 경로 제거(실제 우회로였다).** 도우미는 `--listen` 없이 뜨면
토큰+조상 검사 모드로 동작했는데, **런처가 띄운 파이썬이 바로 그 "서명된 런처의
자손"** 이므로 파이썬이 도우미를 직접 spawn하면 조건을 만족하고 `readLoop()` 가
런처의 `vetted()` 를 **건너뛰고** `handle()` 로 직행했다. 즉 방금 만든 경계를
우리 자신의 자식이 우회할 수 있었다. 이제 플래그 없이 뜨면 즉시 종료하며,
토큰·조상 검사·readLoop 코드는 **삭제**했다. (그 플래그는 지금 `--listen` 이
아니라 `--connect` 다 — §3.18 참조.)

**P0 게이트 — 바이너리 실태 점검(통과).** 런처·도우미 모두
`flags=0x10000(runtime)`(hardened runtime)이고 `get-task-allow`,
`disable-library-validation`, `allow-dyld-environment-variables` 등 위험
entitlement가 없다. 확인 명령은 `codesign -dv --verbose=2` 와
`codesign -d --entitlements :-`.

**양방향 peer 인증.** 0700 디렉터리는 다른 사용자만 막을 뿐, 위협 모델인 **같은
UID 프로세스**는 소켓을 unlink/rebind해 가짜 서버로 응답할 수 있다. 그래서 런처도
연결 직후 `LOCAL_PEERTOKEN` 으로 **도우미의 signing requirement를 역검증**한다.
인증은 양방향이어야 한다.

**FD 위생.** socketpair 양쪽에 `FD_CLOEXEC`(없으면 나중에 spawn하는 도우미로 relay
end가 상속된다), 그리고 자식에서는 `close(relayEnd) → dup2(childEnd,3) →
close(childEnd)` 순서로 **fd 3 하나만** 남긴다(이전엔 원본 childEnd도 함께 남아
"자식은 fd 3만 갖는다"는 불변식이 성립하지 않았다).

**의미적 capability 축소(P1).** 블록리스트는 포기했다 — Terminal만 막아도 VS
Code·Xcode·브라우저 개발자도구의 내장 터미널이 남는다. 대신 **권한을 분해**했다:
- `type_text` 는 이제 **`element_index` 필수**이고, 그 요소의 role이 텍스트 입력
  계열(`AXTextField`/`AXTextArea`/`AXComboBox`/`AXSearchField`)이어야 한다. 이전엔
  요소를 전혀 보지 않고 PID에 키 이벤트를 보냈다 — "폼 채우기"와 "터미널에 명령
  치기"가 같은 primitive였던 이유다.
- **제어문자·개행 금지.** 삽입을 제출로 바꾸는 것이 개행이다.
- **`press_key` 제거.** 도우미에 구현도 없었고, 범용 키 primitive가 바로 실행
  경로다. 되돌아올 때는 `navigationKey` 와 명시적 `submit/shortcut` 으로 분리한다.

**스키마·상태 정합성(P1/P2).** action별 **정확한 키 집합**(이전엔 모든 action 필드의
합집합이라 `click` 에 `text`·`pages` 가 함께 와도 통과), 도우미에도 AX action
allowlist **복제**(권한을 쥔 쪽이 호출자의 필터링에 의존하지 않는다), 연결마다 관찰
상태 리셋, `aborted` 를 락으로 보호, 도우미에도 8MB 프레임 상한, 부분 쓰기·EINTR을
처리하는 `writeAll`.

**XPC는 P2로 유보.** 파이썬이 도우미 fd를 직접 갖지 않고 연결도 런처가 만들므로,
"매 메시지 인증"이 막아 주는 추가 공격면이 현재 토폴로지에서는 좁다. 대신 규칙으로
대신한다: **인증된 stream fd를 `SCM_RIGHTS` 로 전달하지 않고, 내부 fd는 모두
CLOEXEC이며, 소유권이 바뀌어야 하면 연결을 폐기하고 새로 accept-time 인증을
받는다.** 같은 fd에서 메시지마다 `LOCAL_PEERTOKEN` 을 다시 읽는 것은 XPC와 동등한
해결이 아니다. 도우미를 상주시키거나 여러 클라이언트를 받게 되면 그때 XPC로 옮긴다.

### 3.18 계산기 밖으로 — 실기기에서만 드러나는 것들 (2026-09-16)

`examples/scenarios.md` 의 30개는 **그럴듯하게 적은 것**이고, 이 절은 그중 실제로
돌려본 것들에서 나온 기록이다. 검증된 레시피는 `tacto/library.py` 에만 둔다.

**메뉴 막대가 통째로 비어 있었다.** `_walk_windows` 는 AXWindow만 걷는다. 저장·복사·
폴더 열기 같은 일상 명령은 창에 버튼이 없고 메뉴에만 있으므로, 그 어휘로는 **표현
자체가 불가능**했다. 실측 결과 메뉴는 **열지 않아도 전부 읽힌다** — 중첩 서브메뉴,
`AXEnabled`, `AXMenuItemCmdChar` 까지. 그래서 `menu` 는 좌표 추정이나 ⌘키 합성이
아니라 요소 단위 `AXPress` 로 구현된다. 메뉴는 관찰 트리에 넣지 않는다(앱 하나의
메뉴 막대가 수천 항목이라 매 관찰마다 그 값을 치르면 싼 경로가 비싸진다). 대신
**사용자가 읽는 제목 경로**로 지정하고, 각 단계는 대안 목록을 가질 수 있다 — 제목은
지역화될 뿐 아니라 **상태에 따라 바뀐다**(저장 안 된 문서는 `Save…`, 저장된 문서는
`Save` + 별도 `Save As…`).

**"성공" 이 아무것도 뜻하지 않는 경우가 또 있었다.** 선택 영역 없이
`Format > Font > Bold` 를 누르면 `AXPress` 가 0(성공)을 반환하고 문서는 그대로다.
그래서 `menu` 는 `type_text`·`press_key` 와 같은 **검증 의무** 집합에 들어간다.
이건 조심스러워서가 아니라 측정해서 안 것이다.

**재생되는 토글은 수렴해야 한다.** Bold는 설정이 아니라 토글이라, "굵게 해줘"를 두
번 실행하면 문서가 다시 보통 글씨가 된다. 결정론적 재생 엔진이 재실행마다 진동하면
어떤 의미로도 결정론이 아니다. 그래서 스텝은 **자기가 도달시키려는 상태**(`skip_if`)
를 선언하고, 이미 그 상태면 물러선다.

**앱은 활성 상태일 때만 자기 메뉴를 검증한다.** 배경에 있는 TextEdit은 멀쩡한
`Edit > Select All` 을 비활성으로 보고한다. 게다가 도우미는 백그라운드 프로세스라
`NSRunningApplication.activate` 가 무시될 수 있다. 양쪽 모두 **`AXFrontmost`** 로
앞으로 가져온다 — 이미 쥐고 있는 접근성 권한의 경로이고, 요청과 확인을 같은 API로
하므로 둘이 어긋날 수 없다. 요소 클릭은 이 비용을 치르지 않는다(활성화는 사용자의
포커스를 빼앗으므로 모든 동작에서 할 일이 아니다).

**창이 없는 앱에서 시작할 수 있어야 한다.** Finder는 평소 창이 없고, TextEdit은
아무것도 없이 실행하면 문서가 아니라 파일 선택창을 띄운다. 메뉴는 창이 아니라
**앱**에 속하므로, 첫 스텝이 `menu` 인 레시피는 `require_window=False` 관찰로 시작할
수 있다. 창 내용이 필요한 스텝은 여전히 자기 자리에서 정직하게 실패한다.

**한 앱의 여러 창은 같은 요소를 여러 번 노출한다.** TextEdit 문서 두 개는
`First Text View` 를 두 개 만든다. 거의 모든 요청은 **사람이 보고 있는 창**을
뜻하므로, 드라이버가 앞창을 `[focused]` 로 표시하고 셀렉터가
`in_focused_window` 로 범위를 좁힌다. 이게 없으면 문서를 하나 더 여는 순간 멀쩡하던
레시피가 `target_ambiguous` 가 된다.

**두 드라이버는 같은 트리를 내야 한다.** 포커스 표시와 창 없는 시작을 파이썬에만
넣었더니, 레시피가 in-process 에서는 되고 **권한 경계를 넘으면 실패**했다. 관찰
포맷은 프로토콜의 일부다.

#### 동의를 물을 수 있게 만들기

저장은 이 저장소에서 **처음으로 실행이 끝난 뒤에도 남는** 동작이고, 그래서 게이트가
실제로 걸리는 첫 작업이었다. 그런데 **아무도 그 질문에 답할 수 없었다**: CLI에는
확인 경로가 없어서 `write_persistent` 스텝은 게이트에 걸린 채 명령이 그대로 끝났다.
확인은 이제 **화면에 손대기 전에** 모은다. 중간에 묻는다는 건 이미 절반쯤 일어난 일에
동의를 구한다는 뜻이고, 먼저 묻는 쪽이 화면이 아직 그대로일 때 거절할 기회를 준다.
`--yes` 는 스크립트용이고, 물어볼 터미널이 없으면 답은 **아니오**다 — 침묵은 동의가
아니다.

**동의는 버튼이 아니라 대상에 묶인다.** `File > Save` 와 `File > Move to Trash` 는
같은 앱의 같은 `menu` 오퍼레이션이고, "A로 저장"에 대한 승인이 B로 저장하는 것을
허가해선 안 된다. 그래서 `ContextBinding` 은 고정한 신원을 **화면 어디서 다시
읽는지**(`resource_identity_from`)까지 말할 수 있다 — 버튼을 누르는 순간 이름 칸이
여전히 그 이름이어야 한다. 증인 없는 고정은 조용히 통과하지 않고 `unverifiable` 로
거부된다.

**필드를 나열해 재구성하던 두 곳이 새 필드를 계속 흘리고 있었다.** 셀렉터는 bind마다
`in_focused_window` 를, 바인딩은 위 증인을 잃었다. 둘 다 `replace` 로 바꿨다. 같은
부류의 잠복 버그가 하나 더 있었다: `params_digest` 는 기록 측이 `params` 만,
실행 측이 `params + name` 을 해시해서, 그 필드를 **처음 실제로 쓰는 레시피**가
원리적으로 실패하게 되어 있었다.

**실패는 앱 상태를 남긴다.** 중간에 멈춘 실행은 저장 시트를 열어둔 채 끝나고, 모달
시트는 그 앱의 메뉴 막대 전체를 잠근다. 임의로 닫아 되돌리지는 않는다 — 사용자가
시키지 않은 조작이다. 대신 다음 실행이 "메뉴 비활성" 이라는 막다른 말 대신
**"대화상자가 열려 있습니다"** 라고 말한다.

**값 없는 검증은 답이 아니다.** 어떤 속성이든 집어오는 폴백 때문에 "새 문서
만들어줘" 가 `결과는 First Text View 입니다` 라고 답했다. 답은 `Value` 에만 있고,
`Description`/`Identifier` 는 필드의 이름표지 내용이 아니다.

**검증:** 서명된 런처로 `28/28`. 저장 시나리오는 자기가 만든 파일을 지우고 창을
닫으므로 내일 다시 돌려도 쌓이지 않는다.

### 3.19 메일·캘린더까지 — 큰 앱과 목록이 드러낸 것들 (2026-09-16)

**Mail은 관찰이 끝나지 않는 앱이었다.** 트리를 40초 동안 걸어도 11,000개에서 끝이
안 났다. 이건 "느리다"가 아니라 **행(hang)** 이고, 하필 사람들이 가장 도움받고 싶은
앱이 답을 못 하는 앱이었다. 원인은 두 가지였고 둘 다 고쳤다.

- **속성 읽기를 한 번에.** `_elem_label` 이 속성 6개를 각각 읽고 있었다. 어떤 앱은
  이 값들을 요소마다 **지연 계산**한다 — Mail 메시지 행은 낱개로 20.2ms, 묶어서
  3.3ms다(**6.2배**). `AXUIElementCopyMultipleAttributeValues` 로 한 왕복에 읽는다.
- **컨테이너당 자식 상한.** 앱을 비싸게 만드는 건 거대한 **균질 목록** 하나다.
  메일함은 똑같이 생긴 행 수만 개를 갖고 있고, 그걸 다 걷는 건 아무도 요청하지 않은
  일이다. 자식을 60개로 끊으면 창의 **모양**(사이드바·툴바·첫 행들)이 남는다 —
  전역 노드 예산만 있으면 첫 목록에서 예산을 다 쓰고 나머지엔 도달하지 못한다.

결과: **65초 → 6.4초.** 잘린 곳은 트리에 그대로 적는다(`… (N개 더 있음)`,
`truncated: true`). 셀렉터가 못 찾은 것과 **애초에 안 본 것**은 다른 사실이고,
호출자가 그 둘을 구분할 수 있어야 한다.

**"Messages" 는 앱 이름이 아니었다.** `com.apple.messages.AssistantExtension` 도
localizedName이 "Messages"다 — 창도 메뉴도 없는 백그라운드 확장인데, 목록에서 먼저
나온 쪽이 이겼다. 이제 번들 ID는 정확한 답으로, **이름은 사람이 볼 수 있는 앱**
(activation policy 0)으로 해석한다. Notes도 같은 문제였다.

**좌표 클릭이 사실은 동작하지 않았다.** 마우스 이벤트를 pid로 보내면 앱은 어느 뷰가
눌렸는지 알 수 없다 — 저장 패널 사이드바의 행은 눌러도 선택되지 않았다. 마우스
이벤트는 **위치가 곧 지정**이므로 시스템 이벤트 탭으로 보낸다. 이것은 키 입력에서
pid 지정이 막아 주는 "엉뚱한 앱" 위험을 되살리지 않는다 — 키 입력엔 위치가 아예
없기 때문이다. 앱을 먼저 앞으로 가져오고, 포인터는 원래 자리로 되돌린다.
**AX 액션이 없는 컨트롤(목록 행, 캔버스 항목)이 이제 눌린다.**

#### 읽기는 쓰기와 다른 규칙이 필요했다

**목록은 복수다.** 폴더의 내용, 안 읽은 메일의 발신자, 표의 행 — 이것들은 본질적으로
여럿이고, "정확히 하나" 를 요구하는 셀렉터로는 기술할 수 없다. 그래서 `resolve_all`
을 뒀다. 다만 **행동에 필요한 것**(정확히 한 요소를 지목)을 **읽기에서는 범위**로
대신 요구한다: role + 컨테이너는 기술이지만("파일 목록 안의 모든 이름"), role만이면
"화면의 모든 TextField"가 된다. `matches()` 에 섞여 있던 이 규칙을 적용 지점으로
옮겼다 — 행동은 `resolve`, 읽기는 `resolve_all` 이 각자 요구한다.

**"비어 있음" 이 답일 때와 아닐 때가 다르다.** 오늘 일정이 없으면 그건 **한가한
날**이라는 답이다. 폴더 이름이 하나도 안 읽히면 그건 **못 본 것**이다 — Finder는
Downloads의 21개 항목 이름을 하나도 노출하지 않는다(Documents·Applications는 한다).
그래서 캡처는 `empty_ok`(비어 있음도 답)와 `optional`(못 읽으면 넘어감)을 따로 갖는다.
캘린더 쪽이 `empty_ok` 를 쓸 수 있는 근거는 **postcondition이 그 날짜 칸이 화면에
있음을 이미 증명**했다는 점이다.

**끝에서 읽어야 하는 경우.** Finder 컬럼 뷰는 현재 폴더 **옆에 상위 폴더들을 모두**
보여준다. 앞에서 15개를 읽으면 드라이브와 조상 폴더 이름이 나온다 — 그 폴더에 없는
이름이니 **틀린 답**이다. 오른쪽 끝 컬럼이 답이므로 뒤에서 읽는다: 절대 틀리지 않고,
때때로 일부일 뿐이다.

**한 스텝이 여러 값을 읽는다.** 개수와 이름은 다른 질문이지만 같은 화면에서 나온다.
하나가 없다고 다른 하나까지 잃을 이유가 없다.

#### 새 어휘

- **`read` 액션** — 아무것도 바꾸지 않고 본다. 이전엔 모든 스텝이 행동해야 했고,
  읽기는 언제나 다른 일의 부수효과로만 가능했다.
- **`Selector.value`** — 값으로만 이름표가 붙은 요소(저장 패널 사이드바, Finder
  사이드바, 캘린더 이름)는 읽을 수는 있어도 **지목할 수 없었다**.
- **`Selector.click_ancestor`** — 목록은 안쪽 텍스트로 행을 식별하지만 클릭에
  반응하는 건 행이다. **식별되는 요소와 동작하는 요소가 다르다.**
- **`title`/`desc`/`ancestor` 의 대안 목록** — `identifier` 가 이미 그랬듯이. 메뉴
  제목은 지역화될 뿐 아니라 상태에 따라 바뀌고, 캘린더의 요일 칸은 **시스템 언어**로
  적힌다.
- **`Postcondition.absent`** — 어떤 행동은 **사라짐**으로 검증된다. 대화상자가
  닫히면 화면에 새로 생기는 것이 없으므로, 존재만 주장할 수 있는 검증은 검사할 것이
  없었다.

#### 저장 위치와 막힌 대화상자

**파일은 이제 시킨 곳에 떨어진다.** 이전엔 앱이 마지막에 쓰던 곳이었고, 이 기기에선
그게 터미널의 지원 디렉터리였다. 레시피가 폴더를 **이름으로 정하고**, 패널 자신의
사이드바에서 고르고, 동의에 **목적지까지 고정**한 뒤, 창으로 확인한다. "Where:" 팝업이
아니라 사이드바를 쓰는 이유: 팝업은 메뉴이고 **메뉴는 활성 앱에서만 열린다** — 눌러도
성공을 반환하고 아무것도 보여주지 않는다. 사이드바는 평범한 목록이다.

패널이 제공하는 폴더는 **그 사람의 즐겨찾기**뿐이다. 없는 폴더를 요청하면 실패하는데,
이때 쓸모 있는 답은 실패했다는 사실이 아니라 **고를 수 있는 목록**이다. 그래서
`target_missing` 은 같은 role로 화면에 있는 것들을 함께 말한다.

**막힌 대화상자에서 나오는 길.** 중간에 멈춘 실행은 시트를 남기고, 모달 시트는 그 앱의
메뉴 막대 전체를 잠근다. 여전히 **저절로 닫지 않는다** — 대화상자를 취소하는 건
아무도 요청하지 않은 행동이고, 추측으로 닫는 건 작업을 버리는 방법이다. 대신 사람이
부르는 레시피(`"대화상자 닫아줘"`)로 두고, **Cancel만** 누르며 커밋 버튼은 건드리지
않는다. 대상은 앞에 있는 앱이다. 대화상자가 둘이면 모호하다고 말하고 멈춘다.

**검증:** 서명된 런처로 `39/39`. 스위트는 자기가 연 문서와 대화상자를 닫고 만든 파일을
지운다 — 매번 다른 기기를 남기는 스위트는 반복 가능하지 않고, 실제로 그 쌓인 창들이
뒤 시나리오를 모호하게 만들어 깨뜨리고 있었다.

### 3.20 SkyLight를 흉내 낼 수 있는가 — advisor 조사 (2026-09-17)

질문은 "사설 API를 우리가 모사하기 어려운가"였는데, **답은 모사의 난이도가 아니라
모사 대상의 정체**에 있었다. (출처: cxweb/gpt-6-pro, 근거 링크는 §부록 B)

**`focus-without-raise` 는 "사용자 포커스를 빼앗지 않음"이 아니다.** 공개된 yabai
구현(`src/window_manager.c`)은 `_SLPSSetFrontProcessWithOptions` 로 **전면 프로세스를
실제로 바꾸고** `SLPSPostEventRecordTo` 로 key window 를 바꾼다. 생략하는 것은
`AXRaise`(창 올리기)뿐이다. 그러므로 "그 기법을 쓰면 사용자는 계속 자기 앱에 타이핑하고
에이전트는 다른 앱을 조작한다"는 설명은 부정확하다. **창을 올리지 않는 것과 입력 대상을
보존하는 것은 다른 조건이다.**

**심볼은 실재하지만 계약이 아니다.** `NUIKit/CGSInternal` 과 yabai 의 `extern.h` 에서
`SLSGetConnectionIDForPSN`, `_SLPSGetFrontProcess`, `SLPSPostEventRecordTo`,
`CGSSetMouseFocusWindow` 등의 선언이 확인된다. macOS 11.3 SDK 의 `SkyLight.tbd` 에는
`_SLPSSetFrontProcessWithOptions` 와 `__SLPSSetFrontProcessWithOptions` 가 **둘 다**
있다 — 즉 C 이름이 다른 두 진입점이며, 하나의 시그니처를 다른 쪽에 적용해선 안 된다.
yabai 의 key-window 코드는 `0xf8` 크기 레코드와 `0x3c` 오프셋, 40ms 지연까지
하드코딩한다. `dlsym != NULL` 검사로는 안전성을 확인할 수 없다는 뜻이다.

**우리 코드가 틀리게 적어둔 것 두 가지가 교정됐다.**

- `CGEventPostToPid` 에 "hit-test가 없다"는 설명은 과장이었다. WindowServer 가
  `NSView` 까지 고르는 게 아니라, 창까지 도달하면 **앱이 자기 hit-test를 수행**한다.
  pid 전송이 재현하지 못하는 것은 그 앞 단계 — 창 연결·활성 상태와, 비활성 창의 첫
  클릭에 대한 대상 앱의 정책(`acceptsFirstMouse:`) — 이다. 실측된 현상(저장 패널
  사이드바 행이 선택되지 않음)은 그대로지만, 이유의 서술을 고쳤다.
- `kCGEventSourceStateHIDSystemState` 는 **라우팅 옵션이 아니라 상태 테이블 선택**이다.
  이걸 쓴다고 HID tap 에서 재주입되지 않는다. (`kCGEventSourceStatePrivate` 는 공개
  API이며 "사설 API"라는 뜻이 아니다.)

**그리고 실제 버그를 하나 잡았다.** Apple 의 `AXUIElement.h` 는 action 콜백이 modal
처리 중 타임아웃하면 `kAXErrorCannotComplete` 가 반환될 수 있고 **이것이 action 실패를
뜻하지 않는다**고 명시한다. 우리 `click` 은 `err != 0` 이면 좌표 클릭으로 재시도했다 —
전송·삭제 버튼에서 **두 번 실행**될 수 있는 경로였다. 이제 이 오류는 재시도하지 않고
`unverified` 로 보고하며, 성패는 postcondition 이 판정한다.

**사설 API의 대가는 통념과 다르다.** 사설 API 사용 자체가 코드서명이나 공증을 막지
않는다(공증은 App Review 가 아니다). 막히는 것은 **Mac App Store**(가이드라인 2.5.1,
공개 API 전용)이고, `dlopen/dlsym` 으로 우회한다고 공개 API가 되지 않는다. SIP 는 별개
조건이며, 접근성 권한이 있다고 모든 WindowServer 호출이 허용되지도 않는다 — 대부분의
창 속성 변경은 소유자에 제한된다. 이벤트 권한도 AX 신뢰와 별개로
`CGPreflightPostEventAccess` / `CGPreflightListenEventAccess` 로 각각 확인해야 한다.

**결론: 공개 API로 도달 가능한 선.**

| 목표 | 판정 |
|---|---|
| 백그라운드 AX 실행/값 설정 | **가능** (지원 요소에 한해) — action 의 포커스 부작용은 별도 검증 |
| 백그라운드 앱에 키 이벤트 | 앱에 따라 가능 — PID 지정과 앱 내부 입력 대상은 다른 문제 |
| 특정 백그라운드 창에 좌표 클릭 | **앱별 검증 대상**, 범용 보증 없음 |
| hover·drag·IME 까지 완전 재현 | 공개 API 만으로는 범용 보장 어려움 |
| 사용자와 에이전트 입력의 엄격한 분리 | 함수를 찾는 문제가 아니라 **별도 실행 환경**의 설계 문제 |

가장 실용적인 절충은 우리가 이미 하고 있는 것이다: **AX 우선**으로 요소를 지목해
조작하고 상태 재조회로 검증하며, 키보드·픽셀 입력은 검증된 앱에만 capability 단위로
허용한다. 그리고 advisor 의 마지막 경고를 그대로 받아들인다 — **몰래 activate 했다가
복원하는 방식은 무포커스 조작이 아니다.** 그 사이 사용자가 누른 키는 잘못 전달되고,
창 순서를 되돌려도 이미 실행된 명령은 되돌아오지 않는다. 그래서 우리 좌표 클릭은
포인터를 원위치시키되 **"백그라운드 조작"이라고 부르지 않는다.** 전경 조작이 필요한
작업은 사용자가 명시적으로 넘겨준 구간에서 한다.

### 3.21 학습 경로를 실제 모델로 검증하다 (2026-09-17)

재생 엔진은 오래 다듬었지만 **일반성을 담당하는 학습 경로는 한 번도 실행된 적이
없었다.** `--learn` 은 자기 플래그를 이미 제거한 리스트에서 찾고 있었다(295행에서
지우고 338행에서 검사) — 조건이 참이 될 수 없는 코드였고, 그래서 사용자가 본 모든
"그 작업은 아직 방법을 몰라요"는 무조건적이었다. 일상 요청 7개 중 6개가 거기 걸렸다.

**고치자 다음 층이 드러났다: 프롬프트가 몇 라운드 전 엔진을 설명하고 있었다.**
헬퍼에 없는 `press_key` 를 가르치고, 파서가 무시하는 postcondition 형태를 쓰면서,
`menu` 는 아예 몰랐다. 갓 학습한 레시피는 **저장·닫기·복사·종료** — 메뉴에만 있는
명령 — 을 표현할 수 없었다. 계산기를 닫는 건 엔진에겐 메뉴 한 줄인데 작성자에겐
불가능한 일이었다.

**그리고 구조적으로 막혀 있었다.** `author()` 는 모델에게 보여준 트리 하나에 대해
**모든** 셀렉터가 해석되기를 요구했다. 시트의 요소는 시트가 열려야 생기므로, 다이얼로그
안을 건드리는 레시피는 전부 거부됐다 — 저장·열기·인쇄·환경설정이 통째로. 이제
셀렉터가 해석되지 않는 것은 **아직 아무것도 실행하지 않았을 때만** 환각이다. 첫 행동
이후엔 화면이 옮겨간 것이고, 확인하지 못한 단계를 **이름으로 보고**한다.

#### 실제 모델을 붙이자 스텁으로는 안 나올 것들이 나왔다

`cxweb/gpt-6-pro` 에 실제 작업을 시켰다.

- **JSON 골격이 모든 스텝에 `selector` 를 보여줘서**, 모델이 `menu` 스텝에도 달았고
  그 안엔 `role` 뿐이었다. 골격을 두 가지 스텝 모양으로 나누고, 모르는 필드는 쓰지
  말라고 명시했다.
- **작성이 엔진의 검증 의무를 조용히 면제하고 있었다.** postcondition 없는 `menu`
  스텝에 컴파일 시점에 `unverified_ok` 를 붙였다 — 재생이 지키는 유일한 보장을
  작성 단계에서 무효화하는 짓이다. 이제 거부하고 무엇을 써야 하는지 말한다.
- **정적 트리만 본 모델은 전제 조건을 놓친다.** "기울임꼴로" 에 대해
  `Format > Font > Italic` 만 쓰고 선택 단계를 빠뜨려, 실행 시 메뉴가 비활성이었다.

그래서 **실패를 되먹이는 수리 루프**(`compile --verify [--verify-rounds N]`)를 넣었다.
동작마다 모델을 부르는 방식이 아니라 **화면 하나당 한 번**이다. 1단계 → 실행 실패 →
되먹임 → 3단계(텍스트 포커스 + 전체 선택 + 검증 조건)로 개선됐다.

#### 검증을 검증하다가 나온 가장 날카로운 것

**행동 전에 이미 참이던 postcondition 은 아무것도 증명하지 않는다.** 모델 레시피가 한
번 통과했는데, 반응하지 않는 컨트롤을 누르고 처음부터 켜져 있던 체크박스를 확인한
것이었다. 실행을 거부하지는 않는다 — 이미 맞는 값을 설정하는 건 정당하다 — 대신
로그가 `already_true` 로 구분한다. **토글이라면 `skip_if` 로 말해야 한다.**

**그리고 `_verify` 자체가 거짓말을 하고 있었다.** 동의가 필요한 단계가 있으면
"검증됨"을 돌려주고 멈췄다. 서식 변경에 `write_persistent` 를 과잉 선언한 레시피가
그대로 통과했고, **루프는 "그 컨트롤은 동작하지 않는다"고 말할 기회조차 얻지 못했다.**
이제 그것은 통과가 아니라 **되먹일 사실**이다: "이 단계가 정말 세션 밖에 남는 효과를
내는가? 열린 문서의 서식이라면 `write_ephemeral` 이다."

#### 남은 정직한 한계

라운드를 늘려도 이 작업은 아직 완성되지 않았다. 모델이 고르는 툴바 이탤릭 체크박스는
**AXPress 에 반응하지 않는다**(실측: 성공 반환, 값 0 유지) — 그래서 우리 레시피들은
메뉴를 쓴다. 이건 모델의 실수라기보다 **화면만 봐서는 알 수 없는 사실**이고, 알아내는
유일한 방법은 실행해 보는 것이다. 수리 루프가 존재하는 이유이자, 그 루프에 라운드가
더 필요한 이유다.

기타: `unknowns` 에 `"resource.scope"` 라 쓰면 `"resource"` 만 보던 정책을 통과했다 —
**반드시 닫혀야 할 곳의 fail-open** 이었다. 작성이 모델을 두 번 부르고 있었고(앱 고르기
+ 레시피), 첫 번째가 트리를 보기도 전에 타임아웃으로 죽었다 — 요청이 앱을 지목하면
결정론적으로 고른다. 제공자는 사용자가 이미 로그인해 쓰는 에이전트 CLI
(`claude → codex → opencode → cxweb`)이고, 타임아웃은 7분이다. 추론형 에이전트가
접근성 트리 전체를 읽고 레시피를 쓰는 데 실측 4~6분이 걸렸고, 짧은 타임아웃은 아무것도
아끼지 못한다 — 제공자는 계속 생성하고 답만 버려진다.


### 3.22 학습 경로를 더 돌려보다 — 수렴하지 않는 이유 (2026-09-17)

`--verify-rounds` 를 늘려 실제 모델로 다시 돌렸다. **모델은 수렴한다** — 체크박스가
아무것도 증명하지 못했다는 되먹임을 받자 메뉴 명령으로 갈아탔고, 그건 손으로 쓴
레시피가 쓰는 바로 그 방법이다. 그런데 매 라운드가 **새로운 종류의 실수**를 만들어
냈다: `write_persistent` 과잉 선언, 그 순간의 창 제목(`Untitled 3`)을 `Window >`
메뉴에 하드코딩. 그래서 라운드 수는 **끝나는 숫자가 아니라 조절 손잡이**다.

그 과정에서 루프가 고장난 레시피를 통과시키던 구멍 세 개를 막았다.

- **아무것도 증명하지 않은 성공.** 스텝의 postcondition 이 실행 전에 이미 참이면 그
  스텝은 시험된 적이 없다. 반응하지 않는 컨트롤을 누른 1단계 레시피가 그렇게
  통과했다 — 확인한 체크박스가 이전 실행부터 켜져 있었다. 루프의 존재 이유가 바로 이
  경우인데 루프가 그걸 안 보고 있었다.
- **`_verify` 가 막힌 레시피를 "검증됨"이라 했다.** 동의가 필요한 단계가 있으면 검사를
  멈추고 성공을 반환했고, 그래서 루프는 "그 컨트롤은 동작하지 않는다"고 말할 기회를
  얻지 못했다. 과잉 선언은 그 자체로 되먹일 사실이다.
- **모델이 아무도 승인할 수 없는 단계를 만들 수 있었다.** 동의는 기술된 행동에 묶이는데
  작성은 셀렉터가 있을 때만 binding 을 만들었다. 메뉴 스텝엔 셀렉터가 없으니, 모델이
  쓴 모든 되돌리기 어려운 명령이 영구히 막혀 있었다 — 게이트는 묻는데 답할 대상이
  없었다.

**제공자 층에서도 둘.** 수리 라운드 도중 모델 호출이 실패하면 **이미 성공한 1차 결과까지
버려졌다**; 이제 가진 것을 표시해서 넘긴다. 그리고 에이전트 CLI 는 **실패하면서도
성공을 보고한다** — opencode 는 401 을 찍고 exit 0, cxweb 은 웹 UI 가 바뀌자
"추론 수준 컨트롤을 찾지 못했습니다" 를 답으로 돌려줬다. JSON 을 요구한 질문에 JSON
없는 답은 답이 아니므로, 종료 코드와 무관하게 다음 제공자로 넘어간다.

**미완:** 이 기기의 제공자 넷이 모두 사용 불가여서(claude 미로그인, codex 크레딧 소진,
opencode 401, cxweb UI 변경) 3라운드 실행은 끝내지 못했다. 루프 자체는 위 수정들로
검증했고, 남은 것은 환경 문제다.


### 3.23 provider 는 세션을 물려받으면 안 된다 (2026-09-17)

`claude -p` 가 "Not logged in" 을 답해서 제공자가 없다고 보고했는데, **사용자는
로그인돼 있었다.** 원인은 우리 쪽이었다: `claude` shim 은 환경에
`CLAUDE_CONFIG_DIR` 같은 변수가 있으면 "호출자가 이미 백엔드를 골랐다"고 보고 계정
라우팅을 건너뛴다. 그런데 authoring 은 대개 **에이전트 세션 안에서** 실행되고, 그
세션이 자기 배관을 자식에게 물려준다. 그래서 자식이 부모 세션의 설정 디렉터리를 보고
스스로를 로그아웃 상태라고 보고했다. **제공자가 고장난 것처럼 보였지만 아니었다.**

이제 세션 범위 변수(`CLAUDECODE`, `CLAUDE_CODE_*`, `CLAUDE_CONFIG_DIR`)만 떼고
띄운다. 사용자가 일부러 내보낸 자격(`ANTHROPIC_API_KEY` 등)은 진짜 선택이므로 남긴다.

**cxweb 은 죽은 게 아니었다.** `추론 수준 컨트롤을 찾지 못했습니다` 는 오래 돌린 뒤
데몬의 낡은 탭에서 선택기를 못 찾은 일시적 상태였다 — 재시작하고 새 탭이 뜨자
레벨 3까지 정상 동작했다. 제공자 층이 이걸 "no answer" 로 처리하고 다음으로 넘어간
것은 옳았다.

#### 이탤릭 과제의 결말: 모델은 수렴했고, 과제가 불가능했다

claude 로 3라운드를 돌리자 **손으로 쓴 레시피와 같은 구조**가 나왔다 — 본문 포커스 →
`Edit > Select All` → `Format > Font > Italic`, 그리고 토글이라고 `skip_if` 까지
붙였다. 그런데 실행하면 실패했다.

원인을 좁혀 보니 우리 쪽이 아니었다. 같은 선택·같은 순서에서 **Bold 는 적용되고
Italic 만 적용되지 않는다.** 좌표 클릭도, 이벤트 소스도, 선택 상태도 아니었다 —
`AXSelectedText` 로 전체 선택이 살아 있음을 확인했고, 결정적으로 **⌘I 도 아무 일을
하지 않는 반면 ⌘B 는 동작한다.** 즉 이 문서에서 TextEdit 의 이탤릭 명령 자체가
적용되지 않는다(기본 서체에 이탤릭 페이스가 없는 것으로 보인다).

그래서 이 과제의 결론은 "수리 루프가 부족했다"가 아니다. **모델은 올바른 레시피를
만들었고, postcondition 이 적용되지 않은 효과를 성공이라 말하기를 거부했다.** 검증이
있어야 하는 이유가 바로 이것이다 — 없었다면 "다 됐어요"라고 답했을 것이다.

### 3.24 화면 단위 루프 — escalation (2026-09-17)

"모델이 라운드마다 새로운 실수를 만든다"의 근본 원인은 부주의가 아니라 **모델에게
볼 수 없는 사실을 확정하라고 요구한 것**이다. Flow IR 은 불변식의 집합이고(재생이
공짜인 이유), 불변식은 정의상 여러 실행에 걸쳐 참인 것인데 **모델은 한 순간의 화면만
본다.** 스냅샷 한 장에서 불변식을 연역할 방법은 없으므로 라운드를 늘려도 수렴이
보장되지 않는다.

**화면 단위 루프는 이미 우리 코드에 있었다 — 실패 경로가 그것이다.** 엔진은
`target_ambiguous` / `target_missing` / `action_failed` / `postcondition_timeout`
에서 **자기 예측이 깨진 지점을 정확히 알고**, `_nearby` 는 화면에 뭐가 있는지까지
말한다. 단지 거기서 멈출 뿐이었다. 그래서 그 지점들을 **질문**으로 바꿨다.

> 화면 단위 루프 = 매 화면마다 모델을 부르는 것이 아니라,
> **화면이 레시피와 어긋날 때** 부르는 것.

비용 단위가 *실행*이 아니라 *스텝*이고, 트리거가 **데이터 의존적**이다 — 같은
레시피가 화요일엔 공짜, 수요일엔 화면이 모호해서 1회 지불.

**계층.** L0 불변식(셀렉터+postcondition) · L1 분기(`skip_if`, 대안 목록) ·
L2 재관찰(`wait_until`) 까지는 토큰 0이고, **L3 판단**만 지불한다. 하나의 레시피가
이 층들을 섞는다.

**답은 산문이 아니라 IR 이다.** "두 번째 걸 눌러"는 도움이 안 되고, 셀렉터를 돌려주면
도움이 된다. `Resolution` 은 `use_selector` (레시피가 잘못 지목함) 또는
`insert_steps` (먼저 치워야 할 게 있음) 를 돌려주며, 둘 다 레시피가 이미 만들어져
있는 것과 같은 재료다 — 그래서 **레시피가 그것을 간직해 다음엔 공짜가 될 수 있다.**

**안전은 설계로 보장된다.** escalation 은 **어느 요소인지**를 고를 수 있을 뿐
**해도 되는지**는 정하지 못한다. 끼워 넣은 스텝은 중첩 `replay` 로 실행되므로 같은
계약·같은 게이트를 통과해야 하고, **그들에겐 아무것도 사전 승인돼 있지 않다** — 동의가
필요한 스텝은 그냥 게이트에 걸려 수정이 실패한다. 대화상자를 Cancel 로 치우는 것은
범위 안이고, 저장·전송·삭제는 범위 밖이다. 추가 코드가 필요 없었다.

**정책 셋, 엔진 하나.** `tacto replay` 는 escalation 금지(토큰 0 보장, 크론·스크립트용),
`tacto do --think [N]` 는 예산 내 허용, 학습은 별도다. 그리고 스텝이
`on_uncertain` 으로 **어떤 종류의 놀람을 예상하는지 이름을 대야** 한다 — 아무 데서나
escalate 하는 레시피는 정직하게 실패하는 레시피보다 나쁘다.

#### 실측

막힌 저장 대화상자를 남겨두고 `tacto do "…로 저장해줘" --think 1`:

```
저장 창을 엽니다
  ? 예상과 달라 판단이 필요합니다: 메뉴 'File > Save…' 가 지금은 비활성 …
이미 열려 있는 저장 창을 취소해 닫습니다      ← 모델이 끼워 넣음
이미 되어 있어서 건너뜁니다: 저장 위치 …      ← 다시 토큰 0
파일 이름을 '…' 으로 적습니다
저장합니다
[done]
```

**모델 호출 1회**, 정확히 예측이 깨진 지점에서.

그 과정에서 두 가지가 더 드러났다. 모델이 `AXButton` 이라 쓰는데 우리 트리는 접두사를
뗀 `Button` 을 쓴다 — 의미 없는 표기 차이가 답을 버리게 하고 있었으므로 셀렉터가 둘 다
받아들인다. 그리고 판단자에게 **진짜 bundle id** 를 주지 않으면 끼워 넣은 스텝이
자기 계약을 무효화한다(게이트가 옳게 동작했지만 엉뚱한 이유로).

**남은 것:** `learned` 기록은 로그에 남지만 아직 레시피에 **써 넣지 않는다.** 그게
붙어야 비용이 실제로 0에 수렴한다 — escalation 이 자기 자신을 없애는 구조.

---

### 3.25 advisor 채점 30/100 — 받은 지적과 고친 것 (2026-09-17)

escalation 을 붙인 상태로 advisor(gpt-6-pro)에게 채점을 요청했다. 결과는 **30/100**,
헤드라인은 이것이었다.

> 현재 첨부 코드로는 "escalation 은 어느 요소인지 고를 뿐, 해도 되는지는 정하지
> 못한다"는 주장을 보안 보장으로 받아들일 수 없습니다.

읽고 하나씩 재현해 봤고, **지적은 대체로 맞았다.** 우리가 "구조적으로 막혀 있다"고
써 둔 것 중 몇 개는 실제로는 막혀 있지 않았다.

#### P0 — 권한이 내려갈 수 있었다

`policy()` 가 효과별로 **early return** 하고 있었다. `effects` 를 순회하다 먼저
만난 효과의 판정을 그대로 돌려주니, **효과를 더 선언하면 권한이 낮아질 수 있었다.**
`["transmit"]` 은 confirm 인데 `["read", "transmit"]` 은 read 를 먼저 만나 none 이
되는 식이다. 모델이 과잉 선언을 하면 안전해지는 게 아니라 위험해지는 구조였다.

고친 형태는 단순하다. 효과 하나하나를 **독립적으로** 판정하고 그중 **가장 무거운 것**을
택한다.

```python
_SEVERITY = {"none": 0, "preapproved": 1, "confirm": 2, "handoff": 3, "blocked": 4}
decisions = [_for_effect(e, c, data) for e in sorted(eff)]
return _worst(decisions)
```

`scenario_authorization_never_falls` 가 이걸 성질로 검사한다 — 모든 효과 **쌍**에
대해, 둘을 합친 판정이 각각의 판정보다 결코 가볍지 않아야 한다.

#### P0 — 끼워 넣은 스텝은 자기 효과를 스스로 신고하고 있었다

"삽입된 스텝도 똑같이 게이트를 통과한다"는 우리 주장은 틀렸다. 게이트가 계산하는 것은
`policy(모델이 이 스텝의 의미라고 말한 것)` 이다. **Send 를 누르면서
`effects: ["read"]` 라고 쓴 스텝은 통과한다.** 엔진에는 그 주장을 컨트롤의 실제
동작과 대조할 방법이 없다.

그래서 자유 형식 `insert_steps` 를 없애고, 복구를 **이 저장소가 가진 이름들**로
바꿨다.

```python
RECOVERIES = {"dismiss_dialog": _recovery_dismiss_dialog}
```

모델은 **어느 복구가 필요한지**를 고른다. 그게 무엇을 하는지는 모델이 쓰지 않는다.

#### P1 — 실행 전 불확실성과 실행 후 불확실성은 다른 질문이다

가장 값진 지적. `postcondition_timeout` 은 **"아무 일도 없었다"는 뜻이 아니다.**
전송은 끝났는데 화면 갱신이 늦어 deadline 을 넘긴 경우가 정확히 이 모양이다. 그런데
우리 코드는 복구 후 **원래 액션을 무조건 다시 실행**했다. 두 번 보내는 경로가
열려 있었다.

```text
실행 전: 무엇을 실행해야 하는가?   → 대상을 바꿔도 된다
실행 후: 이미 어떤 효과가 발생했는가? → 대상을 바꾸는 건 수정이 아니라 두 번째 행동이다
```

`Uncertainty.effect` 가 이 둘을 가른다.

- `effect != "not_started"` 면 `use_selector` 는 **엔진이 거부한다.** 승인이 필요한
  스텝인지와 무관하다.
- 복구가 성공하면 **먼저 다시 본다.** 가리던 창이 치워지고 postcondition 이 참이면
  그건 "이미 되어 있었다"이지 "다시 해야 한다"가 아니다.
- 그래도 아니면, **두 번 해도 한 번과 같은 액션만** 다시 실행한다 (`read`,
  `set_value`). `click` · `menu` 는 멈추고 "실행은 됐지만 결과를 확인하지 못했다"고
  말한다.

driver 쪽도 같이 갈랐다. 디스패치 **이전**임이 증명되는 거절 — 비활성 메뉴, 앞으로
못 나온 앱, 범위 밖 인덱스 — 은 `NotStarted` 로 올라오고, 나머지 예외는 전부
`unknown` 이다. 예외는 자기가 어디서 났는지는 알려주지만 얼마나 진행됐는지는 알려주지
않기 때문이다.

#### P1 — 질문 횟수는 비용의 상한이 아니었다

예산은 모델 호출 **전에** 차감되고 있었으므로(타임아웃·깨진 JSON·`give_up` 이 모두
예산을 쓴다) 그 부분은 이미 옳았다. 빠져 있던 것은 **스텝당 상한**이다. 같은 스텝이
같은 오답을 계속 받으면 런 전체 예산을 혼자 태우고도 시작한 그 실패로 끝난다.
`_Budget.take(step, kind)` 가 (스텝, 놀람) 조합당 1회로 막는다.

#### P1 — 판단자에게 판단 재료가 안 가고 있었다

`_candidates()` 가 role 이 같은 것들의 표시 문자열만 최대 12개 뽑았다. 서로 다른 창의
Save 버튼 둘은 이렇게 보였다.

```python
["Save", "Save"]
```

구별할 수 없는 후보는 선택지가 아니다. 이제 각 후보가 **어느 창·어느 컨테이너**인지를
달고 나오고, 셀렉터가 `ancestor` 나 `in_focused_window` 로 범위를 좁혔다면 후보도
같은 범위로 좁힌다.

#### P2 — 셀렉터·검증기의 구체적 결함 (전부 재현됨)

| 위치 | 문제 | 고친 것 |
|---|---|---|
| `Selector._stable_id` | 모든 `;` 뒤를 상태로 보고 잘랐다. `control;operation:preview` 가 `control;operation:delete` 와 매칭된다 | 알려진 휘발성 키(`value:` 등)로 시작하는 접미사만 자른다. 모르는 접미사는 **이름의 일부** |
| `Selector.resolve` | `allow_nth` 가 `_climb()` 전에 반환해, `click_ancestor="Row"` 인데 leaf 를 돌려줬다 | nth 로 고른 뒤에도 같은 변환을 적용 |
| `Postcondition.check` | 모든 문자열 equals 에 숫자 동등성을 적용했다. 계좌번호 `00123` 이 `123` 으로 검증된다 | 자릿수 구분기호만 무시한다. `5,535` = `5535` 는 같은 글자, `00123` ≠ `123` |
| `model_judge` | try 가 모델 호출만 감쌌다. 뒤따르는 변환 예외가 replay 로 튀어 올라갔다 | 전체를 감싸고, 응답 타입을 검사하고, 문자열 길이를 자른다 |

#### 고치지 않고 **철회**한 것

write-back. 실행 중 알아낸 수정을 레시피에 써 넣으면 escalation 이 스스로를 없앤다 —
매력적이었고, 두 가지 이유로 틀렸다.

첫째, 손에 있는 flow 는 **바인딩된** 것이다. 저장하면 이번 실행의 파일명과 본문이
템플릿에 박힌다. 모델이 창 제목을 하드코딩하는 걸 막으려던 기능이 **실행 전체를**
하드코딩했을 것이다. 둘째, 지속성은 IR 의 모양이 아니라 **그 수정이 성립하는 조건**의
성질이다. 셀렉터가 오늘 날짜를 가리킬 수도 있고, "그 패널을 먼저 열어라"가 구조적일
수도 있다. 둘을 IR 종류로 가르는 규칙은 존재하지 않는다.

`ReplayResult.resolved` 로 **보고만** 한다. 무엇을 물어봐야 했는지는 사람이 알아야
할 뉴스지, 조용히 지식이 될 것이 아니다.

#### 아직 열려 있는 것 (advisor 지적 중 미해결)

정직하게 남긴다.

- **5.1 — 검증 명세 자체의 누락은 자동으로 발견되지 않는다.** 셀렉터가 유일하게 맞고
  binding 이 통과하고 postcondition 도 참인데 사용자의 실제 목표와 다른 일을 했다면,
  `--think` 를 아무리 키워도 질문은 **0회**다. `on_uncertain` 은 이미 표현한 검증
  실패를 복구할 뿐, 빠뜨린 검증을 찾아내지 못한다. 이건 트리거 구조의 근본 한계이고
  설계로 풀어야 한다.
- **4.3 — `_run_fix()` 가 결과를 `bool` 로 줄인다.** 복구의 첫 스텝이 창을 바꾸고
  둘째가 게이트에 걸리면 "복구 실패"지만 화면은 이미 달라져 있다. 실패를 무효과로
  해석해서는 안 된다.
- **비교 실험의 축.** Codex 와 비교할 것은 호출 수만이 아니라 성공당 비용, **잘못된
  성공 판정**, 안전한 중단, 중복 효과, 사람이 복구한 비용이다.


### 2026-09-20 실험적 L2 창 준비 계약

Native direct/signed helper의 `action.prepare_window` capability는 관측된 표준 창에 대해 `{"name": "prepare_window", "element_index": 0}` 형태의 act action을 받는다. general planner에는 capability를 가진 관측 Window 후보로 노출한다. flow authoring/replay의 준비 검증 계약은 미완료이며, 해당 경로의 자동 승격을 보류한다. 창 identity와 준비 상태를 확인한 뒤 PID·창에 지정한 제목 표시줄 입력을 전송한다. `click_deadline_uptime`은 click과 prepare_window에만 허용되는 같은 Mac 단조 시계의 입력 시작 기한이다.

반환값 `acted + input_route: window_prepare + unverified: observe_effect + reobserve_required: true`는 메뉴 사용 가능이나 작업 완료를 보증하지 않는다. 새 관측과 대상 메뉴의 활성 상태를 확인한 뒤 후속 명령을 판단해야 한다. 후속 signed helper는 `menu_state` 읽기 API를 지원한다. `{type: "menu_state", target: 앱, path: 메뉴경로}`로 실행 중인 앱의 활성 여부·체크 표시·단축키를 조회한다. 메뉴 준비와 general 자동 연결은 별도이며 아직 미완료다. 실제 시험 범위와 모달·중단·입력 감사 한계는 [창 입력 계약](docs/native-window-input.md)에 기록한다.


### 읽기 전용 메뉴 목록

`menu_items` capability는 `{type:"menu_items",target:앱,path:[]}` 요청을 지원한다. 루트/하위 메뉴 직계 목록을 반환하며 조회 자체는 입력을 발생시키지 않는다. 미관측 enabled/has_submenu는 null이며 실행 가능을 뜻하지 않는다. 중복/부분 읽기/대상 변경은 오류다. 정확한 응답과 제한은 [메뉴 발견 계약](docs/menu-discovery.md)을 따른다.


### prepare_window flow 재생

빈 params와 관측된 Window selector를 사용한다. action.prepare_window capability, write_ephemeral 계약, field/equals/attribute의 명시적 postcondition이 필수다. unverified_ok는 허용하지 않는다. 정확한 window_prepare + observe_effect + reobserve_required 응답일 때만 결과를 재관찰한 뒤 진행하며 알 수 없는 응답이나 결과 조건 미충족은 후속 단계를 실행하지 않는다. 준비 입력 자체의 확인 이벤트는 unknown으로 유지하고, 이미 만족한 조건은 already_true로 기록한다.


### 3.26 실행 기록에서 레시피로 — 판단은 필요한 곳에만 (2026-09-23 ~ 09-25)

목표: 에이전트가 처음 하는 일을 직접 조작하고, 그 실행을 매개변수가 있는 레시피로 만들어
모델 없이 재실행하며, 판단이 필요한 곳에서만 에이전트가 개입한다. 사용법과 규칙 전체는
[`docs/recipes.md`](docs/recipes.md), API는 [`docs/api.md`](docs/api.md).

#### 원칙: 모델은 제안하고, 기록이 결정한다

승격 때 모델은 판단 지점을 **제안**만 한다. 채택 여부는 실행 기록이 정한다 — 근거가 그 화면에
보였는가, 선택지가 그 화면에 있었는가, 요청이 이미 정한 값은 아닌가, 되돌릴 수 없는 단계는 아닌가,
기록 당시의 답으로 원래 실행을 재현하는가. 분기 판단은 여기에 하나를 더한다: 재생 때 쓸 판단
모델에게 **기록 당시 화면 값**을 보여주고 다시 물어 같은 답이 나와야 한다. 다르면 그 분기는 다른
경우에 재생될 것이다.

#### 판단의 세 종류와 각각의 안전장치

| 종류 | 답 | 안전장치 |
|---|---|---|
| 고르기 | 선택지 중 하나 | 선택지 밖은 거부 |
| 뽑기 (`extract_from`) | 읽은 글의 일부 | 읽은 글에 글자 그대로 있어야 함 |
| 쓰기 (`compose`) | 자유로운 글 | 입력값으로만 쓸 수 있음 · 저장·전송 전 전문을 보여주고 승인 |

판단 값이 승인 필요 단계로 가면 **값이 정해진 그 시점에** 실제 값을 보여주고 묻는다. 실행 전
승인은 아무도 모르는 값에 대한 승인이라 의미가 없다.

#### 분기는 자란다

한 번의 실행은 한 경로만 기록한다. 분기 판단이 처음 보는 답을 고르면 그 지점부터 에이전트가
이어서 조작하고, 검증되면 그 답의 분기로 그 깊이에 저장한다. 새 분기도 판단 지점 검토를 거친다.
실측: "짝수면 ×2, 홀수면 +1, 그다음 20보다 크면 −5, 아니면 +5" — 7로 처음 실행한 뒤 25와 12에서
각각 안쪽·바깥쪽 분기를 배웠고, 이후 4와 27은 판단 호출만으로 재생했다.

#### 이 과정에서 드러나 고친 것

- 한 버튼만 바꾸는 판단으로 "짝수면 ×2, 홀수면 +1" 을 표현하면 짝수에서 ×1 을 실행한다. 원래 실행
  재현 검사는 기록 당시 답만 쓰므로 이를 잡지 못한다 — 그래서 "뒤 전체가 달라지는 판단"을 따로 둔다.
- 서명된 런처 안에서 예상 못 한 상황의 판단 모델이 매번 `FileNotFoundError` 로 실패하고 있었다
  (런처 안에서는 모델 CLI가 PATH 에 없다). 배포 경로에서 한 번도 동작한 적이 없던 기능이다.
- `Flow.bind` 가 분기 안의 입력을 채우지 않았다. `5를 빼고` 의 `빼고` 를 제외 표현으로 읽었다.
  요청 읽기의 요약된 목표가 규칙을 빠뜨려 에이전트가 할 일을 몰랐다.

#### 남은 약점

판단의 근거가 화면에 나타난 적 없는 중간값일 수 있다(에이전트가 여러 단계를 한 번에 누른 경우).
판단 모델이 앞서 읽은 값으로 계산해 맞힌 사례는 있지만, 보이는 값을 근거로 하는 판단보다 덜 확실하다.
