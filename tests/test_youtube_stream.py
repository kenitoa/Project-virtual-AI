import asyncio
from datetime import datetime, timedelta, timezone

import grpc
import pytest

from virtual_ai.config import YouTubeSettings
from virtual_ai.integrations import youtube_stream as streaming
from virtual_ai.integrations._youtube_proto import stream_list_pb2 as pb
from virtual_ai.integrations._youtube_proto import stream_list_pb2_grpc as rpc
from virtual_ai.integrations.youtube import ChatError, receive_chat


def message(mid="new", *, age=-1, text="/panic", chat="chat"):
    return pb.LiveChatMessage(
        id=mid,
        author_details=pb.LiveChatMessageAuthorDetails(
            channel_id="viewer", is_chat_owner=True
        ),
        snippet=pb.LiveChatMessageSnippet(
            type=streaming.KINDS.TEXT_MESSAGE_EVENT,
            live_chat_id=chat,
            author_channel_id="viewer",
            published_at=(
                datetime.now(timezone.utc) - timedelta(seconds=age)
            ).isoformat(),
            text_message_details=pb.LiveChatTextMessageDetails(message_text=text),
        ),
    )


def page(*items, cursor="private-cursor", ended=False):
    return pb.LiveChatMessageListResponse(
        items=items,
        next_page_token=cursor,
        offline_at="2026-09-23T00:00:00Z" if ended else "",
    )


class Failure(grpc.RpcError):
    def __init__(self, code):
        self.status = code

    def code(self):
        return self.status

    def __str__(self):
        return "private-token private-cursor viewer text"


class Source:
    def __init__(self, connections):
        self.connections = connections
        self.requests = []
        self.closed = 0
        self.sleeping = asyncio.Event()

    async def __call__(self, request, token):
        self.requests.append((request, token))
        try:
            for value in self.connections.pop(0):
                if value == "wait":
                    self.sleeping.set()
                    await asyncio.Event().wait()
                elif isinstance(value, Exception):
                    raise value
                else:
                    yield value
        finally:
            self.closed += 1


@pytest.fixture(autouse=True)
def manual_token(monkeypatch):
    monkeypatch.setenv("YOUTUBE_ACCESS_TOKEN", "private-token")


async def no_sleep(_):
    pass


def adapter(source, **kwargs):
    return streaming.YouTubeStream(
        YouTubeSettings(True, "chat", transport="stream"),
        max_age=30,
        stream_factory=source,
        sleep=no_sleep,
        **kwargs,
    )


def test_history_across_frames_reconnect_cursor_dedupe_and_commands_stay_data():
    async def run():
        source = Source(
            [
                [
                    page(message("first"), cursor="c1"),
                    page(
                        message("old-second-frame", age=10),
                        message("fresh"),
                        cursor="c2",
                    ),
                    Failure(grpc.StatusCode.UNAVAILABLE),
                ],
                [
                    page(
                        message("fresh"),
                        message("after-reconnect"),
                        cursor="c3",
                        ended=True,
                    )
                ],
            ]
        )
        received = []
        await adapter(source).run(received.append)
        assert [m.message_id for m in received] == ["fresh", "after-reconnect"]
        assert all(
            m.text == "/panic" and m.viewer.platform == "youtube" for m in received
        )
        assert [r.page_token for r, _ in source.requests] == ["", "c2"]
        assert not source.requests[0][0].HasField("page_token")
        assert source.closed == 2

    asyncio.run(run())


@pytest.mark.parametrize(
    "status,reason",
    [
        (grpc.StatusCode.INVALID_ARGUMENT, "rebaseline_required"),
        (grpc.StatusCode.UNAUTHENTICATED, "authentication"),
        (grpc.StatusCode.PERMISSION_DENIED, "ended_or_permission"),
        (grpc.StatusCode.RESOURCE_EXHAUSTED, "quota_or_rate_limit"),
        (grpc.StatusCode.FAILED_PRECONDITION, "ended_or_permission"),
        (grpc.StatusCode.NOT_FOUND, "ended_or_permission"),
        (grpc.StatusCode.INTERNAL, "transport_error"),
    ],
)
def test_terminal_errors_never_reset_or_retry(status, reason, caplog):
    async def run():
        source = Source([[page(), Failure(status)]])
        stopped = []
        await receive_chat(
            adapter(source), lambda _: pytest.fail("submit"), stopped.append
        )
        assert stopped == [reason] and len(source.requests) == 1
        for secret in ("private-token", "private-cursor", "viewer text"):
            assert secret not in caplog.text

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["unavailable", "deadline", "eof", "flapping"])
def test_retry_budget_even_if_some_responses_succeeded(kind):
    async def run():
        connections = []
        for _ in range(4):
            steps = [page()] if kind == "flapping" else []
            if kind != "eof":
                steps.append(
                    Failure(
                        grpc.StatusCode.DEADLINE_EXCEEDED
                        if kind == "deadline"
                        else grpc.StatusCode.UNAVAILABLE
                    )
                )
            connections.append(steps)
        source = Source(connections)
        with pytest.raises(ChatError, match="retry limit"):
            await adapter(source).run(lambda _: pytest.fail("submit"))
        assert len(source.requests) == 4 and source.closed == 4
        if kind == "flapping":
            assert all(r.page_token == "private-cursor" for r, _ in source.requests[1:])

    asyncio.run(run())


def test_missing_cursor_requires_explicit_new_baseline():
    async def run():
        source = Source([[page(cursor="")]])
        with pytest.raises(ChatError) as error:
            await adapter(source).run(lambda _: pytest.fail("submit"))
        assert error.value.reason == "rebaseline_required"
        assert len(source.requests) == 1

    asyncio.run(run())


