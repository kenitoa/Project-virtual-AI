"""Only this local console maps operator commands to capabilities."""

import asyncio
import json
from contextlib import suppress
from uuid import uuid4

from virtual_ai.schemas import ChatInput, Viewer


async def console(app):
    print("명령: /stop 중지, /forget 기록 삭제, /quit 종료")
    print("운영: /pause /resume /mute /unmute /status /panic /recover")
    print("기억: /summarize /forget-long /forget-viewer youtube CHANNEL_ID")
    print("PTT: /ptt-start /ptt-stop /ptt-cancel (opt-in)")
    worker = asyncio.create_task(app.run())
    try:
        while True:
            try:
                text = await asyncio.to_thread(input, "> ")
            except EOFError:
                break
            command = text.strip()
            if command == "/quit":
                break
            if command in ("/ptt-start", "/ptt-stop", "/ptt-cancel"):
                mic = app.microphone
                if mic is None:
                    print("microphone=disabled")
                elif command == "/ptt-start":
                    print("microphone_started=" + str(mic.start()))
                elif command == "/ptt-stop":
                    print("microphone_released=" + str(mic.release()))
                else:
                    await mic.cancel()
                    print("microphone=cancelled paused=true")
            elif command == "/stop":
                await app.stop()
            elif command in ("/pause", "/mute", "/panic"):
                await getattr(app, command[1:])()
                print(json.dumps(app.status(), ensure_ascii=False))
            elif command in ("/resume", "/unmute", "/recover"):
                if not getattr(app, command[1:])():
                    print("복구 거절: 비상 잠금 또는 정리 상태를 확인하세요.")
                print(json.dumps(app.status(), ensure_ascii=False))
            elif command == "/status":
                print(json.dumps(app.status(), ensure_ascii=False))
            elif command.startswith("/forget-viewer "):
                parts = command.split()
                if len(parts) == 3 and parts[1] == "youtube":
                    await app.pause()
                    await app.forget(Viewer("youtube", parts[2]))
                    print("viewer_ram_deleted=true paused=true")
                else:
                    print("사용법: /forget-viewer youtube CHANNEL_ID")
            elif command == "/forget-long":
                print(
                    "memory_deleted="
                    + str(await app.forget_long())
                    + " backups_review_required=true paused=true"
                )
            elif command == "/summarize":
                print("memory_summary_updated=" + str(await app.summarize_memory()))
            elif command == "/forget":
                await app.forget()
                print("최근 대화 기록을 삭제했습니다.")
            elif not app.submit(
                ChatInput(Viewer("console", "local"), text, str(uuid4()))
            ):
                print(
                    "입력 잠금·유효성·대기열 정책으로 제외했습니다. /status에서 사유 집계를 확인하세요."
                )
    finally:
        await app.stop()
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker
