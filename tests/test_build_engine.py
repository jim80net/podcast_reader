"""Tests for packaging/build_engine.py (the pure/stageable parts).

The script lives outside ``src/`` (it is a build tool, not part of the
package), so it is loaded by file path. Network download and PyInstaller
invocation are exercised by the CI frozen-smoke job, not unit tests.
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
from pathlib import Path
from types import ModuleType

import pytest

from podcast_reader.engine.managed_tools import seed_tools, tools_dir

_SCRIPT = Path(__file__).resolve().parent.parent / "packaging" / "build_engine.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_engine", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_engine = _load()


class TestParseToolVersion:
    def test_ytdlp_version_is_the_stripped_line(self) -> None:
        assert build_engine.parse_tool_version("yt-dlp", "2026.06.09\n") == "2026.06.09"

    def test_ffmpeg_version_is_third_token_of_first_line(self) -> None:
        output = (
            "ffmpeg version N-118500-g123abc-20260601 Copyright (c) 2000-2026\n"
            "built with gcc 13\n"
        )
        assert build_engine.parse_tool_version("ffmpeg", output) == "N-118500-g123abc-20260601"

    def test_ffprobe_parses_like_ffmpeg(self) -> None:
        assert build_engine.parse_tool_version("ffprobe", "ffprobe version 7.1 Copy") == "7.1"

    def test_unparseable_output_raises(self) -> None:
        with pytest.raises(build_engine.BuildError):
            build_engine.parse_tool_version("ffmpeg", "")


def _stub_tool(directory: Path, filename: str, version_line: str) -> Path:
    """An executable stub that answers --version/-version like the real tool."""
    path = directory / filename
    path.write_text(f'#!/bin/sh\necho "{version_line}"\n')
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


@pytest.mark.skipif(sys.platform == "win32", reason="stub tools are POSIX shell scripts")
class TestStageToolSeeds:
    def test_stages_canonical_names_versions_and_manifest(self, tmp_path: Path) -> None:
        source_dir = tmp_path / "sources"
        source_dir.mkdir()
        sources = {
            # the standalone release asset is named yt-dlp_linux; the staged
            # seed must use the canonical lookup name
            "yt-dlp": _stub_tool(source_dir, "yt-dlp_linux", "2026.06.09"),
            "ffmpeg": _stub_tool(source_dir, "ffmpeg", "ffmpeg version 7.1-static built"),
            "ffprobe": _stub_tool(source_dir, "ffprobe", "ffprobe version 7.1-static built"),
        }
        bundle_tools = tmp_path / "bundle" / "tools"
        versions = build_engine.stage_tool_seeds(sources, bundle_tools)
        assert versions == {"yt-dlp": "2026.06.09", "ffmpeg": "7.1-static", "ffprobe": "7.1-static"}
        assert (bundle_tools / "yt-dlp").is_file()
        assert os.access(bundle_tools / "yt-dlp", os.X_OK)
        manifest = json.loads((bundle_tools / "tools-manifest.json").read_text())
        # flat {name: version} — exactly what _load_seed_manifest parses
        assert manifest == versions

    def test_staged_seeds_feed_engine_seeding(self, tmp_path: Path) -> None:
        """End-to-end with the real consumer: managed_tools.seed_tools reads
        the staged bundle tools dir and reconciles into user data."""
        source_dir = tmp_path / "sources"
        source_dir.mkdir()
        sources = {"yt-dlp": _stub_tool(source_dir, "yt-dlp_linux", "2026.06.09")}
        bundle_tools = tmp_path / "bundle" / "tools"
        build_engine.stage_tool_seeds(sources, bundle_tools)

        data_dir = tmp_path / "data"
        seed_tools(data_dir, seed_dir=bundle_tools)
        seeded = tools_dir(data_dir) / "yt-dlp"
        assert seeded.is_file()
        assert os.access(seeded, os.X_OK)
        user_manifest = json.loads((tools_dir(data_dir) / "tools-manifest.json").read_text())
        assert user_manifest["versions"] == {"yt-dlp": "2026.06.09"}

    def test_windows_exe_suffix_preserved(self, tmp_path: Path) -> None:
        source_dir = tmp_path / "sources"
        source_dir.mkdir()
        sources = {"yt-dlp": _stub_tool(source_dir, "yt-dlp.exe", "2026.06.09")}
        bundle_tools = tmp_path / "bundle" / "tools"
        build_engine.stage_tool_seeds(sources, bundle_tools)
        assert (bundle_tools / "yt-dlp.exe").is_file()


class TestVerifyEngineLayout:
    def _layout(self, tmp_path: Path, *, windows: bool = False) -> Path:
        dist = tmp_path / "engine"
        (dist / "_internal" / "tools").mkdir(parents=True)
        suffix = ".exe" if windows else ""
        (dist / f"podcast-reader-engine{suffix}").write_bytes(b"exe")
        (dist / f"whisper-worker{suffix}").write_bytes(b"exe")
        (dist / "_internal" / "tools" / "tools-manifest.json").write_text("{}")
        return dist

    def test_complete_layout_passes(self, tmp_path: Path) -> None:
        dist = self._layout(tmp_path)
        build_engine.verify_engine_layout(dist, windows=False)  # no raise

    def test_windows_layout_checks_exe_names(self, tmp_path: Path) -> None:
        dist = self._layout(tmp_path, windows=True)
        build_engine.verify_engine_layout(dist, windows=True)  # no raise

    def test_missing_worker_fails(self, tmp_path: Path) -> None:
        dist = self._layout(tmp_path)
        (dist / "whisper-worker").unlink()
        with pytest.raises(build_engine.BuildError, match="whisper-worker"):
            build_engine.verify_engine_layout(dist, windows=False)

    def test_missing_tools_manifest_fails(self, tmp_path: Path) -> None:
        dist = self._layout(tmp_path)
        (dist / "_internal" / "tools" / "tools-manifest.json").unlink()
        with pytest.raises(build_engine.BuildError, match="tools-manifest.json"):
            build_engine.verify_engine_layout(dist, windows=False)

    def test_skip_tools_layout_passes_without_manifest(self, tmp_path: Path) -> None:
        dist = self._layout(tmp_path)
        (dist / "_internal" / "tools" / "tools-manifest.json").unlink()
        build_engine.verify_engine_layout(dist, windows=False, require_tools=False)
