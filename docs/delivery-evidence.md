# 요청과 수신함을 결합한 전송 영수증

`tacto.delivery_receipt.DeliveredMessageReceipt`는 호스트가 실제 전송 요청과
수신 서비스의 새 메시지를 연결할 수 있는 경우 사용하는 in-process adapter다.
`prepare_commit`, `after_commit`, `commit_expected`를 기존 replay에 전달한다.
승인을 발급하거나 메시지를 보내는 기능은 없다. 카카오톡·Mail 등 native 앱에
자동으로 연결되지 않으며, 그 앱의 전송 성공 지원을 선언하는 API도 아니다.

호스트는 target·보내는 계정·받는 사람·정확한 본문을 먼저 고정한다. 본문은
UTF-8 bytes의 SHA256으로 비교한다. 뒤의 영수증이나 모델 답변으로 기대 본문을
바꾸지 않는다. 첨부를 검증할 경우 host가 읽은 AttachmentEvidence manifest를
명시적으로 전달하고 실제 전송 control 및 수신 측 바이트와 대조한다.
이 adapter 자체는 파일을 선택하거나 업로드하지 않는다.

## 준비와 수신 계약

`bind_request(event)`는 그 event의 실제 observation/element/action에 결합된
전송 control과 payload를 읽고 `PendingDelivery`를 반환한다. request_id,
target, obs_id, action_digest, account, recipient, body_sha256을 모두 대조한다.
기대값을 그대로 복사하는 callback, 버튼 이름만 확인하는 callback, 모델이
만든 딕셔너리는 구현이 아니다. click 및 기존 프로토콜의 Return press_key가
가능하지만 실제 runtime의 capability·submit·approval gate를 우회하지 않는다.

준비는 전송 binding 읽기→수신 record 부재 조회→같은 binding 재읽기 순서다.
`bind_request`는 준비 중 두 번 호출될 수 있는 읽기 전용 callback이어야 하며,
호출마다 request ID를 만들거나 payload를 변경해서는 안 된다. 두 읽기의 전체
PendingDelivery가 달라지면 `delivery_binding_changed`, host 기대와 다른 payload는
`delivery_binding_mismatch`, 재읽기 실패는 `delivery_binding_unavailable`로 거절한다.
준비 도중 event ID/digest가 바뀌어도 context를 반환하지 않는다. 이 실패도 준비
시도를 소모하므로 같은 adapter를 재사용하지 않는다. after 단계의 수신함 polling은
binding을 다시 읽거나 전송하지 않는다. 이 재확인은 마지막 읽기와 실제 입력의
atomicity·ABA·service 인증을 증명하지 않으며, immutable request/payload와 독립된
수신 서비스 계약은 계속 필요하다.

`read_inbox(request_id)`는 인증된 수신 서비스에서 해당 요청의 실제 수신함
record를 읽는다. record가 아직 없을 때만 None이다. 권한·연결·부분 조회 등으로
모르면 예외로 거부해야 한다. 반환값은 `InboxDelivery`이며 request_id,
고유한 message_id, account, recipient, 정확한 본문 digest와 `state='delivered'`를
모두 확인한다. 송신 화면의 Sent, API의 accepted/queued와 수신함 반영은 다르다.
처음부터 이 요청의 수신 record가 있으면 prepare에서 거부한다.

서비스는 request_id를 한 immutable payload와 새 고유 message_id에 연결해야
한다. 호스트는 그 요청 identity를 독점 사용하고 실제 전송 control에 연결해야
한다. 타입·hash 자체로 서비스 인증이나 생산자 attribution을 얻는 것은 아니다.
같은 권한의 다른 작성자가 그 요청을 사용할 수 있는 환경은 이 호스트 계약을
충족하지 않는다. 기존 native 메신저에 request_id가 노출되지 않거나 독립 수신
조회가 없다면 이 adapter를 억지로 연결하지 않는다.

prepare 한 번, after 한 번만 허용한다. after는 수신 record만 마감까지 읽고
입력을 다시 보내지 않는다. 늦게 반환된 record도 수용하지 않는다.
수신 reader와 최종 clock hook 뒤 원래 event ID와 target/obs/action/effects의
재계산 digest를 다시 확인한다. 바뀌거나 malformed면 delivery_commit_mismatch로
증거 반환을 거부하며 after attempt는 소모된 상태다. 실제 수신은 이미 일어났을
수 있으므로 이 거부를 전송 실패나 재전송 허가로 해석하지 않는다. 이 재확인은
host event의 ABA나 다른 thread와의 atomicity를 증명하지 않는다. 하나의
OS/API 호출을 강제 취소하거나 원격 요청 비용을 중단하는 기능은 아니다.
본문·주소·서비스 예외의 원문을 public refusal에 넣지 않는다.

### dispatch 응답 확인

