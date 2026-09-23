# 백엔드

## 검증 기간 고정 구성 (2026-09-23)

자산 권한·선택 상태는 [자산 목록](assets.md), 재현 순서는 [설치](setup.md)를 따른다. 아래 엔진 구성은 기존 로컬 검증용이며 방송용 음성 준비 완료를 뜻하지 않는다. 이번에는 서버를 시작하거나 추론·합성·재생하지 않았다.

| 항목 | 고정값 / 이번 확인 |
| --- | --- |
| 앱 기준 | `04564f1134dd330d77a164904190087ab59d1c23` + readiness 문서·예시 설정 변경; 앱 소스 불변 |
| OS·앱 환경 | Windows 11 build 26200, Python 3.14.7, uv 0.12.16; 앱 의존성은 `uv.lock` |
| GPU | 이번 조회: RTX 4050 Laptop, 6141 MiB, 드라이버 610.78 |
| KoboldCpp | 1.121, 아래 설치 파일 SHA-256 유지; 실제 실행 서버 버전은 재실행 시 확인 |
| GGUF | Qwen3-8B-Q4_K_M.gguf, 아래 revision·SHA-256 유지 |
| 컨텍스트·오프로딩 | 서버 `--contextsize 4096 --usecuda 0 --gpulayers 24`; 클라이언트 `llm.context_tokens: 4096` |
| 채팅 템플릿 | 모델 내장 Jinja, `--jinja --jinjathink false` |
| GPT-SoVITS | 로컬 Git HEAD `48b1a0169a28582a8984402f82cf438d3bfa6aca` 재확인 |
| TTS 설정 | `GPT_SoVITS/configs/tts_infer.yaml`의 custom: v2, cuda, is_half=true |
| GPT 가중치 | `GPT_SoVITS/pretrained_models/gsv-v2final-pretrained/s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt` |
| SoVITS 가중치 | `GPT_SoVITS/pretrained_models/gsv-v2final-pretrained/s2G2333k.pth` |
| TTS 환경 | 별도 Python 3.10.21, torch/torchaudio 2.5.1+cu124, transformers 4.51.3, setuptools 80.10.2를 이번 설치 메타데이터에서 확인 |

기존 원본 `configs/app.yaml`도 컨텍스트 4096임을 읽기 전용으로 확인했다. readiness의 예시를 8192에서 4096으로 수정했으며 원본 설정은 변경하지 않았다. 서버 실행 옵션을 바꾸면 클라이언트 예산도 함께 바꾸고 재검증한다. 클라이언트 설정이 서버 컨텍스트를 변경해 주지는 않는다.

이번 KoboldCpp 실행 파일·GGUF 해시는 아래 기존 SHA-256과 일치했다. TTS `uv pip check`는 112개 패키지 호환성 검사를 통과했다. 전체 의존성 스냅샷은 readiness의 `.local/readiness/tts-requirements.txt`, GPT·SoVITS 및 보조 모델 8개 파일의 SHA-256은 `.local/readiness/tts-model-sha256.json`에 보관했다. 두 파일은 Git 제외이며 다른 개발자에게 별도로 전달해야 한다. 이는 기존 로컬 설치 식별 증거이고 새 환경 설치·방송 권한 확인은 아니다.

외부 엔진·모델은 원본 폴더의 Git 제외 `backends/`, `models/`에 있고 새 worktree에 자동 복사되지 않는다. 아래 상대 경로 실행 명령은 **자산이 설치된 원본 루트** 기준이다. 새 개발자는 허가된 동일 자산을 자신의 엔진 루트에 준비한다. TTS 의존성은 앱 `uv.lock`에 섞지 않는다.

`mock`/`fake`는 네트워크 없이 고정 응답을 제공합니다. `koboldcpp`는 재사용하는
`httpx.AsyncClient`로 POST `/v1/chat/completions`를 호출합니다.
기존 LLMClient 계약의 `generate(messages)`, `aclose()`를 구현하며 종료 시 연결을 닫습니다.
요청은 `model`, `messages`, `max_tokens`, `stream: false`로 구성합니다.
응답 크기는 256 KiB로 제한하고 `choices[0].message.content`의 비어 있지 않은 문자열만 반환합니다.
출력 검사는 `app.py`의 `prepare_response()`에서 수행합니다.

## 요청별 서버 중단 (2026-09-21)

`fix/koboldcpp-server-abort`는 `feat/voice-pipeline`을 기준으로 합니다.
음성 통합 PR은 `main` 대상 Draft로, 이번 수정 PR은 `feat/voice-pipeline` 대상으로
분리합니다. 음성 직접 청취는 여전히 미확인입니다. 통합 PR 병합 후에는 수정 브랜치를
`main`과 동기화하고 PR 대상을 변경한 뒤 변경 목록을 다시 확인해야 합니다.

