# TextEdit 새 이름 저장의 호스트 증거

`nlc.textedit_receipt.make_textedit_receipt`는 명시한 새 파일에 현재 문서의
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
