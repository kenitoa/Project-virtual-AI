# LLM 답변과 음성 연결

`feat/voice-pipeline`은 기존 TTS와 독립 WAV 재생 작업을 기반으로 합니다.
선행 PR의 병합·청취 완료와 이번 구현·검증은 구분합니다.

처리 순서: 입력 → LLM → 기존 출력 검사 → final 표시·기록 → 비어 있지 않은 speech 합성
→ 취소 상태 확인 → WAV 재생 → 임시 WAV 삭제.
응답 한 건의 재생과 정리까지 끝난 뒤 다음 입력을 처리합니다.
별도의 음성 대기열이나 병렬 합성·재생은 추가하지 않았습니다.

## 설정

기존 `configs/app.yaml`의 LLM·TTS 서버·참조 음성 설정을 유지하고 활성화 여부만 확인합니다.

| tts.enabled | audio.enabled | 대화 명령 동작 |
| --- | --- | --- |
| false | false 또는 true | 텍스트만, TTS·재생기 생성 안 함 |
| true | false | 텍스트만, TTS·재생기 생성 안 함 |
| true | true | 텍스트 표시 후 합성·재생 |

```powershell
python -m uv sync --locked
python -m uv run --locked python -m virtual_ai.audio --list-devices
python -m uv run --locked python -m virtual_ai --config configs/app.yaml
python -m uv run --locked python -m virtual_ai --config configs/app.yaml --once "짧게 한국어로 인사해 주세요."
```

`audio.output_device`와 TTS 참조 경로·전사문은 로컬 설정을 사용합니다.
LLM과 GPT-SoVITS 서버는 별도로 실행해야 합니다. 활성화 설정이 서버를 시작하지는 않습니다.
독립 TTS·오디오 CLI의 동작은 그대로 유지합니다.

## 중지·오류·파일 수명

- TTS에는 출력 검사와 음성 정리를 거친 `Response.speech`만 전달합니다.
  차단된 원문은 전달하지 않으며 기존 안전한 대체 답변이 speech이면 그 답변만 합성합니다.
- 모델이나 입력의 메시지 ID 대신 프로그램이 생성한 UUID로
  `generated_audio/<response_id>.wav`를 사용합니다. 기본 디렉터리는 작업 폴더 기준입니다.
  TTS가 반환하는 다른 경로로 재생·삭제 대상을 변경하지 않습니다.
- 완료·실패·취소 모두 해당 응답의 파일만 삭제합니다. 기존 독립 CLI 결과와 다른 파일은
  보존합니다. 권한·파일 잠금 때문에 삭제가 실패하면 `audio_cleanup=failed`를 기록합니다.
- `/stop`은 LLM·음성 작업 취소와 대기 입력 제거를 수행하고 실제 재생기의 중단·해제를
  기다립니다. 이미 표시된 텍스트·대화 기록은 유지합니다. `/forget`은 중지 후 기록도 삭제합니다.
- 합성 중 `/stop` 이후 늦은 결과는 재생하지 않고 정리합니다. 클라이언트 취소가 서버 GPU
  추론의 즉각 중단을 보장하지는 않습니다. 취소를 늦게 처리하는 서버가 있으면 기존 요청의
  종료·시간 제한까지 다음 응답 처리가 지연될 수 있습니다.
- TTS·장치 오류는 일반적인 음성 건너뛰기 안내만 표시합니다. 서버 오류 원문은 표시하지
  않습니다. 텍스트 답변과 기록은 유지하며 다음 입력을 처리합니다. `--once`에서도 음성만
  실패한 경우 텍스트 처리가 성공했으므로 종료 코드 0일 수 있습니다.
- `/quit`/EOF는 생성·합성·재생 작업을 정리하고 파일 삭제가 끝난 뒤 클라이언트를 닫습니다.
  주입형 API를 직접 사용하는 호출자는 `Application.shutdown()` 이후 클라이언트를 닫습니다.

로그는 응답 ID와 `llm_seconds`, `tts_seconds`, `playback_seconds`, 오류 상태만 기록합니다.
음성 시간은 각 클라이언트 호출 구간이며 순수 GPU 연산 시간이나 실제 첫 소리 시각은 아닙니다.
모델 raw·speech·참조 전사문·개인 경로·생성 음성은 커밋하지 않습니다.

## 검증

장치 없는 테스트: `python -m uv run --locked pytest tests/test_voice_pipeline.py`.
정리된 speech만 전달, 텍스트 우선 표시, 안전한 경로, 비활성화·빈 speech,
합성·재생 실패 후 복구, 합성 중 중지·늦은 WAV, 재생 중 중지·대기열 제거,
응답 직렬 처리, worker 취소, `/quit`, 늦은 파일 정리를 기다리는 shutdown,
클라이언트 종료 실패 시 나머지 정리를 확인합니다.

### 실제 연결 (2026-09-21)

[백엔드 설정](backends.md)의 KoboldCpp/Qwen3-8B와 GPT-SoVITS v2,
[오디오 설정](audio.md)의 Realtek MME 장치 3을 사용했습니다.
기존 비상업적 KSS 참조 음성을 로컬 검증에만 사용했습니다.

| 항목 | 관측 |
| --- | --- |
| 실제 `--once` | 종료 코드 0, 텍스트 표시, 실제 합성·재생, 클라이언트 종료 로그 확인 |
| LLM / 합성 / 재생·해제 | 1.324888초 / 1.343135초 / 2.404984초 |
| 파일 정리 | 이번 실행의 새 WAV 잔여 0, 음성·파일 정리 오류 없음 |
| 동시 로딩 | 두 서버 실행 후 VRAM 5184 / 6141 MiB 관측. 최대 사용량이나 부하 한도를 의미하지 않음 |
| 재생 중 `Application.stop()` | 실제 출력 시작 0.35초 뒤 호출, 약 0.057초에 중단·해제; 텍스트 유지·대기열 제거 확인 |
| 중지 후 다음 입력 | 다음 답변 표시와 두 번째 실제 출력 스트림 재생·해제 확인, 새 WAV 잔여 0 |
| 자동 검사 | 음성 연결 19개 포함 전체 174개 테스트 및 9개 subtest, Ruff·포맷 검사 통과 |

실제 음성이 들리는지와 중지 후 무음은 사용자 청취 확인과 별개입니다.
위 결과만으로 음질 또는 모든 부하 조건의 동시 실행 안정성을 확정하지 않습니다.
