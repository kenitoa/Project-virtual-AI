# Project-virtual-AI

현재 구현·실제 검증·남은 수용 조건은 [프로젝트 현재 상태](docs/project-status.md)를 기준으로 확인합니다.
운영 화면·발화 모드·전달 상태·지연 측정·OBS 갱신 등 [15개 보완 반영](docs/project-improvements-2026-09-26.md).
외부 엔진 준비 후 `start.cmd`로 로컬 리허설을 실행할 수 있습니다.

v1.0의 필수 범위, 단계별 검증 순서와 후속 확장은 [v1.0 완료 기준](docs/v1-completion.md)을 따릅니다.

선택적 LLM·RAG 검색, 근거 검증, 시청자 기억 후보 승인·삭제와 운영 명령은
[LLM·RAG 구현 계획](docs/llm-rag-plan.md)을 참고하세요. 기본 비활성이며
[자동 검증과 실제 모델·장비 확인](docs/rag-validation.md)을 구분합니다.

**v1.0.0 승인 보류:** 실제 검증과 main/CI/새 환경 재현 증거가 남아 있습니다.
[최종 판정](docs/release-approval.md)과 [릴리스 노트 초안](docs/release-notes-v1.0.0.md)을 참고하세요.

Windows 설치·실행·정상/비상 종료는 [운영 안내](docs/operator-guide.md)를 따릅니다.
업데이트 전에는 [복구·DB 백업](docs/recovery.md), [설정·보관 정책](docs/privacy-and-retention.md)을 확인합니다.
변경 내역은 [CHANGELOG](CHANGELOG.md)에 기록하며 현재는 Unreleased입니다.
제한된 실제 방송은 [수용 절차](docs/live-acceptance.md)와 [결과 양식](docs/live-acceptance-record-template.md)을 사용합니다. 로컬 장치 검증과 공개 방송 수용은 구분합니다.

Python 기반 버추얼 AI 제어 프로그램의 초기 구성입니다.
현재 mock 모드는 외부 서버·네트워크 호출·GPU 없이 고정 답변을 반환합니다.
KoboldCpp 연결과 제한된 최근 대화 기록, GPT-SoVITS WAV 저장·재생 및 답변의 음성 출력을 지원합니다.
음성 합성·재생 중지와 텍스트 대화 유지가 구현되어 있으며, 기존 실제 스피커 청취는 정상 확인됐습니다.
선택적 VTube Studio 표정·립싱크, OBS 파일 자막, YouTube 채팅 수신 코드는 구현되어 있습니다.
실제 모델·OBS 녹화·VTS 연결은 제한된 로컬 검증을 통과했습니다. 실제 시청자 수신·발화와 장시간 수용은 남아 있습니다.

방송 실행에는 `--live`를 사용합니다. YouTube 수신 활성화 시에도 기본 일시 정지로 시작합니다.
`/pause`, `/resume`, `/mute`, `/unmute`, `/status`, `/panic`, `/recover`의 동작과
OBS 수동 음소거는 [운영 제어](docs/operator-controls.md)를 따릅니다.

## 설치와 실행

발화 없는 설정·서버·장치·파일 진단:

```powershell
python -m uv run --locked python -m virtual_ai.diagnostics --config configs/app.yaml
```

라이브·YouTube 활성 실행은 시작 점검을 자동 수행합니다. 일반 실행은 `--check-startup`으로 적용합니다.
원인별 상태와 지연 해석은 [문제 해결](docs/troubleshooting.md)을 참고하세요.

YouTube 운영용 Desktop OAuth 승인·OS 토큰 저장·갱신·방송 선택은 [인증 안내](docs/youtube-auth.md)를 따릅니다.
기존 수동 토큰 방식도 유지하며 실제 계정 승인·수신 검증은 별도로 진행합니다.
전송 선택과 실제 수신·재접속 시험은 [YouTube 스트리밍 검증](docs/youtube-stream.md)을 참고하세요.
사용자별 대기 상한·순환 선택·반복 제외·폐기 집계는 [채팅 선택 정책](docs/chat-selection.md)을 따릅니다.
선택적 SQLite 저장소는 [장기 기억](docs/memory.md)을 참고하세요. 기본 비활성이며 로컬 운영자가 승인한
검증 정보만 저장합니다. 기존 RAM 기록과 분리되고 YouTube 자동 저장은 하지 않습니다.
`--memory-db`로 로컬 사용자·세션의 관련 기억 조회를 켜고, `/summarize`로 유휴 시 발췌 요약,
`/forget-long`으로 장기 삭제를 실행합니다. `/forget`은 RAM만 지웁니다.
선택적 [마이크·STT](docs/stt.md)는 별도 faster-whisper 환경과 로컬 Push-to-talk를 사용합니다.
기본 비활성이며 실제 한국어 음성·마이크 검증은 준비 대기입니다.
고정 질문 반복 측정과 중앙값·p95, 자원 사용량 및 실제 통합 검증 범위는 [성능 측정](docs/performance.md)에 정리했습니다.
장애별 기대 동작, 반복 통합·과부하·지속 실행 절차와 실제 장치 대기 항목은 [수용 시험](docs/acceptance-tests.md)을 따릅니다.

