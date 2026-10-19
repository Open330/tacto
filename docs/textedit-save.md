# TextEdit 새 이름 저장의 호스트 증거

`tacto.textedit_receipt.make_textedit_receipt`는 명시한 새 파일에 현재 문서의
정확한 **텍스트**가 저장됐는지 확인하는 실행별 adapter다. CLI/Agent 연결은 있으나
native backend가 아직 필요한 capability를 제공하지 않으므로 실앱 검증 저장은 미활성이다.

읽기 전용 선행 기능인 in-process `Driver.document_text`는 실제 TextEdit에서
검증했다. `evidence.document_text`는 완전 본문·canonical AXDocument 경로와
세션에 귀속된 문서 token을 제공한다. 앱 실행 인스턴스와 창/본문 AX 객체를
전후 확인하며 다른 세션·교체된 문서·부분 조회·예산 초과는 거절한다.
예산은 UTF-16 기준 0~1,000,000 units이고, 초기 문서가 여러 개면 자동으로 고르지 않는다.
Python direct 본문 읽기는 lock 대기를 포함한3초 operation 기한과 caller의 model
기한을 공유한다. adapter 호출 전후 기한을 확인하고 native attribute/range마다
남은 기한/0.5초 중 작은 messaging timeout을 설정한 뒤 reset한다. 기한을 넘긴
본문과 새 token은 반환하지 않는다. native/lock 호출 강제 취소는 아니며 scoped
observe나 저장 전체의 시간 상한을 보장하지 않는다. timeout 설정/reset 실패도
성공으로 채우지 않고 호출자 중단은 reset을 시도한 뒤 그대로 전달한다.
direct/signed의 `document_text(..., document_path=canonical_absolute_path)`는
AXDocument 경로를 먼저 비교해 일치하는 창 하나의 본문만 읽는다. 경로와 token은
동시에 받지 않는다. 경로로 발급한 token은 그 경로도 고정하며 경로 변경 후 거절한다.
경로 필터를 쓰면 다른 문서의 본문 하위 트리를 순회하지 않는다. 이 추가 경로는
주입 회귀 검증을 통과했으며 실제 여러 TextEdit 문서에서의 검증은 남아 있다.
기존 token으로는 여러 문서 중 같은 문서를 계속 읽을 수 있다. 일반 observe
갱신으로 token을 만료시키지 않으며 reader별 최근 발급 32개만 유지한다.
`observe(..., document_token=token)`은 검증된 문서 창 하나만 관측한다. 관측 전후와
후속 mapped action에서 token을 다시 검증한다. scoped 관측에서는 앱 전체 메뉴·
selection 기대·대상 없는 동작을 허용하지 않는다. 이전 helper는 capability 검사로
요청을 거절하며 전체 앱 조회로 대체하지 않는다. scoped 관측과 본문은 live trace에서
전달만 하고 저장하지 않는다. trace replay에는 해당 capability를 주지 않는다.
AX가 노출한 문서 구조를 기준으로 하며, 경로를 지정하지 않은 초기 조회에서 본문을 식별할 수 없으면
다른 문서가 명확하더라도 전체 조회를 거절한다. AX가 숨긴 요소까지 존재하지
않는다고 증명하는 기능은 아니다.

