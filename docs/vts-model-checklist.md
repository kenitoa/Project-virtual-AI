# 실제 VTS 모델 검증 기록·체크리스트

2026-09-23 / readiness / 기준 코드 `04564f1134dd330d77a164904190087ab59d1c23`.
상태: **실제 검증 대기**. 실제 모델에서 인증·표정 적용·복귀·수동 복구를 확인하기 전 완료·병합으로 판정하지 않는다.

## 이번 확인

- 실행 프로세스 조회에서 VTube Studio를 찾지 못했고 로컬 포트 8001 리스너도 없었다.
- C:의 Program Files 및 Program Files (x86) 아래 기본 Steam 설치 경로에 VTube Studio.exe가 없었다. 다른 설치 경로까지 조사한 것은 아니므로 미설치를 단정하지 않는다.
- `python -m uv run --locked pytest tests/test_vts_client.py tests/test_vts_cli.py tests/test_expression_pipeline.py -q`: **98 passed in 7.48s**.
- 독립 CLI와 표정 정책 코드를 확인했다. 기본 정책은 neutral이며 일반 대화에서 happy가 나오지 않는 것은 VTS 결함 증거가 아니다.
- 실제 VTS 인증·토큰 발급·모델 조회·표정 명령은 아직 실행하지 않았다. 설정·토큰·매핑과 소스는 변경하지 않았다.

## 운영자가 준비할 항목

VTS 실행 경로·버전, 사용 권한 있는 모델, 모델 로드, Plugin API 활성화, 최초 승인 창과 모델 화면 확인이 필요하다. `happy`·`sad`용 ToggleExpression 핫키와 표정 파일을 준비한다. 로컬 `configs/app.yaml`의 기존 LLM·음성 설정은 보존하고 [VTS 설정 예시](vts.md)의 절만 수정한다. 독립 CLI는 음성을 사용하지 않으므로 청취 검증과 분리해서 진행할 수 있다.

| 고정할 값 | 결과 |
| --- | --- |
| VTS 버전·실행 경로 | 대기 |
| 모델 출처·버전·사용 범위 | 대기 |
| expected_model_id | 실제 조회 대기 |
| happy: 핫키 ID / 표정 파일 | 조회 및 운영자 선택 대기 |
| sad: 핫키 ID / 표정 파일 | 조회 및 운영자 선택 대기 |
| 기본 표정·시험 전 활성 표정 | 화면 확인 대기 |
| 검증자·일시·커밋·화면 관측 근거 | 대기 |

토큰은 `.local` 아래 Git 제외 파일에만 저장하고 출력·문서에 복사하지 않는다. 모델 ID·핫키는 조회 결과로 채우며 이름만 보고 임의 추측하지 않는다.

## 실행 순서

```powershell
# readiness 루트, 로컬 vts.enabled=true 및 API 주소 준비 후
python -m uv run --locked python -m virtual_ai.integrations.vts --config configs/app.yaml --authenticate
python -m uv run --locked python -m virtual_ai.integrations.vts --config configs/app.yaml --list-hotkeys
```

첫 명령에서 VTS에 나타난 ProjectVirtualAI / ProjectVirtualAITeam 승인 창을 운영자가 확인한다. 다음 명령은 저장 토큰으로 새 세션 인증을 해야 하며 승인 창을 다시 요청하지 않아야 한다. 실제 modelID와 ToggleExpression 핫키 매핑을 로컬 설정에 저장한 뒤 진행한다.

```powershell
python -m uv run --locked python -m virtual_ai.integrations.vts --config configs/app.yaml --expression happy --hold-seconds 5
python -m uv run --locked python -m virtual_ai.integrations.vts --config configs/app.yaml --expression sad --hold-seconds 5
```

각 명령은 적용과 약 5초 후 같은 세션의 복귀를 화면으로 확인한다. 시험 중 다른 플러그인의 동시 표정 제어는 피한다. 의도적인 모델 변경·VTS 종료는 아래 장애 시험에서만 수행한다.

## 실제 시험표

| 시험 | 기대 동작·확인 방법 | 결과 |
| --- | --- | --- |
| 최초 승인 | 명시적 authenticate에만 승인 창, 승인 후 토큰 저장·세션 인증 성공 | 미실행 |
| 저장 토큰 재사용 | 새 list-hotkeys 프로세스에서 재승인 창 없이 모델·핫키 조회 | 미실행 |
| happy·sad | 올바른 표정 적용, 같은 세션에서 자동 복귀를 화면 확인 | 미실행 |
| 기존 활성 표정 | 시험 전에 운영자가 켠 표정은 클라이언트가 소유하지 않아 복귀 때 유지 | 미실행 |
| 인증 거부 | 별도 시험용 플러그인 식별자·토큰 경로로 요청하고 거부; 새 토큰 저장 없음·실패 코드 1 | 미실행 |
| 토큰 폐기 | VTS에서 시험용 플러그인 권한 폐기 후 기존 토큰으로 조회; 실패 코드 1, 자동 재발급 창 없음 | 미실행 |
| 모델 미로드 | 모델 언로드 후 표정 요청 거부, 표정 적용 없음; 목록 조회의 오류/빈 모델 결과도 기록 | 미실행 |
| 모델 변경 | 기대 모델과 다른 모델에서 표정 명령 거부; hold 중 변경 시 잘못된 모델 복귀를 강행하지 않음 | 미실행 |
| VTS 종료·연결 단절 | hold 중 VTS 종료, 실패·정리 불명 안내, 이전 표정 명령 자동 재전송 없음 | 미실행 |
| 수동 복구 | VTS 재실행 후 화면 상태 확인, 잔여 표정이 있으면 등록 핫키로 직접 해제; 기본 상태 확인 후 CLI 재실행 | 미실행 |

인증 거부·폐기는 기존 정상 인증을 잃지 않도록 별도 시험용 이름과 `.local/vts-rejection-test.json` 같은 토큰 경로를 사용하는 별도 로컬 설정으로 수행한다. 실제 사용자 토큰 내용을 조작하거나 기존 토큰을 임의 삭제하지 않는다. 정상 모델과 매핑으로 복원한 후 정상 표정 적용·복귀를 다시 확인한다.

`neutral`은 **현재 클라이언트 세션이 켠 표정만** 해제한다. 새 프로세스에서 neutral을 실행해도 이전 비정상 종료의 잔여 표정이 모두 해제되지 않는다. 원격 결과가 불확실할 때 명령을 자동 재전송하지 않으며, 수동 복구 화면 확인을 새 프로세스 성공으로 대체하지 않는다.

각 결과에 종료 코드·실제 화면 변화·복귀 여부·오류·수동 복구 조치를 기록한다. 자동 테스트 통과를 이 표의 실제 통과로 옮기지 않는다. 자동 감정 선택 구현은 후속 단계이며 이번 범위에 추가하지 않는다.
