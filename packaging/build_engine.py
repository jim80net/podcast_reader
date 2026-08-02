#!/usr/bin/env python3
"""Build the production frozen engine onedir (task 7.1).

Runs PyInstaller on ``engine.spec`` (two entry points, one ``_internal/``)
and stages the managed tool seeds (yt-dlp / ffmpeg / ffprobe) plus the flat
``{name: version}`` ``tools-manifest.json`` into the bundle's tools directory
— the seed layout ``podcast_reader.engine.managed_tools.seed_tools``
reconciles into ``<data_dir>/tools/`` at engine startup.

Output layout (exactly what ``app/src/main/engine-cmd.ts`` spawns and
``app/scripts/dist.mjs --engine-dir`` maps into extraResources)::

    dist/engine/
      podcast-reader-engine[.exe]
      whisper-worker[.exe]
      _internal/
        tools/{yt-dlp[.exe], ffmpeg[.exe], ffprobe[.exe], manifests + FFmpeg license}

Build environment (PyInstaller is a build tool, never a project dependency)::

    cd packaging
    uv venv .venv-engine --python 3.10
    uv pip install --python .venv-engine/bin/python '..[worker]' pyinstaller
    .venv-engine/bin/python build_engine.py

Tool seeds are downloaded from their canonical hosts (yt-dlp standalone
release binaries; a hash-pinned BtbN LGPL FFmpeg build for linux/windows)
into ``--cache-dir`` and reused across builds. macOS has no scripted ffmpeg source here — pass
``--tools-dir`` with locally provided binaries. ``--skip-tools`` builds an
engine without seeds (PATH fallback still works; used for fast iterations).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

PACKAGING_DIR = Path(__file__).resolve().parent
SPEC_FILE = PACKAGING_DIR / "engine.spec"
TOOLS_MANIFEST = "tools-manifest.json"
TOOL_PROVENANCE = "tool-seed-provenance.json"
FFMPEG_LICENSE = "ffmpeg-LICENSE.txt"
PROVENANCE_FILE = PACKAGING_DIR / TOOL_PROVENANCE
TOOL_NAMES = ("yt-dlp", "ffmpeg", "ffprobe")

#: yt-dlp standalone release binaries (support ``-U`` self-update, no Python
#: required on the target machine) per sys.platform.
_YTDLP_ASSETS = {"linux": "yt-dlp_linux", "win32": "yt-dlp.exe", "darwin": "yt-dlp_macos"}
_YTDLP_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/{asset}"

_FFMPEG_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/{tag}/{asset}"


class BuildError(Exception):
    """A build step failed with a human-readable reason."""


# ---------------------------------------------------------------------------
# Tool seeds
# ---------------------------------------------------------------------------


def load_ffmpeg_provenance() -> dict[str, object]:
    """Load the checked-in, immutable FFmpeg build and license declaration."""
    try:
        provenance = json.loads(PROVENANCE_FILE.read_text())
        ffmpeg = provenance["ffmpeg"]
        if provenance["schema"] != 1 or not isinstance(ffmpeg, dict):
            raise ValueError("unsupported schema")
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise BuildError(f"invalid {TOOL_PROVENANCE}: {exc}") from exc
    return ffmpeg


def verify_file_sha256(path: Path, expected: str) -> None:
    """Fail closed unless *path* matches its checked-in SHA-256 pin."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual != expected:
        raise BuildError(f"SHA-256 mismatch for {path.name}: expected {expected}, got {actual}")


def verify_ffmpeg_license_config(output: str) -> None:
    """Prove an ffmpeg/ffprobe binary has the pinned LGPL-safe configuration."""
    provenance = load_ffmpeg_provenance()
    for flag in provenance["required_configuration"]:
        if flag not in output:
            raise BuildError(f"FFmpeg license configuration is missing required flag {flag}")
    for flag in provenance["forbidden_configuration"]:
        if flag in output:
            raise BuildError(f"FFmpeg license configuration contains forbidden flag {flag}")


def parse_tool_version(name: str, output: str) -> str:
    """Extract the version from a tool's version-command output.

    yt-dlp prints a bare version line; ffmpeg/ffprobe print
    ``<name> version <version> Copyright ...`` on the first line.
    """
    first_line = output.strip().splitlines()[0] if output.strip() else ""
    if name == "yt-dlp":
        if not first_line:
            raise BuildError("yt-dlp --version printed nothing")
        return first_line
    parts = first_line.split()
    if len(parts) >= 3 and parts[1] == "version":
        return parts[2]
    raise BuildError(f"could not parse {name} version from output: {first_line!r}")


def probe_tool_version(name: str, binary: Path) -> str:
    """Run the staged binary's version command and parse the result."""
    args = [str(binary), "--version" if name == "yt-dlp" else "-version"]
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=60, check=False)
    except OSError as exc:
        raise BuildError(f"could not execute staged {name}: {exc}") from exc
    if result.returncode != 0:
        raise BuildError(f"{name} version probe failed: {result.stderr.strip()}")
    if name in {"ffmpeg", "ffprobe"}:
        verify_ffmpeg_license_config(result.stdout)
    return parse_tool_version(name, result.stdout)