Python 3.11 이상과 uv를 설치한 뒤 저장소 루트에서 실행합니다.

```powershell
uv sync --locked
uv run --locked python -m virtual_ai --backend mock
uv run --locked python -m virtual_ai --backend mock --once "안녕"
```

콘솔의 `/stop`은 대기열을 비우고 실행 중인 생성 작업을 취소합니다. 늦게 도착한 결과는
출력하지 않으며 다음 입력은 계속 처리합니다. `/quit` 또는 EOF는 worker와 클라이언트를 정리하고 종료합니다.
일반 시청자 메시지의 `/stop`은 대화 데이터로만 처리합니다.
Python HTTP 요청 취소만으로 GPU 추론 중단을 보장하지 않습니다. 지원 구성의 요청별 서버 중단은
검증했으며, 정리가 불명인 경우 다음 생성과 운영자 재개를 차단합니다. [백엔드 기록](docs/backends.md)을 참고하세요.
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
기본값은 비활성화입니다. 대화 명령의 음성 연결은 아래 두 활성화 설정이 모두 필요합니다.
저장된 WAV의 독립 재생은 아래 명령을 사용합니다. OBS 연결은 후속 범위입니다.
실제 음성 합성 검증은 서버·모델·참조 음성 준비 후 진행하며,
요청 필드와 검증 절차는 [백엔드 문서](docs/backends.md#GPT-SoVITS-WAV-클라이언트)를 따릅니다.

## 저장된 WAV 재생·중지

`configs/app.yaml`에 `audio.enabled: true`, `audio.output_device: null`을 설정합니다.
`null`은 시스템 기본 출력이며 장치 번호 또는 이름도 지정할 수 있습니다.

```powershell
python -m uv sync --locked
python -m uv run --locked python -m virtual_ai.audio --list-devices
python -m uv run --locked python -m virtual_ai.audio generated_audio/tts-real-01.wav --config configs/app.yaml --device 3
python -m uv run --locked python -m virtual_ai.audio generated_audio/tts-real-01.wav --config configs/app.yaml --device 3 --interactive
```

장치 번호는 자신의 목록에서 선택합니다. 기본 명령은 재생 완료 후 종료합니다.
`--interactive`는 재생 중에도 `/stop`, `/play 파일경로`, `/quit`을 받습니다.
`/stop`과 Ctrl+C는 남은 장치 버퍼를 버리고 출력을 중단한 뒤 장치를 닫습니다.
LLM·TTS 서버는 호출하지 않으며 기존 텍스트 콘솔의 `/stop`과는 별도 CLI입니다.
지원 WAV·종료 코드·실제 스피커 검증 절차는 [오디오 문서](docs/audio.md)를 참고하세요.

## LLM 답변의 음성 출력

준비된 로컬 설정에서 `tts.enabled: true`와 `audio.enabled: true`를 모두 지정하면
기존 대화 명령이 텍스트 표시 후 `Response.speech`를 합성하고 재생합니다.

```powershell
python -m uv run --locked python -m virtual_ai --config configs/app.yaml
```

둘 중 하나가 비활성화면 텍스트 대화만 수행합니다. LLM 생성부터 재생 종료까지 한 응답씩
처리합니다. `/stop`은 생성·합성을 취소하고 현재 장치 재생 및 대기 입력을 중지합니다.
이미 표시한 텍스트와 기록은 유지하며, 합성·장치 오류가 나도 다음 입력을 받을 수 있습니다.
`/quit`은 진행 중 작업과 클라이언트를 정리합니다. 프로그램이 만든 응답 ID로 저장한
임시 WAV는 완료·실패·중지 시 삭제합니다. 독립 TTS CLI가 만든 WAV는 삭제하지 않습니다.
설정 조합과 검증 범위는 [음성 파이프라인 문서](docs/voice-pipeline.md)를 참고하세요.

YouTube·VTube Studio의 실제 방송 검증은 대기 중입니다. 선택적 로컬 SQLite 기억은 구현되어 있으며,
벡터DB와 모델 학습은 포함하지 않습니다.
설계 전체는 `skill.md`를 참고하세요. 설계에 기록된 목표가 현재 구현 완료를 뜻하지는 않습니다.
선택적 대화 발전 기능은 `--dialogue`로 활성화합니다. 후속 질문·정정·기억 쿨다운·근거 말투·모델 비교 사용법은 [대화 확장 문서](docs/dialogue-development.md), 실행 결과는 [검증 기록](docs/dialogue-validation.md)에 있습니다.
항목별 구현·후속·외부 검증 상태는 [적용 현황](docs/skill-coverage.md)에 정리했습니다.
단계별 구현과 현재 작업 폴더 검증 결과: [구현 점검](docs/implementation-audit.md). 별도 readiness worktree의 후속 코드를 현재 폴더에 통합했으며, 실제 방송 검증과 v1.0.0 승인은 대기 중입니다.
