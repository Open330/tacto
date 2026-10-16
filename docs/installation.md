# 설치 범위와 실행 경로

Python wheel은 Tacto의 Python 코어를 배포합니다. macOS 앱 조작에 필요한
서명된 런처·도우미, Chromium 실행 파일, 평가 fixture는 포함하지 않습니다.
`py3-none-any` wheel이라는 표시는 Python 패키지의 형식이며, 모든 운영체제의
데스크톱을 조작한다는 뜻이 아닙니다.

| 설치 방식 | 사용할 수 있는 범위 | 별도로 필요한 것 |
| --- | --- | --- |
| Python wheel | flow IR, 계약·결과 검증, 후보 paging, 모델 adapter, trace 등 Python 코어 | Python 3.11 이상 |
| wheel + `web` extra | Playwright 기반 별도 Chromium 페이지의 BrowserDriver | Playwright가 설치하는 Chromium과 운영체제 실행 의존성 |
| macOS source checkout | 서명된 런처를 통한 macOS 접근성 조작 | Xcode Command Line Tools, Apple 발급 코드 서명 인증서, 손쉬운 사용 권한 |

## Python 코어와 브라우저

저장소 루트에서 wheel을 빌드하고 별도 환경에 설치할 수 있습니다.

```sh
uv build --wheel
python3 -m venv /tmp/tacto-client
/tmp/tacto-client/bin/python -m pip install ./dist/tacto_runtime-0.1.0-py3-none-any.whl
/tmp/tacto-client/bin/tacto --help
```

브라우저가 필요한 환경에서는 같은 wheel에 `web` extra를 설치합니다.

```sh
/tmp/tacto-client/bin/python -m pip install './dist/tacto_runtime-0.1.0-py3-none-any.whl[web]'
/tmp/tacto-client/bin/python -m playwright install chromium
```

Linux에는 Chromium이 요구하는 시스템 라이브러리가 추가로 필요할 수 있습니다.
Python 패키지 설치나 import 성공은 Chromium 실행 검증과 다릅니다. 현재
일반 `tacto do` 명령은 macOS 앱 조작 경로이므로 브라우저용 CLI로 간주하지
마십시오. 다른 운영체제에서는 `nlc.browser.BrowserDriver`를 Python API로
명시적으로 사용합니다. 이 경로는 사용자의 기존 Chrome/Safari 세션에
자동 연결하지 않습니다.

wheel에는 기본 flow recipe 파일이 포함되지 않습니다. 명시적으로
`FlowStore(path=...)`를 지정하면 그 경로를 사용하고, wheel 설치의 기본
저장 경로는 `$XDG_DATA_HOME/nlc/flows` 또는
`~/.local/share/nlc/flows`입니다. `XDG_DATA_HOME`은 절대 경로만 사용합니다.
소스 checkout에 `pyproject.toml`과 `flows/`가 함께 있으면 기존 `flows/`를
유지합니다. 설치된 `site-packages`에는 사용자 flow를 만들지 않습니다.

## macOS 네이티브 설치

네이티브 소스 빌드는 **Apple이 발급한 코드 서명 인증서**가 필요합니다.
특정 프로젝트 팀에 고정하지 않습니다. 빌드 도구가 선택한 인증서의 실제 팀으로
런처·도우미를 서명하고, 실행 시 양쪽이 자신의 유효한 서명에서 읽은 팀과 상대의
팀·프로그램 식별자를 비교합니다. 다른 팀·서명 없는 프로그램·ad-hoc 서명은 거부합니다.
`NLC_TEAM_ID` 같은 실행 환경변수로 이 검사를 바꿀 수 없습니다.
인증서 없이 설치하는 일반 사용자용 Developer ID 서명·공증 배포는 별도 준비가
필요합니다. Python 코어·별도 Chromium은 위 wheel 설치 범위에서 사용할 수 있습니다.

서명된 실행 경계는 소스 설치를 사용합니다. 저장소 루트에서 다음 순서로
설치합니다. `./build.sh`는 로컬 서명 identity로 런처와 도우미를 빌드하므로
wheel 설치 과정에서 자동 실행하지 않습니다.

```sh
uv sync
# 필요한 경우 사용할 Apple 코드 서명 인증서를 명시합니다.
# export NLC_SIGN_IDENTITY='사용할 코드 서명 identity'
./build.sh
./skill/install.sh
~/.local/bin/tacto doctor
~/.local/bin/tacto do '계산기로 12 곱하기 34' --helper
```

