"""Reproducible content-free session evidence; no automatic release approval."""

import asyncio
import hashlib
import json
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from uuid import uuid4

from virtual_ai.tts.base import TTSError


def fingerprint(app):
    source = Path(__file__).parent
    digest = hashlib.sha256()
    for path in sorted(source.rglob("*")):
        if path.is_file() and path.suffix in (".py", ".html", ".md", ".sql"):
            digest.update(path.relative_to(source).as_posix().encode())
            digest.update(path.read_bytes())
    revision = "unavailable"
    dirty = None
    try:
        root = source.parent.parent
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=root,
                capture_output=True,
                text=True,
                check=True,
                timeout=5,
            ).stdout.strip()
        )
    except (OSError, subprocess.SubprocessError):
        pass
    return {
        "source_sha256": digest.hexdigest(),
        "git_revision": revision,
        "worktree_dirty": dirty,
        "settings_sha256": hashlib.sha256(repr(app.settings).encode()).hexdigest(),
        "character_prompt_sha256": hashlib.sha256(
            (
                json.dumps(app.character, ensure_ascii=False, sort_keys=True)
                + app.system_prompt
            ).encode()
        ).hexdigest(),
        "python": platform.python_version(),
        "os": platform.platform(),
        "configured_model": app.settings.model,
        "loaded_model_verified": False,
    }


async def warmup(app):
    """Synthesize one fixed sentence without speaker playback or history writes."""
    if not app._voice_enabled:
        return {"status": "disabled"}
    app.audio_directory.mkdir(parents=True, exist_ok=True)
    destination = app.audio_directory / (str(uuid4()) + ".wav")
    started = monotonic()
    try:
        async with asyncio.timeout(min(120, app.settings.tts.total_timeout_seconds)):
            await app.tts.synthesize("안녕하세요. 대화를 준비하고 있어요.", destination)
        return {
            "status": "completed",
            "seconds": monotonic() - started,
            "played": False,
        }
    except (TTSError, OSError, TimeoutError):
        app._voice_enabled = False
        app._component_faults["tts"] = "warmup_failed"
        return {"status": "failed", "seconds": monotonic() - started, "played": False}
    finally:
        try:
            destination.unlink(missing_ok=True)
        except OSError:
            app.runtime.cleanup_failed = True


def write_report(path, app, identity):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "schema": 1,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "identity": identity,
        "observations": app.observations.snapshot(),
        "warmup": app.warmup_status,
        "cleanup_failed": app.runtime.cleanup_failed,
        "component_faults": dict(app._component_faults),
        "obs": app.obs.status() if app.obs else {"state": "disabled"},
        "acceptance": {
            "public_broadcast": "not_evaluated",
            "human_listening": "not_evaluated",
            "human_dialogue_quality": "not_evaluated",
            "release_approved": False,
        },
    }
    with path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