이 API는 아래 `document_snapshot`과 다르다. 저장 패널 디렉터리·설정의 내부 reader와
문서 token의 전후 검증은 구현했으며, 시험용 NSSavePanel에서 설정 변경·폴더 이동을
검증했다. `SavePanelReader.bind_commit`은 내부 native 관측 맵에서 동일한 AX 객체인
Save 버튼을 하나만 찾아 관측 ID·click index에 연결하고 설정을 전후 재검증한다.
시험용 NSSavePanel에서 실제 `Observation.elements`와 연결하고 같은 패널의 설정
변경 후 이전 증거가 거절됨을 확인했다. 읽기 전용 증거이며 action을 실행하지 않는다.
signed helper의 별도 `evidence.save_panel`은 Swift로 설정·디렉터리를 읽고 현재
관측 맵의 실제 Save 버튼에 결합한다. 시험용 패널의 signed 경로에서도 설정 변경과
이전 토큰 거절을 확인했다. 이 API는 `authorizes_save: false`다. 선택적으로 TextEdit
문서 token을 전달하면 같은 창·본문 객체·프로세스를 패널 읽기 전후에 검증하고
패널 token에도 문서 token을 고정한다. 문서 결합은 주입 시험을 통과했으며 실제
TextEdit 문서와 저장 패널의 결합 시험은 남아 있다. 이 API는 `document_snapshot`이
아니다. 서명된 helper의 별도 `save_panel_token` click guard는 AXPress 직전에
동일 패널의 설정·디렉터리·버튼·프로세스와 선택적 문서 token을 재검증한다.
한 입력 시도 전에 token을 소모하므로 timeout 후 같은 token으로 다시 누르지 못한다.
own NSSavePanel에서 실제 완료 콜백과 변경 후 클릭 거절을 시험한다. 실제 TextEdit의
원본 형식·첨부 보존, 최종 입력과 파일 생성의 인과 증거는 남아 저장 receipt를
활성화하지 않는다.
연속 조회 사이의 변경 후 복귀(ABA)도 배제하지 않는다. live trace wrapper는
호출을 전달하되 본문을 trace에 복사하지 않고, 기록 재생에는 이 capability를 주지 않는다.

호스트가 `driver`, `target='com.apple.TextEdit'`, canonical `~/Downloads`의
절대 경로 `directory`, 단일 파일명 `filename='보고서.rtf'`를 전달한다.
흐름 이름이나 모델 결과로 목적지를 추정하지 않는다. 저장 이름 변경, 덮어쓰기,
다른 디렉터리, 일반 텍스트 문서에서 RTF로의 변환, RTFD/첨부 문서 등은 현재 지원하지 않는다.

반환 객체의 `prepare_commit`, `after_commit`, `commit_expected`를 `flow.replay`에
명시적으로 전달한다. adapter는 1회용이다. 승인과 실제 UI 저장은 기존 실행기가
담당하며 adapter는 파일을 생성하거나 내용을 수정하지 않는다.

## 공통 backend 계약

capability `evidence.document_snapshot`과 `action.click.save_panel_guard`가 있어야 하며
다음 API를 제공해야 한다.

```python
driver.document_snapshot('com.apple.TextEdit', document_token=None)
# {
#   'type': 'document_snapshot',
#   'document_token': '<opaque identity>',
#   'text': '<exact complete plain text; empty string is valid>',
#   'text_complete': True,
#   'document_path': None,  # unsaved; otherwise canonical absolute local path
#   'source_format': 'rtf',  # existing recipe does not convert plain text to RTF
#   'save_panel': {  # required at prepare, optional in initial/after snapshot
#       'panel_token': '<connection-owned native panel token>',
#       'document_token': '<same opaque document identity>',
#       'directory': '<canonical absolute destination directory>',
#       'filename': '보고서.rtf',
#       'format': 'rtf',
#       'commit_control': {  # read from the backend's live observation map
#           'obs_id': '<observation used by the upcoming driver.act>',
#           'enabled': True,
#           'action': {'name': 'click', 'element_index': 5},
#       },
#   },
# }
```

첫 호출은 현재 문서 창을 하나 고정한다. 이후 token을 주면 같은 창과 본문
요소를 검증해야 한다. 앱 재시작·창 교체·새 helper 연결에서는 token을 거절한다.
일반 observe 갱신만으로 token을 만료시키면 안 된다. 창 제목이나 전체 tree의
문자열 포함 검사로 identity를 대신할 수 없다. 저장 패널이 열려도 원래 문서의
전체 본문을 읽어야 한다. 부분 관측, 포맷 불명, 접근 오류는 성공으로 반환하지 않는다.
`save_panel`은 현재 최종 Save 동작이 실행될 패널의 실제 소유 문서와 설정이어야 한다.
요청된 token을 그대로 복사하거나 문서에 딸린 임의의 다른 패널을 반환하면 안 된다.
backend는 해당 패널의 실제 절대 디렉터리·파일명·선택된 저장 형식을 읽어야 한다.
초기에는 패널이 없어도 되지만 prepare 시에는 위 설정과 `commit_control`이 필수다.
또한 bounded `panel_token`과 `action.click.save_panel_guard` capability가 필수다.
`commit_control`은 해당 패널의 실제 활성 Save 버튼을 backend의 관측 ID/요소 맵에서
찾은 결과여야 한다. 요청 action을 복사하거나 버튼 이름만으로 생성하면 안 된다.

