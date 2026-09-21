import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from virtual_ai.app import Application
from virtual_ai.config import Settings, load_config
from virtual_ai.llm.base import LLMError
from virtual_ai.memory.recent import RecentHistory
from virtual_ai.prompting import build_messages, estimate_tokens, load_system_prompt
from virtual_ai.schemas import ChatInput, Viewer

ROOT = Path(__file__).resolve().parents[1]
A = Viewer("a", "1")
B = Viewer("b", "1")


def test_history_bounds_isolation_expiry_and_delete():
    now = [0]
    history = RecentHistory(
        Settings(
            history_turns=2, history_chars=20, history_ttl_seconds=10, max_viewers=2
        ),
        clock=lambda: now[0],
    )
    history.add(A, "first", "one")
    history.add(A, "next", "two")
    history.add(A, "last", "three")
    assert [m["content"] for m in history.messages(A)] == [
        "next",
        "two",
        "last",
        "three",
    ]
    history.add(B, "other", "answer")
    copy = history.messages(A)
    copy[0]["content"] = "changed"
    assert history.messages(A)[0]["content"] == "next"
    history.delete(A)
    assert history.messages(A) == []
    assert history.messages(B)
    now[0] = 10
    assert history.messages(B) == []
    history.add(A, "a", "b")
    history.add(B, "c", "d")
    history.add(Viewer("c", "1"), "e", "f")
    assert history.messages(A) == []
    history.delete()
    assert not history._viewers


def test_history_zero_turns_and_oversize_pair_are_not_retained():
    for settings in (Settings(history_turns=0), Settings(history_chars=1)):
        history = RecentHistory(settings)
        history.add(A, "hello", "answer")
        assert not history.messages(A)
        assert not history._viewers


def test_policy_lists_are_immutable_copies():
    terms = ["blocked"]
    settings = Settings(blocked_terms=terms)
    terms.clear()
    assert settings.blocked_terms == ("blocked",)


def test_budget_reserves_output_and_keeps_policy_and_current_request():
    history = [{"role": role, "content": "old" * 100} for role in ("user", "assistant")]
    settings = Settings(context_tokens=1024, max_output_tokens=256)
    messages = build_messages(
        {}, history, "current", system_prompt="policy", settings=settings
    )
    assert [m["role"] for m in messages] == ["system", "user"]
    assert messages[0]["content"].startswith("policy")
    assert messages[-1]["content"] == "current"
    assert (
        estimate_tokens(messages) + settings.max_output_tokens
        <= settings.context_tokens
    )
    with pytest.raises(LLMError, match="컨텍스트"):
        build_messages({}, [], "가" * 300, system_prompt="policy", settings=settings)


@pytest.mark.parametrize(
    "secret",
    [
        "password=private",
        "token: private",
        "sk-12345678901234567890",
        "-----BEGIN OPENSSH PRIVATE KEY-----",
        "Bearer abcdefghijklmnop",
        'api_key": "private',
    ],
)
def test_secret_never_enters_prompt(secret):
    with pytest.raises(LLMError):
        build_messages({}, [], secret)
    with pytest.raises(LLMError):
        build_messages({"personality": secret}, [], "hello")


def test_external_system_prompt_resolution_and_validation(tmp_path):
    prompt = tmp_path / "policy.md"
    prompt.write_text("custom policy", encoding="utf-8")
    config = tmp_path / "app.yaml"
    config.write_text(
        f'system_prompt_path: policy.md\ncharacter_path: "{(ROOT / "configs/character.yaml").as_posix()}"\n',
        encoding="utf-8",
    )
    settings, _ = load_config(config)
    assert load_system_prompt(settings.system_prompt_path) == "custom policy"
    prompt.write_text("password=secret", encoding="utf-8")
    with pytest.raises(ValueError):
        load_system_prompt(prompt)


def test_conversation_success_failure_blocking_and_forget():
    class LLM:
        def __init__(self):
            self.requests = []

        async def generate(self, messages):
            self.requests.append(messages)
            text = messages[-1]["content"]
            if text == "fail":
                raise LLMError("unavailable")
            if text == "blocked":
                return "password=secret"
            return "<think>hidden</think>clean answer"

    async def run():
        settings, character = load_config(ROOT / "configs/app.example.yaml")
        llm = LLM()
        app = Application(settings, character, llm, lambda text: None)
        for i, text in enumerate(("first", "second", "fail", "blocked")):
            app.submit(ChatInput(A, text, str(i)))
            await app.process_next()
        assert [m["role"] for m in llm.requests[1]] == [
            "system",
            "user",
            "assistant",
            "user",
        ]
        assert llm.requests[1][2]["content"] == "clean answer"
        assert len(app.history.messages(A)) == 4
        assert app.history.messages(B) == []
        await app.stop()
        assert len(app.history.messages(A)) == 4
        await app.forget(A)
        assert app.history.messages(A) == []
        app.submit(ChatInput(A, "password=private", "secret"))
        await app.process_next()
        assert len(llm.requests) == 4
        assert app.history.messages(A) == []
        app.settings = replace(settings, context_tokens=512)
        app.submit(ChatInput(A, "hello", "overflow"))
        await app.process_next()
        assert len(llm.requests) == 4

    asyncio.run(run())
