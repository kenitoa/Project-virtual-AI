import asyncio
import json
from dataclasses import replace

import pytest

from virtual_ai.config import VTSSettings
from virtual_ai.integrations.vts.base import AvatarError
from virtual_ai.integrations.vts.client import API_NAME, VTSClient


class FakeSocket:
    def __init__(self):
        self.calls = []
        self.closed = 0
        self.model = "model-1"
        self.loaded = True
        self.authenticated = True
        self.states = {
            "happy.exp3.json": False,
            "sad.exp3.json": False,
            "manual.exp3.json": True,
        }
        self.hotkeys = [
            {
                "name": "Happy",
                "hotkeyID": "happy-id",
                "type": "ToggleExpression",
                "file": "happy.exp3.json",
            },
            {
                "name": "Sad",
                "hotkeyID": "sad-id",
                "type": "ToggleExpression",
                "file": "sad.exp3.json",
            },
        ]
        self.mutate = lambda response: response
        self.pause = None
        self.entered = asyncio.Event()
        self.approval_delay = 0

    async def send(self, raw):
        request = json.loads(raw)
        assert request["apiName"] == API_NAME
        assert request["apiVersion"] == "1.0"
        assert request["requestID"] not in {x["requestID"] for x in self.calls}
        self.calls.append(request)
        if request["messageType"] == "ExpressionActivationRequest":
            self.states[request["data"]["expressionFile"]] = request["data"]["active"]

    async def recv(self):
        await asyncio.sleep(0)
        request = self.calls[-1]
        kind = request["messageType"]
        if kind == self.pause:
            self.entered.set()
            await asyncio.Event().wait()
        if kind == "AuthenticationTokenRequest":
            await asyncio.sleep(self.approval_delay)
        model = {"modelLoaded": self.loaded, "modelID": self.model}
        data = {
            "APIStateRequest": {
                "active": True,
                "vTubeStudioVersion": "test",
                "currentSessionAuthenticated": False,
            },
            "AuthenticationTokenRequest": {"authenticationToken": "private-test-token"},
            "AuthenticationRequest": {
                "authenticated": self.authenticated,
                "reason": "test",
            },
            "CurrentModelRequest": model,
            "HotkeysInCurrentModelRequest": {**model, "availableHotkeys": self.hotkeys},
            "ExpressionStateRequest": {
                **model,
                "expressions": [
                    {"file": file, "active": active}
                    for file, active in self.states.items()
                ],
            },
            "ExpressionActivationRequest": {},
        }[kind]
        response = {
            "apiName": API_NAME,
            "apiVersion": "1.0",
            "requestID": request["requestID"],
            "messageType": kind.removesuffix("Request") + "Response",
            "data": data,
        }
        result = self.mutate(response)
        return result if isinstance(result, str) else json.dumps(result)

    async def close(self):
        self.closed += 1


def make_client(tmp_path, socket=None, **changes):
    socket = socket or FakeSocket()
    settings = VTSSettings(
        enabled=True,
        token_path=str(tmp_path / ".local/vts-token.json"),
        expected_model_id="model-1",
        expression_hotkeys={"happy": "happy-id", "sad": "sad-id"},
    )

    async def connector(settings):
        return socket

    return VTSClient(replace(settings, **changes), connector), socket


def activations(socket):
    return [
        x["data"]
        for x in socket.calls
        if x["messageType"] == "ExpressionActivationRequest"
    ]


def test_authentication_and_saved_token_reuse(tmp_path, caplog):
    async def run():
        client, socket = make_client(tmp_path)
        await client.connect(authenticate=True)
        await client.aclose()
        second, other = make_client(tmp_path)
        await second.connect()
        result = await second.list_hotkeys()
        assert result["modelID"] == "model-1"
        assert not any(
            x["messageType"] == "AuthenticationTokenRequest" for x in other.calls
        )
        assert (
            next(x for x in other.calls if x["messageType"] == "AuthenticationRequest")[
                "data"
            ]["authenticationToken"]
            == "private-test-token"
        )
        await second.aclose()
        assert socket.closed == other.closed == 1

    asyncio.run(run())
    assert "private-test-token" not in caplog.text


