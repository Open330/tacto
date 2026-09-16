<div align="center">

# 🗣️ &nbsp;NLC

### Natural-Language Computer control

**컴퓨터가 불편한 사람도, 말 한마디로 컴퓨터를 쓰게.**
_Talk to your computer. It clicks so you don't have to._

<br>

![status](https://img.shields.io/badge/status-working_prototype-2ea44f?style=flat-square)
![platform](https://img.shields.io/badge/platform-macOS_·_Web-1a2330?style=flat-square)
![python](https://img.shields.io/badge/python-3.11+-3b5fa0?style=flat-square)
![uv](https://img.shields.io/badge/managed_by-uv-de5fe9?style=flat-square)
![eval](https://img.shields.io/badge/eval-29%2F29_passing-2ea44f?style=flat-square)
![tokens](https://img.shields.io/badge/replay-0_model_tokens-0e7a6b?style=flat-square)
![license](https://img.shields.io/badge/license-proprietary-b3403a?style=flat-square)

</div>

---

```
$ nlc do "7 더하기 8 해줘"
  🗣  계산기를 지웁니다 → 7 → + → 8 → 계산합니다
  [done] 다 됐어요. 결과는 15 입니다.

$ nlc do "다운로드 폴더에 뭐 있어?"
  [done] 확인했어요. 21 items, 350.95 GB available

$ nlc do "“보고서.rtf” 로 저장해줘"
  ⚠  문서을(를) 저장합니다
     진행할까요? [y/N]
```

> 모델은 **한 번만** 흐름을 배웁니다. 이미 저장된 흐름으로 정확히 매핑되면, 그
> **재생 경로는 모델을 호출하지 않습니다**(token 0). 새 요청을 기존 흐름에
> 매핑하지 못하면 다시 학습(authoring) 경로로 들어가 토큰을 씁니다 — 즉 "학습된
> 작업의 replay는 model-free"가 정확한 범위입니다.

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
            ┌─────────────┴───────────────┐
       macOS AX                      Browser (선택)
   AXUIElement + CGEvent       Playwright + CDP a11y tree
   ↑ 브라우저 포함 모든 앱       ↑ 헤드리스/프로그래매틱 웹 전용
```

> 컴퓨터 사용에서 **브라우저는 별도 백엔드가 아니라 macOS AX로 조작되는 앱**입니다.
> 오른쪽 Browser 백엔드는 헤드리스·자동화 용도의 선택적 대안일 뿐입니다.

| 모듈 | 역할 |
|---|---|
| `nlc/driver.py` | macOS AX 트리 읽기 + CGEvent 입력, L2 프로토콜, 조건 폴링 |
| `nlc/flow.py` | 결정론 재생 — 셀렉터 카디널리티, precondition, 구조화 postcondition |
| `nlc/safety.py` | 4단계 확인 정책 분류기 + escalation-only merge |
| `nlc/browser.py` | **(선택)** 헤드리스 웹 백엔드(Playwright + CDP), 동일 L2/Flow 어휘 |
| `nlc/voice.py` | 음성 프론트(macOS TTS + 플러그형 STT, 음성 확인 게이트) |
| `nlc/agent.py` | L3/L4 — 인텐트→플로우→내레이션→복구/게이트 |
| `nlc/authoring.py` | 신규 인텐트를 모델로 학습→Flow 저장 (유일한 토큰 소비 경로) |
| `nlc/store.py` | 플로우 레시피 라이브러리 (학습1회·재생다회) |
| `nlc/session.py` | 잠금 상태·capability·keep-awake |
| `nlc/onboard.py` | 권한 온보딩 마법사 |
| `nlc/parallel.py` | 병렬 조작 (AX 액션 병렬 + CGEvent 직렬화) |
| `launcher/nlc.swift` | **서명된 진입점** — 도우미가 받아들이는 유일한 클라이언트 |
| `helper/NLCHelper.swift` | **권한을 보유하는 서명된 도우미** — 표시·Esc 중단을 직접 소유 (`--helper`) |
| `nlc/reparam.py` | 구체 IR → 템플릿 재파라미터화 (모델 불필요) |
| `nlc/cli.py` | `doctor` · `list` · `do` · `replay` |

<br>

## 설치 &nbsp;<sub>with [uv](https://docs.astral.sh/uv/)</sub>

> **요구사항**: macOS(네이티브 조작용) · Python 3.11+ · 손쉬운 사용 권한.
> 웹 백엔드는 Chromium이 필요합니다.

```bash
# 1. 클론
git clone git@github.com:Open330/nlc.git && cd nlc

# 2. 설치 — uv가 venv 생성·의존성 해석을 한 번에
uv sync           # 코어: pyobjc(AX)만. 브라우저 automation 스택 없음

# 3. 권한 경계 빌드 (서명된 런처 + 도우미 번들)
./build.sh
./launcher/nlc doctor       # 'NLC Helper' 에 손쉬운 사용 권한을 부여
```

> **권한은 파이썬이 아니라 도우미에 줍니다.** venv의 `python`은 대개 공유
> 인터프리터로의 심볼릭 링크라, 거기에 권한을 주면 **그 인터프리터로 실행되는 모든
> 스크립트**가 앱을 조작할 수 있게 됩니다(실측 확인). `./build.sh` 로 만든
> **NLC Helper.app** 은 서명으로 식별되므로 권한이 그 하나에만 붙고 재빌드에도
> 유지되며, 시스템 설정 목록에 `python3.11` 대신 "NLC Helper" 로 표시됩니다.
> 도우미 경유로 실행하려면 `--helper` 를 붙입니다.

> **브라우저는 별도 스택이 필요 없습니다.** 컴퓨터 사용에서 브라우저는 다른 앱과
> 똑같이 접근성(AX) 레이어로 조작됩니다 — 코어 설치에 Playwright도 Chromium도
> 들어가지 않습니다.
>
> `nlc/browser.py`는 **선택적** 헤드리스/프로그래매틱 웹 백엔드일 뿐이며, 필요할
> 때만 추가합니다:
> ```bash
> uv sync --extra web                  # Playwright 추가
> uv run playwright install chromium
> ```

uv가 없다면: `curl -LsSf https://astral.sh/uv/install.sh | sh`

<details>
<summary>uv 없이 pip로 설치</summary>

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
python -m playwright install chromium
```
</details>

## 사용 — 세 단계로 나뉩니다

```
① 자연어 → IR        ② IR 실행           ③ 결과
   nlc compile          nlc run             (실행이 값을 검증해 함께 보고)
   (모델 사용)          (모델 토큰 0)
        └──────────── nlc do  (①+②+③ 한 번에) ───────────┘
```

```bash
uv run nlc doctor                       # 권한·화면세션 점검 (맨 처음 한 번)

# ① 배우기만 — 화면은 건드리지 않음 (모델 토큰 사용)
uv run nlc compile "계산기로 9 빼기 4" -o flows/sub.json

# ②+③ 실행 — 모델 호출 없음
uv run nlc run flows/sub.json           # 파일에서 바로
uv run nlc replay calc_multiply         # 저장된 이름으로

# ①+②+③ 한 번에 — 모르는 작업을 배우려면 --learn
uv run nlc do "계산기로 12 곱하기 34"
uv run nlc do "메일 정리해줘" --learn

# 진단
uv run nlc apps                         # 실행 중 앱 + GUI 세션 확인
uv run nlc observe com.apple.calculator # 접근성 트리 덤프
uv run nlc list                         # 저장된 IR 목록 + 위치
uv run nlc show calc_multiply           # IR 전문 보기

# 기존 IR을 템플릿으로 승격 (모델 불필요)
uv run nlc parameterize flows/calc_12x34.json -p 12=a -p 34=b -p 408=result \
       --digits --derived='result={{a}} * {{b}}'

# 권한 경계를 거쳐 실행 (launcher/build.sh, helper/build.sh 로 빌드)
./launcher/nlc replay calc_multiply -p a=3 -p b=4 --helper
./launcher/nlc eval                     # 도우미 시나리오 포함 전체 평가
uv run python -m nlc.voice              # 음성 세션
uv run python examples/eval.py          # 평가 재현
```

> 실행 중에는 조작 대상 창에 **"NLC가 이 창을 조작 중입니다 · Esc로 중단"** 테두리가
> 표시됩니다(`--no-indicator`로 끌 수 있음). 합성 입력에는 `NLC1` 마커가 붙어 사람
> 입력과 구분됩니다. 처음 실행 시 macOS가 **손쉬운 사용** 권한을 요청합니다 —
> `nlc doctor`가 정확한 설정 창을 열어줍니다.

<br>

## 파라미터화 — 하나의 IR, 여러 입력

계산기의 숫자는 절차가 아니라 **입력**입니다. 숫자마다 레시피를 만들지 않습니다:

```bash
uv run nlc replay calc_multiply -p a=12 -p b=34   # → 408
uv run nlc replay calc_multiply -p a=6  -p b=7    # → 42
uv run nlc replay calc_multiply                   # 기본값 → 408
```

| IR 필드 | 역할 |
|---|---|
| `params` | 입력과 기본값 — `{"a": "12", "b": "34"}` |
| `tables` | 앱별 매핑 — `{"digit": {"1": "One", …}}` (숫자 1 → 버튼 `One`) |
| `derived` | 파라미터로부터 계산 — `{"result": "{{a}} * {{b}}"}` (기대값 하드코딩 불필요) |
| `for_each` | 값의 각 글자마다 스텝 반복, `{{item}}` 바인딩 — "12의 각 자리 누르기" |

템플릿 `{{a}}` · `{{item}}` · `{{digit[item]}}` 을 셀렉터·파라미터·검증조건 어디서나 쓸 수 있습니다.
`derived`는 숫자와 `+ - * / // % **`만 허용하는 AST 평가라 `eval`을 쓰지 않습니다.

<br>

## 재현 — 두 개의 IR

| IR | 무엇 | 언제 |
|---|---|---|
| **Flow** (`flows/*.json`) | 무엇을 할지 | 배울 때 저장 |
| **Trace** (`--trace`) | 실행 중 무엇이 보였는지 | `--trace run.json` 줄 때 |

```bash
uv run nlc do "계산기로 12 곱하기 34" --trace run.json
```

Trace 하나면 **앱·GUI 세션·권한 없이** 그 실행을 다른 컴퓨터에서 그대로 재현할 수 있습니다.
문제가 생기면 이 파일을 보내주세요 — 이 프로젝트의 가장 어려운 버그가 이것으로 잡혔습니다.

<br>

## 지금 실제로 되는 일

아래 레시피는 **이 기기에서 실행해 확인한 것**들입니다 — 그럴듯하게 적어둔 게
아니라 [`nlc/library.py`](nlc/library.py) 에서 돌려본 결과입니다.

| 말하면 | 하는 일 | 새로 확인된 형태 |
|---|---|---|
| `7 더하기 8 해줘` | 계산기로 계산하고 **답을 읽어 알려줌** | 사칙연산 전체가 하나의 IR |
| `다운로드 폴더 열어줘` | Finder로 폴더 열기 | **창이 하나도 없는 앱**에서 시작 |
| `다운로드 폴더에 뭐 있어?` | 폴더를 열고 **읽어서 알려줌** | 조작이 아니라 **대답** |
| `"회의 준비" 라고 써줘` | 문서에 텍스트 입력 | 포커스 없이 쓰는 AX 쓰기 |
| `굵게 해줘` | 전체 선택 후 굵게 | **토글이 재생마다 뒤집히지 않음** |
| `새 문서 만들어줘` | 빈 문서 열기 | 메뉴 전용 명령 |
| `"보고서.rtf" 로 저장해줘` | 이름을 정해 저장 | **되돌리기 어려운 동작 + 동의** |

마지막 줄이 나머지와 다릅니다. 저장은 `write_persistent` 라서 정책이 스스로
실행하지 않고 먼저 묻습니다. 그리고 동의는 **어떤 파일인지**에 묶입니다 — "A로
저장"에 동의한 승인은 이름 칸이 B로 바뀌면 거부됩니다.

```bash
./launcher/nlc do "\"보고서.rtf\" 로 저장해줘" --helper
#   ⚠  문서을(를) 저장합니다
#      진행할까요? [y/N]
```

<br>

## 검증 상태 — `29/29 PASS`

```bash
./launcher/nlc eval      # 권한 경계까지 포함해 전부 (29/29)
uv run python examples/eval.py   # 헬퍼 없이 (경계 시나리오는 SKIP)
```

계산기 밖으로 나오면서 드러난 것들이 그대로 시나리오가 되어 있습니다.

```
  PASS  menu_command            메뉴에만 있는 명령 (저장·복사·폴더 열기)
  PASS  menu_verification       "성공" 을 반환하고 아무 일도 안 할 수 있음
  PASS  toggle_converges        재생해도 토글이 진동하지 않음
  PASS  menu_path_pinned        메뉴 경로를 바꾸면 기존 동의가 무효
  PASS  consent_required        저장은 스스로의 권한으로 실행되지 않음
  PASS  save_file               메뉴 → 시트 → 입력칸 → 되돌리기 어려운 클릭
  PASS  consent_is_about_the_file   A를 승인했는데 B를 적으면 거부
  PASS  helper_menu             메뉴 명령이 권한 경계를 넘어감
  PASS  answers_a_question      조작 대신 화면을 읽어 대답
  PASS  intent_parameters       값은 요청에서 뽑아냄 (폴더마다 레시피 X)
  …  그리고 결정론·계약·트레이스·경계 등 기존 19개
```

실사용 시나리오 30개 카탈로그(미검증 포함): [`examples/scenarios.md`](examples/scenarios.md)

<br>

## 예시: 파라미터화된 레시피

```jsonc
{
  "name": "calc_multiply",
  "target": "com.apple.calculator",
  "params":  { "a": "12", "b": "34" },              // 입력 (기본값 포함)
  "tables":  { "digit": { "1": "One", "2": "Two" /* … */ } },
  "derived": { "result": "{{a}} * {{b}}" },          // 기대값을 계산 (하드코딩 X)
  "steps": [
    { "action": "click",
      "selector": { "role": "Button", "identifier": ["AllClear", "Clear"],
                    "ancestor": "CalculatorKeypadView" },   // 매칭 1개 강제
      "intent_text": "초기화" },

    { "action": "click", "for_each": "{{a}}",              // 12 → '1','2' 각각
      "selector": { "role": "Button", "identifier": "{{digit[item]}}",
                    "ancestor": "CalculatorKeypadView" },
      "intent_text": "첫째 수 {{item}} 입력" },

    // … × … {{b}} 의 각 자리 …

    { "action": "click",
      "selector": { "role": "Button", "identifier": "Equals",
                    "ancestor": "CalculatorKeypadView" },
      "intent_text": "계산",
      "postcondition": { "field_identifier": "StandardInputView",
                         "equals": "{{result}}",           // 결과 검증 (D7)
                         "attribute": "Value" } }
  ]
}
```

각 스텝에는 **의미 계약**(`safety`)도 함께 저장됩니다 — 라벨 키워드가 아니라
`effects`·`resource.scope`·`reversibility` 로 권한이 결정됩니다. 자세한 것은
[`PROTOCOL.md` §3.6](PROTOCOL.md).

<br>

## 알려진 한계 (정직하게)

- **잠금화면 조작은 불가.** 일반 접근성·화면기록 권한으로 잠긴 GUI는 조작할 수
  없습니다. ChatGPT "Locked use"는 Apple authorization plug-in을 설치하는 **별도
  제품**이지 권한 플래그가 아닙니다. NLC는 `caffeinate`로 idle 잠금을 방지하고,
  실제 잠금 시엔 핸드오프합니다. → [`PROTOCOL.md` §5.1](PROTOCOL.md)
- **신규 플로우 authoring만 모델 토큰을 씁니다** — 유일한 비결정론 경로.
- **실행 도중 실패하면 앱 상태가 남습니다.** 저장 시트가 열린 채 멈추면 그 앱의
  메뉴 막대 전체가 잠깁니다. 임의로 닫아 되돌리지는 않습니다 — 사용자가 시키지
  않은 조작이니까요. 대신 다음 실행이 "메뉴가 비활성" 대신 "대화상자가 열려
  있습니다" 라고 말해 줍니다.
- **저장 위치는 앱이 마지막에 쓰던 곳입니다.** 폴더를 고르는 건 아직 이 레시피가
  주장하지 않는 기능이고, 조용히 엉뚱한 곳에 떨어뜨리는 것보다 말해두는 편이 낫습니다.
- **`press_key` 는 도우미에 없습니다.** 범용 키 입력은 "칸을 채운다"와 "명령을
  실행한다"를 같은 도구로 만들기 때문입니다. 사람들이 그걸로 하려던 일 대부분
  (저장·복사·열기)은 이름 붙은 메뉴 명령이라 `menu` 로 됩니다.
- **native와 web은 별도 ID 공간** — Flow는 백엔드 간 이식되지 않습니다.
- 음성 STT는 플러그형(기본은 타이핑 폴백); 실 엔진 연결은 배포 시.
- **Python으로 충분합니다.** 비용은 AX 프로세스 간 호출이 지배합니다(관찰 78–172ms,
  클릭 118ms). 순수 파이썬 작업은 0.12ms — 1000배 작아서 병목이 아니고, Rust로 바꿔도
  같은 AX 호출을 같은 속도로 합니다. 저수준이 필요한 곳은 성능이 아니라 **권한 경계**:
  서명된 상주 데몬(TCC 안정성·권한 분리, PROTOCOL.md D8)과 고빈도 이벤트 탭이며,
  오버레이의 run loop는 별도 프로세스로 우회했습니다.

<br>

## 문서

- 📄 [`PROTOCOL.md`](PROTOCOL.md) — 방법론·설계 결정(D1–D8)·L1–L4 스펙·안전 정책 전문
- 📋 [`examples/scenarios.md`](examples/scenarios.md) — 30개 실사용 태스크 카탈로그

<br>

---

<div align="center">
<sub>Codex computer use 실측 리버스 엔지니어링 + 업계 조사로 설계 · macOS AX / Playwright CDP 위에 구현</sub>
</div>
