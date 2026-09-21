# 설치 및 검사

설치·실행·공통 검사 명령은 [README](../README.md)를 따릅니다.

uv sync --locked는 개발 의존성 pytest와 Ruff도 설치합니다.
CI는 Python 3.11에서 동일한 검사를 실행합니다.
캐릭터 경로는 설정 파일 폴더 기준이며 .env 자동 로딩은 제공하지 않습니다.
