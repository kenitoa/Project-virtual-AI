# 프로젝트 전반 보완 반영 — 2026-09-26

사용자가 요청한 15개 제안의 코드·운영·평가 반영표다. 목표 성능과 실제 달성 결과를 구분한다.
현재 상태의 진입점은 [프로젝트 상태](project-status.md)다. 과거 날짜별 보고서는 당시 증거로 보존한다.

## 반영 범위

| 제안 | 적용 위치와 동작 | 남은 수용 조건 |
|---|---|---|
| 1. 지연과 예측 가능성 | `operations.py`, `telemetry.py`, `performance.py`: 큐·검색·생성·검증·합성·첫 장치 콜백·정리 통계. 실패/폐기 포함 집계와 성공만의 집계 구분. `--warmup-tts`는 무음 합성. `--responsive` 인사/반응은 LLM 생략 | 지연 목표의 100회 이상 실제 부하 평가. 모델/음성 변경은 동일 조건 비교 후 선택 |
| 2. 말할 양과 채팅 선택 | `Dialogue.prepare`, `/mode`, 운영 화면: talk/busy/focus/quiet. 혼잡 시 후속 질문 억제, busy/focus 감탄 생략, focus 인사 생략. 전환은 현재 응답/큐 중지 | 실제 시청자 수와 콘텐츠별 발화 빈도 수용 |
| 3. 개인/공개 맥락 | `shared_context`: 운영자 `/state topic` 또는 음성 완료한 승인 지식의 제목만 플랫폼별 120초 공유. 개인 기억·사용자 질문 원문은 공유하지 않음. 질문 대상별 60초 응답 기한 | 복잡한 다중 주제·지시어의 사람 평가. 공개 채팅의 자동 사실 승격 없음 |
| 4. 전달 상태 | 콘솔 표시, 자막 파일 쓰기, 음성 pending/completed/cancelled/failed, OBS 갱신 요청 확인을 구별. 취소 시 동일 질문 억제 제거, 후속 맥락에 이전 전달 상태 제공 | 실제 청취·시청 여부는 시스템이 증명하지 않음. 문장별 전달은 스트리밍 전환 시 별도 |
| 5. RAG 완전성 | `rag/coverage.py`: 비용/마감/시작 시간/환불의 명시적 질문 항목 확인. 일부만 확인되면 누락 안내 추가, 전부 누락되면 제한 답변. 원문 조건 보존·최종 출력 예산 재검사 | 규칙 기반 항목 확인이며 일반 의미 적합성·진실성 검증 아님. 자유 요약·벡터/재정렬 확대는 비교 평가 후 |
| 6. 기억 통제 UX | 운영 화면의 본인 확인·동의 확인·저장/조회/공개 개별 권한 기록, 사용자별 명시적 조회·삭제. 기존 승인/대체/삭제 원장 재사용 | 시청자 직접 동의/본인 인증 웹서비스는 별도 필요. 현재 화면은 동의를 확인한 로컬 운영자용 |
| 7. 캐릭터 일관성 | `character.yaml` 상황 규칙, 혼합 감정 대응, 후속 질문 기한/부하 정책. 예산 부족 시 과거 대화 → 캐릭터 예시 순으로 축소해 현재 맥락 우선 | 반어·부정·감정의 자연스러움은 수동 평가. 범용 감정 판별을 주장하지 않음 |
| 8. 음성 품질 | `tts.pronunciations` 운영자 단어 사전. 한 번 치환해 연쇄 변환 방지, 숫자·기호 포함 항목 거절. final은 그대로 두고 speech만 변환 | 실제 닉네임·고유명사 발음, 음질·운율 검수. 스트리밍/사전 합성 캐시는 측정·조건 보존 평가 후 |
| 9. VTS/OBS 운영화 | `.local` 자막 보조 기능을 `integrations/obs.py`로 정식화. 명시한 1~4개 GDI+ 파일 소스만 확인 후 100ms 갱신. 녹화/송출은 관측만. 기존 VTS 모델 ID·표정 소유권 검사 유지 | 최종 방송용 모델·음성 선정, 실제 화면·립싱크 주관 평가 |
| 10. 운영 화면 | `--operator-panel`: 로컬 HTTP 화면, 기존 제어 함수 재사용, 대화 입력·모드·주제·기억·PTT·상태 검사. 표본수·마지막 관측 시각 제공 | 브라우저 도구가 로컬 주소를 차단하여 시각 검증 대기. 실제 HTTP 동작/보안은 자동 검사 |
| 11. 준비·복구 | `start.cmd` → `virtual_ai.rehearsal`: 외부 채팅 차단, 시작 진단·무음 예열·보고서. 선택적 `scripts/start-rehearsal.ps1`은 기존 소유 세션 실행기를 재사용. 앱 잠금과 서버 중단 확인 유지 | 엔진은 별도 실행. 새 PC·재부팅 이후 장치/인증을 포함한 재현 검사 |
| 12. STT | 기존 PTT·음성 출력 정리·재유입 억제 경로 유지, 운영 화면에서 시작/완료/취소. 인식 결과는 ChatInput으로만 전달 | 실제 방의 한국어/배경음/마이크 분리 시험. 무인 상시 청취 확대 안 함 |
| 13. 평가·장시간 | 기존 평가기·반복 수용 도구 재사용, `operator_acceptance.json` 한국어/장치 평가표, `readiness.py` 증거 판독. 벤치마크 4시간 상한, 최소 표본 미달은 미판정 | 실제 30분/2시간/목표 방송 길이 시험, 독립 평가 세트와 사람 점수 |
| 14. 문서·배포 | `project-status.md` 현재 상태, 세션 코드/설정/프롬프트 지문, HTML/실행기 포함 소스 묶음 정책 | 최종 커밋 CI, 다른 PC 설치, 배포물 권한 확인. 자동 릴리스 승인 없음 |
| 15. 단계별 개발 | 로컬 재현 → 반응/전달 품질 → 실제 사용자 수용 → 지속 운영의 순서를 문서화 | 미세조정·다중 동시 플랫폼·자율 조작·무인 방송은 별도 확장 |

