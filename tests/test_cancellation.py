import asyncio
from argparse import Namespace
from contextlib import suppress
from pathlib import Path

import httpx
import pytest

from virtual_ai import app as app_module
from virtual_ai.app import Application
from virtual_ai.config import Settings, load_config
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
            assert await client.generate([]) == "recovered"
        finally:
            await client.aclose()

    asyncio.run(run())
