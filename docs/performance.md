# 성능 측정과 최적화 (16단계)

2026-09-23: 측정 도구와 mock 기준선은 검증 완료. **목표 방송 구성의 실측·최적화 전후 비교는 대기**다.
현재 KoboldCpp·VTS·OBS 프로세스와 5001/9880/8001 리스너가 확인되지 않았다.
실제 음성·VTS·OBS·STT 준비 대기 상태를 유지한다. mock 수치를 실제 LLM 성능으로 해석하지 않는다.

## 반복 가능한 실행

```powershell
python -m uv run --locked python -m virtual_ai --backend mock --benchmark configs/performance-questions.json --benchmark-rounds 20 --benchmark-report .local/performance-a.json
python -m uv run --locked python -m virtual_ai --backend mock --benchmark configs/performance-questions.json --benchmark-rounds 20 --benchmark-report .local/performance-b.json
```

서버·모델·출력 장치 준비 후 동일 명령에 `--config configs/app.yaml --backend koboldcpp`를 사용한다.
TTS·오디오·VTS·자막은 설정대로 실행되므로 실제 소리가 나고 모델 화면이 바뀐다.
실행 전에 OBS 로컬 녹화를 준비한다. `--once`, `--live`, YouTube 수신·마이크·장기 기억 연결은
이 격리 측정 모드와 함께 사용하지 않는다. 따라서 현재 도구는 모든 입력을 연결한 실방송 부하 시험을 대체하지 않는다.

첫 질문은 `first_request`, 첫 세트의 나머지는 `warmup`, 이후는 `warm`이다.
외부 서버 시작이나 모델 적재 시간은 포함하지 않으므로 첫 요청을 “모델 로딩 시간”이라 부르지 않는다.
모델 최초 적재는 엔진 종료→동일 실행 옵션으로 시작→엔진이 실제 모델 준비를 보고한 시각을
별도로 측정하고 엔진 로그·버전을 함께 남긴다. 열린 포트만으로 모델 준비를 판정하지 않는다.

지속 실행은 `--benchmark-soak-seconds 1800`으로 요청한다. 지정 시간이 지난 후의 표본은
`post_soak`로 분리한다. 최대 1,000세트 제한 때문에 시간이 충족되지 않을 수도 있으므로
보고서의 `post_soak_completed`를 확인한다. 짧은 단위 테스트의 post_soak는 장시간 운용 증거가 아니다.
세트마다 RAM 기록을 초기화하고 세트 안에서는 순서대로 대화한다. 인위적 중복 제외를 피하기 위해
세트별 합성 사용자를 사용한다. 직렬 실행이므로 대기열 과부하 성능은 별도 시험해야 한다.

질문은 최대 20개, 기본 반복 10회, 명시적 반복은 2~100회다. JSON 보고서는 덮어쓰지 않는다.
질문/응답 원문 대신 질문 인덱스·응답 ID·상태·시간과 질문 세트/설정/캐릭터·프롬프트 해시를 남긴다.
배포 커밋 및 미커밋 변경 여부, 모델 리비전, 엔진 시작 옵션, 전원 모드, 장치·OBS 설정은 아래 표와 함께 기록한다.

## 지표의 의미와 누락 처리

정상 응답만 `normal_only` 통계에 들어간다. 취소·오류·출력 차단·불완전·접수 거절은 별도 건수로 남긴다.
중앙값과 nearest-rank p95를 쓰며 표본 수를 표시한다. 적은 표본의 p95는 신뢰할 만한 꼬리 지연 추정치가 아니다.
측정되지 않은 지표는 0으로 채우지 않는다.

