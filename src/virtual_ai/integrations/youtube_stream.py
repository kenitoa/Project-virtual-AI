"""Official protobuf/gRPC streamList transport with bounded cursor recovery."""

import asyncio
import logging
from contextlib import aclosing

import grpc

from virtual_ai.integrations._youtube_proto import stream_list_pb2 as pb
from virtual_ai.integrations._youtube_proto import stream_list_pb2_grpc as rpc
from virtual_ai.integrations.youtube import ChatError, YouTubeChat
from virtual_ai.integrations.youtube_auth import AuthError, prepare_access

logger = logging.getLogger(__name__)
TARGET = "dns:///youtube.googleapis.com:443"
KINDS = pb.LiveChatMessageSnippet.TypeWrapper


async def stream_pages(request, token):
    """TLS and fixed Google endpoint; never accept credentials in command arguments."""
    async with grpc.aio.secure_channel(
        TARGET,
        grpc.ssl_channel_credentials(),
        options=(
            ("grpc.enable_retries", 0),
            ("grpc.max_receive_message_length", 4 * 1024 * 1024),
        ),
    ) as channel:
        call = rpc.V3DataLiveChatMessageServiceStub(channel).StreamList(
            request,
            metadata=(("authorization", "Bearer " + token),),
            wait_for_ready=False,
        )
        try:
            async for response in call:
                yield response
        finally:
            call.cancel()


def events(response):
    """Map only fields needed by the existing validation and ChatInput path."""
    result = []
    for item in response.items:
        snippet = item.snippet
        if snippet.type == KINDS.TEXT_MESSAGE_EVENT:
            result.append(
                {
                    "id": item.id,
                    "authorDetails": {"channelId": item.author_details.channel_id},
                    "snippet": {
                        "type": "textMessageEvent",
                        "liveChatId": snippet.live_chat_id,
                        "authorChannelId": snippet.author_channel_id,
                        "publishedAt": snippet.published_at,
                        "textMessageDetails": {
                            "messageText": snippet.text_message_details.message_text
                        },
                    },
                }
            )
    return result


class YouTubeStream(YouTubeChat):
    def __init__(
        self, *args, stream_factory=stream_pages, rotation_seconds=240, **kwargs
    ):
        super().__init__(*args, **kwargs)
        self.stream_factory = stream_factory
        self.rotation_seconds = rotation_seconds
        self._cursor = None

    async def run(self, submit):
        if not self.settings.enabled:
            return
        self._begin()
        self._cursor = None
        try:
            token, session = await prepare_access(
                self.settings, transport=self.transport, session=self.auth_session
            )
        except AuthError:
            raise ChatError(
                "YouTube authentication failed; reauthenticate explicitly.",
                reason="authentication",
            ) from None
        failures = 0
        while True:
            window = self.rotation_seconds
            if session is not None:
                try:
                    token = await session.access_token()
                    window = min(window, session.stream_window())
                except AuthError:
                    raise ChatError(
                        "YouTube refresh failed; reauthenticate explicitly.",
                        reason="authentication",
                    ) from None
            request = pb.LiveChatMessageListRequest(
                live_chat_id=self.settings.live_chat_id,
                part=["id", "snippet", "authorDetails"],
            )
            if self._cursor is not None:
                request.page_token = self._cursor
            saw_response = False
            try:
                # Proactive rotation refreshes long-lived OAuth streams even in a quiet chat.
                async with asyncio.timeout(window):
                    async with aclosing(
                        self.stream_factory(request, token)
                    ) as responses:
                        async for response in responses:
                            if not isinstance(response, pb.LiveChatMessageListResponse):
                                raise ChatError("Invalid stream response.")
                            ended = bool(response.offline_at) or any(
                                i.snippet.type == KINDS.CHAT_ENDED_EVENT
                                for i in response.items
                            )
                            if not response.next_page_token and not ended:
                                raise ChatError(
                                    "Stream cursor missing; explicit rebaseline required.",
                                    reason="rebaseline_required",
                                )
                            self._consume(
                                events(response), self._cursor is None, submit
                            )
                            if not saw_response:
                                logger.info("youtube_status=connected transport=stream")
                            if response.next_page_token:
                                self._cursor = response.next_page_token
                            saw_response = True
                            if ended:
                                return
                # Unexpected EOF is a disconnect, not permission to reset the cursor.
            except TimeoutError:
                if saw_response:
                    logger.info("youtube_status=rotating cursor=preserved")
                    continue
            except grpc.RpcError as exc:
                code = exc.code()
                if code == grpc.StatusCode.UNAUTHENTICATED:
                    if session is not None:
                        try:
                            session.invalidate()
                        except AuthError:
                            pass
                    raise ChatError(
                        "YouTube authorization failed; run login explicitly.",
                        reason="authentication",
                    ) from None
                if code == grpc.StatusCode.INVALID_ARGUMENT:
                    raise ChatError(
                        "Invalid request/cursor; check settings then explicitly restart to rebaseline.",
                        reason="rebaseline_required",
                    ) from None
                if code == grpc.StatusCode.RESOURCE_EXHAUSTED:
                    raise ChatError(
                        "YouTube quota/rate limit reached; reception stopped.",
                        reason="quota_or_rate_limit",
                    ) from None
                if code in (
                    grpc.StatusCode.PERMISSION_DENIED,
                    grpc.StatusCode.FAILED_PRECONDITION,
                    grpc.StatusCode.NOT_FOUND,
                ):
                    raise ChatError(
                        "Chat ended, disabled, missing or access denied; reception stopped.",
                        reason="ended_or_permission",
                    ) from None
                if code not in (
                    grpc.StatusCode.UNAVAILABLE,
                    grpc.StatusCode.DEADLINE_EXCEEDED,
                ):
                    raise ChatError(
                        "Stream failed; inspect configuration before restarting.",
                        reason="transport_error",
                    ) from None
            failures += 1
            if failures > 3:
                raise ChatError(
                    "Stream retry limit reached; restart explicitly.",
                    reason="retry_exhausted",
                )
            logger.warning(
                "youtube_status=reconnecting attempt=%d cursor=preserved", failures
            )
            await self.sleep(2**failures)