직접 호출자도 실제 dispatcher의 응답을 `after_commit`에 넘겨야 한다.
수신 record가 일치한다는 사실만으로 이 event의 입력 여부를 대신하지 않는다.
`effect='not_started'` 또는 confirm_request/handoff_request/observation/probe는
`delivery_dispatch_not_started`로 거절하며 inbox를 추가 조회하지 않는다.
None, 빈 딕셔너리, 비-dict 또는 지원하지 않는 type은
`delivery_dispatch_unverified`로 거절한다. 기존의 빈 응답 직접 호출은 더 이상
지원하지 않는다. 어댑터는 이 경우에도 소비되므로 응답을 바꿔 다시 호출할 수 없다.

허용하는 dispatch type은 acted와 error다. 실제 응답 유실로 error/unknown이
기록된 경우에는 같은 새 요청의 권위 있는 수신 증거로 완료를 확인할 수 있다.
acted에 unverified가 있어도 수신 측의 권위 있는 새 기록을 확인할 수 있다.
이는 저장된 파일 상태만 확인하는 ExistingTextFileReceipt와 증거 범위가 다르다.
이를 위해 승인·응답·전송 여부를 지어내거나 메시지를 다시 보내지 않는다.
replay는 이미 입력 전 거절에서 after_commit을 호출하지 않으며, 어댑터의
검사는 직접 호출자에게도 같은 경계를 적용한다.

### 일시적인 수신함 읽기 장애

신뢰된 reader가 읽기 전용 요청의 일시적인 서비스 장애를 확인한 경우에만
`InboxTemporarilyUnavailable`(하위 클래스 포함)을 발생시킬 수 있다. 예를 들어 호스트가 자신의
수신 API에서 HTTP503을 일시적인 읽기 장애로 분류할 때 사용한다.
인증·권한 오류, 잘못된 record, 부분 조회 및 프로그램 오류를 이 신호로
바꾸지 않는다. 모든 예외를 이 타입으로 감싸는 reader는 이 계약에 맞지 않는다.

prepare에서 이 신호를 받으면 `inbox_read_unavailable`로 거부한다. 이전
수신 record가 없는지 확인하지 못했으므로 전송을 허용할 수 없다. after에서만
동일 request_id의 수신함을 남은 마감 안에서 다시 읽는다. bind나 click,
전송 POST는 반복하지 않는다. 계속 조회할 수 없으면 미검증으로 중단한다.
일반 예외와 account/recipient/body/message/state 불일치는 즉시 거부하며,
마감에 도달하면 새 읽기를 시작하지 않는다. 이미 진행 중인 reader는 이
adapter가 강제 취소할 수 없고 마감 후 반환된 record는 수용하지 않는다.

```python
from tacto.delivery_receipt import InboxTemporarilyUnavailable

def read_inbox(request_id):
    response = authenticated_receiving_service.read(request_id)
    if response.temporary_service_unavailable:
        raise InboxTemporarilyUnavailable()
    # 인증/형식/부분 조회 오류는 별도 예외로 거부한다.
    # 완전한 조회에서 아직 수신 record가 없을 때만 None을 반환한다.
    return validated_inbox_delivery_or_none(response)
```

이 예시는 host bridge의 신호 계약이며 특정 원격 서비스용 구현이 아니다.

## 응답 유실과 실행기

replay는 dispatch의 error/unknown 뒤에도 유효한 operation-bound host 영수증을
얻으면 commit을 confirmed로 기록한다. 이 경우 dispatch_response에
error/unknown을 보존하고 후속 처리를 진행한다. 영수증이 없으면 unverified로
중단하며 전송을 재시도하지 않는다. not_started 거절은 after callback으로
완료로 바꾸지 않는다. 뒤 단계의 독립 UI postcondition 실패까지 영수증으로
무시하는 기능은 아니다.

## 검증 범위

`tests/test_delivery_receipt.py`는 새/기존 요청·잘못된 수신인/계정/본문·queued·
모델 JSON·이전 관측/다른 action·한 번 사용·권한/조회 실패·마감을 검사한다.
`tests/test_delivery_receipt_integration.py`는 headless Chromium의 실제 Tacto
click→loopback HTTP POST→별도 인증 HTTP 수신함 조회를 실행한다. 시험용
계정·본문·서버만 사용하며 native GUI와 사용자 메시지에는 접근하지 않는다.

