"""Bounded, non-speaking diagnostics. Never infer model readiness from TCP."""

import asyncio
import json
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from time import monotonic
from urllib.parse import urlsplit

import httpx

from virtual_ai.audio.player import load_backend
from virtual_ai.config import load_config
from virtual_ai.integrations.vts.base import AvatarError
from virtual_ai.integrations.vts.client import VTSClient
from virtual_ai.integrations.youtube import ENDPOINT
from virtual_ai.integrations.youtube_auth import AuthError, prepare_access
from virtual_ai.prompting import load_system_prompt

OK = "정상"
DEGRADED = "기능 축소"
RECOVERY = "복구 필요"
DISABLED = "사용 안 함"


@dataclass(frozen=True)
class Check:
    component: str
    status: str
    reason: str
    seconds: float = 0.0


def report_data(checks):
    return [asdict(check) for check in checks]


async def _json(client, url, **kwargs):
    async with client.stream("GET", url, **kwargs) as response:
        if response.status_code in (401, 403):
            raise PermissionError
        response.raise_for_status()
        raw = bytearray()
        async for chunk in response.aiter_bytes():
            raw.extend(chunk)
            if len(raw) > 1024 * 1024:
                raise ValueError
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError
        return value


async def _tcp(url):
    parsed = urlsplit(url)
    _, writer = await asyncio.open_connection(
        parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)
    )
    writer.close()
    await writer.wait_closed()


def _writable(path, *, target=False):
    """Test a sibling temporary file, never replace existing subtitles."""
    path = Path(path)
    directory = path.parent if target else path
    directory.mkdir(parents=True, exist_ok=True)
    if target and path.exists():
        if not path.is_file() or path.is_symlink():
            raise OSError
        with path.open("r+b"):
            pass
    with tempfile.NamedTemporaryFile(
        dir=directory, prefix="diagnostic-", suffix=".tmp"
    ) as stream:
        stream.write(b"probe")
        stream.flush()


