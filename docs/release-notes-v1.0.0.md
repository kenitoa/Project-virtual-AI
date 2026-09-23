# v1.0.0 릴리스 노트 초안 — 미승인·미배포

아래는 계획한 지원 범위이며 실제 검증이 끝난 안정 버전의 기능 보증이 아니다.
최종 판정은 [릴리스 승인표](release-approval.md)를 따른다.

## 지원 범위로 구현한 기능

- 한국어 로컬 대화, KoboldCpp 연결, GPT-SoVITS 합성·출력, 검사된 최종 답변과 음성 구분.
- 운영자 정지·일시 정지·재개·음소거·비상 잠금·복구·종료 및 시작 진단.
- 선택적 VTS 인증·허용 표정·립싱크, OBS 파일 자막.
- 읽기 전용 YouTube 인증·수신·재접속·메시지 선택·제한된 과부하 처리.
- 선택적 로컬 SQLite 기억: 저장 허용·사용자/세션 격리·만료·조회·발췌 요약·삭제·백업.
- 선택적 faster-whisper 파일 STT·로컬 Push-to-talk, 별도 엔진 환경.
- 설치·운영·복구 스크립트, 성능 관측과 자동 수용 시험.

## 기본 비활성화 또는 명시적 선택 기능

기본 로컬 실행은 mock이며 방송 수신을 끈다. 실제 음성·VTS·자막·YouTube는 활성 설정과 외부 준비가 필요하다.
SQLite는 명시적 DB 옵션과 저장 허용, STT는 별도 환경·모델·마이크 옵션이 필요하다.
live 실행은 일시 정지로 시작한다. 기억·STT는 선택 기능이어도 이번 완성 범위의 실제 검증 대상이다.

## 미지원 기능

벡터DB/RAG 확장, 모델 미세조정, 다중 방송 플랫폼, 게임 자동 조작, 무인 24시간 방송,
상시 마이크·자동 끼어들기, 단어별 자막 동기화, 검사 전 토큰 발화, 자동 DB downgrade,
YouTube 채팅의 장기 기억 저장은 지원하지 않는다. GUI는 필수 배포 구성에 포함하지 않는다.

## 알려진 제한과 검증 대기

- 실제 방송 회차 0건. 실제 음성·VTS·OBS·마이크·YouTube 수신과 다른 개발자 설치 재현이 대기다.
- 확장한 Windows/Ubuntu × Python 3.11~3.14 CI는 최종 커밋의 실행 증거가 필요하다.
  STT는 별도 Python 환경이며 제어 프로그램과 같은 호환 범위라고 가정하지 않는다.
- 정규식·정책 검사는 완전한 의미 기반 안전 검사가 아니다. 모든 채팅에 응답하지 않는다.
- 네트워크 exactly-once, 시각적 립싱크 지연 보장, 장시간 안정성을 보장하지 않는다.
- 서버 취소가 확인되지 않으면 생성 재사용을 차단한다. VTS 연결 불확실성은 수동 복구가 필요하다.
- 강제 종료는 정상 정리를 보장하지 않는다. 백업 사본의 물리적 폐기는 운영자가 별도로 처리한다.
- 성능 수치는 mock 기준선이며 실제 방송 처리량이 아니다. STT 모델은 발화마다 다시 로딩한다.

## 배포물 구성과 제외

승인 후 **최종 커밋에서 검토한 파일 목록으로** 소스 배포물을 만든다. 작업 폴더 전체를 ZIP으로 압축하지 않는다.

포함: `src/`, `scripts/`, 합성 `tests/`, `configs/app.example.yaml`, `configs/character.yaml`,
`configs/performance-questions.json`, `docs/`, README·CHANGELOG, pyproject.toml·uv.lock,
STT 엔진의 pyproject.toml·uv.lock, 사용한 프로토콜·의존성의 필요한 라이선스/출처 안내.

기본 제외: configs/app.yaml, .env, 토큰·OS 자격 증명, 실제 시청자 기록, 기억 DB·삭제 원장·백업,
참조 음성·모델 가중치·실제 녹화, 생성 WAV·자막·로그, .local, 가상환경, Git 메타데이터.
`.gitignore`만 믿지 말고 아카이브 파일 목록과 비밀정보·자산 권한을 별도 검사한다.
실제 자산은 사용·재배포 권한을 확인한 경우에만 별도 범위로 판단하며 기본 배포물에는 넣지 않는다.

최종 산출물의 SHA-256, 소스 SHA, 의존성 잠금 해시와 새 PC 설치 결과를 승인표에 연결한다.
현재는 v1.0.0 배포물을 생성하거나 게시하지 않았다.

설치는 [setup](setup.md), 실행은 [운영 안내](operator-guide.md), 업데이트/복구는 [recovery](recovery.md),
자산은 [assets](assets.md)·[backends](backends.md), 데이터 정책은 [보관 문서](privacy-and-retention.md)를 따른다.
## 소스 배포 후보 생성 도구 (미승인)

`python -m uv run --locked python scripts/build_source_bundle.py --output .local/virtual-ai-source-candidate.zip`

현재 작업 사본의 허용된 소스·설정 예시·문서·잠금 파일을 ZIP으로 생성한다. `SOURCE-MANIFEST.json`에 기준 커밋, 미커밋 변경 여부, 파일별 SHA-256, `release_approved: false`를 기록한다. 기존 출력은 덮어쓰지 않는다. 이 결과물은 로컬 검토용 후보이며 v1.0.0 태그나 배포 승인이 아니다. 실제 자산과 런타임 데이터는 별도로 유지한다.
