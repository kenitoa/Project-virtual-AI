# LLM·RAG·기억 구현 및 운영 계획

작성일: 2026-09-26. 사용자의 전체 기획 반영 요청에 따라 구현한 선택 기능이다.
기존 v1.0의 실제 음성·방송 수용 조건을 통과했다는 뜻은 아니다.
실제 시청자 데이터, 외부 서비스 설정, 모델 다운로드, 방송 송출은 이번 변경에 포함하지 않는다.

## 1. 제품 목표와 단계 명칭

목표는 캐릭터성을 유지하면서 필요한 근거·기억을 조회하고, 없는 사실을 만들지 않으며,
잘못된 기억을 정정·삭제할 수 있는 AI 방송 제어 프로그램이다.

```text
입력 → 출처/사용자 확인 → 질문 유형 결정(Pre-retrieval)
     → 권한·범위 제한 → 검색(Retrieval)
     → 충돌/중복/예산 선별(Post-retrieval)
     → 컨텍스트 구성 → LLM 생성(Generation)
     → 근거 검증 → 기존 출력 검사 → final/speech → 자막·음성
     → 명시적인 사용자 발언만 기억 후보 추출 → 운영자 승인
```

Post-retrieval은 LLM 이후가 아닌 검색 이후 단계다. 출력 검증과 기억 저장은 별도 책임이다.
LLM API는 기존 `generate(messages)` / `aclose()`를 보존했다.

## 2. 제안 전체의 반영 위치

| 기획 항목 | 구현·운영 위치 | 완료 범위/남은 조건 |
| --- | --- | --- |
| 사용자 경험과 단계 정의 | 이 문서, `rag/retrieval.py` | 인사·기억·지식·현재 상태·자료 부족 분기 |
| 다섯 정보 종류 분리 | character 설정, `rag/store.py`, RecentHistory, `broadcast_state` | 기존 핵심 설정 유지, 지식과 개인 기억 별도 테이블, 세션은 RAM |
| 검색 여부 결정 | `plan()` | 규칙 기반; 이해되지 않는 문장은 확인 요청/자료 부족 처리 |
| 사용자·채널·공개 범위 | `subject_key()`, `consent()`, `snapshot()` | scope+platform+user_id, 저장/조회/공개 권한 구분 |
| 단계적 검색 향상 | `terms()`, `lexical_scores()`, `Embeddings` | 한국어 별칭·일부 붙여쓰기, BM25형 어휘 순위, 선택적 벡터 결합 |
| 검색 후 정리 | `select()` | 중복·명시적 fact_key 충돌·개수·문자 예산 |
| 자료 등록과 버전 | `ingest()`, `split_document()` | 승인·불변 버전·원문 출처·원자적 버전 교체 |
| 프롬프트 구성 | `prompting.py` | 정책과 참고 데이터 분리, 예산 부족 시 근거 답변 중단 |
| 생성 후 검증 | `RAGPipeline.validate()` | 짧은 근거 번호를 원래 출처에 매핑하고 전체 원문 검증; 자유로운 의미 재서술은 미지원 |
| 선택적 기억 저장 | `memory_candidate()`, `capture()` | 사용자 본인의 명시적 취향/호칭만 후보, 자동 확정 없음 |
| 정정·삭제 | `review()`, `delete()`, `forget()` | 대체 관계·유효기간·삭제 원장·연결 자료 제거 |
| 전달 상태 | Application, `delivery()` | 텍스트 표시와 음성 결과 분리; 실제 청취 여부는 알 수 없음 |
| 시간·장애 대응 | ResponseTrace, performance, pipeline | 검색/검증 시간·timeout/fallback·취소 및 원본 변경 재확인 |
| 운영 도구 | `python -m virtual_ai.rag`, 콘솔 명령 | 등록·검색 미리 보기·승인·삭제·진단·기능별 중지 |
| 구현 단계 | 아래 수용 순서 | 코드 통합과 실제 장비 검증은 구별 |
| 반복 평가 | `rag/evaluation.py`, 100개 fixture, `tests/test_rag.py` | 가상 데이터 회귀 평가; 실제 모델/음성 수용은 별도 |