@pytest.mark.parametrize(
    "token_file", [None, "broken", '{"authenticationToken":"stale"}']
)
def test_missing_or_invalid_token_never_prompts(tmp_path, token_file):
    async def run():
        if token_file is not None:
            folder = tmp_path / ".local"
            folder.mkdir()
            (folder / "vts-token.json").write_text(token_file)
        client, socket = make_client(tmp_path)
        with pytest.raises(AvatarError, match="authenticate"):
            await client.connect()
        assert socket.closed == 1
        assert [x["messageType"] for x in socket.calls] == ["APIStateRequest"]
        await client.aclose()

    asyncio.run(run())


def test_revoked_token_and_explicit_renewal(tmp_path):
    async def run():
        client, _ = make_client(tmp_path)
        await client.connect(authenticate=True)
        await client.aclose()
        stale, socket = make_client(tmp_path)
        socket.authenticated = False
        with pytest.raises(AvatarError, match="refused"):
            await stale.connect()
        assert not any(
            x["messageType"] == "AuthenticationTokenRequest" for x in socket.calls
        )
        assert socket.closed == 1
        fresh, _ = make_client(tmp_path)
        await fresh.connect(authenticate=True)
        await fresh.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "mode",
    [
        "json",
        "id",
        "kind",
        "api",
        "data",
        "error",
        "missing",
        "boolean",
        "token",
        "reason",
    ],
)
def test_invalid_responses_and_denied_authentication(tmp_path, mode):
    def mutate(response):
        if mode == "json":
            return "not JSON private-test-token"
        if mode == "id":
            response["requestID"] = "wrong"
        if mode == "kind":
            response["messageType"] = "UnrelatedEvent"
        if mode == "api":
            response["apiVersion"] = "2.0"
        if mode == "data":
            response["data"] = []
        if mode == "error":
            response.update(
                messageType="APIError",
                data={"errorID": 50, "message": "private-test-token"},
            )
        if mode == "missing":
            response["data"].pop("active", None)
        if mode == "boolean":
            response["data"]["active"] = 1
        if mode == "token" and response["messageType"] == "AuthenticationTokenResponse":
            response["data"]["authenticationToken"] = "x" * 65
        if mode == "reason" and response["messageType"] == "AuthenticationResponse":
            response["data"].pop("reason")
        return response

    async def run():
        client, socket = make_client(tmp_path)
        socket.mutate = mutate
        with pytest.raises(AvatarError) as exc:
            await client.connect(authenticate=True)
        assert "private-test-token" not in str(exc.value)
        assert socket.closed == 1
        assert not (tmp_path / ".local/vts-token.json").exists()

    asyncio.run(run())


def test_approval_has_separate_timeout(tmp_path):
    async def run():
        client, socket = make_client(
            tmp_path, request_timeout_seconds=0.02, authentication_timeout_seconds=0.3
        )
        socket.approval_delay = 0.05
        await client.connect(authenticate=True)
        await client.aclose()

    asyncio.run(run())


def test_idempotent_expression_switch_and_owned_reset(tmp_path):
    async def run():
        client, socket = make_client(tmp_path)
        await client.connect(authenticate=True)
        await asyncio.gather(
            client.set_expression("happy"), client.set_expression("happy")
        )
        assert len(activations(socket)) == 1
        await client.set_expression("sad")
        assert not socket.states["happy.exp3.json"]
        assert socket.states["sad.exp3.json"]
        await client.set_expression("neutral")
        await client.reset()
        assert socket.states == {
            "happy.exp3.json": False,
            "sad.exp3.json": False,
            "manual.exp3.json": True,
        }
        assert [(x["expressionFile"], x["active"]) for x in activations(socket)] == [
            ("happy.exp3.json", True),
            ("happy.exp3.json", False),
            ("sad.exp3.json", True),
            ("sad.exp3.json", False),
        ]
        await client.aclose()
        await client.aclose()
        assert socket.closed == 1

    asyncio.run(run())


def test_preexisting_expression_is_not_owned(tmp_path):
    async def run():
        client, socket = make_client(tmp_path)
        socket.states["happy.exp3.json"] = True
        await client.connect(authenticate=True)
        await client.set_expression("happy")
        await client.reset()
        await client.aclose()
        assert not activations(socket)
        assert socket.states["happy.exp3.json"]

    asyncio.run(run())


