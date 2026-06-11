"""Tests for podcast_reader.cli module."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

import pytest

from podcast_reader.cli import main_with_args
from podcast_reader.pipeline import InputType, PipelineError, classify_input
from podcast_reader.types import PipelineEvent, PipelineResult

if TYPE_CHECKING:
    from podcast_reader.types import PipelineRequest


class TestCliAdapter:
    @patch("podcast_reader.cli.run_pipeline")
    def test_one_shot_invokes_pipeline_and_prints(
        self, mock_run: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def fake(req: PipelineRequest, on_event: Callable[[PipelineEvent], None]) -> PipelineResult:
            on_event(
                PipelineEvent(kind="step_started", step="resolve", message="Resolving...", data={})
            )
            return PipelineResult(
                json_path="a.json", chapters_path=None, html_path="a.html", title="T"
            )

        mock_run.side_effect = fake
        main_with_args(["https://example.com/x.mp3", "T"])
        out = capsys.readouterr().out
        assert "Resolving..." in out and "a.html" in out

    @patch(
        "podcast_reader.cli.run_pipeline",
        side_effect=PipelineError("not_found", "File not found: /nope", ""),
    )
    def test_one_shot_error_exits_1(self, _m: MagicMock) -> None:
        with pytest.raises(SystemExit, match="1"):
            main_with_args(["/nope"])

    @patch("podcast_reader.cli.serve_engine")
    def test_serve_subcommand_dispatches(self, mock_serve: MagicMock) -> None:
        main_with_args(["serve", "--discovery-file", "/tmp/d.json"])
        mock_serve.assert_called_once()


_SAMPLE_SEGMENTS = {
    "segments": [
        {"start": 0.0, "end": 5.0, "text": "Hello world."},
        {"start": 5.0, "end": 10.0, "text": "Goodbye world."},
    ]
}


class TestCliChaptersFaultIsolation:
    """Spec: a chapters failure must not fail the CLI run (exit 0, warning printed)."""

    @patch("podcast_reader.cli._wsl_path", return_value=None)
    @patch("podcast_reader.pipeline._wsl_path", return_value=None)
    @patch("podcast_reader.pipeline.build_html", return_value="<html></html>")
    @patch(
        "podcast_reader.pipeline.generate_chapters",
        side_effect=RuntimeError("provider down"),
    )
    @patch("podcast_reader.pipeline.format_transcript", return_value="[0.0] Hi.")
    @patch("podcast_reader.pipeline.fetch_transcript")
    @patch("podcast_reader.pipeline.snippets_to_whisper_segments", return_value=_SAMPLE_SEGMENTS)
    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    def test_chapter_failure_prints_warning_writes_html_exits_zero(
        self,
        _s: MagicMock,
        _f: MagicMock,
        _fmt: MagicMock,
        _gen: MagicMock,
        _b: MagicMock,
        _pw: MagicMock,
        _cw: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # No SystemExit raised — the CLI run completes (exit 0)
        main_with_args(
            [
                "https://www.youtube.com/watch?v=abc123XYZqq",
                "T",
                "--output-dir",
                str(tmp_path),
            ]
        )
        out = capsys.readouterr().out
        assert "Chapter generation failed" in out
        assert (tmp_path / "abc123XYZqq.html").exists()
        assert "Chapters:" not in out  # chapterless result


class TestClassifyInput:
    def test_youtube_standard(self) -> None:
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        assert classify_input(url) == InputType.YOUTUBE

    def test_youtube_short(self) -> None:
        assert classify_input("https://youtu.be/dQw4w9WgXcQ") == InputType.YOUTUBE

    def test_youtube_embed(self) -> None:
        url = "https://www.youtube.com/embed/dQw4w9WgXcQ"
        assert classify_input(url) == InputType.YOUTUBE

    def test_x_url(self) -> None:
        url = "https://x.com/user/status/123456"
        assert classify_input(url) == InputType.URL

    def test_twitter_url(self) -> None:
        url = "https://twitter.com/user/status/123456"
        assert classify_input(url) == InputType.URL

    def test_vimeo_url(self) -> None:
        assert classify_input("https://vimeo.com/123456") == InputType.URL

    def test_direct_audio_url(self) -> None:
        url = "https://example.com/episode.mp3"
        assert classify_input(url) == InputType.URL

    def test_http_url(self) -> None:
        assert classify_input("http://example.com/video") == InputType.URL

    def test_local_file(self) -> None:
        assert classify_input("/home/user/episode.mp3") == InputType.LOCAL_FILE

    def test_relative_file(self) -> None:
        assert classify_input("episode.mp3") == InputType.LOCAL_FILE