벡터 검색과 재정렬 모델은 기획상 측정 결과에 따른 조건부 단계였다.
벡터 검색 연결 코드는 제공하지만 기본은 비활성이다. 별도 신경망 재정렬기는 실제 평가에서
필요성이 확인되기 전에는 추가하지 않는다. 현재 재정렬은 어휘 점수와 벡터 순위 결합이다.

## 3. 기본 범위와 호환성

- 전체 기능은 `rag.enabled: false`가 기본이다. 비활성일 때 저장소 생성/검색/후보 저장이 없다.
- 기존 `--memory-db`와 그 삭제 원장·마이그레이션은 바꾸지 않는다.
- RAG는 별도 `.local/rag.sqlite3`와 `.revocations.sqlite3`를 사용한다.
- RAG가 켜져 있으면 기존 console 전용 `memory_context`를 중복 주입하지 않는다.
  기존 DB의 기억을 새 시청자 기억으로 자동 이전하지 않는다. 운영자가 출처와 권한을 확인해 등록한다.
- 원문, 기억, 전달 기록, 임베딩은 `.local/` 등 Git 제외 위치에 둔다.
- 기존 치지직 관련 작업과 LLM/TTS/아바타 제어 흐름을 유지한다.
- 캐릭터 이름·기본 말투는 `configs/character.yaml`을 계속 사용한다.
- 상세 설명 요청은 출력 정리에서 최대 8문장·최소 1,200자 한도를 허용한다.
  실제 생성 토큰 한도는 기존 LLM 설정을 따른다. 모든 요청의 최대치는 그대로 제한된다.

## 4. 정보 종류와 우선순위

| 종류 | 작성/갱신 권한 | 사용 시점 |
| --- | --- | --- |
| 핵심 캐릭터 | 운영자 설정 파일 | 매번 기본 프롬프트 |
| 상세 지식/규칙/FAQ | 운영자 승인 문서 | 관련 질문일 때만 검색 |
| 개인 기억 | 허용된 사용자의 원문 + 운영자 승인 | 본인 범위의 기억 질문 |
| 세션 맥락 | 현재 입력과 검사된 답변 | RecentHistory의 턴/시간/사용자 제한 |
| 현재 방송 상태 | 로컬 운영자/프로그램 | `/state game|title` 값 직접 조회 |

시청자 발언의 최신성이 공식 설정의 권한을 덮어쓰지 않는다. 문서 출처별 새 버전 등록은
이전 버전을 superseded로 만든다. 같은 `fact_key`를 가진 서로 다른 활성 출처의 내용이
다르면 충돌을 알린다. 임의 문장 사이의 모든 의미상 충돌을 자동 검출하는 기능은 아니다.

현재 상태 값은 실행 세션의 운영자 입력이다. 게임 프로세스를 자동 감지한다고 주장하지 않는다.
실시간 뉴스나 인터넷 최신 정보를 자동 수집하지 않으며, 연결된 자료가 없음을 설명한다.

## 5. 사용자 식별과 동의

`scope + platform + user_id`를 JSON 배열로 직렬화 후 해시한다. 닉네임은 식별자로 사용하지 않는다.
scope는 채널/운영 단위별로 운영자가 고정한다. 다른 플랫폼·채널의 동명 사용자를 합치지 않는다.
저장 허용, 조회 허용, 공개 발화 허용은 독립된 값이다. 방송 입력에는 셋 모두 필요하다.
콘솔의 명시적 비공개 조회는 public이 없어도 허용할 수 있지만 다른 사용자로 자동 전환하지 않는다.

운영자는 실제 사용자 동의와 공개 가능성을 확인한 다음 권한을 등록해야 한다.
로컬 `consent` 명령은 동의를 받는 UI가 아니라 이미 확인한 동의를 기록하는 도구다.
실제 시청자 대상 셀프서비스 동의 UI와 플랫폼 간 계정 연결은 제공하지 않는다.

## 6. 문서 등록·삭제·색인

현재 입력 형식은 운영자가 확인한 UTF-8 텍스트를 포함한 JSON이다. PDF/웹 자동 수집은 없다.
`source`, `title`, `version`, `content`가 필수이며 `days`, `fact_key`는 선택이다.
source에는 공개 가능한 문서 식별자나 URL을 넣을 수 있지만 URL을 자동 접속하지 않는다.

