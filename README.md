<div align="center">

# 🗣️ &nbsp;NLC

### Natural-Language Computer control

**컴퓨터가 불편한 사람도, 말 한마디로 컴퓨터를 쓰게.**
_Talk to your computer. It clicks so you don't have to._

<br>

![status](https://img.shields.io/badge/status-working_prototype-2ea44f?style=flat-square)
![platform](https://img.shields.io/badge/platform-macOS_·_Web-1a2330?style=flat-square)
![python](https://img.shields.io/badge/python-3.11+-3b5fa0?style=flat-square)
![eval](https://img.shields.io/badge/eval-6%2F6_passing-2ea44f?style=flat-square)
![tokens](https://img.shields.io/badge/replay-0_model_tokens-0e7a6b?style=flat-square)
![license](https://img.shields.io/badge/license-proprietary-b3403a?style=flat-square)

</div>

---

```
$ nlc do "계산기로 12 곱하기 34"

  🗣  이런 순서로 할게요: 초기화 → 1 → 2 → 곱하기 → 3 → 4 → 계산
  🔊  계산 결과 408을 확인했습니다.
  [done] 다 됐어요.
```

> 모델은 **한 번만** 흐름을 배웁니다. 이후 같은 일은 **모델 토큰 0**으로 재생됩니다.
> 라이브러리가 자랄수록 태스크당 비용은 0에 수렴합니다.

<br>

## 왜 다른가

대부분의 computer-use 에이전트는 **매 클릭마다 모델을 호출**합니다 — 느리고, 비싸고,
같은 일을 할 때마다 결과가 흔들립니다. NLC는 반대로 갑니다.

<table>
<tr>
<td width="50%" valign="top">

**보통의 CU 에이전트**
```
관찰 → 모델 → 클릭 → 관찰 → 모델 → 클릭 …
       ↑ 매 스텝 토큰 소비 · 비결정론
```

</td>
<td width="50%" valign="top">

**NLC**
```
[학습 1회]  모델이 흐름을 레시피로 저장
[재생 N회]  셀렉터 재해석 + 검증 (토큰 0)
       ↑ 결정론 · 감사가능 · 안전 게이트
```

</td>
</tr>
</table>

<br>

## 핵심 설계

| | |
|---|---|
| 🌳 **트리 우선 관찰** | 스크린샷 픽셀이 아니라 **접근성 트리(AX tree)** 를 읽는다. diff로 토큰 절약, 잠금 상태도 우아하게 실패 |
| 🔖 **안정 셀렉터** | 매번 바뀌는 인덱스가 아니라 `role + identifier + ancestor`로 저장 — 재생 시 재해석 |
| ✅ **매칭 1개 강제** | 0개면 `target_missing`, 2개 이상이면 `target_ambiguous`. **조용한 오클릭 없음** |
| 🛡️ **4단계 안전 게이트** | 읽기 / 사전승인 / 확인 / 핸드오프. 위험 액션은 승인 없이 실행 안 됨 |
| 🔁 **행동 후 검증** | "클릭했다"가 아니라 "결과가 화면에 보인다"로 완료 판정 (monotonic 폴링) |
| 🗣️ **초보자 대화 레이어** | 실시간 내레이션, 일상어 확인, 자연스러운 핸드오프 |

<br>

## 구조

```
 대화/음성  ─────────────────────────────  L4  음성·내레이션·확인 대화
 자연어 봇  ─────────────────────────────  L3  인텐트→플랜→복구
 액션 프로토콜  ─────────────────────────  L2  observe · act · confirm · handoff
 드라이버  ───────────────┬──────────────  L1  백엔드
                          │
            ┌─────────────┴─────────────┐
       macOS AX                     Browser
   AXUIElement + CGEvent      Playwright + CDP a11y tree
```

| 모듈 | 역할 |
|---|---|
| `nlc/driver.py` | macOS AX 트리 읽기 + CGEvent 입력, L2 프로토콜, 조건 폴링 |
| `nlc/flow.py` | 결정론 재생 — 셀렉터 카디널리티, precondition, 구조화 postcondition |
| `nlc/safety.py` | 4단계 확인 정책 분류기 + escalation-only merge |
| `nlc/browser.py` | 웹 백엔드(Playwright + CDP), 동일 L2/Flow 어휘 |
| `nlc/voice.py` | 음성 프론트(macOS TTS + 플러그형 STT, 음성 확인 게이트) |
| `nlc/agent.py` | L3/L4 — 인텐트→플로우→내레이션→복구/게이트 |
| `nlc/authoring.py` | 신규 인텐트를 모델로 학습→Flow 저장 (유일한 토큰 소비 경로) |
| `nlc/store.py` | 플로우 레시피 라이브러리 (학습1회·재생다회) |
| `nlc/session.py` | 잠금 상태·capability·keep-awake |
| `nlc/onboard.py` | 권한 온보딩 마법사 |
| `nlc/parallel.py` | 병렬 조작 (AX 액션 병렬 + CGEvent 직렬화) |
| `nlc/cli.py` | `doctor` · `list` · `do` · `replay` |

<br>

## 설치

> **요구사항**: macOS(네이티브 조작용) · Python 3.11+ · 손쉬운 사용 권한.
> 웹 백엔드는 Chromium이 필요합니다.

```bash
# 1. 클론
git clone git@github.com:Open330/nlc.git && cd nlc

# 2. 가상환경 + 설치 (콘솔 명령 `nlc` 등록됨)
python3 -m venv .venv
source .venv/bin/activate
pip install -e .                    # requirements는 pyproject가 관리
python -m playwright install chromium   # 웹 백엔드용 (선택)
```

<details>
<summary>가상환경 없이 / requirements.txt로 설치</summary>

```bash
pip install -r requirements.txt
python -m playwright install chromium
# 이 경우 `nlc` 대신 `python -m nlc.cli` 로 실행
```
</details>

## 사용

```bash
# 1. 권한 점검 — 손쉬운 사용(필수)·화면기록(선택)을 확인하고 설정창으로 안내
nlc doctor

# 2. 말로 시키기
nlc do "계산기로 12 곱하기 34"

# 3. 저장된 플로우 재생 (모델 토큰 0)
nlc list
nlc replay calc_12x34

# 4. 음성 세션 (말로 대화)
python -m nlc.voice
```

> 처음 실행 시 macOS가 **손쉬운 사용(Accessibility)** 권한을 요청합니다.
> `nlc doctor`가 정확한 설정 창을 열어주며, 권한을 켠 뒤 한 번 다시 실행하면 됩니다.

<br>

## 검증 상태 — `eval 6/6 PASS`

```
  PASS  task_success        계산기 12×34 → 408 결정론 재생 (토큰 0)
  PASS  determinism         재생은 모델을 호출하지 않음
  PASS  safety_gate         위험 액션 승인 없이 차단 · 승인 시 진행
  PASS  resolver_honesty    ambiguous/missing은 조용히 넘어가지 않고 예외
  PASS  safety_classifier   4단계 분류 정확
  PASS  browser_backend     동일 재생 엔진으로 웹 폼 조작
```

```bash
.venv/bin/python examples/eval.py     # 재현
```

실사용 시나리오 30개 카탈로그: [`examples/scenarios.md`](examples/scenarios.md)

<br>

## 예시: 결정론 레시피

```jsonc
{
  "name": "calc_12x34",
  "target": "com.apple.calculator",
  "steps": [
    { "action": "click",
      "selector": { "role": "Button", "identifier": ["AllClear", "Clear"],
                    "ancestor": "CalculatorKeypadView" },   // 매칭 1개 강제
      "intent_text": "초기화" },
    // … 1 · 2 · × · 3 · 4 …
    { "action": "click",
      "selector": { "role": "Button", "identifier": "Equals",
                    "ancestor": "CalculatorKeypadView" },
      "intent_text": "계산",
      "postcondition": { "field_identifier": "StandardInputView",
                         "equals": "408" } }               // 결과 검증 (D7)
  ]
}
```

<br>

## 알려진 한계 (정직하게)

- **잠금화면 조작은 불가.** 일반 접근성·화면기록 권한으로 잠긴 GUI는 조작할 수
  없습니다. ChatGPT "Locked use"는 Apple authorization plug-in을 설치하는 **별도
  제품**이지 권한 플래그가 아닙니다. NLC는 `caffeinate`로 idle 잠금을 방지하고,
  실제 잠금 시엔 핸드오프합니다. → [`PROTOCOL.md` §5.1](PROTOCOL.md)
- **신규 플로우 authoring만 모델 토큰을 씁니다** — 유일한 비결정론 경로.
- **native와 web은 별도 ID 공간** — Flow는 백엔드 간 이식되지 않습니다.
- 음성 STT는 플러그형(기본은 타이핑 폴백); 실 엔진 연결은 배포 시.

<br>

## 문서

- 📄 [`PROTOCOL.md`](PROTOCOL.md) — 방법론·설계 결정(D1–D8)·L1–L4 스펙·안전 정책 전문
- 📋 [`examples/scenarios.md`](examples/scenarios.md) — 30개 실사용 태스크 카탈로그

<br>

---

<div align="center">
<sub>Codex computer use 실측 리버스 엔지니어링 + 업계 조사로 설계 · macOS AX / Playwright CDP 위에 구현</sub>
</div>
