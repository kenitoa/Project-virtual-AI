import asyncio
from argparse import Namespace
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import yaml
from test_voice_pipeline import LLM, ROOT

from virtual_ai import app as app_module
from virtual_ai.app import Application
from virtual_ai.config import YouTubeSettings, load_config
from virtual_ai.integrations.youtube import ChatError, YouTubeChat, receive_chat


def event(mid="m1", text="hello", age=0):
    return {
        "id": mid,
        "authorDetails": {"channelId": "stable-user", "isChatOwner": True},
        "snippet": {
            "type": "textMessageEvent",
            "liveChatId": "chat",
            "authorChannelId": "stable-user",
            "publishedAt": (
                datetime.now(timezone.utc) - timedelta(seconds=age)
            ).isoformat(),
            "textMessageDetails": {"messageText": text},
        },
    }


def page(items=(), token="next", ended=False):
    data = {"items": list(items), "nextPageToken": token, "pollingIntervalMillis": 2500}
    if ended:
        data["offlineAt"] = "2026-09-22T00:00:00Z"
    return data


def adapter(handler, sleep, enabled=True):
    return YouTubeChat(
        YouTubeSettings(enabled, "chat"),
        max_age=30,
        transport=httpx.MockTransport(handler),
        sleep=sleep,
    )


@pytest.fixture(autouse=True)
def token(monkeypatch):
    monkeypatch.setenv("YOUTUBE_ACCESS_TOKEN", "private-token")


def test_conversion_backlog_duplicates_reconnect_and_poll_interval():
    async def scenario():
        calls, delays, received = [], [], []
        results = [
            page([event("old")], "cursor1"),
            httpx.ConnectError("private detail"),
            page([event("old"), event("new", "/stop")], "cursor2"),
            page([event("new"), event("next", "/quit")], ended=True),
        ]

        def handler(request):
            calls.append(request)
            result = results.pop(0)
            if isinstance(result, Exception):
                raise result
            return httpx.Response(200, json=result)

        async def sleep(delay):
            delays.append(delay)

        await adapter(handler, sleep).run(received.append)
        assert [m.text for m in received] == ["/stop", "/quit"]
        assert received[0].viewer.platform == "youtube"
        assert received[0].viewer.user_id == "stable-user"
        assert [r.url.params.get("pageToken") for r in calls] == [
            None,
            "cursor1",
            "cursor1",
            "cursor2",
        ]
        assert all(r.headers["Authorization"] == "Bearer private-token" for r in calls)
        assert all(d >= 2.5 for d in delays)

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [401, 403, 404, 302])
def test_permanent_failure_is_not_retried_or_leaked(status):
    async def scenario():
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(status, text="private-token viewer message")

        with pytest.raises(ChatError) as error:
            await adapter(handler, asyncio.sleep).run(lambda m: None)
        assert len(calls) == 1
        assert "private" not in str(error.value)

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", [429, 503, "timeout"])
def test_retry_budget_and_retry_after(failure):
    async def scenario():
        calls, delays = [], []

        def handler(request):
            calls.append(request)
            if failure == "timeout":
                raise httpx.ReadTimeout("private")
            return httpx.Response(failure, headers={"Retry-After": "12"})

        async def sleep(delay):
            delays.append(delay)

        with pytest.raises(ChatError, match="retry limit"):
            await adapter(handler, sleep).run(lambda m: None)
        assert len(calls) == 4
        assert len(delays) == 3
        if failure != "timeout":
            assert min(delays) >= 12

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "data",
    [
        [],
        {},
        {"items": []},
        page(token=""),
        {**page(), "pollingIntervalMillis": True},
        page([{"snippet": []}]),
    ],
)
def test_invalid_response_fails_closed(data):
    async def scenario():
        with pytest.raises(ChatError):
            await adapter(lambda r: httpx.Response(200, json=data), asyncio.sleep).run(
                lambda m: pytest.fail("submitted")
            )

    asyncio.run(scenario())


