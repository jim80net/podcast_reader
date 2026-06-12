#!/usr/bin/env python3
"""Build the diarization worker pack (task 7.5).

Reproduces the proven 5.1 freeze recipe (packaging/DIARIZATION_SMOKE.md):

1. Fail fast without ``HF_TOKEN`` (task 7.4 — the token's account must have
   accepted the ``pyannote/speaker-diarization-community-1`` gated-model
   terms; this script never accepts terms on anyone's behalf).
2. Dedicated CPU-torch build venv: torch from the PyTorch CPU index FIRST
   (PyPI torch is the CUDA build and quadruples the bundle), then
   pyannote.audio + PyInstaller, then torchaudio/torchcodec reinstalled from
   the CPU index (their PyPI wheels link a different libtorch and fail to
   load against torch +cpu), then this project ``--no-deps``.
3. PyInstaller ``diarization.spec`` -> ``dist/diarization-worker/``.
4. ``snapshot_download`` of the community-1 pipeline (config + segmentation +
   embedding + plda) into ``dist/diarization-worker/cache/`` — the
   frozen-sibling cache the worker loads offline.
5. Offline smoke: the frozen worker against the fixture WAV with
   ``HF_HUB_OFFLINE=1`` and the DEFAULT model id (what ships).
6. Compress to ``dist/diarization-pack-v<N>.tar.gz`` and emit a manifest
   JSON (sha256 + size) ready for a ``pack-diarization-v<N>`` GitHub Release.

Publishing (one command once the HF_TOKEN secret exists — task 7.4):

    gh release create pack-diarization-v<N> \
        packaging/dist/diarization-pack-v<N>.tar.gz \
        packaging/dist/diarization-pack-v<N>.json \
        --title "Diarization worker pack v<N>" --notes "see manifest asset"

then flip the registry entry (src/podcast_reader/engine/packs.py
``REGISTRY["diarization"]``) from ``files=None`` to the published URL +
sha256 + size from the manifest. NOTE for the flip: the pack ships as one
tar.gz, so the installer needs an archive-extraction step for ``worker``
packs (analogous to ``extract_wheels``) before the pin can go live — kept
out of the engine until then per the S5 no-dormant-code rule.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

PACKAGING_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGING_DIR.parent
VENV_DIR = PACKAGING_DIR / ".venv-diarization"
SPEC_FILE = PACKAGING_DIR / "diarization.spec"
WORKER_DIST = PACKAGING_DIR / "dist" / "diarization-worker"
PIPELINE_REPO = "pyannote/speaker-diarization-community-1"
CPU_INDEX = "https://download.pytorch.org/whl/cpu"


class BuildError(Exception):
    """A build step failed with a human-readable reason."""


def require_hf_token() -> str:
    """Fail fast (spec scenario: Missing secret fails fast) without HF_TOKEN."""
    token = os.environ.get("HF_TOKEN", "").strip()
    if not token:
        raise BuildError(
            "HF_TOKEN is not set. The diarization pack build downloads the gated "
            f"{PIPELINE_REPO} pipeline at build time; provision the HF_TOKEN secret "
            "(task 7.4) with accepted terms for that model before running this."
        )
    return token


def venv_python() -> Path:
    if sys.platform == "win32":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def _run(args: list[str], **kwargs: object) -> None:
    print("+", " ".join(args))
    result = subprocess.run(args, check=False, **kwargs)  # type: ignore[call-overload]
    if result.returncode != 0:
        raise BuildError(f"command failed ({result.returncode}): {' '.join(args)}")


def create_build_venv() -> None:
    """The DIARIZATION_SMOKE.md recipe, verbatim (CPU torch ordering matters)."""
    python = str(venv_python())
    _run(["uv", "venv", str(VENV_DIR), "--python", "3.10"])
    _run(["uv", "pip", "install", "--python", python, "torch", "--index-url", CPU_INDEX])
    _run(["uv", "pip", "install", "--python", python, "pyannote.audio>=4.0", "pyinstaller"])
    _run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            python,
            "--reinstall-package",
            "torchaudio",
            "--reinstall-package",
            "torchcodec",
            "torchaudio",
            "torchcodec",
            "--index-url",
            CPU_INDEX,
        ]
    )
    _run(["uv", "pip", "install", "--python", python, "--no-deps", str(REPO_ROOT)])


def freeze_worker() -> None:
    _run(
        [str(venv_python()), "-m", "PyInstaller", str(SPEC_FILE), "--noconfirm"],
        cwd=PACKAGING_DIR,
    )
    exe_name = "diarization-worker.exe" if sys.platform == "win32" else "diarization-worker"
    exe = WORKER_DIST / exe_name
    if not exe.is_file():
        raise BuildError(f"frozen worker missing after PyInstaller: {exe}")


def seed_pipeline_cache(token: str) -> None:
    """snapshot_download the community-1 pipeline into the pack's cache/."""
    cache = WORKER_DIST / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    seed_code = (
        "from huggingface_hub import snapshot_download\n"
        f"snapshot_download({PIPELINE_REPO!r}, cache_dir={str(cache)!r})\n"
    )
    _run(
        [str(venv_python()), "-c", seed_code],
        env={**os.environ, "HF_TOKEN": token},
    )


