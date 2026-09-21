"""Load trusted local YAML configuration; reject invalid limits."""

import math
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import yaml


@dataclass(frozen=True)
class Settings:
    backend: str = "fake"
    base_url: str = "http://127.0.0.1:5001"
    model: str = "Qwen3-8B"
    max_output_tokens: int = 256
    context_tokens: int = 8192
    system_prompt_path: str = str(Path(__file__).with_name("system_prompt.md"))
    timeout_seconds: float = 30
    total_timeout_seconds: float = 60
    retries: int = 1
    max_input_chars: int = 1000
    max_output_chars: int = 600
    max_sentences: int = 3
    history_turns: int = 6
    history_chars: int = 6000
    history_ttl_seconds: float = 3600
    max_viewers: int = 100
    queue_size: int = 20
    queue_ttl_seconds: float = 30
    blocked_terms: tuple[str, ...] = ()
    allowed_expressions: tuple[str, ...] = ("neutral", "happy", "sad")

    def __post_init__(self):
        bounds = {
            "max_output_tokens": (1, 4096),
            "context_tokens": (512, 131072),
            "retries": (0, 3),
            "max_input_chars": (1, 4000),
            "max_output_chars": (1, 2000),
            "max_sentences": (1, 3),
            "history_turns": (0, 20),
            "history_chars": (1, 16000),
            "max_viewers": (1, 1000),
            "queue_size": (1, 1000),
        }
        for name, (low, high) in bounds.items():
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{name} must be an integer in [{low}, {high}]")
        for name in (
            "timeout_seconds",
            "total_timeout_seconds",
            "history_ttl_seconds",
            "queue_ttl_seconds",
        ):
            value = getattr(self, name)
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or not 0 < value <= 86400
            ):
                raise ValueError(f"{name} must be positive and at most 86400")
        if self.backend not in ("mock", "fake", "koboldcpp"):
            raise ValueError("backend must be mock, fake or koboldcpp")
        if self.max_output_tokens >= self.context_tokens:
            raise ValueError("context_tokens must exceed max_output_tokens")
        if not isinstance(self.system_prompt_path, str) or not self.system_prompt_path:
            raise ValueError("system_prompt_path must be a nonempty path")
        if not isinstance(self.base_url, str):
            raise ValueError("base_url must be a URL")
        url = urlsplit(self.base_url)
        if (
            url.scheme not in ("http", "https")
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in ("", "/")
        ):
            raise ValueError(
                "base_url must be an HTTP(S) server URL without path, credentials or query"
            )
        try:
            url.port
        except ValueError:
            raise ValueError("base_url has an invalid port") from None
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("model must not be empty")
        for name in ("blocked_terms", "allowed_expressions"):
            values = getattr(self, name)
            if not isinstance(values, (tuple, list)) or any(
                not isinstance(v, str) or not v.strip() for v in values
            ):
                raise ValueError(f"{name} must contain nonempty strings")
            object.__setattr__(self, name, tuple(values))
        if "neutral" not in self.allowed_expressions:
            raise ValueError("allowed_expressions must contain neutral")


def _read_yaml(path: Path):
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        raise ValueError(f"invalid YAML: {path.name}") from None


def load_config(path: Path) -> tuple[Settings, dict]:
    data = _read_yaml(path)
    if not isinstance(data, dict) or set(data) - {
        "llm",
        "limits",
        "output",
        "character_path",
        "system_prompt_path",
    }:
        raise ValueError("invalid app configuration")
    groups = {
        "llm": {
            "backend",
            "base_url",
            "model",
            "max_output_tokens",
            "context_tokens",
            "timeout_seconds",
            "total_timeout_seconds",
            "retries",
        },
        "limits": {
            "max_input_chars",
            "history_turns",
            "history_chars",
            "history_ttl_seconds",
            "max_viewers",
            "queue_size",
            "queue_ttl_seconds",
        },
        "output": {
            "max_output_chars",
            "max_sentences",
            "blocked_terms",
            "allowed_expressions",
        },
    }
    options = {}
    for group, allowed in groups.items():
        values = data.get(group, {})
        if not isinstance(values, dict) or set(values) - allowed:
            raise ValueError(f"invalid {group} configuration")
        options.update(values)
    if "system_prompt_path" in data:
        prompt_path = data["system_prompt_path"]
        if not isinstance(prompt_path, str) or not prompt_path.strip():
            raise ValueError("system_prompt_path must be a nonempty path")
        options["system_prompt_path"] = str((path.parent / prompt_path).resolve())
    settings = Settings(**options)
    name = data.get("character_path", "character.yaml")
    if not isinstance(name, str):
        raise ValueError("character_path must be a string")
    character = _read_yaml(path.parent / name)
    required = (
        "name",
        "personality",
        "worldview",
        "speech_style",
        "address_term",
        "sentence_length",
    )
    if not isinstance(character, dict) or any(
        not isinstance(character.get(k), str) or not character[k].strip()
        for k in required
    ):
        raise ValueError("invalid character configuration")
    for key in ("frequent_expressions", "avoid_expressions"):
        if not isinstance(character.get(key, []), list) or any(
            not isinstance(v, str) for v in character.get(key, [])
        ):
            raise ValueError(f"invalid character {key}")
    examples = character.get("examples", [])
    if not isinstance(examples, list) or any(
        not isinstance(e, dict)
        or any(not isinstance(e.get(k), str) for k in ("user", "assistant"))
        for e in examples
    ):
        raise ValueError("invalid character examples")
    if len(str(character)) > 8000:
        raise ValueError("character configuration is too large")
    return settings, character
