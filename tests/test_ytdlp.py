"""Tests for podcast_reader.ytdlp module."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from podcast_reader.ytdlp import build_download_args, build_title_args, download_audio, fetch_title


@patch("podcast_reader.ytdlp.resolve_tool", return_value="yt-dlp")
class TestBuildDownloadArgs:
    def test_basic_url(self, _mock_resolve: MagicMock) -> None:
        result = build_download_args("https://x.com/user/status/123", Path("/tmp/out"))
        assert result == [
            "yt-dlp",
            "-x",
            "--audio-format",
            "mp3",
            "-o",
            "/tmp/out/%(id)s.%(ext)s",
            "https://x.com/user/status/123",
        ]

    def test_with_cookies(self, _mock_resolve: MagicMock) -> None:
        result = build_download_args(
            "https://x.com/user/status/123",
            Path("/tmp/out"),
            cookies=Path("/home/user/cookies.txt"),
        )
        assert result == [
            "yt-dlp",
            "-x",
            "--audio-format",
            "mp3",
            "--cookies",
            "/home/user/cookies.txt",
            "-o",
            "/tmp/out/%(id)s.%(ext)s",
            "https://x.com/user/status/123",
        ]


class TestBuildTitleArgs:
    @patch("podcast_reader.ytdlp.resolve_tool", return_value="yt-dlp")
    def test_basic(self, _mock_resolve: MagicMock) -> None:
        result = build_title_args("https://x.com/user/status/123")
        assert result == [
            "yt-dlp",
            "--print",
            "title",
            "https://x.com/user/status/123",
        ]

    @patch("podcast_reader.ytdlp.resolve_tool", return_value="/tool-venv/bin/yt-dlp")
    def test_uses_resolved_executable(self, _mock_resolve: MagicMock) -> None:
        """Builders must use the resolved path so yt-dlp bundled in an isolated
        venv (e.g. uv tool install) is found even when it's not on PATH."""
        result = build_title_args("https://x.com/user/status/123")
        assert result[0] == "/tool-venv/bin/yt-dlp"


class TestFetchTitle:
    def test_returns_stripped_title(self) -> None:
        with patch("podcast_reader.ytdlp.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="My Video Title\n", stderr=""
            )
            result = fetch_title("https://x.com/user/status/123")
        assert result == "My Video Title"

    def test_raises_on_failure(self) -> None:
        with patch("podcast_reader.ytdlp.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="ERROR: not found"
            )
            with pytest.raises(RuntimeError, match="yt-dlp failed"):
                fetch_title("https://x.com/user/status/123")


class TestDownloadAudio:
    def test_returns_audio_path(self, tmp_path: Path) -> None:
        # Simulate yt-dlp creating the file
        expected_file = tmp_path / "123.mp3"
        expected_file.touch()

        with patch("podcast_reader.ytdlp.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            result = download_audio("https://x.com/user/status/123", tmp_path)

        assert result == expected_file
        marker = tmp_path / "123.ytdlp"
        assert marker.exists()
        assert marker.read_text() == "https://x.com/user/status/123"

    def test_raises_on_failure(self, tmp_path: Path) -> None:
        with patch("podcast_reader.ytdlp.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="ERROR: login required"
            )
            with pytest.raises(RuntimeError, match="yt-dlp failed"):
                download_audio("https://x.com/user/status/123", tmp_path)

    def test_auth_error_suggests_cookies(self, tmp_path: Path) -> None:
        with patch("podcast_reader.ytdlp.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="ERROR: login required"
            )
            with pytest.raises(RuntimeError, match="cookies"):
                download_audio("https://x.com/user/status/123", tmp_path)