## 실행

이미 설치된 로컬 엔진·OBS·VTS를 준비하고 루트 `start.cmd`를 실행한다.
이 PC의 `.local/operator-rehearsal.yaml`이 있으면 검증용 모델·음성·자막 설정을 사용한다.
다른 PC에서는 `configs/app.yaml`과 외부 엔진을 먼저 구성해야 한다. 모델·토큰을 배포하지 않는다.

```powershell
# 외부 장치 없이 운영 화면/제어 확인
python -m uv run --locked python -m virtual_ai --backend mock --local-only --responsive --operator-panel
# 실제 로컬 리허설: URL은 터미널에 출력. 기본 포트는 OS가 할당
python -m uv run --locked python -m virtual_ai --config .local/operator-rehearsal.yaml --backend koboldcpp --local-only --responsive --check-startup --warmup-tts --obs-config .local/obs-operator.json --operator-panel --session-report .local/session-reports/unique-run.json
```

운영 화면 URL은 `http://127.0.0.1:<port>`다. 외부 주소에 바인딩하지 않는다.
앱은 로컬 리허설에서 대화 가능 상태로 시작한다. 실제 방송은 기존 `--live`의 기본 일시 정지를 사용한다.
`--local-only`와 `--live`는 동시에 사용할 수 없다. 리허설 실행기는 공개 송출을 시작하지 않는다.
정상 종료는 터미널 `/quit`. 보고서는 기존 파일을 덮어쓰지 않는다.
기본 실행기는 Python 모듈을 직접 실행하므로 PowerShell 실행 정책을 바꾸지 않는다.
PowerShell 실행기 자체는 해당 정책에서 허용된 환경에서만 사용한다.

운영 화면은 세션 토큰, 엄격한 Host/Origin 확인, 요청 크기·시간·동시 연결 상한,
CSP·프레임 삽입 금지·캐시 금지를 사용한다. 인증 없이 상태/명령 API를 호출할 수 없다.
다른 웹사이트에서 조작할 수 있도록 CORS를 열지 않는다. 같은 PC의 악성 프로세스를 격리하는 보안 경계는 아니다.
입력·응답·시청자 ID는 통계나 세션 보고서에 저장하지 않는다. 기억 조회는 명시적 로컬 화면에만 표시한다.

## 설정과 동작