런처는 자신의 위치를 기준으로 저장소 루트의 `.venv/bin/python`, `helper/`
번들을 찾습니다. 이 디렉터리 배치를 유지해야 하며 `launcher/nlc` 파일만
다른 경로로 복사하면 설치가 되지 않습니다. 코드 서명 identity가 없으면
런처 빌드는 실패합니다. 도우미 파일을 wheel에 복사하거나 임의로 재서명해
설치 문제를 우회하지 않습니다.

wheel의 `tacto` console script는 Python CLI입니다. 서명된 `./launcher/nlc`와
같은 프로그램이 아니며 wheel만 설치해서 `--helper` 채널을 얻을 수는 없습니다.
네이티브 `doctor`, `apps`, `observe`, `do`, `run`, `replay`에는 macOS 실행
환경과 해당 경로의 권한이 필요합니다.

## 평가와 설치 검증

`tacto eval`의 평가 fixture는 소스 checkout에 있으므로 wheel에 포함되지
않습니다. 소스 환경에서 `.venv/bin/python examples/eval.py --offline`으로
오프라인 평가를 실행합니다. 실제 앱 평가에는 위의 네이티브 설치가 필요합니다.

설치 회귀 검사는 기본 import와 CLI help를 확인합니다. 실제 wheel을 새 임시
환경에 설치하는 검사는 `uv`가 준비된 환경에서 명시적으로 실행합니다.

```sh
NLC_TEST_WHEEL_INSTALL=1 .venv/bin/python -m unittest discover -s tests -p test_installation.py -v
```

이 검사는 macOS 접근성 모듈을 import하지 못하게 차단한 별도 프로세스에서
코어·브라우저 모듈과 help를 확인합니다. Linux 컴퓨터에서 실행한 검증이나
실제 Chromium·네이티브 UI 검증을 대신하지 않습니다. 빌드 도구가 캐시에
없으면 최초 wheel 빌드 시 빌드 의존성을 다운로드할 수 있습니다.

## 에이전트 스킬의 native 호출 경로

`skill/install.sh`는 `~/.local/bin/tacto`를 이 checkout의 signed launcher에,
Claude/Codex의 Tacto 스킬을 이 checkout의 `skill/`에 연결합니다.
새 사용자 환경에는 기본 Claude/Codex skills 폴더를 만들고, 명시한
`CLAUDE_CONFIG_DIR`·`CODEX_HOME`의 skills 폴더도 연결합니다.
`skill/install.sh --check`는 폴더를 만들거나 링크를 바꾸지 않으며,
연결이 모두 맞으면 exit0, 누락·오래된 연결·충돌이면 exit1입니다.
backend 선택·코드 서명·권한·helper 실행은 이 링크 검사로 검증하지 않습니다.

이 installer는 `launcher/nlc`가 없을 때 Python console script로 대체하지
않습니다. 먼저 `uv sync && ./build.sh`로 native checkout을 준비합니다.
다른 프로그램이나 스킬의 파일·심볼릭 링크는 보존하고 충돌을 보고합니다.
기존 Tacto 스킬 복사본을 연결로 바꿀 때는 skills 폴더 밖, 그 상위 폴더의
비공개 `.tacto-skill-backup.*/<이전 이름>` 아래에 원본과 로컬 추가 파일을 보관합니다.
이 installer는 두 도구의 기본 스킬을 함께 준비하는 것이 목적이므로
아직 사용하지 않는 도구의 skills 폴더도 생성합니다. 이전 checkout이 여전히
있으면 그 Tacto marker로 링크를 갱신합니다. 이전 checkout이 없어져 출처를
확인할 수 없는 깨진 링크는 자동 교체하지 않고 경로 확인을 안내합니다.
일부 연결만 성공해도 충돌이 남으면 exit1이므로 시작 앱은 완료로 표시하지
않습니다. 옵션은 `--check`와 `--help`이며, 오타나 추가 인자는 설치 전에
exit2로 거부합니다. PATH를 자동 변경하지 않으므로 필요한 경우
`~/.local/bin/tacto`의 절대 경로를 사용할 수 있습니다.

