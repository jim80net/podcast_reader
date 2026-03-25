"""Tests for podcast_reader.cli module."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from podcast_reader.cli import InputType, _run_pipeline, classify_input

_SAMPLE_SEGMENTS = {
    "segments": [
        {"start": 0.0, "end": 5.0, "text": "Hello world."},
        {"start": 5.0, "end": 10.0, "text": "Goodbye world."},
    ]
}

_SAMPLE_CHAPTERS = [
    {
        "title": "Intro",
        "start": 0.0,
        "end": 5.0,
        "abstract": "Opening remarks.",
        "type": "intro",
        "paragraph_breaks": [0.0],
        "key_points": [],
        "pull_quote": None,
        "pull_quote_start": None,
    },
    {
        "title": "Main",
        "start": 5.0,
        "end": 10.0,
        "abstract": "Main content.",
        "type": "content",
        "paragraph_breaks": [5.0],
        "key_points": ["Point one"],
        "pull_quote": None,
        "pull_quote_start": None,
    },
]


def _pipeline_defaults(
    *,
    input_arg: str,
    output_dir: Path,
    title: str | None = "Test Title",
) -> dict:
    """Build common keyword args for _run_pipeline."""
    return {
        "input_arg": input_arg,
        "title": title,
        "output_dir": output_dir,
        "model": "claude-haiku-4-5-20251001",
        "whisper_model": "large-v3",
        "whisper_lang": "en",
        "whisper_device": "cpu",
        "hf_token": None,
        "sentences": 5,
        "cookies": None,
    }


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


class TestRunPipelineYouTube:
    """Tests for the YouTube branch of _run_pipeline."""

    @patch("podcast_reader.cli._wsl_path", return_value=None)
    @patch("podcast_reader.cli.build_html", return_value="<html>test</html>")
    @patch("podcast_reader.cli.fetch_transcript")
    @patch("podcast_reader.cli.snippets_to_whisper_segments")
    def test_fetches_transcript_and_writes_html(
        self,
        mock_snippets: MagicMock,
        mock_fetch: MagicMock,
        mock_build_html: MagicMock,
        _mock_wsl: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_fetch.return_value = [{"text": "Hi.", "start": 0.0, "duration": 5.0}]
        mock_snippets.return_value = _SAMPLE_SEGMENTS

        kwargs = _pipeline_defaults(
            input_arg="https://www.youtube.com/watch?v=abc123XYZqq",
            output_dir=tmp_path,
        )
        _run_pipeline(**kwargs)

        mock_fetch.assert_called_once_with("abc123XYZqq")
        json_path = tmp_path / "abc123XYZqq.json"
        assert json_path.exists()
        assert json.loads(json_path.read_text()) == _SAMPLE_SEGMENTS

        html_path = tmp_path / "abc123XYZqq.html"
        assert html_path.exists()
        assert html_path.read_text() == "<html>test</html>"
        mock_build_html.assert_called_once()

    @patch("podcast_reader.cli._wsl_path", return_value=None)
    @patch("podcast_reader.cli.build_html", return_value="<html>cached</html>")
    @patch("podcast_reader.cli.fetch_transcript")
    def test_skips_fetch_when_json_exists(
        self,
        mock_fetch: MagicMock,
        mock_build_html: MagicMock,
        _mock_wsl: MagicMock,
        tmp_path: Path,
    ) -> None:
        json_path = tmp_path / "abc123XYZqq.json"
        json_path.write_text(json.dumps(_SAMPLE_SEGMENTS))

        kwargs = _pipeline_defaults(
            input_arg="https://www.youtube.com/watch?v=abc123XYZqq",
            output_dir=tmp_path,
        )
        _run_pipeline(**kwargs)

        mock_fetch.assert_not_called()
        mock_build_html.assert_called_once()

    def test_exits_on_invalid_video_id(self, tmp_path: Path) -> None:
        kwargs = _pipeline_defaults(
            input_arg="https://www.youtube.com/watch?v=",
            output_dir=tmp_path,
        )
        with pytest.raises(SystemExit, match="1"):
            _run_pipeline(**kwargs)

    @patch("podcast_reader.cli._wsl_path", return_value=None)
    @patch("podcast_reader.cli.build_html", return_value="<html></html>")
    @patch("podcast_reader.cli.fetch_transcript")
    @patch("podcast_reader.cli.snippets_to_whisper_segments", return_value=_SAMPLE_SEGMENTS)
    @patch("podcast_reader.cli.fetch_video_title", return_value="Auto Title")
    def test_fetches_title_when_none(
        self,
        mock_title: MagicMock,
        _mock_snippets: MagicMock,
        _mock_fetch: MagicMock,
        mock_build_html: MagicMock,
        _mock_wsl: MagicMock,
        tmp_path: Path,
    ) -> None:
        kwargs = _pipeline_defaults(
            input_arg="https://www.youtube.com/watch?v=abc123XYZqq",
            output_dir=tmp_path,
            title=None,
        )
        _run_pipeline(**kwargs)

        mock_title.assert_called_once_with("abc123XYZqq")
        # build_html should receive the auto-fetched title
        call_args = mock_build_html.call_args
        assert call_args[0][1] == "Auto Title"


class TestRunPipelineURL:
    """Tests for the generic URL (yt-dlp) branch of _run_pipeline."""

    @patch("podcast_reader.cli._wsl_path", return_value=None)
    @patch("podcast_reader.cli.build_html", return_value="<html>url</html>")
    @patch("podcast_reader.cli.transcribe")
    @patch("podcast_reader.cli.download_audio")
    def test_downloads_and_transcribes(
        self,
        mock_download: MagicMock,
        mock_transcribe: MagicMock,
        mock_build_html: MagicMock,
        _mock_wsl: MagicMock,
        tmp_path: Path,
    ) -> None:
        audio_path = tmp_path / "video_id.mp3"
        json_path = tmp_path / "video_id.json"

        def fake_download(url: str, out_dir: Path, *, cookies: Path | None = None) -> Path:
            audio_path.write_text("fake audio")
            return audio_path

        mock_download.side_effect = fake_download

        def write_json(**kwargs: object) -> None:
            json_path.write_text(json.dumps(_SAMPLE_SEGMENTS))

        mock_transcribe.side_effect = write_json

        kwargs = _pipeline_defaults(
            input_arg="https://x.com/user/status/123456",
            output_dir=tmp_path,
        )
        _run_pipeline(**kwargs)

        mock_download.assert_called_once_with(
            "https://x.com/user/status/123456", tmp_path, cookies=None
        )
        mock_transcribe.assert_called_once()
        assert (tmp_path / "video_id.html").exists()

    @patch("podcast_reader.cli._wsl_path", return_value=None)
    @patch("podcast_reader.cli.build_html", return_value="<html>cached</html>")
    @patch("podcast_reader.cli.transcribe")
    @patch("podcast_reader.cli.download_audio")
    def test_skips_download_when_mp3_exists(
        self,
        mock_download: MagicMock,
        mock_transcribe: MagicMock,
        mock_build_html: MagicMock,
        _mock_wsl: MagicMock,
        tmp_path: Path,
    ) -> None:
        # Pre-create an mp3 file with .ytdlp marker (simulating a previous download)
        audio_path = tmp_path / "video_id.mp3"
        audio_path.write_text("fake audio")
        (tmp_path / "video_id.ytdlp").write_text("https://x.com/user/status/123456")

        json_path = tmp_path / "video_id.json"

        def write_json(**kwargs: object) -> None:
            json_path.write_text(json.dumps(_SAMPLE_SEGMENTS))

        mock_transcribe.side_effect = write_json

        kwargs = _pipeline_defaults(
            input_arg="https://x.com/user/status/123456",
            output_dir=tmp_path,
        )
        _run_pipeline(**kwargs)

        mock_download.assert_not_called()
        mock_transcribe.assert_called_once()

    @patch("podcast_reader.cli._wsl_path", return_value=None)
    @patch("podcast_reader.cli.build_html", return_value="<html>cached</html>")
    @patch("podcast_reader.cli.transcribe")
    @patch("podcast_reader.cli.download_audio")
    def test_skips_transcribe_when_json_exists(
        self,
        mock_download: MagicMock,
        mock_transcribe: MagicMock,
        mock_build_html: MagicMock,
        _mock_wsl: MagicMock,
        tmp_path: Path,
    ) -> None:
        audio_path = tmp_path / "video_id.mp3"
        audio_path.write_text("fake audio")
        mock_download.return_value = audio_path

        json_path = tmp_path / "video_id.json"
        json_path.write_text(json.dumps(_SAMPLE_SEGMENTS))

        kwargs = _pipeline_defaults(
            input_arg="https://x.com/user/status/123456",
            output_dir=tmp_path,
        )
        _run_pipeline(**kwargs)

        mock_transcribe.assert_not_called()
        mock_build_html.assert_called_once()

    @patch("podcast_reader.cli._wsl_path", return_value=None)
    @patch("podcast_reader.cli.build_html", return_value="<html></html>")
    @patch("podcast_reader.cli.transcribe")
    @patch("podcast_reader.cli.download_audio")
    @patch("podcast_reader.cli.fetch_title", return_value="X Post Title")
    def test_fetches_title_when_none(
        self,
        mock_title: MagicMock,
        mock_download: MagicMock,
        mock_transcribe: MagicMock,
        mock_build_html: MagicMock,
        _mock_wsl: MagicMock,
        tmp_path: Path,
    ) -> None:
        audio_path = tmp_path / "video_id.mp3"
        audio_path.write_text("fake audio")
        mock_download.return_value = audio_path

        json_path = tmp_path / "video_id.json"
        json_path.write_text(json.dumps(_SAMPLE_SEGMENTS))

        kwargs = _pipeline_defaults(
            input_arg="https://x.com/user/status/123456",
            output_dir=tmp_path,
            title=None,
        )
        _run_pipeline(**kwargs)

        mock_title.assert_called_once_with("https://x.com/user/status/123456")
        call_args = mock_build_html.call_args
        assert call_args[0][1] == "X Post Title"

    @patch("podcast_reader.cli._wsl_path", return_value=None)
    @patch("podcast_reader.cli.build_html", return_value="<html></html>")
    @patch("podcast_reader.cli.transcribe")
    @patch("podcast_reader.cli.download_audio")
    @patch("podcast_reader.cli.fetch_title", side_effect=RuntimeError("no title"))
    def test_title_fallback_on_fetch_error(
        self,
        _mock_title: MagicMock,
        mock_download: MagicMock,
        mock_transcribe: MagicMock,
        mock_build_html: MagicMock,
        _mock_wsl: MagicMock,
        tmp_path: Path,
    ) -> None:
        audio_path = tmp_path / "video_id.mp3"
        audio_path.write_text("fake audio")
        mock_download.return_value = audio_path

        json_path = tmp_path / "video_id.json"
        json_path.write_text(json.dumps(_SAMPLE_SEGMENTS))

        kwargs = _pipeline_defaults(
            input_arg="https://x.com/user/status/123456",
            output_dir=tmp_path,
            title=None,
        )
        _run_pipeline(**kwargs)

        # Should fall back to stem-derived title
        call_args = mock_build_html.call_args
        assert call_args[0][1] == "Video Id"


class TestRunPipelineLocalFile:
    """Tests for the local file branch of _run_pipeline."""

    @patch("podcast_reader.cli._wsl_path", return_value=None)
    @patch("podcast_reader.cli.build_html", return_value="<html>local</html>")
    @patch("podcast_reader.cli.transcribe")
    def test_transcribes_local_file(
        self,
        mock_transcribe: MagicMock,
        mock_build_html: MagicMock,
        _mock_wsl: MagicMock,
        tmp_path: Path,
    ) -> None:
        audio_path = tmp_path / "episode.mp3"
        audio_path.write_text("fake audio")

        json_path = tmp_path / "episode.json"

        def write_json(**kwargs: object) -> None:
            json_path.write_text(json.dumps(_SAMPLE_SEGMENTS))

        mock_transcribe.side_effect = write_json

        kwargs = _pipeline_defaults(
            input_arg=str(audio_path),
            output_dir=tmp_path,
        )
        _run_pipeline(**kwargs)

        mock_transcribe.assert_called_once()
        assert (tmp_path / "episode.html").exists()

    def test_exits_on_missing_file(self, tmp_path: Path) -> None:
        kwargs = _pipeline_defaults(
            input_arg=str(tmp_path / "nonexistent.mp3"),
            output_dir=tmp_path,
        )
        with pytest.raises(SystemExit, match="1"):
            _run_pipeline(**kwargs)

    @patch("podcast_reader.cli._wsl_path", return_value=None)
    @patch("podcast_reader.cli.build_html", return_value="<html>cached</html>")
    @patch("podcast_reader.cli.transcribe")
    def test_skips_transcribe_when_json_exists(
        self,
        mock_transcribe: MagicMock,
        mock_build_html: MagicMock,
        _mock_wsl: MagicMock,
        tmp_path: Path,
    ) -> None:
        audio_path = tmp_path / "episode.mp3"
        audio_path.write_text("fake audio")

        json_path = tmp_path / "episode.json"
        json_path.write_text(json.dumps(_SAMPLE_SEGMENTS))

        kwargs = _pipeline_defaults(
            input_arg=str(audio_path),
            output_dir=tmp_path,
        )
        _run_pipeline(**kwargs)

        mock_transcribe.assert_not_called()
        mock_build_html.assert_called_once()


class TestRunPipelineChapters:
    """Tests for chapter generation within _run_pipeline."""

    @patch("podcast_reader.cli._wsl_path", return_value=None)
    @patch("podcast_reader.cli.build_html", return_value="<html></html>")
    @patch("podcast_reader.cli.snap_chapters_to_segments", return_value=_SAMPLE_CHAPTERS)
    @patch("podcast_reader.cli.generate_chapters", return_value=_SAMPLE_CHAPTERS)
    @patch("podcast_reader.cli.format_transcript", return_value="[0.0] Hello.")
    @patch("podcast_reader.cli.fetch_transcript")
    @patch("podcast_reader.cli.snippets_to_whisper_segments", return_value=_SAMPLE_SEGMENTS)
    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    def test_generates_chapters_when_api_key_set(
        self,
        _mock_snippets: MagicMock,
        _mock_fetch: MagicMock,
        mock_format: MagicMock,
        mock_generate: MagicMock,
        mock_snap: MagicMock,
        mock_build_html: MagicMock,
        _mock_wsl: MagicMock,
        tmp_path: Path,
    ) -> None:
        kwargs = _pipeline_defaults(
            input_arg="https://www.youtube.com/watch?v=abc123XYZqq",
            output_dir=tmp_path,
        )
        _run_pipeline(**kwargs)

        mock_format.assert_called_once()
        mock_generate.assert_called_once()
        mock_snap.assert_called_once()

        chapters_path = tmp_path / "abc123XYZqq_chapters.json"
        assert chapters_path.exists()
        assert json.loads(chapters_path.read_text()) == _SAMPLE_CHAPTERS

        # build_html should receive the chapters as keyword arg
        assert mock_build_html.call_args.kwargs["chapters"] == _SAMPLE_CHAPTERS

    @patch("podcast_reader.cli._wsl_path", return_value=None)
    @patch("podcast_reader.cli.build_html", return_value="<html></html>")
    @patch("podcast_reader.cli.generate_chapters")
    @patch("podcast_reader.cli.fetch_transcript")
    @patch("podcast_reader.cli.snippets_to_whisper_segments", return_value=_SAMPLE_SEGMENTS)
    def test_skips_chapters_without_api_key(
        self,
        _mock_snippets: MagicMock,
        _mock_fetch: MagicMock,
        mock_generate: MagicMock,
        mock_build_html: MagicMock,
        _mock_wsl: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        kwargs = _pipeline_defaults(
            input_arg="https://www.youtube.com/watch?v=abc123XYZqq",
            output_dir=tmp_path,
        )
        _run_pipeline(**kwargs)

        mock_generate.assert_not_called()
        # build_html should receive None for chapters
        assert mock_build_html.call_args.kwargs["chapters"] is None

    @patch("podcast_reader.cli._wsl_path", return_value=None)
    @patch("podcast_reader.cli.build_html", return_value="<html></html>")
    @patch("podcast_reader.cli.generate_chapters")
    @patch("podcast_reader.cli.fetch_transcript")
    @patch("podcast_reader.cli.snippets_to_whisper_segments", return_value=_SAMPLE_SEGMENTS)
    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    def test_loads_cached_chapters(
        self,
        _mock_snippets: MagicMock,
        _mock_fetch: MagicMock,
        mock_generate: MagicMock,
        mock_build_html: MagicMock,
        _mock_wsl: MagicMock,
        tmp_path: Path,
    ) -> None:
        chapters_path = tmp_path / "abc123XYZqq_chapters.json"
        chapters_path.write_text(json.dumps(_SAMPLE_CHAPTERS))

        kwargs = _pipeline_defaults(
            input_arg="https://www.youtube.com/watch?v=abc123XYZqq",
            output_dir=tmp_path,
        )
        _run_pipeline(**kwargs)

        mock_generate.assert_not_called()
        assert mock_build_html.call_args.kwargs["chapters"] == _SAMPLE_CHAPTERS


class TestRunPipelineTranscriptSource:
    """Verify the correct transcript source label is passed to build_html."""

    @patch("podcast_reader.cli._wsl_path", return_value=None)
    @patch("podcast_reader.cli.build_html", return_value="<html></html>")
    @patch("podcast_reader.cli.fetch_transcript")
    @patch("podcast_reader.cli.snippets_to_whisper_segments", return_value=_SAMPLE_SEGMENTS)
    def test_youtube_source_label(
        self,
        _mock_snippets: MagicMock,
        _mock_fetch: MagicMock,
        mock_build_html: MagicMock,
        _mock_wsl: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        kwargs = _pipeline_defaults(
            input_arg="https://www.youtube.com/watch?v=abc123XYZqq",
            output_dir=tmp_path,
        )
        _run_pipeline(**kwargs)

        call_kwargs = mock_build_html.call_args[1]
        assert call_kwargs["source"] == "youtube-captions"

    @patch("podcast_reader.cli._wsl_path", return_value=None)
    @patch("podcast_reader.cli.build_html", return_value="<html></html>")
    @patch("podcast_reader.cli.transcribe")
    @patch("podcast_reader.cli.download_audio")
    def test_url_source_label(
        self,
        mock_download: MagicMock,
        _mock_transcribe: MagicMock,
        mock_build_html: MagicMock,
        _mock_wsl: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        audio_path = tmp_path / "video_id.mp3"
        audio_path.write_text("fake audio")
        mock_download.return_value = audio_path

        json_path = tmp_path / "video_id.json"
        json_path.write_text(json.dumps(_SAMPLE_SEGMENTS))

        kwargs = _pipeline_defaults(
            input_arg="https://x.com/user/status/123456",
            output_dir=tmp_path,
        )
        _run_pipeline(**kwargs)

        call_kwargs = mock_build_html.call_args[1]
        assert call_kwargs["source"] == "whisper-ctranslate2"


class TestRunPipelineYtdlpIntegration:
    """End-to-end integration test for the yt-dlp download path.

    Downloads a small public video, mocks only whisper (which needs a GPU)
    and chapter generation. Verifies that yt-dlp download, caching, and
    HTML generation work together.
    """

    @pytest.mark.integration
    @patch("podcast_reader.cli._wsl_path", return_value=None)
    @patch("podcast_reader.cli.transcribe")
    def test_ytdlp_downloads_and_produces_html(
        self,
        mock_transcribe: MagicMock,
        _mock_wsl: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Download a short public video via yt-dlp, mock whisper, verify HTML output."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        # Use a tiny (~194KB) public mp3 from archive.org
        url = "https://archive.org/details/testmp3testfile"

        # Mock whisper to write a simple JSON (no GPU needed)
        def fake_transcribe(**kwargs: object) -> None:
            output_dir = kwargs["output_dir"]
            audio_path = kwargs["audio_path"]
            assert isinstance(output_dir, type(tmp_path))
            assert isinstance(audio_path, type(tmp_path))
            json_path = output_dir / f"{audio_path.stem}.json"  # type: ignore[union-attr]
            json_path.write_text(json.dumps(_SAMPLE_SEGMENTS))  # type: ignore[union-attr]

        mock_transcribe.side_effect = fake_transcribe

        try:
            _run_pipeline(
                input_arg=url,
                title="Integration Test",
                output_dir=tmp_path,
                model="claude-haiku-4-5-20251001",
                whisper_model="large-v3",
                whisper_lang="en",
                whisper_device="cpu",
                hf_token=None,
                sentences=5,
                cookies=None,
            )
        except RuntimeError as exc:
            if "login" in str(exc).lower() or "auth" in str(exc).lower():
                pytest.skip(f"yt-dlp download requires auth: {exc}")
            raise

        # Verify yt-dlp downloaded an audio file and left a marker
        ytdlp_markers = list(tmp_path.glob("*.ytdlp"))
        assert len(ytdlp_markers) >= 1, "Expected .ytdlp marker file after download"

        mp3_files = list(tmp_path.glob("*.mp3"))
        assert len(mp3_files) >= 1, "Expected downloaded mp3 file"

        html_files = list(tmp_path.glob("*.html"))
        assert len(html_files) == 1, "Expected exactly one HTML output"
        assert html_files[0].stat().st_size > 0
