"""Opt-in fixed-file subtitle refresh and read-only output observation.

Never starts recording/streaming or dispatches model-generated requests.
"""

import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
from time import monotonic
from urllib.parse import urlsplit
from uuid import uuid4

from websockets.asyncio.client import connect


class OBSError(Exception):
    pass


class OBS:
    def __init__(self, config_path, subtitle_path):
        config_path = Path(config_path).resolve()
        if config_path.stat().st_size > 8192:
            raise ValueError("OBS configuration too large")
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(config, dict) or set(config) - {
            "url",
            "password_env",
            "credentials_file",
            "subtitle_sources",
        }:
            raise ValueError("invalid OBS configuration")
        url = urlsplit(config.get("url", "ws://127.0.0.1:4455"))
        if (
            url.scheme != "ws"
            or url.hostname not in ("127.0.0.1", "localhost", "::1")
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in ("", "/")
        ):
            raise ValueError("OBS requires a loopback WebSocket URL")
        self.url = url.geturl()
        self.sources = config.get("subtitle_sources", [])
        if (
            not isinstance(self.sources, list)
            or not 1 <= len(self.sources) <= 4
            or any(
                not isinstance(s, str) or not s.strip() or len(s) > 100
                for s in self.sources
            )
            or len(set(self.sources)) != len(self.sources)
        ):
            raise ValueError("OBS requires 1..4 unique subtitle source names")
        if bool(config.get("password_env")) == bool(config.get("credentials_file")):
            raise ValueError("select one OBS credential source")
        if config.get("credentials_file"):
            path = config_path.parent / config["credentials_file"]
            if path.stat().st_size > 32768:
                raise ValueError("OBS credentials file too large")
            self.password = json.loads(path.read_text(encoding="utf-8-sig")).get(
                "server_password"
            )
        else:
            self.password = os.environ.get(config["password_env"])
        if (
            not isinstance(self.password, str)
            or not self.password
            or len(self.password) > 1024
        ):
            raise ValueError("OBS credentials unavailable")
        self.subtitle_path = Path(subtitle_path).resolve()
        self.ws = None
        self.observed = {"state": "not_probed", "checked_at": None}
        self.previous = None

    async def start(self):
        try:
            async with asyncio.timeout(5):
                self.ws = await connect(
                    self.url,
                    proxy=None,
                    open_timeout=3,
                    close_timeout=1,
                    max_size=256 * 1024,
                )
                hello = json.loads(await self.ws.recv())
                if hello.get("op") != 0:
                    raise OBSError()
                auth = hello["d"]["authentication"]
                secret = base64.b64encode(
                    hashlib.sha256((self.password + auth["salt"]).encode()).digest()
                ).decode()
                proof = base64.b64encode(
                    hashlib.sha256((secret + auth["challenge"]).encode()).digest()
                ).decode()
                await self.ws.send(
                    json.dumps(
                        {
                            "op": 1,
                            "d": {
                                "rpcVersion": 1,
                                "authentication": proof,
                                "eventSubscriptions": 0,
                            },
                        }
                    )
                )
                if json.loads(await self.ws.recv()).get("op") != 2:
                    raise OBSError()
                await self.validate_sources()
                await self.refresh()
        except Exception:
            await self.aclose()
            raise OBSError(
                "OBS authentication or subtitle configuration failed"
            ) from None

    async def call(self, request, **data):
        if request not in {
            "GetInputSettings",
            "SetInputSettings",
            "GetRecordStatus",
            "GetStreamStatus",
        }:
            raise OBSError("OBS request not allowed")
        identifier = uuid4().hex
        async with asyncio.timeout(3):
            await self.ws.send(
                json.dumps(
                    {
                        "op": 6,
                        "d": {
                            "requestType": request,
                            "requestId": identifier,
                            "requestData": data,
                        },
                    }
                )
            )
            response = json.loads(await self.ws.recv())
            result = response.get("d", {})
            if (
                response.get("op") != 7
                or result.get("requestId") != identifier
                or result.get("requestType") != request
                or result.get("requestStatus", {}).get("result") is not True
            ):
                raise OBSError("OBS request failed")
            return result.get("responseData", {})

    async def validate_sources(self):
        for name in self.sources:
            info = await self.call("GetInputSettings", inputName=name)
            settings = info.get("inputSettings", {})
            if (
                info.get("inputKind") != "text_gdiplus_v3"
                or settings.get("read_from_file") is not True
                or not isinstance(settings.get("file"), str)
                or Path(settings["file"]).resolve() != self.subtitle_path
            ):
                raise OBSError("OBS subtitle source changed")

    async def refresh(self):
        if self.subtitle_path.stat().st_size > 16384:
            raise OBSError("subtitle file too large")
        current = self.subtitle_path.read_bytes()
        if current != self.previous:
            await self.validate_sources()
            for name in self.sources:
                await self.call(
                    "SetInputSettings",
                    inputName=name,
                    inputSettings={
                        "read_from_file": True,
                        "file": str(self.subtitle_path),
                    },
                    overlay=True,
                )
            self.previous = current
            self.observed["subtitle_update_acknowledged"] = True

    async def monitor(self):
        last_probe = 0
        try:
            while True:
                await self.refresh()
                if monotonic() - last_probe >= 2:
                    await self.validate_sources()
                    record = await self.call("GetRecordStatus")
                    stream = await self.call("GetStreamStatus")
                    self.observed.update(
                        state="observed",
                        recording=record.get("outputActive"),
                        streaming=stream.get("outputActive"),
                        checked_at=monotonic(),
                    )
                    last_probe = monotonic()
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.observed.update(
                state="unavailable", subtitle_update_acknowledged=False
            )

    def status(self):
        checked = self.observed.get("checked_at")
        return {k: v for k, v in self.observed.items() if k != "checked_at"} | {
            "checked_age_seconds": None
            if checked is None
            else max(0, monotonic() - checked),
            "visual_display": "unobserved",
        }

    async def aclose(self):
        if self.ws:
            await self.ws.close()
            self.ws = None
