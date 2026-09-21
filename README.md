# Project-virtual-AI

Python 기반 버추얼 AI 제어 프로그램의 초기 구성입니다.
현재 mock 모드는 외부 서버·네트워크 호출·GPU 없이 고정 답변을 반환합니다.
실제 KoboldCpp는 두 번째 PR, 최근 대화 기록을 포함한 연속 대화는 세 번째 PR에서 추가합니다.

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
