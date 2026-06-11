"""Tests for podcast_reader.tools module."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

from podcast_reader.tools import resolve_tool


class _FrozenSys:
    """Stand-in for the sys module under PyInstaller freezing."""

    frozen = True

    def __init__(self, executable: str, meipass: str) -> None:
        self.executable = executable
        self._MEIPASS = meipass


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

    def test_tools_dir_param_wins(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """An explicit tools_dir takes precedence over the interpreter sibling dir."""
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        exe = tools_dir / "yt-dlp"
        exe.touch()
        exe.chmod(0o755)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        sib = bin_dir / "yt-dlp"
        sib.touch()
        sib.chmod(0o755)
        (bin_dir / "python").touch()
        monkeypatch.setattr("podcast_reader.tools.sys.executable", str(bin_dir / "python"))
        assert resolve_tool("yt-dlp", tools_dir=tools_dir) == str(exe)

    def test_env_var_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """PODCAST_READER_TOOLS_DIR supplies the tools dir when no param is given."""
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        exe = tools_dir / "yt-dlp"
        exe.touch()
        exe.chmod(0o755)
        monkeypatch.setenv("PODCAST_READER_TOOLS_DIR", str(tools_dir))
        assert resolve_tool("yt-dlp") == str(exe)

    def test_frozen_skips_interpreter_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Under sys.frozen the interpreter sibling dir must not be searched."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        sib = bin_dir / "yt-dlp"
        sib.touch()
        sib.chmod(0o755)
        (bin_dir / "python").touch()
        monkeypatch.setattr("podcast_reader.tools.sys.executable", str(bin_dir / "python"))
        monkeypatch.setattr(
            "podcast_reader.tools.sys",
            _FrozenSys(str(bin_dir / "python"), str(tmp_path / "bundle")),
        )
        # bundle tools dir empty; interpreter dir NOT searched
        assert resolve_tool("yt-dlp") == "yt-dlp"

    def test_frozen_bundle_tools_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Under sys.frozen the bundle's tools directory is searched."""
        bundle = tmp_path / "bundle"
        (bundle / "tools").mkdir(parents=True)
        exe = bundle / "tools" / "yt-dlp"
        exe.touch()
        exe.chmod(0o755)
        monkeypatch.setattr(
            "podcast_reader.tools.sys",
            _FrozenSys(str(bundle / "engine.exe"), str(bundle)),
        )
        assert resolve_tool("yt-dlp") == str(exe)
