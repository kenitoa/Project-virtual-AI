# 독립 VTube Studio 클라이언트

범위는 로컬 VTube Studio 인증, 현재 모델·핫키 조회, 운영자가 등록한 표정의 적용과
복귀입니다. 이 독립 CLI는 LLM·TTS·오디오 모듈을 호출하지 않습니다.
선택적인 대화 연결은 별도 [표정 파이프라인](expression-pipeline.md)을 참고하세요.
대화 재생의 입 움직임은 별도 [PCM 립싱크](lipsync.md)로 구현합니다.
모델 교체, 임의 핫키 실행, OBS 연결은 포함하지 않습니다.

## 준비와 설정

VTube Studio에서 모델을 로드하고 설정의 **Allow Plugin API access**를 활성화합니다.
기본 포트는 8001이며 앱에서 바꿨다면 설정도 맞춥니다.
[공식 API 문서](https://github.com/DenchiSoft/VTubeStudio#api-details)의 API 1.0 계약을 사용합니다.

기존 `configs/app.yaml`을 예시 파일로 덮어쓰지 말고 다음 영역만 추가합니다.
기존 서버·음성·출력 장치 설정은 유지하세요.

```yaml
vts:
  enabled: false
  url: ws://127.0.0.1:8001
  plugin_name: ProjectVirtualAI
  plugin_developer: ProjectVirtualAITeam
  token_path: ../.local/vts-token.json
  request_timeout_seconds: 2
  authentication_timeout_seconds: 60
  expected_model_id: ""
  expression_hotkeys:
    happy: ""
    sad: ""
```

- 기본 비활성화 상태에서는 접속·인증 파일 생성이 없습니다. 사용할 때만 `enabled: true`로 바꿉니다.
- 연결은 `ws://127.0.0.1`, `ws://[::1]`, `ws://localhost`만 허용합니다.
  localhost는 127.0.0.1로 연결하며 환경 프록시와 리다이렉트는 사용하지 않습니다.
- 일반 요청·연결·종료 제한 시간은 기본 2초(최대 30초), 최초 사용자 승인 대기는
  기본 60초(최대 300초)입니다. 자동 재접속·재시도는 없습니다.
- 토큰 경로는 설정 파일 기준입니다. Git 제외를 보장하도록 `.local` 바로 아래의
  JSON 파일만 허용하며, 임시 파일을 거쳐 교체합니다. 토큰·개인 로컬 설정·모델 자산은 커밋하지 않습니다.
- `plugin_name`과 `plugin_developer`는 각각 3~32자이며 저장 토큰의 식별자와 일치해야 합니다.
- 인증·목록 조회는 모델 ID를 비워 두고 시작할 수 있습니다. **표정 변경 전에는**
  조회한 실제 ID를 `expected_model_id`에 넣고, 해당 모델의 `ToggleExpression` 핫키 ID를
  `happy` 또는 `sad`에 등록해야 합니다. 미등록 표정은 거절합니다.

## 인증 → 목록 → 표정 확인

```powershell
uv sync --locked
uv run --locked python -m virtual_ai.integrations.vts --config configs/app.yaml --authenticate
uv run --locked python -m virtual_ai.integrations.vts --config configs/app.yaml --list-hotkeys
```

`uv`가 PATH에 없다면 `python -m uv`로 같은 명령을 실행할 수 있습니다.
`--authenticate`만 최초 승인/토큰 재발급을 요청합니다. VTube Studio에 표시되는
플러그인 이름과 개발자를 확인하고 직접 승인하세요. 일반 명령은 저장 토큰으로 매 연결마다
세션 인증을 하며 승인 창을 자동으로 다시 띄우지 않습니다. 토큰이 없거나 손상·폐기되었으면
명령이 실패합니다. 원인을 확인하고 `--authenticate`를 명시적으로 다시 실행합니다.
승인이 거절되거나 세션 인증에 실패하면 새 토큰을 저장하지 않습니다.

목록 출력의 `modelID`와 `availableHotkeys`를 보고 로컬 설정을 채운 다음 실행합니다.

```powershell
uv run --locked python -m virtual_ai.integrations.vts --config configs/app.yaml --expression happy
uv run --locked python -m virtual_ai.integrations.vts --config configs/app.yaml --expression sad --hold-seconds 5
uv run --locked python -m virtual_ai.integrations.vts --config configs/app.yaml --expression neutral
```

표정 CLI는 기본 3초(최대 60초) 동안 보여준 뒤 **같은 세션에서 복귀하고 종료**합니다.
눈으로 표정 변화와 복귀를 각각 확인하세요. 이미 활성화되어 있던 표정은 소유하지 않으므로
복귀 시에도 유지합니다. `neutral`은 특정 핫키가 아니라 이 클라이언트가 켠 표정만 해제하는
동작입니다. 별도 프로세스에서 실행한 `neutral`에는 소유한 표정이 없어 아무것도 해제하지 않습니다.
다른 프로그램의 표정이나 이전 비정상 종료의 잔여 표정을 일괄 해제하는 명령이 아닙니다.

정상 종료는 0, 연결·인증·모델·표정·정리 실패는 1, 설정·인자 오류는 2입니다.
Ctrl+C는 정리를 기다린 뒤 130이며, 정리 실패가 발견되면 오류 1을 우선 보고합니다.

## 표정과 수명 관리

논리 이름은 `happy`, `sad`, `neutral`만 허용합니다. 현재 모델, 등록 핫키의 ID·종류·표정 파일,
현재 표정 목록을 확인한 뒤 [ExpressionActivationRequest](https://github.com/DenchiSoft/VTubeStudio#requesting-activation-or-deactivation-of-expressions)로
활성/비활성 상태를 지정합니다. `HotkeyTriggerRequest`나 모델 교체 요청은 보내지 않습니다.
`ToggleExpression` 핫키를 요구하는 이유는 파일 매핑 검증과 운영자의 수동 복구 경로 확보입니다.

현재 상태를 다시 조회하므로 같은 표정이 이미 켜져 있으면 중복 적용하지 않습니다.
다른 표정으로 바꿀 때와 `reset()`·정상 `aclose()` 때는 이 세션에서 켠 표정만 해제합니다.
요청마다 UUID를 만들고 응답 ID·종류·필수 필드를 검사합니다. 전체 작업을 직렬화하여
WebSocket 수신은 한 경로만 사용합니다. 응답 크기는 1 MiB로 제한합니다.

타임아웃·연결 종료·취소·잘못된 응답 뒤에는 결과가 불확실할 수 있으므로 세션을 재사용하지
않습니다. 특히 표정 요청을 자동 재전송하지 않습니다. 종료 시 연결은 닫되 상태 복귀가
확인되지 않으면 오류로 안내합니다. **이때 VTube Studio에서 해당 표정의 등록 핫키로 직접
해제하고 화면을 확인한 후 CLI를 재실행하세요.** 단순 재실행이나 새 세션의 neutral은 복구를 보장하지 않습니다.

테스트 중에는 모델을 바꾸거나 다른 플러그인·수동 조작으로 같은 표정을 동시에 제어하지 마세요.
모델 ID는 작업 중 반복 확인하지만 API에는 표정 변경과 모델 ID 검사를 원자적으로 묶는 기능이
없습니다. 두 요청 사이의 모델 교체나 같은 표정에 대한 수동 재활성화는 완전히 구분할 수 없습니다.
감지된 모델 불일치에서는 변경·복귀를 거절하고 연결을 닫습니다.

## 검증 기록 (2026-09-22)

- 기반 커밋: `13fb4b141674375464a3c2dc2a2f0dc04e5c33c2`, 브랜치 `feat/vts-client`.
- 실행 환경: Windows, Python 3.14.7, 잠금 의존성 websockets 17.1.
- 로컬 자동 검사: `uv sync --locked`, 전체 **279개 테스트**(VTS 75개 포함),
  Ruff 및 **47개 파일** 포맷 검사 통과. 기존 mock 대화 CLI도 정상 종료했습니다.
- 가짜 전송 및 실제 loopback WebSocket의 가짜 서버로 인증·토큰 재사용/거부,
  응답 검증, 모델·표정 제한, 중복 억제·복귀, 타임아웃·취소·반복 취소·연결 정리를 검사합니다.
  이는 실제 VTube Studio 서버·모델 확인을 대신하지 않습니다.
- 사용자 응답: VTube Studio와 표정 핫키가 있는 모델은 **아직 준비되지 않음**.
  실제 토큰 발급·모델 조회·표정 적용은 실행하지 않았고 로컬 `configs/app.yaml`은 유지했습니다.

| 실제 시나리오 | 화면 관측 / 실패 여부 |
| --- | --- |
| 최초 인증 승인·재접속 시 토큰 재사용 | 미실행·미확인 |
| 실제 모델 ID·핫키 대조 | 미실행·미확인 |
| happy·sad 적용과 중복 요청 시 표정 유지 | 미실행·미확인 |
| 자동 복귀·neutral·정상 종료 | 미실행·미확인 |
| Ctrl+C·연결 단절 이후 수동 복구 | 미실행·미확인 |

모델 준비 후 검증 커밋 SHA, VTube Studio 버전, 모델·핫키 설정, 시나리오별 실제 화면 변화와
복귀, 실패 여부를 이 표에 추가합니다. API 성공이나 자동 테스트만으로 완료 처리하지 않습니다.

이번 브랜치는 아직 병합되지 않은 `fix/koboldcpp-server-abort` 위에서 분리했습니다.
선행 PR #4·#5 병합 후 main과 동기화하고 VTS만의 최종 diff와 전체 검사를 다시 확인합니다.
