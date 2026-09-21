# Project-virtual-AI 적용 기준

이 절은 사용자와 정한 저장소 역할 및 목표 구조다. 아래의 일반 Local AI 지침과 함께 적용하되, 폴더명·기술 선택·개발 단계는 이 절을 우선한다. 일반 지침의 대규모 구조와 예시는 모든 기능을 처음부터 구현하라는 요구가 아니다.

## 저장소 역할과 실행 흐름

이 저장소는 서비스를 연결하고 언제 무엇을 말할지 제어하는 Python 프로그램을 개발한다. LLM 실행 엔진이나 음성 합성 엔진 자체는 초기 수정 대상이 아니다.

```text
입력: 처음에는 터미널, 이후 방송 채팅, 선택적으로 마이크 → STT
  ↓
Python 제어 프로그램
  ├─ 입력 정리 및 응답 순서 결정
  ├─ 캐릭터 설정 + 대화 기록 → 프롬프트 구성
  ├─ KoboldCpp 호출 → Qwen3-8B 답변 생성
  ├─ 답변 검사 및 음성 출력용 텍스트 정리
  ├─ GPT-SoVITS 호출 → 음성 생성 → 재생
  └─ VTube Studio 표정·동작 제어
  ↓
OBS에서 캐릭터 화면과 재생 음성 캡처
```

- KoboldCpp는 별도 실행하고, `/v1/chat/completions` 등 HTTP API 통신을 `llm/koboldcpp.py`에 모은다.
- GPT-SoVITS는 별도 서버의 `api_v2.py`에 있는 `/tts` 호출로 연결한다. 엔진 코드를 이 저장소에 복사하지 않고 Python 가상환경도 분리한다.
- SillyTavern은 캐릭터 말투와 프롬프트 실험 도구다. 실제 방송에서는 Python이 KoboldCpp를 직접 호출한다.
- 캐릭터 설정의 기준 파일은 `configs/character.yaml`이다. SillyTavern 설정은 자동 반영되지 않으며, 캐릭터 카드 가져오기·내보내기는 후속 기능이다.

## 목표 폴더 구조

아래는 목표 구조이며 현재 구현 완료 상태를 뜻하지 않는다. 음성·방송·장기 기억 모듈은 해당 개발 단계에서 추가한다.

```text
Project-virtual-AI/
├─ README.md                    # 프로젝트 소개와 실행 방법
├─ AGENTS.md                    # AI 코딩 도구 작업 규칙
├─ skill.md                     # 프로젝트 적용 기준과 일반 설계 지침
├─ pyproject.toml               # Python 프로젝트·의존성 설정
├─ uv.lock                      # 의존성 버전 고정
├─ .env.example                 # 환경변수 양식, 실제 비밀값 제외
├─ .gitignore
├─ configs/
│  ├─ app.example.yaml          # 서버 주소, 출력 길이 등 설정 예시
│  └─ character.yaml            # 성격, 말투, 세계관, 대화 예시
├─ src/
│  └─ virtual_ai/
│     ├─ __init__.py
│     ├─ __main__.py            # 실행 진입점
│     ├─ app.py                 # 전체 처리 흐름 연결
│     ├─ config.py              # 설정 로딩과 검증
│     ├─ schemas.py             # 입력·응답 공통 데이터 구조
│     ├─ prompting.py           # 캐릭터·기록 → 메시지 구성
│     ├─ safety.py              # 입력·출력 검사
│     ├─ inputs/
│     │  └─ console.py          # 초기 터미널 입력
│     ├─ llm/
│     │  └─ koboldcpp.py        # LLM API 연결
│     ├─ tts/                   # 후속: 음성 합성 연결
│     ├─ audio/                 # 후속: 재생·중지·출력 장치
│     ├─ integrations/          # 후속: 채팅·VTube Studio·OBS
│     └─ memory/                # 후속: SQLite 저장과 조회
├─ tests/                       # 자동 테스트
├─ docs/
│  ├─ setup.md                  # 개발 환경 설치
│  ├─ architecture.md           # 모듈 역할과 데이터 흐름
│  └─ backends.md               # 외부 프로그램 버전·설정
└─ .github/
   └─ workflows/
      └─ ci.yml                # PR마다 테스트와 코드 검사
```

## 책임 분리와 설정 관리

