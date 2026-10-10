# 설치 범위와 실행 경로

Python wheel은 NLC의 Python 코어를 배포합니다. macOS 앱 조작에 필요한
서명된 런처·도우미, Chromium 실행 파일, 평가 fixture는 포함하지 않습니다.
`py3-none-any` wheel이라는 표시는 Python 패키지의 형식이며, 모든 운영체제의
데스크톱을 조작한다는 뜻이 아닙니다.

| 설치 방식 | 사용할 수 있는 범위 | 별도로 필요한 것 |
| --- | --- | --- |
| Python wheel | flow IR, 계약·결과 검증, 후보 paging, 모델 adapter, trace 등 Python 코어 | Python 3.11 이상 |
| wheel + `web` extra | Playwright 기반 별도 Chromium 페이지의 BrowserDriver | Playwright가 설치하는 Chromium과 운영체제 실행 의존성 |
| macOS source checkout | 서명된 런처를 통한 macOS 접근성 조작 | Xcode Command Line Tools, 코드 서명 identity, 손쉬운 사용 권한 |

## Python 코어와 브라우저

저장소 루트에서 wheel을 빌드하고 별도 환경에 설치할 수 있습니다.

```sh
uv build --wheel
python3 -m venv /tmp/nlc-client
/tmp/nlc-client/bin/python -m pip install ./dist/nlc-0.1.0-py3-none-any.whl
/tmp/nlc-client/bin/nlc --help
```

브라우저가 필요한 환경에서는 같은 wheel에 `web` extra를 설치합니다.

```sh
/tmp/nlc-client/bin/python -m pip install './dist/nlc-0.1.0-py3-none-any.whl[web]'
/tmp/nlc-client/bin/python -m playwright install chromium
```

Linux에는 Chromium이 요구하는 시스템 라이브러리가 추가로 필요할 수 있습니다.
Python 패키지 설치나 import 성공은 Chromium 실행 검증과 다릅니다. 현재
일반 `nlc do` 명령은 macOS 앱 조작 경로이므로 브라우저용 CLI로 간주하지
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

서명된 실행 경계는 소스 설치를 사용합니다. 저장소 루트에서 다음 순서로
설치합니다. `./build.sh`는 로컬 서명 identity로 런처와 도우미를 빌드하므로
wheel 설치 과정에서 자동 실행하지 않습니다.

```sh
uv sync
# 필요한 경우 사용할 코드 서명 identity를 명시합니다.
# export NLC_SIGN_IDENTITY='사용할 코드 서명 identity'
./build.sh
./launcher/nlc doctor
./launcher/nlc do '계산기로 12 곱하기 34' --helper
```

런처는 자신의 위치를 기준으로 저장소 루트의 `.venv/bin/python`, `helper/`
번들을 찾습니다. 이 디렉터리 배치를 유지해야 하며 `launcher/nlc` 파일만
다른 경로로 복사하면 설치가 되지 않습니다. 코드 서명 identity가 없으면
런처 빌드는 실패합니다. 도우미 파일을 wheel에 복사하거나 임의로 재서명해
설치 문제를 우회하지 않습니다.

wheel의 `nlc` console script는 Python CLI입니다. 서명된 `./launcher/nlc`와
같은 프로그램이 아니며 wheel만 설치해서 `--helper` 채널을 얻을 수는 없습니다.
네이티브 `doctor`, `apps`, `observe`, `do`, `run`, `replay`에는 macOS 실행
환경과 해당 경로의 권한이 필요합니다.

## 평가와 설치 검증

`nlc eval`의 평가 fixture는 소스 checkout에 있으므로 wheel에 포함되지
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

`skill/install.sh`는 `~/.local/bin/nlc`를 이 checkout의 signed launcher에,
Claude/Codex의 NLC 스킬을 이 checkout의 `skill/`에 연결합니다.
새 사용자 환경에는 기본 Claude/Codex skills 폴더를 만들고, 명시한
`CLAUDE_CONFIG_DIR`·`CODEX_HOME`의 skills 폴더도 연결합니다.
`skill/install.sh --check`는 폴더를 만들거나 링크를 바꾸지 않으며,
연결이 모두 맞으면 exit0, 누락·오래된 연결·충돌이면 exit1입니다.
backend 선택·코드 서명·권한·helper 실행은 이 링크 검사로 검증하지 않습니다.

