# 잔여 검증 진행 기록 — 2026-09-26

사용자가 남은 항목의 해결을 요청한 뒤 수행한 추가 작업이다. 실제 채팅 참여와 마이크 발화는
사용자가 **오늘 15시 이후 가능**하다고 알려왔다. 그 전에 방송 송출이나 마이크 녹음을 시작하지 않는다.
자동 시험 결과와 사람이 참여해야 하는 수용 결과를 구분한다.

## 완료한 추가 검증

| 항목 | 결과와 증거 |
|---|---|
| 운영 화면 상태 요청 | 요청 시간 제한 추가. 응답이 멈추면 연결 끊김을 표시하고, 제어 요청 시간 초과는 실행 실패로 단정하지 않고 결과 미확인으로 표시 |
| 기억 조회 화면 | 사용자/플랫폼 변경 시 이전 조회와 본인 확인·동의 체크 초기화. 이전 대상의 늦은 응답을 새 대상 화면에 표시하지 않음 |
| Windows 자동 회귀 | Python 3.11 **750 passed, 9 subtests**(147.35초), Python 3.14 **750 passed, 9 subtests**(149.63초). Ruff check/format 통과. Node 메모리 DOM 동작 검사는 브라우저 시각 검사와 구분 |
| Linux 배포 ZIP | Python 3.12 격리 컨테이너에서 **746 passed, 4 skipped, 9 subtests**(151.16초), Ruff·mock 실행 통과. 건너뛴 4개는 Windows PowerShell 실행기·소유 프로세스 종료 검사이며 Windows에서는 통과. 실행 중 네트워크 차단, 배포 ZIP만 사용. 같은 PC의 Linux 재현이며 다른 PC 수용은 아님 |
| 생성 기한 수정 | Python 3.11에서 발견한 취소 억제 어댑터의 늦은 답변 노출 가능성 수정. 시간 비교와 `asyncio.Timeout.expired()`를 함께 검사. 고정 시계·취소 억제 회귀 통과 |
| 재개 경계 수정 | YouTube·치지직 재개 시각과 타임스탬프가 정확히 같은 메시지도 과거 메시지로 제외. 낮은 시계 해상도에서 정지 중 채팅이 재개 후 통과하는 경계 방지 |
| 배포 샘플 수정 | Linux 배포 ZIP 검사에서 RAG 예제 누락 발견. 검토된 합성 예제 2개만 명시적으로 포함한 뒤 전체 검사 통과 |
| 실제 OBS 경로 변경 | 새 임시 숨김 자막 소스를 사용. 경로 변경 후 모니터 unavailable, 운영자가 바꾼 경로 보존, 임시 소스 제거 확인. `.local/obs-remapping-actual.json` |
| OBS 화면 정리 | `AI Rehearsal Clean` 장면 추가. VTS 조작 버튼 제외, 기존 장면 변환 보존, 기존 단일 출력 캡처와 자막 소스 재사용. 마이크 캡처 추가 없음 |
| 실제 녹화 | 위 장면에서 60초 녹화. 전체 FFmpeg 디코드, 무재인코딩 MP4 변환, 녹화 중지 완료 확인. `.local/clean-scene-recording.json` |
| 실제 화면 | 저장 이미지에서 캐릭터·자막·리허설 표시 및 조작 버튼 제외 확인. 현재 분노 표식은 보이지 않으며 VTS 표정 상태도 모두 inactive였음. 사용자 청취·주관적 립싱크 수용은 별도 |
| STT 설치 | 기존 `engines/stt/uv.lock`으로 Python 3.12.14 환경 설치. 제어 프로그램 환경에 STT 엔진을 섞지 않음 |
| STT 한국어 파일 | small 모델로 기존 KSS 검증 문장을 2회 인식. 띄어쓰기·문장부호 제외 일치. 약 18.99초/9.47초, 모델 로딩과 프로세스 시작 포함 |
| STT 정리 | 무음 거절, 실제 worker 취소(시작부터 정리까지 약 0.56초), 시간 초과 후 worker 0개 확인. `.local/stt-acceptance/report.json` |
| STT 대안 비교 | base 모델은 같은 문장의 `애쓰는`을 `S는`으로 인식. 채택하지 않음. 설치 작업이 동시에 진행돼 지연 수치를 공정한 모델 간 속도 비교로 사용하지 않음 |