def stage_tool_seeds(
    sources: Mapping[str, Path], tools_dir: Path, *, ffmpeg_license: Path | None = None
) -> dict[str, str]:
    """Copy tool binaries into the bundle tools dir and write the seed manifest.

    Staged files take the canonical lookup name (``yt-dlp``, not the release
    asset name ``yt-dlp_linux``), preserving a Windows ``.exe`` suffix —
    matching what ``managed_tools._seed_file`` and ``resolve_tool`` look up.
    Versions are probed from the staged binaries themselves and recorded as
    the flat ``{name: version}`` manifest ``_load_seed_manifest`` parses.
    """
    tools_dir.mkdir(parents=True, exist_ok=True)
    versions: dict[str, str] = {}
    for name, source in sources.items():
        staged_name = f"{name}.exe" if source.suffix.lower() == ".exe" else name
        staged = tools_dir / staged_name
        shutil.copy2(source, staged)  # copy2 preserves the execute bits
        versions[name] = probe_tool_version(name, staged)
        print(f"staged tool seed {staged_name} {versions[name]}")
    write_tools_manifest(tools_dir, versions)
    if {"ffmpeg", "ffprobe"} & sources.keys():
        if ffmpeg_license is None or not ffmpeg_license.is_file():
            raise BuildError(f"FFmpeg seeds require extracted {FFMPEG_LICENSE}")
        shutil.copy2(ffmpeg_license, tools_dir / FFMPEG_LICENSE)
        shutil.copy2(PROVENANCE_FILE, tools_dir / TOOL_PROVENANCE)
    return versions


def write_tools_manifest(tools_dir: Path, versions: Mapping[str, str]) -> None:
    """Write the bundle's flat ``{name: version}`` seed manifest."""
    (tools_dir / TOOLS_MANIFEST).write_text(json.dumps(dict(versions), indent=2))


def fetch_tool_sources(cache_dir: Path, platform: str) -> dict[str, Path]:
    """Download (or reuse cached) tool binaries for *platform*.

    Returns the canonical-name -> source-path mapping ``stage_tool_seeds``
    consumes. Raises :class:`BuildError` on macOS, which has no scripted
    ffmpeg source — pass ``--tools-dir`` there.
    """
    provenance = load_ffmpeg_provenance()
    platforms = provenance["platforms"]
    if platform not in platforms:
        raise BuildError(
            f"no scripted tool-seed source for platform {platform!r}; "
            "pass --tools-dir with yt-dlp/ffmpeg/ffprobe binaries"
        )
    cache_dir.mkdir(parents=True, exist_ok=True)
    ytdlp_asset = _YTDLP_ASSETS[platform]
    ytdlp_path = cache_dir / ytdlp_asset
    if not ytdlp_path.exists():
        _download(_YTDLP_URL.format(asset=ytdlp_asset), ytdlp_path)
    ytdlp_path.chmod(0o755)

    platform_provenance = platforms[platform]
    archive_asset = platform_provenance["asset"]
    archive_path = cache_dir / archive_asset
    if not archive_path.exists():
        _download(
            _FFMPEG_URL.format(tag=provenance["release_tag"], asset=archive_asset), archive_path
        )
    verify_file_sha256(archive_path, platform_provenance["sha256"])
    suffix = ".exe" if platform == "win32" else ""
    extracted = _extract_ffmpeg(archive_path, cache_dir, suffix)
    return {"yt-dlp": ytdlp_path, **extracted}


def tools_from_dir(tools_dir: Path, platform: str) -> dict[str, Path]:
    """Resolve the three tool binaries from a user-supplied directory."""
    suffix = ".exe" if platform == "win32" else ""
    sources: dict[str, Path] = {}
    for name in TOOL_NAMES:
        for candidate in (tools_dir / f"{name}{suffix}", tools_dir / name):
            if candidate.is_file():
                sources[name] = candidate
                break
        else:
            raise BuildError(f"--tools-dir {tools_dir} does not contain {name}")
    return sources