통합 fixture는 정확한 새 수신, 송신 Sent만 있는 no-op, 수신 본문 손상,
클릭 응답 유실 뒤 수신 증거와 중복 전송 없음, 기존 수신 요청 재사용 거부,
승인 거절, 실제 form의 수신인 변경 및 dispatch 전 거절을 포함한다.
실제 HTTP503 뒤 수신함 읽기 복구, dispatch 응답 유실과 HTTP503의 결합,
지속적인503의 미검증 종료, 수신 인증403의 즉시 거부도 검사한다. 복구 사례는
단일 전송 POST와 새로운 수신 message identity·정확한 본문을 대조하며,
장애 사례에서도 전송이 중복되지 않았는지 검사한다.
이 결과는 실제 원격 서비스·native 메신저·첨부·다양한 백본 또는 Tacto/Sky
동일 조건 비교의 완료 증거가 아니다. 그 실기와 서비스별 bridge는 남아 있다.

## 리뷰 후 강화한 본문·실행 수명 계약

`commit_expected`는 계정·받는 사람에 더해 `보낸 내용 sha256`을 반환한다.
공통 `evidence_error`도 그 호스트 기대 digest와 DeliveryEvidence를 독립적으로
비교한다. digest 형식 불량·다른 본문·원문/해시 기대값 충돌은 거부한다.
기대 본문 원문을 이 필드 때문에 trace에 추가하지 않는다.

replay는 prepare를 confirm gate 전에 호출할 수 있다. 승인되지 않은 실행도
그 adapter의 준비 시도를 소모한다. 실행마다 새 adapter를 만들며, 승인 후
새 요청을 실행할 때는 서비스의 새 request identity와 실제 control을 다시
결합한다. 이전 adapter/context/영수증을 재사용하지 않는다. 실제 HTTP fixture의
gated→adapter 재사용 거부→새 request/새 adapter 승인 실행을 검증한다.

request_id는 URL-safe ASCII `[A-Za-z0-9_-]`1~128자다. message_id는1~1024자의
printable opaque 문자열로 colon·dot·@·Unicode를 그대로 보존한다. 앞뒤 공백·
control 문자와 초과 길이는 거부한다. message_id를 URL·경로·명령으로 실행하거나
자동으로 정규화하지 않는다. 실제 서비스의 요청 ID 규약은 host bridge가 맞춰야 한다.


## 첨부 manifest (2026-10-11)

`AttachmentEvidence(filename, sha256, size_bytes)`의 immutable tuple을 생성자의
`attachments` 인자로 전달한다. PendingDelivery와 InboxDelivery에도 같은 필드가
있다. bind callback은 실제 선택된 파일 바이트를 읽고, inbox callback은 실제
수신된 첨부 바이트를 읽어 manifest를 만들어야 한다. 기대 manifest를 그대로
복사하거나 파일명만 확인하는 callback은 검증이 아니다. manifest는 순서까지
고정하며, 수신 서비스의 순서가 달라지면 일치하지 않은 것으로 거부한다.

기본값 None은 첨부 미관찰/body-only 범위다. 기존 adapter가 첨부 없음까지
검증한 것으로 승격하지 않는다. 명시적 `attachments=()`는 0개 첨부를 요구하며
pending/inbox 양쪽에서 빈 tuple을 실제로 관찰해야 한다. 예상 첨부가 있으면
이름·hash·바이트 수의 누락/추가/변경 모두 거부한다. DeliveryEvidence에 관찰한
manifest가 포함된 경우 공통 evidence_error에도 host 기대 manifest가 필요하다.

최대100개, 이름 UTF-8 최대1024 bytes, 파일별0..4GiB byte count를 허용한다.
파일명 중복·경로 separator·control/앞뒤 공백·'.'/'..', bool 크기·음수·잘못된
hash·mutable list·모델 dict를 거부한다. 이 한도는 업로드 지원 용량이 아니라
증거 자료의 검증 한도다. 같은 이름의 여러 첨부는 현재 지원하지 않는다.
파일명은 receipt/trace에 남을 수 있으므로 원본 trace는 private하게 보관한다.
타입이나 hash만으로 서비스 인증, 원본 file identity 또는 수신자 전달을 증명하지
않는다. 메시지 request identity와 수신 측 reader 계약도 계속 충족해야 한다.

실제 headless Chromium에서 소유 한글 파일을 file input에 fixture bootstrap으로
선택하고 Tacto replay가 Send를 눌렀다. loopback 수신 서비스는 받은 바이트로 hash와
크기를 계산했으며 event-bound receipt가 완료됐다. 별도 corrupted attachment
사례는 unverified/POST1이었다. 파일 선택 동작은 Tacto 기능 평가가 아니다.
전체 관련71 tests/22.603s/OK/skip 없음과 원본은
`reviews/2026-10-11/delivery-attachment-regression-20261011-a.log/.json`에 있다.
이 중16개는 실제 Chromium/HTTP 통합, 나머지는 mock/unit 검사다. 모델 호출과
native GUI 입력은 없었다. source stable은 report manifest.files의 측정 파일
전후 동일만 뜻한다. 실제 메일/native 앱 및 외부 수신 서비스는 미검증이다.
