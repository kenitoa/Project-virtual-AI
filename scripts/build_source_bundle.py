"""Build a local, unapproved source candidate from an explicit file policy."""

import argparse
import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {
    "README.md",
    "CHANGELOG.md",
    "pyproject.toml",
    "uv.lock",
    "skill.md",
    "AGENTS.md",
    ".gitignore",
    ".gitattributes",
    "LICENSE",
    "LICENSE.md",
}
CONFIG_FILES = {
    "configs/app.example.yaml",
    "configs/character.yaml",
    "configs/performance-questions.json",
}
ENGINE_FILES = {"engines/stt/pyproject.toml", "engines/stt/uv.lock"}
VENDOR_LICENSES = {"src/virtual_ai/integrations/_youtube_proto/LICENSE-2.0.txt"}
EXTENSIONS = {
    "src": {".py", ".md", ".sql", ".proto"},
    "tests": {".py", ".json"},
    "scripts": {".py", ".ps1"},
    "docs": {".md"},
    ".github": {".yml", ".yaml"},
}


def allowed(name):
    path = Path(name)
    if (
        path.is_absolute()
        or ".." in path.parts
        or any(
            part
            in {
                ".venv",
                "__pycache__",
                ".local",
                "tokens",
                "recordings",
                "models",
                "secrets",
            }
            for part in path.parts
        )
    ):
        return False
    return name in ROOT_FILES | CONFIG_FILES | ENGINE_FILES | VENDOR_LICENSES or (
        path.parts[0] in EXTENSIONS and path.suffix in EXTENSIONS[path.parts[0]]
    )


def build(root, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("output exists")
    names = (
        subprocess.check_output(
            ["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=root
        )
        .decode()
        .split("\0")
    )
    files = {}
    for name in sorted(set(n for n in names if n and allowed(n))):
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError("unsafe link")
        if not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
            raise ValueError("missing or oversized source")
        files[name] = path.read_bytes()
    required = {
        "pyproject.toml",
        "uv.lock",
        "README.md",
        "configs/app.example.yaml",
        "src/virtual_ai/app.py",
    }
    if not required <= files.keys():
        raise ValueError("incomplete source")
    manifest = {
        "release_approved": False,
        "kind": "source-candidate",
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "dirty": bool(
            subprocess.check_output(["git", "status", "--porcelain"], cwd=root)
        ),
        "files": {n: hashlib.sha256(data).hexdigest() for n, data in files.items()},
        "limitations": "File policy is not a semantic secret scanner. Human review and release approval required.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with output.open("xb") as destination:
            created = True
            with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
                for name, data in files.items():
                    archive.writestr(name, data)
                archive.writestr("SOURCE-MANIFEST.json", json.dumps(manifest, indent=2))
        with zipfile.ZipFile(output) as archive:
            assert archive.testzip() is None
            for name, expected in manifest["files"].items():
                if hashlib.sha256(archive.read(name)).hexdigest() != expected:
                    raise ValueError("bundle integrity failed")
    except Exception:
        if created and output.exists():
            output.unlink()
        raise
    return {
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "files": len(files),
        "release_approved": False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(json.dumps(build(ROOT, args.output)))


if __name__ == "__main__":
    main()
