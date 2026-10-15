# Tacto onboarding

![Tacto 랜딩페이지 미리보기](../../docs/assets/tacto-preview.png)

Claude·Codex가 만든 computer-use Flow를 재사용하는 Tacto의 온보딩 랜딩페이지입니다.
한국어 제품 설명과 영어 브랜드 문구, 반응형 레이아웃, 실행 예시, 시작 안내를 제공합니다.

첫 화면과 사례 영역에서 실제 앱 창을 촬영한 MP4 2개와 원본 PNG를 제공합니다.
macOS 계산기와 자체 네이티브 테스트 앱을 구분하며, 재생 버튼·탐색·한국어 자막을 제공합니다.
영상은 자동 재생하지 않고 사용자가 재생할 때 로드합니다.
[촬영 조건·결과·한계](../../docs/tacto-recorded-demos-2026-10-15.md)를 참고하세요.

## 로컬 미리보기

저장소 루트에서:

```sh
python3 sites/tacto/serve.py
```

브라우저에서 http://127.0.0.1:8787 을 엽니다. 종료는 Ctrl+C입니다.
빌드 단계나 npm 의존성이 없습니다. 로컬 서버에는 macOS/Linux의 Python 3.9 이상이 필요합니다.

다른 기기에서 보려면 서버를 실행하는 컴퓨터의 LAN 주소를 지정합니다.

```sh
python3 sites/tacto/serve.py --host <LAN-IP> --port 8787
```

같은 네트워크에서 `http://<LAN-IP>:8787`로 접속합니다.
서버는 `dist/`의 정적 파일만 제공하며 폴더 목록·숨김 경로·심볼릭 링크는 제공하지 않습니다.
CSP·referrer·프레임·MIME 보호 헤더를 함께 보냅니다.
영상은 HTTP byte range로 탐색할 수 있습니다.
HTML·CSS·JS·SVG·JSON은 gzip을 협상하고, ETag가 같으면 본문 없이 304를 반환합니다.
파일 변경을 매번 재검증하므로 같은 이름의 새 로고나 스크립트가 오래 캐시되지 않습니다.
이 명령을 직접 실행한 경우 Ctrl+C로 종료합니다.

## 파일

- `dist/index.html`: 콘텐츠·측정 조건·온보딩·FAQ
- `dist/style.css`: 반응형 디자인·키보드 포커스·reduced motion
- `dist/app.js`: 설명용 실행 예시·에이전트 선택·명령 복사
- `dist/assets/benchmark-summary.json`: 2026-10-14 동일 조건 비교의 공개 요약
- `dist/assets/`: 외부 폰트 없이 그린 SVG 로고·워드마크·배너·아이콘
- `serve.py`: 정적 파일 전용 LAN 서버
- `qa.py`: Playwright 기반 키보드·복사·반응형·JavaScript 없는 화면 검사

실행 예시 버튼은 실제 앱·명령·모델을 호출하지 않습니다.
폼, 계정, 분석 추적, 서버 API는 없습니다. 폰트·스크립트·이미지는 외부 CDN에 요청하지 않으며 시스템 폰트와 같은 사이트의 파일만 사용합니다.
방문자의 값을 저장·전송하는 기능은 없습니다. 정적 호스팅 사업자는 별도로 접속 로그를 남길 수 있습니다.
페이지는 CSP로 외부 연결을 차단하고 외부 링크에 referrer를 보내지 않습니다.

## 정적 호스팅

`dist/`의 내용을 정적 호스트의 공개 루트에 그대로 올리면 됩니다.
상대 경로를 사용하므로 하위 경로에서도 제공합니다. 별도의 rewrite 설정은 필요하지 않습니다.
공개 배포 시 실제 URL에 맞춰 canonical·Open Graph URL/공유 이미지를 설정할 수 있습니다.

현재 저장소에 배포 설정이나 공개 사이트 URL을 만들지는 않았습니다.
설치 안내는 GitHub의 문서를 연결하고, 아직 게시되지 않은 측정 기록은 페이지 내 조건과 JSON 요약으로 제공합니다.

이전 이름·명령·권한의 호환성은 [전환 안내](../../docs/tacto-branding.md)를 참고하세요.


## 브라우저 회귀 검사

테스트 환경에 Playwright와 Chromium을 준비한 뒤 서버를 실행하고 검사합니다.
Playwright는 QA 도구이며 페이지가 내려받는 의존성이 아닙니다.

```sh
python3 sites/tacto/qa.py --url http://127.0.0.1:8787 --output /tmp/tacto-page-qa
```

이미 준비한 axe-core의 `axe.min.js`가 있으면 `--axe /path/to/axe.min.js`로
자동 접근성 검사를 추가합니다. 파일을 제공하지 않으면 axe 검사는 생략합니다.
검사는 임시 브라우저를 사용하며 개인 브라우저 프로필에 연결하지 않습니다.
결과 JSON과 화면 캡처를 출력 폴더에 저장합니다. 자동 검사 통과가 모든 접근성
요구사항의 수동 검토를 대신하지는 않습니다.