- `koboldcpp.py`는 요청 전송과 응답 수신만 담당한다. 음성 재생이나 OBS 조작은 수행하지 않는다.
- `prompting.py`는 캐릭터 설정과 대화 기록을 메시지로 변환한다. `app.py`는 입력부터 출력까지 실행 순서를 연결한다.
- 캐릭터 설정은 코드와 분리하고, 성격·세계관·말투 외에 호칭, 문장 길이, 자주 쓰는 표현, 피할 표현, 좋은 답변 예시를 포함한다.
- 기존 일반 지침의 정책 분리, 컨텍스트·출력 예산, 입력·출력 검사, 비밀값 보호 원칙을 해당 모듈에 적용한다. 초기 단계에서 불필요한 Router, RAG, Agent 계층을 만들지 않는다.
- 의존성 관리는 `pyproject.toml`과 `uv.lock`으로 통일한다. 팀원 환경과 CI에서 `uv sync --locked`를 적용해 잠금 파일 변경이 필요한 상태를 오류로 처리한다.
- `.env`, 가상환경, 로컬 인증 토큰, 모델 가중치, 외부 프로그램 전체, 생성 음성, 실제 방송·시청자 기록은 저장소에서 제외하도록 `.gitignore`를 구성한다. 비밀값 없는 예시 파일은 추적한다.
- 외부 프로그램과 모델은 다운로드 출처, 사용 버전, 실행 설정을 `docs/backends.md`에 문서화한다.

---

## 개발 순서와 완료 기준

다음은 합의한 개발 계획이며 구현 완료 상태를 뜻하지 않는다. 우선 개발 범위는 **0단계와 1단계**다. 현재는 이 계획을 기록하며, 음성·캐릭터·방송 플랫폼을 동시에 연결하지 않는다.

| 단계 | 구현할 내용 | 완료 기준 |
| --- | --- | --- |
| 0. 프로젝트 기반 | 설정 로더, 실행 진입점, 가짜 LLM 응답, 테스트, CI | 모델이나 GPU가 없어도 프로그램과 테스트가 실행됨 |
| 1. 텍스트 대화 | 터미널 입력 → KoboldCpp → 답변 출력, 캐릭터 설정, 최근 대화 기록 | 연속 대화가 가능하고 서버 연결 실패를 처리함 |
| 2. 음성 대화 | 답변 정리 → GPT-SoVITS → 음성 재생 | 음성이 겹치지 않고 재생 중지와 합성 실패 처리가 가능함 |
| 3. 캐릭터 출력 | VTube Studio 연결, 음성에 맞춘 입 움직임, 기본 표정, OBS 캡처 | 실제 송출 없이 로컬 녹화로 화면·소리·입 움직임을 확인함 |
| 4. 방송 채팅 | 플랫폼 한 곳 연결, 메시지 선택, 중복 제거, 대기열 관리 | 채팅이 몰려도 무한히 밀리지 않고 오래된 메시지를 처리 정책에 따라 제외함 |
| 5. 확장 | SQLite 장기 기억, STT, 세밀한 감정 표현, 성능 최적화 | 기능을 각각 켜고 끌 수 있고 기존 기능 테스트가 유지됨 |

- TTS는 고정 문장 합성과 재생을 먼저 검증한 뒤 LLM 답변과 연결한다.
- 캐릭터는 화면과 오디오를 수동으로 확인한 뒤 자동 표정 제어를 추가한다.
- VTube Studio는 최초 연결 시 인증·사용자 승인 흐름과 이후 재연결 처리를 구분한다.
- OBS는 먼저 수동으로 장면과 오디오를 설정한다. 자동 제어는 첫 방송의 필수 항목이 아니며, 장면 전환 등의 자동화가 필요할 때 obs-websocket을 추가한다.
- 제공된 계획의 참고 사항: obs-websocket은 OBS Studio 28 이상에 기본 포함되며 비밀번호 보호가 권장된다. 실제 연동 단계에서 사용할 버전과 설정을 확인한다.

---

## 처음부터 적용할 출력·운영·권한 기준

