# TextEdit 새 이름 저장의 호스트 증거

`nlc.textedit_receipt.make_textedit_receipt`는 명시한 새 파일에 현재 문서의
정확한 **텍스트**가 저장됐는지 확인하는 실행별 adapter다. CLI/Agent 연결은 있으나
native backend가 아직 필요한 capability를 제공하지 않으므로 실앱 검증 저장은 미활성이다.

읽기 전용 선행 기능인 in-process `Driver.document_text`는 실제 TextEdit에서
검증했다. `evidence.document_text`는 완전 본문·canonical AXDocument 경로와
세션에 귀속된 문서 token을 제공한다. 앱 실행 인스턴스와 창/본문 AX 객체를
전후 확인하며 다른 세션·교체된 문서·부분 조회·예산 초과는 거절한다.
예산은 UTF-16 기준 0~1,000,000 units이고, 초기 문서가 여러 개면 고르지 않는다.
기존 token으로는 여러 문서 중 같은 문서를 계속 읽을 수 있다. 일반 observe
갱신으로 token을 만료시키지 않으며 reader별 최근 발급 32개만 유지한다.
AX가 노출한 문서 구조를 기준으로 하며, 표준 창에서 본문을 식별할 수 없으면
다른 문서가 명확하더라도 전체 조회를 거절한다. AX가 숨긴 요소까지 존재하지
않는다고 증명하는 기능은 아니다.

이 API는 아래 `document_snapshot`과 다르다. 현재 문서 형식과 저장 패널의
소유 관계/목적지/파일명/형식을 아직 증명하지 않으므로 저장 receipt를 활성화하지
않는다. 연속 조회 사이의 변경 후 복귀(ABA)도 배제하지 않는다. live trace wrapper는
호출을 전달하되 본문을 trace에 복사하지 않고, 기록 재생에는 이 capability를 주지 않는다.

호스트가 `driver`, `target='com.apple.TextEdit'`, canonical `~/Downloads`의
절대 경로 `directory`, 단일 파일명 `filename='보고서.rtf'`를 전달한다.
흐름 이름이나 모델 결과로 목적지를 추정하지 않는다. 저장 이름 변경, 덮어쓰기,
다른 디렉터리, 일반 텍스트 문서에서 RTF로의 변환, RTFD/첨부 문서 등은 현재 지원하지 않는다.

반환 객체의 `prepare_commit`, `after_commit`, `commit_expected`를 `flow.replay`에
명시적으로 전달한다. adapter는 1회용이다. 승인과 실제 UI 저장은 기존 실행기가
담당하며 adapter는 파일을 생성하거나 내용을 수정하지 않는다.

## 공통 backend 계약

capability `evidence.document_snapshot`이 있어야 하며 다음 API를 제공해야 한다.

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
