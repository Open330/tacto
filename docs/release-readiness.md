# Tacto 출시 기준

현재 배포 단계는 **개발자 미리보기**입니다. 사용자의 요청에 따라 공증된
Preview 1을 prerelease로 제공하며, 미완료 검증과 알려진 한계를 함께 명시합니다.
소스 공개와 미리보기 제공은 정식 안정 버전의 출시 승인이 아닙니다.

아래 조건은 **정식 안정 배포**의 기준입니다. 로컬 실행·서명·공증만으로
이 기준을 통과한 것으로 처리하지 않습니다. 출시 후보를 정한 뒤 아래 조건을
모두 통과한 **동일한 DMG**만 정식 안정 버전으로 배포합니다.
기능 추가와 미관 개선은 다음 후보로 넘깁니다.

## 첫 출시 범위

- Apple Silicon Mac용 독립 앱, signed helper, Claude Code·Codex 스킬 연결.
- 사용자가 시스템 설정에서 직접 허용하는 앱 조작·화면 기록 권한.
- Finder의 폴더 이동·파일 선택, Safari의 검색·페이지 이동, TextEdit의 임시
  문서 입력·저장. 각 앱의 실제 검증 버전과 macOS 버전을 결과에 기록합니다.
- 저장한 flow의 재생, 결과 확인, 실패 시 중단·제한된 수리. 지원하지 않는
  화면·캡챠·인증은 사람에게 넘깁니다.

Windows·Linux 데스크톱, Intel Mac, 모든 앱의 자동 복구, 무인 인증,
결제·발송·삭제 같은 되돌리기 어려운 작업은 첫 출시의 검증 범위에 넣지 않습니다.
위 앱들도 아래 실증을 마치기 전에는 지원 완료라고 표기하지 않습니다.

## 출시를 차단하는 조건

| 검사 ID | 통과 조건 | 현재 확보한 근거와 남은 일 |
|---|---|---|
| `fresh_install` | 개발 환경이 없는 별도 Mac에서 DMG 복사 → 첫 실행 → 사용자 권한 설정 → Claude·Codex 각각 새 대화의 첫 작업 완료. 터미널 설치 우회 없이 확인 | 현재 Mac 권한·연결 확인 완료. 새 Mac 전체 경로는 미검증 |
| `real_app_tasks` | 위 실제 앱마다 대표 작업 2개를 각각 10회 실행, 총 60/60 결과 확인. 앱·OS·모델 버전, 실패 시도도 기록 | 60회 실행: Finder·Safari 40회 통과, TextEdit 저장 20회 `needs_foreground`. 미충족 |
| `failure_recovery` | 창 닫힘·화면 지연·대상 변경·권한 해제·flow 재생 중 실패를 각 3회. 잘못된 대상 입력·확인 없는 중복 실행 0건, 중단 사유와 재개 경로 확인 | 실제 창 닫힘·지연·이름 변경·중간 실패 12/12 통과. 권한 해제 3회 미실행으로 부분 충족 |
| `upgrade_rollback` | 이전 후보 → 새 후보 → 이전 후보 복귀 1회씩. 저장 flow·설정 보존, 링크·서명·권한 상태 확인. 권한 재요청 여부 기록 | 실제 명령·두 스킬 연결의 업그레이드·복귀 통과. 원래 설치 연결 복원, 저장 flow·설정·기존 권한 보존 |
| `performance` | 같은 조건 10쌍 비교. 성공률 하락 없음, flow 중앙값·합계 및 관찰 중앙값이 승인된 기준보다 15% 넘게 악화되지 않음. 모델·토큰 비교는 별도 조건 명시 | 2026-10-17 재생 비교 통과. 최종 후보와 런타임 동일성 확인 필요 |
| `regressions_security` | 전체 자동 테스트에서 미해결 실패 0건. skip 사유 검토, staged diff·배포 파일의 비밀·개인정보 점검. 동료 검토 완료 | Preview 2 전체 1,550개 검사 실패 0건, 87개 skip. 추가 실행·보안 검토는 아래 기록 참고 |
| `limited_beta` | 최소 5명이 3일간 사용, 각자 실제 작업 3개 이상. 모든 실패를 기록하고 출시 차단 결함 0건 | 아직 수행하지 않음 |