- 모델 원시 응답 → 최종 답변 추출 → 별도 출력 내용 검사 → 음성용 정리 → TTS 순서를 유지한다. 내부 태그·역할 구분·코드 블록의 낭독 방지를 테스트한다.
- Qwen3는 non-thinking을 출발점으로 삼되 실제 KoboldCpp 버전과 템플릿에서 출력을 확인한다. `/no_think`를 강제 보장으로 취급하거나 `enable_thinking=False`를 임의의 API 필드로 보내지 않는다.
- LLM 요청과 음성 재생은 각각 동시 1개, 일반 답변은 1~3문장을 목표로 한다. 대기열 최대 크기·유효시간과 초과·만료 정책을 명시한다.
- 운영자 중지는 현재 재생과 대기 발화를 취소한다. 응답 식별자와 취소 상태를 확인해 늦게 도착한 생성 결과도 재생하지 않는다.
- LLM·TTS 재시도를 제한한다. TTS 실패 시 텍스트는 유지하고 해당 음성만 건너뛴다.
- 최근 대화는 제한된 길이로 유지하고 장기 기억은 5단계 SQLite로 추가한다. 사용자는 플랫폼과 사용자 ID로 구분하고 원문·요약 기억을 분리하며 보관·삭제 정책을 명시한다.
- 일반 채팅과 운영자 권한을 분리한다. 시청자·모델 문자열을 셸·파일 경로·임의의 OBS 작업으로 실행하지 않는다. 표정 제어값은 허용 목록에 매핑한다.
- 현재 구현과 검증 범위 및 제한은 `docs/architecture.md`, 실제 백엔드 검증 항목은 `docs/backends.md`에서 관리한다.

---

## 초기 커밋과 PR 순서 및 공통 검증

- 최초 커밋은 README.md와 .gitignore를 포함한 `chore: initialize repository`로 만들고 main에 게시한다. 기존 체크아웃이 있으면 다시 복제하지 않는다.
- 첫 PR `feat/bootstrap`은 mock 실행, 설정·캐릭터·프롬프트, 교체 가능한 LLM 계약, 로깅·정상 종료, pytest·Ruff·CI·설치 문서를 구성한다. 외부 서버와 GPU 없이 실행 가능해야 한다.
- 첫 PR 병합 후 두 번째 PR은 실제 KoboldCpp 클라이언트·실패 처리, 그다음 세 번째 PR은 캐릭터와 최근 기록을 포함한 연속 대화를 다룬다. 기존 구현은 삭제하지 않고 순차 브랜치로 보존한다.
- 공통 검증: `uv sync --locked`, `uv run --locked pytest`, `uv run --locked ruff check .`, `uv run --locked ruff format --check .`.
- 실제 방송 플랫폼, GPT-SoVITS·VTube Studio 연동, 장기 기억·벡터DB, 모델 학습은 첫 PR에서 제외한다.
- 입력 대기·LLM 생성·TTS 생성·첫 음성 시작까지의 시간을 구분해 측정한다. 실제 음성 시작은 장치 콜백이 있는 경우에만 측정하며 GPU·VRAM 등의 정보 없이 속도를 단정하지 않는다.

---

# Local AI Global Conditions Architecture Guidelines

## 1. 목적

본 문서는 Local AI 시스템을 설계할 때 모델, 추론 엔진, API, Agent, Tool, Memory, RAG 및 사용자 인터페이스 전체에 공통적으로 적용되는 전역 조건(Global Conditions)을 정의한다.

전역 조건의 목적은 다음과 같다.

* 시스템 전체의 일관성 확보
* 모델별 설정 중복 제거
* 정책과 비즈니스 로직 분리
* 모델 교체 가능성 확보
* GPU/CPU/RAM/VRAM 자원의 통제
* 프롬프트 및 컨텍스트 관리 표준화
* 보안 정책 중앙화
* Tool 및 Agent 권한 제어
* 장애 격리 및 복구 가능성 확보
* 런타임 구성 변경 가능성 확보

핵심 원칙은 다음과 같다.

> Model은 시스템의 중심이 아니라 하나의 실행 엔진이다.

애플리케이션 전체의 정책을 특정 LLM 내부에 넣지 않는다.

---

# 2. 전체 아키텍처 원칙

권장 구조:

```text
Client
  │
  ▼
API / Gateway
  │
  ▼
Global Policy Layer
  │
  ├── Security Policy
  ├── Resource Policy
  ├── Prompt Policy
  ├── Context Policy
  ├── Model Routing Policy
  ├── Tool Policy
  ├── Memory Policy
  └── Output Policy
  │
  ▼
AI Orchestrator
  │
  ├── Model Router
  ├── Context Manager
  ├── Prompt Builder
  ├── RAG Manager
  ├── Memory Manager
  ├── Tool Manager
  └── Generation Controller
  │
  ▼
Inference Layer
  │
  ├── llama.cpp
  ├── Ollama
  ├── vLLM
  ├── Transformers
  └── Custom Runtime
  │
  ▼
Local Model
```

