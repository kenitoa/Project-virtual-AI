"""Explicit Desktop OAuth and read-only broadcast identity. Never log credentials."""

import argparse
import asyncio
import base64
import hashlib
import json
import math
import os
import secrets
import sys
import time
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API = "https://www.googleapis.com/youtube/v3/"
SERVICE = "Project-virtual-AI.youtube"


class AuthError(Exception):
    """Only fixed operator-facing messages, never upstream exception text."""


class CredentialStore:
    """Select an OS vault explicitly; never permit a plaintext fallback plugin."""

    def __init__(self, service=SERVICE):
        self.service = service
        try:
            if sys.platform == "win32":
                from keyring.backends.Windows import WinVaultKeyring

                self.backend = WinVaultKeyring()
            elif sys.platform == "darwin":
                from keyring.backends.macOS import Keyring

                self.backend = Keyring()
            else:
                from keyring.backends.SecretService import Keyring

                self.backend = Keyring()
        except Exception:
            raise AuthError("OS credential vault unavailable.") from None

    def load(self):
        try:
            raw = self.backend.get_password(self.service, "desktop")
            return json.loads(raw) if raw else None
        except Exception:
            raise AuthError("Cannot read OS credential vault.") from None

    def save(self, value):
        try:
            self.backend.set_password(self.service, "desktop", json.dumps(value))
        except Exception:
            raise AuthError("Cannot save OS credential vault.") from None

    def delete(self):
        try:
            if self.backend.get_password(self.service, "desktop") is not None:
                self.backend.delete_password(self.service, "desktop")
        except Exception:
            raise AuthError(
                "Cannot clear OS credential vault; remove entry manually."
            ) from None


def _string(value):
    return (
        isinstance(value, str)
        and bool(value)
        and all(33 <= ord(c) <= 126 for c in value)
    )


def _valid_record(record):
    if not isinstance(record, dict):
        return False
    return (
        all(
            _string(record.get(k))
            for k in ("client_id", "client_secret", "access_token", "refresh_token")
        )
        and type(record.get("expires_at")) in (int, float)
        and math.isfinite(record["expires_at"])
        and record.get("scope") == SCOPE
    )


def load_desktop_client(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))["installed"]
        if not all(_string(data.get(k)) for k in ("client_id", "client_secret")):
            raise ValueError
        return {k: data[k] for k in ("client_id", "client_secret")}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise AuthError("Invalid Desktop OAuth client file.") from None


async def authorization_code(client_id, *, opener=webbrowser.open, timeout=180):
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    result = asyncio.get_running_loop().create_future()

    async def callback(reader, writer):
        status = "400 Bad Request"
        try:
            request = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            method, target, _ = request.split(b"\r\n", 1)[0].decode("ascii").split(" ")
            parsed = urlsplit(target)
            values = parse_qs(parsed.query, keep_blank_values=True)
            valid_state = values.get("state", [])
            if (
                method == "GET"
                and parsed.path == "/"
                and len(valid_state) == 1
                and secrets.compare_digest(valid_state[0], state)
            ):
                if not result.done():
                    if "error" in values:
                        result.set_result(None)
                    elif len(values.get("code", [])) == 1 and _string(
                        values["code"][0]
                    ):
                        result.set_result(values["code"][0])
                    else:
                        raise ValueError
                    status = "200 OK"
        except (
            ValueError,
            TypeError,
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
                        f"HTTP/1.1 {status}\r\nContent-Type: text/plain\r\nCache-Control: no-store\r\nReferrer-Policy: no-referrer\r\nConnection: close\r\n\r\nReturn to the application. This page can be closed."
                    ).encode("ascii")
                )
                await writer.drain()
            except (ConnectionError, OSError):
                pass
            writer.close()

    try:
        server = await asyncio.start_server(callback, "127.0.0.1", 0, limit=8192)
        async with server:
            redirect = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/"
            url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(
                {
                    "client_id": client_id,
                    "redirect_uri": redirect,
                    "response_type": "code",
                    "scope": SCOPE,
                    "state": state,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    "access_type": "offline",
                    "prompt": "consent",
                }
            )
            try:
                opened = opener(url)
            except Exception:
                raise AuthError("System browser could not be opened.") from None
            if not opened:
                raise AuthError("System browser could not be opened.")
            code = await asyncio.wait_for(result, timeout)
            if code is None:
                raise AuthError("Authorization denied; run login explicitly to retry.")
            return code, verifier, redirect
    except (OSError, TimeoutError):
        raise AuthError("Local OAuth callback failed or timed out.") from None