횟수는 첫 출시의 최소 운영 기준이며 무결점이나 통계적 신뢰도를 보장하지
않습니다. 실패를 제외하고 다시 계산하지 않습니다. 잘못된 대상 조작, 원치 않는
중복 실행, 사용자 데이터 손상·노출, 설치 불가, 서명·권한 경계 오류는 차단
결함입니다. 작은 표시·정렬 문제는 재현 방법과 우회가 있으면 다음 버전으로
이월할 수 있습니다. 검증 결과가 불분명하면 통과로 바꾸지 않습니다.

## 최종 산출물 검사

`setup/check_release.py`는 실제 DMG를 읽기 전용으로 마운트해 앱 파일을 원본과
비교하고, 앱·런처·helper 서명, DMG 체크섬, Applications 링크를 확인합니다.
검사 후 이미지를 분리합니다. 앱 실행·설치·권한 요청은 하지 않습니다.
포함된 앱과 DMG 각각의 Gatekeeper 승인·공증 ticket, 위 검토 근거가 없으면
exit 1과 `blocked`를
기록합니다. 설치 검사만 통과했다고 공개 승인을 만들지 않습니다.

```sh
# 출력의 부모 폴더는 본인 소유의 절대·실제 경로, 권한 700이어야 합니다.
python3 setup/check_release.py --app /path/to/Tacto.app \
  --image /path/to/Tacto-preview.dmg --output /private/path/release-check.json
```

검토 근거는 로컬 JSON에 기록하고 `--evidence /private/path/evidence.json`으로
제공합니다. `image_sha256`은 해당 DMG의 SHA-256이어야 합니다. `gates`에는 위
7개 ID를 모두 넣고, 각 항목에 다음 형식을 사용합니다.

```json
{
  "status": "passed",
  "reviewer": "검토자",
  "summary": "실행 환경, 시도·성공·실패 수와 판정 근거",
  "artifacts": [{"path": "/absolute/local/result.json", "sha256": "파일 SHA-256"}]
}
```

도구는 근거 파일의 존재와 해시를 검사합니다. 내용의 진실성·검사 조건 충족은
검토자가 확인해야 합니다. 보고서·화면·계정 경로는 비공개로 보관하고, 공유용
요약에는 개인정보를 제거합니다. 근거 없이 `passed`를 작성하지 않습니다.

## 동결과 복구

후보 ID, 소스 커밋, DMG SHA-256, 검사 결과를 함께 기록합니다. 앱 파일을
바꾸면 새 후보로 취급하고 산출물 검사와 영향을 받는 시나리오를 다시 실행합니다.
근거가 같은 DMG에 묶이므로 이전 승인을 자동 승계하지 않습니다.

이전의 검증된 DMG와 flow 백업을 보존합니다. 자동 업데이트는 첫 출시 범위에
없습니다. 문제 발생 시 앱을 종료하고 이전 앱을 별도 경로로 복원한 뒤 해당
버전의 연결 절차를 실행해 명령·스킬을 되돌립니다. 저장 flow는 덮어쓰지 않습니다.
이 복귀 절차도 `upgrade_rollback` 실증 전에는 검증 완료로 보지 않습니다.

공개 배포는 Developer ID 서명·공증·Gatekeeper와 위 실증이 모두 준비된 후에
진행합니다. 현재 Apple Development 서명 미리보기를 일반 사용자용 출시본으로
바꾸어 부르지 않습니다.

## 2026-10-17 후보 검사 기록

검사 대상은 성능 개선 런타임의 개발 미리보기입니다. 소스 기준은 `7bc60a1`,
DMG SHA-256은 `397311851332238df16072462cf95efa06fbbe2a736dc61320944dce3cfbaea5`입니다.
이 문서·출시 검사 도구 추가는 해당 DMG를 변경하지 않습니다.

- 실제 DMG 읽기 전용 검사: 원본·포함 앱 서명, 파일 동일성, Applications 링크,
  볼륨 구성, 체크섬과 검사 후 원본 보존·마운트 분리 통과.
- 포함 앱·DMG의 Gatekeeper 승인과 공증 ticket 모두 없음. 현재 환경의
  Developer ID Application 인증서도 0개로 확인. 공개 배포는 차단 상태.