Global Policy Layer가 전체 시스템의 최상위 조건을 관리한다.

Inference Runtime 또는 Model이 정책을 결정해서는 안 된다.

---

# 3. 설정 우선순위

모든 설정에는 명확한 우선순위가 있어야 한다.

권장 우선순위:

```text
Hard Safety Policy
        ↓
System Global Config
        ↓
Application Config
        ↓
Model Profile
        ↓
Agent Profile
        ↓
Session Config
        ↓
Request Config
```

예:

```text
Global:
max_context_tokens = 32768

Model:
max_context_tokens = 16384

Request:
max_context_tokens = 50000
```

실제 적용:

```text
16384
```

즉,

```text
effective_value =
min(
    global_limit,
    model_limit,
    request_limit
)
```

사용자 요청이 시스템 제한을 확장할 수 없도록 한다.

---

# 4. Global Config

전역 설정은 중앙 configuration에서 관리한다.

예:

```yaml
system:
  environment: local
  offline_mode: true
  telemetry: false

model:
  default_model: qwen
  fallback_model: llama
  context_limit: 32768

generation:
  temperature: 0.3
  top_p: 0.9
  max_output_tokens: 4096
  repetition_penalty: 1.05

memory:
  enabled: true
  max_session_tokens: 12000

rag:
  enabled: true
  top_k: 5

tools:
  enabled: true
  require_permission: true

security:
  network_access: false
  filesystem_access: restricted
  shell_access: restricted
```

환경별로 분리한다.

```text
config/
├── base.yaml
├── development.yaml
├── production.yaml
├── models/
│   ├── qwen.yaml
│   ├── llama.yaml
│   └── mistral.yaml
└── agents/
    ├── coding.yaml
    ├── research.yaml
    └── general.yaml
```

---

# 5. Model Independence

애플리케이션 코드는 특정 모델 API에 직접 의존하지 않는다.

금지:

```python
ollama.chat(...)
```

비즈니스 로직 내부에서 직접 호출하는 구조.

권장:

```python
model.generate(request)
```

인터페이스:

```python
class ModelProvider:
    def generate(self, request): ...

    def stream(self, request): ...

    def embeddings(self, text): ...

    def health(self): ...
```

구현:

```text
ModelProvider
├── OllamaProvider
├── LlamaCppProvider
├── VLLMProvider
└── TransformersProvider
```

모델이나 inference engine을 변경해도 상위 시스템을 수정하지 않는 것을 목표로 한다.

---

# 6. Model Profile

각 모델의 특성을 별도 Profile로 관리한다.

예:

```yaml
id: qwen-local

model:
  path: ./models/qwen.gguf

runtime:
  backend: llama_cpp

capabilities:
  chat: true
  reasoning: true
  tools: true
  vision: false

limits:
  context_tokens: 32768
  output_tokens: 4096

generation:
  temperature: 0.3
  top_p: 0.9
```

모델마다 다음 정보를 명시한다.

```text
Model Identity
Context Limit
Output Limit
Quantization
VRAM Requirement
RAM Requirement
Tool Calling Support
Structured Output Support
Vision Support
Embedding Support
Chat Template
Stop Tokens
Default Sampling
```

---

# 7. Prompt Architecture

프롬프트를 문자열 하나로 관리하지 않는다.

다음 계층으로 분리한다.

```text
System Policy
      +
Application Policy
      +
Agent Instructions
      +
Runtime Context
      +
Memory
      +
RAG Context
      +
Conversation
      +
User Prompt
```

Prompt Builder가 최종 프롬프트를 조립한다.

예:

```text
PromptBuilder
├── SystemPrompt
├── ApplicationPrompt
├── AgentPrompt
├── ToolPrompt
├── MemoryContext
├── RAGContext
├── ConversationContext
└── UserInput
```

---

# 8. System Prompt 관리

System Prompt를 코드 내부에 직접 하드코딩하지 않는다.

금지:

```python
SYSTEM_PROMPT = """
You are...
"""
```

권장:

```text
prompts/
├── system.md
├── coding.md
├── research.md
├── summarize.md
└── tool-use.md
```

llama.cpp에서도 system prompt 또는 system-prompt-file 형태로 시스템 프롬프트를 지정할 수 있으므로, 시스템 프롬프트를 외부 configuration으로 관리하는 구조와 잘 맞는다.

---

