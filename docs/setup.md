# 설치 및 검사

설치·실행·공통 검사 명령은 [README](../README.md)를 따릅니다.

uv sync --locked는 개발 의존성 pytest와 Ruff도 설치합니다.
CI는 Ubuntu와 Windows의 Python 3.11에서 동일한 검사를 실행하도록 설정되어 있습니다.
실제 원격 실행 성공 여부는 PR 검사에서 별도로 확인해야 합니다.
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
