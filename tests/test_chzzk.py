import asyncio
import json
from argparse import Namespace
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from virtual_ai.config import ChzzkSettings
from virtual_ai.integrations import chzzk_auth as auth
from virtual_ai.integrations.chzzk import ChzzkChat, receive_chzzk, socket_url
from virtual_ai.integrations.youtube import ChatError
from virtual_ai.integrations.youtube_auth import AuthError

CHANNEL = "a" * 32


class Store:
    def __init__(self, value=None):
        self.value = deepcopy(value)

    def load(self):
        return deepcopy(self.value)

    def save(self, value):
        self.value = deepcopy(value)

    def delete(self):
        self.value = None


def record(expiry=3000):
    return dict(
        client_id="client",
        client_secret="secret",
        access_token="access",
        refresh_token="refresh",
        channel_id=CHANNEL,
        expires_at=expiry,
    )


def test_refresh_single_use_and_concurrent_requests():
    async def run():
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(
                200,
                json={
                    "accessToken": "new",
                    "refreshToken": "rotated",
                    "expiresIn": "86400",
                    "tokenType": "Bearer",
                },
            )

        store = Store(record(900))
        session = auth.ChzzkSession(
            store=store, transport=httpx.MockTransport(handler), now=lambda: 1000
        )
        assert await asyncio.gather(session.access_token(), session.access_token()) == [
            "new",
            "new",
        ]
        assert len(calls) == 1
        assert store.value["refresh_token"] == "rotated"

    asyncio.run(run())


def test_ambiguous_refresh_clears_old_credentials_and_never_retries():
    async def run():
        calls = []

        def handler(request):
            calls.append(request)
            raise httpx.ReadTimeout("secret-url")

        store = Store(record(900))
        session = auth.ChzzkSession(
            store=store, transport=httpx.MockTransport(handler), now=lambda: 1000
        )
        for _ in range(2):
            with pytest.raises(AuthError) as error:
                await session.access_token()
            assert "secret-url" not in str(error.value)
        assert len(calls) == 1 and store.value is None

    asyncio.run(run())


def test_channel_mismatch_and_write_operations_rejected():
    async def run():
        session = auth.ChzzkSession(
            store=Store(record()),
            now=lambda: 1000,
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    200, json={"code": 200, "content": {"channelId": "b" * 32}}
                )
            ),
        )
        with pytest.raises(AuthError, match="mismatch"):
            await session.verify(CHANNEL)
        with pytest.raises(AuthError, match="not allowed"):
            await session.api("POST", "/open/v1/chats/send")

    asyncio.run(run())


def test_callback_state_validation_and_cleanup():
    async def run():
        tasks = []

        async def callback(url):
            query = parse_qs(urlsplit(url).query)
            assert query["redirectUri"] == [auth.REDIRECT]
            async with httpx.AsyncClient(trust_env=False) as client:
                bad = await client.get(
                    auth.REDIRECT, params={"state": "wrong", "code": "bad"}
                )
                assert bad.status_code == 400
                good = await client.get(
                    auth.REDIRECT, params={"state": query["state"][0], "code": "valid"}
                )
                assert good.status_code == 200

        def opener(url):
            tasks.append(asyncio.create_task(callback(url)))
            return True

        code, state = await auth.authorization_code("client", opener=opener, timeout=5)
        await asyncio.gather(*tasks)
        assert code == "valid" and state
        server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 8766)
        server.close()
        await server.wait_closed()

    asyncio.run(run())


@pytest.mark.parametrize(
    "url",
    [
        "http://ssio08.nchat.naver.com?auth=x",
        "https://ssio08.nchat.naver.com.evil.test?auth=x",
        "https://localhost?auth=x",
        "https://ssio08.nchat.naver.com:444?auth=x",
        "https://ssio08.nchat.naver.com/path?auth=x",
    ],
)
def test_socket_url_rejects_untrusted_destinations(url):
    with pytest.raises(ChatError):
        socket_url(url)


def test_message_boundaries_and_operator_commands_remain_data():
    chat = ChzzkChat(
        ChzzkSettings(True, CHANNEL), max_age=30, session=object(), now=lambda: 1000
    )
    chat._started_at = 990
    event = dict(
        channelId=CHANNEL,
        senderChannelId="viewer",
        content="/quit",
        messageTime=1000000,
    )
    item = chat.message(event)
    assert item.text == "/quit" and item.viewer.platform == "chzzk"
    assert chat.message(event) is None
    assert chat.message({**event, "messageTime": 980000}) is None
    with pytest.raises(ChatError):
        chat.message({**event, "channelId": "b" * 32})
    chat.accept_after = lambda: 1001
    assert chat.message({**event, "content": "new"}) is None


