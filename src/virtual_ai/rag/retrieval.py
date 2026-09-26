"""Deterministic routing, Korean aliases, lexical/optional vector rank fusion."""

import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field

import httpx

from virtual_ai.memory.base import StoreError
from virtual_ai.memory.sqlite_store import _text
from virtual_ai.rag.store import validate_vector

ALIASES = {
    "마인크래프트": ("마인크래프트", "마크"),
    "공포": ("공포", "무서운", "무서워", "호러"),
    "선호": ("좋아하는", "좋아한다고", "좋아해", "좋아하", "선호", "취향"),
    "불호": ("싫어하는", "싫어한다고", "싫어해", "싫어하"),
    "일정": ("일정", "스케줄", "언제", "몇시", "몇 시"),
    "호칭": ("불러줘", "불러 줘", "호칭", "닉네임"),
}
STOP = {
    "내",
    "내가",
    "나는",
    "저는",
    "제가",
    "나",
    "너",
    "뭐",
    "뭐야",
    "뭐였지",
    "알려줘",
    "알려",
    "설명",
    "해줘",
    "기억",
    "지난번",
    "이번",
    "그거",
    "그것",
    "the",
    "is",
    "what",
    "my",
    "a",
    "an",
    "do",
    "you",
}


def reference_instruction(text):
    return bool(
        re.search(
            r"ignore\s+(?:all\s+)?(?:previous|prior|system)|system\s*prompt|"
            r"(?:이전|기존|시스템).{0,15}(?:지시|명령).{0,15}무시|"
            r"<\|(?:system|assistant)\|>|(?:api[_ -]?key|비밀키).{0,20}(?:출력|공개)",
            text,
            re.IGNORECASE,
        )
    )


def terms(text):
    text = text.lower()
    for canonical, forms in ALIASES.items():
        for form in sorted(forms, key=len, reverse=True):
            text = text.replace(form, canonical + " ")
    text = re.sub(r"([가-힣]{2,})(게임|규칙|설명)", r"\1 \2", text)
    words = re.findall(r"[a-z0-9가-힣]+", text)
    result = []
    for word in words:
        if word in STOP:
            continue
        stripped = re.sub(
            r"(에서는|으로|에서|하고|에는|은|는|을|를|이|가|도|의)$", "", word
        )
        word = stripped if len(stripped) >= 2 else word
        if word not in STOP and len(word) >= 2:
            result.append(word)
    return list(dict.fromkeys(result))[:24]


@dataclass(frozen=True)
class Plan:
    kind: str
    query: str
    detailed: bool = False


def plan(question, history=()):
    _text(question, 4000)
    if memory_candidate(question):
        return Plan("chat", question)
    compact = re.sub(r"[\s!?.,~ㅋㅎ]", "", question.lower())
    if compact in (
        "안녕",
        "안녕하세요",
        "하이",
        "반가워",
        "고마워",
        "감사합니다",
        "hello",
        "hi",
        "",
        "웃겼다",
    ):
        return Plan("chat", question)
    if any(w in question for w in ("지금 무슨", "현재 게임", "방송 제목", "현재 상태")):
        return Plan("state", question)
    if any(w in question for w in ("방금 발표", "최신 뉴스", "실시간 뉴스")):
        return Plan("unavailable", question)
    detailed = any(w in question for w in ("자세히", "상세", "길게"))
    if any(w in question for w in ("그거", "그것", "아까 그것")):
        prior = [m["content"] for m in history if m["role"] == "user"]
        if not prior:
            return Plan("ambiguous", question, detailed)
        question = prior[-1][:500] + " " + question
    kind = (
        "memory"
        if (
            any(
                w in question
                for w in (
                    "내가",
                    "나는",
                    "내 ",
                    "내취향",
                    "내가좋",
                    "제가",
                    "나를",
                    "내취",
                    "my ",
                )
            )
            and any(
                w in question
                for w in (
                    "기억",
                    "좋아",
                    "선호",
                    "취향",
                    "호칭",
                    "싫어",
                    "불러",
                    "remember",
                    "like",
                )
            )
        )
        else "knowledge"
    )
    return Plan(kind, question, detailed)


