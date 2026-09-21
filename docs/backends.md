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
| 실제 서버 | `http://127.0.0.1:5001` TCP 연결, API 직접 생성 HTTP 200, 프로젝트 CLI 실제 답변 및 종료 코드 0 확인 |
| KoboldCpp 버전 | 1.121, 공식 Windows CUDA 실행 파일 |
| 실제 모델 파일·양자화·템플릿 | `Qwen3-8B-Q4_K_M.gguf`, Q4_K_M, 모델 내장 Jinja 채팅 템플릿 (`--jinja --jinjathink false`) |
| CPU / RAM | Intel Core i5-13420H / 31.71 GiB |
| GPU / VRAM / 드라이버 | NVIDIA GeForce RTX 4050 Laptop GPU / 6141 MiB / 610.78 |
| 모델 다운로드 출처 | 공식 `Qwen/Qwen3-8B-GGUF`, revision `7c41481f57cb95916b40956ab2f0b139b296d974` |
| 컨텍스트 / GPU 오프로딩 | 서버·클라이언트 4096 / CUDA GPU 0, 24/37 레이어 오프로딩 확인 |

### 실제 연결 검증 진행 상태

로컬 `configs/app.yaml`의 기존 항목을 보존하고 `context_tokens: 4096`으로 설정했습니다.
`backend: koboldcpp`, `retries: 0`, 단계별 제한 30초, 전체 제한 60초,
`max_output_tokens: 256`을 유지했습니다. `/v1/models`에서 확인한 식별자
`model: koboldcpp/Qwen3-8B-Q4_K_M`으로 맞췄습니다. 이 파일은 Git 제외 대상입니다.

실행한 명령(`uv`가 PATH에 없어 `python -m uv` 사용):

```powershell
python -m uv run --locked python -m virtual_ai --config configs/app.yaml --backend koboldcpp --once "한 문장으로 자기소개해 줘."
$LASTEXITCODE
```

- 서버 설치 전 연결 불가: 서버와 주소를 확인하라는 오류 안내, 종료 코드 **1**, mock 답변 없음,
  `application_status=closed` 확인.
- 정상 연결: **확인**. `Test-NetConnection` 성공, 모델 상태 `loaded`, 버전 API 1.121.
  API 직접 호출의 짧은 한글 인사 생성은 HTTP 200, 프로젝트 CLI의 자기소개 생성은
  한글 답변과 종료 코드 **0**, `application_status=closed`를 확인했습니다.
- 출력 처리: 직접 API 응답 1건에서 원시 응답과 `Response.final`, `Response.speech`가
  같은 한글 인사였고 `blocked=False`, 추론 태그 잔류 없음 확인.
  이는 해당 샘플 검증이며 모든 응답의 품질·태그 제거를 보장하지 않습니다.
- 실패 후 복구: **확인**. 실제 서버를 중지해 연결 오류를 발생시킨 뒤 같은 옵션으로
  서버를 재시작했습니다. CLI 프로세스를 유지한 채 다음 질문의 정상 응답을 확인했습니다.

이번 작업의 완료 기준인 **실제 모델 답변 출력과 CLI 종료 코드 0**을 충족했습니다.
연속 대화·기록 삭제·출력 취소·서버 재시작 복구 결과와 제한은 아래에 기록합니다.

### 실제 대화형 검증 (2026-09-21)

위 서버·모델·하드웨어·컨텍스트·오프로딩 설정과 `retries: 0`을 유지했습니다.
`python -m virtual_ai --config configs/app.yaml --backend koboldcpp`를 실행하고,
UTF-8 표준 입력으로 합성 검증 질문과 운영자 명령을 순차 전달했습니다.
성공한 전체 시나리오는 하나의 CLI 프로세스에서 실행했으며 `/quit` 종료 코드는 0입니다.

실제 HTTP 전송을 그대로 수행하는 관측 후크로 요청의 메시지 개수와 삭제 대상의
포함 여부만 별도 확인했습니다. 모델 답변을 대체하는 mock은 사용하지 않았습니다.
아래 시간은 `llm_seconds`이며 프롬프트 구성·HTTP 대기·생성을 포함합니다.
입력 큐 대기 시간이나 순수 GPU 생성 시간과는 다릅니다. 소수의 로컬 관측값입니다.

| 검증 | 실제 관측 | 응답 시간 |
| --- | --- | --- |
| 질문별 응답 | 색상·수도 질문에 서로 다른 적절한 답변 | 1.510초 / 0.452초 |
| 최근 기록 | 합성 별명을 알려준 뒤 같은 프로세스에서 회상 성공; 회상 요청 8개 메시지 | 저장 2.160초 / 회상 0.808초 |
| `/forget` | 다음 요청은 시스템·현재 질문 2개 메시지만 포함, 삭제 대상 문자열 없음 | 2.306초 |
| `/stop` | 생성 중 취소 후 해당 답변 출력 없음, 새 질문 응답 성공 | 새 질문 3.091초 |
| 서버 중지 | 실제 서버 프로세스 종료 후 연결 불가 안내, CLI 유지 | 오류 2.059초 |
| 서버 복구 | 동일 옵션으로 서버 재시작 후 같은 CLI 프로세스에서 응답 성공 | 1.025초 |