class OAuthSession:
    def __init__(self, *, store=None, transport=None, now=time.time):
        self.store = store if store is not None else CredentialStore()
        self.transport = transport
        self.now = now
        self.lock = asyncio.Lock()
        self.failed = False

    async def _request(self, method, url, **kwargs):
        try:
            async with httpx.AsyncClient(
                transport=self.transport, timeout=10, follow_redirects=False
            ) as client:
                return await client.request(method, url, **kwargs)
        except httpx.HTTPError:
            raise AuthError(
                "YouTube authentication network failure; retry manually."
            ) from None

    def invalidate(self):
        self.failed = True
        self.store.delete()

    def _tokens(self, response, client, previous=None):
        try:
            data = response.json()
            if (
                response.status_code != 200
                or data.get("token_type", "").lower() != "bearer"
            ):
                raise ValueError
            expiry = data["expires_in"]
            if type(expiry) is not int or expiry <= 60:
                raise ValueError
            scope = data.get("scope", SCOPE if previous else "")
            if set(scope.split()) != {SCOPE}:
                raise ValueError
            value = dict(
                client,
                access_token=data["access_token"],
                refresh_token=data.get(
                    "refresh_token", (previous or {}).get("refresh_token")
                ),
                expires_at=self.now() + expiry,
                scope=SCOPE,
            )
            if not _valid_record(value):
                raise ValueError
            return value
        except (ValueError, KeyError, TypeError, AttributeError):
            raise AuthError(
                "OAuth token response rejected; reauthenticate explicitly."
            ) from None

    async def login(self, client_file, **browser_options):
        client = load_desktop_client(client_file)
        self.store.load()  # Fail before opening the browser if the vault is inaccessible.
        code, verifier, redirect = await authorization_code(
            client["client_id"], **browser_options
        )
        response = await self._request(
            "POST",
            TOKEN_URL,
            data=dict(
                client,
                code=code,
                code_verifier=verifier,
                redirect_uri=redirect,
                grant_type="authorization_code",
            ),
        )
        record = self._tokens(response, client)
        self.store.save(record)
        self.failed = False

    async def access_token(self):
        async with self.lock:
            if self.failed:
                raise AuthError("Authentication stopped; reauthenticate explicitly.")
            record = self.store.load()  # Also observes logout from another process.
            if not _valid_record(record):
                self.failed = True
                raise AuthError(
                    "Saved OAuth credentials missing or invalid; run login."
                )
            if record["expires_at"] > self.now() + 60:
                return record["access_token"]
            try:
                client = {k: record[k] for k in ("client_id", "client_secret")}
                response = await self._request(
                    "POST",
                    TOKEN_URL,
                    data=dict(
                        client,
                        refresh_token=record["refresh_token"],
                        grant_type="refresh_token",
                    ),
                )
                updated = self._tokens(response, client, record)
                self.store.save(updated)
                return updated["access_token"]
            except AuthError:
                self.invalidate()
                raise

    def stream_window(self):
        """Reconnect before the current access token's refresh margin."""
        record = self.store.load()
        if self.failed or not _valid_record(record):
            raise AuthError("Authentication stopped; reauthenticate explicitly.")
        return max(0.01, record["expires_at"] - self.now() - 60)

    async def api(self, resource, params):
        if resource not in ("channels", "liveBroadcasts"):
            raise AuthError("Unsupported read-only resource.")
        token = await self.access_token()
        response = await self._request(
            "GET",
            API + resource,
            params=params,
            headers={"Authorization": "Bearer " + token},
        )
        if response.status_code == 401:
            self.invalidate()
            raise AuthError("Authorization revoked; run login again.")
        try:
            data = response.json()
            if response.status_code != 200 or not isinstance(data.get("items"), list):
                raise ValueError
            return data
        except (ValueError, TypeError, AttributeError):
            raise AuthError(
                "YouTube account/broadcast lookup failed; check authorization and quota."
            ) from None

    async def channels(self):
        data = await self.api("channels", {"part": "id,snippet", "mine": "true"})
        return [
            {"channel_id": item["id"], "title": item["snippet"]["title"]}
            for item in data["items"]
        ]

    async def broadcasts(self):
        items, cursor = [], None
        for _ in range(10):
            params = {"part": "id,snippet,status", "mine": "true", "maxResults": 50}
            if cursor:
                params["pageToken"] = cursor
            data = await self.api("liveBroadcasts", params)
            items.extend(data["items"])
            cursor = data.get("nextPageToken")
            if not cursor:
                return items
        raise AuthError("Broadcast listing limit reached; select a known broadcast ID.")

    async def resolve(self, channel_id, broadcast_id, expected_chat=None):
        try:
            if channel_id not in {c["channel_id"] for c in await self.channels()}:
                raise ValueError
            data = await self.api(
                "liveBroadcasts", {"part": "id,snippet,status", "id": broadcast_id}
            )
            if len(data["items"]) != 1:
                raise ValueError
            item = data["items"][0]
            snippet = item["snippet"]
            chat = snippet["liveChatId"]
            if (
                item["id"] != broadcast_id
                or snippet["channelId"] != channel_id
                or not _string(chat)
                or item["status"]["lifeCycleStatus"] in ("complete", "revoked")
                or (expected_chat is not None and chat != expected_chat)
            ):
                raise ValueError
            return {
                "channel_id": channel_id,
                "broadcast_id": broadcast_id,
                "live_chat_id": chat,
                "title": snippet["title"],
            }
        except (KeyError, TypeError, ValueError):
            raise AuthError(
                "Account, broadcast or live chat mismatch; select the intended broadcast."
            ) from None

    async def logout(self):
        record = self.store.load()
        self.invalidate()  # Local removal does not depend on network success.
        if _valid_record(record):
            try:
                response = await self._request(
                    "POST", REVOKE_URL, data={"token": record["refresh_token"]}
                )
            except AuthError:
                raise AuthError(
                    "Local logout complete; remote revocation unconfirmed. Remove access in Google Account."
                ) from None
            if response.status_code != 200:
                raise AuthError(
                    "Local logout complete; remote revocation unconfirmed. Remove access in Google Account."
                )