# 9. Context Management

Local AI에서 가장 중요한 시스템 자원 중 하나는 Context Window다.

다음 우선순위를 권장한다.

```text
1. System Policy
2. Safety Policy
3. Current User Request
4. Required Tool Context
5. Relevant RAG
6. Recent Conversation
7. Long-Term Memory
8. Historical Conversation
```

컨텍스트가 초과되면 아래부터 제거한다.

```text
Historical Conversation
↓
Low-score Memory
↓
Low-score RAG
↓
Old Conversation
```

절대 제거하면 안 되는 것:

```text
System Policy
Security Policy
Current Request
Tool Constraints
```

---

# 10. Token Budget

Token Budget을 명시적으로 관리한다.

예:

```text
Context Limit = 32K

System        = 2K
Agent         = 2K
Memory        = 4K
RAG           = 8K
Conversation  = 10K
User Input    = 2K
Output Reserve= 4K
```

항상 출력 토큰을 미리 확보한다.

```text
input_budget =
context_limit
- reserved_output_tokens
```

무제한 conversation history를 모델에 전달하지 않는다.

---

# 11. Generation Policy

Sampling 설정도 전역적으로 관리한다.

Transformers는 `GenerationConfig`를 통해 생성 길이, sampling 및 stop 조건 등을 관리하며, 설정 파일 또는 런타임 인자로 override할 수 있다.

기본값 예:

```yaml
generation:

  default:
    temperature: 0.4
    top_p: 0.9
    max_tokens: 2048

  deterministic:
    temperature: 0.0
    max_tokens: 2048

  creative:
    temperature: 0.8
    top_p: 0.95
```

모든 기능이 임의의 generation 값을 설정하지 않도록 한다.

---

# 12. Model Router

한 모델이 모든 작업을 담당하도록 설계하지 않는다.

권장:

```text
Request
   ↓
Task Classifier
   ↓
Model Router
   ├── Small Model
   ├── General Model
   ├── Reasoning Model
   ├── Coding Model
   ├── Vision Model
   └── Embedding Model
```

예:

```text
Simple Chat
→ 3B / 7B

General Task
→ 8B / 14B

Coding
→ Code-specialized model

Complex Reasoning
→ 30B+

Embedding
→ embedding model
```

---

# 13. Hardware-Aware Routing

Local AI에서는 모델 성능뿐 아니라 시스템 리소스를 정책에 포함해야 한다.

관찰 대상:

```text
GPU Utilization
VRAM Usage
RAM Usage
CPU Usage
Queue Length
Inference Latency
KV Cache Usage
```

예:

```text
VRAM > 90%
→ large model 요청 제한

VRAM > 95%
→ fallback model 사용

OOM 발생
→ context 감소
→ quantized model 전환
```

---

# 14. Quantization Policy

모델 파일마다 임의로 quantization을 선택하지 않는다.

정책 예:

```text
Development:
Q4

Normal Production:
Q5

Quality Priority:
Q6 / Q8

GPU Memory Sufficient:
FP16 / BF16
```

Quantization 정보 역시 Model Profile에 포함한다.

---

# 15. Runtime Isolation

Inference Engine은 별도 프로세스로 분리하는 것을 권장한다.

```text
Application
     │
     ▼
Inference API
     │
     ▼
Model Runtime
```

Application Process 내부에서 직접 모델을 로딩하지 않는 것을 기본값으로 한다.

이렇게 하면:

```text
Model Crash
OOM
CUDA Error
Model Reload
Runtime Update
```

등의 문제가 애플리케이션 전체 장애로 확산되는 것을 줄일 수 있다.

---

# 16. Agent Architecture

Agent가 Model을 직접 제어하지 않는다.

권장:

```text
Agent
  ↓
AI Orchestrator
  ↓
Policy Layer
  ↓
Model Router
  ↓
Inference Engine
```

Agent는 다음만 정의한다.

```text
Role
Goal
Available Tools
Context Requirements
Output Schema
Task Policy
```

---

# 17. Tool Policy

Tool 실행은 LLM 출력과 실제 실행을 분리한다.

금지:

```text
LLM
 ↓
Shell 실행
```

권장:

```text
LLM
 ↓
Tool Request
 ↓
Tool Validator
 ↓
Permission Check
 ↓
Argument Validation
 ↓
Execution Sandbox
 ↓
Result
```

---

# 18. Tool Permission

Tool 권한은 최소 권한 원칙을 사용한다.

