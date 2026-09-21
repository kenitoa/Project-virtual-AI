"""Public CLI contract and privacy checks; no server or GPU required."""

import asyncio
import logging
import os
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import httpx
import pytest

from virtual_ai import app as app_module
from virtual_ai.app import Application
from virtual_ai.config import load_config
from virtual_ai.llm.koboldcpp import KoboldCppClient
from virtual_ai.llm.mock import FakeLLM
from virtual_ai.schemas import ChatInput, Viewer

ROOT = Path(__file__).resolve().parents[1]


def cli(*args, input=None, env=None, utf8=True):
    return subprocess.run(
        [sys.executable, *(["-X", "utf8"] if utf8 else []), "-m", "virtual_ai", *args],
        env=env,
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


@pytest.mark.parametrize(
    "args,input,expected,code",
    [
        (("--once", "hello"), None, "안녕하세요", 0),
        ((), "/forget\n/quit\n", "최근 대화 기록을 삭제했습니다", 0),
        (("--once", " "), None, "입력이 비었거나", 2),
    ],
)
def test_cli_uses_utf8_with_legacy_windows_streams(args, input, expected, code):
    env = {**os.environ, "PYTHONUTF8": "0", "PYTHONIOENCODING": "cp1252"}
    result = cli("--backend", "mock", *args, input=input, env=env, utf8=False)
    assert result.returncode == code
    assert expected in result.stdout + result.stderr
    assert "UnicodeEncodeError" not in result.stderr
    assert "application_status=closed" in result.stderr


@pytest.mark.parametrize("legacy_encoding", [False, True])
def test_invalid_config_has_clear_error_without_traceback(tmp_path, legacy_encoding):
    config = tmp_path / "invalid.yaml"
    config.write_text("llm: [", encoding="utf-8")
    env = (
        {**os.environ, "PYTHONUTF8": "0", "PYTHONIOENCODING": "cp1252"}
        if legacy_encoding
        else None
    )
    result = cli(
        "--config", str(config), "--once", "hello", env=env, utf8=not legacy_encoding
    )
    assert result.returncode == 2
    assert "설정/실행 오류:" in result.stderr
    assert "invalid YAML" in result.stderr
    assert "Traceback" not in result.stderr
    assert "UnicodeEncodeError" not in result.stderr


def test_configure_console_output_accepts_replacement_streams(monkeypatch):
    monkeypatch.setattr(sys, "stdout", object())
    monkeypatch.setattr(sys, "stderr", Namespace(reconfigure=None))
    app_module.configure_console_output()


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


@pytest.mark.parametrize("backend", ["mock", "fake"])
def test_mock_backends_never_create_http_client(monkeypatch, backend):
    def forbidden(*args, **kwargs):
        pytest.fail("mock must not create a network client")

    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    asyncio.run(
        app_module.run_cli(
            Namespace(
                config=str(ROOT / "configs/app.example.yaml"),
                backend=backend,
                once="hello",
            )
        )
    )


@pytest.mark.parametrize("status", [200, 401])
def test_real_backend_output_failure_exit_and_close(
    monkeypatch, capsys, caplog, status
):
    clients = []

    def handler(request):
        return httpx.Response(
            status,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "<think>private-reasoning</think>안녕하세요!"
                        }
                    }
                ]
            },
            headers={"x-private": "secret"},
        )

    def factory(settings):
        client = KoboldCppClient(settings, httpx.MockTransport(handler))
        clients.append(client)
        return client

    monkeypatch.setattr(app_module, "KoboldCppClient", factory)
    monkeypatch.setattr(
        sys, "argv", ["virtual_ai", "--backend", "koboldcpp", "--once", "private-input"]
    )
    with caplog.at_level(logging.INFO):
        if status == 200:
            app_module.main()
        else:
            with pytest.raises(SystemExit) as caught:
                app_module.main()
            assert caught.value.code == 1
    output = capsys.readouterr()
    assert clients[0]._client.is_closed
    if status == 200:
        assert "안녕하세요" in output.out
        assert "private-reasoning" not in output.out
    else:
        assert "HTTP 401" in output.err
        assert "안녕하세요" not in output.out
    for private in ("private-input", "private-reasoning", "안녕하세요", "secret"):
        assert private not in caplog.text


def test_config_selection_and_interactive_recovery(monkeypatch, tmp_path, capsys):
    config = tmp_path / "app.yaml"
    config.write_text(
        f'llm:\n  backend: koboldcpp\n  retries: 0\ncharacter_path: "{(ROOT / "configs/character.yaml").as_posix()}"\n',
        encoding="utf-8",
    )
    clients = []
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "recovered"}}]}
        )

    def factory(settings):
        assert settings.backend == "koboldcpp"
        client = KoboldCppClient(settings, httpx.MockTransport(handler))
        clients.append(client)
        return client

    async def console(app):
        for text in ("first", "second"):
            assert app.submit(ChatInput(Viewer("console", "local"), text, text))
            await app.process_next()

    monkeypatch.setattr(app_module, "KoboldCppClient", factory)
    monkeypatch.setattr("virtual_ai.inputs.console.console", console)
    asyncio.run(
        app_module.run_cli(Namespace(config=str(config), backend=None, once=None))
    )
    output = capsys.readouterr().out
    assert "HTTP 503" in output
    assert "recovered" in output
    assert len(calls) == 2
    assert clients[0]._client.is_closed