macOS UI 작업은 `tacto apps --helper`, `tacto observe <bundle-id> --helper`,
`tacto run <flow.json> --helper`, `tacto replay <name> --helper`,
`tacto do '<task>' --helper`로 signed helper를 선택합니다. 런처를 실행하더라도
이 명령들에서 flag를 생략하면 Python direct backend를 선택합니다.
`apps`와 `observe`도 같은 driver factory를 사용하며 signed 연결 실패 시
Python으로 대체하지 않습니다. Python 직접 경로는 별도의 개발용 선택입니다.

`tacto doctor --check`는 자체적으로 권한 경계를 확인합니다. `tacto eval`은
시나리오별 backend를 선택하므로 signed 시나리오를 지정하고, `--helper`를
평가 인자로 추가하지 않습니다. 스킬 예시와 실행 경로가 어긋날 때는 링크,
backend flag, launcher가 사용하는 `.venv`의 실제 `nlc.cli` import 경로를
함께 확인해야 합니다. 소스 checkout의 editable 설치라면 Python 변경은
즉시 반영되며 Swift launcher/helper 변경에는 별도 재빌드가 필요합니다.

### 드래그 방식 시작 앱 미리보기

네이티브 checkout을 빌드한 뒤, 권한 목록에 추가할 도우미를 드래그할 수
있는 시작 앱을 만들 수 있습니다. 출력 경로는 기존 파일을 덮어쓰지 않습니다.

```sh
.venv/bin/python setup/build_preview.py --output '/tmp/Tacto 시작하기.app'
```

빌드는 컴파일과 로컬 서명 검증만 수행하며 앱을 실행하거나 권한을 요청하지
않습니다.

앱을 열면 두 권한이 한 줄씩 표시됩니다. 각 줄에는 그 권한이 필요한 이유, 시스템
설정에서의 위치, 현재 상태가 나옵니다. 흐름은 ChatGPT 컴퓨터 사용 앱과 같은
방식입니다(조사 기록: [권한 받기 UX](permission-onboarding.md)).

1. 앱을 열면 helper의 권한 상태를 한 번 읽습니다. 이미 켜진 권한은 처음부터
   완료로 표시됩니다. 이 확인은 helper를 LaunchServices로 잠깐 실행해 결과만
   받는 읽기 전용 확인이며, 권한 요청·설정 열기·설치는 하지 않습니다.
2. **설정 열기**를 누르면 해당 설정 화면이 열리고, 시스템 설정 창 옆에 작은 도우미
   창이 붙습니다. 그 단계에 표시된 앱 아이콘을 설정 창의 앱 목록으로 끌어다 놓고
   스위치를 켜면 됩니다. 드래그를 지원하지 않는 목록에서는 `+` 버튼으로 같은
   도우미를 선택합니다.
3. 권한이 켜지면 자동으로 감지해 그 줄을 완료로 바꾸고 도우미 창을 닫습니다.
   **다시 확인**을 누를 필요가 없습니다. 이 감지는 사용자가 **설정 열기**를 누른
   뒤에만 1.5초 간격으로 최대 3분 동안 실행됩니다. 설정에서 돌아와 앱이 다시
   앞으로 오면 한 번 더 확인합니다.
4. 두 권한이 모두 켜지면 **에이전트에 연결**이 활성화됩니다. 다음 권한의 설정을
   자동으로 열지는 않으며, 각 단계는 사용자의 클릭으로 시작합니다.

현재 helper는 손쉬운 사용과 화면 기록 권한이 모두 있어야 일반 창 조작을
준비 완료로 표시합니다. 화면 기록은 조작 중임을 알리는 시스템 표시에도
사용됩니다. 시작 앱은 helper가 발행한 상태를 확인하고, 두 권한이 확인된
경우에만 명령·스킬 연결 버튼을 활성화합니다.

GUI를 띄우지 않고 각 상태의 화면을 PNG로 그려 디자인을 검토할 수 있습니다.
이 모드는 helper 실행, 설정 열기, 설치를 하지 않습니다.

```sh
swiftc setup/main.swift setup/PermissionState.swift -o /tmp/tacto-setup
/tmp/tacto-setup --snapshot /tmp/shots --granted control --waiting screen   # setup.png, panel.png
```

`setup/build_preview.py`가 만드는 시작 앱은 **checkout에 연결된 개발용 미리보기**입니다.
앱만 다른 컴퓨터로 복사해서 사용할 수는 없습니다.

