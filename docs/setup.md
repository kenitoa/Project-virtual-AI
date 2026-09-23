# 설치 및 검사

시작 전 비발화 진단은 `python -m uv run --locked python -m virtual_ai.diagnostics --config configs/app.yaml`로
실행한다. 라이브·YouTube 활성 실행에는 자동 적용되며 일반 실행에는 `--check-startup`을 추가한다.
필수/선택 기능 오류 처리와 출력 시험의 구분은 [문제 해결](troubleshooting.md)을 따른다.

## 고정 구성 재현 (2026-09-23)

1. [릴리스 계획](release-plan.md)의 기준 SHA를 별도 작업 공간에 체크아웃한다. readiness 문서·예시 변경은 현재 미커밋이므로 두 개발자에게 전달할 때 최종 커밋 SHA를 추가로 기록한다. 기준 SHA만 체크아웃하면 예시의 8192가 남아 있으므로 아래 4096 설정을 반드시 확인한다.
2. 앱 Python 3.14.7과 uv 0.12.16을 준비하고 루트에서 `python -m uv sync --locked --python 3.14.7`을 실행한다. [검증 현황](validation-status.md)의 pytest·Ruff·mock 명령을 실행한다.
3. [백엔드](backends.md)의 KoboldCpp 버전·GGUF revision·해시를 맞춘다. 엔진 루트에서 문서의 실행 옵션(4096, GPU 0, 24 레이어, Jinja)을 사용한다. 기존 서버가 있으면 중복 실행하지 않는다.
4. GPT-SoVITS는 별도 디렉터리·Python 3.10.21 환경에서 해당 커밋과 모델 revision으로 준비한다. 기존 검증 의존성 목록과 모델 해시를 비교하고 `python -m uv pip check --python <엔진Python경로>`를 실행한다. 엔진 루트에서 `api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml`을 실행한다. CUDA·half·v2 설정과 두 가중치를 확인한다. 기존 로컬 시작 스크립트는 Git 제외 파일이므로 다른 PC에 있다고 가정하지 않는다.
5. `configs/app.yaml`이 **없을 때만** 예시를 복사한다. 기존 파일은 덮어쓰지 않고 필요한 필드만 수정한다. `llm.context_tokens: 4096`과 서버 컨텍스트가 일치하는지 확인한다. 참조 경로는 TTS 서버가 읽는 경로이며 정확한 전사문은 Git 제외 로컬 설정에 둔다. 최종 권한 자산이 준비될 때까지 방송용으로 활성화하지 않는다.
6. 출력 장치는 `python -m uv run --locked python -m virtual_ai.audio --list-devices`로 각 PC에서 확인한다. [자산 목록](assets.md)의 모델·표정·입 입력 매핑과 OBS 단일 오디오 경로를 준비한다. VTS 토큰은 각 PC에서 인증하여 발급한다.
7. 고정 문장 합성→직접 청취→LLM 답변 음성→VTS 화면→OBS 로컬 녹화 순으로 검사한다. 두 개발자가 동일 자산 해시·옵션과 각자의 장치 매핑·결과를 검증 현황에 기록해야 재현 완료다. 실제 서버 성공과 직접 청취를 분리한다.

외부 엔진 의존성 전체 목록은 엔진 환경에서 `python -m uv pip freeze --python <엔진Python경로>`로 별도 보관한다. 모델·보조 모델 전체 파일 해시와 사용 허락도 함께 전달해야 한다. 현재 문서의 주요 패키지 버전만으로 외부 엔진 전체 환경 고정이 완료되었다고 보지 않는다. 방송용 자산과 개발자 B의 재현은 아직 대기다.

설치·실행·공통 검사 명령은 [README](../README.md)를 따릅니다.

uv sync --locked는 개발 의존성 pytest와 Ruff도 설치합니다.
CI는 Ubuntu와 Windows의 Python 3.11/3.12/3.13/3.14에서 동일한 검사를 실행하도록 설정되어 있습니다.
실제 원격 실행 성공 여부는 PR 검사에서 별도로 확인해야 합니다.
Windows의 설치·진단·실행 스크립트는 [운영 안내](operator-guide.md)를 따릅니다.
업데이트 전에 [복구 절차](recovery.md)의 설정 보존과 SQLite 백업을 먼저 수행합니다.
로컬 설정·런타임 데이터는 [보관 정책](privacy-and-retention.md)에 따라 Git 예시와 분리합니다.
CLI의 표준 출력·오류는 UTF-8과 `backslashreplace` 오류 처리를 사용합니다.
Windows의 기본 코드 페이지에 한글이 없어도 출력할 수 있습니다.
캐릭터 경로는 설정 파일 폴더 기준이며 .env 자동 로딩은 제공하지 않습니다.

## Windows 실행 검사 수정 (2026-09-21)

- 기존 테스트의 `-X utf8` 옵션이 CI 실행 검사와 다른 조건을 만들고 있었습니다.
  `PYTHONUTF8=0`, `PYTHONIOENCODING=cp1252`에서 mock 한글 출력 실패를 재현했습니다.
- CLI 진입점에서 `configure_console_output()`을 호출합니다.
  기존 UTF-8 테스트 도우미를 유지하며 회귀 검사에서는 `-X utf8` 없이 실행합니다.
  기존 코드 페이지 조건의 응답·명령·잘못된 설정의 한글 오류 출력을 검사합니다.
- 로컬 Windows / Python 3.14.7: `python -m uv sync --locked`, 전체 테스트 79개,
  Ruff 검사·포맷 검사, 기본 mock 실행 검사 통과.
- 로컬 Windows / Python 3.11.16: CLI 테스트 15개 및 `PYTHONUTF8=0`,
  `PYTHONIOENCODING=cp1252` 조건의 기본 mock 실행 검사 통과.
- 추가 로컬 검증: Windows / Python 3.11.16 전체 테스트 79개, Ruff와 기본 실행 검사 통과.
- 초기 원격 조회는 404로 실패했으나 이후 접근되어 실행 `35598475972`의 Windows
  `Entry point smoke check` 인코딩 실패 로그를 확인했습니다.
  수정본의 Windows·Ubuntu 원격 CI 결과는 통합 PR의 검사를 기준으로 확인합니다.
