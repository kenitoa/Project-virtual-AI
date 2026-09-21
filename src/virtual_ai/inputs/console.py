"""Only this local console maps operator commands to capabilities."""

import asyncio
from contextlib import suppress
from uuid import uuid4

from virtual_ai.schemas import ChatInput, Viewer


async def console(app):
    print("명령: /stop 중지, /forget 기록 삭제, /quit 종료")
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
            if command == "/stop":
                await app.stop()
            elif command == "/forget":
                await app.forget()
                print("최근 대화 기록을 삭제했습니다.")
            elif not app.submit(
                ChatInput(Viewer("console", "local"), text, str(uuid4()))
            ):
                print("입력이 비었거나 길이 제한을 초과했습니다.")
    finally:
        await app.stop()
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker
