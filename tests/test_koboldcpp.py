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
            with pytest.raises(
                LLMError,
                match="cleanup is unconfirmed"
                if status in (502, 504)
                else f"HTTP {status}",
            ) as caught:
                await client.generate([])
            assert "private" not in str(caught.value)
            assert len(calls) == (1 if status in (502, 504) else count)
        finally:
            await client.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "error,expected,count",
    [
        (httpx.ConnectError, "연결할 수", 2),
        (httpx.ReadTimeout, "cleanup is unconfirmed", 1),
        (httpx.RemoteProtocolError, "cleanup is unconfirmed", 1),
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


@pytest.mark.parametrize("value", [0, -1, 31, float("nan"), float("inf"), True])
def test_abort_timeout_validation(value):
    with pytest.raises(ValueError):
        Settings(abort_timeout_seconds=value)


@pytest.mark.parametrize("value", ["false", 0, None])
def test_abort_enabled_requires_boolean(value):
    with pytest.raises(ValueError):
        Settings(server_abort_enabled=value)


class AbortServer:
    def __init__(self, result=None, failure=None):
        self.started = asyncio.Event()
        self.aborting = asyncio.Event()
        self.release_abort = asyncio.Event()
        self.result = (
            result if result is not None else {"success": "true", "done": "true"}
        )
        self.failure = failure
        self.keys = []
        self.aborts = []
        self.idle = 1
        self.perf_seen = asyncio.Event()

    async def __call__(self, request):
        if request.url.path == "/api/extra/abort":
            self.aborts.append(json.loads(request.content)["genkey"])
            self.aborting.set()
            await self.release_abort.wait()
            if self.failure == "http":
                return httpx.Response(500)
            if self.failure == "json":
                return httpx.Response(200, content=b"private-invalid-json")
            return httpx.Response(200, json=self.result)
        if request.url.path == "/api/extra/perf":
            self.perf_seen.set()
            return httpx.Response(200, json={"idle": self.idle, "queue": 0})
        self.keys.append(json.loads(request.content)["genkey"])
        if len(self.keys) == 1:
            self.started.set()
            if self.failure == "read":
                raise httpx.ReadTimeout("private", request=request)
            await asyncio.Event().wait()
        return httpx.Response(200, json=answer())


def test_late_abort_blocks_next_generation_and_repeated_cancellation():
    async def run():
        server = AbortServer()
        client = KoboldCppClient(
            Settings(server_abort_enabled=True), httpx.MockTransport(server)
        )
        try:
            first = asyncio.create_task(client.generate([]))
            await server.started.wait()
            first.cancel()
            await server.aborting.wait()
            first.cancel()
            next_request = asyncio.create_task(client.generate([]))
            await asyncio.sleep(0)
            assert len(server.keys) == 1
            assert server.aborts == server.keys
            server.release_abort.set()
            with pytest.raises(asyncio.CancelledError):
                await first
            assert await next_request == "answer"
            assert len(set(server.keys)) == 2
            assert len(server.aborts) == 1
            # Cancellation after completion must not touch a later request.
            assert not first.cancel()
            assert await client.generate([]) == "answer"
            assert len(server.aborts) == 1
        finally:
            await client.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "result,failure",
    [
        ({"success": "false", "done": "true"}, None),
        ({"success": "true", "done": "false"}, None),
        ({"success": False, "done": True}, None),
        ({"success": 1, "done": 1}, None),
        ({"success": "TRUE", "done": "true"}, None),
        ({}, None),
        ([], None),
        (None, "http"),
        (None, "json"),
        (None, "timeout"),
    ],
)
def test_unconfirmed_abort_blocks_new_requests(result, failure, caplog):
    async def run():
        server = AbortServer(result, failure)
        if failure != "timeout":
            server.release_abort.set()
        client = KoboldCppClient(
            Settings(server_abort_enabled=True, abort_timeout_seconds=0.03),
            httpx.MockTransport(server),
        )
        try:
            first = asyncio.create_task(client.generate([]))
            await server.started.wait()
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(first, 1)
            with pytest.raises(LLMError, match="cleanup is unconfirmed"):
                await client.generate([])
            assert len(server.keys) == len(server.aborts) == 1
            assert "cleanup=unconfirmed" in caplog.text
            assert "private" not in caplog.text
        finally:
            await client.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("idle", [0, "1", True])
def test_abort_acknowledgement_is_not_cleanup_confirmation(idle):
    async def run():
        server = AbortServer()
        server.idle = idle
        server.release_abort.set()
        client = KoboldCppClient(
            Settings(server_abort_enabled=True, abort_timeout_seconds=0.03),
            httpx.MockTransport(server),
        )
        first = asyncio.create_task(client.generate([]))
        await server.started.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert server.perf_seen.is_set()
        with pytest.raises(LLMError, match="cleanup is unconfirmed"):
            await client.generate([])
        await client.aclose()

    asyncio.run(run())


def test_read_timeout_cleans_before_retry():
    async def run():
        server = AbortServer(failure="read", result={"success": True, "done": True})
        client = KoboldCppClient(
            Settings(server_abort_enabled=True), httpx.MockTransport(server)
        )
        first = asyncio.create_task(client.generate([]))
        await server.aborting.wait()
        assert len(server.keys) == 1
        server.release_abort.set()
        assert await first == "answer"
        assert server.aborts == server.keys[:1]
        assert len(set(server.keys)) == 2
        await client.aclose()

    asyncio.run(run())


def test_close_during_generation_joins_cleanup_even_when_cancelled():
    async def run():
        before = asyncio.all_tasks()
        server = AbortServer()
        client = KoboldCppClient(
            Settings(server_abort_enabled=True), httpx.MockTransport(server)
        )
        first = asyncio.create_task(client.generate([]))
        await server.started.wait()
        closing = asyncio.create_task(client.aclose())
        await server.aborting.wait()
        closing.cancel()
        await asyncio.sleep(0)
        assert not client._client.is_closed
        server.release_abort.set()
        with pytest.raises(asyncio.CancelledError):
            await closing
        with pytest.raises(asyncio.CancelledError):
            await first
        await client.aclose()
        assert client._client.is_closed and client._control.is_closed
        assert len(server.aborts) == 1
        assert asyncio.all_tasks() == before
        with pytest.raises(LLMError, match="closed"):
            await client.generate([])

    asyncio.run(run())


def test_total_timeout_cleans_and_lock_waiter_does_not_abort_owner():
    async def run():
        server = AbortServer()
        server.release_abort.set()
        client = KoboldCppClient(
            Settings(server_abort_enabled=True, total_timeout_seconds=0.1),
            httpx.MockTransport(server),
        )
        first = asyncio.create_task(client.generate([]))
        await server.started.wait()
        waiter = asyncio.create_task(client.generate([]))
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert server.aborts == []
        with pytest.raises(LLMError, match="전체 응답 시간"):
            await first
        assert server.aborts == server.keys
        assert await client.generate([]) == "answer"
        await client.aclose()

    asyncio.run(run())
