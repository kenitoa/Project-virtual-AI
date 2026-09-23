# 선택적 마이크·한국어 STT (15단계)

상태: 자동 검증 완료, **실제 녹음·마이크 검증 대기**. 사용자가 권한 있는 한국어 WAV와
로컬 모델이 아직 준비되지 않았다고 확인했다. 인식 정확도·속도·실제 에코 억제는 검증하지 않았다.

## 1. 분리된 엔진 환경과 파일 변환

제어 프로그램에는 faster-whisper를 설치하지 않는다. 별도 프로세스를 발화마다 실행하므로
모델 로딩 시간이 매번 포함된다. 기본 CPU/int8, 선택 CUDA/float16이며 속도를 보장하지 않는다.
엔진 의존성은 `engines/stt/pyproject.toml`과 해당 디렉터리의 `uv.lock`으로 고정한다.
검증 환경은 Python 3.12.14, faster-whisper 1.2.1, CTranslate2 4.8.2다.

```powershell
python -m uv sync --locked
python -m uv sync --locked --project engines/stt --python 3.12.14
python -m uv run --locked python -m virtual_ai.stt recordings/authorized-ko.wav --python engines/stt/.venv/Scripts/python.exe --model models/faster-whisper-local
```

Linux에서는 엔진 Python 경로를 `engines/stt/.venv/bin/python`으로 바꾼다.
입력은 **16 kHz, mono, signed 16-bit PCM WAV, 0.2~30초**, 최대 964,096바이트다.
다른 형식은 자동 변환하지 않는다. 권한 있는 원본에서 이 형식으로 준비한다.
로컬 모델 디렉터리는 운영자가 별도로 준비하고 모델명·리비전·출처·사용 권한을 기록한다.
모델 이름으로 자동 다운로드하지 않으며 `local_files_only=True`를 사용한다.
현재 실제 모델·녹음 파일은 확정되지 않았다. 예시 경로는 준비된 자산을 뜻하지 않는다.

