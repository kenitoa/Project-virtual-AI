import asyncio
import json

import httpx
import pytest

from virtual_ai.config import Settings
from virtual_ai.llm.base import LLMError
from virtual_ai.llm.koboldcpp import KoboldCppClient


def answer(content="answer"):
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


@pytest.mark.parametrize("base", ["http://localhost:5001", "http://localhost:5001/"])
def test_request_contract_and_reuse(base):
    calls = []
    messages = [{"role": "user", "content": "private-input"}]

    def handler(request):
        calls.append(request)
        assert str(request.url) == "http://localhost:5001/v1/chat/completions"
        assert request.method == "POST"
        assert json.loads(request.content) == {
            "model": "test-model",
            "messages": messages,
            "max_tokens": 123,
            "stream": False,
        }
        return httpx.Response(200, json=answer())

    async def run():
        client = KoboldCppClient(
            Settings(base_url=base, model="test-model", max_output_tokens=123),
            httpx.MockTransport(handler),
        )
        try:
            assert await client.generate(messages) == "answer"
            assert await client.generate(messages) == "answer"
            assert len(calls) == 2
        finally:
            await client.aclose()
        assert client._client.is_closed

    asyncio.run(run())


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"null",
        b"[]",
        b"{}",
        b'{"choices":[]}',
        json.dumps(answer(None)).encode(),
        json.dumps(answer(123)).encode(),
        json.dumps(answer("  ")).encode(),
    ],
)
def test_invalid_response_not_retried(body):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=body)

    async def run():
        client = KoboldCppClient(Settings(retries=3), httpx.MockTransport(handler))
        try:
            with pytest.raises(LLMError, match="응답 형식"):
                await client.generate([])
            assert len(calls) == 1
        finally:
            await client.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "status,count",
    [
        (400, 1),
        (401, 1),
        (403, 1),
        (404, 1),
        (302, 1),
        (500, 1),
        (429, 3),
        (502, 3),
        (503, 3),
        (504, 3),
    ],
)
def test_http_retry_policy(status, count):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="private-server-body")

    async def run():
        client = KoboldCppClient(Settings(retries=2), httpx.MockTransport(handler))
        try:
            with pytest.raises(LLMError, match=f"HTTP {status}") as caught:
                await client.generate([])
            assert "private" not in str(caught.value)
            assert len(calls) == count
        finally:
            await client.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "error,expected,count",
    [
        (httpx.ConnectError, "연결할 수", 2),
        (httpx.ReadTimeout, "응답 시간이", 2),
        (httpx.RemoteProtocolError, "통신 오류", 1),
    ],
)
def test_transport_errors_are_sanitized(error, expected, count, caplog):
    calls = []

    def handler(request):
        calls.append(request)
        raise error("private-input private-answer Bearer secret", request=request)

    async def run():
        client = KoboldCppClient(Settings(retries=1), httpx.MockTransport(handler))
        try:
            with pytest.raises(LLMError, match=expected) as caught:
                await client.generate([])
            assert "private" not in str(caught.value)
            assert "secret" not in str(caught.value)
            assert len(calls) == count
        finally:
            await client.aclose()

    asyncio.run(run())
    assert "private" not in caplog.text
    assert "secret" not in caplog.text


def test_total_deadline_includes_retry_backoff():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ConnectError("private", request=request)

    async def run():
        client = KoboldCppClient(
            Settings(retries=3, total_timeout_seconds=0.03),
            httpx.MockTransport(handler),
        )
        try:
            with pytest.raises(LLMError, match="전체 응답 시간"):
                await client.generate([])
            assert len(calls) == 1
        finally:
            await client.aclose()

    asyncio.run(run())


def test_total_deadline_interrupts_slow_response():
    async def handler(request):
        await asyncio.sleep(10)
        return httpx.Response(200, json=answer())

    async def run():
        client = KoboldCppClient(
            Settings(total_timeout_seconds=0.02), httpx.MockTransport(handler)
        )
        try:
            with pytest.raises(LLMError, match="전체 응답 시간"):
                await client.generate([])
        finally:
            await client.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:5001/v1",
        "http://localhost/v1/",
        "http://localhost:bad",
        "http://user:secret@localhost",
        "http://localhost?key=secret",
    ],
)
def test_reject_non_base_urls(url):
    with pytest.raises(ValueError):
        Settings(backend="koboldcpp", base_url=url)


@pytest.mark.parametrize("value", [0, -1, float("inf"), True])
def test_total_timeout_validation(value):
    with pytest.raises(ValueError):
        Settings(total_timeout_seconds=value)
