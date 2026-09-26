"""Bounded, local-first retrieval configuration."""

import math
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class RAGSettings:
    enabled: bool = False
    knowledge_enabled: bool = True
    memory_enabled: bool = False
    capture_candidates: bool = False
    db_path: str = ".local/rag.sqlite3"
    scope: str = "local"
    top_k: int = 3
    context_chars: int = 3000
    timeout_seconds: float = 0.5
    retention_days: int = 30
    embedding_url: str = ""
    embedding_model: str = ""
    embedding_timeout_seconds: float = 0.3
    semantic_threshold: float = 0.65

    def __post_init__(self):
        for name in ("db_path", "scope", "embedding_url", "embedding_model"):
            if not isinstance(getattr(self, name), str):
                raise ValueError("rag string required: " + name)
        for name in (
            "enabled",
            "knowledge_enabled",
            "memory_enabled",
            "capture_candidates",
        ):
            if type(getattr(self, name)) is not bool:
                raise ValueError("rag boolean required: " + name)
        for name, low, high in (
            ("top_k", 1, 8),
            ("context_chars", 300, 8000),
            ("retention_days", 1, 30),
        ):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError("invalid rag limit: " + name)
        for name, high in (
            ("timeout_seconds", 5),
            ("embedding_timeout_seconds", 5),
            ("semantic_threshold", 1),
        ):
            value = getattr(self, name)
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or not 0 < value <= high
            ):
                raise ValueError("invalid rag timeout/threshold")
        if not self.db_path or not self.scope or len(self.scope) > 128:
            raise ValueError("rag requires db_path and operator scope")
        if bool(self.embedding_url) != bool(self.embedding_model):
            raise ValueError("embedding URL and model must be configured together")
        if len(self.embedding_model) > 128 or len(self.embedding_url) > 512:
            raise ValueError("embedding configuration too long")
        if self.embedding_url:
            url = urlsplit(self.embedding_url)
            if (
                url.scheme != "http"
                or url.hostname not in ("localhost", "127.0.0.1", "::1")
                or url.username
                or url.password
                or url.query
                or url.fragment
            ):
                raise ValueError(
                    "embeddings require an explicit loopback HTTP endpoint"
                )