이 installer는 `launcher/nlc`가 없을 때 Python console script로 대체하지
않습니다. 먼저 `uv sync && ./build.sh`로 native checkout을 준비합니다.
다른 프로그램이나 스킬의 파일·심볼릭 링크는 보존하고 충돌을 보고합니다.
기존 NLC 스킬 복사본을 연결로 바꿀 때는 skills 폴더 밖, 그 상위 폴더의
비공개 `.nlc-skill-backup.*/nlc` 아래에 원본과 로컬 추가 파일을 보관합니다.
이 installer는 두 도구의 기본 스킬을 함께 준비하는 것이 목적이므로
아직 사용하지 않는 도구의 skills 폴더도 생성합니다. 이전 checkout이 여전히
있으면 그 NLC marker로 링크를 갱신합니다. 이전 checkout이 없어져 출처를
확인할 수 없는 깨진 링크는 자동 교체하지 않고 경로 확인을 안내합니다.
일부 연결만 성공해도 충돌이 남으면 exit1이므로 시작 앱은 완료로 표시하지
않습니다. 옵션은 `--check`와 `--help`이며, 오타나 추가 인자는 설치 전에
exit2로 거부합니다. PATH를 자동 변경하지 않으므로 필요한 경우
`~/.local/bin/nlc`의 절대 경로를 사용할 수 있습니다.

macOS UI 작업은 `nlc apps --helper`, `nlc observe <bundle-id> --helper`,
`nlc run <flow.json> --helper`, `nlc replay <name> --helper`,
`nlc do '<task>' --helper`로 signed helper를 선택합니다. 런처를 실행하더라도
이 명령들에서 flag를 생략하면 Python direct backend를 선택합니다.
`apps`와 `observe`도 같은 driver factory를 사용하며 signed 연결 실패 시
Python으로 대체하지 않습니다. Python 직접 경로는 별도의 개발용 선택입니다.

`nlc doctor --check`는 자체적으로 권한 경계를 확인합니다. `nlc eval`은
시나리오별 backend를 선택하므로 signed 시나리오를 지정하고, `--helper`를
평가 인자로 추가하지 않습니다. 스킬 예시와 실행 경로가 어긋날 때는 링크,
backend flag, launcher가 사용하는 `.venv`의 실제 `nlc.cli` import 경로를
함께 확인해야 합니다. 소스 checkout의 editable 설치라면 Python 변경은
즉시 반영되며 Swift launcher/helper 변경에는 별도 재빌드가 필요합니다.

### 드래그 방식 시작 앱 미리보기

네이티브 checkout을 빌드한 뒤, 권한 목록에 추가할 도우미를 드래그할 수
있는 시작 앱을 만들 수 있습니다. 출력 경로는 기존 파일을 덮어쓰지 않습니다.

```sh
.venv/bin/python setup/build_preview.py --output '/tmp/NLC 시작하기.app'
```

빌드는 컴파일과 로컬 서명 검증만 수행하며 앱을 실행하거나 권한을 요청하지
않습니다. 사용자가 앱을 열면 도우미 아이콘, 권한 설정·Finder 열기,
권한 다시 확인, 명령·스킬 연결 버튼이 표시됩니다. 드래그를 지원하지 않는
권한 목록에서는 설정의 `+` 버튼으로 같은 도우미를 선택합니다.
권한은 사용자가 직접 켭니다. 앱을 열기만 해서는 도우미 실행, 설정 열기,
스킬 설치가 시작되지 않습니다.

현재 helper는 손쉬운 사용과 화면 기록 권한이 모두 있어야 일반 창 조작을
준비 완료로 표시합니다. 화면 기록은 조작 중임을 알리는 시스템 표시에도
사용됩니다. 시작 앱은 helper가 발행한 상태를 확인하고, 두 권한이 확인된
경우에만 명령·스킬 연결 버튼을 활성화합니다.

이 앱은 **checkout에 연결된 개발용 미리보기**입니다. checkout의 절대 경로를
기억하므로 앱만 다른 컴퓨터로 복사하면 설치되지 않습니다. 독립 런타임 묶음,
배포 서명·공증, 새 Mac에서의 권한·Gatekeeper 검증과 실제 화면 배치·드래그
검증은 별도로 필요합니다. macOS와 빌드된 네이티브 checkout이 필요합니다.

GUI를 열지 않고 권한 상태의 오류·준비 완료 조건을 검사할 수 있습니다.

```sh
swiftc setup/PermissionState.swift setup/state-check.swift -o /tmp/nlc-setup-state-check
/tmp/nlc-setup-state-check
```
