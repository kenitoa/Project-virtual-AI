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
| 실제 서버 | `http://127.0.0.1:5001` 연결 실패; 실제 생성 및 재시작 복구 미검증 |
| KoboldCpp 버전 | 미확인 |
| 실제 모델 파일·양자화·템플릿 | 미확인 (`Qwen3-8B`는 설정 예시) |
| CPU / RAM | Intel Core i5-13420H / 31.71 GiB |
| GPU / VRAM / 드라이버 | NVIDIA GeForce RTX 4050 Laptop GPU / 6141 MiB / 610.78 |
| 모델 다운로드 출처 | 미확인; 실행 파일과 GGUF 경로 확인 필요 |
| 컨텍스트 / GPU 오프로딩 / 채팅 템플릿 | 서버 미실행으로 미확인 |

### 실제 연결 검증 진행 상태

로컬 `configs/app.yaml`을 예시에서 생성했습니다(기존 파일 없음, Git 제외 확인).
`backend: koboldcpp`, `retries: 0`, 단계별 제한 30초, 전체 제한 60초로 설정했습니다.
`model: Qwen3-8B`는 아직 서버와 대조하지 않은 임시 식별자입니다.

실행한 명령(`uv`가 PATH에 없어 `python -m uv` 사용):

```powershell
python -m uv run --locked python -m virtual_ai --config configs/app.yaml --once "한 문장으로 자기소개해 줘."
```

- 연결 불가: 서버와 주소를 확인하라는 오류 안내, 종료 코드 **1**, mock 답변 없음,
  `application_status=closed` 확인.
- 정상 연결: **미검증**. KoboldCpp 자체 응답과 서로 다른 질문의 실제 생성 확인 필요.
- 출력 처리: **실제 모델 기준 미검증**. 원시 응답과 `Response.final`, `Response.speech`를
  비교하고 추론 태그·역할 구분 잔류 여부를 확인해야 합니다. `/no_think`만으로 통과 판정하지 않습니다.
- 실패 후 복구: **미검증**. 정상 실행한 서버를 중지한 뒤 오류를 확인하고, 같은 설정으로
  재시작 후 다음 요청이 성공하는지 확인해야 합니다.

현재 결과는 연결 불가 오류 경로만 확인한 것이며, 실제 모델 검증 완료가 아닙니다.

실제 검증 시 서버 버전, 모델 파일명·양자화, 채팅 템플릿, OS·GPU·VRAM, 실행 옵션을
추가 기록합니다. `retries: 0`으로 README의 단일 입력 명령을 실행하고 출력과 종료 코드를
확인하세요. 자동 테스트 통과는 실제 모델의 응답 품질이나 서버 연결 성공을 뜻하지 않습니다.
모델 및 외부 프로그램, 실제 대화 원문과 비밀값은 커밋하지 않습니다.

## 참고

- [KoboldCpp 공식 API 안내](https://github.com/LostRuins/koboldcpp/wiki#with-chat-completions-how-do-i-control-how-many-tokens-the-ai-outputs)
- [HTTPX 비동기 클라이언트와 종료](https://www.python-httpx.org/async/)
- [HTTPX 단계별 타임아웃](https://www.python-httpx.org/advanced/timeouts/)
- [HTTPX MockTransport](https://www.python-httpx.org/advanced/transports/#mock-transports)
