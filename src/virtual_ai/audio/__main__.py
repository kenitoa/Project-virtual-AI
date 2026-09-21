"""Play existing WAVs only. No synthesis, LLM or broadcast integration."""

import argparse
import asyncio
import threading
from contextlib import suppress
from dataclasses import replace
from pathlib import Path

from virtual_ai.app import configure_console_output
from virtual_ai.audio.base import AudioError
from virtual_ai.audio.player import WAVPlayer, load_backend
from virtual_ai.config import load_config


async def interactive(player, path, commands):
    print("명령: /stop 즉시 중지, /play 파일경로 재생, /quit 종료", flush=True)
    playback = asyncio.create_task(player.play(path))
    command = asyncio.create_task(commands.get())
    try:
        while True:
            pending = [command] + ([playback] if playback is not None else [])
            done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            if playback in done:
                try:
                    print("재생 완료" if playback.result() else "재생 중지", flush=True)
                except AudioError as exc:
                    print(f"오디오 오류: {exc}", flush=True)
                playback = None
            if command not in done:
                continue
            text = command.result()
            if text is None or text.strip() == "/quit":
                break
            text = text.strip()
            if text == "/stop":
                try:
                    await player.stop()
                except AudioError as exc:
                    print(f"오디오 오류: {exc}", flush=True)
                print("중지 처리 완료", flush=True)
            elif text.startswith("/play "):
                if playback is not None and not playback.done():
                    print("이미 재생 중입니다. 먼저 /stop 하세요.", flush=True)
                else:
                    # Retrieve any previous exception before replacing its task.
                    if playback is not None:
                        with suppress(AudioError):
                            playback.result()
                    playback = asyncio.create_task(
                        player.play(Path(text[6:].strip().strip('"')))
                    )
            else:
                print("명령: /stop, /play 파일경로, /quit", flush=True)
            command = asyncio.create_task(commands.get())
    finally:
        command.cancel()
        with suppress(asyncio.CancelledError):
            await command
        if playback is not None:
            playback.cancel()
            with suppress(asyncio.CancelledError, AudioError):
                await playback
        await player.aclose()


async def run(args):
    settings, _ = load_config(Path(args.config).resolve())
    audio = settings.audio
    if args.device is not None:
        device = int(args.device) if args.device.isdecimal() else args.device
        audio = replace(audio, output_device=device)
    player = WAVPlayer(audio)
    try:
        if not args.interactive:
            await player.play(Path(args.wav))
            print("재생 완료")
            return
        loop = asyncio.get_running_loop()
        commands = asyncio.Queue()
        reader_closed = threading.Event()

        def read_commands():
            while not reader_closed.is_set():
                try:
                    text = input()
                except EOFError:
                    text = None
                if reader_closed.is_set():
                    return
                try:
                    loop.call_soon_threadsafe(commands.put_nowait, text)
                except RuntimeError:
                    return
                if text is None:
                    return

        # A blocked stdin read must not delay Ctrl+C or device cleanup at exit.
        threading.Thread(target=read_commands, daemon=True).start()
        try:
            await interactive(player, Path(args.wav), commands)
        finally:
            reader_closed.set()
    finally:
        await player.aclose()


def main():
    configure_console_output()
    parser = argparse.ArgumentParser(
        description="저장된 PCM WAV 재생 (LLM/TTS 호출 없음)"
    )
    parser.add_argument("wav", nargs="?")
    parser.add_argument("--config", default="configs/app.example.yaml")
    parser.add_argument("--device", help="출력 장치 번호 또는 이름 (설정 덮어쓰기)")
    parser.add_argument("--list-devices", action="store_true")
    parser.add_argument("--interactive", action="store_true")
    args = parser.parse_args()
    try:
        if args.list_devices:
            try:
                backend = load_backend()
                hosts = backend.query_hostapis()
                for index, device in enumerate(backend.query_devices()):
                    if device["max_output_channels"] > 0:
                        print(
                            f"{index}: {device['name']} [{hosts[device['hostapi']]['name']}]"
                        )
            except AudioError:
                raise
            except Exception:
                raise AudioError("출력 장치 목록을 조회할 수 없습니다.") from None
            return
        if args.wav is None:
            parser.error("재생할 WAV 경로가 필요합니다.")
        asyncio.run(run(args))
    except AudioError as exc:
        parser.exit(1, f"오디오 오류: {exc}\n")
    except (ValueError, OSError) as exc:
        parser.exit(2, f"설정/실행 오류: {exc}\n")
    except KeyboardInterrupt:
        parser.exit(130, "재생을 중지했습니다.\n")


if __name__ == "__main__":
    main()