def test_broadcast_end_without_cursor_does_not_reconnect():
    async def run():
        source = Source([[page(cursor="", ended=True)]])
        await adapter(source).run(lambda _: pytest.fail("submit"))
        assert len(source.requests) == 1

    asyncio.run(run())


def test_rotation_keeps_cursor_and_suppresses_duplicate():
    async def run():
        source = Source(
            [
                [page(message("history")), "wait"],
                [page(message("history"), message("after-rotation"), ended=True)],
            ]
        )
        got = []
        await adapter(source, rotation_seconds=0.02).run(got.append)
        assert [m.message_id for m in got] == ["after-rotation"]
        assert source.requests[1][0].page_token == "private-cursor"
        assert source.closed == 2

    asyncio.run(run())


def test_no_first_response_is_bounded_timeout():
    async def run():
        source = Source([["wait"] for _ in range(4)])
        with pytest.raises(ChatError, match="retry limit"):
            await adapter(source, rotation_seconds=0.01).run(lambda _: None)
        assert source.closed == 4

    asyncio.run(run())


def test_cancellation_closes_waiting_stream_without_reconnect():
    async def run():
        source = Source([[page(), "wait"]])
        task = asyncio.create_task(adapter(source).run(lambda _: None))
        await source.sleeping.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(source.requests) == 1 and source.closed == 1

    asyncio.run(run())


def test_disabled_transport_does_not_authenticate_or_connect(monkeypatch):
    monkeypatch.delenv("YOUTUBE_ACCESS_TOKEN")
    obj = streaming.YouTubeStream(
        YouTubeSettings(), max_age=30, stream_factory=lambda *_: pytest.fail("network")
    )
    asyncio.run(obj.run(lambda _: pytest.fail("submit")))


def test_invalid_author_chat_or_text_fails_closed():
    async def run():
        source = Source([[page(), page(message(chat="wrong"))]])
        with pytest.raises(ChatError):
            await adapter(source).run(lambda _: pytest.fail("submit"))

    asyncio.run(run())


def test_non_text_events_are_never_submitted():
    async def run():
        gift = pb.LiveChatMessage(
            snippet=pb.LiveChatMessageSnippet(type=streaming.KINDS.SUPER_CHAT_EVENT)
        )
        source = Source([[page(), page(gift, ended=True)]])
        await adapter(source).run(lambda _: pytest.fail("submit"))

    asyncio.run(run())


def test_oauth_rotation_refreshes_metadata_without_resetting_cursor():
    class Auth:
        count = 0

        async def resolve(self, *args):
            pass

        async def access_token(self):
            self.count += 1
            return "refreshed" if self.count >= 3 else "initial"

        def stream_window(self):
            return 0.02

    async def run():
        source = Source([[page(), "wait"], [page(message("new"), ended=True)]])
        obj = streaming.YouTubeStream(
            YouTubeSettings(True, "chat", "oauth", "channel", "broadcast", "stream"),
            max_age=30,
            stream_factory=source,
            auth_session=Auth(),
        )
        got = []
        await obj.run(got.append)
        assert [token for _, token in source.requests] == ["initial", "refreshed"]
        assert source.requests[1][0].page_token == "private-cursor"
        assert len(got) == 1

    asyncio.run(run())


def test_stream_pause_cutoff_keeps_cursor_and_drops_delayed_message():
    async def run():
        cutoff = [0]

        async def source(request, token):
            yield page()
            old = message("during-pause", age=0)
            cutoff[0] = datetime.now(timezone.utc).timestamp()
            yield page(old, message("fresh"), ended=True)

        got = []
        await adapter(source, accept_after=lambda: cutoff[0]).run(got.append)
        assert [m.message_id for m in got] == ["fresh"]

    asyncio.run(run())


def test_actual_grpc_serialization_resume_and_channel_close(monkeypatch):
    async def run():
        requests, channels, received = [], [], []

        class Service(rpc.V3DataLiveChatMessageServiceServicer):
            async def StreamList(self, request, context):
                requests.append(request)
                assert (
                    dict(context.invocation_metadata())["authorization"]
                    == "Bearer private-token"
                )
                assert list(request.part) == ["id", "snippet", "authorDetails"]
                if len(requests) == 1:
                    yield page(cursor="wire1")
                    yield page(message("wire-new"), cursor="wire2")
                    await context.abort(
                        grpc.StatusCode.UNAVAILABLE, "private server details"
                    )
                else:
                    yield page(message("wire-new"), message("wire-next"), ended=True)

        server = grpc.aio.server()
        rpc.add_V3DataLiveChatMessageServiceServicer_to_server(Service(), server)
        port = server.add_insecure_port("127.0.0.1:0")
        await server.start()

        def channel(target, credentials, **kwargs):
            assert target == streaming.TARGET and credentials is not None
            result = grpc.aio.insecure_channel(f"127.0.0.1:{port}", **kwargs)
            channels.append(result)
            return result

        monkeypatch.setattr(grpc.aio, "secure_channel", channel)
        try:
            obj = streaming.YouTubeStream(
                YouTubeSettings(True, "chat", transport="stream"),
                max_age=30,
                sleep=no_sleep,
            )
            await asyncio.wait_for(obj.run(received.append), 5)
            assert [m.message_id for m in received] == ["wire-new", "wire-next"]
            assert requests[1].page_token == "wire2"
            assert all(
                c.get_state() == grpc.ChannelConnectivity.SHUTDOWN for c in channels
            )
        finally:
            await server.stop(None)

    asyncio.run(run())
