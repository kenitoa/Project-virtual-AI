# YouTube Desktop OAuth와 토큰 수명

2026-09-23 readiness 미커밋 구현. 자동 검사와 실제 Google 승인을 구분한다.
`youtube_auth.py`는 읽기 전용 인증, `youtube.py`는 채팅 수신을 담당한다.
기존 수동 환경변수 토큰 방식은 `auth_mode: manual`에서 그대로 사용한다.

## Cloud 준비와 최초 승인

1. 사용할 Google Cloud 프로젝트에서 YouTube Data API v3를 활성화한다.
2. Google Auth Platform에서 앱 이름·대상 사용자·동의 화면을 준비하고 테스트 상태라면 사용할 계정을 테스트 사용자로 등록한다.
3. **Desktop app** OAuth 클라이언트를 생성한다. Web 앱 클라이언트 파일은 거부한다.
4. 받은 JSON을 Git 제외 경로 `secrets/youtube-desktop.json`에 보관하거나 별도의 보호된 로컬 경로를 사용한다.
   다운로드 파일에는 클라이언트 자격 증명이 있으므로 공유·커밋하지 않는다. 사용자 토큰은 이 파일에 쓰지 않는다.
5. 아래 명령으로 승인한다. 승인 브라우저는 이 명령에서만 열리며 앱 실행·갱신 실패·상태 조회는 자동 로그인하지 않는다.

```powershell
python -m uv sync --locked
python -m uv run --locked python -m virtual_ai.integrations.youtube_auth login --client-file secrets/youtube-desktop.json
python -m uv run --locked python -m virtual_ai.integrations.youtube_auth status
python -m uv run --locked python -m virtual_ai.integrations.youtube_auth broadcasts
python -m uv run --locked python -m virtual_ai.integrations.youtube_auth select --channel-id CHANNEL_ID --broadcast-id BROADCAST_ID
```