in-process backend는 AX 객체 identity를 보관하고, signed helper는 연결에 귀속된
token으로 동일 계약을 구현해야 한다. helper 사용 중 Python/PyObjC로 우회하지 않는다.
`source_format`은 확장자만 보고 추측해서는 안 된다. plain-text 값만으로 문서의
첨부·지원하지 않는 컨테이너 유무를 판별할 수 없다면 backend는 거절해야 한다.

## 실행 경계와 한계

초기 본문과 identity, 기존 문서 경로/형식을 고정한다. 최종 Save 전에 같은 token의
본문·경로·형식을 재검증하고 목적지 부재를 확인한다. 저장 후 같은 token의 본문과
AXDocument 목적지가 일치하고, 실제 RTF를 textutil로 읽은 텍스트가 초기 본문과
정확히 일치해야 receipt를 발급한다. 실행 후 관측값을 기대 본문으로 바꾸지 않는다.
실패 후 자동 재저장하지 않는다.

prepare는 `SavePanelCommitPreparation`을 반환한다. 이 호스트 객체는 해당 이벤트·
동작 digest·관측 ID에 패널 token과 원래 receipt context를 결합한다. replay와 일반
루프는 literal approval을 재사용해 native guarded click에 token을 전달한다.
TextEditReceipt는 after 단계에서도 결합을 확인하고 내부 RTF receipt context를
검증한다. AXPress가 `acted/unverified: cannot_complete`를 반환해도 독립 파일·문서
영수증이 정확하면 완료를 확인할 수 있다. 증거가 없거나 불일치하면 unknown으로
중단하며 같은 Save를 다시 누르지 않는다. 순수 native helper의 snapshot capability는
여전히 OFF이므로 이 연결 시험이 실제 TextEdit 전체 계약을 검증한 것은 아니다.

prepare는 저장 패널의 소유 문서 token·절대 디렉터리·파일명·RTF 형식이 고정된
기대값과 일치하는지 검사한다. 패널 증거 누락이나 불일치는 Save 실행 전에 거절한다.
또한 실제 dispatch에 사용할 event의 `obs_id`와 정확한 click action을
`commit_control`에 대조한다. 다른 동작·다른 버튼·이전 관측·비활성/미관측 버튼은
거절한다. general/replay는 같은 관측 ID를 event와 driver.act에 전달하고,
receipt digest에도 관측 ID를 포함해 동일 인덱스의 다른 관측으로 바꿀 수 없게 한다.
`Downloads`라는 폴더 라벨만으로 절대 경로를 대신할 수 없다. 이 계약은 아직
native backend에서 구현·검증해야 하며, 검사 이후 실행 사이에 패널이 바뀌는 경쟁도
backend의 최종 action precondition으로 막아야 한다.
현재 adapter의 action 비교만으로 이 마지막 경쟁을 막았다고 주장하지 않는다.

이 검증은 글꼴·스타일·레이아웃 등 rich-text 서식 전체의 보존을 증명하지 않는다.
초기 문서가 기존 파일이어도 새 목적지는 반드시 없어야 한다. 원본 경로로 저장하거나
기존 목적지를 교체하는 작업은 이 adapter의 지원 범위가 아니다.

디렉터리는 현재 사용자 소유이며 다른 사용자에게 쓰기가 허용되지 않아야 한다.
같은 UID의 다른 작성자와의 경쟁이나 마지막 검사 직후의 변경을 완전히 막는 장치는
아니다. 저장 전 확인 직후 다른 프로세스가 파일을 만드는 경쟁도 배제할 수 없다.
UUID 이름 역시 생산자 인과 증명을 제공하지 않는다. caller는 작업 중 해당 목적지의
단독 작성자라는 호스트 신뢰 조건을 확보해야 한다.