문단과 제목을 보존한다. 900자를 넘는 단일 의미 블록은 임의로 자르지 않고 등록을 거절한다.
운영자가 조건과 예외를 함께 유지하도록 문단을 정리한다. 제목만 있는 빈 문서는 거절한다.
조각 크기는 문자 기준이며 실제 모델 토큰 개수가 아니다.

검증과 분할이 성공한 뒤 단일 트랜잭션에서 버전을 교체한다. 동일 버전·동일 본문은 멱등적이다.
동일 버전·다른 본문은 거절한다. 과거 버전을 다시 활성화하려면 새 버전을 등록해야 한다.
문서 삭제 시 조각과 벡터는 외래키로 함께 삭제된다. 지식 벡터만 만들며 개인 기억은 임베딩하지 않는다.
재색인 도중 문서/권한이 바뀌면 전체 새 색인 반영을 거절한다.

검색 상한은 지식·기억을 합쳐 2,000개 후보다. 개인 기억은 200개를 넘으면 범위 초과로 처리한다.
더 큰 데이터 규모는 상한을 무작정 올리지 않고 서버 검색 방식과 성능을 평가해야 한다.

## 7. 검색과 컨텍스트

인사·단순 반응은 검색하지 않는다. 명시적 취향 발언은 대화로 처리하고 후보 저장 경로만 사용한다.
개인 기억 질문은 기억만, 지식 질문은 문서만 검색한다. “그거”는 같은 사용자의 최근 질문으로
보완하고 이전 질문이 없으면 확인을 요청한다. 단순 규칙이므로 복잡한 지시어 해석은 보장하지 않는다.

어휘 검색은 별칭(마크/마인크래프트, 호러/공포 등), 일부 조사·붙여쓰기 정규화와 BM25형 점수를 사용한다.
전체 한국어 형태소 분석기나 범용 오타 교정기는 아니다. 지원하지 않는 표현은 평가에서 드러내고 확장한다.
선택적 임베딩 검색은 어휘 결과와 reciprocal rank fusion으로 결합한다.
벡터 유사도 기본 0.65는 모델별 보정 전 가설값이며 확률·정확도 의미가 아니다.
재정렬 결과는 최대 3개, 기본 조각 JSON 3,000자다. 프롬프트 전체는 기존 보수적 UTF-8 예산으로 다시 제한한다.

출처를 포함한 근거를 현재 입력의 reference data로 넣고, 행동 지시는 운영자 system 규칙에 둔다.
검색 자료의 일부 지시문 패턴을 별도로 제외하지만 이 패턴 필터만으로 모든 프롬프트 주입이 차단되지는 않는다.
시청자/자료/모델 출력에는 셸·운영 제어·파일 쓰기 권한을 주지 않는다.

## 8. 생성·검증·발화 계약

근거가 있는 질문에서 LLM은 다음 JSON을 반환한다.

```json
{"source_ids": ["s1"]}
```

LLM은 답에 관련된 턴별 근거 번호만 선택한다. 프로그램은 번호를 원래 출처 ID에 매핑하여 전체 원문을 가져오고,
중복·내용·출력 길이를 검증한다. 조건·부정·예외가 잘리지 않도록 일부 발췌와 임의 재서술은 거절한다.
이 계약은 대화 기능 활성 여부와 무관하다. 기존 quotes 응답도 검증기에서 호환 처리하지만 생성 프롬프트는 source_ids를 요구한다.
이전 원문 복사 계약 비교는 평가 CLI의 `--legacy-quotes`로만 수행한다.
개인 기억에는 “이전에 이렇게 말씀하셨어요”라는 출처 성격을 표시한다.
최종 자막/음성에 JSON이나 출처 ID를 읽지 않는다. `raw`는 원시 JSON, `final`은 검사된 문장,
`speech`는 기존 음성 정리 결과로 유지한다. raw는 DB/일반 로그에 저장하지 않는다.

이 검사는 **원문 충실도**를 보수적으로 검증한다. 문서 자체의 진실성, 질문에 대한 완전성,
선택한 원문의 의미적 적합성, 캐릭터다운 자연스러운 재서술을 보장하지 않는다.
잘못된 답을 그럴듯하게 재생성하지 않고 제한 안내로 종료한다. 자동 재생성은 현재 0회다.
이는 기획의 제한 재시도 상한(최대 1회) 안에서 가장 보수적인 초기 동작이다.

