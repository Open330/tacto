# Tacto 출시 기준

> 제품·측정 라벨·식별자는 현재 Tacto 이름으로 정규화했습니다. 당시 측정 수치와 성공·실패는 유지하며, 새 서명 배포를 검증했다는 뜻은 아닙니다.

현재 배포 단계는 **개발자 미리보기**입니다. 사용자의 요청에 따라 공증된
Preview 7을 prerelease로 제공하며, 미완료 검증과 알려진 한계를 함께 명시합니다.
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
| `fresh_install` | 개발 환경이 없는 별도 Mac에서 DMG 복사 → 첫 실행 → 사용자 권한 설정 → Claude·Codex 각각 새 대화의 첫 작업 완료. 터미널 설치 우회 없이 확인 | 세 Mac 설치·연결 확인, 기존 권한 보존. 한 Mac의 초기 권한과 사용자 DMG 설치·두 에이전트 새 대화 첫 작업은 남음 |
| `real_app_tasks` | 위 실제 앱마다 대표 작업 2개를 각각 10회 실행, 총 60/60 결과 확인. 앱·OS·모델 버전, 실패 시도도 기록 | 최종 Preview 6 동일 산출물 60/60 통과. 이전 후보·수정 중 소스의 실패는 아래에 별도 보존 |
| `failure_recovery` | 창 닫힘·화면 지연·대상 변경·권한 해제·flow 재생 중 실패를 각 3회. 잘못된 대상 입력·확인 없는 중복 실행 0건, 중단 사유와 재개 경로 확인 | 최종 설치본 창 닫힘·지연·이름 변경·중간 실패 12/12 통과. 권한 해제·재부여 3회는 사용자 확인 필요 |
| `upgrade_rollback` | 이전 후보 → 새 후보 → 이전 후보 복귀 1회씩. 저장 flow·설정 보존, 링크·서명·권한 상태 확인. 권한 재요청 여부 기록 | Preview 5↔6 명령·두 스킬 연결 및 실제 앱 교체·복귀 통과. 최종 6 복원, flow·설정·권한 보존 |
| `performance` | 같은 조건 10쌍 비교. 성공률 하락 없음, flow 중앙값·합계 및 관찰 중앙값이 승인된 기준보다 15% 넘게 악화되지 않음. 모델·토큰 비교는 별도 조건 명시 | Preview 5 대비 최종 설치본 10쌍 통과. flow 중앙값 +2.1%, 합계 −0.4%, 관찰 중앙값 −1.0%; 세 지표 모두 15% 제한 충족 |
| `regressions_security` | 전체 자동 테스트에서 미해결 실패 0건. skip 사유 검토, staged diff·배포 파일의 비밀·개인정보 점검. 동료 검토 완료 | 최종 소스 2,629개 실패·오류 0, opt-in 3개 별도 실행. 현재 소스·배포 파일 점검 통과. 과거 GitHub 참조·캐시 정리는 사용자 Support 요청 필요 |
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
진행합니다. 현재 미리보기는 Developer ID 서명·공증·Gatekeeper 검사를 통과했습니다.
정식 안정 버전 승인은 아래 실증을 별도로 충족해야 합니다.

## 사람이 수행해야 하는 최종 확인

자동 검사는 아래 확인을 대신하지 않습니다. 권한을 프로그램으로 해제하거나
베타 사용자를 만들어 통과로 기록하지 않습니다. 개인정보가 들어가는 원자료는
공개 저장소에 올리지 않고, 후보 태그·DMG 해시와 익명 결과만 공유합니다.

1. **새 Mac 설치와 첫 작업:** 별도 Mac에는 앱과 두 스킬 연결을 설치했습니다.
   해당 Mac에서 Tacto를 열어 앱 조작·화면 기록 권한을 직접 허용하고,
   Claude Code와 Codex를 각각 새 대화로 시작해 Finder의 소유 테스트 파일
   선택을 요청합니다. 각 대화의 모델·버전, 성공 여부와 걸린 시간을 기록합니다.
   SSH 설치는 DMG를 직접 끌어 넣는 사용자 설치 검증을 대신하지 않으므로,
   개발 환경 없는 Mac의 DMG 복사부터 시작하는 경로도 한 번 확인합니다.