@pytest.mark.parametrize(
    "mode",
    [
        "no-model",
        "wrong-model",
        "unmapped",
        "unknown-id",
        "wrong-type",
        "unsafe-file",
        "missing-file",
        "forbidden",
        "unconfigured-model",
        "duplicate-id",
        "bad-state",
    ],
)
def test_model_mapping_and_expression_restrictions(tmp_path, mode):
    async def run():
        options = {}
        if mode == "unmapped":
            options["expression_hotkeys"] = {}
        if mode == "unknown-id":
            options["expression_hotkeys"] = {"happy": "unknown"}
        if mode == "unconfigured-model":
            options["expected_model_id"] = ""
        client, socket = make_client(tmp_path, **options)
        await client.connect(authenticate=True)
        if mode == "no-model":
            socket.loaded = False
        if mode == "wrong-model":
            socket.model = "other"
        if mode == "wrong-type":
            socket.hotkeys[0]["type"] = "ChangeModel"
        if mode == "unsafe-file":
            socket.hotkeys[0]["file"] = "../evil.exp3.json"
        if mode == "missing-file":
            del socket.states["happy.exp3.json"]
        if mode == "duplicate-id":
            socket.hotkeys.append(socket.hotkeys[0])
        if mode == "bad-state":
            socket.states["happy.exp3.json"] = 1
        with pytest.raises(AvatarError):
            await client.set_expression(
                "raw-hotkey-id" if mode == "forbidden" else "happy"
            )
        assert not activations(socket)
        await client.aclose()

    asyncio.run(run())


def test_model_switch_does_not_reset_other_model(tmp_path):
    async def run():
        client, socket = make_client(tmp_path)
        await client.connect(authenticate=True)
        await client.set_expression("happy")
        socket.model = "other"
        with pytest.raises(AvatarError, match="model"):
            await client.aclose()
        assert len(activations(socket)) == 1
        assert socket.closed == 1

    asyncio.run(run())


@pytest.mark.parametrize("cancel", [False, True])
def test_uncertain_activation_is_not_retried(tmp_path, cancel):
    async def run():
        client, socket = make_client(tmp_path, request_timeout_seconds=0.02)
        await client.connect(authenticate=True)
        socket.pause = "ExpressionActivationRequest"
        task = asyncio.create_task(client.set_expression("happy"))
        await socket.entered.wait()
        if cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else AvatarError):
            await task
        with pytest.raises(AvatarError):
            await client.set_expression("happy")
        with pytest.raises(AvatarError, match="manually"):
            await client.aclose()
        assert len(activations(socket)) == 1
        assert socket.closed == 1

    asyncio.run(run())


def test_cancelled_authentication_closes_connection(tmp_path):
    async def run():
        client, socket = make_client(tmp_path)
        socket.pause = "AuthenticationTokenRequest"
        task = asyncio.create_task(client.connect(authenticate=True))
        await socket.entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await client.aclose()
        assert socket.closed == 1
        assert not (tmp_path / ".local/vts-token.json").exists()

    asyncio.run(run())


def test_disconnect_and_connection_error_are_avatar_errors(tmp_path):
    async def run():
        client, socket = make_client(tmp_path)
        await client.connect(authenticate=True)

        async def disconnected():
            raise OSError("private path")

        socket.recv = disconnected
        with pytest.raises(AvatarError):
            await client.list_hotkeys()
        await client.aclose()
        assert socket.closed == 1

        async def unavailable(settings):
            raise OSError("private path")

        failed = VTSClient(client.settings, unavailable)
        with pytest.raises(AvatarError, match="Cannot connect"):
            await failed.connect()
        await failed.aclose()

    asyncio.run(run())


def test_close_resets_owned_expression_even_if_caller_cancelled(tmp_path):
    async def run():
        client, socket = make_client(tmp_path)
        await client.connect(authenticate=True)
        await client.set_expression("happy")
        started = asyncio.Event()
        release = asyncio.Event()

        async def close():
            started.set()
            await release.wait()
            socket.closed += 1

        socket.close = close
        task = asyncio.create_task(client.aclose())
        await started.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not socket.states["happy.exp3.json"]
        assert socket.closed == 1

    asyncio.run(run())