자유로운 말투 재서술을 출시하려면 한국어 근거 일치 평가와 실제 모델 검증이 추가로 필요하다.
일반 잡담은 기존 캐릭터 프롬프트를 그대로 이용한다.

## 9. 기억 후보와 수명

자동 후보 추출은 “나는 … 좋아해/싫어해/선호해”, “나를 … 불러줘”와 같은 제한된 명시적 표현만 받는다.
질문·농담·역할극·타인 언급·명령 형태를 보수적으로 제외한다. 예측한 성격/건강/정체성을 저장하지 않는다.
후보 text와 evidence에는 같은 사용자 원문을 보관한다. assistant 응답을 증거로 만들지 않는다.
중복 원문은 중복 후보를 생성하지 않는다. 후보는 운영자가 승인하기 전 검색되지 않는다.

candidate → active → superseded 또는 삭제/만료 흐름이다. 정정은 새 후보를 승인하면서
`--replaces`에 같은 사용자·유형의 활성 기억을 지정한다. 사용자가 새로운 취향을 말했다고
기존 취향을 모두 자동 삭제하지 않는다. 운영자가 서로 공존하는 취향인지 정정인지 구분한다.
거절한 후보는 내용 삭제 및 삭제 원장 기록을 수행한다.

기억에는 subject, session_id, response_id, kind, text, evidence, created, expires,
state, supersedes를 저장한다. created는 시스템에 들어온 시점이며 과거 사건 발생 시간을 추정하지 않는다.
회고 발언의 실제 사건 시점은 원문으로 보존하며 별도 자동 날짜 추론은 하지 않는다.
기본 보관은 30일, 최대 30일이다. 실제 데이터 운영 전 보관 기간과 사용자 안내를 운영자가 확정한다.

후보 저장은 답변 표시 후 작은 비동기 작업으로 수행한다. 대기 작업은 최대 8개다.
중지·삭제 시 작업을 무효화하고 실행 중 DB 작업을 기다린 후 늦게 들어간 후보도 지운다.
후보 저장 실패는 표시된 답변을 취소하지 않으며 운영자 진단에 남는다.

## 10. 삭제·캐시·재시작·백업

- subject 삭제: 기억 후보/확정 기억/전달 기록 삭제, 같은 subject 재허용 차단.
- document 삭제: 원문/조각/벡터 삭제. memory 삭제: 해당 후보 또는 기억 삭제.
- 활성 자료가 변경되면 저장소 revision이 바뀌며 다음 요청 전 RAM 대화 기록을 비운다.
- 생성 후 자료를 재확인하고, TTS 합성 후에도 재생 전에 다시 확인한다. 변경된 자료의 늦은 출력은 억제한다.
- 사용자 즉시 삭제는 앱의 `/rag-forget`을 사용한다. 이 명령은 일시 정지·현재 출력 중지·RAM 삭제도 수행한다.
- 별도 CLI 삭제는 DB에 적용된다. 다른 프로세스에서 이미 재생 중인 음성을 원격 중지하지는 못한다.
- 벡터는 원문과 같은 DB에 저장하며 별도 검색 캐시·파생 요약을 만들지 않는다.
- 삭제 원장은 내용 없는 해시/자료 ID를 유지해 과거 본체 DB가 복원되어도 삭제된 행을 다시 사용하지 않게 한다.
- 본체 DB와 원장의 identity가 다르거나 원장이 사라지면 읽기/쓰기를 거절한다.

자동 백업/복원 명령은 제공하지 않는다. 오프라인 백업에는 실제 원문이 포함될 수 있다.
앱을 종료한 상태에서 운영자가 별도 보관 정책으로 관리하고, 삭제 요청 시 보관된 사본도 검토해야 한다.
복구 시 **최신 삭제 원장**을 유지해야 한다. 본체와 원장 모두 과거 사본으로 되돌리는 작업은 지원하지 않는다.
SQLite secure_delete는 사용하지만 운영체제/OneDrive/디스크의 과거 사본 완전 삭제를 보장하지 않는다.

## 11. 전달·관측·중지

기존 trace에 retrieval_started/complete, grounding_checked와 검색/검증 소요 시간을 추가했다.
진단에는 응답 ID·근거 ID·제외 이유·상태·컨텍스트 바이트·시간만 노출한다.
`preview`/`inspect`는 운영자가 명시적으로 실행한 비공개 조회이므로 내용을 표시한다.