`tests/test_textedit_receipt.py`는 가짜 snapshot backend와 실제 임시 RTF/textutil로
계약을 검증한다. 실제 TextEdit 또는 signed helper 지원을 입증하는 테스트는 아니다.

Agent and direct CLI replay now prepare the host factory for the exact canonical built-in save recipe when the driver advertises `evidence.document_snapshot`. Live tracing forwards snapshots without recording their bodies as reusable evidence; recorded traces do not advertise this live capability. The native backend does not yet provide the capability, so this wiring alone does not make real CLI saves verified.


File-reference URL support: Python and the signed helper resolve local file-reference URLs through Foundation after strict raw URL checks. An ordinary document path URL may name a missing file; a file reference must resolve at observation time and fails if unreachable. Neither an AXDocument path nor successful reference resolution guarantees that the file remains present after the read. The existing before/after document-path checks continue to reject changes during observation.


## 서명된 문서 range RTF export

`evidence.document_rtf_export`는 기존 문서 token으로 `AXRTFForRange`의 full
UTF-16 range를 읽는 별도 읽기 API다. helper는 complete plain text를 세 번,
RTF bytes를 두 번 읽어 서로 일치하는지 확인하고 각 phase 전후 PID/start·
window/body CF identity·문서 경로를 검사한다. RTF는2MiB까지이며 header와
bytes SHA256·canonical base64를 Python transport에서도 검증한다. text_sha256은
별도로 읽은 plain text hash이고, RTF를 importer로 decode해서 얻은 hash가 아니다.
임의 RTF의 외부 resource를 읽는 importer를 실행하지 않는다.

operation deadline은3초 기본/최대6초이고 native document traversal의 각 읽기
경계에서 확인한다. body messaging timeout은 phase마다 remaining budget과0.5초
중 작은 값으로 설정한 뒤 reset한다. OS/AX 호출 강제 취소나 IPC 내부 할당을
2MiB로 제한하는 보장은 아니며, 늦게 끝난 읽기는 반환하지 않는다. 두 export 사이
변경 후 복귀(ABA)나 provider가 노출하지 않는 첨부는 배제하지 못한다.

`source_format_verified:false`, `attachment_completeness_verified:false`,
`authorizes_save:false`를 반환한다. plain-text 문서도 RTF range로 export될 수
있으므로 이 API의 이름/RTF header를 원본 format 판별로 사용하면 안 된다.
`document_snapshot`을 활성화하지 않으며 TextEditReceipt의 원본 형식/첨부 조건을
대신하지 않는다. Live TracingDriver는 export를 기록하지 않고 TraceDriver는
capability를 제거해 재생 증거로 사용하지 못하게 한다.

`signed_document_rtf` eval은 이미 백그라운드에서 실행 중인 TextEdit에 새 자체
RTF만 열어 path-selected token으로 조회한다. 앱 실행·사용자 문서 복원을 하지
않으며 TextEdit가 전경이거나 실행되지 않으면 파일을 열기 전에 거절한다. 읽기
시험의 신규 owned 문서는 다음 G7 실험을 위해 보존하며 사용자 문서를 닫지 않는다.
최초 실측은 TextEdit process 부재로 document open 전에 실패했다. 이것을 실제
TextEdit export 성공으로 계산하지 않는다.

### 2026-10-11: 파일 읽기 이후 바뀐 TextEdit 문서·파일의 저장 상태 증거 거부

