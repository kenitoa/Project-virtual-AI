"""Read-only official CHZZK chat, Engine.IO 3 / Socket.IO text events only."""

import asyncio
import hashlib
import json
import logging
import time
from collections import OrderedDict
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from virtual_ai.integrations.chzzk_auth import ChzzkSession
from virtual_ai.integrations.youtube import ChatError
from virtual_ai.integrations.youtube_auth import AuthError
from virtual_ai.schemas import ChatInput, Viewer

logger = logging.getLogger(__name__)
# WebSocket diagnostics can contain the session URL's authorization query.
wire_logger = logging.Logger("chzzk.transport", level=logging.CRITICAL + 1)
wire_logger.addHandler(logging.NullHandler())


def socket_url(value):
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or not parsed.hostname.endswith(".nchat.naver.com")
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
            or parsed.fragment
            or parsed.path not in ("", "/", "/socket.io/")
        ):
            raise ValueError
        query = dict(parse_qsl(parsed.query, strict_parsing=True))
        if set(query) != {"auth"} or not query["auth"]:
            raise ValueError
        return urlunsplit(
            (
                "wss",
                parsed.netloc,
                "/socket.io/",
                urlencode({**query, "EIO": "3", "transport": "websocket"}),
                "",
            )
        )
    except (TypeError, ValueError, AttributeError):
        raise ChatError(
            "Invalid CHZZK session URL.", reason="invalid_response"
        ) from None