| 지표 | 의미와 제한 |
| --- | --- |
| queue_seconds | 접수 후 선택까지. 현재 기본 작업량은 직렬이므로 거의 비어 있는 큐 |
| llm_generation_seconds | 생성 호출 시작→완료. 외부 모델 로딩은 별도 |
| llm_seconds | 기존 호환 지표. 프롬프트 준비를 포함한 LLM 구간 |
| tts_seconds | 합성 요청 구간 |
| input_to_first_callback_seconds | 접수→장치 첫 출력 콜백. 실제 귀에 들리는 시각은 아님 |
| audio_seconds | PCM 프레임 수/샘플레이트로 계산한 파일 길이 |
| playback_seconds | 재생 호출 구간 |
| mouth_send_after_callback_seconds | 첫 콜백→첫 입 입력 전송 시도. VTS 수신·화면 반영 확인이 아님 |
| input_to_cleanup_seconds | 접수→정리 완료 |
| controller_rss_after_bytes | 요청 직후 제어 프로세스 RSS. 순간 최대치·외부 엔진 RAM은 아님 |
| controller_cpu_percent_one_core | 요청 구간 CPU/실시간, 한 코어 100% 기준. 100ms 미만은 해상도 문제로 제외 |
| controller_cpu_seconds_total | 실행 전체 제어 프로세스 CPU 시간. 측정 오버헤드 포함 |

GPU·VRAM·외부 엔진 CPU/RAM·OBS·실제 화면 립싱크는 현재 JSON의 `unmeasured`에 명시한다.
이 항목이 빠진 보고서는 통합 방송 성능 완료 증거가 아니다.

NVIDIA GPU는 별도 터미널에서 동일 시험 구간을 샘플링할 수 있다. 완료 후 Ctrl+C로 종료한다.

```powershell
nvidia-smi --query-gpu=timestamp,name,utilization.gpu,memory.used,memory.total --format=csv --loop=1 --filename=.local/gpu-performance.csv
```