- 전체 자동 검사 1,518개: 실패 0건, skip 87개. 이 중 84개는 Playwright 의존성
  부재였습니다. 해당 모듈을 의존성이 갖춰진 환경에서 재실행해 **115개 모두
  통과(skip 0건)**했습니다. 나머지는 실제 화면 조작·wheel 설치·일회용
  서명 검사를 명시적으로 켜야 하는 항목입니다.
- wheel을 checkout 밖 새 환경에 설치하는 검사와 실제 임시 바이너리의 서명
  경계 검사도 각각 통과. 실제 화면 조작 opt-in은 이 기록에서 실행하지 않았습니다.
- 전체 검사에서 발견한 오래된 테스트 4개를 현재 계약에 맞췄습니다. 문서 token
  없이 앱 전체를 읽거나 실제 모델을 호출하도록 바꾸지 않았습니다. 동료 검토 승인.

이 기록 이후의 실제 앱·장애·복귀 실행은 아래에 구분합니다. 새 Mac 설치,
배포 파일 보안 검토, 5명 베타는 미완료입니다.

## 실제 앱과 복귀 검증

환경: macOS 27.0.1, Finder 27.0, Safari 27.0.1, TextEdit 1.21.
모델 호출 없이 작성한 flow를 signed helper로 재생했습니다. 에이전트의
자연어 작성 성능이나 자동 모델 수리 성공률을 측정한 것은 아닙니다.

| 실제 작업 | 시도 | 완료 | 결과 |
|---|---:|---:|---|
| Finder: 소유한 임시 폴더 이동 | 10 | 10 | 새 관찰에서 폴더와 파일 확인 |
| Finder: 파일 하나 선택 | 10 | 10 | 파일 목록에 선택된 행이 정확히 하나인지 확인 |
| Safari: 공개 Wikipedia 검색 | 10 | 10 | Computer hardware 페이지의 제목·heading 확인 |
| Safari: 공개 링크로 페이지 이동 | 10 | 10 | Computer 페이지의 제목·heading 확인 |
| TextEdit: 한 줄 편집 후 저장 | 10 | 0 | 편집은 관찰했으나 저장 메뉴가 비활성. `needs_foreground` |
| TextEdit: 여러 줄·한글·emoji 편집 후 저장 | 10 | 0 | 같은 저장 제약. 사람 개입 전 완료로 세지 않음 |

최초 실행은 **40/60**입니다. 이후 사용자가 TextEdit를 앞으로 가져온 뒤,
앞서 실패한 저장 작업 20개를 명시적으로 재시도해 **20/20 저장·파일 내용
확인을 통과**했습니다. 최초 20회 실패를 유지하므로 이 두 묶음은 80회 시도 중
60회 성공입니다. 무인 60/60 통과로 바꾸지 않으며 공개 출시 기준은 미충족입니다.
첫 파일럿의 실패와 후보 변경
기록도 비공개 원자료에 보존합니다. 새 후보의 성공만 모아 기존 실패를 지우지
않았습니다. 저장은 정확한 `.txt` 파일 내용과 typed receipt로만 판정하며,
`set_value`의 `commit_unverified`를 저장 성공으로 바꾸지 않습니다.

실제 장애는 네 종류를 각 3회 확인했습니다. 빈 소유 TextEdit 창을 실제로 닫은
뒤 입력 거부·다시 열어 읽기, 지연해서 나타나는 창 기다리기, Finder에서 실제
파일 이름 변경 후 이전 선택자 거부·새 관찰로 재선택, flow 중간 실패 후 뒤의
단계를 실행하지 않고 새 flow로 이어가기를 검증했습니다. 거부 단계 입력 0회,
지연 중 추가 변경 입력 0회, 완료한 앞 단계 재전송 없음이 trace에서 확인됐습니다.
복구는 테스트 호스트가 정한 절차이며 범용 모델 자동 수리의 증거는 아닙니다.
시스템 권한은 변경하지 않았으므로 권한 해제·재부여 실증 3회는 남습니다.

