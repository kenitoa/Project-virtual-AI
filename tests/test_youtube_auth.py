import asyncio
import base64
import hashlib
import json
from copy import deepcopy
from urllib.parse import parse_qs

import httpx
import pytest

from virtual_ai.config import YouTubeSettings
from virtual_ai.integrations import youtube_auth as auth
from virtual_ai.integrations.youtube import ChatError, YouTubeChat


class Store:
    def __init__(self, value=None):
        self.value = deepcopy(value)

    def load(self):
        return deepcopy(self.value)

    def save(self, value):
        self.value = deepcopy(value)

    def delete(self):
        self.value = None


def record(expiry=2000):
    return dict(
        client_id="client",
        client_secret="private-client",
        access_token="private-access",
        refresh_token="private-refresh",
        expires_at=expiry,
        scope=auth.SCOPE,
    )


def tokens(**changes):
    return dict(
        access_token="new-access",
        refresh_token="new-refresh",
        token_type="Bearer",
        expires_in=3600,
        scope=auth.SCOPE,
        **changes,
    )


def session(store, handler, now=lambda: 1000):
    return auth.OAuthSession(
        store=store, transport=httpx.MockTransport(handler), now=now
    )


def test_browser_loopback_pkce_state_and_duplicate():
    async def run():
        tasks = []
        observed = {}

        def opener(url):
            params = dict(httpx.URL(url).params)
            observed.update(params)
            assert params["scope"] == auth.SCOPE
            assert params["code_challenge_method"] == "S256"
            assert params["redirect_uri"].startswith("http://127.0.0.1:")
            assert "code_verifier" not in params

            async def callback():
                async with httpx.AsyncClient(trust_env=False) as client:
                    bad = await client.get(
                        params["redirect_uri"], params={"state": "wrong", "code": "bad"}
                    )
                    assert bad.status_code == 400
                    duplicate = await client.get(
                        params["redirect_uri"],
                        params=[
                            ("state", params["state"]),
                            ("state", params["state"]),
                            ("code", "bad"),
                        ],
                    )
                    assert duplicate.status_code == 400
                    good = await client.get(
                        params["redirect_uri"],
                        params={"state": params["state"], "code": "one-use-code"},
                    )
                    assert good.status_code == 200
                    assert "one-use-code" not in good.text

            tasks.append(asyncio.create_task(callback()))
            return True

        code, verifier, redirect = await auth.authorization_code(
            "client", opener=opener, timeout=3
        )
        await asyncio.gather(*tasks)
        assert code == "one-use-code" and redirect == observed["redirect_uri"]
        assert (
            observed["code_challenge"]
            == base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        assert 43 <= len(verifier) <= 128
        with pytest.raises(httpx.ConnectError):
            async with httpx.AsyncClient(trust_env=False) as client:
                await client.get(redirect)

    asyncio.run(run())


def test_denied_timeout_and_browser_failure():
    async def run():
        tasks = []

        def opener(url):
            params = dict(httpx.URL(url).params)

            async def callback():
                async with httpx.AsyncClient(trust_env=False) as client:
                    await client.get(
                        params["redirect_uri"],
                        params={"state": params["state"], "error": "access_denied"},
                    )

            tasks.append(asyncio.create_task(callback()))
            return True

        with pytest.raises(auth.AuthError, match="denied"):
            await auth.authorization_code("client", opener=opener, timeout=3)
        await asyncio.gather(*tasks)
        with pytest.raises(auth.AuthError, match="timed out"):
            await auth.authorization_code("client", opener=lambda _: True, timeout=0.01)
        with pytest.raises(auth.AuthError, match="browser"):
            await auth.authorization_code("client", opener=lambda _: False)

    asyncio.run(run())


def test_login_exchange_and_restart_without_browser(tmp_path, monkeypatch):
    path = tmp_path / "client.json"
    path.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": "client",
                    "client_secret": "private-client",
                    "token_uri": "https://evil.invalid",
                }
            }
        )
    )

    async def code(*args, **kwargs):
        return "code", "verifier", "http://127.0.0.1:1234/"

    monkeypatch.setattr(auth, "authorization_code", code)
    calls = []

    def handler(request):
        calls.append(request)
        assert str(request.url) == auth.TOKEN_URL
        data = parse_qs(request.content.decode())
        assert data["grant_type"] == ["authorization_code"]
        assert data["code_verifier"] == ["verifier"]
        return httpx.Response(200, json=tokens())

    async def run():
        store = Store()
        await session(store, handler).login(path)
        assert store.value["refresh_token"] == "new-refresh"
        assert await session(store, handler).access_token() == "new-access"
        assert len(calls) == 1

    asyncio.run(run())


def test_refresh_margin_concurrent_and_refresh_token_preserved():
    calls = []

    def handler(request):
        calls.append(request)
        data = parse_qs(request.content.decode())
        assert data["grant_type"] == ["refresh_token"]
        payload = tokens()
        del payload["refresh_token"]
        del payload["scope"]
        return httpx.Response(200, json=payload)

    async def run():
        store = Store(record(1060))
        obj = session(store, handler)
        assert await asyncio.gather(obj.access_token(), obj.access_token()) == [
            "new-access",
            "new-access",
        ]
        assert len(calls) == 1 and store.value["refresh_token"] == "private-refresh"

    asyncio.run(run())


