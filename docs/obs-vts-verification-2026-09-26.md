# OBS·VTube Studio 실제 검증 — 2026-09-26

사용자의 후속 요청에 따라 OBS 설치, 실제 출력 캡처, 저장 영상 검사와 VTube Studio 설치까지 진행했다.
LLM/RAG의 앞선 평가와 이번 장치 검증은 [이전 검증 기록](live-verification-2026-09-26.md)과 구분한다.

## 완료한 OBS·음성 검증

- OBS 공식 32.2.2 Windows ZIP을 내려받아 Git 제외 `.local/obs-verification/portable-clean/`에 구성했다.
  공식 릴리스 SHA-256 `4d6e40e3ab155f56b30de517380566a206d74b63cdf5ad49aa596924768f97e1` 일치,
  실행 파일의 OBS Project 서명 유효를 확인했다.
- 별도 `LocalVerification` 프로필/장면, 1280×720 / 30 FPS, x264 / MKV, 48 kHz 스테레오 AAC를 사용했다.
  Realtek 스피커 WASAPI 출력 캡처 하나만 두고 마이크·전역 데스크톱 오디오·오디오 모니터링은 사용하지 않았다.
  WebSocket 제어에는 무작위 암호를 적용하고 로컬 클라이언트에서 접속했다. 송출은 시작하지 않았다.
- 실제 `WAVPlayer`와 `SubtitleWriter`로 인사, 긴 안내, 중간 정지, 정지 후 재생을 수행했다.
  사용자는 직접 청취 후 **“정상적으로 들림 — 겹침·잘림 없음”**이라고 확인했다.
- 예열한 실제 Qwen3-8B + GPT-SoVITS와 RAG의 전체 앱 경로를 OBS와 함께 실행했다.
  질문부터 재생 완료까지 **12.983초**, 근거 검증 `supported`, 음성 `completed`, 임시 WAV 0개를 확인했다.
- 저장된 MKV 전체를 FFmpeg로 디코딩해 오류가 없음을 확인했다.
  저장 영상의 4초·38초·43초 프레임을 직접 열어 한글 자막, 실제 생성 답변, 정지 후 자막 비움을 확인했다.
  단순 OBS 미리보기/API 성공으로 대신하지 않았다.

| 기록 항목 | 관측 결과 |
|---|---|
| 저장 파일 | `.local/obs-verification/recordings/2026-09-26 12-08-42.mkv` |
| 디코딩된 오디오 길이 | 44.821초 |
| 음성 중간 정지 | 0.017초, 재생 완료값 false, 자막 빈 파일 |
| 정지 후 재생 | 완료값 true |
| 원본 WAV와 캡처 음량 변화 상관계수 | 인사 0.9980 / 안내 0.9974 / 재개 0.9781 |
| 최초·정지 후·종료 무음 구간 RMS | 모두 0 |
| PCM 최대 진폭 / 클리핑 샘플 | 26997 / 0 |
| OBS 출력 누락 프레임 | 0 |
| OBS 렌더 누락 프레임 | 해당 구간 1개 증가 |
| 공개 송출 | 비활성 |

이 수치는 약 45초의 국소 검사다. 음질 전체나 장시간 무오류를 보장하지 않는다.
사용자의 청취 확인은 실제 스피커 재생에 대한 확인이며, 저장 영상의 직접 청취 확인과 구분한다.
저장 오디오는 원본 신호 대조·무음·클리핑 검사로 추가 확인했다.

## 발견하고 해결한 설정 문제

처음에는 OBS `StartRecord` 요청이 성공해도 녹화 파일이 생성되지 않았다.
최초 실행 마법사가 출력을 잠근 상태가 원인이었다. 최초 실행 완료 설정을 적용한 새 포터블 인스턴스를 만들고,
검증 스크립트가 `GetRecordStatus.outputActive`를 확인한 뒤에만 재생하도록 수정했다.
이전 로그는 보존했다. 이후 MKV 생성·정지·전체 디코딩까지 성공했다.

## VTube Studio 실제 연결 및 통합 녹화

