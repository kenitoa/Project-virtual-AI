"""Optional loopback operator UI. Same controller methods as the console.

Bounded HTTP/1.1, strict Host/Origin, per-process token and no CORS. Not a remote
administration service. No model/viewer text is dispatched as a command.
"""

import asyncio
import hmac
import json
import secrets
from pathlib import Path
from time import monotonic

from virtual_ai.memory.base import StoreError
from virtual_ai.schemas import ChatInput, Viewer


class OperatorPanel:
    def __init__(self, app):
        self.app = app
        self.token = secrets.token_urlsafe(32)
        self.server = None
        self.clients = set()

    async def start(self, port=0):
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("invalid operator port")
        self.server = await asyncio.start_server(
            self._client, "127.0.0.1", port, limit=16384
        )
        self.authority = "127.0.0.1:" + str(self.server.sockets[0].getsockname()[1])
        self.url = "http://" + self.authority
        return self.url

    def status(self):
        state = self.app.status()
        keys = (
            "operating_state",
            "component_faults",
            "paused",
            "panic",
            "muted",
            "phase",
            "queue",
            "input_drops",
            "cleanup_failed",
            "cleanup_pending",
            "broadcast_mode",
            "observations",
            "delivery",
            "startup_check_age_seconds",
            "microphone",
            "vts",
            "tts",
            "audio",
            "youtube",
            "chzzk",
            "startup_checks",
        )
        result = {key: state[key] for key in keys}
        result["obs"] = self.app.obs.status() if self.app.obs else {"state": "disabled"}
        return result

    async def dispatch(self, data):
        if not isinstance(data, dict) or not isinstance(data.get("action"), str):
            raise ValueError("invalid command")
        action = data["action"]
        app = self.app
        if action in ("stop", "pause", "mute", "panic"):
            await getattr(app, action)()
        elif action in ("resume", "unmute", "recover"):
            return {"accepted": getattr(app, action)(), "status": self.status()}
        elif action == "mode":
            await app.set_mode(data.get("value"))
        elif action == "topic":
            await app.stop()
            app.set_broadcast_state("topic", data.get("value"))
        elif action == "say":
            from uuid import uuid4

            text = data.get("text")
            if not isinstance(text, str) or len(text) > app.settings.max_input_chars:
                raise ValueError("invalid input")
            return {
                "accepted": app.submit(
                    ChatInput(Viewer("console", "local"), text, str(uuid4()))
                )
            }
        elif action == "diagnose":
            if (
                not app.runtime.paused
                or app.runtime.phase != "idle"
                or app._lock.locked()
            ):
                raise ValueError("pause and wait for cleanup before diagnostics")
            from virtual_ai.health import check_health

            app.health_checks = tuple(await check_health(app.settings))
            app.health_checked_at = monotonic()
            # Diagnostics never clear a cleanup lock or silently re-enable a client.
        elif action in ("ptt_start", "ptt_stop", "ptt_cancel"):
            if app.microphone is None:
                raise ValueError("microphone is disabled")
            if action == "ptt_start":
                return {"accepted": app.microphone.start()}
            if action == "ptt_stop":
                return {"accepted": app.microphone.release()}
            await app.microphone.cancel()
        elif action.startswith("memory_"):
            return await self.memory_action(action, data)
        else:
            raise ValueError("unknown command")
        return {"accepted": True, "status": self.status()}

    async def memory_action(self, action, data):
        if not self.app.rag:
            raise ValueError("RAG memory is disabled")
        platform, user = data.get("platform"), data.get("user")
        if (
            platform not in ("youtube", "chzzk", "console")
            or not isinstance(user, str)
            or not 1 <= len(user) <= 256
        ):
            raise ValueError("verified platform user ID required")
        viewer = Viewer(platform, user)
        rag = self.app.rag
        if action == "memory_inspect":
            return {
                "private_memory": await asyncio.to_thread(
                    rag.store.inspect, rag.settings.scope, viewer
                )
            }
        if data.get("identity_verified") is not True:
            raise ValueError("operator must verify requester identity")
        if action == "memory_forget":
            await self.app.forget_rag(viewer)
        elif action == "memory_consent":
            flags = [data.get(k) for k in ("storage", "retrieval", "public")]
            if any(type(f) is not bool for f in flags) or (
                any(flags) and data.get("consent_verified") is not True
            ):
                raise ValueError(
                    "record only permissions actually confirmed by the viewer"
                )
            await self.app.pause()
            await self.app.forget(viewer)
            await asyncio.to_thread(
                rag.store.consent,
                rag.settings.scope,
                viewer,
                storage=flags[0],
                retrieval=flags[1],
                public=flags[2],
            )
        else:
            raise ValueError("unknown memory command")
        return {"accepted": True, "paused": True}

    async def _client(self, reader, writer):
        task = asyncio.current_task()
        if len(self.clients) >= 16:
            writer.close()
            return
        self.clients.add(task)
        status, content_type, body = (
            400,
            "application/json; charset=utf-8",
            b'{"error":"invalid request"}',
        )
        try:
            async with asyncio.timeout(5):
                raw = await reader.readuntil(b"\r\n\r\n")
                if len(raw) > 8192:
                    raise ValueError()
                lines = raw.decode("ascii").split("\r\n")
                method, path, version = lines[0].split(" ")
                headers = {}
                for line in lines[1:-2]:
                    key, value = line.split(":", 1)
                    key = key.lower()
                    if key in headers:
                        raise ValueError()
                    headers[key] = value.strip()
                if (
                    version != "HTTP/1.1"
                    or headers.get("host") != self.authority
                    or "transfer-encoding" in headers
                ):
                    raise ValueError()
                if (
                    headers.get("origin", self.url) != self.url
                    or headers.get("sec-fetch-site") == "cross-site"
                ):
                    raise ValueError()
                length = int(headers.get("content-length", "0"))
                if not 0 <= length <= 8192:
                    raise ValueError()
                payload = await reader.readexactly(length)
            if method == "GET" and path == "/" and length == 0:
                content_type = "text/html; charset=utf-8"
                body = (
                    Path(__file__)
                    .with_name("operator_panel.html")
                    .read_text(encoding="utf-8")
                    .replace("__TOKEN__", self.token)
                    .encode()
                )
                status = 200
            elif not hmac.compare_digest(
                headers.get("x-operator-token", ""), self.token
            ):
                status, body = 403, b'{"error":"operator token required"}'
            elif method == "GET" and path == "/status" and length == 0:
                status, body = (
                    200,
                    json.dumps(self.status(), ensure_ascii=False).encode(),
                )
            elif (
                method == "POST"
                and path == "/command"
                and headers.get("content-type") == "application/json"
            ):
                result = await self.dispatch(json.loads(payload))
                status, body = 200, json.dumps(result, ensure_ascii=False).encode()
            else:
                status, body = 404, b'{"error":"not found"}'
        except (
            ValueError,
            TypeError,
            KeyError,
            StoreError,
            OSError,
            TimeoutError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
        ):
            pass  # Never serialize exception strings, credentials or incoming text.
        finally:
            try:
                headers_out = f"HTTP/1.1 {status} Result\r\nContent-Type: {content_type}\r\nContent-Length: {len(body)}\r\nConnection: close\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\nReferrer-Policy: no-referrer\r\nContent-Security-Policy: default-src 'none'; script-src 'nonce-{self.token}'; style-src 'nonce-{self.token}'; connect-src 'self'; frame-ancestors 'none'; form-action 'none'; base-uri 'none'\r\n\r\n"
                writer.write(headers_out.encode() + body)
                async with asyncio.timeout(2):
                    await writer.drain()
            except (OSError, TimeoutError):
                pass
            writer.close()
            self.clients.discard(task)

    async def aclose(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        if self.clients:
            for task in tuple(self.clients):
                task.cancel()
            await asyncio.gather(*self.clients, return_exceptions=True)