def test_disabled_does_not_connect_or_write(tmp_path):
    async def run():
        client, socket = make_client(tmp_path, enabled=False)
        with pytest.raises(AvatarError, match="disabled"):
            await client.connect(authenticate=True)
        await client.aclose()
        assert not socket.calls and not socket.closed
        assert not (tmp_path / ".local").exists()

    asyncio.run(run())


def test_redirect_is_never_followed(tmp_path):
    from websockets.asyncio.server import serve

    async def run():
        target_calls = []

        async def target(ws):
            target_calls.append(True)

        async with serve(target, "127.0.0.1", 0) as destination:
            target_url = f"ws://127.0.0.1:{destination.sockets[0].getsockname()[1]}"

            def redirect(connection, request):
                response = connection.respond(302, "redirect")
                response.headers["Location"] = target_url
                return response

            async with serve(
                target, "127.0.0.1", 0, process_request=redirect
            ) as server:
                settings = VTSSettings(
                    enabled=True,
                    url=f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}",
                    token_path=str(tmp_path / ".local/token.json"),
                )
                client = VTSClient(settings)
                with pytest.raises(AvatarError, match="Cannot connect"):
                    await client.connect(authenticate=True)
                await client.aclose()
                assert not target_calls
                assert not (tmp_path / ".local").exists()

    asyncio.run(run())


def test_approval_denial_does_not_overwrite_saved_token(tmp_path):
    async def run():
        client, _ = make_client(tmp_path)
        await client.connect(authenticate=True)
        await client.aclose()
        path = tmp_path / ".local/vts-token.json"
        original = path.read_bytes()
        denied, socket = make_client(tmp_path)

        def reject(response):
            if response["messageType"] == "AuthenticationTokenResponse":
                response.update(
                    messageType="APIError", data={"errorID": 50, "message": "denied"}
                )
            return response

        socket.mutate = reject
        with pytest.raises(AvatarError):
            await denied.connect(authenticate=True)
        assert path.read_bytes() == original
        assert socket.closed == 1

    asyncio.run(run())


def test_repeated_auth_cancel_waits_for_socket_cleanup(tmp_path):
    async def run():
        client, socket = make_client(tmp_path)
        socket.pause = "AuthenticationTokenRequest"
        closing = asyncio.Event()
        release = asyncio.Event()

        async def close():
            closing.set()
            await release.wait()
            socket.closed += 1

        socket.close = close
        task = asyncio.create_task(client.connect(authenticate=True))
        await socket.entered.wait()
        task.cancel()
        await closing.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert socket.closed == 1
        await client.aclose()

    asyncio.run(run())


def test_real_websocket_transport_with_local_fake_server(tmp_path, monkeypatch):
    from websockets.asyncio.server import serve

    async def run():
        fake = FakeSocket()

        async def handler(ws):
            async for raw in ws:
                await fake.send(raw)
                await ws.send(await fake.recv())

        # Invalid environment proxy must not redirect the local connection.
        monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
        async with serve(handler, "127.0.0.1", 0) as server:
            settings = VTSSettings(
                enabled=True,
                url=f"ws://localhost:{server.sockets[0].getsockname()[1]}",
                token_path=str(tmp_path / ".local/token.json"),
                expected_model_id="model-1",
                expression_hotkeys={"happy": "happy-id"},
            )
            client = VTSClient(settings)
            await client.connect(authenticate=True)
            await client.set_expression("happy")
            await client.aclose()
            assert not fake.states["happy.exp3.json"]
            assert len(activations(fake)) == 2

    asyncio.run(run())


@pytest.mark.parametrize("mode", ["timeout", "error"])
def test_socket_close_failure_is_reported(tmp_path, mode):
    async def run():
        client, socket = make_client(tmp_path, request_timeout_seconds=0.02)
        await client.connect(authenticate=True)

        async def close():
            if mode == "timeout":
                await asyncio.Event().wait()
            raise OSError("private detail")

        socket.close = close
        with pytest.raises(AvatarError, match="cleanup failed"):
            await client.aclose()

    asyncio.run(run())
