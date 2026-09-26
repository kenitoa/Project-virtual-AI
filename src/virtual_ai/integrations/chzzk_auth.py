"""Explicit CHZZK OAuth. Secrets stay in the OS vault; no browser cookies."""

import argparse
import asyncio
import json
import math
import secrets
import sys
import time
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from virtual_ai.integrations.youtube_auth import AuthError, CredentialStore, _string

API = "https://openapi.chzzk.naver.com"
REDIRECT = "http://127.0.0.1:8766/callback"
SERVICE = "Project-virtual-AI.chzzk"


async def authorization_code(client_id, *, opener=webbrowser.open, timeout=180):
    state = secrets.token_urlsafe(32)
    result = asyncio.get_running_loop().create_future()

    async def callback(reader, writer):
        status = "400 Bad Request"
        try:
            request = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            method, target, _ = request.split(b"\r\n", 1)[0].decode("ascii").split(" ")
            parsed = urlsplit(target)
            values = parse_qs(parsed.query, keep_blank_values=True)
            if (
                method == "GET"
                and parsed.path == "/callback"
                and len(values.get("state", [])) == 1
                and secrets.compare_digest(values["state"][0], state)
                and not result.done()
            ):
                if "error" in values:
                    result.set_result(None)
                elif len(values.get("code", [])) == 1 and _string(values["code"][0]):
                    result.set_result(values["code"][0])
                else:
                    raise ValueError
                status = "200 OK"
        except (
            ValueError,
            UnicodeError,
            TimeoutError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
        ):
            pass
        finally:
            try:
                writer.write(
                    (
                        f"HTTP/1.1 {status}\r\nContent-Type: text/plain\r\n"
                        "Cache-Control: no-store\r\nReferrer-Policy: no-referrer\r\n"
                        "Connection: close\r\n\r\nReturn to the application."
                    ).encode()
                )
                await writer.drain()
            except (ConnectionError, OSError):
                pass
            writer.close()

    try:
        server = await asyncio.start_server(callback, "127.0.0.1", 8766, limit=8192)
        async with server:
            url = "https://chzzk.naver.com/account-interlock?" + urlencode(
                {"clientId": client_id, "redirectUri": REDIRECT, "state": state}
            )
            if not opener(url):
                raise AuthError("Cannot open browser for CHZZK login.")
            code = await asyncio.wait_for(result, timeout)
            if code is None:
                raise AuthError("CHZZK authorization denied.")
            return code, state
    except (OSError, TimeoutError):
        raise AuthError(
            "CHZZK callback unavailable or timed out; retry login."
        ) from None