이전 설치와 수정 후보 사이에서 실제 `tacto`·`nlc` 명령, Claude·Codex 스킬 연결을
갱신하고 복귀했습니다. 실제 사용자 저장 위치에 전용 flow 하나를 독점 생성해
각 버전의 해석·원본 파일 해시를 확인하고, 내용이 같을 때만 그 테스트 파일을
제거했습니다. 기존 flow·설정은 해시가 유지됐고 두 권한도 재요청하지 않았습니다.
검사 끝에는 **검사 전의 설치 연결을 복원**했습니다. 새 Mac이나 공증된 배포본의
업데이트 실증을 의미하지 않습니다.

발견해 수정한 사항:

- signed helper의 여러 줄 label을 Python과 같이 한 줄로 이스케이프합니다.
  뒤의 Identifier가 사라지거나 값이 가짜 tree 행이 되는 문제를 막습니다.
- Finder 목록 행은 부모의 `AXSelectedRows=[row]`로 선택을 교체한 후 정확히
  한 행인지 재확인합니다. 실패하면 포인터 입력으로 재시도하지 않습니다.
- 실제 폴더형 문서의 단일 끝 슬래시를 정규화합니다. 다른 RTFD 문서가 열려
  있어도 지정한 `.txt` 읽기를 막지 않습니다. 비정규 경로 검사는 유지합니다.
- `observe --document`는 비어 있지 않은 문서도 제한된 예산 안에서 읽어 pin을
  얻습니다. 오류 원문 대신 정해진 짧은 사유만 전달합니다.
- `subrole` 선택자로 소유 창의 닫기와 최소화 버튼을 구분합니다. OS 상수용이며
  템플릿 값은 지원하지 않습니다. 사용하지 않는 `subrole`은 JSON에서 생략해
  기존 flow를 이전 후보에서도 읽을 수 있게 합니다. 이 새 필드를 실제로 쓰는
  flow는 이를 지원하는 버전이 필요합니다.

전체 회귀 검사: **1,521개, 실패 0건, skip 87개**. 이전 브라우저 추가 검사
115개 통과 결과는 해당 이전 소스의 근거이며 이번 후보에서 재실행한 것으로
세지 않습니다. 동료 검토를 통과했습니다.

재현 도구는 `examples/validate_native_release.py`, `validate_native_faults.py`,
`validate_upgrade_rollback.py`입니다. `--help`로 필수 경로를 확인하세요. 새 비공개
출력 폴더를 사용하고, 테스트 파일을 연다는 점과 실제 설치 연결을 바꿨다가
원래대로 복원한다는 점을 확인한 뒤 실행합니다. 보고서는 앱 manifest, 모듈 해시,
launcher/helper/Python CDHash로 실행 대상을 구분합니다. **다른 후보의 근거를
최종 DMG의 통과 근거로 자동 승계하지 않습니다.**

사용자가 Vault 잠금을 해제한 뒤, 기존 Developer ID 인증서 백업과 공증 키
기록을 확인했습니다. 세션은 보호된 도구 경로로 연결했고, 인증서·개인키는
비밀값을 출력하거나 명령 인자에 넣지 않고 macOS Security API로 복원했습니다.
이 Developer ID는 기존 개발용 서명과 팀이 다릅니다. 따라서 이전 권한 유지·
앱 실행 결과를 새 서명 후보의 통과 근거로 자동 승계하지 않습니다. 새 서명
후보는 현재 설치와 별도로 두며, 공증과 Gatekeeper 결과는 최종 산출물 보고서로
판정합니다. 새 Mac·권한·베타 조건이 남아 공개 배포는 계속 차단 상태입니다.

### 사람 개입 후 저장 재개와 배포 서명

사용자가 TextEdit를 앞으로 가져온 뒤, 최초에 보류한 단일·다중 줄 저장 작업을
각 10회 재실행했습니다. **20/20**에서 저장 receipt와 디스크의 정확한 본문이
일치했습니다. 사람 개입 없이 완료한 40회와 사람 개입 뒤 재시도한 20회를
구분하며, 처음 실패한 20회를 지우지 않습니다. 전경 복귀는 사용자가 수행했고
Tacto가 임의로 앱을 활성화하거나 비활성 메뉴를 강제로 실행하지 않았습니다.