`--responsive`는 아래 대화 옵션과 `rag.question_coverage`를 켠다. RAG 자체·기억 저장·음성·채팅은 자동 활성화하지 않는다.

```yaml
dialogue:
  enabled: true
  fast_reactions: true
  shared_context: true
  shared_context_ttl_seconds: 120
rag:
  question_coverage: true
tts:
  pronunciations:
    OBS: 오비에스
```

모드는 `/mode talk|busy|focus|quiet`. 운영자 설정 주제는 `/state topic <내용>`이다.
quiet는 공개 입력의 발화를 생략하며 콘솔 입력은 유지한다. 입력 전체 잠금은 `/pause`다.
모드 전환은 큐와 현재 출력을 중지하지만 panic/mute 잠금을 해제하지 않는다.
인사·감탄 직답도 기존 출력 검사와 음성/표정 제어를 거친다. 지식 답변의 조건을 짧게 자르지 않는다.

공유 주제에는 승인 지식의 제목만 사용한다. 원문·개인 기억·시청자 이름을 공유하지 않는다.
검색 자료 revision 변경, 삭제·기억 기능 변경 시 기존 무효화 흐름이 공유 주제도 제거한다.
운영자 주제는 자동 사실 검증 결과가 아니며 직접 확인한 내용만 입력한다.

OBS 설정은 `configs/obs.example.json`을 로컬로 복사한다. `password_env` 또는
`credentials_file` 중 하나를 지정한다. 후자는 OBS가 저장한 JSON의 `server_password`를 읽는다.
비밀번호 자체는 프로젝트 설정 예시에 넣지 않는다. `subtitle_sources`는 실제 고정 자막 파일과
일치하는 `text_gdiplus_v3` 소스만 가능하다. 다른 소스로 변경되면 덮어쓰지 않고 unavailable로 남는다.
OBS 갱신 ACK는 렌더링/청취 증거가 아니다. 실제 녹화 검토를 별도로 유지한다.

## 평가와 완료 기준

첫 음성 목표는 선택 후 인사 p95 2초, 일반 대화 중앙값 4초/p95 7초,
근거 질문 p95 10초를 출발점으로 한다. 일반 세션 판독기는 입력부터 첫 콜백 p95 7초를
보수적인 참고 목표로 비교한다. 대화 유형별 목표 달성은 유형별 별도 고정 부하 보고서로 판정한다.
요청 처리 시간과 실제 소리 중단 시간은 구분한다. 현재 통계는 장치 콜백 관측이며 실제 청취가 아니다.

```powershell
python -m uv run --locked pytest tests/test_operations_readiness.py tests/test_obs_operations.py
python -m uv run --locked python -m virtual_ai.dialogue_evaluation --report .local/dialogue-new.json
python -m uv run --locked python scripts/run_acceptance.py --repeat 2 --output .local/acceptance-new
# 1800/7200초의 위 도구 soak는 모의 회귀 부하다. 실제 음성 방송 시험과 구분한다.
python -m uv run --locked python -m virtual_ai.readiness --report .local/session-reports/unique-run.json --output .local/readiness-new.json --duration-target 1800
```

운영 통계는 최근 256개 표본과 프로세스 누적 사유별 결과를 보관한다. 미관측 콜백은 0초로 치환하지 않는다.
의도적 생략/중단을 포함한 미완료 비율은 오류율과 다르다. p95 표본 100개 미만은 자동 합격시키지 않는다.
프로세스 가동 시간 충족은 연속 음성 부하 통과가 아니다. 사람이 비어 있는 점수 칸을 채우지 않아도 합격으로 바꾸지 않는다.
누출·취소 후 출력·삭제 정보 복원·조건 반전은 평균 점수와 별개로 차단 결함으로 취급한다.

현재는 전체 답변 검증 후 합성한다. 문장 스트리밍은 조건/예외를 함께 검사하는 단위,
취소 시 버퍼 폐기, 문장별 전달 기록을 검증한 뒤 도입한다. 지연 목표에 맞추려고 공개 응답 기한을 늘리지 않았다.
최종 방송 자산이나 실제 시청자 동의를 대신 만들어 넣지 않았다. 설치된 검증용 자산의 공개 방송 권한을 가정하지 않는다.