class ChzzkChat:
    def __init__(
        self,
        settings,
        *,
        max_age,
        accept_after=None,
        session=None,
        connector=connect,
        now=time.time,
    ):
        self.settings = settings
        self.max_age = max_age
        self.accept_after = accept_after
        self.session = session if session is not None else ChzzkSession()
        self.connector = connector
        self.now = now
        self._started_at = None
        self._seen = OrderedDict()
        self.connected = asyncio.Event()

    def message(self, data):
        try:
            if (
                not isinstance(data, dict)
                or data["channelId"] != self.settings.channel_id
            ):
                raise ValueError
            sender, content, millis = (
                data["senderChannelId"],
                data["content"],
                data["messageTime"],
            )
            if (
                not isinstance(sender, str)
                or not sender.strip()
                or len(sender) > 128
                or not isinstance(content, str)
                or not content.strip()
                or len(content) > 4000
                or type(millis) is not int
                or not 0 < millis < 10**15
            ):
                raise ValueError
            published = millis / 1000
        except (KeyError, TypeError, ValueError):
            raise ChatError(
                "Invalid CHZZK chat event.", reason="invalid_response"
            ) from None
        if (
            not -30 <= self.now() - published <= self.max_age
            or self._started_at is not None
            and published <= self._started_at
            or self.accept_after is not None
            and published <= self.accept_after()
        ):
            return None
        # The official event has no message ID. Bound deduplication by its stable fields.
        mid = hashlib.sha256(
            json.dumps(
                [data["channelId"], sender, millis, content], ensure_ascii=True
            ).encode()
        ).hexdigest()
        if mid in self._seen:
            return None
        self._seen[mid] = None
        if len(self._seen) > 4096:
            self._seen.popitem(last=False)
        return ChatInput(Viewer("chzzk", sender), content, mid, published)

    async def _connection(self, submit):
        await self.session.verify(self.settings.channel_id)
        data = await self.session.api("GET", "/open/v1/sessions/auth")
        if not isinstance(data, dict):
            raise ChatError("Invalid CHZZK session response.")
        url = socket_url(data.get("url"))
        async with self.connector(
            url,
            open_timeout=10,
            close_timeout=3,
            ping_interval=None,
            max_size=262144,
            max_queue=16,
            logger=wire_logger,
        ) as ws:
            raw = await asyncio.wait_for(ws.recv(), 10)
            if not isinstance(raw, str) or not raw.startswith("0"):
                raise ChatError("CHZZK handshake missing.")
            hello = json.loads(raw[1:])
            interval, timeout = (
                hello["pingInterval"] / 1000,
                hello["pingTimeout"] / 1000,
            )
            if not 1 <= interval <= 120 or not 1 <= timeout <= 120:
                raise ChatError("Invalid CHZZK heartbeat.")
            loop = asyncio.get_running_loop()
            ping_at = loop.time() + interval
            pong_at = None
            deadline = loop.time() + 20
            rotate_at = loop.time() + 240
            subscribed = False
            session_key = None
            while loop.time() < rotate_at:
                now = loop.time()
                if pong_at is not None and now >= pong_at:
                    raise TimeoutError
                if not subscribed and now >= deadline:
                    raise ChatError(
                        "CHZZK subscription not confirmed.",
                        reason="subscription_timeout",
                    )
                if pong_at is None and now >= ping_at:
                    await ws.send("2")
                    pong_at = now + timeout
                wake = min(
                    rotate_at,
                    pong_at if pong_at is not None else ping_at,
                    rotate_at if subscribed else deadline,
                )
                try:
                    raw = await asyncio.wait_for(
                        ws.recv(), max(0.01, wake - loop.time())
                    )
                except TimeoutError:
                    continue
                if not isinstance(raw, str):
                    raise ChatError("Unsupported CHZZK binary event.")
                if raw == "3":
                    pong_at = None
                    ping_at = loop.time() + interval
                    continue
                if raw == "2":
                    await ws.send("3")
                    continue
                if raw in ("1", "41"):
                    raise ChatError("CHZZK session ended.", reason="ended")
                if raw == "40":
                    continue
                if not raw.startswith("42"):
                    raise ChatError(
                        "Unsupported CHZZK packet.", reason="invalid_response"
                    )
                event = json.loads(raw[2:])
                if not isinstance(event, list) or len(event) != 2:
                    raise ValueError
                kind, body = event
                if isinstance(body, str):
                    body = json.loads(body)
                if not isinstance(body, dict):
                    raise ValueError
                if kind == "SYSTEM":
                    details = body.get("data", {})
                    if body.get("type") == "connected":
                        if session_key is not None:
                            raise ValueError
                        session_key = details["sessionKey"]
                        if not isinstance(session_key, str) or not session_key:
                            raise ValueError
                        await self.session.api(
                            "POST",
                            "/open/v1/sessions/events/subscribe/chat",
                            params={"sessionKey": session_key},
                        )
                    elif body.get("type") == "subscribed":
                        if (
                            session_key is None
                            or details.get("eventType") != "CHAT"
                            or details.get("channelId") != self.settings.channel_id
                        ):
                            raise ChatError("CHZZK subscription identity mismatch.")
                        subscribed = True
                        self.connected.set()
                        logger.info("chzzk_status=connected transport=websocket")
                    elif body.get("type") in ("revoked", "unsubscribed"):
                        raise ChatError(
                            "CHZZK access revoked.", reason="authorization_revoked"
                        )
                elif kind == "CHAT" and subscribed:
                    item = self.message(body)
                    if item is not None:
                        submit(item)

    async def run(self, submit):
        if not self.settings.enabled:
            return
        self._started_at = self.now()
        self._seen.clear()
        failures = 0
        while True:
            self.connected.clear()
            try:
                await self._connection(submit)
                failures = 0  # Regular rotation; deduplication stays intact.
            except AuthError:
                raise ChatError(
                    "CHZZK authentication failed; run login.", reason="authentication"
                ) from None
            except (ValueError, KeyError, TypeError, AttributeError):
                raise ChatError(
                    "Invalid CHZZK response.", reason="invalid_response"
                ) from None
            except (OSError, TimeoutError, WebSocketException):
                failures += 1
                if failures > 3:
                    raise ChatError(
                        "CHZZK reconnect limit reached.", reason="retry_exhausted"
                    ) from None
                await asyncio.sleep(2**failures)


async def receive_chzzk(adapter, submit, on_stopped=None):
    try:
        await adapter.run(submit)
        reason = "ended"
    except ChatError as exc:
        reason = exc.reason
        logger.warning(
            "chzzk_status=stopped reason=%s recovery=explicit_restart", reason
        )
    if on_stopped is not None:
        on_stopped(reason)
