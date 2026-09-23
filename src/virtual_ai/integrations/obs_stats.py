"""Explicit, read-only OBS WebSocket v5 statistics on loopback."""

import asyncio
import base64
import hashlib
import json
import math
import os
import uuid

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

FIELDS = (
    "cpuUsage",
    "memoryUsage",
    "activeFps",
    "averageFrameRenderTime",
    "renderSkippedFrames",
    "renderTotalFrames",
    "outputSkippedFrames",
    "outputTotalFrames",
)


def authentication(password, salt, challenge):
    secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest())
    return base64.b64encode(
        hashlib.sha256(secret + challenge.encode()).digest()
    ).decode()


async def sample(port=4455):
    if not 1 <= port <= 65535:
        return {"status": "invalid_port"}
    try:
        async with asyncio.timeout(3):
            async with connect(
                f"ws://127.0.0.1:{port}",
                proxy=None,
                max_size=16384,
                max_queue=1,
                open_timeout=2,
                close_timeout=0.2,
            ) as socket:
                hello = json.loads(await socket.recv())
                if hello["op"] != 0:
                    raise ValueError()
                identify = {"rpcVersion": 1, "eventSubscriptions": 0}
                auth = hello["d"].get("authentication")
                if auth:
                    password = os.environ.get("OBS_WEBSOCKET_PASSWORD")
                    if not password:
                        return {"status": "authentication_required"}
                    identify["authentication"] = authentication(
                        password, auth["salt"], auth["challenge"]
                    )
                await socket.send(json.dumps({"op": 1, "d": identify}))
                if json.loads(await socket.recv())["op"] != 2:
                    raise ValueError()
                request_id = uuid.uuid4().hex
                await socket.send(
                    json.dumps(
                        {
                            "op": 6,
                            "d": {
                                "requestType": "GetStats",
                                "requestId": request_id,
                            },
                        }
                    )
                )
                response = json.loads(await socket.recv())
                data = response["d"]
                if (
                    response["op"] != 7
                    or data["requestId"] != request_id
                    or data["requestType"] != "GetStats"
                    or data["requestStatus"]["result"] is not True
                ):
                    raise ValueError()
                values = {key: data["responseData"][key] for key in FIELDS}
                if any(
                    type(v) not in (int, float) or not math.isfinite(v) or v < 0
                    for v in values.values()
                ):
                    raise ValueError()
                return {"status": "ok", **values}
    except (OSError, TimeoutError, WebSocketException, ValueError, KeyError, TypeError):
        return {"status": "unavailable"}
