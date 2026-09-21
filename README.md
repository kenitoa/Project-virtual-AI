# Project-virtual-AI

Python 기반 버추얼 AI 제어 프로그램의 초기 구성입니다.
현재 mock 모드는 외부 서버·네트워크 호출·GPU 없이 고정 답변을 반환합니다.
KoboldCpp 연결과 제한된 최근 대화 기록, 별도 명령의 GPT-SoVITS WAV 저장을 지원합니다.
음성 재생과 방송 채팅은 후속 단계입니다.

## 설치와 실행

Python 3.11 이상과 uv를 설치한 뒤 저장소 루트에서 실행합니다.

```powershell
uv sync --locked
uv run --locked python -m virtual_ai --backend mock
uv run --locked python -m virtual_ai --backend mock --once "안녕"
```

콘솔의 `/stop`은 대기열을 비우고 실행 중인 생성 작업을 취소합니다. 늦게 도착한 결과는
출력하지 않으며 다음 입력은 계속 처리합니다. `/quit` 또는 EOF는 worker와 클라이언트를 정리하고 종료합니다.
일반 시청자 메시지의 `/stop`은 대화 데이터로만 처리합니다.
Python HTTP 요청 취소가 KoboldCpp의 GPU 추론 즉시 중단을 보장하지는 않습니다. 서버 측 중단은 미검증입니다.
설정 예시는 `configs/app.example.yaml`, 캐릭터 설정은 `configs/character.yaml`입니다.
`--config configs/app.yaml`로 별도 설정을 사용할 수 있으며 잘못된 설정은 종료 코드 2로 안내합니다.
`character_path`는 해당 YAML 폴더 기준입니다. `fake`는 기존 명령 호환을 위한 mock 별칭입니다.

`/forget`은 실행 중 응답을 취소하고 메모리의 최근 기록을 삭제합니다. `/stop`은 기록을 유지합니다.
기록은 플랫폼·사용자 ID별로 분리하며 턴 수·문자 수·보관 시간·사용자 수 제한을 적용합니다.
실패·취소·출력 차단된 응답은 기록하지 않으며 프로세스 종료 시 모두 사라집니다.
`history_turns: 0`으로 기록을 끌 수 있습니다.

기본 시스템 프롬프트는 `src/virtual_ai/system_prompt.md`입니다.
설정 파일 최상위의 `system_prompt_path`로 다른 파일을 지정할 수 있으며 경로는 YAML 폴더 기준입니다.
`llm.context_tokens`에서 `max_output_tokens`를 예약하고, 남은 입력 예산을 넘으면 오래된 대화 쌍부터 제외합니다.
시스템 정책과 현재 입력만으로도 넘으면 오류를 안내합니다. 토큰 수는 UTF-8 바이트 기반 추정치이며
실제 서버 토크나이저·채팅 템플릿의 정확한 계산값은 아닙니다.
비밀키·암호 등 알려진 비밀값 형식은 모델 전송 전에 차단하지만 모든 비밀값을 식별하는 보장은 없습니다.

## 실제 KoboldCpp 연결

KoboldCpp와 모델을 별도로 실행한 뒤 다음 명령으로 한 건을 요청합니다.

```powershell
uv run --locked python -m virtual_ai --backend koboldcpp --once "안녕"
```

`configs/app.example.yaml`을 `configs/app.yaml`로 복사하고 `llm.backend: koboldcpp`,
`base_url`, `model`을 서버에 맞추면 `--config configs/app.yaml`만으로 선택할 수 있습니다.
`base_url`은 `http://127.0.0.1:5001`처럼 서버 주소만 지정합니다. `/v1`이나 다른 경로는
설정 오류입니다. 클라이언트가 `/v1/chat/completions`를 붙이며 `max_output_tokens`를
`max_tokens`로 전송합니다. 스트리밍 없이 받은 문자열은 기존 출력 검사를 통과합니다.

최초 연결 확인은 예시의 `retries: 0`을 유지하세요. `timeout_seconds`는 HTTP 단계별 제한,
`total_timeout_seconds`는 재시도·대기까지 포함한 전체 호출 제한입니다.
연결 실패·시간 초과 및 HTTP 429/502/503/504만 최대 `retries`회 추가 시도합니다.
그 외 HTTP 오류와 잘못된 JSON·누락·빈 답변은 즉시 실패합니다. mock으로 대체하지 않습니다.
`--once`의 LLM 실패는 종료 코드 1, 설정·입력 오류는 2입니다.
`--once`를 생략한 대화형 실행은 오류를 안내한 뒤 다음 입력을 받으며 허용된 최근 대화를 함께 보냅니다.
현재 인증 헤더 설정은 지원하지 않습니다. 실제 환경 검증 상태는 `docs/backends.md`를 참고하세요.

```powershell
uv sync --locked
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
```

INFO 로그는 응답 ID·대기 시간·LLM 시간·종료 상태를 stderr에 기록합니다.
입력·답변·사용자 ID는 로그에 남기지 않습니다. `--log-level WARNING`으로 줄일 수 있습니다.

## GPT-SoVITS WAV 한 건 저장

운영자가 별도로 GPT-SoVITS `api_v2.py` 서버와 음성 모델을 준비합니다.
로컬 `configs/app.yaml`에 예시의 `tts` 절을 추가하고 `enabled: true`, 서버 주소,
사용 권한이 있는 참조 음성의 **서버 측 경로**와 전사문·언어를 지정합니다.

```powershell
python -m uv run --locked python -m virtual_ai.tts --config configs/app.yaml --output generated_audio/tts-test.wav
```

고정 문장 “안녕하세요. 음성 합성 테스트입니다.”를 비스트리밍 WAV로 저장합니다.
성공 시 종료 코드 0, TTS 실패 1, 설정 오류 2, 키보드 취소 130입니다.
같은 출력 경로의 파일은 정상 WAV 검증이 끝난 경우에만 교체합니다.
기본값은 비활성화이며 텍스트 대화 명령에서 TTS를 자동 호출하지 않습니다.
재생·OBS·LLM `Response.speech` 연결은 후속 범위입니다.
실제 음성 합성 검증은 서버·모델·참조 음성 준비 후 진행하며,
요청 필드와 검증 절차는 [백엔드 문서](docs/backends.md#GPT-SoVITS-WAV-클라이언트)를 따릅니다.

실제 방송 플랫폼, VTube Studio, 장기 기억·벡터DB, 모델 학습은 포함하지 않습니다.
설계 전체는 `skill.md`를 참고하세요. 설계에 기록된 목표가 현재 구현 완료를 뜻하지는 않습니다.
항목별 구현·후속·외부 검증 상태는 [적용 현황](docs/skill-coverage.md)에 정리했습니다.