ExistingTextFileReceipt는 문서→파일 순서로만 읽어, 파일 read 동안 바뀐
문서 본문/token이나 파일 reader 직후 교체된 path를 다시 확인하지 않았다.
전후 EOF 재현 (local evidence: `../reviews/2026-10-11/existing-save-bracket-before-after-20261011-a.json`)은
before08511ef에서 실제 임시 파일을 읽는 도중 host document 응답의 text/token을
바꾸면 SavedFileStateEvidence를 발급하고 evidence_error=None인 것을 확인했다.
after는 changed_during_save/document_identity_changed로 증거를 거부한다.
파일 reader 이후 실제 path 교체 전후 재현 (local evidence: `../reviews/2026-10-11/existing-save-postread-before-after-20261011-a.json`):
before는 named file이 다른 bytes인데도 saved-state proof를 발급했고,
after는 전체 readback을 버린 뒤 관측만 반복하고 file_does_not_hold_text로
거부했다. 두 재현은 실제 임시 파일·host document stub·synthetic acted response를
사용하며 native GUI/실제 TextEdit document change/model 요청은0이다.

현재는 정확한 file read를 같은 document token·본문·경로 읽기로 양쪽에서
검증하고, 마지막 문서 read 뒤 named file의 dev/ino/mode/uid/nlink/size/
mtime/ctime 및 canonical path를 확인한다. 파일이 바뀌면 transient observation만
deadline 안에서 반복하며 Save를 재전송하지 않는다. 이미 deadline을 넘었으면
새 문서/file read를 시작하지 않고 느린 host read 뒤에도 증거를 발급하지 않는다.
_file_state의 기존 첫6 fields는 유지하고 full fingerprint를7번째에 추가했다.
추가 snapshot은 OS/document/filesystem의 원자적 transaction이나 동일 사용자
ABA·마지막 검사 이후 변경·앱 writer attribution을 증명하지 않는다.
증거 타입은 saved_state이며 producer/causal Save 증거로 승격하지 않는다.

최종 관련 unit (local evidence: `../reviews/2026-10-11/existing-save-bracket-unit-20261011-a.json`):
115 tests3.260s/OK/no skips,process3.358s/source subset stable.
최초 새 regression은 수정 전 ArtifactReceiptError not raised로 실패했다.
문서 변경·실제 inode swap·동일 bytes swap·mtime 복원 덮어쓰기·느린 최초/최종
문서 read·one-shot after 호출을 검사한다. 이 unit은 일반 앱 실기가 아니다.

실제 scoped TextEdit Save 시도 (local evidence: `../reviews/2026-10-11/existing-save-bracket-direct-20261011-a.json`)는
exit2/11.445s/window_preparation_unverified/Save dispatch·receipt 미도달,
실제 installed scoped CLI Save 시도 (local evidence: `../reviews/2026-10-11/existing-save-bracket-cli-20261011-a.json`)도
exit2/10.397s/같은 준비 거부였다. 두 소유 fixture는 closed/cleanup 완료,
source_changed=false다. 실패를 PASS나 기능 미지원 SKIP로 바꾸지 않았다.
현재 전체 regression k (local evidence: `../reviews/2026-10-11/combo-popup-regression-current-20261011-k.json`)와
raw log (local evidence: `../reviews/2026-10-11/combo-popup-regression-current-20261011-k.log`):
1915 tests276.013s/OK/skipped2(실행1913),process276.504s/exit0/source subset stable.
직전 setup link recovery·native cancellation·repeated sheet evaluation과 이번
saved-state 검사를 포함한다. declared manifest가 모든 dependency/OS를 고정하거나
실제 GUI/백본/비교 과제 완료를 증명하지는 않는다.

현재 probe는 preparation의 상세 error code를 보존하지 않아 거부 원인을 더
확인해야 한다. 사용자 앱 강제 활성화·문서 close·권한/서명 변경으로 우회하지 않았다.
일반 앱/새 Save·format·수신자/첨부·동시 사용자 입력·G0~G10 전체 감사는 계속 남는다.

### 2026-10-11: scoped TextEdit 창 준비의 문서 context 비용과 read-only cadence

signed_textedit_save probe가 window_preparation_unverified만 남기던 경로에
기존 preparation_diagnostic validator를 적용해 closed phase/reason/숫자만
보존한다. raw error/detail/document text나 허용되지 않은 key는 기록하지 않는다.
수정 전 실제 진단 a (local evidence: `../reviews/2026-10-11/textedit-prepare-diagnostic-20261011-a.json`):
FAIL/not_started/observation_deadline/dispatch phase/4994ms. scoped AX314회/63ms,
full context918회/context4880ms,readiness pass2/windows4/nodes111이었다.
정확한 document token의 documentWindowForToken 재탐색이 full context마다
반복되는 비용을 확인했다. 다른 사용자 window를 닫거나 activate하지 않았다.