GPU는 `--device cuda`로 선택한다. CUDA/cuDNN 등 네이티브 의존성은 설치한 CTranslate2에 맞게
별도 준비해야 한다. 이 환경의 GPU 실행은 미검증이다.
[faster-whisper 공식 안내](https://github.com/SYSTRAN/faster-whisper/blob/v1.2.1/README.md)의
CPU/int8·CUDA 예시와 VAD 사용법을 따른다. `segments`를 실제 순회해 변환을 실행한다.

파일 CLI는 운영자가 요청한 인식문을 JSON으로 출력한다. 앱 일반 로그에는 인식문이나 녹음 내용을
출력하지 않는다. 임시 WAV는 성공·실패·취소 시 제거하며 원본 파일은 수정하지 않는다.
강제 프로세스 종료·OS 중단은 정리 코드를 실행하지 못할 수 있으므로 OS 임시 폴더의
`virtual-ai-stt-*`, `virtual-ai-mic-*` 잔여물도 운영자가 보관 정책에 따라 확인한다.

## 2. Push-to-talk와 대화 연결

장치 목록은 기존 명령으로 조회한다. 입력 채널이 있는 **실제 마이크**의 번호를 선택한다.
장치 번호는 재부팅·장치 재연결 때 달라질 수 있다.

```powershell
python -m uv run --locked python -m virtual_ai.audio --list-devices
python -m uv run --locked python -m virtual_ai --backend mock --microphone-device 1 --stt-python engines/stt/.venv/Scripts/python.exe --stt-model models/faster-whisper-local --stt-device cpu
```

`1`은 예시 번호다. 마이크 옵션을 생략하면 입력 장치를 열거나 STT 프로세스를 실행하지 않는다.
실제 LLM 연결은 파일 인식과 마이크 시험을 통과한 후 기존 설정·backend 옵션으로 전환한다.

첫 버전의 조작은 전역 단축키나 상시 청취가 아닌 **신뢰된 로컬 콘솔**이다.
버튼 UI를 연결할 경우 동일한 `start()` / `release()` / `cancel()` API를 사용한다.

| 명령 | 동작 |
| --- | --- |
| `/ptt-start` | 입력을 먼저 잠그고 AI 출력을 중지·정리한 뒤 녹음 시작 |
| `/ptt-stop` | 녹음 종료 → STT → 입력 검사 → `Application.submit()` |
| `/ptt-cancel` | 녹음/STT 취소, 장치·프로세스 정리, 일시 정지 유지 |
| `/stop`, `/pause`, `/panic`, `/quit` | 진행 중 마이크/STT도 취소. 늦은 인식문 폐기 |
| `/status` | disabled/idle/stopping_output/recording/transcribing 및 고정된 오류 사유 |

`--live` 등의 일시 정지 상태에서는 `/resume` 후 시작한다. 녹음·STT 동안 `/resume`은 거절한다.
성공한 녹음만 자동으로 입력 처리를 재개해 마이크 발언을 전달한다. 실패·취소 시 일시 정지를
유지하고 원인을 확인한 운영자가 `/resume`한다. 30초에 도달하면 자동 종료·변환한다.
출력 정리가 확인되지 않으면 녹음을 시작하지 않는다.

녹음부터 STT 완료까지 새 AI 출력을 금지하는 반이중 정책이다. 이 기간의 방송 채팅은 버리고
기존 수신 위치는 전진한다. STT 결과는 `Viewer("microphone", "local")`의 일반 대화 입력이며
“멈춰”, “종료”, `/quit`도 운영 명령으로 해석하지 않는다. 장기 기억에 자동 저장하지 않는다.

**헤드폰을 사용하고 Stereo Mix·루프백·OBS 모니터 출력을 마이크로 선택하지 않는다.**
출력 정리 후 250ms를 기다리지만 이는 음향 에코 제거 기능이 아니다. 다른 앱·OBS가 재생하는
소리와 방의 잔향까지 제거하지 못한다. 실제 재유입 시험 전에는 방송용 검증 완료로 판정하지 않는다.

## 3. 제한·오류 및 검증표

- 녹음 버퍼는 최대 960,000바이트. 입력 overflow는 정상 녹음으로 취급하지 않는다.
- RMS 0.005 미만은 무음 거절. VAD와 no-speech/log-probability 기준으로 낮은 신뢰도 구간을 제외한다.
  잡음이 말처럼 오인되는 모든 경우를 막는 것은 아니다.
- 인식문은 최대 1,000자 및 앱 입력 길이 제한을 모두 적용하고 기존 민감정보 검사를 통과해야 한다.
- STT 기본 제한은 120초, 한 작업만 허용. 시간 초과·취소 시 자식 프로세스를 종료하고 회수한다.
- 엔진 오류·파일 오류·장치 오류는 고정된 사유로 보고하며 예외 원문은 일반 로그에 넣지 않는다.

| 시험 | 자동 검증 | 실제 검증 |
| --- | --- | --- |
| 한국어 인식·문장 정확도·CPU 지연 | generator 순회/결과 경로 fixture | 대기: 권한 있는 녹음·모델 필요 |
| 무음·입력 길이·overflow | 합성 PCM·모의 장치 | 대기 |
| 잡음 | 낮은 신뢰도 구간 제외 fixture | 대기: 실제 환경 잡음 |
| 녹음 중 취소·길이 제한 | 모의 장치 해제·상한 | 대기 |
| STT 실패·시간 초과·취소 | 실제 자식 프로세스 fixture 종료/회수 | 실제 모델 장애 대기 |
| AI 출력 재유입 | 출력 종료 후 캡처, 늦은 결과 폐기 | 헤드폰/OBS 경로 시험 대기 |
| 마이크 꺼짐 | 기존 전체 회귀 테스트 | 기존 방송 검증 상태 유지 |

실제 시험은 “안녕하세요. 오늘은 한국어 음성 입력을 시험합니다.” 등 고정 문장을 사용하고,
기대문·인식문·문장 길이·처리 시간·모델 리비전·장치·CPU/GPU 설정을 비공개 검증 기록에 적는다.
무음, 배경 잡음, 녹음 취소, 엔진 실패, AI 재생 중 시작, 재시작 후 비활성도 각각 확인한다.
