import asyncio
import io
import json
import logging
import subprocess
import sys
import threading
import wave
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
import yaml

from virtual_ai.config import TTSSettings, load_config
from virtual_ai.tts.base import TTSError
from virtual_ai.tts.gpt_sovits import GPTSoVITSClient

ROOT = Path(__file__).resolve().parents[1]


def wav_bytes():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(32000)
        audio.writeframes(b"\x01\x00" * 320)
    return buffer.getvalue()


def settings(**kwargs):
    return replace(
        TTSSettings(enabled=True, ref_audio_path="server/private.wav"), **kwargs
    )


def wav_response():
    return httpx.Response(
        200, content=wav_bytes(), headers={"content-type": "audio/wav"}
    )


def test_contract_wav_save_reuse_and_privacy(tmp_path, caplog):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == "POST"
        assert str(request.url) == "http://127.0.0.1:9880/tts"
        assert json.loads(request.content) == {
            "text": "안녕하세요.",
            "text_lang": "ko",
            "ref_audio_path": "server/private.wav",
            "prompt_text": "private transcript",
            "prompt_lang": "ko",
            "media_type": "wav",
            "streaming_mode": False,
        }
        return wav_response()

    async def run():
        client = GPTSoVITSClient(
            settings(prompt_text="private transcript"), httpx.MockTransport(handler)
        )
        try:
            destination = tmp_path / "nested/result.wav"
            for _ in range(2):
                assert (
                    await client.synthesize(" 안녕하세요. ", destination) == destination
                )
                assert destination.read_bytes() == wav_bytes()
                assert list(destination.parent.glob("*.tmp")) == []
            assert len(calls) == 2
        finally:
            await client.aclose()
        assert client._client.is_closed

    with caplog.at_level(logging.INFO, logger="virtual_ai"):
        asyncio.run(run())
    assert "private" not in caplog.text
    assert "안녕하세요" not in caplog.text


@pytest.mark.parametrize("text", ["", " \n\t", "x" * 1001, None])
def test_empty_or_long_text_never_sent(tmp_path, text):
    async def run():
        client = GPTSoVITSClient(
            settings(), httpx.MockTransport(lambda r: pytest.fail("network called"))
        )
        try:
            with pytest.raises(TTSError, match="텍스트"):
                await client.synthesize(text, tmp_path / "out.wav")
        finally:
            await client.aclose()
        assert not list(tmp_path.iterdir())

    asyncio.run(run())


@pytest.mark.parametrize(
    "status,media,body",
    [
        (400, "application/json", b'{"message":"private failure"}'),
        (503, "application/json", b"{}"),
        (200, "application/json", b'{"error":"private"}'),
        (200, "audio/wav", b'{"error":"disguised"}'),
        (200, "audio/wav", b""),
        (200, "audio/wav", wav_bytes()[:-1]),
        (200, "audio/wav", b"RIFF\xff\xff\xff\xffWAVE"),
    ],
)
def test_bad_responses_preserve_existing_file_without_retry(
    tmp_path, status, media, body
):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, content=body, headers={"content-type": media})

    async def run():
        output = tmp_path / "result.wav"
        output.write_bytes(b"previous file")
        client = GPTSoVITSClient(settings(), httpx.MockTransport(handler))
        try:
            with pytest.raises(TTSError) as caught:
                await client.synthesize("hello", output)
            assert "private" not in str(caught.value)
            assert len(calls) == 1
            assert output.read_bytes() == b"previous file"
            assert list(tmp_path.iterdir()) == [output]
        finally:
            await client.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "error", [httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError]
)
def test_transport_errors_do_not_save(tmp_path, error):
    def handler(request):
        raise error("private transport detail")

    async def run():
        client = GPTSoVITSClient(settings(), httpx.MockTransport(handler))
        try:
            with pytest.raises(TTSError) as caught:
                await client.synthesize("hello", tmp_path / "out.wav")
            assert "private" not in str(caught.value)
            assert not list(tmp_path.iterdir())
        finally:
            await client.aclose()

    asyncio.run(run())


def test_total_timeout_and_response_limit(tmp_path):
    async def slow(request):
        await asyncio.sleep(1)
        return wav_response()

    async def run():
        for config, handler in [
            (settings(total_timeout_seconds=0.02), slow),
            (settings(max_response_bytes=100), lambda r: wav_response()),
        ]:
            client = GPTSoVITSClient(config, httpx.MockTransport(handler))
            try:
                with pytest.raises(TTSError):
                    await client.synthesize("hello", tmp_path / "out.wav")
            finally:
                await client.aclose()
        assert not list(tmp_path.iterdir())

    asyncio.run(run())


@pytest.mark.parametrize("ignore_cancellation", [False, True])
def test_cancelled_late_result_not_saved_and_next_request_succeeds(
    tmp_path, ignore_cancellation
):
    async def run():
        entered = asyncio.Event()
        calls = 0

        async def handler(request):
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    if not ignore_cancellation:
                        raise
            return wav_response()

        client = GPTSoVITSClient(settings(), httpx.MockTransport(handler))
        output = tmp_path / "out.wav"
        output.write_bytes(b"existing")
        try:
            task = asyncio.create_task(client.synthesize("first", output))
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert output.read_bytes() == b"existing"
            assert await client.synthesize("second", output) == output
            assert output.read_bytes() == wav_bytes()
            assert list(tmp_path.iterdir()) == [output]
        finally:
            await client.aclose()

    asyncio.run(run())


