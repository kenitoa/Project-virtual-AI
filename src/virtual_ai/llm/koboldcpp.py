"""HTTP chat transport only. Never executes text or controls output devices."""

import asyncio
import json
import logging
from uuid import uuid4

import httpx

from virtual_ai.llm.base import LLMError

logger = logging.getLogger(__name__)


class KoboldCppClient:
    def __init__(self, settings, transport=None):
        self.settings = settings
        self._lock = asyncio.Lock()
        self._blocked = False
        self._closed = False
        self._owners = set()
        self._close_task = None
        # Separate pool: abort must never wait for a generation connection.
        self._control = httpx.AsyncClient(
            timeout=settings.abort_timeout_seconds, transport=transport, trust_env=False
        )
        self._client = httpx.AsyncClient(
            timeout=settings.timeout_seconds, transport=transport, trust_env=False
        )

    @property
    def cleanup_confirmed(self):
        return not self._blocked and not self._closed

    async def generate(self, messages):
        if self._closed:
            raise LLMError("LLM client is closed.")
        owner = asyncio.current_task()
        self._owners.add(owner)
        try:
            # One deadline includes lock waiting, every attempt and backoff.
            async with asyncio.timeout(self.settings.total_timeout_seconds):
                async with self._lock:
                    if self._blocked:
                        raise LLMError(
                            "LLM server cleanup is unconfirmed; verify the server is idle "
                            "and restart the application before generating again."
                        )
                    result = await self._generate_with_retries(messages)
                    if owner.cancelling():
                        raise asyncio.CancelledError
                    return result
        except TimeoutError:
            raise LLMError("LLM 전체 응답 시간이 초과되었습니다.") from None
        finally:
            self._owners.discard(owner)

    async def _generate_with_retries(self, messages):
        for attempt in range(self.settings.retries + 1):
            genkey = uuid4().hex
            try:
                return await self._attempt(messages, genkey)
            except httpx.TimeoutException:
                error = "LLM 서버 응답 시간이 초과되었습니다."
            except httpx.ConnectError:
                error = "LLM 서버에 연결할 수 없습니다. 서버와 주소를 확인하세요."
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                error = f"LLM 요청이 실패했습니다 (HTTP {status})."
                if status not in (429, 502, 503, 504):
                    raise LLMError(error) from None
            except httpx.RequestError:
                raise LLMError("LLM 서버 통신 오류가 발생했습니다.") from None
            if attempt == self.settings.retries:
                raise LLMError(error) from None
            await asyncio.sleep(0.1 * (attempt + 1))

    async def _attempt(self, messages, genkey):
        try:
            return await self._request(messages, genkey)
        except asyncio.CancelledError:
            await self._cleanup(genkey)
            raise
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            # No generation was sent; normal bounded retry remains safe.
            raise
        except httpx.RequestError:
            await self._cleanup(genkey)
            if self._blocked:
                raise LLMError(
                    "LLM server cleanup is unconfirmed; retry blocked."
                ) from None
            raise
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (502, 504):
                await self._cleanup(genkey)
                if self._blocked:
                    raise LLMError(
                        "LLM server cleanup is unconfirmed; retry blocked."
                    ) from None
            raise

    async def _cleanup(self, genkey):
        # Keep the generation lock until this one cleanup finishes. Repeated
        # cancellation must neither duplicate abort nor interrupt its deadline.
        task = asyncio.create_task(self._abort_and_confirm(genkey))
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
        task.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _abort_and_confirm(self, genkey):
        self._blocked = True
        if not self.settings.server_abort_enabled:
            logger.warning("llm_abort_status=disabled cleanup=unconfirmed")
            return
        base = self.settings.base_url.rstrip("/")
        try:
            async with asyncio.timeout(self.settings.abort_timeout_seconds):
                response = await self._control.post(
                    base + "/api/extra/abort", json={"genkey": genkey}
                )
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict) or not all(
                    data.get(field) is True or data.get(field) == "true"
                    for field in ("success", "done")
                ):
                    logger.warning("llm_abort_status=rejected cleanup=unconfirmed")
                    return
                # done means the abort handler returned, not that GPU work and
                # its queue have drained. Require a separate idle observation.
                while True:
                    response = await self._control.get(base + "/api/extra/perf")
                    response.raise_for_status()
                    perf = response.json()
                    if (
                        isinstance(perf, dict)
                        and type(perf.get("idle")) is int
                        and perf["idle"] == 1
                        and type(perf.get("queue")) is int
                        and perf["queue"] == 0
                    ):
                        self._blocked = False
                        logger.info("llm_abort_status=accepted cleanup=confirmed")
                        return
                    await asyncio.sleep(0.05)
        except (httpx.HTTPError, TimeoutError, ValueError):
            # Never expose a response body, prompt or credentials in the log.
            logger.warning("llm_abort_status=failed cleanup=unconfirmed")

    async def _request(self, messages, genkey):
        base = self.settings.base_url.rstrip("/")
        url = base + "/v1/chat/completions"
        payload = {
            "model": self.settings.model,
            "messages": messages,
            "max_tokens": self.settings.max_output_tokens,
            "stream": False,
        }
        if self.settings.server_abort_enabled:
            payload["genkey"] = genkey
        # enable_thinking is template-specific and intentionally not sent.
        async with self._client.stream("POST", url, json=payload) as response:
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > 262144:
                    raise LLMError("LLM 응답 크기가 제한을 초과했습니다.")
        try:
            data = json.loads(body)
            if data["choices"][0].get("finish_reason") == "length":
                raise LLMError(
                    "LLM 답변이 길이 제한으로 잘렸습니다. 짧게 다시 요청하세요."
                )
            message = data["choices"][0]["message"]
            content = message["content"]
            if (
                message.get("role", "assistant") != "assistant"
                or not isinstance(content, str)
                or not content.strip()
            ):
                raise ValueError()
            return content
        except (ValueError, KeyError, IndexError, TypeError):
            raise LLMError("LLM 응답 형식이 올바르지 않습니다.") from None

    async def aclose(self):
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(self._close())
        cancelled = False
        while not self._close_task.done():
            try:
                await asyncio.shield(self._close_task)
            except asyncio.CancelledError:
                cancelled = True
        self._close_task.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _close(self):
        owners = tuple(self._owners)
        for owner in owners:
            owner.cancel()
        # Wait for request cleanup, not the callers' entire task lifetimes.
        async with self._lock:
            pass
        await self._client.aclose()
        await self._control.aclose()
