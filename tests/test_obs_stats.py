import asyncio
import json

from websockets.asyncio.server import serve

from virtual_ai.integrations import obs_stats


def test_read_only_authenticated_stats(monkeypatch):
    monkeypatch.setenv("OBS_WEBSOCKET_PASSWORD", "test-only")

    async def scenario():
        async def handler(socket):
            await socket.send(
                json.dumps(
                    {
                        "op": 0,
                        "d": {
                            "authentication": {"salt": "salt", "challenge": "challenge"}
                        },
                    }
                )
            )
            identify = json.loads(await socket.recv())
            assert identify == {
                "op": 1,
                "d": {
                    "rpcVersion": 1,
                    "eventSubscriptions": 0,
                    "authentication": obs_stats.authentication(
                        "test-only", "salt", "challenge"
                    ),
                },
            }
            await socket.send('{"op":2}')
            request = json.loads(await socket.recv())
            assert request["op"] == 6
            assert request["d"]["requestType"] == "GetStats"
            await socket.send(
                json.dumps(
                    {
                        "op": 7,
                        "d": {
                            **request["d"],
                            "requestStatus": {"result": True},
                            "responseData": dict.fromkeys(obs_stats.FIELDS, 1),
                        },
                    }
                )
            )

        async with serve(handler, "127.0.0.1", 0) as server:
            return await obs_stats.sample(server.sockets[0].getsockname()[1])

    result = asyncio.run(scenario())
    assert result == {"status": "ok", **dict.fromkeys(obs_stats.FIELDS, 1)}
    assert "test-only" not in json.dumps(result)


def test_missing_auth_and_bad_protocol(monkeypatch):
    monkeypatch.delenv("OBS_WEBSOCKET_PASSWORD", raising=False)

    async def scenario(message):
        async def handler(socket):
            await socket.send(message)
            await socket.wait_closed()

        async with serve(handler, "127.0.0.1", 0) as server:
            return await obs_stats.sample(server.sockets[0].getsockname()[1])

    assert asyncio.run(scenario('{"op":0,"d":{"authentication":{"salt":"s"}}}')) == {
        "status": "authentication_required"
    }
    assert asyncio.run(scenario("[]")) == {"status": "unavailable"}