def _download(url: str, dest: Path, timeout: float = 600.0) -> None:
    """Download *url* to *dest* atomically, with a bounded socket timeout.

    Generous (the FFmpeg archives run to hundreds of MB) but bounded — an
    unresponsive host must fail the build, not hang it forever; the same
    posture as frozen_smoke.py and the pack manager's httpx timeouts.
    """
    print(f"downloading {url}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=timeout) as response, tmp.open("wb") as fh:  # noqa: S310
        shutil.copyfileobj(response, fh)
    tmp.replace(dest)


def _extract_ffmpeg(archive: Path, cache_dir: Path, suffix: str) -> dict[str, Path]:
    """Extract ffmpeg/ffprobe and the license from a BtbN archive."""
    out: dict[str, Path] = {}
    license_path: Path | None = None
    wanted = {f"ffmpeg{suffix}": "ffmpeg", f"ffprobe{suffix}": "ffprobe"}
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            for member in zf.namelist():
                base = member.rsplit("/", 1)[-1]
                if base in wanted and "/bin/" in member:
                    dest = cache_dir / base
                    with zf.open(member) as src, dest.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
                    out[wanted[base]] = dest
                elif base == "LICENSE.txt":
                    license_path = cache_dir / FFMPEG_LICENSE
                    with zf.open(member) as src, license_path.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
    else:
        with tarfile.open(archive) as tf:
            for tarinfo in tf.getmembers():
                base = tarinfo.name.rsplit("/", 1)[-1]
                if base in wanted and "/bin/" in tarinfo.name:
                    extracted = tf.extractfile(tarinfo)
                    assert extracted is not None  # noqa: S101 — regular member
                    dest = cache_dir / base
                    with dest.open("wb") as dst:
                        shutil.copyfileobj(extracted, dst)
                    out[wanted[base]] = dest
                elif base == "LICENSE.txt" and tarinfo.isfile():
                    extracted = tf.extractfile(tarinfo)
                    assert extracted is not None  # noqa: S101 — regular member
                    license_path = cache_dir / FFMPEG_LICENSE
                    with license_path.open("wb") as dst:
                        shutil.copyfileobj(extracted, dst)
    missing = set(wanted.values()) - set(out)
    if missing:
        raise BuildError(f"archive {archive.name} did not contain: {sorted(missing)}")
    if license_path is None:
        raise BuildError(f"archive {archive.name} did not contain LICENSE.txt")
    for path in out.values():
        path.chmod(0o755)
    return out


# ---------------------------------------------------------------------------
# PyInstaller build + layout verification
# ---------------------------------------------------------------------------


def run_pyinstaller(dist_dir: Path, work_dir: Path) -> None:
    """Freeze the engine with the interpreter running this script."""
    args = [
        sys.executable,
        "-m",
        "PyInstaller",
        str(SPEC_FILE),
        "--noconfirm",
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(work_dir),
    ]
    result = subprocess.run(args, cwd=PACKAGING_DIR, check=False)
    if result.returncode != 0:
        raise BuildError(f"PyInstaller failed with exit code {result.returncode}")


def verify_engine_layout(engine_dir: Path, *, windows: bool, require_tools: bool = True) -> None:
    """Assert the output matches the packaged-engine contract.

    The spawn chain (``engine-cmd.ts``) expects ``podcast-reader-engine[.exe]``
    at the onedir root; ``resolve_bundled_worker`` expects the sibling
    ``whisper-worker[.exe]``; seeding expects ``_internal/tools/`` with the
    flat manifest.
    """
    suffix = ".exe" if windows else ""
    problems: list[str] = []
    for exe in (f"podcast-reader-engine{suffix}", f"whisper-worker{suffix}"):
        if not (engine_dir / exe).is_file():
            problems.append(f"missing executable: {exe}")
    if not (engine_dir / "_internal").is_dir():
        problems.append("missing _internal/ directory")
    if require_tools:
        for filename in (TOOLS_MANIFEST, TOOL_PROVENANCE, FFMPEG_LICENSE):
            if not (engine_dir / "_internal" / "tools" / filename).is_file():
                problems.append(f"missing _internal/tools/{filename}")
    if problems:
        raise BuildError("engine layout verification failed: " + "; ".join(problems))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build the production frozen engine onedir.")
    parser.add_argument(
        "--dist",
        type=Path,
        default=PACKAGING_DIR / "dist",
        help="PyInstaller distpath; the engine lands in <dist>/engine (default: packaging/dist)",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=PACKAGING_DIR / ".tool-cache",
        help="Download cache for tool seeds (default: packaging/.tool-cache)",
    )
    parser.add_argument(
        "--tools-dir",
        type=Path,
        default=None,
        help="Use yt-dlp/ffmpeg/ffprobe binaries from this directory instead of downloading",
    )
    parser.add_argument(
        "--skip-tools",
        action="store_true",
        help="Skip tool seeding entirely (PATH fallback still works at runtime)",
    )
    args = parser.parse_args(argv)

    windows = sys.platform == "win32"
    work_dir = PACKAGING_DIR / "build"
    run_pyinstaller(args.dist, work_dir)
    engine_dir = args.dist / "engine"
    if not args.skip_tools:
        if args.tools_dir is not None:
            sources = tools_from_dir(args.tools_dir, sys.platform)
            ffmpeg_license = args.tools_dir / FFMPEG_LICENSE
        else:
            sources = fetch_tool_sources(args.cache_dir, sys.platform)
            ffmpeg_license = args.cache_dir / FFMPEG_LICENSE
        stage_tool_seeds(sources, engine_dir / "_internal" / "tools", ffmpeg_license=ffmpeg_license)
    verify_engine_layout(engine_dir, windows=windows, require_tools=not args.skip_tools)
    print(f"frozen engine ready: {engine_dir}")


if __name__ == "__main__":
    main()
