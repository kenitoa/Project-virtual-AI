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

## GPT-SoVITS WAV 클라이언트

`feat/tts-client`의 범위는 고정 한국어 문장 한 건의 WAV 저장입니다.
[공식 api_v2.py](https://github.com/RVC-Boss/GPT-SoVITS/blob/main/api_v2.py)의
`POST /tts`를 사용하며 `media_type: wav`, `streaming_mode: false`를 전송합니다.
언어, 참조 음성 경로, 참조 음성 전사문은 운영자가 설정합니다.

### 운영자 준비

1. GPT-SoVITS를 별도 디렉터리·Python 환경에서 설치합니다.
   한국어를 지원하는 서버/모델 버전과 사용할 음성 모델을 지정합니다.
   [공식 서버 설정 예시](https://github.com/RVC-Boss/GPT-SoVITS/blob/main/GPT_SoVITS/configs/tts_infer.yaml)의
   `custom.version`, `t2s_weights_path`, `vits_weights_path`, `device` 등을 실제 설치에 맞춥니다.
   클라이언트가 모델 교체 API를 자동 호출하지 않습니다.
2. 사용 권한이 있는 참조 음성과 해당 음성의 실제 전사문을 준비합니다.
   `ref_audio_path`는 **GPT-SoVITS 서버가 읽을 수 있는 경로**이며 업로드 기능은 없습니다.
   상대 경로는 서버 작업 디렉터리 기준입니다. 클라이언트 설정 폴더 기준으로 변환하지 않습니다.
3. GPT-SoVITS 디렉터리의 해당 Python 환경에서 서버를 실행합니다.

```powershell
python api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml
```

4. 기존 로컬 `configs/app.yaml`을 덮어쓰지 않고 다음 절을 추가·수정합니다.
   아래 참조 경로는 예시이므로 운영자가 실제 파일을 지정해야 합니다.

```yaml
tts:
  enabled: true
  base_url: http://127.0.0.1:9880
  ref_audio_path: "C:/authorized-voice/reference.wav"
  prompt_text: "참조 음성에서 실제로 말한 내용을 여기에 입력합니다."
  prompt_lang: ko
  text_lang: ko
  timeout_seconds: 60
  total_timeout_seconds: 120
  max_text_chars: 1000
  max_response_bytes: 20971520
```

5. 이 저장소에서 실행합니다. 기본 고정 문장을 합성하며 LLM 요청은 발생하지 않습니다.

```powershell
python -m uv run --locked python -m virtual_ai.tts --config configs/app.yaml --output generated_audio/tts-test.wav
$LASTEXITCODE
```

종료 코드 0과 WAV 존재를 확인합니다. 클라이언트는 WAV Content-Type, RIFF 길이·청크,
PCM 형식, 채널 수(1 또는 2), 양수 샘플레이트·프레임 수 및 실제 프레임 길이를 검사합니다.
오류 JSON, 스트리밍용 미완성 헤더, 잘린 파일, 비어 있는 오디오 데이터는 저장하지 않습니다.
음질·발음·무음 여부는 이 구조 검사로 판정할 수 없습니다.

빈 문자열·길이 초과는 전송 전에 거절합니다. HTTP 단계별 및 전체 시간 제한,
응답 크기 제한을 적용하고 자동 재시도는 하지 않습니다. 오류 응답 원문이나 전사문을
로그에 출력하지 않습니다. `tts_seconds`는 합성 요청부터 검증·저장·연결 종료까지의
시간이며 실제 음성 재생 시작 시간이 아닙니다.
실패·취소된 요청은 기존 출력 파일을 보존하고, 성공한 WAV만 같은 디렉터리의 임시
파일을 통해 교체합니다. 취소를 무시한 늦은 응답도 저장하지 않습니다.
GPU 추론 중단과 이후 재생 큐 취소는 별도 검증 대상이며 이번 범위에 재생기는 없습니다.

### 현재 검증 상태

- 로컬 Windows / Python 3.11.16: TTS 36개를 포함한 전체 115개 테스트,
  Ruff 검사·포맷 검사 및 기존 텍스트 CLI 실행 검사 통과.
- 서버 없는 자동 검사: 요청 필드·정상 저장·오류 JSON·손상 WAV·시간 제한·크기 제한·
  취소·늦은 응답·클라이언트 정리 검사 통과.
- 테스트 HTTP 서버가 만든 PCM WAV를 실제 CLI로 받아 저장하는 검사 통과.
  이는 HTTP/파일 경로 검사이며 GPT-SoVITS 음성 모델 합성 성공을 뜻하지 않습니다.
- 실제 GPT-SoVITS: 기본 포트 9880에 리스너 없음. 사용할 서버 버전·음성 모델·
  권한 있는 참조 음성이 지정되지 않아 **실제 고정 문장 합성과 음질은 미검증**입니다.
- 실제 검증 시 서버 커밋/버전, GPT·SoVITS 모델 파일과 버전, 참조 음성 사용 권한 확인,
  설정 언어, 장치, 응답 시간, WAV 채널·샘플레이트·프레임 수, 종료 코드를 추가 기록합니다.
  참조 음성 원본·전사문·생성 오디오·개인 경로는 커밋하지 않습니다.

## 참고

- [KoboldCpp 공식 API 안내](https://github.com/LostRuins/koboldcpp/wiki#with-chat-completions-how-do-i-control-how-many-tokens-the-ai-outputs)
- [HTTPX 비동기 클라이언트와 종료](https://www.python-httpx.org/async/)
- [HTTPX 단계별 타임아웃](https://www.python-httpx.org/advanced/timeouts/)
- [HTTPX MockTransport](https://www.python-httpx.org/advanced/transports/#mock-transports)