정확한 observed bundle의 read-only readiness에서는 기존 NativeWindowReadScope
cadence를 문서 token에도 적용한다. 매 RPC의 abort/obsID/PID/process start/
element generation/최신 document token 검사는 유지하고, connection pin의
pid/stamp/window/body/selectedPath/lifecycleWindowID가 처음과 동일한지도
검사한다. live document enumeration은16 AX operations마다, readiness entry/exit,
이 구간 밖의 모든 full checkpoint·입력 전 validation에서 유지한다.
Alias는 per-RPC full resolver 검사를 유지한다. 실제 live document의 모든
속성을 매 RPC 다시 읽는다는 주장은 하지 않는다. 관측은 원자적 lease가 아니며
sampling 사이의 ABA/연속 사용자 caret 보존을 증명하지 않는다.

helper signed build s (local evidence: `../reviews/2026-10-11/combo-popup-routing-build-20261011-s.json`):
PASS22.051s/source subset stable/designated requirement·identifier·Team identity
unchanged/strict helper·deep app verify PASS. TCC/launcher identity는 변경하지 않았다.
이후 direct scoped Save (local evidence: `../reviews/2026-10-11/textedit-prepare-cadence-direct-20261011-a.json`):
FAIL7.070s/not_started/validation_headroom/dispatch. preparation1461ms,
full557/incremental809/scoped AX463/context1380ms/AX40ms.
이후 installed CLI Save (local evidence: `../reviews/2026-10-11/textedit-prepare-cadence-cli-20261011-a.json`):
FAIL9.051s/not_started/validation_headroom/preparation3858ms,
full559/incremental813/scoped AX465/context3681ms/AX89ms.
각 source_changed=false/cleanup closed/closed_observed=true/artifact_removed=true/
focus activations0/model requests0. 모두 Save·receipt까지 미도달이므로 saved-state
성공이나 causal Save로 채점하지 않는다. 두 시도의 AX latency도 달랐으므로
일반 속도 우위/성공률을 주장하지 않는다. 전체 post sequence의 measured-cost
headroom gate는 완화하지 않았다. 다음 작업은 남은 validation/routing/context
비용의 closed 계측과 live document enumeration의 동등한 조회 최적화다.

최종 관련 unit (local evidence: `../reviews/2026-10-11/textedit-prepare-cadence-unit-20261011-a.json`):
40 tests14.413s/OK/no skips,process14.526s/source subset stable.
실제 추출 Swift 함수에서 pin 교체·삭제/문서 full validator 실패를 입력 전에
거부하고, 실제 Swift ReadScope에서16 ops checkpoint·entry/exit·phase 밖 full
검사·abort/deadline을 확인했다. headroom의 느린 검증/라우팅/입력/context는
첫 post0으로 거부하는 기존 Swift 검사를 실행했다. 이 unit은 real document
replacement/다중 앱/모델 평가나 강제 OS cancellation의 증거가 아니다.
새 helper의 정상 native visual (local evidence: `../reviews/2026-10-11/signed-native-visual-fixed-1791718776596674000.json`):
PASS6.493s/DONE/history1/posted_events2/source subset stable/UNCHANGED_SAMPLED/
owned child reaped/model calls0. document token 없는 소유 canvas 회귀다.
이전 full k1915는 이번 helper cadence·probe 변경을 포함하지 않는다.
TextEdit/Finder·G0~G10·backbone·Sky 동일 조건 감사는 여전히 미완료다.

### 2026-10-11: window input headroom의 closed 비용 계측