### 지원 계약과 설정

실행 서버의 `/api/extra/version`에서 **1.121**을 확인하고
[공식 v1.121 소스](https://github.com/LostRuins/koboldcpp/blob/v1.121/koboldcpp.py)의
chat-completions 변환(`transform_genparams`), `generate`의 `currentusergenkey`,
`/api/extra/abort` 처리 경로를 확인했습니다. 이 버전은 JSON 본문의 `genkey`를
chat-completions 변환 이후에도 유지합니다. HTTP 헤더로 보내는 계약이 아닙니다.

실제 생성 중 다른 임의 키로 중단을 요청하면 `success: "false", done: "false"`이고
서버는 계속 생성(`idle: 0`)했습니다. 원래 키로 중단하면 `"true"/"true"`,
원래 생성 HTTP 200, `idle: 1, queue: 0`을 확인했습니다(정리 관측 0.116초).
공식 개발 브랜치 동작을 설치 버전의 검증으로 대신하지 않았습니다.

```yaml
llm:
  server_abort_enabled: false
  abort_timeout_seconds: 3
```

- 기본값은 비활성화입니다. 사용 서버의 요청별 키 전달·중단 계약을 검증한 뒤
  `server_abort_enabled: true`로 설정합니다. 확인한 환경은 1.121의 단일 생성 슬롯,
  직접 연결된 로컬 서버입니다. 병렬 생성 모드·라우터·프록시·다른 버전은 미검증이며
  검증 전 활성화하지 않습니다. 실행 시 버전이나 병렬 모드를 자동 탐지하지 않습니다.
- 활성화 시 매 HTTP 생성 시도에 새로운 UUID `genkey`를 전송하며 해당 키만 중단합니다.
  키가 없는 전역 중단으로 대체하지 않습니다. 다른 클라이언트가 있는 서버에서도
  전역 중단을 사용하지 않으며, 다른 작업 때문에 유휴 상태를 못 확인하면 보수적으로 차단합니다.
- 응답 필드 각각은 JSON `true` 또는 정확한 문자열 `"true"`만 허용합니다.
  `"false"`, 숫자 1, 누락 필드, HTTP 200만으로 성공 판정하지 않습니다.
  1.121의 `done: "false"`는 대기 요청에 대한 지연 중단일 수 있으므로 완료로 취급하지 않습니다.
- `done: "true"`도 GPU 정리 완료 보장은 아닙니다. 별도로 `/api/extra/perf`의
  정수 `idle: 1, queue: 0`을 관측할 때만 다음 요청을 허용합니다.
  이 관측은 서버 전체 상태이며, 다른 클라이언트가 이후 새 작업을 넣지 않는다는 보장은 아닙니다.
- 중단 응답과 유휴 확인 전체에 별도 제한 시간(기본 3초, 양수·최대 30초)을 적용합니다.
  일반 생성 제한 시간 이후 정리 시간이 추가될 수 있습니다. 3초는 성능 보장이 아닙니다.
- `/stop`은 기존 응답 무효화·입력 큐 제거·오디오 중지를 먼저 수행합니다.
  취소된 `generate()`가 별도 HTTP 연결로 서버를 정리하는 동안 생성 잠금은 유지합니다.
  반복 취소도 같은 정리 작업을 기다리고 `CancelledError`를 다시 전달합니다.
  `/quit`과 `aclose()`는 정리 완료 또는 정리 제한 시간까지 기다린 뒤 연결을 닫습니다.
- 읽기/쓰기 시간 초과·전송 상태 불명·502/504에서는 재시도 전에 정리를 확인합니다.
  연결/연결 풀 시간 초과처럼 생성이 전송되지 않은 경우에는 기존 제한된 재시도를 유지합니다.
- 중단 실패·정리 미확인·중단 비활성화 상태의 생성 취소/전송 상태 불명에서는
  해당 클라이언트의 이후 생성을 차단합니다. 서버가 실제 유휴 상태인지 운영자가 확인한 후
  앱을 다시 시작해야 합니다. 앱 재시작 자체가 이전 서버 작업을 정리하지는 않습니다.
  서버 강제 종료나 자동 잠금 해제는 하지 않습니다.
- 취소한 답변의 화면·음성·기록 차단 및 늦은 WAV 삭제는 기존 방어를 유지합니다.
  mock/fake LLM에서는 중단 설정을 켜도 KoboldCpp 클라이언트나 중단 네트워크 요청이 없습니다.

### 실제 중단 후 재개 검증

위에 기록된 동일 서버·모델·GPU 설정을 유지했습니다. 출력 한도 1024토큰의 긴 합성 질문을
시작한 후 해당 키의 `/api/extra/generate/check`에 생성 텍스트가 나타나고 `idle: 0`인
것을 확인했습니다. 실제 `Application.stop()`(콘솔 `/stop`의 실행 경로)을 호출하고
이전 답변의 출력·기록 차단, 서버 정리, 같은 앱/클라이언트의 짧은 새 질문 성공을 확인했습니다.
HTTP 응답은 대체하지 않았고, TTS·청취 검증과 분리해 음성을 비활성화했습니다.

| 반복 | 중단 응답 success / done | 정리 확인 | 이전 답변 출력·기록 | 다음 질문 | 정리 시간 | 다음 응답 시간 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `"true" / "true"` | `idle: 1, queue: 0` | 차단 | 성공 | 0.378초 | 0.841초 |
| 2 | `"true" / "true"` | `idle: 1, queue: 0` | 차단 | 성공 | 0.310초 | 0.896초 |
| 3 | `"true" / "true"` | `idle: 1, queue: 0` | 차단 | 성공 | 0.305초 | 1.026초 |

`stop()` 호출 반환은 세 번 모두 0.0001초 미만으로, 서버 정리 응답을 기다리지 않았습니다.
정리 시간은 중지 요청부터 생성 작업 정리 완료까지, 다음 응답 시간은 다음 입력의
프롬프트 처리·HTTP·생성을 포함합니다. 소수의 로컬 관측이며 지연 상한 보장이 아닙니다.
이전 **30.019초 시간 초과** 기록은 아래에 그대로 보존했습니다.
이 실제 검증은 생성 진행 중 중단/재개에 한정하며, API 장애·반복 취소·종료·시간 초과
재시도 경합은 제어 가능한 가짜 HTTP 서버 응답을 사용한 자동 테스트로 구분합니다.
원문·생성 음성·개인 설정·관측용 스크립트는 커밋하지 않습니다.

자동 검증: Windows / Python 3.14.7에서 `python -m uv sync --locked`,
대상 테스트 73개, 전체 pytest 204개, Ruff lint·format 검사 통과.
기존 Windows/Ubuntu CI 정의와 잠금 파일은 변경하지 않았습니다.

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
python -m uv sync --locked
python -m uv run --locked python -m virtual_ai.tts --config configs/app.yaml --output generated_audio/tts-real-01.wav
$LASTEXITCODE
```

매번 새 출력 파일명을 사용하고 종료 코드 0과 이번 실행의 WAV 생성을 확인합니다.
실패하면 기존 파일을 보존하므로 파일 존재만으로 성공을 판정하지 않습니다.
클라이언트는 WAV Content-Type, RIFF 길이·청크,
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
- 2026-09-21 재검증: 프로젝트 Python 3.14.7에서 `uv sync --locked` 및
  TTS 테스트 36개 통과. 실제 서버 합성 결과는 아래와 같습니다.

### 실제 GPT-SoVITS 검증 (2026-09-21)

| 항목 | 검증 구성·결과 |
| --- | --- |
| 서버 | 공식 RVC-Boss/GPT-SoVITS, 커밋 `48b1a0169a28582a8984402f82cf438d3bfa6aca`, `api_v2.py` |
| 모델 출처 | 공식 안내의 `lj1995/GPT-SoVITS`, revision `336b2ec4e8d4ac74740798dd40af44e74659ecaf` |
| GPT 모델 | v2, `gsv-v2final-pretrained/s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt` |
| SoVITS 모델 | v2, `gsv-v2final-pretrained/s2G2333k.pth` |
| 보조 모델 | 같은 revision의 `chinese-roberta-wwm-ext-large`, `chinese-hubert-base` |
| 엔진 환경 | 프로젝트와 분리한 Python 3.10.21, PyTorch/Torchaudio 2.5.1+cu124, Transformers 4.51.3 |
| 장치 | RTX 4050 Laptop, VRAM 6141 MiB, `device: cuda`, `is_half: true`; KoboldCpp 종료 후 TTS 단독 실행 |
| 참조 음성 | KSS, Bingsu/KSS_Dataset의 default/train 행 0, 데이터 revision `48fdfd7ab1dbc1a62e4e8a8b9f4c360259d51d3c`; 44.1kHz 스테레오, 약 3.53초 |
| 사용 조건 | 배포 문서의 CC BY-NC-SA 4.0 및 비상업적 사용 조건 확인. 이번 로컬 테스트에 한정하며 방송용 사용 허락을 뜻하지 않음 |
| 클라이언트 | `prompt_lang: ko`, `text_lang: ko`, 단계별 60초 / 전체 120초; 참조 녹음에 대응하는 배포 전사문 사용 |
| 합성 문장 | 기존 CLI의 고정 한국어 테스트 문장, LLM 호출 없음 |
| 재생 | Windows `winsound.PlaySound`로 첫 성공 파일 2건의 재생 호출 정상 종료. 사람의 청취·발음·잘림·잡음 판정은 확인 대기 |

시간은 CLI의 `tts_seconds`이며 요청·WAV 검사·저장·연결 종료를 포함합니다.
서버 시작 및 가중치 로딩 시간은 제외합니다. 각 성공 요청은 새 파일명으로 저장했습니다.

| 실행 | 종료 코드 | tts_seconds | WAV 결과 |
| --- | --- | --- | --- |
| 설치 직후 최초 요청 | 1 | 54.519631 | HTTP 400, 파일 없음; 한국어 전처리의 `pkg_resources` 누락 |
| 의존성 보완 후 같은 서버 첫 성공 | 0 | 6.749671 | 32kHz, 모노, PCM 16-bit, 102400프레임 / 3.20초 |
| 후속 요청 | 0 | 3.763185 | 같은 형식, 119040프레임 / 3.72초 |
| 서버 중지 후 요청 | 1 | 2.096373 | 연결 실패 안내, 새 파일 없음 |
| 서버 재시작 후 첫 요청 | 0 | 16.272584 | 같은 형식, 125440프레임 / 3.92초 |
| 재시작 후 후속 요청 | 0 | 3.425561 | 같은 형식, 115200프레임 / 3.60초 |

첫 성공 파일 두 건의 정규화 RMS는 각각 약 0.125 / 0.134로 완전 무음은 아닙니다.
신호 크기나 재생 API 성공만으로 한국어 발음·잡음·잘림이 정상이라고 판정하지 않습니다.
**실제 WAV 저장과 서버 재시작 복구는 확인했으며, 청취 확인 전까지 PR은 Draft를 유지합니다.**
이 절은 TTS 단독 검증입니다. 후속 LLM/TTS 동시 로딩과 순차 합성·재생 관측은
[음성 파이프라인 기록](voice-pipeline.md)에 구분했습니다.

설치 시 서버 코드 수정 없이 누락된 `matplotlib`, `python-multipart`,
`setuptools==80.10.2`를 엔진 환경에 추가했습니다. Windows 콘솔은
`PYTHONUTF8=1`, `PYTHONIOENCODING=utf-8`로 실행하고 FFmpeg를 엔진 PATH에 배치했습니다.
엔진의 `uv pip check`는 통과했으며 패키지 목록·모델 SHA-256·실행 로그는 로컬에 보관합니다.
프로젝트의 `pyproject.toml`과 `uv.lock`에는 엔진 의존성을 추가하지 않았습니다.
서버 설정은 위의 `custom` v2 구성이고, 프로젝트 설정에는 참조 경로·전사문·언어만
전달합니다. 재시작에는 동일한 `api_v2.py` 실행 명령을 사용합니다.

출처: [공식 GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS/tree/48b1a0169a28582a8984402f82cf438d3bfa6aca),
[모델 revision](https://huggingface.co/lj1995/GPT-SoVITS/tree/336b2ec4e8d4ac74740798dd40af44e74659ecaf),
[KSS 원 배포처](https://www.kaggle.com/datasets/bryanpark/korean-single-speaker-speech-dataset),
[전사문·사용 조건을 포함한 KSS 배포 문서](https://huggingface.co/datasets/Bingsu/KSS_Dataset/blob/48fdfd7ab1dbc1a62e4e8a8b9f4c360259d51d3c/README.md).
KSS 출처 표기: Kyubyong Park, KSS Dataset: Korean Single speaker Speech Dataset, 2018.
참조 음성·전사문·생성 WAV·개인 경로는 커밋하지 않습니다.

## 참고

- [KoboldCpp 공식 API 안내](https://github.com/LostRuins/koboldcpp/wiki#with-chat-completions-how-do-i-control-how-many-tokens-the-ai-outputs)
- [HTTPX 비동기 클라이언트와 종료](https://www.python-httpx.org/async/)
- [HTTPX 단계별 타임아웃](https://www.python-httpx.org/advanced/timeouts/)
- [HTTPX MockTransport](https://www.python-httpx.org/advanced/transports/#mock-transports)