녹화 파일: `.local/obs-verification/recordings/clean-rehearsal-verified.mp4`.
녹화 중지 요청 직후에는 OBS가 active를 반환했다. 정리 완료까지 다시 확인한 결과를 기록했으며,
요청 ACK를 중지 완료로 취급하지 않았다. 현재 공개 송출은 꺼져 있다.
저장된 MP4에서 직접 추출한 프레임도 확인했다. 조작 버튼은 제외됐으며 캐릭터 리본 윗부분이 프레임 밖에 있어 최종 방송 구도 수용은 별도다.

Linux 검증 ZIP은 204개 파일, SHA-256 `95131b917cf8acf2dfd415ba70c0ab49f40d4aebd33a166e6e96785104a9d4bb`다.
JUnit은 `.local/python311-final.xml`, `.local/python314-final.xml`, `.local/linux-validation/reports/linux-py312.xml`에 있다.
이후 변경은 검증 기록 문서이며 해당 ZIP을 최종 승인 릴리스로 게시하지 않았다. 검증 후 작업용 Docker Desktop을 종료했다.

## 실제 장시간 운용

`.local/run_endurance.py`로 실제 KoboldCpp/Qwen3-8B, GPT-SoVITS, 스피커, VTS, OBS를 연결했다.
공개 입력으로 분류한 **합성 데이터**를 약 15초 주기로 전달한다. 인사, 짧은 질문 3종, 오래된 입력을
반복하며 실제 플랫폼 수신은 사용하지 않는다. RAG·기억·마이크는 이 운용에서 비활성이다.

- 최초 목표 시간: 7,200초. 생성 기한 수정본 적용을 위해 **1,918.78초(약 32분), 121회에서 종료**했다. 상태는 `operator_stopped`이며 2시간 합격이 아니다.
- 매 회차: 결과, 단계별 시간, 큐, controller RSS, 서버 정리 확인, 임시 WAV 잔여물, avatar/OBS 상태.
- 미리 정한 기준: 일반 요청 음성 완료율 95% 이상, 정리 실패 0, 회차 종료 큐 0, controller RSS 증가 256 MiB 미만.
- 첫 콜백 p95 7초는 별도의 지연 목표다. 기능 안정성 통과와 지연 목표 통과를 합치지 않는다.
- 외부 프로세스 RSS·CPU·핸들·스레드 및 GPU 사용량·온도도 별도 기록한다. 이 수집은 운용 시작 후 약 15분부터다.
- 같은 PC에서 STT/음성 후보 설치·CPU 추론·회귀 검사도 진행했다. 이 부하는 실제 관측에 포함되며 전용 유휴 환경의 성능으로 일반화하지 않는다.
- 최초 실행은 수정 전 Python 모듈을 로드했다. 디스크의 생성 기한 수정은 실행 중 프로세스에 적용되지 않았으므로 수정본 검증으로 사용하지 않는다.

정상 입력 97건 중 음성 완료 89건(**91.75%**, 기준 95% 미달). 전체 결과는 음성 완료 89건·만료 25건·LLM 기한 초과 7건이다.
첫 오디오 콜백 89건의 중앙값 **7.34초**, p95 **16.80초**로 목표 7초에 미달했다.
controller RSS 최대 증가는 약 **5.93 MiB**이며 회차 종료 후 대기열·취소 정리·임시 WAV 검사는 실패하지 않았다.
원본은 `.local/endurance-baseline-before-timeout-fix.json`, 자원 기록은 `.local/endurance-baseline-resources.json`에 보존했다.