제한 및 실패 관측도 함께 보존합니다.

- `/stop` 직후 서버 `/api/extra/perf`가 `idle: 0`을 반환했습니다.
  Python 요청 취소와 답변 출력 차단은 확인했지만 서버 추론의 즉시 중단은 확인되지
  않았습니다. 별도 장시간 응답 시도에서는 서버가 256토큰 생성을 계속했고,
  직후 새 요청이 **30.019초에 시간 초과**했습니다. 따라서 중지 직후 새 질문의
  즉시 성공을 보장하지 않습니다. 위 표의 재실행 성공으로 이 실패를 대체하지 않습니다.
- 기록 삭제는 전송 메시지 제거로 확인했습니다. 첫 시도에서는 삭제 후 모델이
  다른 별명을 지어냈고, 재실행에서는 모른다고 답했습니다. 삭제 기능과 모델의
  사실 정확성은 별도이며 간단한 산술 질문에서도 오답 1건을 관측했습니다.
- 모델 원문·실제 사용자 대화·비밀값은 이 문서에 기록하지 않았습니다.
  검증용 후크·요청 메타데이터·서버 로그는 Git 제외 `logs/`에만 보관합니다.

### 설치 파일과 재실행

- 실행 파일: `backends/koboldcpp.exe` (633,707,419 bytes),
  [공식 v1.121 릴리스](https://github.com/LostRuins/koboldcpp/releases/tag/v1.121).
- 모델: `models/Qwen3-8B-Q4_K_M.gguf` (5,027,783,488 bytes),
  [공식 Qwen GGUF 저장소](https://huggingface.co/Qwen/Qwen3-8B-GGUF/tree/7c41481f57cb95916b40956ab2f0b139b296d974).
- 배포처 SHA-256과 다운로드 파일 해시 일치 확인:
  실행 파일 `90b0d74ec01e5ef72efb6d45e6f10bee649458920ec951f48d58794c366b1639`,
  모델 `d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785`.

저장소 루트에서 다음 명령으로 같은 설정을 재현할 수 있습니다.
이미 서버가 실행 중이면 중복 실행하지 않습니다.

```powershell
.\backends\koboldcpp.exe --model models/Qwen3-8B-Q4_K_M.gguf --host 127.0.0.1 --port 5001 --contextsize 4096 --usecuda 0 --gpulayers 24 --jinja --jinjathink false --skiplauncher --quiet
```

검증에서는 위 옵션으로 서버를 숨김 창에서 실행했습니다. TCP 리스너가
`127.0.0.1:5001`에만 바인딩된 것을 확인했습니다. TTS·방송 연동은 실행하지 않았습니다.
첫 초기화는 수 분 걸렸으므로 포트와 모델 `loaded` 상태를 확인한 뒤 요청합니다.
`--jinjathink false`는 서버 템플릿 설정이며 클라이언트 API에 임의 필드를 추가하지 않았습니다.
`--quiet`로 생성 입력·출력의 서버 로그 기록을 억제했습니다.
PowerShell에서 Python 스크립트에 한글을 파이프로 보낼 때는 `$OutputEncoding`도 UTF-8로
설정해야 합니다. 위 CLI 명령의 인자 전달은 별도 파이프를 사용하지 않습니다.

실행 파일·모델·서버 로그(`logs/koboldcpp-server*.log`)는 Git 제외 상태를 확인했습니다.

실제 검증 시 서버 버전, 모델 파일명·양자화, 채팅 템플릿, OS·GPU·VRAM, 실행 옵션을
추가 기록합니다. `retries: 0`으로 README의 단일 입력 명령을 실행하고 출력과 종료 코드를
확인하세요. 자동 테스트 통과는 실제 모델의 응답 품질이나 서버 연결 성공을 뜻하지 않습니다.
모델 및 외부 프로그램, 실제 대화 원문과 비밀값은 커밋하지 않습니다.

## 참고

- [KoboldCpp 공식 API 안내](https://github.com/LostRuins/koboldcpp/wiki#with-chat-completions-how-do-i-control-how-many-tokens-the-ai-outputs)
- [HTTPX 비동기 클라이언트와 종료](https://www.python-httpx.org/async/)
- [HTTPX 단계별 타임아웃](https://www.python-httpx.org/advanced/timeouts/)
- [HTTPX MockTransport](https://www.python-httpx.org/advanced/transports/#mock-transports)
