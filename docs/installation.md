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
`skill/install.sh --check`는 링크만 확인하며 backend 선택을 검증하지 않습니다.

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
