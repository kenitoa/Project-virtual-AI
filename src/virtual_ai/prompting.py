"""Compose policy, character, bounded history and current input."""

import json
from pathlib import Path

from virtual_ai.llm.base import LLMError
from virtual_ai.safety import contains_secret


def load_system_prompt(path=None):
    text = Path(path or Path(__file__).with_name("system_prompt.md")).read_text(
        encoding="utf-8"
    )
    if not text.strip() or len(text) > 8000 or contains_secret(text):
        raise ValueError("invalid system prompt: empty, too large or contains a secret")
    return text.strip()


def estimate_tokens(messages):
    """UTF-8 byte estimate with template reserve, not an exact tokenizer count."""
    return 256 + sum(len(m["content"].encode("utf-8")) + 32 for m in messages)


def build_messages(character, history, text, *, system_prompt=None, settings=None):
    character_text = json.dumps(character, ensure_ascii=False)
    if contains_secret(character_text.replace('\\"', '"')) or contains_secret(text):
        raise LLMError("입력 또는 캐릭터 설정에 비밀값 형식이 포함되어 있습니다.")
    system = system_prompt if system_prompt is not None else load_system_prompt()
    system += "\n" + character_text
    messages = [
        {"role": "system", "content": system},
        *[dict(m) for m in history],
        {"role": "user", "content": text},
    ]
    if settings is not None:
        budget = settings.context_tokens - settings.max_output_tokens
        while len(messages) > 2 and estimate_tokens(messages) > budget:
            del messages[1:3]
        if estimate_tokens(messages) > budget:
            raise LLMError(
                "컨텍스트 예산을 초과했습니다. 입력이나 캐릭터 설정을 줄이세요."
            )
    return messages