def test_socket_subscription_then_event_and_revocation():
    async def run():
        calls = []

        class Session:
            async def verify(self, channel):
                assert channel == CHANNEL

            async def api(self, method, path, **kwargs):
                calls.append((method, path, kwargs))
                return {"url": "https://ssio08.nchat.naver.com?auth=secret"}

        def packet(kind, data):
            return "42" + json.dumps([kind, json.dumps(data)])

        messages = iter(
            [
                '0{"pingInterval":25000,"pingTimeout":5000}',
                "40",
                packet(
                    "SYSTEM", {"type": "connected", "data": {"sessionKey": "session"}}
                ),
                packet(
                    "SYSTEM",
                    {
                        "type": "subscribed",
                        "data": {"channelId": CHANNEL, "eventType": "CHAT"},
                    },
                ),
                packet(
                    "CHAT",
                    {
                        "channelId": CHANNEL,
                        "senderChannelId": "viewer",
                        "content": "hi",
                        "messageTime": 1000000,
                    },
                ),
                packet("SYSTEM", {"type": "revoked", "data": {}}),
            ]
        )

        class Socket:
            closed = False

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                self.closed = True

            async def recv(self):
                return next(messages)

            async def send(self, message):
                pass

        ws = Socket()

        def connector(url, **kwargs):
            assert url.startswith("wss://ssio08.nchat.naver.com/socket.io/")
            assert parse_qs(urlsplit(url).query)["EIO"] == ["3"]
            return ws

        chat = ChzzkChat(
            ChzzkSettings(True, CHANNEL),
            max_age=30,
            session=Session(),
            connector=connector,
            now=lambda: 1000,
        )
        items, stopped = [], []
        await receive_chzzk(chat, items.append, stopped.append)
        assert len(items) == 1
        assert stopped == ["authorization_revoked"] and ws.closed
        assert calls[1][2] == {"params": {"sessionKey": "session"}}

    asyncio.run(run())


@pytest.mark.parametrize(
    "kwargs",
    [
        {"enabled": "true"},
        {"enabled": True},
        {"channel_id": "bad"},
        {"channel_id": None},
    ],
)
def test_settings_reject_invalid_values(kwargs):
    with pytest.raises(ValueError):
        ChzzkSettings(**kwargs)


@pytest.mark.parametrize("local_only", [False, True])
def test_cli_pause_local_only_and_cancellation(monkeypatch, local_only):
    from virtual_ai import app as app_module
    from virtual_ai.config import load_config
    from virtual_ai.inputs import console as console_module

    async def run():
        settings, character = load_config(Path("configs/app.example.yaml"))
        settings = replace(settings, chzzk=ChzzkSettings(True, CHANNEL))
        monkeypatch.setattr(app_module, "load_config", lambda p: (settings, character))

        async def health(_):
            return []

        monkeypatch.setattr(app_module, "check_health", health)
        started, closed = asyncio.Event(), asyncio.Event()

        class Chat:
            def __init__(self, *args, **kwargs):
                assert not local_only

            async def run(self, submit):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    closed.set()

        async def console(app):
            assert app.runtime.paused == (not local_only)
            if not local_only:
                await started.wait()

        monkeypatch.setattr(app_module, "ChzzkChat", Chat)
        monkeypatch.setattr(console_module, "console", console)
        await app_module.run_cli(
            Namespace(config="unused", backend="mock", once=None, local_only=local_only)
        )
        assert closed.is_set() == (not local_only)

    asyncio.run(run())


def test_login_mismatch_does_not_save_tokens(tmp_path, monkeypatch):
    async def run():
        path = tmp_path / "client.json"
        path.write_text(json.dumps({"client_id": "client", "client_secret": "secret"}))

        async def code(*args, **kwargs):
            return "code", "state"

        monkeypatch.setattr(auth, "authorization_code", code)

        def handler(request):
            if request.url.path.endswith("/token"):
                return httpx.Response(
                    200,
                    json={
                        "accessToken": "access",
                        "refreshToken": "refresh",
                        "expiresIn": 86400,
                    },
                )
            return httpx.Response(
                200, json={"code": 200, "content": {"channelId": "b" * 32}}
            )

        store = Store()
        session = auth.ChzzkSession(store=store, transport=httpx.MockTransport(handler))
        with pytest.raises(AuthError, match="mismatch"):
            await session.login(path, CHANNEL)
        assert store.value is None

    asyncio.run(run())
