import asyncio
import json
from argparse import Namespace
from contextlib import suppress
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from virtual_ai import app as app_module
from virtual_ai.app import Application
from virtual_ai.config import AudioSettings, Settings, TTSSettings, load_config
from virtual_ai.llm.base import LLMError
from virtual_ai.llm.koboldcpp import KoboldCppClient
from virtual_ai.schemas import ChatInput, Viewer

ROOT = Path(__file__).resolve().parents[1]


class ControlledLLM:
    def __init__(self, late_result=False):
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = []
        self.closed = False
        self.late_result = late_result

    async def generate(self, messages):
        text = messages[-1]["content"]
        self.calls.append(text)
        if len(self.calls) == 1:
            self.started.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                if not self.late_result:
                    raise
        return text

    async def aclose(self):
        self.closed = True


def item(text):
    return ChatInput(Viewer("viewer-chat", "viewer"), text, text)


@pytest.mark.parametrize("late_result", [False, True])
def test_stop_discards_active_and_queued_responses_then_recovers(late_result):
    async def run():
        settings, character = load_config(ROOT / "configs/app.example.yaml")
        llm = ControlledLLM(late_result)
        output = []
        emitted = asyncio.Event()

        def emit(text):
            output.append(text)
            emitted.set()

        app = Application(settings, character, llm, emit)
        worker = asyncio.create_task(app.run())
        try:
            assert app.submit(item("old"))
            await asyncio.wait_for(llm.started.wait(), 2)
            assert app.submit(item("queued"))
            await app.stop()
            assert len(app.queue) == 0
            assert app.submit(item("new"))
            await asyncio.wait_for(emitted.wait(), 2)
            assert llm.cancelled.is_set()
            assert llm.calls == ["old", "new"]
            assert output == ["new"]
            assert [m["content"] for m in app.history.messages(item("new").viewer)] == [
                "new",
                "new",
            ]
            assert not worker.done()
            assert app._generation is None
        finally:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker

    asyncio.run(run())


def test_viewer_stop_is_only_conversation_data():
    async def run():
        settings, character = load_config(ROOT / "configs/app.example.yaml")
        llm = ControlledLLM()
        llm.release.set()
        output = []
        app = Application(settings, character, llm, output.append)
        app.submit(item("/stop"))
        app.submit(item("next"))
        await app.process_next()
        await app.process_next()
        assert llm.calls == ["/stop", "next"]
        assert output == ["/stop", "next"]
        assert app._epoch == 0

    asyncio.run(run())


@pytest.mark.parametrize("stop_first", [False, True])
@pytest.mark.parametrize("late_result", [False, True])
def test_worker_cancellation_propagates(stop_first, late_result):
    async def run():
        settings, character = load_config(ROOT / "configs/app.example.yaml")
        llm = ControlledLLM(late_result)
        output = []
        app = Application(settings, character, llm, output.append)
        app.submit(item("old"))
        worker = asyncio.create_task(app.run())
        await asyncio.wait_for(llm.started.wait(), 2)
        if stop_first:
            await app.stop()
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(worker, 2)
        assert llm.cancelled.is_set()
        assert output == []
        assert app._generation is None

    asyncio.run(run())


def test_console_quit_during_generation_closes_client_and_worker(monkeypatch):
    async def run():
        llm = ControlledLLM()
        monkeypatch.setattr(app_module, "FakeLLM", lambda: llm)
        inputs = iter(["old", "/quit"])

        async def controlled_input(function, *args):
            command = next(inputs)
            if command == "/quit":
                await llm.started.wait()
            return command

        monkeypatch.setattr(asyncio, "to_thread", controlled_input)
        before = asyncio.all_tasks()
        await asyncio.wait_for(
            app_module.run_cli(
                Namespace(
                    config=str(ROOT / "configs/app.example.yaml"),
                    backend="mock",
                    once=None,
                )
            ),
            2,
        )
        assert llm.cancelled.is_set()
        assert llm.closed
        assert asyncio.all_tasks() == before

    asyncio.run(run())


def test_http_cancellation_is_not_retried_or_wrapped():
    async def run():
        started = asyncio.Event()
        cleaned = asyncio.Event()
        calls = []

        async def handler(request):
            calls.append(request)
            if len(calls) == 1:
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cleaned.set()
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "recovered"}}]}
            )

        client = KoboldCppClient(Settings(retries=3), httpx.MockTransport(handler))
        try:
            generation = asyncio.create_task(client.generate([]))
            await asyncio.wait_for(started.wait(), 2)
            generation.cancel()
            with pytest.raises(asyncio.CancelledError):
                await generation
            assert cleaned.is_set()
            assert len(calls) == 1
            with pytest.raises(LLMError, match="cleanup is unconfirmed"):
                await client.generate([])
            assert len(calls) == 1
        finally:
            await client.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("abort_success", [True, False])