def test_malformed_json():
    async def scenario():
        with pytest.raises(ChatError):
            await adapter(lambda r: httpx.Response(200, text="{"), asyncio.sleep).run(
                lambda m: None
            )

    asyncio.run(scenario())


def test_event_filtering_and_identity_validation():
    client = adapter(None, asyncio.sleep)
    assert client._message(event(age=60)) is None
    assert client._message(event(age=-60)) is None
    assert client._message({"snippet": {"type": "superChatEvent"}}) is None
    for field in ("liveChatId", "authorChannelId", "publishedAt"):
        invalid = deepcopy(event())
        invalid["snippet"][field] = "wrong"
        with pytest.raises(ChatError):
            client._message(invalid)


def test_disabled_and_missing_credentials(monkeypatch):
    monkeypatch.delenv("YOUTUBE_ACCESS_TOKEN")

    async def scenario():
        def forbidden(request):
            pytest.fail("network")

        await adapter(forbidden, asyncio.sleep, False).run(lambda m: None)
        with pytest.raises(ChatError, match="YOUTUBE_ACCESS_TOKEN"):
            await adapter(forbidden, asyncio.sleep).run(lambda m: None)

    asyncio.run(scenario())


def test_cancellation_closes_transport():
    async def scenario():
        started = asyncio.Event()

        class Transport(httpx.AsyncBaseTransport):
            closed = False

            async def handle_async_request(self, request):
                started.set()
                await asyncio.Event().wait()

            async def aclose(self):
                self.closed = True

        transport = Transport()
        client = YouTubeChat(
            YouTubeSettings(True, "chat"), max_age=30, transport=transport
        )
        task = asyncio.create_task(client.run(lambda m: None))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert transport.closed

    asyncio.run(scenario())


def test_chat_commands_are_data_and_failure_does_not_stop_app(caplog):
    async def scenario():
        settings, character = load_config(ROOT / "configs/app.example.yaml")
        llm = LLM("answer")
        app = Application(settings, character, llm, output=lambda s: None)
        client = adapter(None, asyncio.sleep)
        for i, command in enumerate(("/stop", "/quit", "/forget")):
            assert app.submit(client._message(event(str(i), command)))
            await app.process_next()
        assert app._epoch == 0 and not app._closed
        assert len(llm.calls) == 3

        class Failed:
            async def run(self, submit):
                raise ChatError("private-token")

        await receive_chat(Failed(), app.submit)
        assert app.submit(client._message(event("after")))
        await app.process_next()
        assert len(llm.calls) == 4
        assert "private-token" not in caplog.text
        await app.shutdown()

    asyncio.run(scenario())


@pytest.mark.parametrize("enabled", [False, True])
def test_cli_lifecycle(monkeypatch, enabled):
    import virtual_ai.inputs.console as console_module

    async def scenario():
        settings, character = load_config(ROOT / "configs/app.example.yaml")
        settings = replace(settings, youtube=YouTubeSettings(enabled, "chat"))
        monkeypatch.setattr(app_module, "load_config", lambda p: (settings, character))
        started, stopped = asyncio.Event(), asyncio.Event()

        class Fake:
            def __init__(self, *args, **kwargs):
                assert enabled

            async def run(self, submit):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    stopped.set()

        async def console(app):
            if enabled:
                await started.wait()

        monkeypatch.setattr(app_module, "YouTubeChat", Fake)
        monkeypatch.setattr(console_module, "console", console)
        await app_module.run_cli(Namespace(config="unused", backend="mock", once=None))
        assert stopped.is_set() == enabled

    asyncio.run(scenario())


def test_settings_and_config(tmp_path):
    for kwargs in ({"enabled": "false"}, {"enabled": True}, {"live_chat_id": 1}):
        with pytest.raises(ValueError):
            YouTubeSettings(**kwargs)
    data = yaml.safe_load(
        (ROOT / "configs/app.example.yaml").read_text(encoding="utf-8")
    )
    data["youtube"] = {
        "enabled": True,
        "live_chat_id": "chat",
        "access_token": "private",
    }
    path = tmp_path / "app.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValueError, match="youtube"):
        load_config(path)
