"""Tests for podcast_reader.tools module."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

from podcast_reader.tools import resolve_tool


class TestResolveTool:
    def test_resolves_executable_next_to_interpreter(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A console script in the interpreter's bin dir is resolved to its full path."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "python").touch()
        sibling = bin_dir / "yt-dlp"
        sibling.touch()
        sibling.chmod(0o755)

        monkeypatch.setattr("podcast_reader.tools.sys.executable", str(bin_dir / "python"))

        assert resolve_tool("yt-dlp") == str(sibling)

    def test_ignores_non_executable_sibling(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A non-executable file of the same name must not shadow PATH lookup."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "python").touch()
        (bin_dir / "yt-dlp").touch()  # exists but not executable

        monkeypatch.setattr("podcast_reader.tools.sys.executable", str(bin_dir / "python"))

        assert resolve_tool("yt-dlp") == "yt-dlp"

    def test_falls_back_to_bare_name_for_path_lookup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When no sibling executable exists, the bare name is returned for PATH lookup."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "python").touch()

        monkeypatch.setattr("podcast_reader.tools.sys.executable", str(bin_dir / "python"))

        assert resolve_tool("whisper-ctranslate2") == "whisper-ctranslate2"