전달 기록은 저장 동의가 있는 사용자에게만 남긴다. displayed와 playback 결과를 분리하고
completed/failed/cancelled/not_started/unknown을 구분한다. 실제 첫 재생 콜백과 TTS 완료는
기존 ResponseTrace로 측정한다. 재생 완료도 실제 시청자의 청취를 증명하지 않는다.

RAG timeout·자료 부족·충돌·예산 부족·기능 비활성·오류는 각각 다른 짧은 안내를 사용한다.
중지 시 기존 epoch와 응답 ID를 유지하고 늦은 검색 결과를 생성에 넘기지 않는다.
LLM 동시 실행과 음성 동시 재생은 기존 단일 작업 정책을 유지한다.

## 12. 운영 명령

아래 예시는 모두 가상 데이터이며 실제 시청자 ID로 자동 실행하지 않는다.

```powershell
# 지식 등록과 검색 미리 보기
python -m uv run --locked python -m virtual_ai.rag --db .local/rag-demo.sqlite3 ingest --file examples/rag/knowledge.json --approve
python -m uv run --locked python -m virtual_ai.rag --db .local/rag-demo.sqlite3 documents
python -m uv run --locked python -m virtual_ai.rag --db .local/rag-demo.sqlite3 preview --platform console --user local --question "교환 규칙"

# 기존 설정에 RAG 지식 조회만 명시적으로 추가
python -m uv run --locked python -m virtual_ai --backend mock --rag-db .local/rag-demo.sqlite3 --once "교환 규칙"
# mock의 고정 일반 답변은 근거 JSON 계약을 만족하지 않으므로 제한 안내가 정상이다.
# 실제 생성은 검증한 KoboldCpp 설정으로 --backend koboldcpp를 선택한다.

# 별도 가상 사용자에 저장/조회/공개 권한 기록
python -m uv run --locked python -m virtual_ai.rag --db .local/rag-demo.sqlite3 consent --platform youtube --user fixture-user --storage --retrieval --public
python -m uv run --locked python -m virtual_ai.rag --db .local/rag-demo.sqlite3 propose --platform youtube --user fixture-user --file examples/rag/memory-candidate.json
python -m uv run --locked python -m virtual_ai.rag --db .local/rag-demo.sqlite3 inspect --platform youtube --user fixture-user
# 출력된 실제 후보 ID를 아래에 넣는다.
python -m uv run --locked python -m virtual_ai.rag --db .local/rag-demo.sqlite3 review --id ACTUAL_CANDIDATE_ID --approve
python -m uv run --locked python -m virtual_ai.rag --db .local/rag-demo.sqlite3 preview --platform youtube --user fixture-user --memory --question "내가 좋아하는 게임 기억해?"

# 정정 승인 / 거절 / 개별 삭제 / 사용자 전체 삭제
python -m uv run --locked python -m virtual_ai.rag review --id NEW_CANDIDATE_ID --approve --replaces OLD_MEMORY_ID
python -m uv run --locked python -m virtual_ai.rag review --id CANDIDATE_ID --reject
python -m uv run --locked python -m virtual_ai.rag delete --kind document --id DOCUMENT_ID
python -m uv run --locked python -m virtual_ai.rag forget --platform youtube --user fixture-user
```

마지막 네 명령은 기본 DB를 사용한다. 데모 DB에서 실행하려면 앞 예시처럼 `--db`를 반드시 지정한다.
실제 채널은 모든 명령과 앱 설정에 같은 `--scope`/`rag.scope`를 사용한다.
후보 자동 수집은 별도 설정의 `memory_enabled: true`와 `capture_candidates: true`를 모두 켜야 한다.

앱 로컬 콘솔:

```text
/rag-status
/rag-knowledge on
/rag-knowledge off
/rag-memory on
/rag-memory off
/rag-forget youtube ACTUAL_USER_ID
/state game 현재 게임
/state title 현재 방송 제목
/resume
```

기능 전환과 삭제는 앱을 일시 정지하고 기록을 비운다. 운영자가 상태를 확인한 뒤 재개한다.
방송 채팅에 같은 문자열을 입력해도 이 명령을 실행하지 않는다.

## 13. 선택적 의미 검색

