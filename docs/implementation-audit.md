# 단계별 실제 구현 점검 (2026-09-24)

현재 IDE 폴더는 `feat/youtube-chat`의 `04564f1` 위 작업 사본이다. 후속 구현은 같은 커밋에서 시작한 `Project-virtual-AI-readiness`에만 있었으므로 변경 파일 117개를 현재 폴더에 통합했다. 이는 main 병합이나 릴리스 확정이 아니다. 기존 사용자 문서·skill.md를 보존하고 덮어쓴 원본은 `.local/before-readiness-integration/`에 보관했다.

| 사용자 단계 | 실행 가능한 구현 | 남은 외부 증거 |
| --- | --- | --- |
| 1~2 기준·자산 | 잠금 의존성, 설정 검증, 진단 CLI | 자산 권한·동일 환경의 다른 개발자 실행 |
| 3 음성 | llm/koboldcpp.py, tts/gpt_sovits.py, audio/player.py | 직접 청취·중지·복구 |
| 4~5 VTS·OBS | integrations/vts, 립싱크·자막 출력 | 실제 모델·녹화 |
| 6 브랜치 통합 | 현재 작업 폴더에 후속 코드 통합 | 검증된 PR의 main 통합·지원 환경 CI |
| 7~9 운영·진단·캐릭터 | runtime_state.py, health.py, diagnostics, expressions.py, safety.py | 실제 운영·발음 평가 |
| 10~12 YouTube | youtube_auth.py, youtube_stream.py, inputs/selection.py | 계정 승인·테스트 방송 수신 |
| 13~14 기억 | memory 저장소·마이그레이션·조회·요약·삭제 | 운영 환경 백업·복구 수용 시험 |
| 15 마이크 | stt, inputs/microphone.py, engines/stt 격리 환경 | 권한 있는 WAV·모델·실제 마이크 |
| 16 성능 | performance.py 및 새 resource_monitor.py | 목표 장비의 전체 방송 부하·장시간 측정 |
| 17 통합 시험 | tests/integration, scripts/run_acceptance.py | 실제 장치·30분/2시간 운영 시험 |
| 18 운영 편의 | bootstrap/check-environment/start-local/start-live/stop-owned-processes.ps1 | 다른 개발자의 새 환경 실행 |
| 19 실방송 | 기존 전체 경로·운영 제어와 수용 절차 | 운영자 감독하의 반복 방송·녹화 검토 |
| 20 배포 | 새 scripts/build_source_bundle.py | 모든 게이트 통과 후 v1.0.0 승인 |

이번에 추가한 자원 측정기는 지정 PID의 CPU·RSS·스레드·핸들/FD·TCP 연결 수, 선택적 NVIDIA GPU·VRAM, 선택적 OBS GetStats를 실제 수집한다. ZIP 생성기는 포함 경로를 제한하고 파일별 SHA-256 및 압축 무결성을 검사한다. 토큰·로컬 설정·DB·음성·모델·녹화는 포함 경로에서 제외한다. 소스에 직접 적힌 비밀값까지 의미적으로 탐지하는 도구는 아니므로 배포 전 내용 검토가 필요하다.

실제 청취, 자산 준비, OAuth 승인, 모델 매핑, 테스트 방송, 다른 개발자 설치는 문서에만 남은 코드 구현이 아니라 외부 수용 조건이다. 미준비라는 기존 답변을 유지하며 자동 테스트로 완료 처리하지 않는다.

## 현재 폴더 검증 결과

- Windows / Python 3.14.7 작업 사본에서 `uv sync --locked` 성공.
- 전체 pytest: 615 passed, 9 subtests passed (58.86초). 번들 라이선스 포함 보강 후 관련 12개 테스트 재통과.
- Ruff check와 format check, mock 단일 응답 통과.
- `.local/resource-with-obs-check.json`: 실제 측정기 프로세스 및 NVIDIA GPU 수집 성공. OBS 미실행으로 unavailable, 통계 표본 0건. 실제 OBS 성능 검증 아님.
- `.local/virtual-ai-source-candidate.zip`: 생성 도구가 SHA-256 목록과 압축 무결성을 검사하는 로컬 후보. 미커밋 변경을 포함하며 릴리스 승인 아님.
- 이 결과는 현재 작업 사본 검증이다. main CI·다른 OS/지원 버전·새 개발자 설치 결과로 확대 해석하지 않는다.
