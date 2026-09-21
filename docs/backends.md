# 백엔드

`mock`/`fake`는 네트워크 없이 고정 응답을 제공합니다. `koboldcpp`는 재사용하는
`httpx.AsyncClient`로 POST `/v1/chat/completions`를 호출합니다.
기존 LLMClient 계약의 `generate(messages)`, `aclose()`를 구현하며 종료 시 연결을 닫습니다.
요청은 `model`, `messages`, `max_tokens`, `stream: false`로 구성합니다.
응답 크기는 256 KiB로 제한하고 `choices[0].message.content`의 비어 있지 않은 문자열만 반환합니다.
출력 검사는 `app.py`의 `prepare_response()`에서 수행합니다.

## 검증 기록 (2026-09-21)

| 항목 | 결과 |
| --- | --- |
| 로컬 환경 | Windows 11 build 26200, Python 3.14.7, uv 0.12.16, HTTPX 0.28.1 |
| 자동 검증 | MockTransport 기반 요청·응답·재시도·시간 제한·종료·로그 테스트, 모델/GPU 불필요 |
| 실제 서버 | 기본 주소 `127.0.0.1:5001`의 TCP 연결 확인 실패; 실제 생성 미검증 |
| KoboldCpp 버전 | 미확인 |
| 실제 모델 파일·양자화·템플릿 | 미확인 (`Qwen3-8B`는 설정 예시) |
| GPU·VRAM·오프로딩 설정 | 미확인 |

실제 검증 시 서버 버전, 모델 파일명·양자화, 채팅 템플릿, OS·GPU·VRAM, 실행 옵션을
추가 기록합니다. `retries: 0`으로 README의 단일 입력 명령을 실행하고 출력과 종료 코드를
확인하세요. 자동 테스트 통과는 실제 모델의 응답 품질이나 서버 연결 성공을 뜻하지 않습니다.
모델 및 외부 프로그램, 실제 대화 원문과 비밀값은 커밋하지 않습니다.

## 참고

- [KoboldCpp 공식 API 안내](https://github.com/LostRuins/koboldcpp/wiki#with-chat-completions-how-do-i-control-how-many-tokens-the-ai-outputs)
- [HTTPX 비동기 클라이언트와 종료](https://www.python-httpx.org/async/)
- [HTTPX 단계별 타임아웃](https://www.python-httpx.org/advanced/timeouts/)
- [HTTPX MockTransport](https://www.python-httpx.org/advanced/transports/#mock-transports)
