"""Read-only YouTube chat input. No operator capabilities or outbound messages."""

import asyncio
import logging
from collections import OrderedDict
from datetime import datetime, timezone

import httpx

from virtual_ai.integrations.youtube_auth import AuthError, prepare_access
from virtual_ai.schemas import ChatInput, Viewer

logger = logging.getLogger(__name__)
ENDPOINT = "https://www.googleapis.com/youtube/v3/liveChat/messages"


class ChatError(Exception):
    def __init__(self, message, *, reason="chat_error"):
        super().__init__(message)
        self.reason = reason


class YouTubeChat:
    def __init__(
        self,
        settings,
        *,
        max_age,
        transport=None,
        sleep=asyncio.sleep,
        accept_after=None,
        auth_session=None,
    ):
        self.settings = settings
        self.max_age = max_age
        self.transport = transport
        self.sleep = sleep
        self._seen = OrderedDict()
        self.accept_after = accept_after
        self.auth_session = auth_session
        self._started_at = None

    def _begin(self):
        self._started_at = datetime.now(timezone.utc).timestamp()
        self._seen.clear()

    def _consume(self, items, initial, submit):
        if not isinstance(items, list):
            raise ChatError("Invalid chat response.")
        messages = [self._message(event) for event in items]
        for message in messages:
            if message is None or message.message_id in self._seen:
                continue
            self._seen[message.message_id] = None
            if len(self._seen) > 4096:
                self._seen.popitem(last=False)
            if not initial:
                submit(message)

    def _message(self, event):
        if not isinstance(event, dict):
            raise ChatError("Invalid chat event.")
        snippet = event.get("snippet")
        if not isinstance(snippet, dict):
            raise ChatError("Invalid chat event.")
        if snippet.get("type") != "textMessageEvent":
            return None
        try:
            mid = event["id"]
            author = event["authorDetails"]["channelId"]
            text = snippet["textMessageDetails"]["messageText"]
            published = datetime.fromisoformat(
                snippet["publishedAt"].replace("Z", "+00:00")
            )
            valid = (
                all(isinstance(v, str) and v.strip() for v in (mid, author, text))
                and snippet["liveChatId"] == self.settings.live_chat_id
                and snippet["authorChannelId"] == author
                and published.tzinfo is not None
            )
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ChatError("Invalid text chat event.") from None
        if not valid:
            raise ChatError("Invalid text chat event.")
        age = (datetime.now(timezone.utc) - published).total_seconds()
        if self._started_at is not None and published.timestamp() < self._started_at:
            return None
        if (
            self.accept_after is not None
            and published.timestamp() < self.accept_after()
        ):
            return None
        if not -30 <= age <= self.max_age:
            return None
        return ChatInput(Viewer("youtube", author), text, mid, published.timestamp())

    async def run(self, submit):
        if not self.settings.enabled:
            return
        self._begin()
        try:
            token, session = await prepare_access(
                self.settings, transport=self.transport, session=self.auth_session
            )
        except AuthError:
            raise ChatError(
                "YouTube authentication failed; check YOUTUBE_ACCESS_TOKEN in manual mode or run OAuth login explicitly."
            ) from None
        page_token = None
        interval = 1.0
        failures = 0
        async with httpx.AsyncClient(
            transport=self.transport,
            timeout=10,
            follow_redirects=False,
        ) as client:
            while True:
                if session is not None:
                    try:
                        token = await session.access_token()
                    except AuthError:
                        raise ChatError(
                            "YouTube token refresh failed; reauthenticate explicitly."
                        ) from None
                params = {
                    "liveChatId": self.settings.live_chat_id,
                    "part": "id,snippet,authorDetails",
                    "maxResults": 200,
                }
                if page_token is not None:
                    params["pageToken"] = page_token
                try:
                    response = await client.get(
                        ENDPOINT,
                        params=params,
                        headers={"Authorization": f"Bearer {token}"},
                    )
                except httpx.TransportError:
                    response = None
                if response is None or (response.status_code >= 500):
                    failures += 1
                    if failures > 3:
                        raise ChatError(
                            "Chat retry limit reached.", reason="retry_exhausted"
                        )
                    delay = max(interval, 2**failures)
                    if response is not None:
                        retry_after = response.headers.get("Retry-After", "")
                        if retry_after.isdecimal():
                            delay = max(delay, int(retry_after))
                    await self.sleep(delay)
                    continue
                if response.status_code != 200:
                    if response.status_code == 400:
                        raise ChatError(
                            "Invalid request/cursor; inspect settings then explicitly restart to rebaseline.",
                            reason="rebaseline_required",
                        )
                    if response.status_code == 429:
                        raise ChatError(
                            "Chat quota/rate limit reached; reception stopped.",
                            reason="quota_or_rate_limit",
                        )
                    if response.status_code == 401 and session is not None:
                        try:
                            session.invalidate()
                        except AuthError:
                            pass
                    # Never log server bodies, request URLs, tokens or viewer text.
                    raise ChatError(
                        "Chat unavailable; check credentials, quota and live chat ID.",
                        reason="authentication"
                        if response.status_code == 401
                        else "ended_or_permission",
                    )
                try:
                    data = response.json()
                    items = data["items"]
                    next_token = data["nextPageToken"]
                    millis = data["pollingIntervalMillis"]
                    if (
                        not isinstance(items, list)
                        or not isinstance(next_token, str)
                        or not next_token
                        or type(millis) is not int
                        or millis <= 0
                    ):
                        raise ValueError
                except (ValueError, KeyError, TypeError):
                    raise ChatError("Invalid chat response.") from None
                # First page is historical: establish a cursor without speaking backlog.
                self._consume(items, page_token is None, submit)
                if page_token is None:
                    logger.info(
                        "youtube_status=connected transport=rest history=discarded"
                    )
                page_token = next_token
                interval = max(1.0, millis / 1000)
                if data.get("offlineAt") or any(
                    event["snippet"].get("type") == "chatEndedEvent" for event in items
                ):
                    return
                await self.sleep(interval)


async def receive_chat(adapter, submit, on_stopped=None):
    """A chat failure must not terminate local audio or operator controls."""
    try:
        await adapter.run(submit)
        reason = "ended"
    except ChatError as exc:
        reason = exc.reason
        logger.warning(
            "youtube_status=stopped reason=%s recovery=manual_restart_or_explicit_rebaseline oauth_recovery=explicit_login",
            reason,
        )
    if on_stopped is not None:
        on_stopped(reason)
