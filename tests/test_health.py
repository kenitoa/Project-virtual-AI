import asyncio
import json
from argparse import Namespace
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from virtual_ai import app as app_module
from virtual_ai.config import (
    AudioSettings,
    SubtitleSettings,
    TTSSettings,
    VTSSettings,
    YouTubeSettings,
    load_config,
)
from virtual_ai.health import (
    DEGRADED,
    DISABLED,
    OK,
    RECOVERY,
    Check,
    check_health,
    diagnose,
    report_data,
)

ROOT = Path(__file__).resolve().parents[1]


def settings():
    return load_config(ROOT / "configs/app.example.yaml")[0]


def run_checks(config, tmp_path, **kwargs):
    return {
        c.component: c
        for c in asyncio.run(
            check_health(config, audio_directory=tmp_path / "audio", **kwargs)
        )
    }


def test_disabled_features_do_not_touch_network_device_or_vts(tmp_path):
    def fail(*args, **kwargs):
        pytest.fail("disabled feature accessed")

    result = run_checks(
        settings(),
        tmp_path,
        transport=httpx.MockTransport(fail),
        audio_backend=object(),
        vts_factory=fail,
        tcp_probe=fail,
    )
    assert result["config"].status == OK
    for name in ("llm", "tts", "audio", "vts", "youtube", "subtitles"):
        assert result[name].status == DISABLED


@pytest.mark.parametrize(
    "mode,reason",
    [
        ("valid", "model_reported_loaded_inference_not_tested"),
        ("empty", "model_unavailable"),
        ("context", "context_mismatch"),
        ("identity", "server_identity"),
        ("forbidden", "authentication_or_permission"),
        ("redirect", "http_status"),
        ("bad_json", "invalid_or_unavailable"),
        ("timeout", "timeout"),
    ],
)
def test_llm_readiness_is_not_port_success(tmp_path, mode, reason):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        assert request.method == "GET"
        assert request.url.path != "/health"
        if mode == "timeout":
            raise httpx.ReadTimeout("secret-token")
        if mode == "forbidden":
            return httpx.Response(403, text="secret-token")
        if mode == "redirect":
            return httpx.Response(
                302, headers={"location": "https://example.invalid/secret-token"}
            )
        if mode == "bad_json":
            return httpx.Response(200, text="secret-token")
        data = {
            "/api/extra/version": {
                "result": "wrong" if mode == "identity" else "KoboldCpp",
                "version": "1.121",
                "llm": mode != "empty",
            },
            "/api/v1/model": {"result": "model"},
            "/api/extra/true_max_context_length": {
                "value": 1024 if mode == "context" else 4096
            },
        }
        return httpx.Response(200, json=data[request.url.path])

    result = run_checks(
        replace(settings(), backend="koboldcpp"),
        tmp_path,
        transport=httpx.MockTransport(handler),
    )
    assert result["llm"].reason == reason
    assert result["llm"].status == (OK if mode == "valid" else RECOVERY)
    assert "secret-token" not in json.dumps(report_data(result.values()))


def test_tts_tcp_and_reference_do_not_claim_synthesis(tmp_path):
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"not a verified recording")

    async def tcp(_):
        pass

    config = replace(
        settings(), tts=TTSSettings(enabled=True, ref_audio_path=str(reference))
    )
    result = run_checks(config, tmp_path, tcp_probe=tcp)
    assert result["tts"].status == DEGRADED
    assert result["tts_reference"].status == OK
    reference.unlink()
    assert (
        run_checks(config, tmp_path, tcp_probe=tcp)["tts_reference"].status == RECOVERY
    )
    remote = replace(
        config, tts=replace(config.tts, base_url="http://remote.example:9880")
    )
    assert (
        run_checks(remote, tmp_path, tcp_probe=tcp)["tts_reference"].status == DEGRADED
    )


def test_files_devices_and_disk_are_separate_and_subtitles_preserved(tmp_path):
    target = tmp_path / ".local" / "subtitle.txt"
    target.parent.mkdir()
    target.write_text("existing subtitle", encoding="utf-8")

    class Backend:
        def query_devices(self, *_):
            raise RuntimeError("private-device-name")

    config = replace(
        settings(),
        audio=AudioSettings(True),
        subtitles=SubtitleSettings(True, str(target)),
    )
    result = run_checks(
        config,
        tmp_path,
        audio_backend=Backend(),
        disk_usage=lambda _: SimpleNamespace(free=1),
    )
    assert result["audio"].status == RECOVERY
    assert result["disk"].status == RECOVERY
    assert result["subtitles"].status == OK
    assert target.read_text(encoding="utf-8") == "existing subtitle"
    assert list(target.parent.iterdir()) == [target]
    target.unlink()
    target.mkdir()
    assert (
        run_checks(config, tmp_path, audio_backend=Backend())["subtitles"].status
        == RECOVERY
    )


