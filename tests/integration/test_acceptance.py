"""Repeated busy cancellation with bounded state in a single event loop."""

import asyncio
import json
import os
import time
from pathlib import Path

from test_operator_controls import make_app
from test_voice_pipeline import Player

from virtual_ai.performance import rss_bytes
from virtual_ai.schemas import ChatInput, Viewer

ROOT = Path(__file__).resolve().parents[2]


def handles():
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.GetProcessHandleCount.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        count = wintypes.DWORD()
        if kernel.GetProcessHandleCount(
            kernel.GetCurrentProcess(), ctypes.byref(count)
        ):
            return count.value
    if Path("/proc/self/fd").is_dir():
        return len(list(Path("/proc/self/fd").iterdir()))
    return None


def test_busy_flood_stop_resume_soak(tmp_path):
    duration = float(os.environ.get("VIRTUAL_AI_SOAK_SECONDS", "0"))
    assert 0 <= duration <= 7200
    fixture = json.loads(
        (ROOT / "tests/fixtures/chat_scenarios/acceptance.json").read_text()
    )

    async def run():
        player = Player(hold=True)
        app = make_app(tmp_path, player=player)
        baseline_tasks = len(asyncio.all_tasks())
        start = time.monotonic()
        samples = []
        cycle = 0
        next_sample = 0
        try:
            while cycle < 10 or time.monotonic() - start < duration:
                player.calls.clear()
                player.started.clear()
                player.release.clear()
                app.submit(
                    ChatInput(
                        Viewer("test", "active"), f"active {cycle}", f"active-{cycle}"
                    )
                )
                task = asyncio.create_task(app.process_next())
                async with asyncio.timeout(5):
                    await player.started.wait()
                    for i in range(fixture["flood_per_cycle"]):
                        app.submit(
                            ChatInput(
                                Viewer("youtube", str(i % fixture["users"])),
                                f"fixture {cycle} {i}",
                                f"{cycle}-{i}",
                            )
                        )
                    assert len(app.queue) <= app.settings.queue_size
                    await app.panic()
                    assert not app.submit(
                        ChatInput(Viewer("test", "locked"), "locked", "locked")
                    )
                    await task
                assert not player.active and not app._voice and not app._generation
                assert not len(app.queue) and not app._receipts
                assert not list(tmp_path.glob("*.wav"))
                assert len(asyncio.all_tasks()) <= baseline_tasks
                assert len(app.queue._seen) <= 2 * app.settings.queue_size
                assert app.recover() and app.resume() and app.unmute()
                # Fixture clients retain call histories for assertions; clear their own logs.
                app.llm.calls.clear()
                app.tts.calls.clear()
                app.llm.events.clear()
                app.tts.events.clear()
                player.events.clear()
                cycle += 1
                elapsed = time.monotonic() - start
                if elapsed >= next_sample:
                    samples.append(
                        {
                            "seconds": elapsed,
                            "rss_bytes": rss_bytes(),
                            "open_handles_or_fds": handles(),
                            "tasks": len(asyncio.all_tasks()),
                            "queue": len(app.queue),
                            "wav_files": len(list(tmp_path.glob("*.wav"))),
                            "active_fake_playbacks": int(player.active),
                        }
                    )
                    next_sample = elapsed + 1
                await asyncio.sleep(0.01)
            # Shutdown at peak playback, not just an idle shutdown.
            player.calls.clear()
            player.started.clear()
            player.release.clear()
            app.submit(
                ChatInput(Viewer("test", "shutdown"), "shutdown fixture", "shutdown")
            )
            task = asyncio.create_task(app.process_next())
            async with asyncio.timeout(5):
                await player.started.wait()
                await app.shutdown()
                await task
            assert not player.active and not list(tmp_path.glob("*.wav"))
        finally:
            await app.shutdown()
        output = os.environ.get("VIRTUAL_AI_SOAK_REPORT")
        if output:
            report = {
                "fixture_only": True,
                "requested_seconds": duration,
                "elapsed_seconds": time.monotonic() - start,
                "cycles": cycle,
                "samples": samples,
                "unmeasured": ["real_network_connections", "gpu", "real_devices"],
            }
            Path(output).write_text(json.dumps(report, indent=2), encoding="utf-8")

    asyncio.run(run())
