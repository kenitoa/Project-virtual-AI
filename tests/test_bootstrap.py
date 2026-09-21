"""Public CLI contract and privacy checks; no server or GPU required."""

import asyncio
import logging
import subprocess
import sys
from pathlib import Path

from virtual_ai.app import Application
from virtual_ai.config import load_config
from virtual_ai.llm.mock import FakeLLM
from virtual_ai.schemas import ChatInput, Viewer

ROOT = Path(__file__).resolve().parents[1]


def cli(*args, input=None):
    return subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "virtual_ai", *args],
        input=input,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=ROOT,
        timeout=10,
    )


def test_mock_cli_runs_without_network_or_gpu():
    result = cli("--backend", "mock", "--once", "private-user-input")
    assert result.returncode == 0
    assert "안녕하세요" in result.stdout
    assert "<think>" not in result.stdout
    assert "queue_seconds=" in result.stderr
    assert "llm_seconds=" in result.stderr
    assert "application_status=closed" in result.stderr
    assert "private-user-input" not in result.stderr
    assert "안녕하세요" not in result.stderr


def test_invalid_config_has_clear_error_without_traceback(tmp_path):
    config = tmp_path / "invalid.yaml"
    config.write_text("llm: [", encoding="utf-8")
    result = cli("--config", str(config), "--once", "hello")
    assert result.returncode == 2
    assert "invalid YAML" in result.stderr
    assert "Traceback" not in result.stderr


def test_quit_and_eof_exit_cleanly():
    for text in ("/quit\n", ""):
        result = cli("--backend", "mock", input=text)
        assert result.returncode == 0
        assert "application_status=closed" in result.stderr


def test_empty_input_rejected():
    result = cli("--backend", "mock", "--once", " ")
    assert result.returncode == 2
    assert "Traceback" not in result.stderr


def test_injected_llm_client_and_private_metrics(caplog):
    class CustomLLM(FakeLLM):
        async def generate(self, messages):
            return "custom answer"

    async def run():
        settings, character = load_config(ROOT / "configs/app.example.yaml")
        output = []
        app = Application(settings, character, CustomLLM(), output.append)
        assert app.submit(
            ChatInput(
                Viewer("private-platform", "private-id"),
                "private-text",
                "private-message",
            )
        )
        await app.process_next()
        assert output == ["custom answer"]

    with caplog.at_level(logging.INFO, logger="virtual_ai.app"):
        asyncio.run(run())
    assert "queue_seconds=" in caplog.text
    assert "llm_seconds=" in caplog.text
    assert "private-" not in caplog.text
    assert "custom answer" not in caplog.text
