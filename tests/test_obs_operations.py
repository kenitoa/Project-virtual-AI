import asyncio
import json

import pytest
from websockets.asyncio.server import serve

from virtual_ai.integrations.obs import OBS, OBSError


def test_obs_authenticated_refresh_rechecks_mapping_and_never_starts_output(
    tmp_path, monkeypatch
):
    async def run():
        subtitle = tmp_path / "subtitles.txt"
        subtitle.write_text("테스트", encoding="utf-8")
        requests = []
        mismatch = [False]

        async def handler(ws):
            await ws.send(
                json.dumps(
                    {
                        "op": 0,
                        "d": {
                            "authentication": {"salt": "salt", "challenge": "challenge"}
                        },
                    }
                )
            )
            identify = json.loads(await ws.recv())
            assert identify["op"] == 1 and identify["d"]["authentication"]
            await ws.send('{"op":2}')
            async for raw in ws:
                data = json.loads(raw)["d"]
                name = data["requestType"]
                requests.append(name)
                result = {}
                if name == "GetInputSettings":
                    result = {
                        "inputKind": "text_gdiplus_v3",
                        "inputSettings": {
                            "read_from_file": True,
                            "file": str(
                                tmp_path / "other.txt" if mismatch[0] else subtitle
                            ),
                        },
                    }
                if name in ("GetRecordStatus", "GetStreamStatus"):
                    result = {"outputActive": False}
                await ws.send(
                    json.dumps(
                        {
                            "op": 7,
                            "d": {
                                "requestType": name,
                                "requestId": data["requestId"],
                                "requestStatus": {"result": True},
                                "responseData": result,
                            },
                        }
                    )
                )

        monkeypatch.setenv("TEST_OBS_PASSWORD", "private-password")
        async with serve(handler, "127.0.0.1", 0) as server:
            config = tmp_path / "obs.json"
            config.write_text(
                json.dumps(
                    {
                        "url": f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}",
                        "password_env": "TEST_OBS_PASSWORD",
                        "subtitle_sources": ["AI Captions"],
                    }
                )
            )
            obs = OBS(config, subtitle)
            await obs.start()
            assert requests.count("SetInputSettings") == 1
            await obs.refresh()
            assert requests.count("SetInputSettings") == 1
            mismatch[0] = True
            subtitle.write_text("", encoding="utf-8")
            with pytest.raises(OBSError):
                await obs.refresh()
            assert requests.count("SetInputSettings") == 1
            with pytest.raises(OBSError):
                await obs.call("StartStream")
            assert (
                "StartStream" not in requests
                and "private-password" not in json.dumps(obs.status())
            )
            await obs.aclose()

    asyncio.run(run())


def test_obs_rejects_remote_endpoint_before_connect(tmp_path):
    config = tmp_path / "obs.json"
    config.write_text(
        json.dumps(
            {
                "url": "ws://example.com:4455",
                "password_env": "unused",
                "subtitle_sources": ["Captions"],
            }
        )
    )
    with pytest.raises(ValueError, match="loopback"):
        OBS(config, tmp_path / "subtitle.txt")