예:

```yaml
tools:

  filesystem:
    read: true
    write: project_only

  shell:
    enabled: true
    allow:
      - git
      - npm
      - python

  network:
    enabled: false
```

Agent마다 권한을 별도로 지정한다.

---

# 19. Shell Security

다음은 기본 차단 대상으로 간주한다.

```text
sudo
rm -rf /
disk formatting
raw device access
system shutdown
arbitrary process kill
credential dumping
```

Shell 명령은 가능한 경우 다음 경로를 통과한다.

```text
LLM
↓
Command Parser
↓
Policy Validator
↓
Sandbox
↓
Execution
```

---

# 20. Filesystem Policy

AI에게 전체 filesystem 접근권을 주지 않는다.

권장:

```text
workspace/
├── project/
├── data/
├── output/
└── temp/
```

허용 root:

```text
workspace/
```

차단:

```text
/etc
/root
~/.ssh
~/.config
OS credential stores
browser profiles
```

---

# 21. Network Policy

Local AI의 기본 네트워크 정책은:

```text
deny by default
```

필요한 경우 명시적으로 허용한다.

```yaml
network:

  enabled: true

  allow_domains:
    - github.com
    - huggingface.co

  block_private_network: true
```

---

# 22. Offline First

Local AI의 핵심 장점을 유지하기 위해 가능한 경우:

```text
Model
Embedding
Vector DB
RAG
Memory
Inference
Logging
```

모두 로컬에서 동작하도록 설계한다.

외부 API 사용은 명시적 기능으로 취급한다.

---

# 23. Memory Architecture

Memory와 Conversation History를 구분한다.

```text
Conversation History
=
현재 대화 기록

Memory
=
장기간 유지할 정보
```

권장 구조:

```text
Memory Manager
├── Session Memory
├── User Memory
├── Project Memory
└── Knowledge Memory
```

---

# 24. Memory Injection

모든 Memory를 매번 모델에 넣지 않는다.

```text
User Query
↓
Memory Search
↓
Relevance Ranking
↓
Top-K Memory
↓
Context Injection
```

Memory는 Retrieval 대상으로 관리한다.

---

# 25. RAG Architecture

RAG는 다음 계층으로 분리한다.

```text
Document
↓
Parser
↓
Chunker
↓
Embedding
↓
Vector Store
↓
Retriever
↓
Reranker
↓
Context Builder
↓
LLM
```

LLM이 직접 Vector DB를 조회하지 않게 한다.

---

# 26. Structured Output

프로그램에서 사용하는 응답은 가능하면 자연어 parsing에 의존하지 않는다.

권장:

```json
{
  "status": "success",
  "answer": "...",
  "actions": [],
  "confidence": 0.87
}
```

Schema validation:

```text
LLM Output
↓
JSON Parser
↓
Schema Validator
↓
Application
```

---

# 27. Hallucination Boundary

모델이 다음과 같은 값을 임의 생성해서 시스템 동작에 사용하지 않도록 한다.

```text
File Path
User Permission
API Endpoint
Database ID
Tool Capability
Security Policy
Hardware Capability
```

이 값은 반드시 실제 시스템에서 조회한다.

---

# 28. Error Handling

Model failure와 Application failure를 구분한다.

```text
MODEL_TIMEOUT
MODEL_OOM
INVALID_OUTPUT
CONTEXT_OVERFLOW
TOOL_ERROR
TOOL_DENIED
RAG_ERROR
MODEL_UNAVAILABLE
```

모든 오류를 단일 `"AI Error"`로 처리하지 않는다.

---

# 29. Fallback Architecture

권장 fallback:

```text
Primary Model
   ↓ fail
Secondary Model
   ↓ fail
Reduced Context
   ↓ fail
CPU Model
   ↓ fail
Graceful Error
```

예:

```text
14B GPU
↓
7B GPU
↓
3B GPU
↓
3B CPU
```

---

# 30. Timeout Policy

각 단계별 timeout을 별도로 관리한다.

```text
Inference Timeout
Embedding Timeout
RAG Timeout
Tool Timeout
Agent Timeout
```

하나의 global timeout으로 모든 작업을 관리하지 않는다.

---

# 31. Observability

최소한 다음 정보를 수집한다.

```text
model
runtime
prompt_tokens
output_tokens
context_tokens
generation_time
tokens_per_second
queue_time
VRAM
RAM
tool_calls
RAG latency
error_type
```

