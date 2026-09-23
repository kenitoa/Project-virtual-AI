"""Small, local-only memory context. No shared viewer identity or cache."""

import asyncio
import json
import re
from uuid import uuid4

from virtual_ai.safety import contains_personal_data, contains_secret
from virtual_ai.schemas import Viewer


class MemoryContext:
    def __init__(self, store, user_id="local", session_id=None):
        self.store = store
        self.user_id = user_id
        self.session_id = session_id or str(uuid4())
        self.blocked = False
        self.lock = asyncio.Lock()

    async def call(self, function, *args):
        async with self.lock:
            task = asyncio.create_task(asyncio.to_thread(function, *args))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                try:
                    await task
                finally:
                    raise

    async def retrieve(self, viewer, question):
        if self.blocked or viewer != Viewer("console", "local"):
            return ""
        keywords = list(dict.fromkeys(re.findall(r"[^\W_]{2,80}", question.lower())))[
            :5
        ]
        rows = await self.call(
            self.store.retrieve, self.user_id, self.session_id, keywords
        )
        if self.blocked:
            return ""
        chosen = []
        for row in rows:
            candidate = json.dumps(chosen + [row], ensure_ascii=False)
            if (
                len(chosen) < 3
                and len(candidate) <= 1000
                and not contains_secret(candidate)
                and not contains_personal_data(candidate)
            ):
                chosen.append(row)
        return json.dumps(chosen, ensure_ascii=False) if chosen else ""

    async def forget(self):
        self.blocked = True
        return await self.call(self.store.forget, self.user_id)
