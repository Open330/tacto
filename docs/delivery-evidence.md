# 요청과 수신함을 결합한 전송 영수증

`nlc.delivery_receipt.DeliveredMessageReceipt`는 호스트가 실제 전송 요청과
수신 서비스의 새 메시지를 연결할 수 있는 경우 사용하는 in-process adapter다.
`prepare_commit`, `after_commit`, `commit_expected`를 기존 replay에 전달한다.
승인을 발급하거나 메시지를 보내는 기능은 없다. 카카오톡·Mail 등 native 앱에
자동으로 연결되지 않으며, 그 앱의 전송 성공 지원을 선언하는 API도 아니다.

호스트는 target·보내는 계정·받는 사람·정확한 본문을 먼저 고정한다. 본문은
UTF-8 bytes의 SHA256으로 비교한다. 뒤의 영수증이나 모델 답변으로 기대 본문을
바꾸지 않는다. 첨부의 identity·내용은 현재 계약에 없으므로 첨부 전송의 완료를
이 adapter로 증명할 수 없다.

## 준비와 수신 계약

`bind_request(event)`는 그 event의 실제 observation/element/action에 결합된
전송 control과 payload를 읽고 `PendingDelivery`를 반환한다. request_id,
target, obs_id, action_digest, account, recipient, body_sha256을 모두 대조한다.
기대값을 그대로 복사하는 callback, 버튼 이름만 확인하는 callback, 모델이
만든 딕셔너리는 구현이 아니다. click 및 기존 프로토콜의 Return press_key가
가능하지만 실제 runtime의 capability·submit·approval gate를 우회하지 않는다.

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
입력을 다시 보내지 않는다. 늦게 반환된 record도 수용하지 않는다. 하나의
OS/API 호출을 강제 취소하거나 원격 요청 비용을 중단하는 기능은 아니다.
본문·주소·서비스 예외의 원문을 public refusal에 넣지 않는다.

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
`tests/test_delivery_receipt_integration.py`는 headless Chromium의 실제 NLC
click→loopback HTTP POST→별도 인증 HTTP 수신함 조회를 실행한다. 시험용
계정·본문·서버만 사용하며 native GUI와 사용자 메시지에는 접근하지 않는다.

통합 fixture는 정확한 새 수신, 송신 Sent만 있는 no-op, 수신 본문 손상,
클릭 응답 유실 뒤 수신 증거와 중복 전송 없음, 기존 수신 요청 재사용 거부,
승인 거절, 실제 form의 수신인 변경 및 dispatch 전 거절을 포함한다.
이 결과는 실제 원격 서비스·native 메신저·첨부·다양한 백본 또는 NLC/Sky
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
