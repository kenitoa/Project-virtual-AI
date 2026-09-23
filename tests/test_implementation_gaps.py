import importlib.util
import json
import os
import zipfile
from pathlib import Path

import psutil
import pytest

from virtual_ai import resource_monitor as monitor

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "bundle", ROOT / "scripts/build_source_bundle.py"
)
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)


@pytest.mark.parametrize(
    "name",
    [
        "configs/app.yaml",
        "tokens/access.json",
        ".local/memory.sqlite3",
        "engines/stt/.venv/private.py",
        "recordings/live.wav",
        "models/model.gguf",
        "../outside.py",
        "tests/__pycache__/cached.py",
    ],
)
def test_source_policy_excludes_runtime(name):
    assert not bundle.allowed(name)


def test_bundle_integrity_and_no_overwrite(tmp_path):
    output = tmp_path / "candidate.zip"
    result = bundle.build(ROOT, output)
    assert not result["release_approved"]
    with zipfile.ZipFile(output) as archive:
        manifest = json.loads(archive.read("SOURCE-MANIFEST.json"))
        assert "src/virtual_ai/stt/client.py" in manifest["files"]
        assert "engines/stt/uv.lock" in manifest["files"]
        assert (
            "src/virtual_ai/integrations/_youtube_proto/LICENSE-2.0.txt"
            in manifest["files"]
        )
        assert all(bundle.allowed(name) for name in manifest["files"])
    before = output.read_bytes()
    with pytest.raises(ValueError):
        bundle.build(ROOT, output)
    assert output.read_bytes() == before


def test_process_sample_and_exit(monkeypatch):
    probe = monitor.ProcessProbe(os.getpid())
    sample = probe.sample()
    assert sample["rss_bytes"] > 0 and sample["threads"] > 0
    assert "cmdline" not in sample and "connections" not in sample
    monkeypatch.setattr(probe.process, "is_running", lambda: False)
    assert probe.sample() == {"status": "exited_or_reused"}


def test_denied_is_not_zero_usage(monkeypatch):
    probe = monitor.ProcessProbe(os.getpid())

    def fail():
        raise psutil.AccessDenied()

    monkeypatch.setattr(probe.process, "memory_info", fail)
    assert probe.sample() == {"status": "access_denied"}


def test_gpu_unavailable_and_malformed(monkeypatch):
    monkeypatch.setattr(monitor.shutil, "which", lambda _: None)
    assert monitor.gpu_sample()["status"] == "unavailable"
    monkeypatch.setattr(monitor.shutil, "which", lambda _: "fixture")
    from types import SimpleNamespace

    monkeypatch.setattr(
        monitor.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(stdout="0, N/A, 1, 2"),
    )
    assert monitor.gpu_sample()["status"] == "unavailable"
