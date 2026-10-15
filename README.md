<div align="center">

![Tacto — Make it once. Use it again.](docs/assets/tacto-banner.svg)

### Let your agent use your apps.

**Claude와 Codex가 사용자를 대신해 앱을 조작하도록.**<br>
Tacto는 에이전트의 컴퓨터 조작(computer use)을 연결합니다.<br>
한 번 만든 작업 흐름을 저장하고, 다음에는 입력값을 바꿔 다시 실행하세요.

[![macOS](docs/assets/badge-macos.svg)](docs/installation.md)
[![Claude + Codex](docs/assets/badge-agents.svg)](skill/SKILL.md)
[![Status](docs/assets/badge-status.svg)](#getting-started)

[시작하기](#getting-started) &nbsp;·&nbsp; [작동 방식](#how-it-works) &nbsp;·&nbsp; [벤치마크](#benchmarks) &nbsp;·&nbsp; [온보딩 웹사이트](sites/tacto/README.md) &nbsp;·&nbsp; [문서](#documentation)

</div>

<br>

## 에이전트에게 앱 조작을 맡기세요

에이전트가 앱 화면을 읽고 버튼을 누르고 내용을 입력합니다.
이처럼 사용자를 대신해 컴퓨터를 조작하는 기능을 **computer use**라고 부릅니다.
처음에는 에이전트가 앱을 관찰하고 작업 방법을 만듭니다. 그 흐름을 **Flow**로 저장하면,
다음에는 입력값을 바꿔 다시 실행할 수 있습니다. 대상을 찾고, 동작하고, 결과를 확인하는 반복은 Tacto 런타임이 맡습니다.

| 한 번 작성 | 여러 번 실행 | 필요한 순간에 개입 |
| :--- | :--- | :--- |
| Claude·Codex로 재사용할 Flow 작성 | 새 입력값으로 같은 작업 흐름 재생 | 판단이 필요한 단계에만 모델 사용 |
| 작성한 모델에 종속되지 않는 실행 형식 | 정상적인 결정적 재생은 **모델 호출 0회** | 지원되는 실패는 해당 단계만 수리 |

작성·판단·수리에는 모델 비용이 발생합니다. 자동 선택자 수리는 현재 개발 버전의 Codex provider가 지원합니다.

<a id="how-it-works"></a>

## Make it once. Use it again.

```text
처음     Claude / Codex → 앱 관찰 → Flow 작성·저장
다음     저장한 Flow + 새 입력값 → Tacto 실행·결과 확인
반복     같은 Flow + 다른 입력값 → Tacto 실행·결과 확인
```

보고서 입력, 여러 항목 처리, 같은 앱에서 반복하는 다단계 작업에 재사용할 수 있는 실행 단위를 만듭니다.
Tacto는 실행할 때 대상을 새 관찰에서 찾고 결과 조건을 검사합니다.

<details>
<summary>화면이 바뀌거나 완료 여부가 불확실하면?</summary>

개발 버전의 자동 수리는 지원되는 정적 선택자 실패에 대해 필요한 후보만 에이전트에 전달합니다.
확인된 checkpoint 이후부터 재개하며, 이미 완료됐을 수 있는 동작은 그대로 다시 보내지 않습니다.
모든 UI 변화나 crash를 복구하는 기능은 아닙니다. [자동 개입의 지원 범위](docs/nlc-auto-repair-2026-10-14.md)

네이티브 실행 중에는 메뉴 막대에 `● Tacto`가 표시되며 Esc로 중단할 수 있습니다.

</details>

<a id="getting-started"></a>

## 첫 작업 시작하기

현재는 **macOS 소스 빌드 방식의 프로토타입**입니다. NLC가 Tacto로 이름을 바꿨습니다.
Python 3.11+, [uv](https://docs.astral.sh/uv/), Xcode Command Line Tools, 로컬 코드 서명 identity가 필요합니다.

```sh
git clone https://github.com/Open330/tacto.git
cd tacto
uv sync
./build.sh
./skill/install.sh
~/.local/bin/tacto doctor
```

`doctor` 안내에 따라 NLC Helper의 macOS 권한을 허용한 뒤, Claude 또는 Codex에 요청하세요.

> Tacto 스킬로 계산기에서 12 × 34를 계산하고 결과를 확인해줘.<br>
> 같은 작업을 다시 쓸 수 있도록 매개변수가 있는 Flow로 저장해줘.

저장한 Flow 파일을 실행할 때는:

```sh
~/.local/bin/tacto run flow.json --helper
```

[설치·권한 안내](docs/installation.md) · [Flow 작성 가이드](docs/recipes.md) · [에이전트 스킬](skill/SKILL.md)

<details>
<summary>설치 문제와 개발 버전의 반복 실행</summary>

스킬을 찾지 못하면 에이전트 세션을 다시 열고 `./skill/install.sh --check`로 연결을 확인하세요.
Python 배포 이름은 `tacto-runtime`이며 내부 import는 `nlc`를 유지합니다.
Python wheel만으로는 서명된 macOS 런처·helper를 사용할 수 없습니다.
기존 `nlc` 명령과 Flow 저장 경로는 계속 지원합니다. macOS 권한 목록의 **NLC Helper**도 같은 실행 경계입니다.
선택적 Playwright backend는 별도 Chromium 페이지를 사용합니다.

다음 batch·자동 수리 명령은 개발 checkout 기준이며 아직 기본 브랜치의 배포 기능이 아닙니다.

```sh
~/.local/bin/tacto batch template.json --helper < jobs.jsonl
~/.local/bin/tacto batch template.json --helper --auto-repair codex < jobs.jsonl
```

</details>

<a id="benchmarks"></a>

## 반복 추론을 줄이면 무엇이 달라질까요?

**개발 버전의 Tacto(측정 당시 NLC)·Sky 비교에서 전체 입력 토큰 53.5% 감소를 관측했습니다.**
같은 요청 모델·추론 강도와 테스트 앱의 초기 상태를 맞춘 독립 새 대화 3쌍의 결과입니다.

| 지표 | Tacto | Sky |
| :--- | ---: | ---: |
| 작업 성공 | 3/3 | 3/3 |
| 전체 입력 토큰 | **201,447** | 433,312 |
| 평균 시간 | **42.10초** | 46.17초 |

작은 표본의 관측값입니다. 모든 작업에서 더 빠르다는 뜻은 아니며 한 케이스에서는 Sky가 더 빨랐습니다.
[측정 조건과 전체 결과 →](docs/nlc-matched-comparison-2026-10-14.md)

<details>
<summary>캐시·출력·복잡한 Flow 재사용 결과</summary>

2026-10-14 · Codex CLI 0.161.0 · 요청 모델 gpt-6.1-sol · medium.
최초 작성·실행·최종 응답을 포함하고 테스트 준비 시간은 제외했습니다.
실제 serving model·서버 캐시·청구비용은 미확인입니다.

- 캐시 읽기 제외 입력: Tacto 22,119 / Sky 50,592 (**56.3% 감소**).
- 출력: Tacto 1,605 / Sky 1,604.
- 시간 합계: **8.8% 감소**. 이 측정은 10회 재사용 비교가 아닙니다.
- 별도의 11단계 테스트 앱: 정상 작업 **13/13 완료**, 기대 중단 **5/5**, 한 작업에서 버튼 2개 수리.
- 해당 테스트의 정상 10회 반복은 모델 호출 **0회**. 실제 업무 앱 전체의 성공률을 대신하지 않습니다.

[복잡한 시나리오 결과](docs/nlc-complex-scenarios-2026-10-14.md)

</details>

## 먼저 흐름을 살펴보세요

[온보딩 랜딩페이지](sites/tacto/README.md)에서 재생·수리·중단 예시와 설치 안내를 제공합니다.
실제 앱이나 모델을 실행하지 않는 인터랙티브 소개입니다.

```sh
python3 sites/tacto/serve.py
```

`http://127.0.0.1:8787`에서 확인할 수 있습니다.

<a id="documentation"></a>

## 필요한 문서부터

[설치](docs/installation.md) · [Flow 작성·재생](docs/recipes.md) · [API](docs/api.md) · [지원 기능](docs/capabilities.md) · [에이전트 스킬](skill/SKILL.md)

<details>
<summary><strong>CLI 명령 찾아보기 · 이전 nlc 명령 호환</strong></summary>

| 목적 | 명령 |
| :--- | :--- |
| 설치·환경 확인 | `nlc doctor` |
| 앱·화면 관찰 | `nlc apps`, `nlc observe`, `nlc menu-state`, `nlc document-attachments` |
| 자연어·Flow 실행 | `nlc do`, `nlc run`, `nlc replay` |
| 반복·세션·수리 | `nlc batch`, `nlc session`, `nlc repair` |
| Flow 작성·매개변수화 | `nlc compile`, `nlc parameterize` |
| 저장 Flow·후보 관리 | `nlc list`, `nlc show`, `nlc candidates`, `nlc promote` |
| 평가 | `nlc eval` |
| 전용 작업 경로 | `nlc web-upload`, `nlc vroid-focus`, `nlc vroid-drag` |

batch·session·repair 등 개발 중인 명령은 기본 브랜치에 아직 포함되지 않을 수 있습니다.
`tacto --help`와 해당 명령의 `--help`로 현재 checkout의 사용법을 확인하세요.
아래 `nlc` 명령은 동일한 `tacto` 명령으로 사용할 수 있습니다.
네이티브 작업은 서명된 런처를 사용하고 필요한 명령에 `--helper`를 지정합니다.

</details>

<details>
<summary><strong>문서 찾아보기</strong></summary>

- [로고·시각 아이덴티티](docs/tacto-visual-identity.md)
- [랜딩페이지 개선·검증](docs/tacto-landing-engineering-2026-10-15.md)
- [Tacto 전환·이전 이름 호환](docs/tacto-branding.md)
- [Python API](docs/api.md)
- [새 파일 저장의 host receipt](docs/artifact-receipts.md)
- [비교 실행 기록](docs/benchmark-records.md)
- [지원 경로와 검증 범위](docs/capabilities.md)
- [Claude CLI result adapter](docs/claude-adapter.md)
- [전체 목표의 완료 기준과 현재 작업](docs/completion-plan.md)
- [큰 관측값과 이력 조회](docs/context-paging.md)
- [요청과 수신함을 결합한 전송 영수증](docs/delivery-evidence.md)
- [평가 실행과 결과 읽기](docs/evaluation.md)
- [배경 입력의 포커스·캐럿 관측](docs/input-observation.md)
- [설치 범위와 실행 경로](docs/installation.md)
- [읽기 전용 메뉴 발견](docs/menu-discovery.md)
- [모델 호출의 시간 예산](docs/model-deadlines.md)
- [Native window image reads](docs/native-visual.md)
- [네이티브 창별 입력의 현재 지원 범위](docs/native-window-input.md)
- [Flow의 자동 에이전트 개입](docs/nlc-auto-repair-2026-10-14.md)
- [복잡한 11단계 작업과 실패 시나리오 검증](docs/nlc-complex-scenarios-2026-10-14.md)
- [같은 요청 조건에서 Tacto·Sky 비교](docs/nlc-matched-comparison-2026-10-14.md)
- [Signed provider 응답](docs/provider-envelope.md)
- [실행 기록에서 워크플로로 — 판단은 필요한 곳에만](docs/recipes.md)
- [Native RTF save receipts](docs/rtf-receipts.md)
- [Native Save panel directory observation](docs/save-directory-observation.md)
- [TextEdit 새 이름 저장의 호스트 증거](docs/textedit-save.md)
- [Browser visual reads and clicks](docs/visual.md)

</details>

---

**Tacto** · Local execution. Reusable computer use.<br>
Developer preview · Formerly NLC · Proprietary license