Linux 검사와 후보 엔진 비교 종료 후 수정 코드·기존 8B 엔진으로 **300초 재검증**을 실행했다.
19회 중 정상 입력 15건은 모두 음성 완료, 만료 입력 4건은 차단됐다. 첫 음성 중앙값 4.61초·p95 16.06초,
controller RSS 최대 증가 약 3.70 MiB다. 그러나 마지막 회차에서 VTS 프로세스가 종료돼 avatar unavailable이 되어
시험 상태는 **failed**다. 음성 완료율 100%만으로 전체 합격 처리하지 않는다.
VTS 이전 로그에는 정상 종료 절차가 기록됐으며 크래시나 종료 주체는 확인되지 않았다.
종료 후 OBS 송출·녹화 모두 꺼짐, LLM `idle=1`, `queue=0`을 확인했다.
증거: `.local/remaining-final-state.json`. 30분·2시간 내구 수용과 지연 목표는 여전히 미완료다.
VTS를 다시 실행한 뒤 저장된 토큰 인증·예상 모델 확인·입 닫기 ACK·모델 상태 10회 조회에 성공했다.
활성 표정은 없었다. `.local/vts-recovery-final.json`에 별도 기록했으며, 재연결 성공이 직전 시험 실패를 지우지는 않는다.

현재 상세 기록은 `.local/endurance-current.json`, 외부 자원은 `.local/endurance-resources.json`이다.
정상 종료 시 별도 세션 보고서를 기록한다. 현재 실행의 종료 요청은 `.local/endurance-stop.request` 파일로도 전달할 수 있다.
이 파일은 다음 회차 전에 확인하며 즉시 중단용은 운영 화면의 비상 정지다. 방송 시작 기능은 없다.

## 마이크 참여 검증 준비

현재 장치 열거에서 MME 마이크 배열은 1번, 스피커는 3번이다. 장치 번호는 영구 식별자가 아니다.
`.local/start-ptt-rehearsal.cmd`는 장치 이름과 호스트 API를 다시 확인하고, 장시간 시험 실행 중에는 시작을 거절한다.
STT 모델은 `models/faster-whisper-small`, 엔진은 `engines/stt/.venv/Scripts/python.exe`를 사용한다.
런처를 켜도 녹음은 시작하지 않는다. 운영 화면 PTT 시작 또는 `/ptt-start`가 필요하다.

15시 이후에는 장시간 시험과 음성 출력이 겹치지 않게 정리한 뒤 다음을 진행한다.

1. 운영 화면에서 PTT 시작 → 한국어 고정 문장 → 완료. 인식문과 실제 발화를 비교한다.
2. 무음, 배경 소음, 발화 중 취소, AI 출력 중 PTT 시작을 별도 회차로 검사한다.
3. 테스트 방송 URL·접속 가능한 채팅 계정이 제공되면 실제 플랫폼 수신부터 최종 음성까지 검사한다.
4. 사용자 청취와 화면 판단을 확인한 뒤 해당 항목만 완료로 갱신한다.

15시라는 시간만으로 녹음·방송을 자동 시작하지 않는다. 아직 테스트 방송 URL은 제공되지 않았다.

## 방송 자산 조사

