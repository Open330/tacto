# First-run quality review — 2026-10-19

Claude peer and Codex agreed on the following split and implemented it in the
shared source tree. Claude's Windows work stayed in its separate worktree.
This source update follows `v0.1.0-preview.6-first-run.5` and is the source
for the `v0.1.0-preview.7` release candidate. Final artifact and installation
results are recorded with the published release.

| Owner | Concrete issue | Change |
| --- | --- | --- |
| Codex | Failed Helper connection prints a traceback | One JSON result: `connect/helper_unavailable`, no input or direct-backend fallback, short diagnostic hint |
| Codex | Read exceptions or malformed observations escape the first task | Reuse normal replay's read normalization at all three observation stages; report the failed stage without repeating input |
| Codex | Native launch lookup/dispatch raises; missing dispatch receipt can cause a repeat | Short native errors; distinguish not-started from unknown effects; mark dispatch before sending and send at most once |
| Claude | Terminal opens and the button immediately enables another start | Shared controller state blocks button and method reentry for a bounded 20-second hold; expired pending launchers are removed |
| Claude | nvm/fnm CLIs are missed by the GUI; `env node` cannot find adjacent Node | Inspect version directories without executing a shell; prepend the selected CLI's bin while retaining the existing PATH |
| Claude | A selected CLI disappears and starting silently does nothing | Restore a valid default selection or show a short message |

Claude reviewed the Python changes with no blocking findings. Codex reviewed the
Swift changes and requested the shared method guard. The final controller check
renders real AppKit controls, exercises ready/opening/handed-off/expired states,
and directly reenters the start method during both holds. A test-only external
Terminal stub ensures a broken guard cannot start an actual agent.

## Verification

- **92 affected regression checks passed**, including generated shell argument
  boundaries, nvm execution with a fake adjacent Node and a minimal PATH, fnm
  version-path ordering, CLI failures, normal replay routes and GUI state changes.
- The actual unsigned Python invocation without an inherited channel returned
  short JSON, exit 1, no traceback and no UI input.
- One actual signed first task on the development Mac verified its new document's
  visible text: 5.479 seconds, zero internal model calls, no Save command. This
  single run is a functional check, not evidence of a speed gain or external
  agent token usage.
- The unmodified GUI sources compiled and rendered ready/light, ready/dark and
  missing-permission snapshots. Existing layout assertions passed; the ready
  content height remained 420 points. Prompt-copy verification passed and
  restored the previous clipboard contents.

Targeted verification command:

```sh
.venv/bin/python -m unittest \
  tests.test_first_run_gui tests.test_first_run_agent \
  tests.test_standalone_preview tests.test_release_onboarding tests.test_drag_preview \
  tests.test_first_run tests.test_app_launch tests.test_first_run_cli \
  tests.test_cli_read_routes tests.test_cli_provider tests.test_cli_document_scope \
  tests.test_cli_dispatch_journal tests.test_cli_event_timing \
  tests.test_cli_authoring_rounds tests.test_flow_session
```

## Limits

The hold mitigates rapid repeated starts. Script deletion means the agent session
started, not that the task completed; a later intentional start is still possible.
This is not a cross-process idempotency or agent-completion protocol.

Version-manager fixtures use a fake Node process. Actual user Terminal execution
with nvm/fnm remains to be checked in a new signed app. asdf/nodenv and installations
where Node is outside both the CLI bin and inherited PATH are not added here.
No initial-run wait, new approval screen or permission reset was introduced.
Private screenshots and detailed logs are retained separately from tracked docs.

## 2026-10-20 권한 도우미·창 수명 수정

상태 확인 결과마다 주 창 높이를 다시 맞추던 동작을 제거했습니다. 최초 표시
크기를 제한하고, 이후 확인·실패·복구·상세 표시 전환에서는 사용자의 창 크기와
위치를 유지합니다. 넘치는 내용은 기존 스크롤 영역에서 다룹니다. 빈 스크롤
영역에도 같은 배경을 그립니다.

권한 도우미의 닫기 이벤트를 주 창과 함께 처리합니다. 닫으면 안내 세션과
권한 확인·위치 추적 타이머를 해제하므로 이미 대기 중인 콜백이나 반복적인
앱 다시 열기가 도우미를 되살리지 않습니다. ‘설정 열기’를 새로 누를 때만
새 안내 세션을 시작합니다. 이전 세션의 완료 타이머도 새 세션을 닫지 못합니다.

도우미는 키보드 입력을 받는 창이 아니므로 key/main window가 될 수 없게
했습니다. 인증 앱이나 다른 앱이 전경에 오면 도우미를 숨깁니다. 암호 입력을
자동화하거나 실제 인증 정보를 관찰하지 않습니다.

검증: 실제 AppKit 컨트롤을 쓰는 오프스크린 회귀 검사에서 상태 전환 12회,
닫은 뒤 다시 열기 20회, 타이머 해제, 인증 창 우선 처리, 별도 테스트용 보안
입력란의 포커스를 확인했습니다. 관련 검사 14개 통과. 정상·미허용·확인 실패·
작은 어두운 화면·대체 안내 등 5개 화면의 잘림 검사도 통과했습니다. 실제
macOS 암호·Touch ID 입력을 대신 수행해 확인한 결과는 아닙니다.

### 2026-10-20 permission completion and first-task window settling

Basic app control and completion of both displayed permissions are separate
states. The permission section now stays expanded with a 1/2 label until both
app control and screen capture are granted; the badge cannot claim full
completion while screen capture is unset. Basic app control can still connect
an agent without requiring the optional capture capability. Watching a specific
permission takes precedence over the general status message.

The first task previously read native window identity only once after New.
Its generic creation postcondition can already match an existing document, so
that read can precede publication of the new AX window. It now takes at most
eight read-only identity observations, 100 ms apart, without resending New or
text input. A unique new window and preservation of existing document identities
remain required. An exhausted/ambiguous result includes document counts and read
count, without document content, so a future failure can distinguish delayed
creation from disappearing or multiple documents. This addresses a code-level
race; it does not establish the cause of the user's past failure without a trace.

Regression coverage exercises all four permission combinations, stale/unknown
receipts, delayed new-window publication, the bounded timeout, and single menu
and text dispatch. These checks do not replay the user's interrupted task or
prove an OS authentication interaction.

### 2026-10-20 result presentation and automatic access receipts

After the fixed first task verifies its text, it explicitly activates the same
TextEdit process and reobserves the same native window with matching text and
focus. Ordinary launch and Flow replay remain in the background. Presentation
failure preserves `text_verified` with `foreground_verified=false` and a short
presentation error; it never resends New, text or Save. The first-request guide
now explains that distinction.

Supported computer-use CLI invocations create private, bounded JSONL access
receipts automatically. The transport wrapper records operations and outcomes
without argument bodies, screen trees or provider messages; a missing end record
is an unknown outcome. The GUI footer/menu and `tacto logs` expose the records.
Full screen/action traces remain separate opt-in artifacts. Retention, limits,
privacy scope and the distinction from outer-agent reasoning are documented in
[access logging](access-logging.md). Tests cover forwarding the exact driver
arguments/result once, exception handling, storage failure, restrictive file
modes, symlink rejection, retention and terminal command records after truncation.
