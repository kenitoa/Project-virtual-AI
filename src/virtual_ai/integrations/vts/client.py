"""Serialized VTS requests and explicit expression states, never arbitrary hotkeys."""

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from virtual_ai.integrations.vts.base import AvatarError

API_NAME = "VTubeStudioPublicAPI"


class _LocalConnect(connect):
    def process_redirect(self, exc):
        # Even a local endpoint must not redirect us to a remote server.
        return exc


async def _connect(settings):
    url = urlsplit(settings.url)
    if url.hostname == "localhost":
        url = url._replace(netloc=f"127.0.0.1:{url.port or 80}")
    # Avoid payload debug logs containing authentication tokens.
    logger = logging.Logger("virtual_ai.vts.transport", level=logging.CRITICAL)
    logger.addHandler(logging.NullHandler())
    return await _LocalConnect(
        urlunsplit(url),
        proxy=None,
        open_timeout=settings.request_timeout_seconds,
        close_timeout=settings.request_timeout_seconds,
        max_size=1024 * 1024,
        max_queue=4,
        logger=logger,
    )


def _text(value, maximum=256):
    return isinstance(value, str) and 0 < len(value) <= maximum and value.isprintable()


def _token(value):
    return _text(value, 64) and value.isascii()


def _file(value):
    return (
        _text(value)
        and value.endswith(".exp3.json")
        and not any(c in value for c in ("/", "\\", ":"))
        and value not in (".exp3.json", "..exp3.json")
    )


async def _wait_cleanup(task):
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    task.result()
    if cancelled:
        raise asyncio.CancelledError