def test_stop_audio_is_immediate_and_shutdown_joins_server_cleanup(abort_success):
    async def run():
        started, aborting, release = (asyncio.Event() for _ in range(3))
        aborts, output = [], []

        async def handler(request):
            if request.url.path == "/api/extra/abort":
                aborts.append(json.loads(request.content)["genkey"])
                aborting.set()
                await release.wait()
                return httpx.Response(
                    200, json={"success": abort_success, "done": True}
                )
            if request.url.path == "/api/extra/perf":
                return httpx.Response(200, json={"idle": 1, "queue": 0})
            started.set()
            await asyncio.Event().wait()

        class Player:
            stopped = False

            async def stop(self):
                self.stopped = True

        settings, character = load_config(ROOT / "configs/app.example.yaml")
        settings = replace(
            settings,
            server_abort_enabled=True,
            audio=AudioSettings(enabled=True),
            tts=TTSSettings(enabled=True, ref_audio_path="unused.wav"),
        )
        llm = KoboldCppClient(settings, httpx.MockTransport(handler))
        player = Player()
        app = Application(
            settings, character, llm, output.append, tts=object(), player=player
        )
        app.submit(item("old"))
        processing = asyncio.create_task(app.process_next())
        await started.wait()
        app.submit(item("queued"))
        await app.stop()
        assert player.stopped and not app.queue
        await aborting.wait()
        await app.stop()
        closing = asyncio.create_task(app.shutdown())
        await asyncio.sleep(0)
        assert not closing.done()
        assert len(aborts) == 1
        release.set()
        await asyncio.wait_for(closing, 1)
        await processing
        await llm.aclose()
        assert output == []
        assert app.history.messages(item("old").viewer) == []
        assert app._generation is None

    asyncio.run(run())


def test_mock_with_server_abort_enabled_never_constructs_http_client(monkeypatch):
    settings, character = load_config(ROOT / "configs/app.example.yaml")
    settings = replace(settings, backend="mock", server_abort_enabled=True)
    monkeypatch.setattr(app_module, "load_config", lambda _: (settings, character))

    def unexpected(*args, **kwargs):
        pytest.fail("mock mode created a network client")

    monkeypatch.setattr(httpx, "AsyncClient", unexpected)
    asyncio.run(
        app_module.run_cli(Namespace(config="unused", backend="mock", once="hello"))
    )


def test_console_quit_joins_koboldcpp_abort(monkeypatch):
    async def run():
        started, aborting, release = (asyncio.Event() for _ in range(3))
        aborts = []

        async def handler(request):
            if request.url.path == "/api/extra/abort":
                aborts.append(json.loads(request.content)["genkey"])
                aborting.set()
                await release.wait()
                return httpx.Response(200, json={"success": "true", "done": "true"})
            if request.url.path == "/api/extra/perf":
                return httpx.Response(200, json={"idle": 1, "queue": 0})
            started.set()
            await asyncio.Event().wait()

        settings, character = load_config(ROOT / "configs/app.example.yaml")
        settings = replace(settings, backend="koboldcpp", server_abort_enabled=True)
        llm = KoboldCppClient(settings, httpx.MockTransport(handler))
        monkeypatch.setattr(app_module, "load_config", lambda _: (settings, character))
        monkeypatch.setattr(app_module, "KoboldCppClient", lambda _: llm)
        inputs = iter(["old", "/quit"])

        async def controlled_input(function, *args):
            command = next(inputs)
            if command == "/quit":
                await started.wait()
            return command

        monkeypatch.setattr(asyncio, "to_thread", controlled_input)
        before = asyncio.all_tasks()
        cli = asyncio.create_task(
            app_module.run_cli(
                Namespace(config="unused", backend="koboldcpp", once=None)
            )
        )
        await aborting.wait()
        assert not cli.done() and not llm._client.is_closed
        release.set()
        await asyncio.wait_for(cli, 1)
        assert len(aborts) == 1
        assert llm._client.is_closed and llm._control.is_closed
        assert asyncio.all_tasks() == before

    asyncio.run(run())