@pytest.mark.parametrize(
    "failure", ["invalid_grant", "network", "malformed", "scope", "expiry"]
)
def test_refresh_failure_stops_once_and_clears_credentials(failure, caplog):
    calls = []

    def handler(request):
        calls.append(request)
        if failure == "network":
            raise httpx.ConnectError("private-refresh")
        if failure == "invalid_grant":
            return httpx.Response(
                400, json={"error": "invalid_grant", "detail": "private-refresh"}
            )
        data = tokens()
        if failure == "malformed":
            data = {"secret": "private-refresh"}
        elif failure == "scope":
            data["scope"] += " https://www.googleapis.com/auth/youtube"
        else:
            data["expires_in"] = -1
        return httpx.Response(200, json=data)

    async def run():
        store = Store(record(1001))
        obj = session(store, handler)
        for _ in range(2):
            with pytest.raises(auth.AuthError) as exc:
                await obj.access_token()
            assert "private-refresh" not in str(exc.value)
        assert len(calls) == 1 and store.value is None
        assert "private-refresh" not in caplog.text

    asyncio.run(run())


@pytest.mark.parametrize("remote_status", [200, 400, 503])
def test_logout_always_removes_local_credentials(remote_status):
    def handler(request):
        assert str(request.url) == auth.REVOKE_URL
        assert request.method == "POST" and not request.url.query
        return httpx.Response(remote_status)

    async def run():
        store = Store(record())
        obj = session(store, handler)
        if remote_status == 200:
            await obj.logout()
        else:
            with pytest.raises(auth.AuthError):
                await obj.logout()
        assert store.value is None
        with pytest.raises(auth.AuthError):
            await obj.access_token()

    asyncio.run(run())


def identity_handler(request):
    if request.url.path.endswith("channels"):
        return httpx.Response(
            200, json={"items": [{"id": "channel", "snippet": {"title": "Owner"}}]}
        )
    return httpx.Response(
        200,
        json={
            "items": [
                {
                    "id": "broadcast",
                    "snippet": {
                        "channelId": "channel",
                        "title": "Show",
                        "liveChatId": "chat",
                    },
                    "status": {"lifeCycleStatus": "live"},
                }
            ]
        },
    )


@pytest.mark.parametrize(
    "channel,broadcast,chat",
    [
        ("channel", "broadcast", "chat"),
        ("other", "broadcast", "chat"),
        ("channel", "other", "chat"),
        ("channel", "broadcast", "other"),
    ],
)
def test_account_broadcast_chat_are_separately_checked(channel, broadcast, chat):
    async def run():
        obj = session(Store(record()), identity_handler)
        if (channel, broadcast, chat) == ("channel", "broadcast", "chat"):
            assert (await obj.resolve(channel, broadcast, chat))[
                "live_chat_id"
            ] == "chat"
        else:
            with pytest.raises(auth.AuthError, match="mismatch"):
                await obj.resolve(channel, broadcast, chat)

    asyncio.run(run())


def test_revoked_authorization_clears_and_does_not_retry():
    async def run():
        store = Store(record())
        obj = session(store, lambda _: httpx.Response(401, text="private-access"))
        with pytest.raises(auth.AuthError, match="revoked"):
            await obj.channels()
        assert store.value is None

    asyncio.run(run())


def test_manual_mode_never_reads_vault(monkeypatch):
    monkeypatch.setenv("YOUTUBE_ACCESS_TOKEN", "manual")
    monkeypatch.setattr(auth, "CredentialStore", lambda: pytest.fail("vault"))
    assert asyncio.run(auth.prepare_access(YouTubeSettings(True, "chat"))) == (
        "manual",
        None,
    )


def test_oauth_chat_uses_refreshed_token_and_stops_on_401(monkeypatch):
    monkeypatch.setenv("YOUTUBE_ACCESS_TOKEN", "wrong-manual")
    calls = []
    store = Store(record(1001))

    def handler(request):
        if str(request.url) == auth.TOKEN_URL:
            return httpx.Response(200, json=tokens())
        assert request.headers["Authorization"] == "Bearer new-access"
        if request.url.path.endswith("messages"):
            calls.append(request)
            return httpx.Response(401, text="private-access")
        return identity_handler(request)

    async def run():
        obj = session(store, handler)
        adapter = YouTubeChat(
            YouTubeSettings(True, "chat", "oauth", "channel", "broadcast"),
            max_age=30,
            auth_session=obj,
            transport=httpx.MockTransport(handler),
        )
        with pytest.raises(ChatError):
            await adapter.run(lambda _: pytest.fail("submitted"))
        assert len(calls) == 1 and store.value is None

    asyncio.run(run())