async def check_health(
    settings,
    *,
    audio_directory=Path("generated_audio"),
    transport=None,
    audio_backend=None,
    vts_factory=VTSClient,
    tcp_probe=_tcp,
    disk_usage=shutil.disk_usage,
    timeout=3,
):
    checks = [Check("config", OK, "validated")]

    async def probe(
        component, action, *, enabled=True, success="checked", failure=RECOVERY
    ):
        if not enabled:
            checks.append(Check(component, DISABLED, "disabled"))
            return
        started = monotonic()
        try:
            async with asyncio.timeout(timeout):
                result = await action()
            status, reason = result or (OK, success)
        except PermissionError:
            status, reason = failure, "authentication_or_permission"
        except (TimeoutError, httpx.TimeoutException):
            status, reason = failure, "timeout"
        except (httpx.ConnectError, ConnectionError):
            status, reason = failure, "connection"
        except httpx.HTTPStatusError:
            status, reason = failure, "http_status"
        except Exception:
            # Exception text may contain tokens, transcripts, URLs or local paths.
            status, reason = failure, "invalid_or_unavailable"
        checks.append(Check(component, status, reason, round(monotonic() - started, 6)))

    async def files():
        load_system_prompt(settings.system_prompt_path)

    await probe("prompt_file", files)
    async with httpx.AsyncClient(
        timeout=timeout, trust_env=False, follow_redirects=False, transport=transport
    ) as client:

        async def llm():
            base = settings.base_url.rstrip("/")
            version = await _json(client, base + "/api/extra/version")
            model = await _json(client, base + "/api/v1/model")
            context = await _json(client, base + "/api/extra/true_max_context_length")
            if version.get("result") != "KoboldCpp" or not isinstance(
                version.get("version"), str
            ):
                return RECOVERY, "server_identity"
            name = model.get("result")
            if version.get("protected") is True or name == "koboldcpp/protected-model":
                return RECOVERY, "authentication_required"
            if (
                not isinstance(name, str)
                or not name.strip()
                or name.lower() in ("none", "no model", "disabled")
            ):
                return RECOVERY, "model_unavailable"
            if version.get("llm") is not True:
                return RECOVERY, "model_unavailable"
            limit = context.get("value")
            if type(limit) is not int or limit < settings.context_tokens:
                return RECOVERY, "context_mismatch"
            if settings.server_abort_enabled and version["version"] != "1.121":
                return RECOVERY, "abort_contract_unverified"
            return OK, "model_reported_loaded_inference_not_tested"

        await probe("llm", llm, enabled=settings.backend not in ("mock", "fake"))

        async def youtube():
            try:
                token, _ = await prepare_access(settings.youtube, transport=transport)
            except AuthError:
                return RECOVERY, (
                    "token_missing_or_invalid"
                    if settings.youtube.auth_mode == "manual"
                    else "authentication_or_broadcast_selection"
                )
            data = await _json(
                client,
                ENDPOINT,
                headers={"Authorization": "Bearer " + token},
                params={
                    "liveChatId": settings.youtube.live_chat_id,
                    "part": "id",
                    "maxResults": 200,
                },
            )
            if not isinstance(data.get("items"), list) or not isinstance(
                data.get("nextPageToken"), str
            ):
                return RECOVERY, "invalid_response"
            if data.get("offlineAt"):
                return RECOVERY, "chat_ended"
            if settings.youtube.transport == "stream":
                return DEGRADED, "rest_authenticated_stream_unverified"
            return OK, "authenticated_read_only_messages_discarded"

        await probe("youtube", youtube, enabled=settings.youtube.enabled)

        async def chzzk():
            from virtual_ai.integrations.chzzk_auth import ChzzkSession

            try:
                await ChzzkSession(transport=transport).verify(
                    settings.chzzk.channel_id
                )
            except AuthError:
                return RECOVERY, "authentication_or_channel_selection"
            return DEGRADED, "authenticated_session_unverified"

        await probe("chzzk", chzzk, enabled=settings.chzzk.enabled)

    async def tts():
        await tcp_probe(settings.tts.base_url)
        return DEGRADED, "tcp_only_model_unverified"

    await probe("tts", tts, enabled=settings.tts.enabled)

    async def reference():
        host = urlsplit(settings.tts.base_url).hostname
        path = Path(settings.tts.ref_audio_path)
        if host not in ("localhost", "127.0.0.1", "::1") or not path.is_absolute():
            return DEGRADED, "server_side_file_unverified"
        with path.open("rb") as stream:
            if not stream.read(1):
                return RECOVERY, "reference_empty"
        return OK, "reference_readable_transcript_and_rights_unverified"

    await probe("tts_reference", reference, enabled=settings.tts.enabled)

    async def audio():
        backend = audio_backend or load_backend()
        device = backend.query_devices(settings.audio.output_device, "output")
        if device["max_output_channels"] < 1:
            return RECOVERY, "no_output_channels"
        backend.check_output_settings(
            device=settings.audio.output_device,
            channels=1,
            dtype="int16",
            samplerate=device["default_samplerate"],
        )
        return OK, "format_supported_stream_and_listening_not_tested"

    await probe("audio", audio, enabled=settings.audio.enabled)

    async def audio_files():
        _writable(audio_directory)

    await probe(
        "audio_files",
        audio_files,
        enabled=settings.tts.enabled and settings.audio.enabled,
    )

    async def subtitles():
        _writable(settings.subtitles.path, target=True)
        return OK, "sibling_write_target_opened_atomic_replace_not_tested"

    await probe("subtitles", subtitles, enabled=settings.subtitles.enabled)

    async def disk():
        path = Path(audio_directory).resolve()
        while not path.exists():
            path = path.parent
        if disk_usage(path).free < 256 * 1024 * 1024:
            return RECOVERY, "less_than_256_mib"
        return OK, "at_least_256_mib_not_recording_capacity_guarantee"

    await probe("disk", disk)

    async def vts():
        avatar = vts_factory(settings.vts)
        try:
            try:
                await avatar.connect()  # Saved token only. Never request approval.
            except AvatarError as exc:
                # Classify known client messages, never return their raw text.
                message = str(exc).lower()
                if "token" in message or "authentication" in message:
                    return RECOVERY, "saved_authentication"
                if "cannot connect" in message:
                    return RECOVERY, "connection"
                return RECOVERY, "api_protocol_unavailable"
            except Exception:
                return RECOVERY, "connection_or_saved_authentication"
            try:
                data = await avatar.list_hotkeys()
                if not settings.vts.expected_model_id:
                    return RECOVERY, "expected_model_missing"
                if data["modelID"] != settings.vts.expected_model_id:
                    return RECOVERY, "model_mismatch"
                for key in settings.vts.expression_hotkeys.values():
                    if key and not any(
                        h["hotkeyID"] == key and h["type"] == "ToggleExpression"
                        for h in data["availableHotkeys"]
                    ):
                        return RECOVERY, "expression_mapping"
                if settings.vts.lipsync_enabled:
                    parameters = (await avatar.list_parameters())["parameters"]
                    if not any(
                        p["name"] == settings.vts.mouth_parameter
                        and p["min"] <= 0
                        and p["max"] >= 1
                        for p in parameters
                    ):
                        return RECOVERY, "mouth_mapping"
            except Exception:
                return RECOVERY, "model_or_mapping_unavailable"
            return OK, "authenticated_model_reported_visual_unverified"
        finally:
            await avatar.aclose()

    await probe("vts", vts, enabled=settings.vts.enabled)

    async def rag_check():
        from virtual_ai.rag.store import RAGStore

        # Starting an enabled store is explicit opt-in; disabled checks never touch it.
        store = await asyncio.to_thread(RAGStore, settings.rag.db_path)
        await asyncio.to_thread(store.revision)
        return OK, "local_store_checked_model_quality_unverified"

    await probe("rag", rag_check, enabled=settings.rag.enabled)
    return checks


async def diagnose(path, **kwargs):
    try:
        settings, _ = load_config(Path(path).resolve())
    except OSError:
        return [Check("local_files", RECOVERY, "config_or_character_unreadable")]
    except ValueError:
        return [Check("config", RECOVERY, "invalid_configuration")]
    return await check_health(settings, **kwargs)
