# 운영 보완 검증 — 2026-09-26

## 실제 로컬 연결

새 운영 API와 기존 Qwen3-8B/KoboldCpp, GPT-SoVITS, 스피커, VTube Studio, OBS를 함께 사용했다.
외부 채팅과 공개 송출은 꺼둔 상태에서 합성 입력만 전달했다. RAG 실제 지식은 이번 리허설에 활성화하지 않았다.

`.local/operator-live-check.json`과 `2026-09-26 13-10-50.mkv`에 4개 시나리오를 기록했다.

| 사례 | 관측 결과 |
|---|---|
| LLM 생략 인사 | 음성 completed, 입력 요청부터 처리 완료까지 약 4.46초 |
| 실제 LLM 짧은 대화 | 음성 completed, 처리 완료 약 10.70초 |
| 긴 설명 재생 중 비상 정지 | 음성 cancelled, 운영 HTTP 요청 왕복 약 0.030초 |
| 복구 후 인사 | completed, panic 해제만으로는 pause/mute가 풀리지 않음을 확인 |

4개 요청의 첫 장치 콜백 중앙값 약 4.60초, 최대/p95 약 30.28초다.
긴 설명은 콘솔 입력으로 공개 채팅의 10초 생성/20초 응답 기한이 적용되지 않는 사례였다.
표본이 적고 입력도 달라 이전 8~13초 결과와 동일 조건 속도 비교로 취급하지 않는다.
국소 동작 검증 통과이며 지연 목표 달성·공개 방송 수용·장시간 안정성 통과를 뜻하지 않는다.

기록 중 OBS 송출 false, 음성 중단/복구와 임시 파일 정리 실패 없음 확인.
화면 표시·립싱크 주관 품질과 새 녹화의 직접 청취 확인은 별도다.

## 자동 검사

기존 관련 검사 107개 통과 후 새 제어/통계/권한/OBS 검사 추가.
전체 검사에서 확장한 캐릭터 규칙의 컨텍스트 예산 회귀 2개를 발견했다.
과거 대화 다음에 선택적 캐릭터 예시를 제거하고 현재 맥락/조건/규칙을 보존하도록 수정했다.
해당 실패 2개와 새 검사 묶음 15개는 수정 후 통과했다. 최종 전체 결과는 아래에 추가한다.

## 브라우저 검증 제한

사용 가능한 Chrome 도구에서 `http://127.0.0.1:8768` 열기가 `net::ERR_BLOCKED_BY_CLIENT`로 차단됐다.
브라우저 보호를 우회하지 않았다. 실제 HTTP 서버에서 페이지 응답, 토큰·Host·Origin 검증,
비상 정지/복구, 개인 기억 범위와 권한 확인, OBS WebSocket 인증/매핑 변경 거부는 검사했다.
시각적 레이아웃·브라우저 클릭 수용은 미검증이다.

## 최종 결과

- 전체 `pytest`: **747 passed + 9 subtests**, 105.98초. Ruff check/format 통과.
- 발음 사전으로 출력 차단어를 우회하지 못하도록 합성 직전 재검사를 추가하고 관련 음성/운영 검사 31개를 통과했다.
- JavaScript 문법, PowerShell 구문, 소스 묶음 생성, wheel 빌드와 별도 경로 설치 후 HTML 리소스/모듈 import를 확인했다.
- 이 PC의 PowerShell 실행 정책이 `.ps1` 실행을 막아 기본 `start.cmd`는 `python -m uv run --locked python -m virtual_ai.rehearsal`로 변경했다. 정책은 변경하지 않았다. 새 실행기의 시작 진단·운영 화면·`/quit` 및 `--once` 경로 exit 0.
- 최종 음성 경로 smoke: 예열 후 첫 콜백 약 2.16초, 음성 completed, 정상 종료/세션 보고서 기록 확인.

추가 공개 정책 검사는 **실제 플랫폼 수신이 아닌 가상의 youtube 분류 입력**으로 수행했다.
`.local/operator-public-verified.json`과 `.local/session-reports/operator-public-verified-20260926.json`에 기록했다.

| 입력 | 관측 결과 |
|---|---|
| 인사 | 첫 콜백 약 1.37초, completed |
| 짧은 실제 LLM 대화 | 첫 콜백 약 5.88초, completed |
| 긴 생성 | `llm:deadline_exceeded`, 서버 정리 확인 후 재사용 가능 |
| 오래된 질문 | `discarded:response_expired`, LLM/음성 호출 없음 |

검증 도구의 초기 시도에서는 오래된 메시지가 큐 입구에서 거절되는 동작을 잘못 실패로 판정했고,
기존 사용자가 보관한 WAV까지 없다고 검사했다. 앱에 문제가 있는 것으로 확정하지 않고 검사를
해당 요청/해당 회차 생성 파일에 한정해 재실행했다. 기존 WAV는 보존했다. 최종 시나리오는 통과했다.

두 통합 녹화를 FFmpeg로 전체 디코딩해 오류 없음 확인 후 재인코딩 없이 MP4로 변환했다.

- `.local/obs-verification/recordings/operator-integration-verified.mp4`
- `.local/obs-verification/recordings/operator-public-policy-verified.mp4`
- `.local/operator-artifacts-verified.json`

공개 정책 녹화의 저장 프레임을 직접 열어 실제 모델·입 열림·한글 자막 표시를 확인했다.
별도의 분노 표식도 남아 있어 감정/표정의 주관적 일치가 통과했다고 판정하지 않는다.
앱이 소유하지 않은 표정을 임의로 지우지 않았으며 최종 방송 장면 수용에서 확인해야 한다.
이번 첫 음성 수치는 소수의 다른 입력 표본이다. 전체 지연 목표 달성이나 장시간 무오류를 뜻하지 않는다.