async def prepare_access(settings, *, transport=None, session=None):
    if settings.auth_mode == "manual":
        token = os.environ.get("YOUTUBE_ACCESS_TOKEN", "").strip()
        if not _string(token):
            raise AuthError("Set YOUTUBE_ACCESS_TOKEN before enabling chat.")
        return token, None
    session = session if session is not None else OAuthSession(transport=transport)
    await session.resolve(
        settings.channel_id, settings.broadcast_id, settings.live_chat_id
    )
    return await session.access_token(), session


async def _main(args):
    session = OAuthSession()
    if args.command == "login":
        await session.login(args.client_file)
        print(
            "Authorization saved in OS credential vault. Confirm channel and broadcast before enabling chat."
        )
    elif args.command == "logout":
        await session.logout()
        print("Local credentials removed; any saved valid token was revoked.")
    elif args.command == "status":
        print(json.dumps(await session.channels(), ensure_ascii=True))
    elif args.command == "broadcasts":
        items = await session.broadcasts()
        print(
            json.dumps(
                [
                    {
                        "broadcast_id": i["id"],
                        "channel_id": i["snippet"]["channelId"],
                        "title": i["snippet"]["title"],
                        "status": i["status"]["lifeCycleStatus"],
                    }
                    for i in items
                ],
                ensure_ascii=True,
            )
        )
    else:
        print(
            json.dumps(
                await session.resolve(args.channel_id, args.broadcast_id),
                ensure_ascii=True,
            )
        )


def main():
    parser = argparse.ArgumentParser(
        description="Explicit YouTube Desktop OAuth; read-only scope."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    login = sub.add_parser("login")
    login.add_argument("--client-file", default="secrets/youtube-desktop.json")
    for name in ("logout", "status", "broadcasts"):
        sub.add_parser(name)
    select = sub.add_parser("select")
    select.add_argument("--channel-id", required=True)
    select.add_argument("--broadcast-id", required=True)
    try:
        asyncio.run(_main(parser.parse_args()))
    except AuthError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (KeyError, TypeError, ValueError):
        print(
            "YouTube authorization operation failed. Check OS vault/client setup, account and broadcast; use login explicitly if authorization expired. No automatic retry.",
            file=sys.stderr,
        )
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
