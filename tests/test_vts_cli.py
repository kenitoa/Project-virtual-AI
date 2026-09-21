import argparse
import asyncio
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from virtual_ai.config import VTSSettings, load_config
from virtual_ai.integrations.vts import __main__ as cli

ROOT = Path(__file__).resolve().parents[1]


def config(tmp_path, **vts):
    path = tmp_path / "app.yaml"
    path.write_text(
        yaml.safe_dump(
            {"character_path": str(ROOT / "configs/character.yaml"), "vts": vts}
        ),
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize(
    "options",
    [
        {"enabled": 1},
        {"url": "http://127.0.0.1:8001"},
        {"url": "ws://example.com:8001"},
        {"url": "ws://127.0.0.1:0"},
        {"url": "ws://user:pass@localhost:8001"},
        {"url": "ws://localhost:8001/path"},
        {"url": "ws://localhost:8001?x=1"},
        {"url": "ws://localhost:bad"},
        {"url": 123},
        {"token_path": "public/token.json"},
        {"request_timeout_seconds": True},
        {"request_timeout_seconds": 0},
        {"authentication_timeout_seconds": float("inf")},
        {"authentication_timeout_seconds": 301},
        {"plugin_name": "a"},
        {"plugin_name": "name" + " " * 40},
        {"plugin_developer": None},
        {"expected_model_id": 1},
        {"expression_hotkeys": {"neutral": "id"}},
        {"expression_hotkeys": {"happy": 123}},
        {"expression_hotkeys": {"happy": "same", "sad": "same"}},
    ],
)
def test_configuration_restrictions(options):
    with pytest.raises(ValueError):
        VTSSettings(**options)


def test_relative_token_path_and_immutable_mapping(tmp_path):
    path = config(
        tmp_path, token_path=".local/my-token.json", expression_hotkeys={"happy": "id"}
    )
    settings, _ = load_config(path)
    assert settings.vts.token_path == str(tmp_path / ".local/my-token.json")
    assert not settings.vts.enabled
    assert not (tmp_path / ".local").exists()
    with pytest.raises(TypeError):
        settings.vts.expression_hotkeys["happy"] = "other"
    with pytest.raises(ValueError, match="vts"):
        load_config(config(tmp_path, unknown=True))


@pytest.mark.parametrize(
    "arguments, code",
    [
        (["--authenticate"], 2),
        (["--list-hotkeys"], 2),
        (["--expression", "happy"], 2),
        (["--expression", "raw-id"], 2),
        (["--expression", "happy", "--hold-seconds", "nan"], 2),
        (["--authenticate", "--list-hotkeys"], 2),
    ],
)
def test_disabled_and_invalid_cli(tmp_path, arguments, code):
    path = config(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "virtual_ai.integrations.vts",
            "--config",
            str(path),
            *arguments,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    assert result.returncode == code
    assert "Traceback" not in result.stderr
    assert not (tmp_path / ".local").exists()


@pytest.mark.parametrize(
    "action", ["authenticate", "list_hotkeys", "list_parameters", "expression"]
)
def test_cli_actions_and_cleanup(tmp_path, monkeypatch, action):
    calls = []

    class Client:
        def __init__(self, settings):
            calls.append("created")

        async def connect(self, **kwargs):
            calls.append(kwargs)

        async def list_hotkeys(self):
            calls.append("list")
            return {"modelID": "test", "availableHotkeys": []}

        async def list_parameters(self):
            calls.append("parameters")
            return {"modelID": "test", "parameters": []}

        async def set_expression(self, expression):
            calls.append(expression)

        async def reset(self):
            calls.append("reset")

        async def aclose(self):
            calls.append("close")

    monkeypatch.setattr(cli, "VTSClient", Client)
    args = argparse.Namespace(
        config=str(config(tmp_path, enabled=True)),
        authenticate=action == "authenticate",
        list_hotkeys=action == "list_hotkeys",
        list_parameters=action == "list_parameters",
        expression="happy",
        hold_seconds=0.001,
    )
    asyncio.run(cli.run(args))
    assert calls[-1] == "close"
    if action == "expression":
        assert calls[-3:] == ["happy", "reset", "close"]


def test_cli_cancellation_still_closes(tmp_path, monkeypatch):
    closed = []

    class Client:
        def __init__(self, settings):
            pass

        async def connect(self, **kwargs):
            raise asyncio.CancelledError

        async def aclose(self):
            closed.append(True)

    monkeypatch.setattr(cli, "VTSClient", Client)
    args = argparse.Namespace(
        config=str(config(tmp_path, enabled=True)), authenticate=True
    )
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(cli.run(args))
    assert closed == [True]


def test_standalone_cli_does_not_import_conversation_pipeline():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import virtual_ai.integrations.vts.__main__; assert 'virtual_ai.app' not in sys.modules; assert not any(x.startswith(('virtual_ai.llm', 'virtual_ai.tts', 'virtual_ai.audio')) for x in sys.modules)",
        ],
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0