시스템 브라우저, 임의 포트의 `127.0.0.1` 콜백, 매회 무작위 `state`, PKCE S256을 사용한다.
콜백은 180초 후 닫히며 잘못된 state·중복 state·코드 누락은 교환하지 않는다. 거부·시간 초과는 수동 재시도한다.
이 흐름은 [Google Desktop OAuth 안내](https://developers.google.com/identity/protocols/oauth2/native-app)를 따른다.

요청 범위는 `https://www.googleapis.com/auth/youtube.readonly` 하나다.
이메일/OpenID·채팅 쓰기·삭제·방송 시작 권한은 요청하지 않는다. `status`의 계정 식별자는 이메일이 아니라
인증된 YouTube 채널 ID/제목이다. OAuth 동의 화면의 Google 계정과 채널, 방송 ID, 채팅 ID를 각각 확인한다.
방송 목록에서 첫 항목을 자동 선택하지 않는다. `select`는 지정 방송의 실제 `snippet.liveChatId`를 조회하여 표시하며 설정 파일을 덮어쓰지 않는다.
방송 제목의 제어 문자도 JSON 이스케이프 출력한다.
[방송 목록 API](https://developers.google.com/youtube/v3/live/docs/liveBroadcasts/list)와
[채널 목록 API](https://developers.google.com/youtube/v3/docs/channels/list)를 사용한다.

확인한 세 ID를 로컬 설정에 넣는다. 토큰·client secret은 YAML에 넣지 않는다.

```yaml
youtube:
  enabled: false # 실제 방송 수신 검증 준비 후에만 true
  auth_mode: oauth
  channel_id: "확인한 채널 ID"
  broadcast_id: "선택한 방송 ID"
  live_chat_id: "select로 조회한 채팅 ID"
```

OAuth 모드에서는 환경변수 수동 토큰을 무시한다. 수신 시작과 시작 점검에서 채널 소유 관계·방송 ID·채팅 ID를
대조하고 종료/취소된 방송은 거부한다. 불일치 시 임의 방송으로 전환하지 않는다. 운영자 `/resume` 전 기본 일시 정지도 유지한다.

## 저장·갱신·오류·로그아웃

`keyring`을 잠금 의존성에 추가했으며 Windows Credential Manager, macOS Keychain, Linux Secret Service를
명시적으로 선택한다. 평문 keyring 플러그인·파일·환경변수로 자동 대체하지 않는다. Linux는 로그인 세션의
Secret Service가 필요하다. 보호 저장소를 사용할 수 없으면 인증을 중단한다.
저장 항목은 서비스 `Project-virtual-AI.youtube`, 사용자 `desktop` 하나다. 현재 OS 사용자당 한 계정을 사용하며
재로그인하면 교체한다. 다른 OS 사용자·개발자는 자신의 계정으로 승인해야 한다.
저장 데이터에는 access/refresh token, 만료 시각, scope, 갱신에 필요한 Desktop client 정보가 포함된다.
이는 [Google 토큰 보관·폐기 지침](https://developers.google.com/identity/protocols/oauth2/resources/best-practices)의 OS 보호 저장 원칙을 따른다.

- 요청 직전에 저장소를 읽고 만료 60초 전이면 한 번 갱신한다. 동시 갱신은 세션 잠금으로 직렬화한다.
- 새로운 refresh token이 있으면 교체하고 없으면 이전 값을 유지한다. 예상하지 않은 scope나 형식은 거부한다.
- 갱신 실패는 네트워크 실패를 포함해 저장 토큰을 지우고 해당 세션을 잠근다. 무한 재시도·자동 승인창은 없다.
- API 401이면 토큰을 지우고 수신을 중단한다. 403/권한·할당량 오류도 수신을 중단하지만 무조건 토큰을 삭제하지 않는다.
- 운영자는 원인을 확인하고 필요하면 `login`, 계정·방송 확인, 앱 재시작을 한다. `/resume`만으로 죽은 채팅 작업이 재생성되지는 않는다.
- 수신 중단은 로컬 콘솔·음성 앱을 종료하지 않는다. 로그에는 고정 상태·복구 안내만 기록한다.
- 서버 본문·토큰·승인 코드·PKCE verifier를 출력하지 않는다. HTTP 디버그 덤프나 브라우저 콜백 URL을 공유하지 않는다.

```powershell
# 먼저 실행 중인 앱에서 /pause 후 /quit으로 수신 종료
python -m uv run --locked python -m virtual_ai.integrations.youtube_auth logout
```

로그아웃은 먼저 로컬 토큰을 제거하고 Google에 POST로 폐기 요청을 한 번 보낸다. 네트워크 실패 시에도 로컬 삭제는 유지하며,
원격 폐기 미확인 안내가 나오면 Google 계정의 앱 연결 관리에서 직접 해제한다. 저장소 삭제 실패는 성공으로 보고하지 않는다.
앱과 인증 CLI를 동시에 조작하지 않는다. 수동 모드의 환경변수는 OAuth 로그아웃으로 지워지지 않으므로 별도로 제거한다.

## 검증표

관련 테스트 76개, 전체 487개와 하위 검사 9개 통과. Windows 자격 증명 관리자에는
무작위 테스트 서비스 이름과 비밀값 없는 임시 항목을 사용해 실제 저장·조회·삭제를 확인했다.
실제 Google 토큰은 사용하지 않았다. macOS/Linux 저장소의 실제 검증은 대기다.

| 시나리오 | 자동 검증 | 실제 Google/장치 |
| --- | --- | --- |
| 최초 승인·코드 교환 | 실제 loopback 소켓 + 가짜 브라우저/HTTP, state·PKCE 검사 | 대기 |
| 재실행·저장 토큰 사용 | 저장소 fixture 재사용, 브라우저 호출 없음 | 대기 |
| 만료·동시 갱신·토큰 회전 | 가짜 시계/응답으로 60초 여유와 직렬화 검사 | 대기 |
| 갱신 실패·권한 취소 | invalid_grant·통신 실패·401, 한 번 시도 후 중단/삭제 | 대기 |
| 계정·방송·채팅 불일치 | 각각 다른 ID 거부 | 대기 |
| 로그아웃·원격 폐기 실패 | 로컬 삭제 유지·세션 재사용 차단 | 대기 |
| 수동 모드·OAuth 모드 분리 | 환경변수/보호 저장소 자동 대체 없음 | 대기 |

실제 승인에는 운영자의 Cloud 프로젝트·Desktop 클라이언트·계정 선택이 필요하다. 이번 구현만으로 승인·실제 채팅 수신 완료로 판정하지 않는다.