Vault에 보관된 기존 Developer ID Application 인증서와 공증 키를 재사용했습니다.
개인키·인증서 암호는 소비 프로세스의 표준 입력과 Security API로 전달했으며,
저장소·앱 번들·명령 인자에 넣지 않았습니다. 각 native 파일을 hardened runtime과
보안 타임스탬프로 다시 서명한 별도 후보를 만들었습니다. 현재 설치는 교체하지
않았습니다. 이 서명의 팀은 앞서 검증한 개발용 서명과 다르므로 새 서명 후보의
앱 권한과 실제 GUI 실행은 별도로 검증해야 합니다.

Apple 심사 결과: **앱·DMG 모두 Accepted**, 두 ticket의 staple·validate와
각각의 Gatekeeper 검사를 통과했습니다. DMG SHA-256:
`83f66f77237d3904bd4fd056e60ada07d67b9633da584780baa607072a8f38a8`.
배포 서명·공증 조건을 충족했으며, 새 서명 팀의 실제 권한 설정·GUI 작업,
새 Mac 설치·권한 해제 시나리오·베타 검증을 통과했다는 뜻은 아닙니다.
공개 출시 승인은 계속 차단합니다.


## 2026-10-18 Preview 2

사용자가 승인한 비공개 개발 미리보기입니다. 런타임 소스 `64cacfe`, 배포 버전
`0.1.0` build `2`, Apple Silicon·macOS 27.0.1에서 확인했습니다. 저장소와 다운로드는
GitHub 접근 권한이 필요하며 정식 안정 버전이나 일반 공개 승인으로 취급하지 않습니다.

- Tacto 이름·아이콘, 읽기 쉬운 연결 버튼과 접근성 이름, 업데이트 권한 안내를 반영했습니다.
- 권한 도우미는 설정 본창이 보인 뒤 표시하고, 본창을 고정해 암호·Touch ID 창을 따라가지 않습니다.
- 빌드 Mac에서 실제 설치·두 권한·Claude/Codex 연결과 같은 팀의 업데이트 뒤 권한 보존을 확인했습니다.
- 전체 자동 검사 1,550개: 실패 0건, 선택적 의존성·명시적 실행 조건에 따른 87개 skip.
- 앱·DMG 모두 Apple 공증 Accepted, ticket staple·validate와 각각의 Gatekeeper 검사를 통과했습니다.
- 새 Mac 설치, 다른 macOS·Intel·Windows, 전체 실제 앱·실패 복구·성능·베타의 최종 후보 재검증은 미완료입니다.
- 과거 PR·커밋 참조가 여전히 조회되어 저장소를 Private으로 유지합니다. 공개 전 GitHub 쪽 잔존 참조 정리가 필요합니다.

DMG SHA-256: `05f71d93c2bc5f66d16112c5bd1b848f8da284b1a4b7e43cda503bfa545e726a`.
[Preview 2 릴리스](https://github.com/Open330/tacto/releases/tag/v0.1.0-preview.2).


## 2026-10-19 Preview 3 update

Runtime source: `4089af1`; app version `0.1.0`, build `3`. This is a developer
prerelease refresh, not stable-release qualification.

- Final clean-checkout regression: 2,605 tests in 408.635 seconds, zero failures
  or errors, three opt-in skips. Tracked source hashes were unchanged throughout.
- The initial integration run had eleven documentation failures and one import
  error. Those failures were preserved, corrected and followed by the full run
  above; no failing row was relabeled as a pass.
- Developer ID secure timestamps, hardened runtime, app and DMG notarization
  Accepted, stapled tickets and Gatekeeper checks passed. The read-only artifact
  check passed all twelve automated integrity checks.
- This exact DMG was installed locally. Existing Accessibility and screen-reading
  permissions, designated requirements and both agent skill/command connections
  were retained without resetting permissions. The previous app is retained for
  rollback.
- Installed runtime smoke: a fresh owned native popup Flow selected Beta then
  Alpha, observing count 0→2; zero model calls, sampled input audit unchanged,
  4.560 seconds for the CLI run. This is not proof of actual human typing/caret
  preservation or general-app reliability.

DMG SHA-256: `1af52523ee2577a1b8c9780e3ff5b2f8eecd1660a9b84d17531f3b9cca21b65c`.

The complete stable-release evidence gates remain unqualified; the release
checker therefore reports stable readiness as blocked. Fresh-Mac installation,
broader real-app repetition, matched performance qualification and a limited
beta have not been replaced by the automated checks or notarization.