Python과 네이티브 런타임을 포함하는 독립 실행 개발용 앱과 드래그 설치 이미지는
다음 명령으로 만들 수 있습니다. 빌드된 native checkout과 Apple 발급 서명 인증서가
필요합니다. `NLC_SIGN_IDENTITY`에는 `security find-identity -v -p codesigning`에서 확인한
인증서 식별자를 지정합니다. 출력 경로는 아직 존재하지 않는 경로를 지정하세요.

```sh
tacto_preview_dir="$(mktemp -d /private/tmp/tacto-preview.XXXXXX)"
uv run python setup/build_standalone_preview.py --identity "$NLC_SIGN_IDENTITY" --output "$tacto_preview_dir/Tacto.app" --report "$tacto_preview_dir/standalone-report.json"
uv run python setup/build_drag_preview.py --app "$tacto_preview_dir/Tacto.app" --output "$tacto_preview_dir/Tacto-preview.dmg" --report "$tacto_preview_dir/drag-report.json"
```

두 빌더는 앱을 실행하거나 설치하거나 권한을 변경하지 않습니다. DMG는 앱과
Applications 링크를 포함합니다. 실제 독립 앱 빌드·서명·이동 후 import 검사와
DMG 체크섬 검증은 통과했지만, **공증과 새 Mac에서의 Gatekeeper·권한 설정·드래그
설치는 아직 검증하지 않았습니다**. 일반 사용자 배포 완료를 의미하지 않습니다.
[수정·검증 결과](tacto-native-release-fixes-2026-10-16.md)를 참고하세요.

GUI를 열지 않고 권한 상태의 오류·준비 완료 조건, 감지 시간 제한, 도우미 창 배치를 검사할 수 있습니다.

```sh
swiftc setup/PermissionState.swift setup/state-check.swift -o /tmp/tacto-setup-state-check
/tmp/tacto-setup-state-check
```

Tacto 설치는 `tacto`와 이전 `nlc` 명령을 같은 서명된 런처에 연결합니다.
스킬 이름 전환과 저장 경로·권한 호환성은 [전환 안내](tacto-branding.md)를 확인하세요.

### 드래그로 권한용 앱 추가하기

‘설정 열기’를 누르면 설정 창 옆에 단계별 도우미가 나타납니다. 아이콘을 설정의
앱 목록에 끌어다 놓고 해당 앱 옆 스위치를 켭니다. 소스 checkout은 두 단계 모두
**NLC Helper.app**, 독립 실행 앱은 앱 조작에 **NLC Helper.app**, 화면 기록에
**Tacto.app**을 추가합니다. 각 단계의 ‘허용할 앱’을 확인하세요.
이미 목록에 있으면 다시 추가할 필요가 없습니다.

드래그가 되지 않으면 **다른 방법 보기 → Finder에서 이 앱 보기**로 실제 앱을
선택할 수 있습니다. + 버튼을 사용하려면 **이 앱 위치 복사**를 누르고, 파일 선택
창에서 Shift + Command + G → Command + V → Return으로 이동한 뒤 ‘열기’를
누릅니다. 권한이 확인되면 완료로 표시되고 ‘에이전트에 연결’이 활성화됩니다.

설치 이미지(DMG) 안에서 앱을 바로 열면 설정을 시작하지 않고, Tacto.app을
Applications로 옮긴 뒤 다시 열라고 안내하고 종료합니다. 이미지를 꺼내면 연결된
명령이 끊기기 때문입니다. 권한 확인이 실패하면(도우미 실행 실패·시간 초과 등)
이전의 완료 표시를 지우고 ‘에이전트에 연결’을 다시 잠급니다. 이 창으로 돌아오면
켜지지 않은 권한을 자동으로 다시 확인합니다.

독립 앱의 화면 기록은 macOS가 바깥 Tacto.app을 권한 소유자로 평가합니다.
helper만 추가했는데 화면 기록이 미완료라면, 화면 기록 단계에서 보여주는
**Tacto.app**을 위쪽 ‘화면 및 시스템 오디오 기록’ 앱 목록에 추가해야 합니다.
앱 번들에 화면 기록 권한을 주는 모델이며, 공유 Python이나 터미널 권한은
요청하지 않습니다. [권한 소유자와 드래그 안내](permission-onboarding.md)를 참고하세요.
