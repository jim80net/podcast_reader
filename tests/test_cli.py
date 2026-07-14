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

    @patch(
        "podcast_reader.cli.run_pipeline",
        side_effect=PipelineError("download_auth_required", "yt-dlp failed: login required", ""),
    )
    def test_auth_required_maps_the_env_hint(
        self, _m: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Spec scenario: CLI keeps the env hint — download_auth_required is
        raised neutral, and the CLI face authors the YT_DLP_COOKIES advice."""
        with pytest.raises(SystemExit, match="1"):
            main_with_args(["https://x.com/user/status/1"])
        err = capsys.readouterr().err
        assert "Set YT_DLP_COOKIES to a cookies file path for authenticated content." in err
        assert "--cookies-from-browser" not in err  # never recommended (per N2)

    @patch(
        "podcast_reader.cli.run_pipeline",
        side_effect=PipelineError("download_failed", "yt-dlp failed: broken", ""),
    )
    def test_non_auth_failure_gets_no_env_hint(
        self, _m: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit, match="1"):
            main_with_args(["https://x.com/user/status/1"])
        assert "YT_DLP_COOKIES" not in capsys.readouterr().err

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

_DUMMY_RESULT = PipelineResult(
    json_path="a.json", chapters_path=None, html_path="a.html", title="T"
)


class TestCliProviderSelection:
    """Spec: Key resolution and skip semantics + Model precedence (CLI face)."""

    def _captured_request(
        self, argv: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> PipelineRequest:
        """Run the CLI with run_pipeline patched, returning the built request."""
        with (
            patch("podcast_reader.cli.run_pipeline", return_value=_DUMMY_RESULT) as mock_run,
            patch("podcast_reader.cli._wsl_path", return_value=None),
        ):
            main_with_args(argv)
        request: PipelineRequest = mock_run.call_args.args[0]
        return request

    def test_caption_cleanup_requires_explicit_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        default = self._captured_request(["https://example.com/x.mp3"], monkeypatch)
        enabled = self._captured_request(
            ["https://example.com/x.mp3", "--cleanup-captions"], monkeypatch
        )

        assert default["caption_cleanup"] is False
        assert enabled["caption_cleanup"] is True

    def test_anthropic_env_var_compatibility(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Spec scenario: ANTHROPIC_API_KEY set, no provider flag — as before."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-legacy")
        request = self._captured_request(["https://example.com/x.mp3", "T"], monkeypatch)
        assert request["chapter_provider"] == "anthropic"
        assert request["chapter_api_key"] == "sk-ant-legacy"
        assert request["model"] is None  # provider default

    def test_no_key_resolves_to_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        request = self._captured_request(["https://example.com/x.mp3", "T"], monkeypatch)
        assert request["chapter_api_key"] is None

    def test_provider_flag_resolves_that_providers_env_var(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spec scenario: --provider deepseek + DEEPSEEK_API_KEY."""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds-1")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-ignored")
        request = self._captured_request(
            ["https://example.com/x.mp3", "T", "--provider", "deepseek"], monkeypatch
        )
        assert request["chapter_provider"] == "deepseek"
        assert request["chapter_api_key"] == "sk-ds-1"

    def test_provider_flag_without_model_uses_provider_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spec scenario: --provider deepseek without --model — model stays None,
        so generate_chapters resolves the DeepSeek registry default."""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds-1")
        request = self._captured_request(
            ["https://example.com/x.mp3", "T", "--provider", "deepseek"], monkeypatch
        )
        assert request["model"] is None

    def test_explicit_model_passes_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-1")
        request = self._captured_request(
            [
                "https://example.com/x.mp3",
                "T",
                "--provider",
                "openrouter",
                "--model",
                "meta-llama/llama-4-maverick",
            ],
            monkeypatch,
        )
        assert request["model"] == "meta-llama/llama-4-maverick"

    def test_custom_provider_reads_url_and_key_envs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PODCAST_READER_CUSTOM_PROVIDER_KEY", "sk-local")
        monkeypatch.setenv("PODCAST_READER_CUSTOM_PROVIDER_URL", "http://127.0.0.1:11434/v1")
        request = self._captured_request(
            ["https://example.com/x.mp3", "T", "--provider", "custom"], monkeypatch
        )
        assert request["chapter_provider"] == "custom"
        assert request["chapter_api_key"] == "sk-local"
        assert request["custom_provider_url"] == "http://127.0.0.1:11434/v1"

    def test_unknown_provider_rejected_by_argparse(self) -> None:
        with pytest.raises(SystemExit):
            main_with_args(["https://example.com/x.mp3", "T", "--provider", "nonsense"])


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