@dataclass
class Evidence:
    plan: Plan
    revision: int = 0
    rows: list = field(default_factory=list)
    status: str = "none"
    excluded: dict = field(default_factory=dict)
    semantic: str = "disabled"

    def context(self, *, compact_ids=False):
        return json.dumps(
            {
                "kind": self.plan.kind,
                "status": self.status,
                "sources": [
                    {
                        key: f"s{index}" if key == "id" and compact_ids else row[key]
                        for key in ("id", "kind", "title", "text", "source", "version")
                        if key in row
                    }
                    for index, row in enumerate(self.rows, 1)
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def diagnostic(self):
        return {
            "route": self.plan.kind,
            "status": self.status,
            "source_ids": [r["id"] for r in self.rows],
            "excluded": self.excluded,
            "semantic": self.semantic,
            "context_bytes": len(self.context().encode()),
        }


def lexical_scores(question, rows):
    query = terms(question)
    documents = [terms(r["title"] + " " + r["text"]) for r in rows]
    if not query or not documents:
        return {}
    lengths = [len(d) for d in documents]
    average = sum(lengths) / len(lengths) or 1
    result = {}
    for row, words, length in zip(rows, documents, lengths):
        counts = Counter(words)
        score = 0
        for term in query:
            # Korean compact chat: bounded substring fallback after normalization.
            tf = sum(
                v
                for k, v in counts.items()
                if term == k or (len(term) >= 2 and term in k)
            )
            df = sum(
                any(term == k or (len(term) >= 2 and term in k) for k in d)
                for d in documents
            )
            if tf:
                idf = math.log(1 + (len(rows) - df + 0.5) / (df + 0.5))
                score += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * length / average))
        if score > 0:
            result[row["id"]] = score
    return result


def select(evidence, rows, lexical, semantic, settings):
    rank = {}
    for scores in (lexical, semantic):
        for position, identifier in enumerate(
            sorted(scores, key=lambda k: (-scores[k], k))
        ):
            rank[identifier] = rank.get(identifier, 0) + 1 / (61 + position)
    ranked = sorted(
        (r for r in rows if r["id"] in rank), key=lambda r: (-rank[r["id"]], r["id"])
    )
    # Explicit fact slots only; multiple personal preferences are not automatically conflicts.
    keys = {}
    for row in ranked:
        if row["kind"] == "knowledge" and row["fact_key"]:
            keys.setdefault(row["fact_key"], {}).setdefault(
                row["source_id"], set()
            ).add(row["text"])
    if any(
        len({tuple(sorted(texts)) for texts in sources.values()}) > 1
        for sources in keys.values()
    ):
        evidence.status = "conflict"
        evidence.excluded["conflict"] = len(ranked)
        return evidence
    seen = set()
    for row in ranked:
        if row["text"] in seen:
            evidence.excluded["duplicate"] = evidence.excluded.get("duplicate", 0) + 1
            continue
        seen.add(row["text"])
        proposed = evidence.rows + [row]
        if (
            len(proposed) > settings.top_k
            or len(json.dumps(proposed, ensure_ascii=False)) > settings.context_chars
        ):
            evidence.excluded["budget"] = evidence.excluded.get("budget", 0) + 1
            continue
        evidence.rows = proposed
    evidence.status = "available" if evidence.rows else "none"
    evidence.excluded["irrelevant"] = len(rows) - len(ranked)
    return evidence


class Embeddings:
    def __init__(self, settings):
        self.settings = settings

    async def encode(self, texts):
        if not self.settings.embedding_url:
            raise StoreError("embedding_disabled")
        if not 1 <= len(texts) <= 32:
            raise StoreError("embedding_batch_limit")
        for text in texts:
            _text(text, 4000)
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.embedding_timeout_seconds,
                trust_env=False,
                follow_redirects=False,
            ) as client:
                async with client.stream(
                    "POST",
                    self.settings.embedding_url.rstrip("/") + "/v1/embeddings",
                    json={"model": self.settings.embedding_model, "input": texts},
                ) as response:
                    response.raise_for_status()
                    body = bytearray()
                    async for part in response.aiter_bytes():
                        body.extend(part)
                        if len(body) > 4_000_000:
                            raise StoreError("embedding_response_limit")
            data = json.loads(body)["data"]
            if not isinstance(data, list) or len(data) != len(texts):
                raise StoreError("embedding_count")
            by_index = {row["index"]: row["embedding"] for row in data}
            if set(by_index) != set(range(len(texts))):
                raise StoreError("embedding_indices")
            vectors = [by_index[i] for i in range(len(texts))]
            for vector in vectors:
                validate_vector(vector)
            if len({len(v) for v in vectors}) != 1:
                raise StoreError("embedding_dimensions")
            return vectors
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise StoreError("embedding_unavailable") from None


def cosine(a, b):
    validate_vector(a)
    validate_vector(b)
    if len(a) != len(b):
        raise StoreError("embedding_dimensions")
    return sum(x * y for x, y in zip(a, b)) / (
        math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    )


def memory_candidate(text):
    """Only explicit first-person preferences/names; preserve the actual quotation."""
    if len(text) > 500 or any(
        x in text
        for x in (
            "?",
            "？",
            "농담",
            "역할극",
            "가정",
            "명령",
            "무시",
            "시스템",
            "/",
            "친구",
            "라면",
        )
    ):
        return None
    if re.search(
        r"^(나는|저는|난|전|내가)\s*.+(좋아해|좋아합니다|싫어해|선호해|선호합니다)",
        text,
    ):
        return "preference", text
    if re.fullmatch(
        r"(나를|저를)\s+[가-힣a-zA-Z ]{1,30}(으?로)?\s*불러\s*줘[.!]?", text
    ):
        return "name", text
    return None
