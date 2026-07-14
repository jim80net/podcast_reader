"""Tests for the yt-dlp *video* download variant (floating-video-player, task 3).

The video path shares the audio path's structured-error and managed-copy
self-heal discipline; these tests assert the video arg builder shape, the
audio-only fallback selector, and that the shared heal/auth behavior still
holds for ``download_video`` (mock the ``run_child`` subprocess boundary).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from podcast_reader.engine.managed_tools import load_user_manifest
from podcast_reader.types import PipelineError
from podcast_reader.ytdlp import build_video_args, download_video

if TYPE_CHECKING:
    from podcast_reader.types import PipelineEvent


@patch("podcast_reader.ytdlp.resolve_tool", return_value="yt-dlp")
class TestBuildVideoArgs:
    def test_basic_url(self, _mock_resolve: MagicMock) -> None:
        result = build_video_args("https://x.com/user/status/123", Path("/tmp/out"))
        assert result == [
            "yt-dlp",
            "-f",
            "bv*+ba/b",
            "--merge-output-format",
            "mp4",
            "-o",
            "/tmp/out/%(id)s.%(ext)s",
            "https://x.com/user/status/123",
        ]

    def test_audio_only_fallback_selector_present(self, _mock_resolve: MagicMock) -> None:
        """Per F7: the ``/b`` tail in ``bv*+ba/b`` falls back to the best single
        stream when there is no video track, so audio-only remote posts resolve."""
        result = build_video_args("https://x.com/p/1", Path("/tmp/out"))
        selector = result[result.index("-f") + 1]
        assert selector == "bv*+ba/b"
        assert selector.endswith("/b")

    def test_with_cookies(self, _mock_resolve: MagicMock) -> None:
        result = build_video_args(
            "https://x.com/user/status/123",
            Path("/tmp/out"),
            cookies=Path("/home/user/cookies.txt"),
        )
        assert result == [
            "yt-dlp",
            "-f",
            "bv*+ba/b",
            "--merge-output-format",
            "mp4",
            "--cookies",
            "/home/user/cookies.txt",
            "-o",
            "/tmp/out/%(id)s.%(ext)s",
            "https://x.com/user/status/123",
        ]

    def test_uses_resolved_executable(self, _mock_resolve: MagicMock) -> None:
        _mock_resolve.return_value = "/tool-venv/bin/yt-dlp"
        result = build_video_args("https://x.com/p/1", Path("/tmp/out"))
        assert result[0] == "/tool-venv/bin/yt-dlp"


class TestDownloadVideo:
    URL = "https://x.com/user/status/123"

    def test_returns_video_path(self, tmp_path: Path) -> None:
        expected = tmp_path / "123.mp4"
        with patch("podcast_reader.ytdlp.run_child") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            expected.touch()
            result = download_video(self.URL, tmp_path)
        assert result == expected

    def test_returns_audio_only_fallback_file(self, tmp_path: Path) -> None:
        """A remote with no video track resolves to a single audio stream (F7):
        the helper returns whichever media file yt-dlp produced, not just mp4."""
        produced = tmp_path / "123.m4a"
        with patch("podcast_reader.ytdlp.run_child") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            produced.touch()
            result = download_video(self.URL, tmp_path)
        assert result == produced

    def test_ignores_sidecar_files(self, tmp_path: Path) -> None:
        """yt-dlp sidecars (.info.json, thumbnails, partials) must never be
        returned as the media file (OCR): selection is by media-container
        suffix, so it holds regardless of mtime — no timing dependency."""
        media = tmp_path / "123.mp4"
        with patch("podcast_reader.ytdlp.run_child") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            media.touch()
            (tmp_path / "123.info.json").touch()
            (tmp_path / "123.jpg").touch()
            (tmp_path / "123.mp4.part").touch()
            result = download_video(self.URL, tmp_path)
        assert result == media

    def test_raises_structured_download_failed(self, tmp_path: Path) -> None:
        with patch("podcast_reader.ytdlp.run_child") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="ERROR: unable to extract"
            )
            with pytest.raises(PipelineError, match="unable to extract") as excinfo:
                download_video(self.URL, tmp_path)
        assert excinfo.value.code == "download_failed"
        assert excinfo.value.detail == "ERROR: unable to extract"

    def test_auth_failure_raises_download_auth_required(self, tmp_path: Path) -> None:
        with patch("podcast_reader.ytdlp.run_child") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="ERROR: login required"
            )
            with pytest.raises(PipelineError) as excinfo:
                download_video(self.URL, tmp_path)
        assert excinfo.value.code == "download_auth_required"
        assert excinfo.value.hint == ""
        assert excinfo.value.detail == "ERROR: login required"


class TestDownloadVideoSelfHeal:
    """The video path reuses the audio path's managed-copy self-heal wrapper."""

    URL = "https://x.com/user/status/123"

    def _managed_binary(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        data = tmp_path / "data"
        monkeypatch.setenv("PODCAST_READER_DATA_DIR", str(data))
        binary = data / "tools" / "yt-dlp"
        binary.parent.mkdir(parents=True)
        binary.touch()
        return binary

    @staticmethod
    def _completed(
        args: list[str], returncode: int = 0, stdout: str = "", stderr: str = ""
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr=stderr)

    def test_extractor_breakage_heals_in_video_download(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        binary = self._managed_binary(tmp_path, monkeypatch)
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        downloads: list[list[str]] = []

        def fake_download_run(args: list[str]) -> subprocess.CompletedProcess[str]:
            downloads.append(args)
            if len(downloads) == 1:
                return self._completed(args, 1, stderr="ERROR: unable to extract")
            (out_dir / "123.mp4").touch()
            return self._completed(args)

        def fake_update_run(args: list[str]) -> subprocess.CompletedProcess[str]:
            if args[1] == "-U":
                return self._completed(args, stdout="Updated\n")
            return self._completed(args, stdout="2026.06.06\n")

        events: list[PipelineEvent] = []
        with (
            patch("podcast_reader.ytdlp.resolve_tool", return_value=str(binary)),
            patch("podcast_reader.ytdlp.run_child", side_effect=fake_download_run),
            patch("podcast_reader.engine.managed_tools.run_child", side_effect=fake_update_run),
        ):
            result = download_video(self.URL, out_dir, on_event=events.append)

        assert result == out_dir / "123.mp4"
        assert len(downloads) == 2
        (warning,) = events
        assert warning["data"]["code"] == "ytdlp_self_update"
        assert load_user_manifest(tmp_path / "data")["versions"]["yt-dlp"] == "2026.06.06"

    def test_unmanaged_binary_never_self_updates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PODCAST_READER_DATA_DIR", str(tmp_path / "data"))
        with (
            patch("podcast_reader.ytdlp.resolve_tool", return_value="/usr/bin/yt-dlp"),
            patch(
                "podcast_reader.ytdlp.run_child",
                return_value=self._completed([], 1, stderr="ERROR: broken"),
            ) as download_run,
            patch("podcast_reader.engine.managed_tools.run_child") as update_run,
            pytest.raises(PipelineError),
        ):
            download_video(self.URL, tmp_path, on_event=lambda _e: None)

        assert download_run.call_count == 1
        update_run.assert_not_called()