- 공식 Steam 앱 1325860을 설치했다. 다운로드 642,899,392바이트, 설치 완료 상태를 확인했다.
- 기본 모델 Akari / Hiyori / Hijiki / Tororo / Wanko의 설치 파일을 확인했다.
- OBS 창 캡처로 실제 VTube Studio 화면을 확인했다.
- VTube Studio **1.35.10**에 실제 인증 토큰을 발급받아 저장했다. Akari를 API로 불러오고 실제 모델 ID를 고정했다.
- `MouthOpen` 입력 0..1 → `ParamMouthOpen` 출력 0..2.3의 기존 매핑을 확인했다.
  입력 0 / 1 / 0을 보내 실제 입 닫힘 / 열림 / 닫힘 화면을 확인했다.
  Heart Eyes / Eyes Cry의 실제 `ToggleExpression` ID만 happy / sad에 연결하고 기본 표정 복원도 확인했다.
- 실제 PCM 재생 콜백의 음량에 따른 입 움직임, 음성 정지 후 입 닫힘, 다음 재생을 확인했다.
  표정·입 매핑 증거는 `2026-09-26 12-23-43.mkv`와 `avatar-report.json`에 보존했다.
- 현재 Windows 화면 제어 도구는 `Computer Use native pipe is unavailable` 오류로 연결되지 않는다.
  설치 버튼과 최초 인증은 사용자가 처리했다. API 시작 설정은 원본 백업 후 직접 적용했고,
  인증 이후의 모델 선택·표정·입 매핑·OBS 장면 구성은 API로 수행했다.

실제 기본 모델에는 이름이 빈 애니메이션 단축키가 있었다. 기존 클라이언트가 목록 전체를 거부하던 문제를
수정했다. 표시 이름만 빈 문자열을 허용하며 실제 실행 권한인 모델 ID·단축키 ID·액션 종류·표정 파일 검증은 유지한다.
관련 회귀 검사 82개를 통과했고, 전체 검사는 **733 passed + 9 subtests**다.
전체 실행 중 Windows/Python 3.14 파이프 소멸자의 경고가 1개 있었다.
해당 자막 검사 18개를 `PytestUnraisableExceptionWarning` 오류 처리로 별도 재실행해 통과했다.
Ruff check / format(158개 파일)도 통과했다.

### 반복 실행에서 발견한 문제와 재검증