2. **권한 해제·재부여 3회:** 저장하지 않은 개인 작업이 없는 상태에서
   시스템 설정의 Tacto Helper 권한을 직접 끕니다. 에이전트가 소유 테스트
   파일 읽기를 시도했을 때 입력 없이 권한 부족으로 멈추는지 확인합니다.
   권한을 다시 켜고 새 관찰부터 같은 파일 읽기를 완료합니다. 각 회차에
   껐던 권한, 거부 사유, 변경 입력 수 0, 재개 결과를 기록합니다.
   시스템 인증과 권한 변경은 사용자가 수행하고, 실행·로그 검사는 에이전트가
   이어서 처리할 수 있습니다.
3. **5명·3일 베타:** 각 참여자는 최소 세 가지 실제 작업을 수행합니다.
   익명 참여자 ID, 날짜, 후보 태그, OS·앱·에이전트 버전, 작업과 기대 결과,
   실제 결과, 소요 시간, 실패 사유, 잘못된 대상·중복 실행·데이터 손상 여부를
   기록합니다. 실패도 모두 포함하고 차단 결함이 있으면 안정 배포를 보류합니다.
4. **GitHub 기록 정리 요청:** 과거 개인정보가 담긴 PR 참조가 서버에 남아 있어
   저장소는 비공개로 유지합니다. 준비된 비공개 Support 요청 초안을 사용자가
   제출한 뒤, GitHub의 처리 결과와 실제 이전 참조 접근 여부를 재확인합니다.
   이 작업은 앱 서명·공증 상태와 별개의 소스 공개 조건입니다.

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

## 2026-10-17 후보: 실제 앱과 복귀 검증

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

이전 설치와 수정 후보 사이에서 실제 명령 연결(이 기록에서는 현재 `tacto` 이름으로 정규화), Claude·Codex 스킬 연결을
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
  flow는 이를 지원하는 버전이 필요합니다. 현재 native tree가 `Subrole`을
  내보내는 무라벨 창 버튼에만 적용하며, 관찰에 없는 값은 추정하지 않습니다.
  승인 기록에도 이 값을 고정하고 실행 직전에 실제 대상과 다시 비교합니다.
  이 값만으로 식별되는 무라벨 버튼은 현재 selector 자동·오프라인 수리
  대상이 아닙니다. 새 관찰에 근거해 별도 flow를 작성해야 합니다.

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


## 2026-10-19 Preview 4 menu bar update

GUI source: `8d2cc4f`; app version `0.1.0`, build `4`. This update adds a menu bar
app with no Dock or Command + Tab entry. Closing the settings window keeps the
menu available. An initial `--background` launch shows no settings window; an
explicit reopen shows settings. Automation helpers still launch on demand and
end with their CLI session. Login auto-start is not registered.

- Signed checkout-backed and standalone app checks passed: background launch,
  hidden initial window, hidden Dock icon, explicit reopen and a Dock icon that
  stays hidden after reopening. Peer code review found no blocking issue.
- Documentation, branding and packaging checks: 19 tests passed. Preview 3's
  2,605-test runtime regression is prior evidence; that full suite was not rerun
  for this GUI update.
- Developer ID signing, secure timestamps, hardened runtime, app/DMG notarization
  Accepted, ticket validation and Gatekeeper assessments passed.
- The exact DMG was installed on two existing macOS installations. Both retained
  their signing requirements, Accessibility/screen-reading grants and agent
  connections. Each installed GUI was running with no visible settings window
  and an accessory activation policy (no Dock icon). Previous apps were retained
  for rollback. These were upgrades, not fresh-Mac qualification.

