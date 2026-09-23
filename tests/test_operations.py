import asyncio
import os
import shutil
import subprocess
from argparse import Namespace
from dataclasses import replace
from pathlib import Path

import pytest

from virtual_ai import app as module
from virtual_ai.config import YouTubeSettings, load_config

ROOT = Path(__file__).resolve().parents[1]


def test_local_mode_disables_broadcast_even_in_enabled_config(monkeypatch):
    settings, character = load_config(ROOT / "configs/app.example.yaml")
    settings = replace(settings, youtube=YouTubeSettings(True, "fixture-chat"))
    monkeypatch.setattr(module, "load_config", lambda _: (settings, character))
    monkeypatch.setattr(
        module, "YouTubeChat", lambda *a, **k: pytest.fail("broadcast constructed")
    )

    async def console(app):
        assert not app.settings.youtube.enabled
        assert not app.runtime.paused

    monkeypatch.setattr("virtual_ai.inputs.console.console", console)
    asyncio.run(
        module.run_cli(
            Namespace(
                config="unused", backend="mock", local_only=True, live=False, once=None
            )
        )
    )
    assert settings.youtube.enabled


@pytest.mark.skipif(os.name != "nt", reason="Windows launcher")
def test_powershell_local_launcher_preserves_config(tmp_path):
    config = ROOT / "configs/app.example.yaml"
    before = config.read_bytes()
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "scripts/start-local.ps1"),
            "-Once",
            "hello with spaces",
        ],
        cwd=tmp_path,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert config.read_bytes() == before


@pytest.mark.skipif(os.name != "nt", reason="Windows ownership guard")
@pytest.mark.parametrize("identity", ["owned", "stale", "foreign"])
def test_stop_only_verified_owner_with_mock_process(tmp_path, identity):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("common.ps1", "stop-owned-processes.ps1"):
        shutil.copyfile(ROOT / "scripts" / name, scripts / name)
    # Only the process APIs are mocked. No real termination is performed.
    harness = scripts / "test.ps1"
    harness.write_text(
        r"""param([string]$Identity)
. "$PSScriptRoot/common.ps1"
$tag = [guid]::NewGuid().ToString()
$stamp = [datetime]::UtcNow
$folder = Join-Path $ProjectRoot '.local/owned-processes'
New-Item -ItemType Directory -Force -Path $folder | Out-Null
$record = @{pid=1234; started=$stamp.ToString('o'); executable=$ProjectPython; session=$tag; root=$ProjectRoot; children=@()}
if ($Identity -eq 'stale') { $record.started = $stamp.AddDays(-1).ToString('o') }
if ($Identity -eq 'foreign') { $record.executable = 'different.exe' }
$record | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $folder ($tag+'.json'))
function global:Get-Process { param($Id,$ErrorAction) $p=[pscustomobject]@{Path=$ProjectPython;StartTime=$stamp;HasExited=$false}; $p | Add-Member -MemberType ScriptMethod -Name Refresh -Value {}; return $p }
function global:Get-CimInstance { param($ClassName,$Filter) return [pscustomobject]@{CommandLine="python -m virtual_ai --operator-session-id $tag"} }
function global:Stop-Process { param($InputObject,[switch]$Force,$ErrorAction) Write-Output 'VERIFIED_STOP' }
& "$PSScriptRoot/stop-owned-processes.ps1" -Force
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(harness),
            identity,
        ],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert (b"VERIFIED_STOP" in result.stdout) == (identity == "owned")
