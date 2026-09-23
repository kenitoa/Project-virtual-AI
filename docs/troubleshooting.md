# 시작 점검과 지연 진단

## 운영 스크립트 오류

- 스크립트 실행 정책 오류: [운영 안내](operator-guide.md)의 프로세스 한정 실행 방법을 사용한다.
  시스템 정책을 영구 변경하지 않는다. 조직 정책은 우회하지 않는다.
- `.venv/Scripts/python.exe` 없음: Python·uv 설치 후 `bootstrap.ps1` 실행. 모델·음성은 별도 준비한다.
- local 설정 파일 없음: 예시를 참고해 파일이 없을 때만 준비한다. 기존 설정을 덮어쓰지 않는다.
- live 점검 실패: 진단의 구성요소·사유를 먼저 해결한다. 점검 스크립트 실패를 무시하고 시작하지 않는다.
- 종료 소유권 불일치: 다른 프로세스일 수 있으므로 이름으로 전체 종료하지 않는다. 콘솔 `/quit` 또는
  해당 세션의 실제 프로세스 정보를 확인한다.
- DB pre-migration 백업 오류: 공간·권한을 해결한다. 이전 DB는 업그레이드되지 않았으며,
  남은 부분 백업을 정상 백업으로 사용하지 않는다. [복구 절차](recovery.md)를 따른다.

## 발화 없는 점검

```powershell
python -m uv run --locked python -m virtual_ai.diagnostics --config configs/app.yaml
```

JSON의 `component`, `status`, `reason`, `seconds`로 원인을 구분한다. 상태는 **정상 / 기능 축소 / 복구 필요 / 사용 안 함**이다. 정상은 해당 제한된 검사 통과이며 실제 발화·화면·녹화 성공을 뜻하지 않는다. 복구 필요가 하나라도 있으면 진단 CLI는 종료 코드 1, 나머지는 0이다. 기능 축소도 본문에서 확인한다. 비활성 기능은 접속·장치 조회를 하지 않는다.

합성·발화·표정 변경·토큰 발급·YouTube 메시지 제출은 하지 않는다. YouTube 활성화 시 인증된 GET 한 번으로 API 사용량을 소비하며 수신 내용은 버린다. 네트워크 검사는 항목당 기본 3초 제한, 리다이렉트·환경 프록시 비사용이다. VTS 종료 정리는 기존 클라이언트 제한을 함께 적용한다. 동기 파일·장치 OS 호출은 같은 강제 중단 보장이 없다.

## 원인별 복구

| 항목 / reason | 의미와 조치 |
| --- | --- |
| config / invalid_configuration | YAML·값·형식 오류. 기존 설정을 보존하며 예시와 비교해 수정 |
| local_files / config_or_character_unreadable | 설정·캐릭터 파일 읽기 실패. 경로·권한 확인 |
| prompt_file | 시스템 프롬프트 파일·내용 확인 |
| llm / connection, timeout | 외부 서버 실행·주소·응답 지연 확인 |
| llm / server_identity, model_unavailable | 서버 계약 또는 모델 보고 상태 불일치. 엔진·모델 로드 로그 확인 |
| llm / context_mismatch | 서버 컨텍스트가 클라이언트 예산보다 작거나 잘못된 응답. 고정 구성 4096과 맞춤 |
| llm / authentication_required, authentication_or_permission | 보호 서버·인증/권한 문제. 지원 구성과 접근 권한 확인 |
| llm / abort_contract_unverified | 중단 활성화 상태에서 확인된 1.121이 아님. 계약 검증 전 사용 금지 |
| tts / tcp_only_model_unverified | 연결만 성공. 서버 정체·모델·합성 준비는 별도 고정 문장 시험 필요 |
| tts_reference / server_side_file_unverified | 원격 또는 상대 경로. TTS 서버 작업 디렉터리에서 파일 확인 |
| tts_reference / invalid_or_unavailable | 로컬에서 확인 가능한 참조 파일 읽기 실패 |
| audio | 출력 장치·기본 샘플레이트의 모노 int16 형식 확인. 실제 WAV 형식·스트림·청취는 별도 |
| audio_files / subtitles | 저장 경로·쓰기 권한·잠금 확인 |
| vts / connection, saved_authentication | VTS/API 또는 저장 토큰 문제. 필요할 때만 authenticate를 명시적으로 실행 |
| vts / model_mismatch, model_or_mapping_unavailable, mouth_mapping | 모델·표정·입 입력 조회값과 운영자 매핑 확인 |
| youtube / token_missing_or_invalid, authentication_or_permission | 환경변수·계정 권한·API 사용량 확인. 403의 세부 원인은 운영자 콘솔에서 확인 |
| youtube / chat_ended, invalid_response | 활성 채팅 ID·응답 계약 확인 |
| disk / less_than_256_mib | 생성 음성 경로 볼륨에 최소 256 MiB 확보. OBS 저장 볼륨의 녹화 용량은 별도 확인 |