def test_timeout_includes_waiting_for_single_synthesis_slot(tmp_path):
    async def run():
        entered = asyncio.Event()
        calls = []

        async def handler(request):
            calls.append(request)
            entered.set()
            await asyncio.Event().wait()

        client = GPTSoVITSClient(
            settings(total_timeout_seconds=0.05), httpx.MockTransport(handler)
        )
        await client._lock.acquire()
        try:
            with pytest.raises(TTSError, match="시간"):
                await client.synthesize("queued", tmp_path / "out.wav")
            assert not calls
        finally:
            client._lock.release()
            await client.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "values",
    [
        {"enabled": "false"},
        {"enabled": True},
        {"base_url": "http://host/tts"},
        {"base_url": "http://user:pass@host"},
        {"timeout_seconds": 0},
        {"total_timeout_seconds": float("nan")},
        {"max_response_bytes": True},
        {"max_text_chars": 0},
        {"prompt_lang": " "},
        {"ref_audio_path": 42},
    ],
)
def test_invalid_settings(values):
    with pytest.raises(ValueError):
        TTSSettings(**values)


def test_config_loading_and_cli_disabled_by_default(tmp_path):
    config, _ = load_config(ROOT / "configs/app.example.yaml")
    assert not config.tts.enabled
    result = subprocess.run(
        [sys.executable, "-m", "virtual_ai.tts", "--output", str(tmp_path / "out.wav")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    assert result.returncode == 1
    assert "비활성화" in result.stderr
    assert not list(tmp_path.iterdir())
    path = tmp_path / "bad.yaml"
    path.write_text("tts: {unknown: true}", encoding="utf-8")
    with pytest.raises(ValueError, match="tts"):
        load_config(path)


def test_save_failure_cleans_temp_file(tmp_path):
    async def run():
        destination = tmp_path / "directory.wav"
        destination.mkdir()
        client = GPTSoVITSClient(
            settings(), httpx.MockTransport(lambda r: wav_response())
        )
        try:
            with pytest.raises(TTSError, match="저장"):
                await client.synthesize("hello", destination)
            assert list(tmp_path.iterdir()) == [destination]
        finally:
            await client.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("corruption", ["empty", "truncated_data", "partial_frame"])
def test_wav_structure_not_just_magic_bytes(tmp_path, corruption):
    data = bytearray(wav_bytes())
    if corruption == "empty":
        data = data[:44]
        data[40:44] = (0).to_bytes(4, "little")
    elif corruption == "truncated_data":
        data = data[:-2]
    else:
        # Declared data size ends halfway through a PCM16 frame, with RIFF padding.
        data[40:44] = (639).to_bytes(4, "little")
    data[4:8] = (len(data) - 8).to_bytes(4, "little")

    async def run():
        client = GPTSoVITSClient(
            settings(),
            httpx.MockTransport(
                lambda r: httpx.Response(
                    200, content=bytes(data), headers={"content-type": "audio/wav"}
                )
            ),
        )
        try:
            with pytest.raises(TTSError, match="PCM WAV"):
                await client.synthesize("hello", tmp_path / "out.wav")
            assert not list(tmp_path.iterdir())
        finally:
            await client.aclose()

    asyncio.run(run())


def test_cancel_during_body_read_closes_stream_and_leaves_no_file(tmp_path):
    async def run():
        entered = asyncio.Event()

        class Stream(httpx.AsyncByteStream):
            closed = False

            async def __aiter__(self):
                yield wav_bytes()[:44]
                entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    pass  # Simulate a late body arriving after cancellation.
                yield wav_bytes()[44:]

            async def aclose(self):
                self.closed = True

        stream = Stream()
        client = GPTSoVITSClient(
            settings(),
            httpx.MockTransport(
                lambda r: httpx.Response(
                    200, stream=stream, headers={"content-type": "audio/wav"}
                )
            ),
        )
        try:
            task = asyncio.create_task(client.synthesize("hello", tmp_path / "out.wav"))
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert stream.closed
            assert not list(tmp_path.iterdir())
        finally:
            await client.aclose()

    asyncio.run(run())


def test_fixed_sentence_cli_over_real_http_with_fixture_audio(tmp_path):
    # Exercises the actual executable/HTTP/save path, not a real voice model.
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            requests.append(
                (
                    self.path,
                    json.loads(self.rfile.read(int(self.headers["content-length"]))),
                )
            )
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(wav_bytes())))
            self.end_headers()
            self.wfile.write(wav_bytes())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = tmp_path / "app.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "character_path": str(ROOT / "configs/character.yaml"),
                "tts": {
                    "enabled": True,
                    "base_url": f"http://127.0.0.1:{server.server_port}",
                    "ref_audio_path": "operator/reference.wav",
                    "prompt_text": "private-reference",
                },
            }
        ),
        encoding="utf-8",
    )
    destination = tmp_path / "audio/out.wav"
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "virtual_ai.tts",
                "--config",
                str(config),
                "--output",
                str(destination),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            cwd=ROOT,
        )
        assert result.returncode == 0, result.stderr
        assert destination.read_bytes() == wav_bytes()
        assert len(requests) == 1 and requests[0][0] == "/tts"
        assert requests[0][1]["text"] == "안녕하세요. 음성 합성 테스트입니다."
        assert requests[0][1]["streaming_mode"] is False
        assert "tts_seconds=" in result.stderr
        assert "private-reference" not in result.stderr
        assert "operator/reference.wav" not in result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
