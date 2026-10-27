# Vault-backed delegated login: source verification

Subsequent release: [v1.0.0-rc.10](https://github.com/Open330/tacto/releases/tag/v1.0.0-rc.10) includes this feature and its own notarization, installed-app tests and fleet verification. The record below describes the earlier source-development stage.

Date: 2026-10-26. This feature is a development source change. The published
v1.0.0-rc.9 app/DMG and installations have not been replaced by this work.

## Result

The credential policy previously sent both credential effects and credential
data to a mandatory human handoff. Ordinary task approval did not authorize a
Vault source or destination. The new `auth` route registers reusable app/site,
Vault-item and operation scopes, then obtains values inside the runtime and
completes delegated field entry or login without exposing plaintext to the agent.

- `fill`: field entry without submit. `login`: entry, one submit and declared
  success-condition verification. Login grants also allow fill-only use.
- macOS Keychain stores local values under Tacto's dedicated service. Bitwarden
  requires its existing unlocked session and exact configured server/item/field.
- Grants contain references and selectors, not credential values. They may
  expire or last until revoked; changing their provider invalidates them.
- Scope is checked before reading the Vault and on live frame ancestry at input
  and submit. Revocation/replacement during a Vault fetch prevents dispatch.
- Signed helper capability negotiation prevents sending values through old
  helpers. Credential operations are excluded from full-screen tracing and
  automatic access receipts contain only operation outcomes.
- The stage-specific skill directs agents to this route instead of asserting
  that delegated login is impossible. Normal runs do not require renewed
  approvals or invoke a model.

## Receiving verification

1. Browser SDK: an owned loopback HTTP form received the expected synthetic
   username/password, returned a session cookie and rendered the declared success
   element. Reuse observed the authenticated state without another credential
   read or submit. A wrong origin and navigation during Vault retrieval sent no
   credential into the replacement document.
2. macOS Keychain: synthetic item create/read/update/delete passed. No real user
   credential was inspected. All owned Keychain test items and test grants were
   removed after use.
3. Real Safari through the built, signed source launcher/helper: the owned HTTP
   receiver verified both synthetic fields; `login_verified` was returned. A
   second run returned `already_authenticated`, zero fields dispatched and no
   second POST. This explicit native receiving test passed in 6.893 seconds total
   (server/setup, navigation and both runs); this is not a comparative benchmark.
4. That Safari test initially exposed a real WebKit issue: setting AXValue on an
   unfocused field could acknowledge success, then lose the username when the
   password field received input. Credential writes now focus the exact AX
   element and recheck its origin before setting the value. The receiving test
   passed with that correction.
5. Full Swift launcher and helper builds, Developer ID signatures and helper
   bundle verification passed with the existing Tacto signing identifiers/team.
   No published binary was relabeled or modified.

## Boundaries

Bitwarden protocol tests use controlled responses for session/server/item
matching, caching, child-environment session delivery and npm/node lookup under
an isolated launcher PATH. A real user's Bitwarden item was not read or used to
log into an external account. The external Vault was neither unlocked nor locked.

A configured success condition proves the observed UI condition in its scoped
destination, not independent account identity. MFA, passkeys, challenges and
multi-screen SSO authoring are not implemented by this provider. Native Safari
verification covers a two-field owned HTML login form; it is not a claim that
every website login has been tested. Generic plaintext Flow parameters remain
separate from this Vault-backed execution route.

## Checks

- Credential unit, transport and local-browser suite: 33 tests, zero failures,
  one opt-in native test skipped in ordinary discovery.
- Explicit native Safari/Keychain/signed transport receiving test: one pass.
- Contract suites: 71 tests, zero failures, one optional test skipped.
- CLI suites: 60 tests, zero failures.
- Safety suites: 30 tests, zero failures.
- Access-record suite: nine tests, zero failures.
- Native activity-view fixture: one pass.
- Source compilation, dependency lock refresh and whitespace checks passed.
- Full discovery ran 3,011 tests in 497.463 seconds with 16 skips. One existing
  Swift source-extraction test failed because the newly inserted AX functions
  fell between its pure-function extraction markers. Moving the credential
  functions outside that region preserved the existing extraction contract;
  all five native selection-parameter tests then passed. The credential suite
  was rerun after that correction and the default non-expiring grant change.
  The entire eight-minute discovery was not repeated for that source relocation.

## 2026-10-27 개발 후속: Flow 연결과 앱 설정

이 후속 변경은 [rc.11](https://github.com/Open330/tacto/releases/tag/v1.0.0-rc.11)에 포함해 신규 공증·설치 검증을 마쳤습니다. 설치된 배포본의 실제 Safari Flow와 Vault 관리 창도 확인했습니다. 최종 범위는 릴리즈의 VALIDATION.json과 [출시 기준](release-readiness.md)을 따릅니다.

아래는 rc.10 이후 개발 소스의 변경이다. 이미 공개된 rc.10 앱·DMG를 이
변경이 포함된 파일로 바꾸거나 소급 표기하지 않았다.

- Flow의 `authenticate` 단계가 등록된 위임 이름을 사용하고, 로그인 결과를
  확인한 뒤 새 관찰에서 다음 작업을 실행한다. 누락·만료·다른 앱·입력 전용
  위임은 앞선 Flow 입력이 시작되기 전에 확인한다. Flow에는 비밀값이나 별도의
  임의 인증 권한이 들어가지 않는다.
- 사용자명 → 비밀번호처럼 명시적으로 선언한 여러 화면을 순서대로 실행한다.
  성공 화면이 이미 보이는 앞 단계는 재제출하지 않는다. 임의 SSO/MFA를
  추론해서 처리하는 기능은 아니다.
- 선택한 Vault 필드와 현재 화면의 계정 표시를 내부에서 비교하는 `account`
  검사를 추가했다. Vault 조회가 끝난 뒤 화면을 다시 읽어 확인한다. 설정하지
  않은 경우 `account_verified: false`로 반환하며 특정 계정을 확인했다고 주장하지 않는다.
- 실패 결과의 `repair`에는 실패 필드 별칭·단계·완료한 단계 수와 짧은 안내만
  포함한다. 관찰 트리·비밀값·Vault 응답은 반환하지 않는다. 제출 효과가
  미확정인 경우 자동 재제출하지 않는다.
- 앱 설정에 Vault 연결 등록·수정, 위임 파일 선택/끌어놓기·검토·입력 전용으로
  축소·저장·취소, 로컬 Keychain의 보안 입력 필드를 추가했다. 사용자는 JSON을
  직접 작성하지 않고 에이전트가 만든 파일을 검토한다. 검토한 파일의 digest를
  저장 때 대조해, 중간에 내용이 바뀌면 다시 검토하도록 한다. 비밀값은 프로세스
  인자 대신 stdin으로만 전달한다. 설정 화면은 필요할 때만 생성한다.
- durable journal은 인증 필드 입력과 제출을 모두 예약한다. 비밀값 자체나
  비밀값의 해시를 저장하지 않고 작업 종류·대상 메타데이터만 지문으로 남긴다.
  같은 실행 기록으로 재시작했을 때 이전 입력·제출을 무조건 반복하지 않는다.
- 로그인 뒤 다른 Flow 단계가 앱에 표시된 비밀값을 읽어도 결과 출력에서
  가리도록 보호 목록을 전달한다. 저장하지 않은 GUI 비밀값은 창을 닫을 때 지운다.

검증은 소유한 HTTP 두 화면에서 실제 사용자명/비밀번호 수신 → 로그인 → 후속
보고서 작업, 로그인 재사용과 다른 계정 표시 감지를 확인했다. 실제 Safari와
서명된 소스 launcher/helper에서도 로그인 포함 Flow를 두 번 실행해 두 번 모두
후속 읽기를 완료했고, 서버에는 로그인 POST가 한 번만 도착했다. 실제 서비스
계정으로 로그인한 시험은 아니다. 임시 Keychain 항목과 위임은 제거했다.

네이티브 GUI fixture는 세 화면의 제어·뷰포트 배치, stdin 전용 비밀값 전달,
위임 검토/수준 축소/취소 연결을 확인하고 렌더링을 검토했다. GUI의 실제 외부
Vault 연결이나 운영 계정 등록을 자동으로 수행한 시험과는 구분한다. 회귀 결과와
원본 증빙은 별도 개발 검증 기록으로 보존한다.
