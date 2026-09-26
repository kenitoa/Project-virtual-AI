# 치지직 채널 인증과 읽기 전용 채팅

치지직 공식 Open API를 사용합니다. 필요한 앱 권한은 **유저 조회**, **채팅 메시지 조회**입니다.
채팅 전송·스트림키 조회·방송 설정 변경 권한은 사용하지 않습니다.

## 최초 연결

1. 치지직 개발자센터에서 앱을 등록합니다. 로그인 리디렉션 URL은
   `http://127.0.0.1:8766/callback`으로 정확하게 설정합니다.
2. 인증정보를 Git 제외 파일 `secrets/chzzk-client.json`에 저장합니다.
   키 이름은 `client_id`, `client_secret`입니다. 실제 값을 문서나 명령행에 넣지 않습니다.
3. 다음 명령으로 계정 승인을 진행합니다.

```powershell
python -m uv run --locked python -m virtual_ai.integrations.chzzk_auth login --channel-id CHANNEL_ID
python -m uv run --locked python -m virtual_ai.integrations.chzzk_auth status --channel-id CHANNEL_ID
```

기본 브라우저 프로필이 다르면 `login`에 `--print-url`을 붙이고 출력된 승인 URL을 원하는
프로필에서 엽니다. URL은 일회용 로그인 상태를 포함하므로 공유하지 않습니다.
콜백은 localhost에만 열리며 최대 10분 후 닫힙니다. 임의 state·중복 응답은 거부합니다.
선택한 채널과 API가 반환한 채널 ID가 일치해야 Windows 자격 증명 저장소에 저장합니다.
저장 서비스는 `Project-virtual-AI.chzzk`, 항목은 `desktop`입니다. YouTube 자격 증명과 분리됩니다.

## 실행

`configs/app.yaml`의 설정:

```yaml
chzzk:
  enabled: true
  channel_id: "인증한 32자리 채널 ID"
```

앱은 기존 실행 방법으로 시작하며 채팅 활성화 시 일시정지 상태입니다. 운영자가 `/resume`해야
응답을 시작합니다. 첫 검증은 음성·VTS를 끈 별도 로컬 설정과 mock 백엔드로 진행합니다.
`--local-only`는 YouTube와 치지직을 모두 끕니다.

공식 세션 API와 WebSocket 텍스트 이벤트를 사용합니다. Engine.IO 3 heartbeat와 Socket.IO 기본
네임스페이스의 SYSTEM/CHAT 이벤트만 처리합니다. 4분마다 연결을 교체하여 인증 만료를 점검합니다.
통신 실패는 최대 3회 재연결하며 권한 취소·구독 불일치·잘못된 응답은 수신을 중단합니다.
연결 종료·앱 취소 시 소켓을 닫습니다. 갱신 토큰은 일회용이므로 갱신 결과가 불명확하면
이전 토큰을 재사용하지 않고 명시적 재로그인을 요구합니다.

작성자는 `chzzk` 플랫폼과 `senderChannelId`로 구분하며 권한 문자열은 운영 명령으로 사용하지
않습니다. 시청자의 `/stop`, `/quit`, `/forget`도 일반 데이터입니다. 오래된 메시지·시작 전 기록·
일시정지 이전 입력을 제외하고, ID가 없는 공식 이벤트는 채널·작성자·시간·내용의 해시로 중복을
제거합니다. 최대 4096개 해시만 메모리에 보관하며 원문·토큰을 로그에 출력하지 않습니다.
운영자 `/forget-viewer chzzk CHANNEL_ID`로 해당 시청자의 RAM 기록을 지울 수 있습니다.

## 검증 구분

인증 테스트와 모의 세션 검사는 실제 계정 승인·서버 구독·실제 채팅 수신을 대신하지 않습니다.
연결 로그 `chzzk_status=connected`는 구독 확인만 의미하며 실제 채팅 처리·음성 청취는 별도입니다.
실제 계정 연결 상태는 Git 제외 `.local/channel-connection-status.json`에 기록합니다.

공식 자료:
- [OAuth 인증](https://chzzk.gitbook.io/chzzk/chzzk-api/authorization)
- [유저 조회](https://chzzk.gitbook.io/chzzk/chzzk-api/user)
- [세션과 채팅 이벤트](https://chzzk.gitbook.io/chzzk/chzzk-api/session)
- [Engine.IO 3](https://github.com/socketio/engine.io-protocol/tree/v3)
