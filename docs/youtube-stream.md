# YouTube 실제 수신·재접속 검증

2026-09-23 readiness 미커밋 구현. **자동 검증 완료 / 실제 검증 대기**.
사용자가 테스트 방송 미준비로 확인했고 실제 수신은 대기로 기록하도록 요청했다.
현재 채팅 ID·수동 토큰·저장 OAuth 자격 증명 없이 YouTube 수신은 비활성이다.
테스트 방송을 생성하거나 메시지를 게시하거나 실제 시청자 기록을 저장하지 않았다.

## 전송 선택과 고정 프로토콜

기존 `transport: rest`를 기본으로 유지한다. 로컬 설정에서 `youtube.transport: stream`을
명시하면 TLS gRPC `dns:///youtube.googleapis.com:443`의
`/youtube.api.v3.V3DataLiveChatMessageService/StreamList`를 사용한다.
REST에 `stream=True`를 붙이는 방식이 아니며 두 전송 사이의 자동 대체는 없다.
두 경로 모두 기존 `ChatInput` → `Application.submit()`을 사용한다.
시청자·모델은 전송 선택, 재기준 설정, 운영 명령을 실행할 수 없다.

[공식 Python 예제·프로토콜](https://developers.google.com/youtube/v3/live/streaming-live-chat)을
`src/virtual_ai/integrations/_youtube_proto/`에 고정했다. 생성 코드·Apache 2.0 라이선스·출처·해시·재생성 명령을 포함한다.
원문에 누락된 `google/protobuf/duration.proto` import만 추가했다.
grpcio/grpcio-tools **1.84.0**, protobuf **7.36.2**를 정확한 버전으로 고정했고 `uv.lock`도 갱신했다.
시작 점검은 여전히 REST로 권한·채팅 상태를 확인한다. stream 선택 시 `rest_authenticated_stream_unverified`로
표시하며 이 점검만으로 gRPC 연결 성공을 주장하지 않는다.

## 수신과 복구 정책

| 상황 | 처리 |
| --- | --- |
| 최초 접속 | 실행 시각을 고정하고 첫 응답을 기준점으로만 사용한다. 이후 여러 응답에 걸친 초기 이력도 시작 시각 이전이면 제외한다. |
| 일시 단절 | 마지막 정상 응답의 `nextPageToken`을 보존한다. gRPC UNAVAILABLE/DEADLINE_EXCEEDED와 예기치 않은 EOF만 제한 재시도한다. |
| 반복 단절 | 실행 1회에 오류 재시도 총 3회, 대기 2/4/8초. 중간 정상 응답이 있어도 예산을 리셋하지 않아 반복 연결 폭주를 막는다. |
| 정상 연결 교체 | 최대 240초 또는 OAuth 갱신 여유 시각 전에 커서를 유지하고 교체한다. 응답이 한 번도 없는 연결의 시간 초과는 오류 예산을 사용한다. |
| 무효·누락 커서/잘못된 요청 | `rebaseline_required`로 중단한다. 커서를 지우고 자동 재접속하지 않는다. gRPC INVALID_ARGUMENT는 커서와 다른 인자 오류를 확정 구분할 수 없어 둘 다 운영자 확인을 요구한다. |
| 방송 종료 | `offline_at`/CHAT_ENDED_EVENT면 정상 종료한다. FAILED_PRECONDITION은 종료와 채팅 비활성화를 코드만으로 구분하지 않고 중단한다. |
| 인증·권한·할당량 | UNAUTHENTICATED/PERMISSION_DENIED/RESOURCE_EXHAUSTED/NOT_FOUND는 재시도하지 않는다. 인증 실패 시 OAuth 자격 증명을 폐기하고 명시적 login을 요구한다. |
| 종료·취소 | 스트림 RPC를 취소하고 채널을 닫는다. 일시 정지는 수신을 유지하지만 메시지를 버리고 위치만 갱신한다. |

커서 이어받기와 오류 분류는 [공식 streamList 계약](https://developers.google.com/youtube/v3/live/docs/liveChatMessages/streamList)을 따른다.
RPC의 자동 재시도는 끄고 앱에서 한도를 관리한다. 오류 원문·토큰·커서 값·시청자 텍스트는 로그에 쓰지 않는다.
로그에는 연결/재접속/중단과 고정 사유만 기록하고, 수신이 끝나면 `/status`의 `component_faults.youtube`에도 이유를 남긴다.
로컬 콘솔·다른 출력 기능은 유지한다.

REST도 시작 전 메시지 제외와 실행별 총 재시도 한도를 적용했다. HTTP 429는 할당량/속도 제한으로 즉시 중단하며,
통신 실패·5xx만 재시도한다. 400은 설정·커서를 확인하도록 중단한다. 두 전송 모두 최근 4096개 메시지 ID만
메모리에서 중복 제거하며 TTL과 재개 시각 필터를 적용한다. 프로세스 재시작·캐시 초과·네트워크 전체에 걸친
**완전한 exactly-once를 보장하지 않는다**. 보수적 초기 이력 제외 때문에 시작 순간의 일부 새 메시지도 제외될 수 있다.

## 명시적 재기준 설정

`rebaseline_required`가 발생해도 `/resume`이 수신 작업을 다시 만들지는 않는다.
운영자는 `/pause`와 `/status`로 상태를 확인하고 `/quit`으로 종료한다.
[OAuth 안내](youtube-auth.md)의 status/select로 계정·방송·채팅을 다시 대조한 뒤,
새 기준으로 시작한다는 판단 하에 앱을 재실행한다. 새 실행 시각과 첫 응답으로 기준을 다시 잡고,
기본 일시 정지를 유지한 채 연결을 확인한 후 `/resume`한다. 이전 커서를 파일로 저장하거나 임의로 주입하지 않는다.
PC 시계는 정확히 맞춘다. 과거 채팅을 다시 발화시키려는 자동 초기화는 없다.

## 실제 테스트 방송 체크리스트 — 미실행

먼저 OAuth 계정·선택 방송·채팅 ID를 준비한다. 실제 음성/VTS/OBS 검증이 아직 대기이므로,
채팅 수신 검증은 별도 로컬 설정에서 mock backend와 음성·VTS 비활성으로 먼저 수행한다.
기존 `configs/app.yaml`을 예시 파일로 덮어쓰지 않는다. 테스트용 방송은 운영자가 준비한다.

```powershell
python -m uv run --locked python -m virtual_ai --config configs/app.yaml --backend mock --live
```

| 시험 | 통과 기준 | 실제 결과 |
| --- | --- | --- |
| REST 최초 접속 | 시작 전 작성 메시지 미발화, /resume 후 새 메시지 입력 도착 | 대기 |
| stream 최초 접속 | gRPC 연결 기록과 새 메시지 입력, 여러 초기 이력 응답 미발화 | 대기 |
| 일시 네트워크 차단·복구 | 마지막 커서 유지, 중복 제외, 새 메시지 재개 | 대기 |
| 장기 단절·반복 연결 실패 | 총 오류 재시도 3회 후 중단, 콘솔 제어 유지 | 대기 |
| 무효 커서·요청 | 중단 사유 표시, 자동 이력 초기화 없음, 운영자 재기준 설정 | 대기 |
| 방송 종료·채팅 비활성 | 수신 종료, 무한 재시도 없음 | 대기 |
| 권한 취소·할당량 | 수신 중단, 자동 승인창 없음 | 대기 |
| pause/resume·panic | 정지 기간 메시지의 뒤늦은 발화 없음 | 대기 |
| OAuth 연결 교체 | 갱신 후 같은 커서로 재개, 중복·과거 채팅 폭주 없음 | 대기 |
| /quit | RPC·채널 정리, 재실행은 새로운 일시 정지 기준점 | 대기 |

실제 시험에는 커밋, 전송 방식, 고정 프로토콜 해시, 의존성 버전, 시험 시각, 입력 도착 건수,
중복·누락·재시도 횟수·중단 사유만 기록한다. 실제 토큰·커서·시청자 원문은 검증 문서에 넣지 않는다.
할당량을 고의로 소진하거나 실제 서버의 토큰을 조작하지 말고, 해당 오류는 우선 모의 서버로 재현한다.

## 자동 검증 증거

`test_youtube_stream.py`에서 이력·중복·커서 이어받기·무효 커서·권한/할당량·종료·EOF·재시도 한도·
연결 교체·OAuth 메타데이터 교체·취소·일시 정지를 검사한다. 로컬 gRPC 서버로 생성 코드의 실제 직렬화,
UNAVAILABLE 후 재접속과 채널 종료도 확인했다. 이 서버는 테스트용 loopback이며 Google 연결 성공이 아니다.
REST·OAuth·운영 제어·시작 점검을 합친 관련 검사 116개 통과. 고정 프로토콜에서 생성 파일을 재생성하여
기존 파일과 바이트 단위로 일치함을 확인했다. 실제 YouTube 수신 완료·장시간 운영 승인·병합은 대기다.
전체 514개와 하위 검사 9개, locked 동기화·Ruff lint/format·mock 실행도 통과했다.