class ChzzkSession:
    def __init__(self, *, store=None, transport=None, now=time.time):
        self.store = store if store is not None else CredentialStore(SERVICE)
        self.transport = transport
        self.now = now
        self.lock = asyncio.Lock()
        self.failed = False

    async def _request(self, method, path, **kwargs):
        try:
            async with httpx.AsyncClient(
                transport=self.transport, timeout=10, follow_redirects=False
            ) as client:
                response = await client.request(method, API + path, **kwargs)
            if response.status_code not in (200, 204):
                if response.status_code in (400, 401, 403):
                    self.failed = True
                raise AuthError(
                    "CHZZK API rejected the request; check authorization and scopes."
                )
            if response.status_code == 204:
                return None
            data = response.json()
            if not isinstance(data, dict) or data.get("code", 200) != 200:
                raise ValueError
            return data.get("content", data)
        except (httpx.HTTPError, ValueError):
            raise AuthError("CHZZK request failed; no automatic retry.") from None

    def _record(self):
        data = self.store.load()
        if (
            not isinstance(data, dict)
            or not all(
                _string(data.get(k))
                for k in (
                    "client_id",
                    "client_secret",
                    "access_token",
                    "refresh_token",
                    "channel_id",
                )
            )
            or type(data.get("expires_at")) not in (int, float)
            or not math.isfinite(data["expires_at"])
        ):
            raise AuthError("CHZZK credentials missing or invalid; run login.")
        return data

    def _tokens(self, data, base):
        if not isinstance(data, dict):
            raise AuthError("Invalid CHZZK token response.")
        try:
            seconds = int(data["expiresIn"])
            if (
                not 60 <= seconds <= 86400 * 7
                or not all(
                    _string(data.get(k)) for k in ("accessToken", "refreshToken")
                )
                or data.get("tokenType", "Bearer").lower() != "bearer"
            ):
                raise ValueError
        except (KeyError, ValueError, TypeError, AttributeError):
            raise AuthError("Invalid CHZZK token response.") from None
        return {
            **base,
            "access_token": data["accessToken"],
            "refresh_token": data["refreshToken"],
            "expires_at": self.now() + seconds,
        }

    async def login(self, path, channel_id, *, opener=webbrowser.open, timeout=180):
        try:
            client = json.loads(Path(path).read_text(encoding="utf-8"))
            if not all(_string(client.get(k)) for k in ("client_id", "client_secret")):
                raise ValueError
        except (OSError, ValueError, TypeError, AttributeError):
            raise AuthError("Invalid CHZZK client file.") from None
        code, state = await authorization_code(
            client["client_id"], opener=opener, timeout=timeout
        )
        data = await self._request(
            "POST",
            "/auth/v1/token",
            json={
                "grantType": "authorization_code",
                "clientId": client["client_id"],
                "clientSecret": client["client_secret"],
                "code": code,
                "state": state,
            },
        )
        record = self._tokens(
            data, {k: client[k] for k in ("client_id", "client_secret")}
        )
        user = await self._request(
            "GET",
            "/open/v1/users/me",
            headers={"Authorization": "Bearer " + record["access_token"]},
        )
        if not isinstance(user, dict) or user.get("channelId") != channel_id:
            raise AuthError("CHZZK channel mismatch; credentials not saved.")
        record["channel_id"] = channel_id
        self.store.save(record)
        self.failed = False
        return user

    async def access_token(self):
        async with self.lock:
            if self.failed:
                raise AuthError("CHZZK authorization failed; run login explicitly.")
            record = self._record()
            if record["expires_at"] <= self.now() + 120:
                # Refresh tokens are single-use. Never retry an ambiguous exchange.
                self.failed = True
                self.store.delete()
                data = await self._request(
                    "POST",
                    "/auth/v1/token",
                    json={
                        "grantType": "refresh_token",
                        "clientId": record["client_id"],
                        "clientSecret": record["client_secret"],
                        "refreshToken": record["refresh_token"],
                    },
                )
                record = self._tokens(data, record)
                self.store.save(record)
                self.failed = False
            return record["access_token"]

    async def api(self, method, path, **kwargs):
        allowed = {
            ("GET", "/open/v1/users/me"),
            ("GET", "/open/v1/sessions/auth"),
            ("POST", "/open/v1/sessions/events/subscribe/chat"),
            ("POST", "/open/v1/sessions/events/unsubscribe/chat"),
        }
        if (method, path) not in allowed:
            raise AuthError("CHZZK operation is not allowed.")
        token = await self.access_token()
        return await self._request(
            method, path, headers={"Authorization": "Bearer " + token}, **kwargs
        )

    async def verify(self, channel_id):
        user = await self.api("GET", "/open/v1/users/me")
        if not isinstance(user, dict) or user.get("channelId") != channel_id:
            raise AuthError("CHZZK channel mismatch.")
        return user


async def _main(args):
    session = ChzzkSession()
    if args.command == "login":

        def opener(url):
            if args.print_url:
                print(url, flush=True)
                return True
            return webbrowser.open(url)

        user = await session.login(
            args.client_file, args.channel_id, opener=opener, timeout=600
        )
    else:
        user = await session.verify(args.channel_id)
    print(
        json.dumps(
            {"channel_id": user["channelId"], "channel_name": user.get("channelName")},
            ensure_ascii=True,
        )
    )


def main():
    parser = argparse.ArgumentParser(
        description="CHZZK read-only channel authorization"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    login = sub.add_parser("login")
    login.add_argument("--client-file", default="secrets/chzzk-client.json")
    login.add_argument("--print-url", action="store_true")
    for command in (login, sub.add_parser("status")):
        command.add_argument("--channel-id", required=True)
    try:
        asyncio.run(_main(parser.parse_args()))
    except AuthError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
