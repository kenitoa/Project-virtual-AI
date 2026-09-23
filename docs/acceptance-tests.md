# 통합·장애·과부하·지속 실행 수용 시험 (17단계)

상태: **자동 검증 완료 / 실제 장치·운영 수용 시험 대기**.
자동 시험에서 새 중대 실패는 발견되지 않았으나, 목표 방송 구성의 완료 판정은 아직 내리지 않는다.
실제 음성 청취·VTS·OBS·YouTube·마이크 준비 대기 상태는 이전 단계와 같다.

## 네 시험층

| 층 | 실행 및 증거 | 현재 상태 |
| --- | --- | --- |
| 단위 | `python -m uv run --locked pytest`의 설정·파싱·정책·기억·인증·대기열 테스트 | 로컬 통과 |
| 가짜 서버 통합 | `scripts/run_acceptance.py`, 기존 HTTP/loopback WebSocket/모의 장치·콜백 테스트와 `tests/integration` | 반복 통과 |
| 실제 장치 | 권한 있는 음성, 실제 출력 장치 제거, VTS 모델, 마이크, OBS MKV 재생 확인 | 대기 |
| 운영 | 테스트 방송·토큰 만료/취소·네트워크 단절·30분/2시간 실행 | 대기 |

시험 목록과 합성 채팅 작업량은 `tests/fixtures/chat_scenarios/acceptance.json`에 있다.
기존 테스트 파일을 복사하지 않고 시나리오마다 연결한다. 일부 파일은 단위와 통합 시험을 함께 포함한다.
통합이라는 이름만으로 모든 시험이 실제 소켓·장치를 사용한다고 해석하지 않는다.
HTTP MockTransport, loopback HTTP/WebSocket 서버, 모의 PortAudio 콜백을 각각 기존 경로에서 검증한다.

공개 테스트의 `tests/conftest.py`는 TCP 연결을 loopback으로 제한한다. 실제 서버 주소로 자동
폴백하지 않는다. 프록시 환경변수와 수동 YouTube 토큰을 테스트에서 제거하고 시험별 fixture를 사용한다.
이 보호는 Python 테스트 연결에 대한 가드이며 OS 수준 네트워크 샌드박스는 아니다.
실제 토큰·참조 음성·모델·시청자 원문은 CI에 넣지 않는다. 외부 연결이 필요한 실제 시험은 별도 승인된
테스트 서버와 비공개 로컬 설정에서만 수행한다.

## 실행 순서

```powershell
python -m uv sync --locked
python -m uv run --locked pytest
python -m uv run --locked ruff check .
python -m uv run --locked ruff format --check .
python -m uv run --locked python scripts/run_acceptance.py --repeat 2 --soak-seconds 10 --output .local/acceptance-short
```

짧은 시험과 실제 장치 수용 시험이 통과한 뒤 필요할 때 점진적으로 늘린다:

```powershell
python -m uv run --locked python scripts/run_acceptance.py --repeat 1 --soak-seconds 1800 --output .local/acceptance-30m
python -m uv run --locked python scripts/run_acceptance.py --repeat 1 --soak-seconds 7200 --output .local/acceptance-2h
```

30분과 2시간은 **제안된 시험 길이**다. 이번 작업에서는 실행하지 않았다.
이 스크립트의 soak는 모의 장치 반복 시험이다. 실제 방송의 동일 시간 시험은 별도 녹화·운영 기록이 필요하다.
기존 출력 폴더는 덮어쓰지 않는다. `.local/` 아래 새 폴더를 사용한다.
단위 시험 전체 통과는 별도 게이트이고, 실행기는 manifest의 장애·통합 선택 집합을 반복한다.

`report.json`에는 HEAD, 미커밋 여부, 추적 및 Git에서 제외되지 않은 파일의 내용 해시,
Python/OS, 반복별 종료 코드와 소스 변경 여부를 기록한다. 시험 도중 소스가 바뀌면 실패한다.
`tests-N.xml`은 JUnit 결과, `tests-N.log`는 로컬 상세 결과, `soak-N.json`은 자원 표본이다.
미커밋 작업 공간의 같은 해시 통과는 **릴리스 커밋 통과를 대신하지 않는다**. 커밋 후 같은 명령을 다시 실행한다.
실행 중 소스를 편집하지 않는다. 시간 제한 초과는 성공으로 취급하지 않는다.

## 필수 장애별 기대 동작

