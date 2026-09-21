"""HTTP chat transport only. Never executes text or controls output devices."""

import asyncio
import json

import httpx

from virtual_ai.llm.base import LLMError


class KoboldCppClient:
    def __init__(self, settings, transport=None):
        self.settings = settings
        self._lock = asyncio.Lock()
        self._client = httpx.AsyncClient(
            timeout=settings.timeout_seconds, transport=transport, trust_env=False
        )

    async def generate(self, messages):
        try:
            # One deadline includes lock waiting, every attempt and backoff.
            async with asyncio.timeout(self.settings.total_timeout_seconds):
                async with self._lock:
                    return await self._generate_with_retries(messages)
        except TimeoutError:
            raise LLMError("LLM 전체 응답 시간이 초과되었습니다.") from None

    async def _generate_with_retries(self, messages):
        for attempt in range(self.settings.retries + 1):
            try:
                return await self._request(messages)
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

    async def _request(self, messages):
        base = self.settings.base_url.rstrip("/")
        url = base + "/v1/chat/completions"
        payload = {
            "model": self.settings.model,
            "messages": messages,
            "max_tokens": self.settings.max_output_tokens,
            "stream": False,
        }
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
        await self._client.aclose()