모델 서버는 외부 프로세스로 별도 실행한다. 프로그램은 모델을 다운로드하거나 서버를 실행하지 않는다.
설정에는 명시적 loopback HTTP endpoint와 model 이름을 함께 지정한다.
클라이언트는 `/v1/embeddings`의 input 배열/data[index,embedding] 계약을 사용하며
리다이렉트와 환경 프록시를 사용하지 않는다. 응답 크기·차원·유한 숫자·시간을 검사한다.

```yaml
rag:
  enabled: true
  db_path: .local/rag.sqlite3
  scope: my-channel
  embedding_url: http://127.0.0.1:5002
  embedding_model: OPERATOR_VERIFIED_MODEL_NAME
```

```powershell
python -m uv run --locked python -m virtual_ai.rag --scope my-channel reindex --config configs/app.yaml
```

설정 DB와 CLI DB, scope가 정확히 같아야 재색인한다. 모델명이 바뀌면 새 색인을 만든다.
실제 서버의 한국어 임베딩 품질·메모리 사용·지연을 검증하기 전에는 기본 어휘 검색을 사용한다.
서버 실패·색인 없음은 어휘 검색으로 축소하고 진단 상태에 표시한다.

## 14. 평가와 출시 기준

```powershell
python -m uv sync --locked
python -m uv run --locked pytest
python -m uv run --locked ruff check .
python -m uv run --locked ruff format --check .
python -m uv run --locked python -m virtual_ai.rag.evaluation --report .local/rag-evaluation.json
# 명시적으로 실제 로컬 LLM을 추가 검증할 때만:
python -m uv run --locked python -m virtual_ai.rag.evaluation --live-config configs/app.yaml --report .local/rag-evaluation-live.json
```

평가 보고서는 이미 존재하는 파일을 덮어쓰지 않는다. 가상 자료 전용 임시 DB를 사용한다.
100개 구성: 인사 15, 문서 20, 기억 15, 한국어 10, 자료 없음 10, 정정/수명 10,
사용자 분리 10, 잘못된 출력/주입/취소 10. 마지막 20개는 회귀 holdout이며 독립 연구 벤치마크가 아니다.
동일 테스트로 반복 튜닝한 결과를 미지 입력의 성능으로 해석하지 않는다.

검색 성공률 분모는 정답 근거가 존재하는 문서·한국어·기억 45문항이다.
기존 문자열 키워드 기준선은 문서·한국어 30문항에서만 비교하며 이전 앱 전체를 재현한 점수가 아니다.
시간은 각 가상 시나리오 전체의 중앙값/p95이며 LLM·TTS 응답 시간을 뜻하지 않는다.
앱의 별도 performance 측정에는 검색/검증 단계가 추가된다. 실제 방송 데이터 수집과 벤치마크는 섞지 않는다.

초기 목표: 근거 검색 90% 이상, 시험 내 다른 사용자 정보 노출/삭제 정보 재등장/취소 출력 0건.
자동 검사 통과를 실제 모델의 의미적 정확성·캐릭터성·음성 품질 통과로 표현하지 않는다.

출시 전 순서:

1. 가상 데이터 단위/회귀/100개 평가 통과.
2. 실제 KoboldCpp에서 JSON 근거 계약, 관련 자료 선택, 한글 출력, 지연·중단 확인.
3. 선택적 임베딩은 같은 질문으로 어휘 검색 대비 개선/자원 경합 확인 후 활성화.
4. 저장 동의·공개 범위·보관/삭제/백업 운영 정책 확인.
5. 고정 문장과 실제 근거 답변을 직접 청취하고 자막·음성의 조건/예외 누락 확인.
6. VTS/OBS 로컬 녹화·현재 재생 중 삭제/중지·제한된 방송에서 감독하에 확인.

## 15. 검증 기록

추가 대화 기획의 코드 반영과 source_ids 선택/표면 어미 계약은 [대화 확장](dialogue-development.md)에 정리한다.

이번 작업의 실제 실행 결과는 [RAG 검증 기록](rag-validation.md)에 분리한다.
기존 v1.0 실제 장치 수용/릴리스 승인 보류 상태를 변경하지 않는다.

설계 참고: [RAG 단계 분류](https://arxiv.org/abs/2404.10981),
[어휘·의미 검색 결합과 문맥 보존](https://www.anthropic.com/engineering/contextual-retrieval).
외부 글의 성능 수치를 이 프로젝트 성능으로 사용하지 않는다.