| 장애 | 자동 증거 | 기대 동작 / 실제 확인할 사항 |
| --- | --- | --- |
| LLM 종료 | cancellation | 오류를 제한해 보고하고 늦은 답변은 폐기. 서버 정리 불확실 시 다음 생성 차단 |
| TTS 종료 | tts, voice_pipeline | 텍스트 정책 유지, 늦은 WAV 미재생, 임시 파일 정리 |
| VTS 종료 | vts_client, expression_pipeline | 아바타 기능 축소, 불확실한 명령 재전송 금지, 음성 유지 |
| 출력 장치 제거 | audio | 재생 중단·장치 해제, 정리 불확실 시 복구 필요. 실제 물리 제거는 대기 |
| 인증 만료/취소 | youtube_auth, youtube_stream | 제한된 갱신/재시도, 실패 시 수신 정지·재인증 요구 |
| 자막 파일 잠금 | subtitles | 파일 교체 오류를 기능 축소로 표시, 임시 파일 정리, 음성 유지. 실제 OS 잠금은 대기 |
| DB 잠금 | memory_store locked | 제한된 대기와 명확한 오류, 데이터 손상·무한 대기 없음 |
| 디스크 부족 | memory_store capacity | 실제 SQLite 페이지 한도로 SQLITE_FULL 유도, 트랜잭션 롤백. OS 볼륨 고갈은 미실행 |
| 채팅 폭주 | input_overload, integration | 제한된 큐·중복 캐시·receipt, 독점 방지, 만료/버림 집계 |
| 연속 중지·재개 | operator_controls, integration | 먼저 잠금, 바쁜 재생 중 중단, 복구 전 새 발화 금지 |
| 재생 중 종료 | audio, voice_pipeline, integration | 음성 중단, 작업 종료 대기, 장치·WAV 정리 |

디스크 부족의 자동 증거는 SQLite 저장 경로에 한정한다. 실제 TTS 출력 볼륨의 ENOSPC와
장치 분리·OBS 캡처 복구까지 완료했다고 취급하지 않는다. 실제 시험에서 누락된 결과는 대기로 표시한다.

## 지속 실행의 관측과 판정

한 앱·한 이벤트 루프에서 실제 응답 처리 경로를 반복한다. 가짜 플레이어가 재생을 유지하는 가장
바쁜 시점에 합성 채팅 100건을 투입하고 `/panic`에 해당하는 운영자 API를 실행한다.
이후 잠금·출력 종료·잔여 파일·대기열·receipt·캐시·작업 수를 검사하고 복구한다.
마지막에도 재생 중 종료한다. fixture 자체가 남기는 호출 목록은 매회 비워 시험 도구의 누적을 제외한다.

매회 단언: 정리 후 활성 재생 0, 대기열/receipt 0, 임시 WAV 0, 기준 이상의 비동기 작업 없음,
중복 캐시 상한 준수. 약 1초마다 RSS와 Windows 핸들/Linux FD 수를 추가 기록한다.
RSS·핸들은 자동 선형 누수 판정을 하지 않는다. 최초 워밍업과 이후 구간을 나눠 지속 증가가 있는지
운영자가 검토하고, 미지원 값은 미측정으로 남긴다. 핸들 수는 열린 네트워크 연결 수와 같지 않다.
실제 연결 수·GPU·장치는 `unmeasured`에 명시한다.

실제 시험에서는 [성능 절차](performance.md)의 프로세스·GPU 표본과 OBS 녹화를 함께 수집한다.
엔진별 열린 연결 수도 Windows TCP 연결 목록/OS 성능 도구에서 동일 간격으로 기록한다.
기대 동작과 다르거나 작업·연결·WAV가 계속 증가하면 실패로 기록하고 장시간 시험 확장을 중단한다.

## 이번 결과

- 기준 HEAD `04564f1134dd330d77a164904190087ab59d1c23`, readiness 미커밋 작업 공간.
- Windows 11, Python 3.14.7. 전체 **595 passed, 9 subtests passed**, Ruff 통과.
- `.local/acceptance-stage17/report.json`: 각 **326 passed**, 2회 모두 종료 코드 0,
  동일 작업 공간 해시 `3412f447f4d0ca290ee9373ce27d915a521745a2178860fc4defcbc10e512f75`.
- 10초씩 실행한 soak: 290회/271회, 각 사이클당 채팅 100건. 종료 후 작업 1개(시험 주 작업),
  큐 0, WAV 0, 활성 모의 재생 0, 열린 핸들 154로 일정.
- RSS 표본 범위: 62,877,696~62,963,712바이트 / 62,689,280~62,775,296바이트.
  짧은 구간 관측이며 메모리 누수가 없다는 장시간 보증은 아니다.

CI 설정은 Windows·Ubuntu × Python 3.11/3.12/3.13/3.14로 확장하고 두 번의 짧은 수용 시험을 추가했다.
**원격 CI는 아직 실행하지 않았다.** 이번 로컬 통과는 3.14.7이며, 다른 조합의 실행 통과와 실제 장치
검증이 확보되기 전에는 그 조합을 릴리스 검증 완료로 표시하지 않는다. STT 엔진의 별도 Python 환경은
이 제어 프로그램 CI와 구분한다.

릴리스 완료 판정은 커밋된 동일 소스의 반복 통과, 지원 OS/Python CI, 실제 장치·운영 결과,
장애 기대 동작 일치 및 알려진 중대 결함 해소가 모두 확인된 뒤 내린다.