DMG SHA-256: `300cfe4561fe533dd14e8b61f42bbaf1cf53ce9fd35c866f6b68dc8b08aa6c8c`.
[Preview 4 release](https://github.com/Open330/tacto/releases/tag/v0.1.0-preview.4).
The stable-release evidence gates above remain unqualified.


## 2026-10-19 Preview 5 helper cleanup

Runtime source: `cd93ed9`; app version `0.1.0`, build `5`.
[Preview 5 is published](https://github.com/Open330/tacto/releases/tag/v0.1.0-preview.5)
with a Developer ID signed and notarized app/DMG, checksums and notarization notes.
The repository remains private, so downloads require repository access.

- Removed former helper-name fallback paths and stale onboarding references.
  Helper authority is read only from current `TACTO_` variables. Existing bundle
  identifiers and signing requirements remain unchanged.
- Declared `tiktoken` in the checkout-only dev group and synchronized `uv.lock`.
  The frozen environment ran 2,607 tests in 432.143 seconds: zero failures/errors,
  three existing opt-in skips. Independent review and main integration each
  reran 36 related tests successfully. Earlier build/dependency failures and
  their logs were preserved before approval.
- Developer ID secure timestamps, hardened runtime, app/DMG notarization Accepted,
  stapled tickets and Gatekeeper checks passed. All twelve automatic artifact
  integrity checks passed. GitHub's uploaded asset digests match the local files.
- Installed the exact DMG on three Macs. Core-file fingerprints match the verified
  release; each app runs without a Dock icon, with current Claude/Codex links.
  Existing enabled grants were retained. One newly installed Mac still needs the
  user's initial Accessibility and screen-reading grants; its onboarding window
  remains open. Installation is not evidence of completed first-time onboarding.
- Installed-launcher owned native Flow smoke passed: Alpha/count0 to Alpha/count2
  through a Beta selection, zero model calls, sampled input audit unchanged,
  4.720 seconds. This does not certify general-app reliability or human typing
  preservation.

DMG SHA-256: `b11e1cb5440a0206190b39a666b3eff9c76e1f88676996b2b369b466293575aa`.

### What the blocked status means

Preview deployment is **published** and automatic artifact checks are **passed**.
The `blocked` result from `setup/check_release.py` describes **stable/public-release
qualification**. It requires a reviewed evidence packet tied to this exact image
hash for seven areas: fresh installation, real-app tasks, failure recovery,
upgrade/rollback, matched performance, regressions/security and a limited beta.
These areas are not all qualified for this candidate. Source regression tests,
notarization and upgrade installation do not automatically complete that packet.
The private deployment report records preview publication and stable qualification
as separate fields.


## 2026-10-19 Preview 6 qualification

Runtime and packaging source: `b1d5177`; app version `0.1.0`, build `6`.
[Preview 6 release](https://github.com/Open330/tacto/releases/tag/v0.1.0-preview.6).
This is a private developer prerelease. Stable/public qualification still requires
the human checks listed above. The source revision's bundled documentation is an
immutable snapshot; this post-build qualification record and release notes carry
the final image-specific results.

- Full frozen-source regression: **2,629 tests, zero failures/errors, three
  opt-in skips**. The outside-checkout wheel and actual signing-policy opt-ins
  passed separately. Live direct-driver contract: 25/28 passed, three foreground
  popup checks unexecuted. Direct plus signed helper: 50/56 passed, the same
  three popup checks per transport unexecuted. They are not counted as passes.
- Actual Finder, Safari and TextEdit: **60/60**, two representative tasks per app,
  ten repetitions each, zero model requests. TextEdit's twenty saves were checked
  against the owned files through pinned-document receipts. The tested candidate
  and installed app have the same full bundle manifest; the DMG artifact check
  independently verified its packaged copy against that candidate.
- Earlier attempts remain separate: Preview 5 finished 59/60 with one unverified
  save. A subsequent source cohort completed 55 successful tasks, then refused
  one Finder selection because process/target identity could not be confirmed;
  zero input was attempted and four later tasks were not executed. Those failures
  were not relabeled or removed. The final 60/60 is a distinct frozen-candidate
  cohort, not an aggregate that omits the failed attempts.
- Installed Preview 6 failure/recovery: **12/12** for owned window closure,
  delayed appearance, renamed Finder targets and partial flow failure. Refused
  steps sent no mutating input; completed prefixes were not resent. Recovery in
  this harness is explicitly prescribed, not proof of general model repair.
  Three actual OS-permission revocation/regrant cycles remain for the user.
- Both command/agent-link upgrade and rollback passed with an exclusively owned
  compatibility flow. Physical `/Applications/Tacto.app` replacement with Preview
  5 and restoration to Preview 6 also passed. Existing flow/config hashes and
  enabled permissions were preserved; no permission reset or regrant was used.
- **10 alternating performance pairs**, with independent commit-count checks and
  two additional negative scenarios per release, passed. Flow median 1.169 →
  1.193 seconds; ten-run total 11.980 → 11.933 seconds; observation median 0.608 →
  0.602 seconds. All three ratios met the 1.15 gate. This is one Mac, warmed file
  caches and a deterministic owned fixture, not a speed-gain claim, single-patch
  attribution or model/token comparison.
- Developer ID secure timestamps and hardened runtime, app/DMG notarization
  Accepted, stapled tickets, Gatekeeper and **12/12 automated artifact checks**
  passed. GitHub's three uploaded asset digests match the local files.
- Installed the same DMG on three Macs. Claude/Codex links and hidden Dock icons
  were verified. Existing grants were retained. One Mac still needs its initial
  Accessibility and screen-reading grants; its onboarding window remains open.
  Its denied-helper check now shows permission guidance without the former file
  descriptor error or advice to grant a shared Python interpreter.
- Packaging now sanitizes Python build metadata and dylib identity before
  bytecode generation/signing, compiles the GUI using relative source names, and
  rejects a bundle containing the build home/checkout path. The final **5,366-file**
  scan found none of the known historical home/contact, checkout or per-user temp
  paths. Bounded secret scans found no credentials in current publication history
  or the final app. The current reachable publication blobs also had no matches
  for the known historical personal-data targets.
- Five older GitHub DMGs with confirmed build-path metadata were withdrawn.
  Byte-identical private backups and original release metadata remain available
  for audit/rollback; the historical release notes direct users to Preview 6.
  Downloaded copies, cached asset URLs and the old PR ref are not recalled by
  that deletion. GitHub Support cleanup remains a human task; the repository
  therefore remains private.

DMG SHA-256: `ef832c313520f3f57578ed813a551d2b772ddcbbf5e8bf442a1e8172b3971643`.


## 2026-10-19 first-run update 5

`v0.1.0-preview.6-first-run.5` is an incremental developer prerelease. It adds
installed-app launch through LaunchServices and a fixed first-run command,
compacts the ready onboarding window, and detects installed Claude Code/Codex
CLIs. First-run document detection uses native window IDs plus process identity
instead of mutable title sets; verification follows the same window after autosave
renames it. No Save command is sent, and visible text verification is not a
persistent-save receipt.

The exact signed app was installed on the designated secondary Mac. Both existing
permissions and agent links were retained. Two successive actual TextEdit tasks
passed in 3.352s and 3.257s wall time (2.981s and 2.916s engine time), each with zero
internal model calls. A first document autosaved to an `.rtf` title during the
sequence. These two runs are a functional check, not a statistically established
speed gain or an end-to-end agent/token benchmark. Targeted automated checks ran
130 tests: 129 passed and one opt-in test was skipped.

The app and DMG passed Developer ID signing, secure timestamps, hardened runtime,
Apple notarization, stapled tickets and Gatekeeper. The candidate's 5,369-file
build scan found no build-home or checkout path. An earlier uninstalled candidate
failed the designated-requirement continuity check and was not deployed; the final
candidate matches the prior app, launcher and helper signing requirements.

The DMG is the exact tested incremental candidate, not a rebuild after tagging.
Its source manifest records relative file hashes, the verified Preview 6 runtime
base and the workspace commit before the then-uncommitted onboarding patch. The
release tag includes that patch and updated publication documentation. Historical
Preview 6 qualification results above are not new runs against this update.
Stable-release human checks and repository privacy restrictions remain unchanged.

DMG SHA-256: `969fb83221998cf4e1671d9360387befe018efde6aed4c08afa8424a697d33d9`.

Prepublication full regression suite: 2,651 tests in 431.247s,
2,648 passed, 3 opt-in skips, zero failures/errors.


<a id="2026-10-19-preview-7-release"></a>

## 2026-10-19 Preview 7 release

[Preview 7](https://github.com/Open330/tacto/releases/tag/v0.1.0-preview.7)
was published as a private developer prerelease. The exact app source is
`969583c14721f8707e3c6b5298cac43e72164951`; the manifest records the committed
build inputs and the retained Preview 6 runtime base. This section and README
validation links are post-publication documentation, not a rebuild of that app.

- The initial full regression run exercised 2,662 tests: 2,658 passed, three
  opt-in skips, one missing README link to the new quality review. Both READMEs
  were corrected and all six documentation checks passed. No runtime code changed
  for the correction. The initial failure is preserved rather than relabeled a
  clean full-suite run; no automated failure remains unresolved.
- The affected-scope suite passed 92 checks, including real AppKit button and
  controller reentry, shell argument boundaries, fixture Node execution, CLI
  failure normalization and existing replay routes.
- App and DMG Developer ID signatures, hardened runtime, secure timestamps,
  Apple notarization, stapled tickets and Gatekeeper passed. All 12 automated
  artifact checks passed. The 5,373-file bundle path/credential scans passed.
- Two successive actual installed TextEdit tasks passed: 3.434s and 3.346s wall
  time, zero internal model calls and no Save command. Existing document bodies
  remained intact. These are functional smoke checks, not a speed or external
  agent token benchmark.
- The same app archive was installed and integrity-checked on all three
  designated Macs. Previously enabled permissions were retained; command/skill
  links and hidden Dock icons were verified. One Mac still needs its initial
  Accessibility and screen-reading permissions, with its onboarding window open.
- All five uploaded GitHub asset SHA-256 digests matched the local files.
  The release tag points to the app's recorded source commit. README and landing
  download links point to Preview 7.

Prior Preview 6 qualification results above are historical evidence, not new
runs against Preview 7. Stable-release human checks and historical GitHub cleanup
remain separate; the repository remains private.

DMG SHA-256: `59fc0b2658c069cc5c0cb7cf11f4eb3842a4d2347bca8f63a1eee51fa507e6cb`.


## 2026-10-19 Preview 8 window drag

Preview 8 adds signed-helper same-window drag and read-only window capture. The
agent guide loads drag details only when needed; ordinary tasks do not load the
coordinate schema. See [native drag](native-drag.md) for tested scope and limits.

Development receiving tests verified an opaque canvas, exact short-zigzag
waypoints/timestamps, a standard NSSlider and actual TextEdit selection without
changing document text. A reusable Flow checked the resulting slider value.
Peer review found and resolved waypoint loss, identical event timestamps and
an unbounded AX call in cancellation cleanup; the follow-up found no additional
blocker within the documented same-window scope.

The initial full run executed 2,666 tests, with 3 skips and 3 failures. The
failures were a missing README command example, a native drag test affected by
the implementation update, and missing drag boundary stubs in the existing
preparation-diagnostic harness. All were corrected; the affected 15 tests
passed on rerun. An overlapping focused regression run passed 18 tests with
1 expected skip. Three newly added CLI/capture tests were included in those
reruns. The initial full-run failure is retained rather than relabeled as a
clean full-suite pass.

This change does not qualify cross-app drag/drop or a stable release. Signing,
notarization and installation results belong to the release's validation asset;
existing fresh-install/beta qualification requirements remain applicable.


Preview 8 distribution completed from source commit
`f37b5bc31002bf86b560bbb37852d172e57e2083`. App and DMG passed Apple notarization,
stapled-ticket validation and Gatekeeper. All five published assets matched
recorded SHA-256 digests before publication. The first packaging candidate was
rejected for missing secure timestamps on the new native peers; it was never
installed or published. The corrected candidate timestamps every executable.

The same app was installed on three Macs with unchanged signing requirements,
matching runtime hashes, hidden Dock icons and current Claude/Codex skill links.
Two retained both existing grants. The third had neither grant before updating
and still requires its owner's setup step. Packaged capture verified Retina
mapping (1172×976 pixels to 586×488 points); installed TextEdit received a text
selection while preserving its body. A separate border-selection attempt
resized its test window before geometry monitoring stopped the stroke with an
unknown effect. That failed case is retained in validation; window resizing
remains outside this route's supported scope.
