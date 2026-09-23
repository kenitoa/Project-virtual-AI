# 선택적 SQLite 장기 기억

2026-09-23 readiness 미커밋 구현. `RecentHistory`는 기존 RAM 대화 기록 그대로다.
새 `SQLiteStore`는 **기본 비활성인 로컬 운영자 도구/API**이며 앱의 채팅·모델 응답을 자동 저장하지 않는다.
14단계에서 명시적 실행 옵션으로 승인된 기억의 프롬프트 조회를 연결했다.
시청자·모델에는 저장·승인·삭제·복구 명령이 연결되지 않는다.

## 대화에서 사용하기 (14단계)

```powershell
python -m uv run --locked python -m virtual_ai --backend mock --memory-db .local/memory.sqlite3 --memory-user fixture-user --memory-session fixture-session
```

DB 옵션을 생략하면 장기 기억을 열지 않는다. `--memory-user`는 운영자가 선택한 로컬 별칭이며
현재 콘솔 사용자에게만 연결한다. 같은 ID를 가진 YouTube 시청자에게도 연결하지 않는다.
`--memory-session`을 생략하면 실행마다 새 세션 ID를 만든다. 여러 사람이 콘솔을 공유할 때는
앱을 종료하고 별도 별칭으로 다시 실행한다. 기존 사용자 RAM 기록을 공유하지 않는다.

- 사용자 범위: 승인한 사실 중 질문의 최대 5개 키워드와 일치하는 항목.
- 세션 범위: 같은 사용자·같은 세션의 출처가 있는 요약. 세션 없는 기존 요약은 대화 조회에서 제외.
- 캐릭터 공통 범위: 기존 신뢰된 `character.yaml`. 개인 기억을 공통 설정으로 승격하지 않는다.

조회 시 만료와 삭제 원장을 적용하고 최대 3개, JSON 전체 1,000자 이내를 현재 사용자 메시지에
참고 데이터로 넣는다. 시스템 지침으로 넣지 않으며 문맥 예산이 부족하면 기억부터 제외한다.
별도 검색 인덱스·조회 캐시는 없다. 단순 키워드 검색이라 한국어 조사·동의어는 놓칠 수 있다.
민감정보 정규식과 참고 데이터 표시는 완전한 의미 검증이나 프롬프트 공격 방어를 보장하지 않는다.
DB 오류는 고정된 사유로 상태에 표시하고 해당 답변은 기억 없이 진행한다.

`/summarize`는 대기열·응답·음성이 없는 때만 실행한다. 명시적으로 저장한 같은 세션의 최근
최대 5개 사용자 입력을 700자 이내로 **원문 발췌**한다. AI 답변을 근거로 사용하지 않고
“아마” 같은 불확실성도 유지한다. 모델/GPU 호출은 없으며 원문 턴 ID를 붙인다.
저장 실패 시 기존 요약을 유지하고, 만료는 가장 빨리 만료되는 근거를 넘지 않는다.
`turn`과 `summary` 저장 JSON의 `session_id`를 실행 옵션과 맞춘다.

### 삭제 요청 처리

`/forget`은 기존처럼 RAM만 삭제한다. `/forget-long`은 먼저 입력을 일시 정지하고 조회를 차단한 뒤
현재 출력·대기열과 전체 RAM을 정리하고, 연결된 로컬 별칭의 DB 원문·사실·요약을 삭제한다.
성공 후에도 `/resume` 전까지 정지하며 현재 실행에서는 해당 기억을 다시 사용하지 않는다.
실패하면 삭제 완료로 취급하지 않고 `memory` 오류를 해결한 뒤 명령을 재시도한다.
실패한 삭제를 해결하기 전에는 기억 옵션을 켜서 재실행하지 않는다.

이용자 요청은 운영자가 방송 설명의 실제 연락 경로로 받아 소유자를 확인한 뒤 처리한다.
YouTube 장기 저장은 계속 금지이며, 해당 채널의 RAM 삭제는 신뢰된 콘솔에서
`/forget-viewer youtube CHANNEL_ID`로 실행한다. 입력을 정지하고 대기열·현재 출력을 정리한 뒤
해당 시청자의 RAM을 지운다. 시청자가 채팅에 같은 문자열을 써도 명령이 실행되지 않는다.
장기 저장한 로컬 별칭은 실행 중 `/forget-long` 또는 앱 종료 후 저장 CLI `forget --user`로 처리한다.
접수·처리 결과만 별도 운영 기록으로 남기고 본문을 복제하지 않는다.

