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
![eval](https://img.shields.io/badge/eval-scope_reported-2ea44f?style=flat-square)
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
| 🤔 **놀랐을 때만 묻기** | 화면이 레시피와 어긋나는 **그 지점에서만** 모델 1회 (`--think`). 나머지는 여전히 토큰 0 |

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

# 4. 어디서나 `nlc` 로, 그리고 코딩 에이전트(Claude Code·Codex)에 스킬 연결
./skill/install.sh          # ~/.local/bin/nlc → 서명된 런처, 스킬은 복사가 아니라 링크
./skill/install.sh --check  # 무엇이 설치됐고 무엇이 빠졌는지만 확인 (아무것도 바꾸지 않음)
```

> **에이전트가 "스킬은 있는데 `nlc`가 없다"고 하면** 4단계를 하지 않은 것입니다.
> 예전 설치 스크립트는 `SKILL.md`만 복사해서, 스킬은 `nlc apps`를 실행하라고 하는데
> 명령은 어디에도 없었고, 복사본은 저장소가 바뀌어도 그대로 낡았습니다. 이제 명령은
> 서명된 런처로의 링크, 스킬은 이 저장소 `skill/` 로의 링크라 저장소를 고치면 모든
> 에이전트가 바로 새 내용을 읽습니다. `~/.local/bin` 이 PATH 에 없으면 스크립트가 알려줍니다.

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

# ①+검증 — 만든 레시피를 실행해 보고, 실패하면 그 이유를 모델에 돌려줘 고침
uv run nlc compile "TextEdit 글자를 기울임꼴로" --verify --verify-rounds 2

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

# 에이전트가 처음 해 본 작업을 레시피로 — 아래 "실행 기록에서 레시피로" 참고
uv run nlc candidates                   # 후보 목록과, 아직 레시피가 될 수 없는 이유
uv run nlc promote <후보> -p 25=n       # 25 를 입력 n 으로 · 판단 지점 검토 후 저장
uv run nlc replay <이름> -p n=49        # 모델 없이 재실행 (--think 0: 다시 지목도 끔)

# 권한 경계를 거쳐 실행 (launcher/build.sh, helper/build.sh 로 빌드)
./launcher/nlc replay calc_multiply -p a=3 -p b=4 --helper
./launcher/nlc eval                     # 도우미 시나리오 포함 전체 평가
uv run python -m nlc.voice              # 음성 세션
uv run python examples/eval.py          # 평가 재현
```

> **배우기는 권한 경계 밖에서 합니다.** 서명된 런처는 자식의 PATH를 시스템
> 디렉터리로 고정해 — 무엇이 실행될지 아무도 바꿀 수 없게 하려고 — 모델 CLI가 닿지
> 않습니다. 그래서 `nlc compile` 로 배우고 `./launcher/nlc run` 으로 실행합니다.
> 학습 엔진은 사용자가 이미 로그인해 쓰는 에이전트 CLI입니다:
> `claude → codex → opencode → cxweb` (`--compile-engine` 으로 지정).
>
> 실행 중에는 메뉴 막대에 **`● NLC`** 가 표시됩니다(`--no-indicator`로 끌 수 있음).
> 마우스를 올리면 어느 앱을 조작 중인지, 누르면 **중단** 메뉴가 나오고, Esc로도 즉시
> 멈춥니다. 화면을 캡처할 때는 macOS가 자체 녹화 표시를 띄웁니다 — NLC가 그리지도,
> 숨기지도 못하는 표시입니다. NLC는 포인터나 키보드를 합성하지 않고 접근성 API로만
> 조작하며, 앞에 있는 앱을 바꾸지 않습니다(앱을 앞으로 가져와야 하는 단계는 사람에게
> 넘깁니다). 처음 실행 시 macOS가 **손쉬운 사용**
> 권한을 요청합니다(macOS 27에서는 설정의 "Device Control and Data Access") — `nlc doctor`가 정확한 설정 창을 열어줍니다.

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

## 실행 기록에서 레시피로 — 판단은 필요한 곳에만

레시피가 없는 요청은 에이전트가 화면을 보며 직접 해냅니다. 결과까지 검증된 실행은
**후보**가 되고, 승격하면 매개변수가 있는 레시피가 됩니다. 이후에는 모델 없이 재실행하고,
**판단이 필요한 지점에서만** 모델에 묻습니다.

```bash
$ nlc do "계산기에서 25의 제곱근 구해줘"        # 에이전트가 처음 해 봄 → 후보 저장
$ nlc promote 계산기로_의_제곱근_ce4ab9 -p 25=n
$ nlc do "계산기로 49 제곱근 좀 알려줘요"       # 모델 호출 0 — 결과 7
```

| 무엇 | 어떻게 확인하나 |
|---|---|
| 입력 (`-p 25=n`) | 요청에서 온 값만. 원래 값을 넣으면 원래 실행과 똑같아야 저장 |
| 판단 단계 (`decide`) | 고르기는 선택지 안에서만, 뽑기는 읽은 글에 글자 그대로 있어야, 쓰기는 입력값으로만 |
| 경우가 나뉘는 작업 | 처음 보는 경우는 그 지점부터 에이전트가 이어서 하고, 검증되면 그 경우의 분기로 저장 (분기 안에 또 분기 가능) |
| 예상 못 한 화면 | 되돌릴 수 없는 결과를 남기지 않는 단계만 한 번 다시 지목 (`--think`) |
| 판단 값이 저장·전송으로 | 값이 정해진 그 시점에 **실제 값(쓴 글은 전문)** 을 보여주고 승인 |

레시피 찾기는 저장된 문장 → 핵심 단어(모델 0) → 요청 읽기(모델 1) 순서이며, 요청 읽기로
찾은 말투는 성공하면 저장돼 다음부터 모델 0 입니다. 전체 설명과 실측은
[`docs/recipes.md`](docs/recipes.md), Python에서 쓰는 법은 [`docs/api.md`](docs/api.md).

<br>

## 재현 — 두 개의 IR

| IR | 무엇 | 언제 |
|---|---|---|
| **Flow** (`flows/*.json`) | 무엇을 할지 | 배울 때 저장 |
| **Trace** (`--trace`) | 실행 중 무엇이 보였는지 | `--trace run.json` 줄 때 |

```bash
uv run nlc do "계산기로 12 곱하기 34" --trace run.json
```

Trace v2는 요청 순서·인자와 원래 응답을 함께 기록합니다. 앱·GUI 없이 기록된 실행을 검사할 수 있으며, 요청이 달라지거나 기록이 끝나면 실패합니다. 실패 응답을 성공으로 바꾸지 않습니다.

저장 시 원문 의도를 제거하고 민감 캡처로 표시한 값을 가립니다. 파일은 원자적으로 교체하고 권한을 `0600`으로 제한합니다. 가린 값에 의존하는 실행은 정확한 재생이 불가능할 수 있습니다. 표시되지 않은 화면 정보까지 자동으로 모두 익명화하는 기능은 아닙니다.

<br>

## 레시피와 현재 검증 범위

아래는 [`nlc/library.py`](nlc/library.py)의 레시피 예시입니다. 이전 전경 실행 결과를 현재 배경 실행의 성공 보장으로 사용하지 않습니다. 포커스 보존 변경 후 Calculator는 Python·signed helper에서 실제 결과를 확인했고, TextEdit 본문 입력은 Python 경로의 소유 시험 문서에서 확인했습니다. 소유 TextEdit 문서에서는 자체 실험 경로로 배경 Save As 패널 열기·취소를 확인했습니다. 일반 메뉴의 자동 활성화와 최종 저장 검증은 아직 완료하지 못했습니다. [현재 실앱 검증 기록](reviews/2026-09-19/background-input.md)을 참고하세요.

| 말하면 | 하는 일 | 새로 확인된 형태 |
|---|---|---|
| `7 더하기 8 해줘` | 계산기로 계산하고 **답을 읽어 알려줌** | 사칙연산 전체가 하나의 IR |
| `다운로드 폴더 열어줘` | Finder로 폴더 열기 | **창이 하나도 없는 앱**에서 시작 |
| `문서 폴더에 뭐 있어?` | 폴더를 열고 **파일 이름을 읽어줌** | 조작이 아니라 **대답** |
| `오늘 일정 뭐 있어?` | 오늘 캘린더 일정을 읽어줌 | 보기 설정은 건드리지 않음 |
| `메일 뭐 왔어?` | 받은편지함의 발신자·제목 | 요소 2만 개짜리 앱 |
| `대화상자 닫아줘` | 막힌 대화상자에서 빠져나옴 | 저절로는 안 닫음 |
| `"회의 준비" 라고 써줘` | 문서에 텍스트 입력 | 소유 시험 문서에서 배경 입력 확인; 앱별 지원 차이 있음 |
| `굵게 해줘` | 전체 선택 후 굵게 | **토글이 재생마다 뒤집히지 않음** |
| `새 문서 만들어줘` | 빈 문서 열기 | 메뉴 전용 명령 |
| `"보고서.rtf" 로 저장해줘` | 이름과 **폴더를 정해** 저장 | **되돌리기 어려운 동작 + 동의** |

저장에는 `write_persistent` 계약과 **어떤 파일을 어느 폴더에** 저장할지에 대한 승인이 필요합니다. "A로 저장"에 동의했어도 이름 칸이 B로 바뀌면 거부됩니다. TextEdit는 본문 입력만으로 자동 저장할 수 있으므로 문서 편집도 영속 효과로 분류합니다. 승인은 실행·완료 증거를 대신하지 않으며, 현재 배경 Save As 메뉴가 비활성인 조건에서는 저장을 진행하지 않습니다.

```bash
./launcher/nlc do "\"보고서.rtf\" 로 저장해줘" --helper
# 필요한 실행 전 증거가 없으면 중단합니다. 승인만으로 DONE을 반환하지 않습니다.
```

<br>

## 검증

일반 실행의 모델을 지정하려면 `nlc do "작업 내용" --provider codex`를 사용합니다.
`claude`, `gemini`, `opencode`, `cxweb`, `auto`도 지원하며, 선택은 요청 해석·화면 계획·witness 질문·`--learn` 학습·`--think` 복구에 동일하게 적용됩니다.

```bash
uv run python -m unittest discover -s tests -v  # 코어 회귀, 선택적 Chromium 테스트
uv run python examples/corpus.py               # 144개 가상 앱 시나리오
uv run python examples/eval.py --offline       # GUI·모델 호출 없이 가능한 시나리오
uv run python examples/eval.py --preview       # 화면을 조회하지 않고 평가 범위 확인
uv run python examples/eval.py --offline --report /tmp/nlc-eval.json
./launcher/nlc eval background_menu_refusal     # 소유한 배경 앱의 메뉴 동작·거절 검증
```

테스트 결과는 PASS / FAIL / SKIP을 구분합니다. Chromium 테스트에는 `web` extra와 Chromium이 필요합니다. 실제 앱 테스트에는 잠금 해제된 macOS 세션, AX 권한 및 빌드한 helper가 필요합니다.

전체 GUI 평가는 사용자 앱에 대화상자를 열 수 있습니다. 앱을 앞으로 가져오는
시나리오는 `--foreground`를 명시해야 실행됩니다. 전부 SKIP이면 성공으로 처리하지
않고 종료 코드 2를 반환합니다. JSON 보고서의 범위·시간·실패 단계 해석은
[평가 안내](docs/evaluation.md)를 참조하세요.

2026-09-19의 가상 앱 corpus 결과는 시나리오 판정 통과 **83.3%**, 완료를 기대한 작업의 완료 **40/64 (62.5%)**입니다. 잘못된 성공 보고·검증 없는 성공 보고·범위 밖 효과·중복 효과는 각각 **0건**입니다. 24개 저장 작업은 저장 이름·위치를 검증할 수 없어 중단했습니다. 중단 기대 사례의 통과는 작업 완료에 포함하지 않습니다. 이 수치는 실제 모델이나 실제 앱의 성공률이 아닙니다.

실사용 시나리오 카탈로그(미검증 포함): [`examples/scenarios.md`](examples/scenarios.md)

## 실행 계약과 현재 범위

- 실행 가능한 Flow는 schema 2이며 모든 단계에 safety 계약이 필요합니다. schema 생략으로 예전 정책을 켤 수 없습니다. schema 1은 자동 실행하거나 덮어쓰지 않습니다. `Flow.from_json(..., allow_legacy=True)`는 이관용 읽기만 허용하며, 계약과 관측 가능한 binding을 추가하고 검토한 뒤 schema 2로 저장해야 합니다.
- 전송·저장 등 일반 실행의 확정 행동 전에는 `OutcomeSpec`의 기대값을 새 관측·probe 결과와 정확히 비교합니다. 승인을 기다리는 동안 값이 바뀌면 다시 확인하고 중단합니다. 기대값이나 관측 증거가 부족하면 실행하지 않습니다.
- 발신 계정과 본문은 요청의 `account`·`body` 슬롯으로 전달합니다. 호스트는 `Agent(outcome_context=...)` 콜백으로 실행 전에 `(expected, provenance)`를 공급할 수 있습니다. 콜백 인자는 `(intent, reading)`이며 `prior`·`relationship` 등 확인된 출처를 명시해야 합니다. 실행 결과에서 기대값을 가져오는 `result` 출처는 거절합니다. 완료 전에는 사후 witness도 읽습니다. BCC/덮어쓰기 부재는 명시적인 빈 필드 등 관측 가능한 증거가 필요하며, 요소를 못 찾은 것만으로 없다고 판단하지 않습니다.
- 일반 실행은 스크롤 후 남은 행동을 중단하고 새 화면에서 다시 계획합니다. 탐색 키는 드라이버가 `action.press_key`를 선언할 때만 제공하며 `Tab`, `shift+Tab`, 방향키, `Escape`로 제한합니다. 키 입력은 현재 포커스에 전달됩니다. 네이티브의 `press_key.no_escape` capability에서는 Escape를 제외하며, 사용자의 Esc 중단 채널은 유지합니다. 반복 탐색은 화면 수 예산으로 제한합니다.
- 이름이 지정된 원본 보존 검사는 해당 항목의 직접 관측값을 요구합니다. 같은 문자열이 화면 다른 곳에 있는 것은 증거로 인정하지 않습니다. 결과 검증이 완료된 실행만 재사용 후보로 저장합니다.
- CLI 일반 실행은 `ask_here_paged`로 NLC가 작성한 요청 본문을 기본 16,000 UTF-8 byte 예산 안에서 페이지로 나눕니다. provider CLI가 추가하는 시스템 지침·도구 정의 등은 이 예산에 포함되지 않습니다. 원본 후보 번호를 유지하며 모델은 다음 페이지를 명시적으로 요청합니다. 단일 후보가 예산을 넘으면 호출 전에 중단합니다. byte 수는 보수적 추정이며 정확한 토큰 계산은 tokenizer 주입으로 지원합니다. 드라이버가 처음부터 관측하지 않은 요소까지 복원하는 기능은 아닙니다.
- 네이티브의 직접 `select_option` API는 PopUpButton/ComboBox의 정확한 label 선택과 readback을 지원합니다. 일반 모델 루프는 `open_select` 후 새 관측의 label을 읽고 `select_custom_option`으로 선택합니다. 모의 통합은 검증됐지만 실제 앱의 배경 메뉴 지원과 거절 후 복구 검증은 남아 있습니다.
- 웹의 단일 선택 HTML select는 `select_option`으로 관측한 value와 label을 고정해 선택하고 값을 다시 읽습니다. 중복 value·비활성·변경된 옵션은 거절합니다. 명시적 `aria-controls`가 연결된 custom combobox는 `open_select` 후 재관찰하고 `select_custom_option`으로 보이는 옵션의 정확한 label을 선택합니다. 다중 선택은 지원하지 않습니다.
- `general.run(timeout_seconds=..., cancelled=...)`는 모델·관측·행동 경계에서 작업 예산과 취소를 확인합니다. 이미 실행한 효과를 기록하고 재시도하지 않습니다. 내장 모델 호출에는 남은 시간 예산을 전달하며 페이지·provider fallback·witness 질문이 이를 공유합니다. 임의의 동기 callback과 AX 호출을 즉시 강제 종료하는 취소는 아닙니다. signed 모델 timeout 뒤에는 응답 혼선을 막기 위해 채널을 폐기합니다. [시간 예산의 범위](docs/model-deadlines.md)를 참고하세요. CLI에서는 `--general-timeout 120 --max-screens 12 --prompt-budget 16000 --max-pages 8`로 설정합니다. 시간 제한은 일반 실행 루프에만 적용되며 발화 해석·Flow 재생 전체의 제한은 아닙니다.
- 저장·전송은 모델의 성공 문구만으로 확정하지 않습니다. 신뢰된 host의 `prepare_commit(event)`와 `after_commit(event, response, context)`가 실제 작업 영수증에 결합된 `CommitEvidence`를 제공해야 합니다. artifact 경로·내용 digest 또는 delivery 수신자·계정·본문 digest를 기대값과 대조합니다. 호스트 어댑터가 없는 실제 앱의 commit은 완료 미검증으로 남습니다.
- 선택적 `nlc.visual.capture_browser`와 `query_visual`은 viewport 이미지의 읽기를 지원합니다. `plan_visual`과 `nlc.visual_actions.run_visual`은 명시적 host 승인·결과 검증 콜백으로 브라우저 좌표 클릭을 실행합니다. 현재 이미지 provider는 Codex·Claude CLI입니다. signed helper의 소유 native 창 캡처와 이미지 좌표→대상 AX hit-test→AXPress는 실제 fixture에서 확인했습니다. AX hit-test도 제공하지 않는 surface와 자율 native 이미지 계획은 별도 검증이 필요합니다. native 좌표의 AX hit-test와 전경 창 준비의 지원 조건은 [창별 입력 문서](docs/native-window-input.md)에 설명합니다. 이미지 해석을 승인이나 완료 witness로 사용할 수 없습니다. 클릭 직전 DOM·scroll·viewport·PNG 일치를 확인하지만 캡처와 입력의 원자성은 보장하지 않으며, 애니메이션이 있는 화면은 변경 감지로 중단될 수 있습니다. 사용 예시는 [시각 API 문서](docs/visual.md)에 있습니다.

- `nlc run`과 `nlc replay`는 같은 승인 경로를 사용합니다. `--yes`는 확인 가능한 승인에 적용되며 handoff·차단된 계약을 해제하지 않습니다.
- Python 코어는 드라이버를 주입하여 macOS 없이 가져올 수 있습니다. 네이티브 드라이버는 macOS 전용이고 웹 드라이버는 선택적입니다. 브라우저의 관측 인덱스는 관측한 DOM 노드에 고정되며 같은 이름의 다른 노드로 바꾸지 않습니다.
- 모델 provider 선택은 launcher 경계를 지나 전달되고 fallback 시도는 하나의 총 시간 예산을 공유합니다. `ask_model_result`/`reach_model_result`는 요청 ID·status·소요 시간·관측 가능한 provider를 반환하며 미관측 모델·사용량·비용은 null입니다. signed 경로도 실제 관측 metadata를 보존하며 CLI 비용 추정치는 청구액과 구분합니다([응답 계약](docs/provider-envelope.md)). 로컬 CLI timeout은 프로세스 그룹을 종료하지만 원격 provider 작업의 취소까지 보장하지 않습니다.
- 안전 계약은 효과에 대한 선언을 검증하는 구조입니다. 임의의 모델이 작성한 효과 분류가 항상 사실이라는 보장은 없으므로, 중요한 작업의 recipe·기대값·권한 출처는 호스트가 검토해야 합니다. 가상 앱 테스트가 실제 전송·저장 성공을 증명하지는 않습니다.

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
      "answers": true,                                  // 이 값이 사용자가 물은 답
      "postcondition": { "field": { "role": "ScrollArea",
                                    "identifier": "StandardInputView" },
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
- **authoring·일반 화면 판단·복구 판단·레시피의 판단 단계·이미지 해석은 모델을 호출합니다.** 검증된 Flow의 결정론적 재생은 모델 호출 없이 동작하며, 판단 단계가 있는 레시피는 그 단계에서만 묻습니다.
- **판단의 근거가 화면에 나타난 적 없는 중간값일 수 있습니다.** 에이전트가 여러 단계를 한 번에 눌러 중간 결과를 띄우지 않았다면, 재생 때 그 시점의 화면에는 그 값이 없습니다. → [`docs/recipes.md`](docs/recipes.md#한계)
- **실행 도중 실패하면 대화상자가 남습니다.** 여전히 **저절로 닫지 않습니다** —
  대화상자를 취소하는 건 아무도 요청하지 않은 행동이니까요. 대신 `대화상자 닫아줘`
  라고 하시면 (Cancel만) 닫아드리고, 막힌 실행은 그 방법을 함께 알려줍니다.
- **저장 위치는 패널이 제공하는 폴더 중에서만 고를 수 있습니다** — 즉 사용자의
  즐겨찾기입니다. 목록에 없는 폴더를 요청하면 **고를 수 있는 목록을 알려주며** 실패하고,
  엉뚱한 곳에 저장하지는 않습니다.
- **큰 앱은 트리를 다 읽지 않습니다.** 메일함처럼 똑같은 행이 수만 개인 앱은 컨테이너당
  60개·전체 2500개에서 끊고, 잘린 자리를 트리에 적습니다(`… (N개 더 있음)`). 못 찾은
  것과 애초에 안 본 것은 다른 사실이니까요.
- **폴더 목록은 화면에 보이는 것만입니다.** Finder는 폴더에 따라 파일 이름을 아예
  노출하지 않기도 합니다(이 기기에선 Downloads가 그렇습니다) — 그럴 땐 이름 대신
  개수로 답합니다.
- **키 입력은 탐색 키로 제한됩니다.** Python driver와 signed helper는 배경 창과 관측한 현재 입력 요소에 결합한 Tab·shift+Tab·방향키를 제공하며 Return·임의 chord·Escape를 거절합니다. 실제 결과는 다시 관측해야 하며, 재생에는 입력 selector와 명시적 postcondition이 필요합니다. 저장·복사·열기는 이름 붙은 `menu` 경로를 사용합니다.
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
- [`docs/installation.md`](docs/installation.md) — Python wheel·브라우저·서명된 macOS source 설치 범위
- [`docs/visual.md`](docs/visual.md) — 시각 관측·좌표 클릭 API와 검증 경계
- [`docs/capabilities.md`](docs/capabilities.md) — backend별 기능과 실험 범위
- [`docs/completion-plan.md`](docs/completion-plan.md) — 전체 목표의 완료 기준·남은 작업·비교 증거와 우선순위
- [`docs/input-observation.md`](docs/input-observation.md) — 포커스·캐럿 표본 관측과 검증 한계
- [`docs/artifact-receipts.md`](docs/artifact-receipts.md) — 실제 새 파일 readback을 통한 완료 증거
- [`docs/delivery-evidence.md`](docs/delivery-evidence.md) — 새 요청과 독립 수신함을 연결하는 호스트 전송 증거
- [`docs/recipes.md`](docs/recipes.md) — 실행 기록에서 레시피로, 판단 단계, 분기가 자라는 방식
- [`docs/api.md`](docs/api.md) — Python API: `replay`·`Agent`·승격·판단 콜백
- [`skill/SKILL.md`](skill/SKILL.md) — 에이전트가 레시피를 직접 쓰기 위한 스킬 (IR·판단 단계·오류표)
- [`docs/evaluation.md`](docs/evaluation.md) — 평가 실행과 결과 읽기
- [`docs/menu-discovery.md`](docs/menu-discovery.md) — 읽기 전용 메뉴 발견
- [`docs/context-paging.md`](docs/context-paging.md) — 큰 관측값과 이력 조회
- [`docs/model-deadlines.md`](docs/model-deadlines.md) — 모델 호출의 시간 예산
- [`docs/provider-envelope.md`](docs/provider-envelope.md) — signed provider 응답 계약
- [`docs/claude-adapter.md`](docs/claude-adapter.md) — Claude CLI 결과 어댑터
- [`docs/native-window-input.md`](docs/native-window-input.md) — 네이티브 창별 클릭
- [`docs/native-visual.md`](docs/native-visual.md) — 네이티브 창 이미지 읽기
- [`docs/rtf-receipts.md`](docs/rtf-receipts.md) — RTF 저장 영수증
- [`docs/textedit-save.md`](docs/textedit-save.md) — TextEdit 새 이름 저장의 호스트 증거
- [`docs/save-directory-observation.md`](docs/save-directory-observation.md) — 저장 패널 디렉터리·설정의 읽기 전용 증거
- [`docs/benchmark-records.md`](docs/benchmark-records.md) — 평가 계측과 외부 비교 기록 검증

<br>

---

<div align="center">
<sub>Codex computer use 실측 리버스 엔지니어링 + 업계 조사로 설계 · macOS AX / Playwright CDP 위에 구현</sub>
</div>