[NVIDIA 공식 조회 안내](https://docs.nvidia.com/deploy/nvidia-smi/)를 따른다. 장치 전체 부하이며
개별 엔진의 VRAM이라고 해석하지 않는다. 지원하지 않는 값은 미측정으로 남긴다.
Windows 성능 모니터로 제어 프로그램·KoboldCpp·TTS·VTS·OBS 각각의 CPU와 작업 집합을 1초 간격으로
수집하고, 구간별 중앙값·p95·최대치를 함께 남긴다. 공유 PC의 다른 작업도 기록한다.

OBS는 동일 장면·해상도·FPS·인코더로 녹화하며 통계 창의 평균 프레임 렌더 시간, 렌더링 지연 누락,
인코딩 지연 건수를 기록한다. 프레임 누락률을 지연 시간(ms)으로 바꾸지 않는다.
[OBS 성능 안내](https://obsproject.com/kb/encoding-performance-troubleshooting)를 참고한다.
립싱크는 녹화 파일에서 소리 시작/끝과 입 움직임을 직접 비교하고 허용 오차와 관측 건수를 적는다.

## 이번 장비와 측정 결과

- 작업 기준: `04564f1` 위 readiness 미커밋 구현.
- Windows, 제어 Python 3.14.7, Intel Core i5-13420H.
- OS 조회 RAM 34,049,024,000바이트, NVIDIA RTX 4050 Laptop GPU 6,141 MiB.
- `configs/app.example.yaml`, mock, 실제 TTS·오디오·VTS·OBS 미사용.
- 고정 5문항 × 20회 × 독립 프로세스 2회. 각각 최초 1건, 워밍업 4건, warm 정상 95건.
- 최종 원본: `.local/performance-mock-final-a.json`, `.local/performance-mock-final-b.json`.

| warm 지표 | A 중앙값 / p95 | B 중앙값 / p95 |
| --- | --- | --- |
| 큐 대기 (ms) | 0.046 / 0.290 | 0.047 / 0.494 |
| mock 생성 호출 (ms) | 0.055 / 0.388 | 0.056 / 0.784 |
| 입력→정리 (ms) | 0.861 / 4.278 | 0.857 / 7.540 |
| 제어 RSS (바이트) | 45,735,936 / 45,940,736 | 45,932,544 / 46,149,632 |

A 전체 실행 0.539초, 제어 CPU 0.266초. B 전체 실행 0.462초, 제어 CPU 0.328초.
매우 짧은 mock 시험이므로 운영체제 스케줄링과 측정 오버헤드 영향을 크게 받는다.
두 실행은 **동일 구현의 반복성 확인**이며 최적화 전후 비교가 아니다.
실제 모델 적재·장시간·TTS·음성 길이·립싱크·GPU 부하·OBS 결과는 미측정이다.

## 최적화 결정과 다음 통과 조건

현재 자료로 실제 병목을 특정할 수 없어 답변 길이·프롬프트·컨텍스트·GPU 배치·파이프라인을 변경하지 않았다.
문장별 합성·선행 처리·검사 전 토큰 발화도 도입하지 않았다.

실제 통합 기준선 확보 후 한 번에 한 항목만 바꾼다:

1. 답변 길이와 불필요한 프롬프트/최근 기록/장기 기억을 줄이고 말투·사실·회상 품질을 재검사한다.
2. 모델·컨텍스트·GPU 배치를 바꾸되 LLM/TTS/VTS/OBS 동시 실행에서 자원 여유와 p95를 비교한다.
3. 그래도 확인된 병목이 남을 때 전체 답변 검사 후 문장 단위 합성 등을 별도 설계한다.

변경 전후 같은 질문 해시·장비·전원·장면·작업량을 사용하고 설정 차이는 하나씩 기록한다.
`uv run --locked pytest`, Ruff 및 실제 `/stop`·`/panic`·자막·음성·립싱크 회귀 시험을 통과해야 한다.
현재 완료된 것은 관측 도구와 mock 기준선이며, 사용자 요청의 **목표 구성 실측 및 최적화 전후 통과 기준은 대기**다.
# 외부 자원 수집 도구 추가 (2026-09-24)

```powershell
python -m uv run --locked python -m virtual_ai.resource_monitor --pid 1234 --pid 5678 --seconds 30 --interval 1 --gpu --obs --output .local/resources-run01.json
```

PID는 실제 제어 프로그램·외부 엔진 PID로 교체한다. 생략하면 측정기 자신의 PID만 측정한다. 출력은 기존 파일을 덮어쓰지 않는다. 프로세스별 CPU(코어 하나=100%), RSS, 스레드, 핸들/FD, TCP 연결 수의 중앙값·p95와 원시 표본을 저장한다. 프로세스 종료·접근 거부는 0 사용량으로 처리하지 않는다. GPU는 `nvidia-smi`가 제공하는 장치 전체 사용량이며 프로세스별 사용량이 아니다.

`--obs`는 로컬 OBS WebSocket v5의 GetStats만 호출한다. 기본 포트는 4455이며 `--obs-port`로 변경한다. 인증이 필요하면 운영 환경의 `OBS_WEBSOCKET_PASSWORD`를 사용하고 비밀번호를 인자나 저장소에 기록하지 않는다. 화면 전환·송출·녹화를 시작하지 않는다. 연결 실패는 unavailable, 암호 미제공은 authentication_required로 남긴다. [공식 프로토콜](https://github.com/obsproject/obs-websocket/blob/master/docs/generated/protocol.md)의 평균 프레임 렌더 시간과 렌더/출력 누락 프레임 누적 카운터를 기록한다. 누락 카운터는 실행 구간의 처음·끝 차이로 해석하며 인코더 지연 시간 자체가 아니다.

립싱크의 실제 시청각 지연·인코더 지연은 여전히 별도 검증이다. 짧은 자원 측정기 smoke 실행은 목표 방송 부하 검증이 아니다. 앞선 성능 보고서의 미측정 값은 이 도구 추가로 소급 변경되지 않는다.
