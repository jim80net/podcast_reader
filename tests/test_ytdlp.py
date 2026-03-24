"""Tests for podcast_reader.ytdlp module."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from podcast_reader.ytdlp import build_download_args, build_title_args, download_audio, fetch_title


class TestBuildDownloadArgs:
    def test_basic_url(self) -> None:
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

    def test_with_cookies(self) -> None:
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
    def test_basic(self) -> None:
        result = build_title_args("https://x.com/user/status/123")
        assert result == [
            "yt-dlp",
            "--print",
            "title",
            "https://x.com/user/status/123",
        ]


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