단, 개인정보 및 prompt 원문 저장 여부는 별도 정책으로 관리한다.

---

# 32. Privacy

기본 원칙:

```text
Prompt telemetry = OFF
External analytics = OFF
Training upload = OFF
Remote logging = OFF
```

민감 데이터가 포함될 수 있는 시스템에서는 prompt 전체를 로그에 남기지 않는다.

---

# 33. Secrets

다음 정보를 prompt에 삽입하지 않는다.

```text
API Keys
Passwords
Tokens
SSH Keys
Database Credentials
Private Certificates
```

Secret Manager와 AI Context를 분리한다.

---

# 34. Configuration Immutability

Production 환경의 핵심 정책은 요청 단위에서 수정할 수 없어야 한다.

예:

```text
immutable:
  security.network_policy
  security.tool_policy
  model.max_context
  filesystem.allowed_roots
```

---

# 35. Dynamic Configuration

반대로 다음은 runtime 변경을 허용할 수 있다.

```text
temperature
top_p
max_output_tokens
model selection
RAG top_k
memory depth
```

단, Global Limit 범위를 벗어나지 못하도록 한다.

---

# 36. Concurrency

Local Model에서는 동시 요청을 무제한 허용하지 않는다.

```text
Request
↓
Scheduler
↓
Queue
↓
Inference
```

정책:

```text
max_concurrency
max_queue_size
queue_timeout
request_priority
```

vLLM과 같은 추론 서버는 batching 및 비동기 inference 구조를 제공하기 때문에, 모델 서버가 지원하는 scheduling 기능과 애플리케이션 수준 요청 정책을 구분해 설계하는 것이 좋다.

---

# 37. Model Lifecycle

Model lifecycle을 명시적으로 관리한다.

```text
UNLOADED
↓
LOADING
↓
READY
↓
BUSY
↓
IDLE
↓
UNLOADING
```

모델 상태 확인 없이 요청을 직접 전달하지 않는다.

---

# 38. Model Registry

모든 모델을 중앙 Registry에서 관리한다.

예:

```yaml
models:

  qwen:
    provider: llama_cpp
    path: models/qwen.gguf
    context: 32768

  coder:
    provider: vllm
    path: models/coder
    context: 65536
```

Model Router는 Registry를 기반으로 모델을 선택한다.

---

# 39. Hardware Profile

시스템 시작 시 hardware 정보를 탐지한다.

```text
CPU
RAM
GPU
VRAM
CUDA
ROCm
Metal
Disk
```

예:

```yaml
hardware:

  gpu:
    type: RTX
    vram: 24GB

  ram: 64GB

  policy:
    max_vram_usage: 90%
```

---

# 40. Startup Validation

서비스 시작 시 반드시 검증한다.

```text
Config
↓
Hardware
↓
Model Files
↓
Tokenizer
↓
Chat Template
↓
Runtime
↓
Health Test
```

하나라도 실패하면 잘못된 상태로 서비스를 시작하지 않는다.

---

# 41. Health Check

최소 다음 health 상태를 제공한다.

```json
{
  "runtime": "ready",
  "model": "loaded",
  "gpu": "available",
  "memory": "normal"
}
```

API 예:

```text
/health
/health/model
/health/gpu
/health/runtime
```

---

# 42. Project Structure

권장 프로젝트 구조:

```text
local-ai/
│
├── app/
│
│   ├── api/
│   ├── agents/
│   ├── orchestrator/
│   ├── models/
│   ├── tools/
│   ├── rag/
│   ├── memory/
│   └── security/
│
├── core/
│
│   ├── policy/
│   ├── config/
│   ├── routing/
│   ├── context/
│   └── generation/
│
├── runtimes/
│
│   ├── llama_cpp/
│   ├── ollama/
│   ├── vllm/
│   └── transformers/
│
├── configs/
│
│   ├── global.yaml
│   ├── security.yaml
│   ├── hardware.yaml
│   └── models/
│
├── prompts/
│
├── models/
│
├── data/
│
├── logs/
│
└── tests/
```

---

# 43. Dependency Direction

의존성 방향은 반드시 다음 방향을 유지한다.

```text
Application
     ↓
Orchestrator
     ↓
Core
     ↓
Provider Interface
     ↓
Inference Runtime
```

금지:

```text
Core
↓
Ollama
```

Core가 특정 inference engine을 직접 참조하지 않는다.

---

# 44. 핵심 금지사항

다음 구조는 피한다.