shared mouse/key preparation sequence의 첫 post 이전 headroom gate에
required_ms/remaining_ms/validation_ms/routing_ms/input_ms/context_ms/event_count/
saturated를 추가했다. 각 duration은 ceil ms/0..600000에 한정하며 nonfinite나
600s 초과는 포화 표식을 남긴다. 원래 float reserve 판정은 유지하고, 진단
callback이 끝난 뒤 실제 now를 다시 사용한다. 이 숫자는 authority/receipt가
아니며 기한·검증·입력 보존 요구를 완화하지 않는다. opaque input 등의 다른
branch 전부에 동일 계측이 생겼다고 주장하지 않는다.
helper는 이 metadata만 preparation_diagnostic에 넣고 Python의 기존 closed
validator가 정확한 key/type/bounds를 검사한다. unknown key/body/raw error
text는 거부하고 nested dictionary를 복사해 이후 mutation도 차단한다.

signed helper build t (local evidence: `../reviews/2026-10-11/combo-popup-routing-build-20261011-t.json`):
PASS22.191s/source subset stable/designated requirement·identifier·Team unchanged/
strict helper·deep app signature verify PASS/TCC 변경 없음.
실제 scoped TextEdit 진단 a (local evidence: `../reviews/2026-10-11/textedit-headroom-diagnostic-20261011-a.json`):
FAIL/exit2/7.405s/window_preparation_unverified/not_started/validation_headroom.
prep1532ms/required6115ms/remaining3442ms/validation558ms/routing1ms/input8ms/
context6ms/events3/saturated=false. full557/incremental809/scoped AX463,
context1464ms/AX28ms/readiness pass3/windows4/nodes111이었다.
이 시도의 reserve에서는 전체 binding validation의 최대558ms가 주요 비용이다.
이는 실제 deadline을 늘리거나 느린 검증을 생략하면 된다는 증거가 아니다.
Save/receipt 미도달,cleanup closed/source_changed=false/model requests0,
실패를 saved-state·causal Save 성공으로 바꾸지 않았다. 다음 구현 작업은
binding validation 안의 live document/window 조회를 중복 없이 검증하는
동등한 최적화와 새 signed 실제 Save 확인이다. 환경/동시 입력/일반 앱
성공률이나 속도/비용 우위는 이1회 진단으로 증명하지 않는다.

최종 관련 unit (local evidence: `../reviews/2026-10-11/window-headroom-components-unit-20261011-a.json`):
36 tests11.124s/OK/no skips,process11.253s/source subset stable.
실제 Swift dispatcher에서 slow validation/routing/input/context와 늦은 diagnostic
callback은 첫 post0으로 거부한다. 정상 mouse/submit은 기존 event 수를 유지하며
bounded telemetry/marker transport/후속 mutation·private key 거부를 검사했다.
실제 정상 native visual (local evidence: `../reviews/2026-10-11/signed-native-visual-fixed-1791719138244139000.json`):
PASS6.595s/DONE/history1/posted_events2/source subset stable/UNCHANGED_SAMPLED/
owned child reaped/model calls0. required203ms/remaining4804ms/validation21ms/
routing4ms/input1ms/context1ms/events2/saturated=false. 이 소유 canvas는 document
token 없는 회귀이며 TextEdit 성공을 대신하지 않는다. 이전 full k1915는 이번
계측 및 앞선 document cadence 변경을 포함하지 않는다. G0~G10 전체는 미완료다.

### 2026-10-11: complete binding capture의 read-only context 주기와 실제 Save

batch node 후보는 복원된 fe199ba 코드 그대로 비활성이다. 새 withBindingContext는
비-popup physical binding capture와 재검증의 읽기 전용 구간에만 기존16 AX ops
auxiliary context 주기를 적용한다. binding의 window/process/geometry/hit/모달
검사 자체를 생략하거나 결과를 캐시하지 않는다. exact observed bundle은 매 RPC
abort/obsID/PID/process start/element generation/document connection pin을 검증하고,
live document context를 주기 및 구간 경계에서 검사한다. alias는 per-RPC full
검사를 유지한다. popup capture/selection transition의 기존 검사 경로는 유지했다.
routing/preflight/입력 상태 확인/dispatch context/기한/headroom gate는 밖에서
기존 full 검사를 수행한다. live document 속성 전체를 매 RPC 다시 읽거나
동일 사용자 ABA·연속 caret/pointer 불변을 증명한다는 주장은 하지 않는다.