def offline_smoke(fixture: Path) -> None:
    """Run the frozen worker fully offline with the shipping default model."""
    exe_name = "diarization-worker.exe" if sys.platform == "win32" else "diarization-worker"
    exe = WORKER_DIST / exe_name
    turns_path = PACKAGING_DIR / "dist" / "smoke-turns.json"
    env = {**os.environ, "HF_HUB_OFFLINE": "1"}
    env.pop("HF_TOKEN", None)  # the target machine has no HF account
    _run([str(exe), str(fixture), "--output", str(turns_path)], env=env)
    turns = json.loads(turns_path.read_text())
    if "turns" not in turns:
        raise BuildError(f"offline smoke produced malformed turns JSON: {turns_path}")
    print(f"offline smoke ok: {len(turns['turns'])} turn(s)")


def compress_pack(version: str) -> tuple[Path, Path]:
    """tar.gz the worker onedir (incl. cache/) and write the release manifest."""
    archive = PACKAGING_DIR / "dist" / f"diarization-pack-v{version}.tar.gz"
    print(f"compressing {WORKER_DIST} -> {archive}")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(WORKER_DIST, arcname="diarization-worker")
    digest = hashlib.sha256()
    with archive.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    manifest = {
        "id": "diarization",
        "pack_version": version,
        "archive": archive.name,
        "sha256": digest.hexdigest(),
        "size": archive.stat().st_size,
        "release_tag": f"pack-diarization-v{version}",
        "pipeline": PIPELINE_REPO,
    }
    manifest_path = archive.with_suffix("").with_suffix(".json")  # .tar.gz -> .json
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest, indent=2))
    return archive, manifest_path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build the diarization worker pack.")
    parser.add_argument("--version", default="1", help="pack version (release tag number)")
    parser.add_argument(
        "--fixture",
        type=Path,
        default=REPO_ROOT / "tests" / "fixtures" / "fixture_speech.wav",
        help="16 kHz mono WAV for the offline smoke",
    )
    parser.add_argument(
        "--skip-venv",
        action="store_true",
        help="reuse an existing .venv-diarization (local iteration)",
    )
    args = parser.parse_args(argv)

    token = require_hf_token()
    if not args.skip_venv or not venv_python().exists():
        create_build_venv()
    freeze_worker()
    seed_pipeline_cache(token)
    offline_smoke(args.fixture)
    archive, manifest = compress_pack(args.version)
    print(
        f"\npack ready: {archive}\nmanifest:   {manifest}\n"
        f"publish:    gh release create pack-diarization-v{args.version} "
        f"{archive} {manifest} --title 'Diarization worker pack v{args.version}'\n"
        "then flip REGISTRY['diarization'] in src/podcast_reader/engine/packs.py "
        "from files=None to the published pin (see module docstring)."
    )


if __name__ == "__main__":
    try:
        main()
    except BuildError as exc:
        print(f"diarization pack build FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