삭제 결과의 `backups_review_required=true`는 **백업 파일까지 지워졌다는 뜻이 아니다**.
운영자는 보유한 이전 백업·복제본·동기화 휴지통을 확인해 해당 원문이 포함된 사본을 폐기하고
처리 완료를 기록한다. 삭제 원장은 유지한다. 관리되지 않는 외부 사본의 자동 삭제는 지원하지 않는다.
공식 [YouTube 사용자 데이터 정책](https://developers.google.com/youtube/terms/developer-policies)의
삭제 요청 처리 의무와 별도 보관 조건을 준수할 실제 운영 연락 경로는 방송 전에 준비해야 한다.

스키마 2는 세션 필드와 인덱스를 추가하며 버전 1 DB를 트랜잭션으로 갱신한다.
18단계부터 v1 DB를 갱신하기 전에 같은 폴더에 `.pre-migration-UUID.sqlite3` 백업을 만들며,
백업 실패 시 마이그레이션을 진행하지 않는다. 앱과 다른 작성자를 먼저 종료해야 한다.
이 자동 백업도 삭제 요청 시 폐기해야 할 원문 사본에 포함한다.
현재 `restore`는 동일 스키마 백업만 받으므로 업그레이드 후 새 백업을 만든다.
오래된 메인 DB를 복원해도 현재 삭제 원장으로 재조회가 차단된다. 원장까지 외부에서
동시에 과거로 되돌리는 복원은 지원 범위가 아니다.

## 저장 정책

- 운영자가 먼저 로컬 검증용 사용자 별칭을 `allow`로 허용해야 한다. 동의 전 저장은 `consent_required`로 거부한다.
- 출처는 `local_operator`만 허용한다. YouTube를 포함한 외부 플랫폼 유래 원문·사실·요약 저장은 이번 구현에서 차단한다.
  운영자가 YouTube 내용을 로컬 정보로 다시 이름 붙여 저장해서도 안 된다.
- 사실과 요약은 운영자의 명시적 `confirmed: true`가 필요하다. 모델이 자동으로 사실을 확정하거나 요약을 만들지 않는다.
- 요약에는 같은 사용자의 저장된 턴 ID가 필요하고, 요약 만료는 근거 턴의 가장 이른 만료를 넘지 않는다.
- 로컬 검증 데이터의 기본 보관은 1일, 명시적으로 지정할 수 있는 최대값은 30일이다. 이는 본 프로젝트의
  로컬 실험 정책이며 YouTube 데이터에 일괄 적용할 수 있는 정책 해석이 아니다.
- 비밀값·개인정보 형태는 기존 출력 안전 검사 함수를 재사용해 거부한다. 정규식 검사가 모든 민감 정보를
  판별하지 못하므로 실제 개인 정보 대신 합성 검증 정보를 사용한다.

YouTube API 데이터와 파생 데이터는 저장·갱신·삭제 조건을 별도로 검토해야 한다.
요약했다는 이유만으로 원래 데이터의 제한에서 벗어났다고 취급하지 않는다.
[공식 Developer Policies](https://developers.google.com/youtube/terms/developer-policies)를 확인한 뒤
향후 별도 승인 범위를 설계해야 하며, 이 단계에는 YouTube 저장을 켜는 옵션이 없다.

`enabled=False` 또는 CLI에서 `--enabled`를 생략하면 DB 파일/폴더를 만들거나 열지 않고 새 정보를 저장하지 않는다.
이미 승인해 저장한 기존 정보를 비활성화만으로 삭제하지는 않는다. 삭제는 별도 `forget` 작업이다.
앱의 기존 `/forget`은 RAM 기록만 지우는 기존 의미를 유지한다.

## 스키마와 전달 상태

| 테이블 | 내용 |
| --- | --- |
| schema_migrations | 버전과 정규화한 SQL SHA-256. 알 수 없는 버전·변경된 체크섬은 거부 |
| store_meta | DB와 삭제 원장의 동일 저장소 식별자 |
| viewers | local 플랫폼, 사용자 별칭 해시, 저장 허용 상태 |
| turns | 승인된 입력·최종 텍스트·표시 여부·재생 상태·생성/만료 시각 |
| memories | 확인된 사실·local_operator 출처·명시적 근거·만료 |
| session_summaries | 운영자가 확인한 요약·소유자가 일치하는 근거 턴 ID·만료 |
| 별도 삭제 원장의 tombstones | 삭제한 사용자 별칭 해시와 삭제 시각, 본문 없음 |

`displayed`와 `playback`은 별개다. 화면에 표시된 뒤 TTS가 실패했다면
`displayed=true`, `playback=failed`로 기록한다. 재생 상태는 `not_started`, `completed`, `failed`,
`cancelled`, `unknown`이다. 최종 전달 상태 변경은 `not_started`에서 한 번만 허용한다.
재시작 후 `not_started`인 항목을 자동으로 completed로 승격하지 않는다.
`completed`도 플레이어 재생 완료 증거를 뜻할 뿐 사람이 실제 들었다는 확인은 아니다.
현재는 검증 API로 명시적으로 기록하며 실제 앱/TTS 이벤트를 자동 수집하지 않는다.

## 로컬 검증 실행

DB와 fixture는 Git 제외 경로를 사용한다. 실제 운영 DB는 공유·동기화 중인 폴더 대신 운영 PC의 로컬 저장 경로를 사용한다.
이 저장소는 암호화 DB가 아니므로 OS 사용자 권한과 보관 경로를 관리한다.

```powershell
python -m uv run --locked python -m virtual_ai.memory --enabled allow --user fixture-user
python -m uv run --locked python -m virtual_ai.memory --enabled store --file .local/fact.json
python -m uv run --locked python -m virtual_ai.memory --enabled lookup --user fixture-user
```

`.local/fact.json` 예시 — 실제 개인정보가 아닌 운영자가 제공한 합성 정보:

```json
{
  "kind": "fact",
  "user_id": "fixture-user",
  "fact": "파란색을 좋아한다",
  "evidence": "운영자가 명시적으로 입력한 로컬 검증 정보",
  "source": "local_operator",
  "confirmed": true,
  "retention": 86400
}
```

`kind: turn`은 `user_id`, `input_text`, `final`, `source`, `displayed`, `playback`, `retention`을 받는다.
`kind: summary`는 `user_id`, `summary`, `turn_ids`, `source`, `confirmed`, `retention`을 받는다.
CLI는 저장한 행의 ID를 반환하며 텍스트를 명령행 인자로 받지 않는다. 조회는 사용자가 명시적으로 요청한
비공개 콘솔 출력이고, 표준 앱 로그에는 저장 본문을 넣지 않는다. 조회 결과는 테이블별 최대 100건으로 제한한다.

```powershell
# 명시적 전달 결과 기록: TURN_ID는 앞서 저장한 로컬 검증 턴 ID
python -m uv run --locked python -m virtual_ai.memory --enabled delivery --user fixture-user --turn-id TURN_ID --displayed --playback failed
python -m uv run --locked python -m virtual_ai.memory --enabled purge
python -m uv run --locked python -m virtual_ai.memory --enabled backup --file .local/memory-backup.sqlite3
python -m uv run --locked python -m virtual_ai.memory --enabled forget --user fixture-user
python -m uv run --locked python -m virtual_ai.memory --enabled restore --file .local/memory-backup.sqlite3
```

다른 경로는 전역 인자 `--db PATH`를 명령 앞에 넣는다. API 사용은 `SQLiteStore(path, enabled=True)`로
명시적으로 켠다. 새 외부 의존성은 없으며 Python 표준 `sqlite3`를 사용한다.

## 삭제·백업·복구

DB가 `memory.sqlite3`이면 삭제 원장은 같은 폴더의 `memory.sqlite3.deletions.sqlite3`이다.
둘을 ATTACH한 짧은 DELETE-journal 트랜잭션에서 삭제 마커 기록과 원문/사실/요약 삭제를 함께 처리한다.
백업은 메인 DB에만 SQLite `Connection.backup()`을 사용하며 기존 대상 파일을 덮어쓰지 않는다.
[Python sqlite3 백업 API](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup)를 사용하고,
실행 중 파일을 단순 복사하지 않는다.

복구는 앱과 다른 저장소 사용 프로세스를 종료한 **오프라인 유지보수 작업**이다. 동일 저장소 ID와
스키마 체크섬을 확인한 뒤 SQLite 백업 API로 복구하고 현재 삭제 원장과 만료를 다시 적용한다.
삭제 원장을 옛 백업으로 교체하지 않는다. 모든 공개 조회/쓰기에서도 삭제 원장을 적용하므로
삭제된 데이터가 예전 메인 DB 백업에 들어 있어도 조회되지 않는다.

- 원장 분실·다른 저장소 원장·알 수 없는 마이그레이션은 실패 처리하며 빈 원장으로 자동 초기화하지 않는다.
- 삭제된 별칭은 재승인하지 못하도록 차단한다. 이 초기 구현은 삭제 후 동일 식별자의 재가입을 지원하지 않는다.
- 해시 삭제 마커는 복구 방지를 위해 유지하며 본문을 담지 않는다. 해시도 완전한 익명화로 취급하지 않는다.
- 원장을 포함한 전체 파일을 외부 도구로 과거 상태로 되돌리는 작업은 이 복구 보장의 범위 밖이다.
- 기존 백업 파일 안의 삭제/만료 본문까지 자동 제거하지 않는다. 운영자는 오래된 백업을 별도로 폐기해야 한다.
- 비활성·미실행 중에는 만료 항목을 디스크에서 자동 정리하지 않는다. 활성 작업 시 또는 `purge`에서 제거한다.
- 백업 실패 시 부분 파일이 남을 수 있다. 실패한 산출물은 사용하지 말고 새 대상 경로로 다시 시도한다.

## 장애와 검증

연결 잠금 대기는 기본 0.2초, 최대 2초로 제한한다. SQL 작업은 진행 핸들러로 예산을 제한하고,
백업은 페이지 단위 진행 콜백으로 최대 2초를 확인한다. OS 파일 I/O 자체의 강제 시간 중단을 보장하는 것은 아니다.
메인 DB와 삭제 원장에 각각 페이지 상한을 적용한다(기본 65536페이지). SQL 값은 파라미터 바인딩하며
테이블명은 내부 고정 목록만 사용한다. 오디오 콜백·방송 앱 실행 경로에서 DB를 호출하지 않는다.

| 오류 사유 | 처리 |
| --- | --- |
| locked | 자동 무한 재시도 없음. 다른 쓰기 작업을 종료한 뒤 수동 재시도 |
| capacity | 트랜잭션 롤백. 저장 공간/페이지 한도 확인, 성공으로 기록하지 않음 |
| corrupt | 손상 파일을 덮어쓰거나 빈 DB로 재생성하지 않음 |
| storage_io | 경로·파일 권한·I/O 오류 확인. 예외에 경로/본문을 포함하지 않음 |
| unsupported_schema | 호환 코드·마이그레이션 검토, 자동 다운그레이드 금지 |
| deletion_ledger_missing_or_orphaned / mismatch | 현재 원장과 DB를 확인. 과거 원장으로 우회 복구하지 않음 |

`test_memory_store.py`와 `test_memory_migrations.py`의 23개 검사로 재시작·비활성·동의·출처 차단·
전달 상태·근거·만료·삭제·옛 백업 복구·SQL 바인딩·실제 SQLite 잠금/페이지 용량 한도·손상·마이그레이션
롤백을 확인했다. OS 디스크 전체를 실제로 채우지는 않았다. 별도 CLI 프로세스들에서 합성 정보의
승인→저장→조회→백업→삭제→복구 후 미조회도 확인했다. 실제 시청자 정보는 사용하지 않았다.
전체 570개와 하위 검사 9개, Ruff lint/format·diff 공백 검사 통과. wheel 빌드와 마이그레이션 SQL 포함도 확인했다.