1. OBS GDI+ 파일 자막은 변경 감지 후 다음 주기에 화면을 갱신하므로 약 1~2초의 지연이 있다.
   실제 녹화에서도 정지 직후 자막이 남았다. 검증용 `subtitle_refresh.py`가 고정 파일 변경을 100ms 간격으로
   확인해 해당 두 자막 소스만 갱신하도록 했다. OBS의 기존 파일 읽기도 유지해 보조 프로세스가 종료되어도
   원래 파일 감시는 계속된다. 이는 `.local` 리허설 도구이며 기본 앱에 OBS 자동 제어를 추가한 것은 아니다.
   [OBS 구현 근거](https://github.com/obsproject/obs-studio/blob/32.2.2/plugins/obs-text/gdiplus/obs-text.cpp).
2. 24 GPU 레이어 설정으로 모든 프로그램을 반복 실행했을 때 합성이 22.7 / 24.1초까지 늘었다.
   앱은 공개 응답 기한을 넘긴 음성을 차단했다. 이 실패 보고서는 `avatar-report-refreshed.json`에 남겼다.
3. 이 6GB GPU의 동시 실행을 위해 KoboldCpp GPU 레이어를 **24 → 18**로 조정했다.
   동시 상주 VRAM 관측치는 약 **5,853 → 5,139 MiB / 6,141 MiB**로 줄었다.
   이는 메모리 여유 확보 조치이며 지연 증가의 모든 원인을 확정한 것은 아니다.
   생성 제한·공개 응답 기한은 늘리지 않았다. TTS 예열 6.406초 후 같은 앱 시나리오를 재실행했다.

| 최종 통합 시나리오 | 첫 재생까지 | 결과 |
|---|---:|---|
| 실제 LLM + RAG + TTS + VTS + OBS | 12.887초 | 음성 완료, 전체 20.785초 |
| 재생 도중 운영자 중지 | 8.947초 | 정지 요청 처리 0.019초, 음성 cancelled |
| 정지 후 다음 질문 | 8.154초 | 음성 완료, 전체 14.616초 |

최종 파일은 `.local/obs-verification/recordings/live2d-integrated-verified.mp4`이며
원본은 `2026-09-26 12-34-08.mkv`다. 전체 디코딩 오류가 없었다.
52.992초 오디오, 클리핑 0개, 정지 후·최종 무음 RMS 0을 확인했다.
저장 영상 34초에서 정지 약 0.44초 후 **입 닫힘과 자막 비움**, 51초에서 종료 상태를 직접 확인했다.
해당 구간 OBS 렌더/출력 누락 프레임 증가는 모두 0, 임시 WAV는 0개다.
첫 재생 지연은 아직 8~13초 수준이므로 즉각적인 대화 반응이나 장시간 방송 안정성이 검증됐다고 판단하지 않는다.

### 실제 VTS 연결 단절

실제 TTS·스피커·VTS 재생 도중 해당 플러그인의 WebSocket 연결만 닫았다.
음성은 끝까지 재생되고 VTS는 `unavailable`, 운영 상태는 `기능 축소`로 남았다.
같은 불확실한 세션의 표정 요청을 재개하지 않았다. 이 검사는 `_speak` 경로의 장애 주입으로,
LLM/RAG 생성 검사와 구분하며 `disconnect-report.json`에 기록했다.

Akari는 이번 로컬 검증용 모델이다. 최종 방송용 모델로 확정하지 않는다.
[제작사 EULA 9절](https://denchisoft.com/wp-content/uploads/2023/04/denchisoft_eula_vts_2023_04_24.pdf)은
Akari를 주력 방송 모델로 사용하는 것을 제한하고 상업 사용에는 별도 서면 허락을 요구한다.
기존 KSS 참조 음성도 로컬 검증용으로 유지한다.

## 재현 자료

Git 제외 `.local/obs-verification/`에 설정·스크립트·보고서·녹화·추출 프레임을 보관했다.
`report.json`, `recording-analysis.json`, `recorded-voice-1.png`, `recorded-app.png`, `recorded-stopped.png`가 이번 실행 증거다.
최종 통합 증거는 `avatar-report-18layers.json`, `final-audio-analysis.json`,
`final-saved-stopped.png`, `final-saved-cleared.png`다. 초기 지연·자막 문제의 보고서와 녹화도 보존했다.
VTS 토큰과 OBS 암호, 모델·음성·영상 파일은 커밋하지 않는다. 원래 `configs/app.yaml`도 유지했다.

현재 로컬 엔진·OBS·VTube Studio를 켠 상태에서는
`.local/obs-verification/start-local-conversation.cmd`로 대화와 자막 갱신을 함께 실행한다.
실제 CLI 시작 진단과 `/quit` 정상 정리도 검증했다. 외부 채팅·송출은 활성화하지 않는다.
RAG 평가에 사용한 합성 자료는 실제 방송 지식으로 자동 등록하지 않는다.
재부팅 후에는 [백엔드 실행 안내](backends.md)를 따르되 동시 리허설에서 `--gpulayers 18`을 사용하고,
VTube Studio 및 `.local/obs-verification/portable-clean/bin/64bit/obs64.exe`도 실행한다.
OBS는 `LocalVerification` 프로필의 `AvatarVerification` 장면을 사용한다.
첫 실시간 입력 전 TTS 예열을 수행한다.

남은 수용 항목은 최종 방송 자산 선정·권한 확인, 실제 시청자 메시지부터 응답까지의 방송 리허설,
장시간 부하 및 주관적 음성/입 싱크 품질 평가다. 로컬 검증의 통과를 공개 방송 무오류 보장으로 확장하지 않는다.

설치·제어 방식은 [OBS 포터블 문서](https://obsproject.com/kb/portable-mode),
[OBS WebSocket 프로토콜](https://github.com/obsproject/obs-websocket/blob/master/docs/generated/protocol.md),
[VTube Studio API 문서](https://github.com/DenchiSoft/VTubeStudio)를 확인했다.
