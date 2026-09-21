# Project-virtual-AI

Python 기반 버추얼 AI 제어 프로그램의 초기 구성입니다.
현재 mock 모드는 외부 서버·네트워크 호출·GPU 없이 고정 답변을 반환합니다.
KoboldCpp 연결을 지원합니다. 대화 기록, TTS, 방송 채팅은 포함하지 않습니다.

## 설치와 실행

Python 3.11 이상과 uv를 설치한 뒤 저장소 루트에서 실행합니다.

```powershell
uv sync --locked
uv run --locked python -m virtual_ai --backend mock
uv run --locked python -m virtual_ai --backend mock --once "안녕"
```

콘솔의 `/stop`은 대기 응답을 취소하고 `/quit` 또는 EOF는 종료합니다.
설정 예시는 `configs/app.example.yaml`, 캐릭터 설정은 `configs/character.yaml`입니다.
`--config configs/app.yaml`로 별도 설정을 사용할 수 있으며 잘못된 설정은 종료 코드 2로 안내합니다.
`character_path`는 해당 YAML 폴더 기준입니다. `fake`는 기존 명령 호환을 위한 mock 별칭입니다.

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
`--once`를 생략한 대화형 실행은 오류를 안내한 뒤 다음 입력을 받습니다. 이전 대화는 보내지 않습니다.
현재 인증 헤더 설정은 지원하지 않습니다. 실제 환경 검증 상태는 `docs/backends.md`를 참고하세요.

```powershell
uv sync --locked
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
```

INFO 로그는 응답 ID·대기 시간·LLM 시간·종료 상태를 stderr에 기록합니다.
입력·답변·사용자 ID는 로그에 남기지 않습니다. `--log-level WARNING`으로 줄일 수 있습니다.

실제 방송 플랫폼, GPT-SoVITS·VTube Studio, 장기 기억·벡터DB, 모델 학습은 포함하지 않습니다.
설계 전체는 `skill.md`를 참고하세요. 설계에 기록된 목표가 현재 구현 완료를 뜻하지는 않습니다.
