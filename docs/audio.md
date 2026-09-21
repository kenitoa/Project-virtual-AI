# WAV 재생·중지

범위는 운영자가 지정한 기존 WAV의 재생, 출력 장치 선택, 중지, 자원 해제입니다.
TTS 생성·LLM 대화·OBS·자동 재생 큐·오디오 변환은 포함하지 않습니다.
`feat/audio-playback`은 아직 병합되지 않은 `feat/tts-client`를 기반으로 작업했습니다.
PR #3의 완료·병합과 이 기능의 실제 청취 검증은 별도로 확인해야 합니다.

## 설정과 실행

기존 `configs/app.yaml`의 다른 설정을 보존하고 다음 절만 추가합니다.

```yaml
audio:
  enabled: true
  output_device: null
```

`output_device`는 기본 출력(`null`), 음수가 아닌 장치 번호, 또는 장치 이름입니다.
이름이 여러 호스트 API와 일치하면 모호성 오류가 발생할 수 있으므로 목록의 장치 번호나
호스트 API를 포함한 이름으로 지정합니다. 장치 번호는 재부팅·연결 변경 후 달라질 수 있습니다.
`--device`는 이번 실행에만 설정을 덮어씁니다. 장치 목록 조회에는 활성화 설정이 필요 없습니다.

```powershell
python -m uv sync --locked
python -m uv run --locked python -m virtual_ai.audio --list-devices
python -m uv run --locked python -m virtual_ai.audio generated_audio/tts-real-01.wav --config configs/app.yaml --device 3
python -m uv run --locked python -m virtual_ai.audio generated_audio/tts-real-01.wav --config configs/app.yaml --device 3 --interactive
```

경로에 공백이 있으면 명령행 인자를 따옴표로 감쌉니다. 상대 WAV 경로는 현재 작업 폴더 기준입니다.
대화형 모드에서는 `/stop`으로 중지한 다음 `/play generated_audio/다음파일.wav`로 다시 재생하고,
`/quit` 또는 EOF로 종료합니다. Ctrl+C도 출력 장치 중단·해제를 기다립니다.
한 번에 한 파일만 재생하며 이미 재생 중이면 새 요청을 거절합니다.

지원 형식은 최대 100 MiB의 RIFF PCM WAV, 모노·스테레오, 8/16/24/32-bit 정수입니다.
8-bit는 unsigned PCM입니다. WAV의 샘플레이트·채널 수·비트 깊이를 그대로 장치에 전달하며
미지원 형식은 오류로 처리합니다. MP3, float WAV, 샘플레이트 변환은 지원하지 않습니다.
Linux에서 실제 장치를 사용하려면 OS의 PortAudio 라이브러리도 필요합니다.
텍스트 명령·CI 가짜 장치 테스트는 PortAudio를 로딩하지 않습니다.

한 번 재생하는 CLI 종료 코드는 정상 완료 0, 파일·장치·재생 오류 1,
설정·인자 오류 2, Ctrl+C 130입니다. 대화형 명령의 재생 오류는 화면에 알리고 다음 명령을
받으며 `/quit`/EOF는 정상 종료합니다.

## 중지 의미와 검증