@pytest.mark.parametrize(
    "mode,reason",
    [
        ("ok", "authenticated_model_reported_visual_unverified"),
        ("auth", "connection_or_saved_authentication"),
        ("model", "model_or_mapping_unavailable"),
        ("mismatch", "model_mismatch"),
        ("mouth", "mouth_mapping"),
    ],
)
def test_vts_saved_auth_and_model_queries_only(tmp_path, mode, reason):
    calls = []

    class Avatar:
        def __init__(self, _):
            pass

        async def connect(self):
            calls.append("saved_auth")
            if mode == "auth":
                raise RuntimeError("secret-token")

        async def list_hotkeys(self):
            if mode == "model":
                raise RuntimeError("private-model")
            return {
                "modelID": "other" if mode == "mismatch" else "model",
                "availableHotkeys": [],
            }

        async def list_parameters(self):
            return {
                "parameters": []
                if mode == "mouth"
                else [{"name": "MouthInput", "min": 0, "max": 1}]
            }

        async def aclose(self):
            calls.append("close")

    config = replace(
        settings(),
        vts=VTSSettings(
            enabled=True,
            expected_model_id="model",
            lipsync_enabled=True,
            mouth_parameter="MouthInput",
        ),
    )
    result = run_checks(config, tmp_path, vts_factory=Avatar)
    assert result["vts"].reason == reason
    assert calls == ["saved_auth", "close"]


def test_youtube_auth_no_messages_returned_and_no_token_leak(tmp_path, monkeypatch):
    config = replace(settings(), youtube=YouTubeSettings(True, "private-chat"))
    monkeypatch.delenv("YOUTUBE_ACCESS_TOKEN", raising=False)
    assert run_checks(config, tmp_path)["youtube"].reason == "token_missing_or_invalid"
    monkeypatch.setenv("YOUTUBE_ACCESS_TOKEN", "secret-token")

    def handler(request):
        assert (
            request.method == "GET"
            and request.headers["Authorization"] == "Bearer secret-token"
        )
        return httpx.Response(
            200,
            json={
                "items": [{"text": "private-message"}],
                "nextPageToken": "private-cursor",
            },
        )

    result = run_checks(config, tmp_path, transport=httpx.MockTransport(handler))
    assert result["youtube"].status == OK
    output = json.dumps(report_data(result.values()))
    assert not any(
        s in output
        for s in ["secret-token", "private-message", "private-cursor", "private-chat"]
    )


def test_invalid_config_report_omits_source(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("secret-token: [", encoding="utf-8")
    result = asyncio.run(diagnose(path))
    assert result[0].status == RECOVERY
    assert "secret-token" not in json.dumps(report_data(result))


def test_live_startup_core_failure_prevents_client_creation(monkeypatch):
    async def probe(_):
        return [Check("llm", RECOVERY, "connection")]

    monkeypatch.setattr(app_module, "check_health", probe)
    monkeypatch.setattr(
        app_module, "KoboldCppClient", lambda _: pytest.fail("client constructed")
    )
    with pytest.raises(ValueError, match="Startup requires recovery"):
        asyncio.run(
            app_module.run_cli(
                Namespace(
                    config=str(ROOT / "configs/app.example.yaml"),
                    backend="koboldcpp",
                    live=True,
                    once=None,
                )
            )
        )


def test_optional_vts_failure_reduces_feature_without_exiting(monkeypatch):
    from test_voice_pipeline import TTS, Player

    config, character = load_config(ROOT / "configs/app.example.yaml")
    config = replace(
        config,
        tts=TTSSettings(True, ref_audio_path="reference.wav"),
        audio=AudioSettings(True),
        vts=VTSSettings(enabled=True),
    )
    monkeypatch.setattr(app_module, "load_config", lambda _: (config, character))
    monkeypatch.setattr(app_module, "GPTSoVITSClient", lambda _: TTS())
    monkeypatch.setattr(app_module, "WAVPlayer", lambda _: Player())
    monkeypatch.setattr(
        app_module, "VTSClient", lambda _: pytest.fail("failed avatar constructed")
    )

    async def probe(_):
        return [Check("vts", RECOVERY, "connection")]

    async def console(app):
        assert app.runtime.paused
        assert app._voice_enabled
        assert "vts" in app.status()["disabled_features"]
        assert app.resume()

    monkeypatch.setattr(app_module, "check_health", probe)
    monkeypatch.setattr("virtual_ai.inputs.console.console", console)
    asyncio.run(
        app_module.run_cli(
            Namespace(
                config=str(ROOT / "configs/app.example.yaml"),
                backend="mock",
                live=True,
                once=None,
            )
        )
    )


def test_missing_local_file_is_not_reported_as_server_error(tmp_path):
    checks = asyncio.run(diagnose(tmp_path / "missing.yaml"))
    assert checks[0].component == "local_files"
    assert checks[0].status == RECOVERY