- Akari는 계속 로컬 검증용이다. [VTS 공식 조건](https://denchisoft.com/license/)과
  [Steam EULA](https://store.steampowered.com/eula/1325860_eula_0)의 사용 범위를 최종 운영 용도와 맞춰야 한다.
- KSS 참조 음성은 [배포 문서](https://huggingface.co/datasets/Bingsu/KSS_Dataset/blob/48fdfd7ab1dbc1a62e4e8a8b9f4c360259d51d3c/README.md)의
  비상업 조건 때문에 기존 로컬 시험용으로 유지한다.
- 대체 음성 후보로 [Qwen3-TTS 0.6B CustomVoice](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice)의
  한국어 Sohee 프리셋을 별도 설치했다. 모델 카드의 Apache-2.0, revision `85e237c12c027371202489a0ec509ded67b5e4b5`를 기록했다.
  현재 방송 엔진을 교체하지 않았고, 생성 음성의 품질·청취 수용을 자동 승인하지 않는다.
- 이미 설치된 [Hiyori](https://www.live2d.com/en/learn/sample/momose-hiyori/)는 대체 모델 후보다.
  [사용 계약](https://www.live2d.com/eula/live2d-free-material-license-agreement_en.html)의 운영 주체 조건과
  [개별 조건](https://www.live2d.com/learn/sample/model-terms/)의 디자인 변경 금지·출처 표시를 적용해야 한다.
  사용자/운영 법인의 자격을 임의로 가정하지 않았으며, 운용 중인 모델을 바꾸지 않았다.

STT 모델은 [SYSTRAN 공식 small 변환본](https://huggingface.co/Systran/faster-whisper-small),
revision `536b0662742c02347bc0e980a01041f333bce120`, MIT 표시를 확인했다.
모델 해시·음성·토큰·녹화·실제 입력은 `.local`/`models` 등 Git 제외 경로에 둔다.

## 새 음성 후보 파일 검증

Qwen3-TTS Sohee로 “안녕하세요. 만나서 반가워요.”를 생성했다. CPU 생성에는 로딩 포함 101.92초가 걸렸고,
24 kHz 모노 PCM16, 4.4초 파일의 small STT 인식문은 원문과 일치했다.
이를 기존 GPT-SoVITS 참조 음성으로 사용한 고정 문장 2개도 공백을 제외한 전사 대조를 통과했다.
TTS 생성 시간은 각각 8.45초·5.54초였다. 파일 생성·전사 검사이며 사람의 청취나 음색 평가는 아니다.
첫 긴 참조 후보는 뒤 문장이 잘려 폐기 판정하고 짧은 v2 후보를 사용했다.

- 참조 후보: `.local/voice-reference/sohee-reference-candidate-v2.wav`
- 생성·전사 증거: `.local/voice-reference/generation-v2-report.json`, `transcript-v2-check.json`, `sovits-candidate-report.json`
- 별도 리허설 설정: `.local/voice-candidate-rehearsal.yaml`. 기본 참조 음성은 바꾸지 않았다.

## 실제 LLM 후보 비교

기존 8B는 실제 24턴 기계적 계약 검사 **24/24**를 통과했다. 텍스트 처리 중앙값 2.28초·p95 4.55초다.
TTS·음성 출력은 비활성이고 생성 기한을 60초로 늘린 설정이므로 생방송 지연 합격으로 해석하지 않는다.

[4B 후보](https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF)는 별도 CPU 프로세스·포트 5002로 실행했다.
결과 **21/24**: 초기 생성 기한 초과·정리 미확인으로 해당 시나리오가 중지되어 3턴이 실패했다.
이는 세 답변의 의미 품질이 나쁘다는 판정이 아니다. 전체 중앙값 7.16초·p95 31.05초도 실패/차단 턴을 포함하며,
GPU 8B와 공정한 모델 속도 비교가 아니다. 이 구성은 채택하지 않았고 작업용 CPU 후보는 종료했다.
보고서는 `.local/llm-quality-comparison.json`이다. 평가기의 실제 로드 모델 신원 검증 필드는 별도 미확인으로 유지된다.

실행 중인 8B 엔진을 종료·교체하는 명령은 자동 승인 검토가 `blocked by policy`로 차단했다.
구체적인 사유는 반환되지 않았으며 명령은 실행되지 않았다. 차단을 우회하지 않고 기존 8B를 유지했다.

## 아직 합격으로 바꾸지 않은 항목

실제 시청자 왕복, 실제 방의 마이크·청취, 최종 방송 자산 적합성, 다른 사람/PC의 설치 재현,
최종 릴리스 커밋 CI 및 승인이다. 운영 화면의 실제 브라우저 시각·클릭 검사는 도구 연결 문제도 남아 있다.
Windows 도우미는 `native pipe unavailable (os error 2)`, 브라우저는 로컬 URL에
`net::ERR_BLOCKED_BY_CLIENT`를 반환했다. 실제 HTTP/JS 동작 검사를 시각 검사로 바꾸어 적지 않는다.

공개 저장소의 현재 브랜치 상태만 읽었으며 새 커밋·push·병합·v1.0 태그·릴리스를 게시하지 않았다.
