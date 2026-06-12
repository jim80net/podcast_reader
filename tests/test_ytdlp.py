"""Tests for podcast_reader.ytdlp module."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from podcast_reader.engine.managed_tools import load_user_manifest
from podcast_reader.types import PipelineError
from podcast_reader.ytdlp import build_download_args, build_title_args, download_audio, fetch_title

if TYPE_CHECKING:
    from podcast_reader.types import PipelineEvent


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
        with patch("podcast_reader.ytdlp.run_child") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="My Video Title\n", stderr=""
            )
            result = fetch_title("https://x.com/user/status/123")
        assert result == "My Video Title"

    def test_raises_on_failure(self) -> None:
        with patch("podcast_reader.ytdlp.run_child") as mock_run:
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

        with patch("podcast_reader.ytdlp.run_child") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            result = download_audio("https://x.com/user/status/123", tmp_path)

        assert result == expected_file
        marker = tmp_path / "123.ytdlp"
        assert marker.exists()
        assert marker.read_text() == "https://x.com/user/status/123"

    def test_raises_structured_download_failed(self, tmp_path: Path) -> None:
        """Per S7: yt-dlp exit != 0 surfaces as PipelineError('download_failed')
        — an expected, user-explainable failure, not an internal error."""
        with patch("podcast_reader.ytdlp.run_child") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="ERROR: unable to extract"
            )
            with pytest.raises(PipelineError, match="yt-dlp failed") as excinfo:
                download_audio("https://x.com/user/status/123", tmp_path)
        assert excinfo.value.code == "download_failed"

    @pytest.mark.parametrize(
        "stderr",
        ["ERROR: login required", "ERROR: authentication needed to access this content"],
    )
    def test_auth_failure_raises_download_auth_required(self, tmp_path: Path, stderr: str) -> None:
        """Per U2: auth-detected failures carry the distinct code
        download_auth_required with a neutral, hint-free message — the hint
        is authored by the face (CLI: env var; engine: extension + import)."""
        with patch("podcast_reader.ytdlp.run_child") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr=stderr
            )
            with pytest.raises(PipelineError, match="yt-dlp failed") as excinfo:
                download_audio("https://x.com/user/status/123", tmp_path)
        assert excinfo.value.code == "download_auth_required"
        assert excinfo.value.hint == ""


class TestDownloadSelfUpdateRetry:
    """The failure-triggered yt-dlp self-update (per Q3/S7): gated purely on
    the resolved binary residing in the user-data tools dir."""

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

    def test_extractor_breakage_heals_in_job(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spec scenario: download fails, the self-update installs a newer
        yt-dlp, the single retry succeeds — with a warning recording the
        recovery and the new version recorded in the tools manifest."""
        binary = self._managed_binary(tmp_path, monkeypatch)
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        downloads: list[list[str]] = []

        def fake_download_run(args: list[str]) -> subprocess.CompletedProcess[str]:
            downloads.append(args)
            if len(downloads) == 1:
                return self._completed(args, 1, stderr="ERROR: unable to extract")
            (out_dir / "123.mp3").touch()
            return self._completed(args)

        def fake_update_run(args: list[str]) -> subprocess.CompletedProcess[str]:
            assert args[0] == str(binary)
            if args[1] == "-U":
                return self._completed(args, stdout="Updated\n")
            return self._completed(args, stdout="2026.06.06\n")

        events: list[PipelineEvent] = []
        with (
            patch("podcast_reader.ytdlp.resolve_tool", return_value=str(binary)),
            patch("podcast_reader.ytdlp.run_child", side_effect=fake_download_run),
            patch(
                "podcast_reader.engine.managed_tools.run_child", side_effect=fake_update_run
            ) as update_run,
        ):
            result = download_audio(self.URL, out_dir, on_event=events.append)

        assert result == out_dir / "123.mp3"
        assert len(downloads) == 2
        assert [c.args[0][1] for c in update_run.call_args_list] == ["-U", "--version"]
        (warning,) = events
        assert warning["kind"] == "warning"
        assert warning["data"]["code"] == "ytdlp_self_update"
        assert load_user_manifest(tmp_path / "data")["versions"]["yt-dlp"] == "2026.06.06"

    def test_persistent_failure_surfaces_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spec scenario: the retry after self-update also fails — the job
        fails with the structured error and no further retries occur."""
        binary = self._managed_binary(tmp_path, monkeypatch)
        with (
            patch("podcast_reader.ytdlp.resolve_tool", return_value=str(binary)),
            patch(
                "podcast_reader.ytdlp.run_child",
                return_value=self._completed([], 1, stderr="ERROR: still broken"),
            ) as download_run,
            patch(
                "podcast_reader.engine.managed_tools.run_child",
                return_value=self._completed([], stdout="2026.06.06\n"),
            ) as update_run,
            pytest.raises(PipelineError) as excinfo,
        ):
            download_audio(self.URL, tmp_path, on_event=lambda _e: None)

        assert excinfo.value.code == "download_failed"
        assert download_run.call_count == 2  # exactly one retry
        assert update_run.call_count == 2  # one -U, one --version

    def test_unmanaged_binary_never_self_updates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Q3 residence gate: a PATH-resolved yt-dlp (dev environment) gets
        no -U and no retry — the structured error surfaces immediately."""
        monkeypatch.setenv("PODCAST_READER_DATA_DIR", str(tmp_path / "data"))
        events: list[PipelineEvent] = []
        with (
            patch("podcast_reader.ytdlp.resolve_tool", return_value="/usr/bin/yt-dlp"),
            patch(
                "podcast_reader.ytdlp.run_child",
                return_value=self._completed([], 1, stderr="ERROR: broken"),
            ) as download_run,
            patch("podcast_reader.engine.managed_tools.run_child") as update_run,
            pytest.raises(PipelineError),
        ):
            download_audio(self.URL, tmp_path, on_event=events.append)

        assert download_run.call_count == 1
        update_run.assert_not_called()
        assert events == []

    def test_auth_required_failure_skips_the_self_update_retry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spec scenario (per U2): a managed-copy failure with
        download_auth_required runs no -U and no retry — a yt-dlp update
        cannot conjure missing credentials, so the error surfaces at once."""
        binary = self._managed_binary(tmp_path, monkeypatch)
        events: list[PipelineEvent] = []
        with (
            patch("podcast_reader.ytdlp.resolve_tool", return_value=str(binary)),
            patch(
                "podcast_reader.ytdlp.run_child",
                return_value=self._completed([], 1, stderr="ERROR: login required"),
            ) as download_run,
            patch("podcast_reader.engine.managed_tools.run_child") as update_run,
            pytest.raises(PipelineError) as excinfo,
        ):
            download_audio(self.URL, tmp_path, on_event=events.append)

        assert excinfo.value.code == "download_auth_required"
        assert download_run.call_count == 1  # no retry
        update_run.assert_not_called()  # no -U
        assert events == []  # no self-update warning

    def test_failed_self_update_still_retries_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failing -U (offline, already newest) does not abort the retry —
        the spec mandates -U once and one retry; recording is skipped."""
        binary = self._managed_binary(tmp_path, monkeypatch)
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        downloads: list[list[str]] = []

        def fake_download_run(args: list[str]) -> subprocess.CompletedProcess[str]:
            downloads.append(args)
            if len(downloads) == 1:
                return self._completed(args, 1, stderr="ERROR: flaky")
            (out_dir / "123.mp3").touch()
            return self._completed(args)

        with (
            patch("podcast_reader.ytdlp.resolve_tool", return_value=str(binary)),
            patch("podcast_reader.ytdlp.run_child", side_effect=fake_download_run),
            patch(
                "podcast_reader.engine.managed_tools.run_child",
                return_value=self._completed([], 1, stderr="-U failed"),
            ),
        ):
            result = download_audio(self.URL, out_dir, on_event=lambda _e: None)

        assert result == out_dir / "123.mp3"
        assert load_user_manifest(tmp_path / "data")["versions"] == {}