NativeWindowReadScope의 중첩 구간도 항상 full entry/exit를 거치며 이전 read-only
모드를 defer로 복원한다. nested failure를 호스트가 잡아도 바깥 phase는 유지되고
최종 metadata가 바뀌면 결과를 내보내지 않는다. phase 밖은 full 검사로 복귀한다.
최종 관련 unit (local evidence: `../reviews/2026-10-11/binding-context-unit-20261011-a.json`):
61 tests15.709s/OK/no skips,process15.818s/source subset stable. 실제 추출 Swift scope의
nested exception/live metadata/identity drift와 helper preparation의 pin 교체·삭제·
body validator 실패 거부, key/window/save-panel/receipt 관련 회귀를 확인했다.
이 unit은 actual user document replacement나 동시 사용자 입력 증거가 아니다.

signed build w (local evidence: `../reviews/2026-10-11/combo-popup-routing-build-20261011-w.json`):
PASS21.722s/source subset stable/designated requirement/identifier/Team unchanged/
strict helper/deep app verify PASS. TCC·launcher identity는 변경하지 않았다.
실제 direct a (local evidence: `../reviews/2026-10-11/textedit-binding-context-direct-20261011-a.json`):
FAIL7.892s/save_menu_not_enabled_after_prepare. 준비는 acted/dispatched_unverified/
741ms로 완료했고 first-post headroom required1030ms/remaining4756ms/
validation62ms/routing1ms/input10ms/context4ms/planned events3/saturated=false였다.
scoped AX1243/full161/incremental3538/context481ms/AX87ms/8 readiness passes였다.
입력 전 validation_headroom 거부는 해결됐지만 이후 Save menu disabled를 관측해
Save/receipt는 실행하지 않았다. 준비가 acted라는 이유로 Save 성공으로 채점하지 않는다.

실제 installed scoped CLI a (local evidence: `../reviews/2026-10-11/textedit-binding-context-cli-20261011-a.json`):
PASS9.813s/CLI exit0/done/정확한 disk·document text after Save/cleanup closed.
실제 direct b (local evidence: `../reviews/2026-10-11/textedit-binding-context-direct-20261011-b.json`):
PASS6.895s/Save acted/evidence_error=None/exact disk text/cleanup closed.
두 PASS는 menu가 이미 enabled여서 preparation not_needed였다. 새 binding 준비
변경이 이 두 Save 성공을 직접 만들었다고 귀속하지 않는다. 모두 source_changed=false/
closed_observed=true/artifact_removed=true/focus activations0/model requests0다.
Save 직전 disk_matched_before_save=false지만, saved-state proof/파일 수렴을 일반
앱 writer attribution이나 causal Save 증거로 승격하지 않는다. direct b의 proof는
현재 ExistingTextFileReceipt의 같은 document token·본문 양쪽 검사와 최종 file
fingerprint 검사를 실행했다. 준비 필요 상태와 불필요 상태의 초기 UI는 동등하지
않고, 이번3회로 대표 성공률·속도 우위·Sky 동일 조건 비교를 주장하지 않는다.
현재 전체 regression l (local evidence: `../reviews/2026-10-11/combo-popup-regression-current-20261011-l.json`)와
raw log (local evidence: `../reviews/2026-10-11/combo-popup-regression-current-20261011-l.log`):
1918 tests276.854s/OK/skipped2(실행1916),process277.422s/exit0/source subset stable.
앞선 document readiness cadence·headroom 계측 및 이번 binding/nested phase 변경을
포함한다. 이 manifest는 dependency/OS 전체 고정이나 실제 G0~G10/백본/비교 과제
완료를 증명하지 않는다. rejected node batch 후보는 현재 소스·전체 검사에 포함하지 않는다.

남은 실제 격차는 preparation 뒤 disabled Save menu의 이유·responder readiness,
Finder와 다른 앱·백본의 반복, 동시 사용자 numeric caret·Save producer/receiver
인과 증거와 전체 G0~G10 감사다. 기존 실패 cohort는 계속 보존한다.
