# Tacto onboarding

![Tacto 랜딩페이지 미리보기](../../docs/assets/tacto-preview.png)

Claude·Codex가 만든 computer-use Flow를 재사용하는 Tacto의 온보딩 랜딩페이지입니다.
한국어 제품 설명과 영어 브랜드 문구, 반응형 레이아웃, 실행 예시, 시작 안내를 제공합니다.

## 로컬 미리보기

저장소 루트에서:

```sh
python3 -m http.server 8787 --bind 127.0.0.1 --directory sites/tacto/dist
```

브라우저에서 http://127.0.0.1:8787 을 엽니다. 종료는 Ctrl+C입니다.
빌드 단계나 npm 의존성이 없습니다.

## 파일

- `dist/index.html`: 콘텐츠·측정 조건·온보딩·FAQ
- `dist/style.css`: 반응형 디자인·키보드 포커스·reduced motion
- `dist/app.js`: 설명용 실행 예시·에이전트 선택·명령 복사
- `dist/assets/benchmark-summary.json`: 2026-10-14 동일 조건 비교의 공개 요약
- `dist/assets/`: SVG 배너·아이콘

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