class VTSClient:
    def __init__(self, settings, connector=None):
        self.settings = settings
        self._connector = connector or _connect
        self._ws = None
        self._lock = asyncio.Lock()
        self._ready = False
        self._broken = False
        self._closed = False
        self._close_task = None
        self._model_id = None
        self._owned = set()

    async def _request(self, kind, data=None, timeout=None):
        if self._ws is None or self._broken:
            raise AvatarError("VTS connection unavailable; reconnect explicitly.")
        request_id = uuid4().hex
        try:
            async with asyncio.timeout(
                timeout or self.settings.request_timeout_seconds
            ):
                await self._ws.send(
                    json.dumps(
                        {
                            "apiName": API_NAME,
                            "apiVersion": "1.0",
                            "requestID": request_id,
                            "messageType": kind + "Request",
                            "data": data or {},
                        }
                    )
                )
                raw = await self._ws.recv()
                if not isinstance(raw, str) or len(raw) > 1024 * 1024:
                    raise AvatarError("Invalid VTS response frame.")
                response = json.loads(raw)
                if (
                    not isinstance(response, dict)
                    or response.get("apiName") != API_NAME
                    or response.get("apiVersion") != "1.0"
                    or response.get("requestID") != request_id
                    or not isinstance(response.get("data"), dict)
                ):
                    raise AvatarError("Invalid VTS response envelope.")
                if response.get("messageType") == "APIError":
                    # Server text may contain private paths or tokens.
                    raise AvatarError(
                        "VTS rejected the request; check API permission and model settings."
                    )
                if response.get("messageType") != kind + "Response":
                    raise AvatarError("Unexpected VTS response type.")
                return response["data"]
        except asyncio.CancelledError:
            self._broken = True
            raise
        except (OSError, WebSocketException, TimeoutError, ValueError, AvatarError):
            self._broken = True
            raise AvatarError(
                "VTS request failed or response invalid; no retry. "
                "If an expression changed, reset it manually in VTube Studio."
            ) from None

    def _read_token(self):
        path = Path(self.settings.token_path)
        try:
            with path.open(encoding="utf-8") as stream:
                data = json.loads(stream.read(4097))
            if (
                not isinstance(data, dict)
                or data.get("pluginName") != self.settings.plugin_name
                or data.get("pluginDeveloper") != self.settings.plugin_developer
                or not _token(data.get("authenticationToken"))
            ):
                raise ValueError
            return data["authenticationToken"]
        except FileNotFoundError:
            raise AvatarError(
                "No saved VTS token; run --authenticate explicitly."
            ) from None
        except (OSError, ValueError):
            raise AvatarError(
                "Invalid VTS token file; run --authenticate explicitly."
            ) from None

    def _save_token(self, token):
        path = Path(self.settings.token_path)
        temporary = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(
                dir=path.parent, prefix="vts-", suffix=".tmp"
            )
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(
                    {
                        "pluginName": self.settings.plugin_name,
                        "pluginDeveloper": self.settings.plugin_developer,
                        "authenticationToken": token,
                    },
                    stream,
                )
            os.replace(temporary, path)
        except OSError:
            raise AvatarError("Could not save VTS token privately.") from None
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

    async def connect(self, *, authenticate=False):
        async with self._lock:
            if not self.settings.enabled:
                raise AvatarError(
                    "VTS is disabled; enable it in the local configuration."
                )
            if self._closed or self._broken or self._ws is not None:
                raise AvatarError("Create a new VTS client for a new session.")
            try:
                async with asyncio.timeout(self.settings.request_timeout_seconds):
                    self._ws = await self._connector(self.settings)
                state = await self._request("APIState")
                if (
                    state.get("active") is not True
                    or type(state.get("currentSessionAuthenticated")) is not bool
                    or not _text(state.get("vTubeStudioVersion"))
                ):
                    raise AvatarError("VTS API is inactive or its state is invalid.")
                identity = {
                    "pluginName": self.settings.plugin_name,
                    "pluginDeveloper": self.settings.plugin_developer,
                }
                if authenticate:
                    response = await self._request(
                        "AuthenticationToken",
                        identity,
                        self.settings.authentication_timeout_seconds,
                    )
                    token = response.get("authenticationToken")
                    if not _token(token):
                        raise AvatarError("Invalid VTS authentication token response.")
                else:
                    token = self._read_token()
                response = await self._request(
                    "Authentication",
                    {
                        **identity,
                        "authenticationToken": token,
                    },
                )
                if response.get("authenticated") is not True or not isinstance(
                    response.get("reason"), str
                ):
                    raise AvatarError(
                        "VTS authentication refused; run --authenticate explicitly."
                    )
                if authenticate:
                    self._save_token(token)
                self._ready = True
            except BaseException as exc:
                self._broken = True
                await self._disconnect()
                if isinstance(exc, (OSError, WebSocketException, TimeoutError)):
                    raise AvatarError("Cannot connect to the local VTS API.") from None
                raise

    def _require_ready(self):
        if (
            not self._ready
            or self._closed
            or self._broken
            or self._close_task is not None
        ):
            raise AvatarError(
                "VTS session is not ready; connect and authenticate first."
            )

    def _check_model(self, data):
        model = data.get("modelID")
        if data.get("modelLoaded") is not True or not _text(model, 128):
            raise AvatarError("No valid VTS model is loaded.")
        expected = self.settings.expected_model_id
        if (expected and model != expected) or (
            self._model_id and model != self._model_id
        ):
            raise AvatarError("VTS model changed or does not match expected_model_id.")
        self._model_id = model
        return model

    async def _current_model(self):
        return self._check_model(await self._request("CurrentModel"))

    async def _hotkeys(self):
        await self._current_model()
        data = await self._request("HotkeysInCurrentModel")
        self._check_model(data)
        entries = data.get("availableHotkeys")
        if not isinstance(entries, list):
            raise AvatarError("Invalid VTS hotkey list.")
        seen = set()
        for item in entries:
            if (
                not isinstance(item, dict)
                or not _text(item.get("hotkeyID"), 128)
                or item["hotkeyID"] in seen
                or not _text(item.get("name"))
                or not _text(item.get("type"))
                or not isinstance(item.get("file"), str)
            ):
                raise AvatarError("Invalid VTS hotkey entry.")
            seen.add(item["hotkeyID"])
        return entries

    async def list_hotkeys(self):
        async with self._lock:
            self._require_ready()
            entries = await self._hotkeys()
            return {"modelID": self._model_id, "availableHotkeys": entries}

    async def _states(self):
        data = await self._request("ExpressionState", {"details": False})
        self._check_model(data)
        entries = data.get("expressions")
        if not isinstance(entries, list):
            raise AvatarError("Invalid VTS expression list.")
        states = {}
        for item in entries:
            if (
                not isinstance(item, dict)
                or not _file(item.get("file"))
                or type(item.get("active")) is not bool
                or item["file"] in states
            ):
                raise AvatarError("Invalid VTS expression state.")
            states[item["file"]] = item["active"]
        return states

    async def _activate(self, file, active):
        # VTS has no atomic model-ID precondition; the operator must keep the model stable.
        await self._current_model()
        await self._request(
            "ExpressionActivation",
            {
                "expressionFile": file,
                "active": active,
                "fadeTime": 0.25,
            },
        )
        if (await self._states()).get(file) is not active:
            raise AvatarError(
                "VTS expression state was not confirmed; inspect the model manually."
            )

    async def set_expression(self, expression: str) -> None:
        async with self._lock:
            self._require_ready()
            if expression == "neutral":
                await self._reset()
                return
            if expression not in ("happy", "sad"):
                raise AvatarError("Expression is not allowed.")
            if not self.settings.expected_model_id:
                raise AvatarError("Set expected_model_id before changing expressions.")
            hotkey_id = self.settings.expression_hotkeys.get(expression)
            if not hotkey_id:
                raise AvatarError("Expression has no operator-configured hotkey ID.")
            entries = await self._hotkeys()
            item = next(
                (entry for entry in entries if entry["hotkeyID"] == hotkey_id), None
            )
            if (
                item is None
                or item["type"] != "ToggleExpression"
                or not _file(item["file"])
            ):
                raise AvatarError(
                    "Configured hotkey is not a valid ToggleExpression on this model."
                )
            file = item["file"]
            states = await self._states()
            if file not in states:
                raise AvatarError(
                    "Mapped expression is missing from the current model."
                )
            await self._reset(keep=file)
            # Re-read: no toggles, no cached assumption about repeated calls.
            states = await self._states()
            if file not in states:
                raise AvatarError(
                    "Mapped expression disappeared from the current model."
                )
            if not states[file]:
                # Track before sending: a canceled request might already have executed.
                self._owned.add(file)
                await self._activate(file, True)

    async def _reset(self, keep=None):
        if not self._owned:
            return
        await self._current_model()
        states = await self._states()
        for file in sorted(self._owned - {keep}):
            if file not in states:
                raise AvatarError(
                    "Managed expression is missing; reset it manually in VTube Studio."
                )
            if states[file]:
                await self._activate(file, False)
            self._owned.remove(file)

    async def reset(self) -> None:
        async with self._lock:
            self._require_ready()
            await self._reset()

    async def _disconnect(self):
        ws, self._ws = self._ws, None
        self._ready = False
        if ws is not None:
            await _wait_cleanup(asyncio.create_task(self._close_socket(ws)))

    async def _close_socket(self, ws):
        try:
            async with asyncio.timeout(self.settings.request_timeout_seconds):
                await ws.close()
        except (OSError, WebSocketException, TimeoutError):
            raise AvatarError("VTS connection cleanup failed.") from None

    async def _close(self):
        async with self._lock:
            self._closed = True
            try:
                if self._owned and self._broken:
                    raise AvatarError(
                        "Expression result uncertain; reset manually in VTube Studio."
                    )
                if self._ready:
                    await self._reset()
            finally:
                await self._disconnect()

    async def aclose(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await _wait_cleanup(self._close_task)
