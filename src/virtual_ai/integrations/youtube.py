"""Read-only YouTube chat input. No operator capabilities or outbound messages."""

import asyncio
import logging
import os
from collections import OrderedDict
from datetime import datetime, timezone

import httpx

from virtual_ai.schemas import ChatInput, Viewer

logger = logging.getLogger(__name__)
ENDPOINT = "https://www.googleapis.com/youtube/v3/liveChat/messages"


class ChatError(Exception):
    pass


class YouTubeChat:
    def __init__(self, settings, *, max_age, transport=None, sleep=asyncio.sleep):
        self.settings = settings
        self.max_age = max_age
        self.transport = transport
        self.sleep = sleep
        self._seen = OrderedDict()

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
        if not -30 <= age <= self.max_age:
            return None
        return ChatInput(Viewer("youtube", author), text, mid)

    async def run(self, submit):
        if not self.settings.enabled:
            return
        token = os.environ.get("YOUTUBE_ACCESS_TOKEN", "").strip()
        if not token or any(c.isspace() for c in token):
            raise ChatError("Set YOUTUBE_ACCESS_TOKEN before enabling chat.")
        page_token = None
        interval = 1.0
        failures = 0
        async with httpx.AsyncClient(
            transport=self.transport,
            timeout=10,
            follow_redirects=False,
            headers={"Authorization": f"Bearer {token}"},
        ) as client:
            while True:
                params = {
                    "liveChatId": self.settings.live_chat_id,
                    "part": "id,snippet,authorDetails",
                    "maxResults": 200,
                }
                if page_token is not None:
                    params["pageToken"] = page_token
                try:
                    response = await client.get(ENDPOINT, params=params)
                except httpx.TransportError:
                    response = None
                if (
                    response is None
                    or response.status_code == 429
                    or (response.status_code >= 500)
                ):
                    failures += 1
                    if failures > 3:
                        raise ChatError("Chat retry limit reached.")
                    delay = max(interval, 2**failures)
                    if response is not None:
                        retry_after = response.headers.get("Retry-After", "")
                        if retry_after.isdecimal():
                            delay = max(delay, int(retry_after))
                    await self.sleep(delay)
                    continue
                if response.status_code != 200:
                    # Never log server bodies, request URLs, tokens or viewer text.
                    raise ChatError(
                        "Chat unavailable; check credentials, quota and live chat ID."
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
                    messages = [self._message(event) for event in items]
                except (ValueError, KeyError, TypeError):
                    raise ChatError("Invalid chat response.") from None
                # First page is historical: establish a cursor without speaking backlog.
                for message in messages:
                    if message is None or message.message_id in self._seen:
                        continue
                    self._seen[message.message_id] = None
                    if len(self._seen) > 4096:
                        self._seen.popitem(last=False)
                    if page_token is not None:
                        submit(message)
                page_token = next_token
                failures = 0
                interval = max(1.0, millis / 1000)
                if data.get("offlineAt") or any(
                    event["snippet"].get("type") == "chatEndedEvent" for event in items
                ):
                    return
                await self.sleep(interval)


async def receive_chat(adapter, submit):
    """A chat failure must not terminate local audio or operator controls."""
    try:
        await adapter.run(submit)
    except ChatError:
        logger.warning("youtube_status=stopped recovery=manual")