[sounddevice 공식 스트림 API](https://python-sounddevice.readthedocs.io/en/0.5.3/api/streams.html)의
`stop()`은 대기 버퍼를 마저 출력하고, `abort()`는 기다리지 않고 출력을 중단합니다.
이 프로젝트의 `AudioPlayer.stop()`은 **긴급 중지**이며 장치의 `abort()`에 연결됩니다.
정상 EOF만 버퍼를 소진합니다. 중지·취소·종료는 `close()`까지 기다리고,
해제 실패는 `AudioError`로 보고합니다. 재생 완료 대기로 이벤트 루프를 막지 않습니다.
물리 장치·드라이버의 지연이 있으므로 API 반환만으로 사람이 듣는 무음을 확정하지 않습니다.

CI: `python -m uv run --locked pytest tests/test_audio.py`.
가짜 스트림으로 정상 EOF의 버퍼 소진, PCM 형식·장치 전달, 중지·취소·종료의 abort/close,
장치 준비 중 반복 취소, 다음 파일, 중복 재생 거절, 잘못된 파일·장치,
출력 콜백·장치 분리·자원 해제 오류, 설정 검증, 대화형 명령 처리를 확인합니다.

실제 스피커·헤드폰에서는 다음을 별도로 확인합니다.

1. 사용할 출력 장치를 목록에서 선택하고 기존 음성 WAV를 끝까지 듣습니다.
2. `--interactive`로 재생 중 `/stop`을 입력하고 남은 음성이 끊겨 무음이 되는지 듣습니다.
3. `/play`로 다음 WAV가 정상 재생되는지 듣습니다.
4. 잘못된 경로와 존재하지 않는 장치 번호로 오류를 확인합니다.
5. 재생 중 `/quit`/Ctrl+C 후 소리가 멈추고 같은 장치를 다시 열 수 있는지 확인합니다.

## 로컬 기록 (2026-09-21)

Windows, Python 3.14.7, sounddevice 0.5.6, PortAudio V19.7.0-devel에서 확인했습니다.
선택 장치는 Realtek Audio 스피커의 MME 출력(검증 당시 장치 3)이며,
기존 TTS의 32kHz 모노 16-bit PCM 파일만 사용했습니다.
참조·생성 음성의 사용 조건은 [백엔드 기록](backends.md)을 따릅니다.

| 항목 | 관측 |
| --- | --- |
| 정상 재생 | 실제 RawOutputStream 완료 `True`, 스트림 `closed=True` |
| 재생 중지 | 출력 시작 후 약 1초에 `stop()`; `False` 반환, 최종 검증의 장치 해제까지 약 0.043초 |
| 다음 파일 | 2초 대기 후 정상 재생 `True`, 장치 해제 확인 |
| 잘못된 파일·장치 | 없는 WAV와 장치 99999에서 `AudioError` 확인 |
| 종료·자원 | 모든 실제 스트림 `closed=True` 확인 |
| 실제 CLI | 단일 음성 WAV 재생 종료 코드 0; 별도 무음 fixture로 `/stop` → `/play` → 재생 중 `/quit` 종료 코드 0, 이후 동일 장치 다시 열기·닫기 성공 |
| 직접 청취 | 사용자 확인 대기. 발음·중지 후 무음·다음 재생의 청취 완료로 간주하지 않음 |

초기 실제 CLI 검사에서 장치 `active=False`가 완료 콜백보다 먼저 도착해 정상 EOF를
오류로 처리하는 경합을 발견했습니다. 완료 콜백과 제한 시간으로 판정하도록 수정한 뒤
실제 장치 재검증과 해당 순서의 회귀 테스트를 통과했습니다.
장치의 `stop`/`abort`/`close`는 오류 무시 기본값을 끄고 실패를 보고합니다.

모델·음성 자산·개인 경로·실행 로그는 Git 제외 경로에만 보관합니다.
자동 테스트나 장치 API 성공은 실제 청취 확인을 대체하지 않습니다.

### 통합 전 재확인 (2026-09-21, 기반 커밋 `5bc00ad`)

`uv sync --locked` 후 장치 목록을 다시 조회했습니다. 로컬 설정의
`tts.enabled=true`, `audio.enabled=true`, 출력 3번 Realtek 스피커(MME)를
현재 목록과 대조했고 설정 파일은 변경하지 않았습니다.
KoboldCpp 대화 CLI에서 한국어 답변 표시와 합성·재생 완료 로그, 임시 WAV 삭제,
재생 완료 후 `/quit`의 정상 종료를 확인했습니다. 재생 중 종료 검증과는 구분합니다.

사용자가 이번에는 청취할 수 없다고 답했으므로 다음 항목은 모두 **청취 미확인**입니다.

| 직접 확인할 항목 | 상태 / 통과 기준 |
| --- | --- |
| 일반 답변 | 대기: 표시된 한국어 답변과 음성이 대응하고 심한 잘림·잡음·무음이 없음 |
| 재생 중 `/stop` | 대기: 남은 음성이 멈추고 다음 질문의 음성이 정상 재생됨 |
| 합성 중 `/stop` | 대기: 취소한 답변이 뒤늦게 재생되지 않음 |
| 재생 중 `/quit` | 대기: 음성이 멈추고 종료되며 재실행 후 같은 장치를 사용함 |

종료 코드 0이나 완료 로그로 위 항목을 통과 처리하지 않습니다.
통합 PR은 청취 결과를 기록할 때까지 초안으로 두며 main 병합은 보류합니다.