@pytest.mark.parametrize(
    "value", [{}, {"web": {}}, {"installed": {"client_id": "client"}}]
)
def test_reject_non_desktop_client(tmp_path, value):
    path = tmp_path / "client.json"
    path.write_text(json.dumps(value))
    with pytest.raises(auth.AuthError):
        auth.load_desktop_client(path)


def test_vault_errors_are_sanitized_and_never_fall_back_to_file(tmp_path):
    class Broken:
        def get_password(self, *args):
            raise RuntimeError("private-access")

        set_password = delete_password = get_password

    store = object.__new__(auth.CredentialStore)
    store.backend = Broken()
    for operation in (store.load, lambda: store.save(record()), store.delete):
        with pytest.raises(auth.AuthError) as exc:
            operation()
        assert "private-access" not in str(exc.value)
    assert list(tmp_path.iterdir()) == []


def test_oauth_settings_require_explicit_identity():
    with pytest.raises(ValueError):
        YouTubeSettings(True, "chat", "oauth")
    with pytest.raises(ValueError):
        YouTubeSettings(False, "", "automatic")


def test_vault_failure_prevents_browser(tmp_path, monkeypatch):
    path = tmp_path / "client.json"
    path.write_text(
        json.dumps({"installed": {"client_id": "client", "client_secret": "secret"}})
    )

    class Unavailable(Store):
        def load(self):
            raise auth.AuthError("Vault unavailable")

    async def forbidden(*args, **kwargs):
        pytest.fail("browser opened")

    monkeypatch.setattr(auth, "authorization_code", forbidden)
    with pytest.raises(auth.AuthError):
        asyncio.run(
            session(Unavailable(), lambda _: pytest.fail("network")).login(path)
        )


def test_login_requires_refresh_token(tmp_path, monkeypatch):
    path = tmp_path / "client.json"
    path.write_text(
        json.dumps({"installed": {"client_id": "client", "client_secret": "secret"}})
    )

    async def code(*args, **kwargs):
        return "code", "verifier", "http://127.0.0.1:1234/"

    monkeypatch.setattr(auth, "authorization_code", code)
    payload = tokens()
    del payload["refresh_token"]
    store = Store()
    with pytest.raises(auth.AuthError):
        asyncio.run(
            session(store, lambda _: httpx.Response(200, json=payload)).login(path)
        )
    assert store.value is None


def test_logout_network_failure_keeps_local_removal():
    def handler(request):
        raise httpx.ConnectError("private-refresh")

    store = Store(record())
    with pytest.raises(auth.AuthError, match="Local logout complete"):
        asyncio.run(session(store, handler).logout())
    assert store.value is None


@pytest.mark.parametrize(
    "bad", [None, {}, record(float("nan")), dict(record(), access_token="bad\x00token")]
)
def test_invalid_saved_credentials_never_go_to_network(bad):
    with pytest.raises(auth.AuthError):
        asyncio.run(
            session(Store(bad), lambda _: pytest.fail("network")).access_token()
        )


def test_refresh_failure_during_polling_does_not_read_next_page():
    clock = [1000]
    polls = []
    store = Store(record(2000))

    def handler(request):
        if str(request.url) == auth.TOKEN_URL:
            return httpx.Response(400, json={"error": "invalid_grant"})
        if request.url.path.endswith("messages"):
            polls.append(request)
            return httpx.Response(
                200,
                json={
                    "items": [],
                    "nextPageToken": "cursor",
                    "pollingIntervalMillis": 1000,
                },
            )
        return identity_handler(request)

    async def sleep(delay):
        clock[0] = 1990

    async def run():
        obj = session(store, handler, now=lambda: clock[0])
        adapter = YouTubeChat(
            YouTubeSettings(True, "chat", "oauth", "channel", "broadcast"),
            max_age=30,
            auth_session=obj,
            transport=httpx.MockTransport(handler),
            sleep=sleep,
        )
        with pytest.raises(ChatError, match="refresh failed"):
            await adapter.run(lambda _: pytest.fail("submitted"))
        assert len(polls) == 1 and store.value is None

    asyncio.run(run())


def test_health_checks_oauth_identity_without_speaking(tmp_path, monkeypatch):
    from dataclasses import replace

    from test_health import run_checks, settings

    from virtual_ai.health import OK, RECOVERY

    store = Store(record())
    monkeypatch.setattr(auth, "CredentialStore", lambda: store)
    store.value["expires_at"] = auth.time.time() + 3600

    def handler(request):
        if request.url.path.endswith("messages"):
            return httpx.Response(200, json={"items": [], "nextPageToken": "cursor"})
        return identity_handler(request)

    config = replace(
        settings(),
        youtube=YouTubeSettings(True, "chat", "oauth", "channel", "broadcast"),
    )
    assert (
        run_checks(config, tmp_path, transport=httpx.MockTransport(handler))[
            "youtube"
        ].status
        == OK
    )
    config = replace(
        config, youtube=YouTubeSettings(True, "wrong", "oauth", "channel", "broadcast")
    )
    assert (
        run_checks(config, tmp_path, transport=httpx.MockTransport(handler))[
            "youtube"
        ].status
        == RECOVERY
    )
