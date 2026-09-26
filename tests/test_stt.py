import asyncio
import math
import struct
import sys
import wave
from types import SimpleNamespace

import pytest

from virtual_ai.stt.base import STTError, read_audio, validate_text
from virtual_ai.stt.client import ProcessSTT
from virtual_ai.stt.worker import transcribe


def audio(tmp_path, *, silent=False, seconds=1):
    path = tmp_path / "input.wav"
    pcm = b"".join(
        struct.pack("<h", 0 if silent else int(5000 * math.sin(i * 0.1)))
        for i in range(int(16000 * seconds))
    )
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(pcm)
    return path


def test_audio_limits_silence_and_transcript(tmp_path):
    assert read_audio(audio(tmp_path))
    for kwargs, reason in [
        ({"silent": True}, "silence"),
        ({"seconds": 0.1}, "duration_limit"),
        ({"seconds": 31}, "input_too_large"),
    ]:
        with pytest.raises(STTError, match=reason):
            read_audio(audio(tmp_path, **kwargs))
    with pytest.raises(STTError, match="no_speech"):
        validate_text(" ")
    with pytest.raises(STTError, match="transcript_too_large"):
        validate_text("x" * 1001)
    assert validate_text("/quit") == "/quit"


def test_lazy_segments_consumed_and_noise_filtered():
    consumed = []

    class Model:
        def transcribe(self, path, **kwargs):
            assert kwargs["vad_filter"] and kwargs["language"] == "ko"

            def segments():
                consumed.append(True)
                yield SimpleNamespace(
                    text="hello", no_speech_prob=0.1, avg_logprob=-0.1
                )
                yield SimpleNamespace(
                    text="noise", no_speech_prob=0.9, avg_logprob=-0.1
                )

            return segments(), None

    assert transcribe(Model(), "unused") == "hello"
    assert consumed


def test_subprocess_success_error_timeout_and_cancel(tmp_path, monkeypatch):
    import virtual_ai.stt.client as module

    original = asyncio.create_subprocess_exec
    processes = []
    scripts = iter(
        [
            "import json; print(json.dumps(dict(text='/quit')))",
            'print("not json")',
            "import time; time.sleep(10)",
            "import time; time.sleep(10)",
        ]
    )

    async def launch(*args, **kwargs):
        process = await original(sys.executable, "-c", next(scripts), **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", launch)

    async def run():
        # Interpreter startup is not the timeout case under test; it can exceed
        # 300 ms on a loaded Windows machine or a fresh Python installation.
        client = ProcessSTT(sys.executable, tmp_path, timeout=5)
        path = audio(tmp_path)
        assert await client.transcribe(path) == "/quit"
        with pytest.raises(STTError, match="engine_failed"):
            await client.transcribe(path)
        client.timeout = 0.05
        with pytest.raises(STTError, match="timeout"):
            await client.transcribe(path)
        client.timeout = 5
        task = asyncio.create_task(client.transcribe(path))
        while len(processes) < 4:
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert all(p.returncode is not None for p in processes)

    asyncio.run(run())