### 모델 종속 코드

```text
Business Logic
→ Ollama API
```

### 무제한 Context

```text
Entire Conversation
→ LLM
```

### Tool 직접 실행

```text
LLM
→ Shell
```

### 모든 정책을 System Prompt로 해결

```text
Security
Memory
Permissions
Routing
Resources
↓
System Prompt
```

### Secret Prompt Injection

```text
API_KEY
→ System Prompt
```

### 단일 거대 AI Service

```text
AIService.py
10,000 lines
```

---

# 45. 핵심 설계 철학

Local AI 시스템은 다음 구조로 생각한다.

```text
        Policies
           ↓
        Context
           ↓
        Planner
           ↓
        Router
           ↓
         Model
           ↓
      Validation
           ↓
         Tools
```

LLM은 판단 가능한 컴포넌트이지만 시스템의 절대 권한자가 아니다.

---

# 46. 최종 Global Rules

모든 Local AI 프로젝트에서 다음 원칙을 전역 조건으로 적용한다.

1. Model과 Application을 분리한다.

2. 특정 inference engine에 종속되지 않는다.

3. 모든 AI 요청은 Orchestrator를 통과한다.

4. 모든 설정은 Global Config를 기준으로 한다.

5. 사용자 요청은 Global Limit을 초과할 수 없다.

6. System Prompt는 코드와 분리한다.

7. Context에는 명확한 Token Budget을 설정한다.

8. Conversation 전체를 무조건 전달하지 않는다.

9. Memory는 Retrieval 기반으로 삽입한다.

10. RAG와 Memory를 분리한다.

11. 모델은 직접 Tool을 실행하지 않는다.

12. Tool 호출에는 Permission Validator를 둔다.

13. File System은 Sandbox 기반으로 제한한다.

14. Network Access는 기본 차단한다.

15. Secret은 AI Context에 포함하지 않는다.

16. 모델별 Profile을 관리한다.

17. Model Registry를 중앙 관리한다.

18. Hardware 상태를 기반으로 모델을 선택할 수 있어야 한다.

19. OOM에 대한 fallback 전략을 갖는다.

20. 출력은 가능한 경우 Schema Validation을 거친다.

21. Prompt, Model, Runtime, Tool을 독립적인 계층으로 유지한다.

22. Production 정책은 요청에서 수정할 수 없도록 한다.

23. 오류 유형을 명확하게 구분한다.

24. 모델 교체가 애플리케이션 변경으로 이어지지 않도록 한다.

25. 시스템의 신뢰성은 LLM의 지시 준수 능력에만 의존하지 않는다.

---

# 47. 권장 최종 아키텍처

```text
                  ┌─────────────────────┐
                  │       Client        │
                  └──────────┬──────────┘
                             │
                  ┌──────────▼──────────┐
                  │     API Gateway     │
                  └──────────┬──────────┘
                             │
               ┌─────────────▼─────────────┐
               │    Global Policy Layer    │
               │                           │
               │ Security / Resources      │
               │ Context / Prompt          │
               │ Tool / Memory / Output    │
               └─────────────┬─────────────┘
                             │
                  ┌──────────▼──────────┐
                  │   AI Orchestrator   │
                  └──────────┬──────────┘
                             │
            ┌────────────────┼────────────────┐
            │                │                │
      ┌─────▼─────┐   ┌─────▼─────┐   ┌─────▼─────┐
      │  Context  │   │   Model   │   │   Tool    │
      │  Manager  │   │  Router   │   │  Manager  │
      └─────┬─────┘   └─────┬─────┘   └───────────┘
            │                │
       ┌────▼────┐      ┌────▼─────────┐
       │Memory / │      │ ModelProvider│
       │  RAG    │      └────┬─────────┘
       └─────────┘           │
                    ┌────────┼────────┐
                    │        │        │
                 Ollama   llama.cpp  vLLM
                    │        │        │
                    └────────┼────────┘
                             │
                       Local Models
```

가장 중요한 기준은 다음 한 문장으로 정리할 수 있다.

> **정책은 Global Layer가 결정하고, Orchestrator가 실행 계획을 통제하며, Model은 주어진 범위 안에서 추론만 수행한다.**

이 경계를 유지하면 모델이나 추론 엔진을 교체하고, 단일 모델에서 멀티모델·RAG·Agent 시스템으로 확장하더라도 전체 아키텍처를 다시 설계할 필요가 크게 줄어든다.