`invalid_or_unavailable`은 해당 component의 일반 검사 실패다. 원문 오류를 노출하지 않으므로 모든 세부 원인을 자동 구분하지는 않는다. 인증 토큰·전사문·시청자 메시지·개인 경로·응답 본문은 결과에 담지 않는다.

KoboldCpp는 설치된 1.121 소스에서 확인한 `/api/extra/version`, `/api/v1/model`, `/api/extra/true_max_context_length`만 사용한다. `model_reported_loaded_inference_not_tested`는 서버 보고이며 실제 생성 성공은 아니다. 다른 버전 계약은 별도 확인한다. GPT-SoVITS에 미확인 `/health`를 호출하지 않으며 포트 성공을 모델 정상으로 표시하지 않는다.

자막 검사는 같은 폴더의 임시 파일 생성·쓰기·삭제와 기존 대상의 비파괴 열기 검사다. 기존 내용을 덮어쓰지 않으며 원자적 교체·OBS 읽기 성공까지 보장하지 않는다. 음성 디렉터리가 없으면 생성한다. 참조 파일 읽기도 형식·전사문 정확성·사용 권한 확인을 대신하지 않는다.

## 시작 및 실행 상태

`--live`, YouTube 활성 실행, 또는 `--check-startup`이면 시작 점검을 수행한다. 핵심 LLM·프롬프트 오류는 시작을 막는다. TTS·참조·장치·음성 파일·디스크 오류는 음성을, VTS·자막·YouTube 오류는 각각 해당 기능을 이번 세션에서 비활성화한다. TCP만 성공한 TTS는 준비 미확인 경고와 함께 명시적 출력 시험 대상으로 남긴다. 로컬 설정 파일은 자동 수정하지 않는다.

라이브 기본 일시 정지는 유지된다. `/status`의 `startup_checks`는 시작 스냅샷, `disabled_features`는 제외된 기능이다. `operating_state`와 `component_faults`는 이후 LLM·TTS·장치·자막 오류 및 VTS 가용성도 반영한다. `not_probed`는 현재 연결을 조회하지 않았다는 뜻이다. 상태 조회는 네트워크를 호출하지 않는다. 문제 해결 후 진단·앱 재시작으로 시작 시 제외된 기능을 복구한다. 자동 재연결·불명 표정 재전송은 추가하지 않았다.

## 응답별 시간

INFO 로그를 같은 `response_id`로 묶어 `elapsed_seconds` 차이를 비교한다. 단위는 초이며 `monotonic_seconds`는 같은 프로세스 안에서 순서를 확인하는 값이다.

| stage | 의미 |
| --- | --- |
| received | 입력 접수. 외부 메시지 ID 대신 새 UUID |
| selected | 대기열 선택 |
| llm_complete | LLM 반환; failed이면 실패 |
| output_checked | 출력 검사 완료; blocked는 안전한 대체 출력 |
| tts_complete | 합성 응답 검증·WAV 저장 완료 |
| first_playback_callback | 첫 출력 콜백 관측 시각; 미관측은 unobserved |
| playback_finished | 장치 처리 종료: completed/stopped/cancelled/failed |
| cleanup_complete | 정리 경로 종료: settled/cancelled/recovery_required |
| voice_failed | TTS 또는 오디오 오류 |
| discard | input_locked / invalid_input / duplicate / overflow / expired / cleared |

기존 queue_seconds·llm_seconds·tts_seconds·playback_seconds도 유지한다. 실행되지 않은 단계의 완료 이벤트는 없다. cleanup의 settled는 답변 성공이 아니다. 파일 삭제·장치 정리가 실패하면 recovery_required를 확인한다.

콜백에서는 타임스탬프만 저장하고 로그 I/O는 재생 처리 종료 후 수행한다. 로그 출력이 늦어도 기록 시각은 실제 콜백 시각이다. 콜백 없는 가짜 플레이어나 장치 열기 실패는 unobserved이며 그 elapsed를 첫 소리 지연으로 해석하지 않는다. 콜백은 DAC·귀에 도달한 시각이 아니므로 동기화 판정에는 녹화·청취가 필요하다.

합성·발화는 운영자가 [청취 체크리스트](voice-listening-checklist.md)를 별도로 실행한다. 진단으로 실제 출력 시험을 자동 시작하지 않는다.
