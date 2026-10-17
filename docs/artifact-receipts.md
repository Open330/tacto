# 새 파일 저장의 host receipt

`FreshFileReceipt`는 실행 전에 host가 지정한 경로와 UTF-8 본문을 기준으로,
실행 후 새 파일의 실제 바이트를 읽어 `CommitEvidence`를 발급합니다.
화면 제목이 바뀌거나 저장 버튼이 정상 응답했다는 사실만으로는 발급하지 않습니다.
이 adapter는 파일을 생성하거나 UI를 조작하지 않습니다.

## 지원 범위

- host가 전용으로 준비한 기존 디렉터리: 현재 사용자 소유, 권한 `0700`,
  symlink가 없는 절대 경로
- 처음에는 존재하지 않는 파일 이름 한 개
- host가 실행 전에 지정한 정확한 UTF-8 바이트
- 준비 한 번, 결과 확인 한 번인 단일 commit
- POSIX `O_NOFOLLOW`, directory descriptor를 제공하는 환경

RTF를 디코딩하거나 줄바꿈·인코딩을 보정하지 않습니다. TextEdit 실앱 연결은
plain-text 문서와 `.txt` 목적지부터 검증해야 합니다. 현재 adapter의 파일
검사가 통과한 것과 TextEdit의 실제 저장 UI 경로가 검증된 것은 다릅니다.

## 연결

host가 별도로 전용 디렉터리를 준비하고 사용자 요청·신뢰된 사전 정보에서
목적지와 본문을 확정한 뒤 연결합니다.

```python
from pathlib import Path
from tacto.artifact_receipt import FreshFileReceipt
from tacto.flow import replay

receipt = FreshFileReceipt(
    Path(private_directory).resolve(),
    filename="report.txt",
    text=expected_text,
    timeout_seconds=2.0,
)
result = replay(
    driver, flow, approvals=approvals,
    prepare_commit=receipt.prepare_commit,
    after_commit=receipt.after_commit,
    commit_expected=receipt.commit_expected,
)
```

driver의 `prepare_commit`, `after_commit`, `commit_expected`로 같은 객체의
속성을 연결할 수도 있습니다. 실제 `TracingDriver`는 이 속성을 전달합니다.
녹화된 `TraceDriver`의 재생은 현재 파일 저장에 대한 새로운 host 증거가 아닙니다.

`general.run`에는 같은 두 callback을 전달하고, 같은 사전 기대값을 가진
`OutcomeSpec`을 사용합니다. 결과를 읽은 뒤 그 값으로 기대값을 바꿔 통과시키면
검증이 아닙니다. `commit_expected`는 호출할 때마다 별도 사본을 반환합니다.

general/replay가 만드는 UI event에는 실제 `driver.act`에 사용할 `obs_id`가
포함되며 action digest에도 묶입니다. 관측이 다르면 같은 element index도 다른
바인딩입니다. 직접 만든 비 UI 호스트 event는 관측 ID 없이 사용할 수 있습니다.
호스트가 digest를 직접 계산한다면 `action_digest(..., obs_id=event.get('obs_id'))`를
사용해야 합니다. 이 바인딩은 실제 버튼 identity나 작업 성공의 증거를 대신하지 않습니다.

## 거절과 제한

준비 시 목적지가 이미 있으면 거절합니다. 실행 후에는 디렉터리 identity,
단일 링크의 일반 파일, 파일 소유자, 정확한 경로·바이트와 readback 전후
identity·metadata를 검사합니다. symlink, hardlink, 다른 디렉터리로 교체,
readback 도중 또는 직후의 파일 교체를 발견하면 거절합니다. 같은 context를
재사용하거나 다른 event의 receipt로 가져갈 수 없습니다.

없는 파일은 제한된 시간 동안 기다립니다. 시간이 끝나거나 예산이 0이면
receipt를 반환하지 않습니다. 기존 파일·잘못된 바이트·경계 위반은
`ArtifactReceiptError` 또는 관련 `OSError`로 거절합니다. 잘못된 바이트를
발견한 후 다시 저장하거나 정상 값이 될 때까지 무제한 기다리지 않습니다.
확인 시간은 producer 취소나 운영체제 read의 강제 중단을 보장하지 않습니다.

**producer attribution은 host의 신뢰 경계입니다.** 이 파일 검사는 해당
앱이 파일을 썼다는 것을 운영체제 수준에서 인증하지 않습니다. 동일 사용자
권한의 다른 프로세스가 정답 바이트를 쓰면 구분할 수 없으므로, host가 대상
디렉터리의 단독 사용을 보장해야 합니다. 마지막 검사 이후 파일이 바뀌는 것도
막지 않습니다. receipt는 그 관측 시점의 결과이며 영구 불변 보증이 아닙니다.

회귀 검사는 실제 임시 파일을 사용하지만 native UI나 외부 모델을 호출하지
않습니다. 기존 파일·잘못된 내용·경로 교체·symlink·hardlink·중복 완료·시간
예산 및 지연 생성 경계를 확인합니다.

Formatted TextEdit output uses a separate [RTF receipt adapter](rtf-receipts.md). It compares exact decoded text inside the host callback while the artifact digest remains the hash of raw RTF bytes. It is not interchangeable with the UTF-8 receipt described above.
