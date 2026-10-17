# Tacto 이름 전환과 호환성

NLC의 제품 이름을 **Tacto**로 변경했습니다. Tacto는 에이전트가 작성한 computer-use
Flow를 재사용하는 실행 계층입니다. 문서·웹·CLI·에이전트 스킬의 제품 표기를 통일합니다.

| 사용자 접점 | 현재 이름 | 호환성 |
| --- | --- | --- |
| 제품·메뉴 막대 | Tacto / ● Tacto | 재빌드한 실행기에 반영 |
| 명령 | tacto | nlc도 같은 signed launcher를 실행 |
| 에이전트 스킬 | tacto | 설치기가 소유를 확인한 예전 NLC 스킬을 발견 경로 밖에 백업 |
| Python 배포 | tacto-runtime | import nlc 유지; PyPI의 다른 tacto 패키지와 구분 |
| 웹페이지 | sites/tacto/dist | 예전 사이트 README는 새 경로 안내 |

기존 Python namespace, Flow schema·데이터 경로, NLC_* 환경 변수,
`ai.callabo.nlc.*` 서명 식별자와 `helper/NLC Helper.app` 경로는 유지합니다.
이름 변경만으로 기존 권한을 잃거나 저장 Flow를 다른 저장소로 이동시키지 않습니다.
새 빌드는 `InfoPlist.strings`의 지역화된 앱 이름을 **Tacto Helper**로
표시합니다. Finder가 지역화 이름을 쓰도록 기본 plist 이름은 내부 폴더 이름과
일치시킵니다. 서명 bundle ID·요구조건·실행 파일·번들 경로는 유지합니다.
GUI와 `doctor`는 설치된 번들의 실제 표시 이름을 읽습니다. 따라서 Preview 1이나
이전 설치가 **NLC Helper**이면 그 이름을 그대로 안내하며, 아직 교체하지 않은
배포물을 Tacto Helper로 바꿨다고 주장하지 않습니다. macOS 설정 목록은 이름을 캐시할 수 있어 이전 이름으로 남을 수 있습니다.
LaunchServices 캐시를 자동으로 지우지 않습니다. 표시 이름 변경 뒤 기존
권한이 유지되는지는 실제 업그레이드에서 별도로 검증합니다.

`./skill/install.sh`는 tacto와 nlc 명령을 서명된 `launcher/nlc`에 연결하고
Claude·Codex의 tacto 스킬을 설치합니다. 다른 프로그램·스킬은 덮어쓰지 않습니다.
소유를 확인한 예전 NLC 스킬은 skills 폴더 밖 `.tacto-skill-backup.*`에 보관해
중복 발견을 피합니다. `--check`는 읽기 전용입니다.

GitHub 저장소 이름도 `Open330/tacto`로 변경했습니다. clone·문서 링크와
Git remote를 새 주소로 갱신했습니다. 저장소 공개와 라이선스 변경은 별도입니다. 과거 측정의 nlc 라벨과 수치는 원본대로 보존합니다.
자동 수리·batch 등 아직 미배포인 개발 버전 기능은 해당 안내에 표시합니다.

Tacto라는 기존 제품과 Python 패키지가 있습니다. 이 변경은 상표·도메인·패키지
사용 가능성이 검증됐다는 의미가 아니며, 외부 서비스와의 제휴를 뜻하지 않습니다.

검증: 새 사용자 설치·이전 스킬 백업·동명이인 프로그램 보존·읽기 전용 확인,
별도 wheel의 두 CLI 동일 동작, 웹 인터랙션·모바일·외부 요청 차단을 확인했습니다.
직접 조작하는 기존 앱 작업을 전부 재평가한 결과나 상표 권리 검증은 아닙니다.

## 앱 아이콘과 메뉴 막대 자산

| 용도 | 파일 |
| --- | --- |
| macOS 앱 아이콘 | `docs/assets/tacto-app.icns` (16–1024 px, 10개 크기) |
| 아이콘 벡터 원본 | `docs/assets/tacto-app-icon.svg` (64 px 이상), `docs/assets/tacto-app-icon-small.svg` (16·32 px) |
| 설정 메뉴 막대 template | `docs/assets/tacto-menu-template.svg` |

- 로고, 워드마크, 파비콘, 탐색 시트, 세 파일과 `.icns`는 모두 `python3 setup/build_brand_assets.py`가 만듭니다. SVG를 직접
  고치지 말고 스크립트 상수를 고친 뒤 다시 생성합니다. `--check`는 렌더링 없이
  추적 중인 SVG가 생성기와 같은지만 확인합니다. 같은 Mac에서 다시 생성하면 `.icns`가
  바이트 단위로 같습니다.
- 마크는 2026-10-17부터 **포인터 t**입니다. 코랄 포인터의 끝이 t 가로획의 시작점을
  찍습니다. 에이전트가 앱을 클릭하면 작성한 경로가 거기서 이어진다는 뜻입니다.
- 아이콘은 잉크(`#1b211c`) 타일 위의 아이보리 *t*와 코랄 포인터입니다. 마크 모양은
  `tacto-logo.svg` 그대로이고, macOS 아이콘 격자(1024 캔버스 안의 824 px 본체)를 따릅니다.
  파비콘도 같은 잉크 타일을 씁니다.
- 16·32 px은 획을 4.2로 굵게 그리는 소형 광학 크기를 씁니다. 포인터를 가로획에서 떼거나
  돌리지 않습니다. 마크를 커서나 작업 중 표시로 쓰지 않습니다.
- 메뉴 막대의 상시 아이콘은 **설정**을 뜻합니다. 두 개의 슬라이더와 속이 빈 손잡이로
  그리고, 채운 점은 쓰지 않습니다. 작업 중 표시 `● Tacto`와 혼동되지 않게 하려는 것입니다.
  검정과 투명도만 쓰므로 `NSImage.isTemplate = true`로 불러야 밝은·어두운 메뉴 막대에서
  시스템이 색을 맞춥니다.
- 번들 ID와 서명 식별자는 바꾸지 않습니다. 아이콘 교체는 권한이나 저장 Flow에 영향을 주지 않습니다.
