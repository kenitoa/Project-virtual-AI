"""One bounded subprocess per utterance; cancellation terminates the engine."""

import asyncio
import json
import tempfile
from pathlib import Path

from virtual_ai.stt.base import STTError, read_audio, validate_text


async def _settled(task):
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            pass
    return task.result()


class ProcessSTT:
    def __init__(self, python, model, *, device="cpu", timeout=120):
        self.python = Path(python).resolve()
        self.model = Path(model).resolve()
        if (
            not self.python.is_file()
            or not self.model.is_dir()
            or device not in ("cpu", "cuda")
            or not 0 < timeout <= 300
        ):
            raise STTError("invalid_configuration")
        self.device, self.timeout = device, timeout
        self.lock = asyncio.Lock()

    async def transcribe(self, path):
        try:
            return await self._transcribe(path)
        except OSError:
            raise STTError("file_io") from None

    async def _transcribe(self, path):
        if self.lock.locked():
            raise STTError("busy")
        async with self.lock:
            data = read_audio(path)
            with tempfile.TemporaryDirectory(prefix="virtual-ai-stt-") as folder:
                audio = Path(folder) / "input.wav"
                audio.write_bytes(data)
                process = None
                try:
                    spawn = asyncio.create_task(
                        asyncio.create_subprocess_exec(
                            str(self.python),
                            str(Path(__file__).with_name("worker.py")),
                            str(audio),
                            "--model",
                            str(self.model),
                            "--device",
                            self.device,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.DEVNULL,
                            limit=8192,
                        )
                    )
                    try:
                        process = await asyncio.shield(spawn)
                    except asyncio.CancelledError:
                        process = await _settled(spawn)
                        raise
                    async with asyncio.timeout(self.timeout):
                        output = bytearray()
                        while chunk := await process.stdout.read(1024):
                            output.extend(chunk)
                            if len(output) > 8192:
                                raise STTError("invalid_engine_output")
                        await process.wait()
                    if process.returncode:
                        raise STTError("engine_failed")
                    payload = json.loads(output)
                    if not isinstance(payload, dict) or set(payload) != {"text"}:
                        raise STTError("invalid_engine_output")
                    return validate_text(payload["text"])
                except TimeoutError:
                    raise STTError("timeout") from None
                except (OSError, ValueError, UnicodeError):
                    raise STTError("engine_failed") from None
                finally:
                    if process is not None and process.returncode is None:
                        try:
                            process.kill()
                        except ProcessLookupError:
                            pass
                        await _settled(asyncio.create_task(process.wait()))
