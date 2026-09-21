# 설치 및 검사

설치·실행·공통 검사 명령은 [README](../README.md)를 따릅니다.

uv sync --locked는 개발 의존성 pytest와 Ruff도 설치합니다.
CI는 Ubuntu와 Windows의 Python 3.11에서 동일한 검사를 실행하도록 설정되어 있습니다.
실제 원격 실행 성공 여부는 PR 검사에서 별도로 확인해야 합니다.
캐릭터 경로는 설정 파일 폴더 기준이며 .env 자동 로딩은 제공하지 않습니다.
