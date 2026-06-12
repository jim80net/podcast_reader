"""Tests for podcast_reader.pipeline module."""

from __future__ import annotations

import json
import os
import sys
from typing import TYPE_CHECKING
from unittest.mock import ANY, MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

    from podcast_reader.types import PipelineEvent

import pytest

from podcast_reader.pipeline import (
    PipelineError,
    _find_ytdlp_marker,
    _valid_artifact,
    run_pipeline,
)
from podcast_reader.types import PipelineRequest


@pytest.fixture(autouse=True)
def _isolate_anthropic_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip ANTHROPIC_API_KEY so no test accidentally calls the real Anthropic API.

    Tests that exercise chapter generation set the key explicitly via
    @patch.dict, which applies after this fixture runs.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


_YT_URL = "https://www.youtube.com/watch?v=abc123XYZqq"

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


def _request(
    *,
    input_arg: str,
    output_dir: Path,
    title: str | None = "Test Title",
    chapter_provider: str = "anthropic",
    chapter_api_key: str | None = None,
) -> PipelineRequest:
    """Build a PipelineRequest with test defaults."""
    return PipelineRequest(
        source=input_arg,
        title=title,
        output_dir=str(output_dir),
        model=None,
        whisper_model="large-v3",
        whisper_lang="en",
        whisper_device="cpu",
        hf_token=None,
        sentences=5,
        cookies=None,
        chapter_provider=chapter_provider,
        chapter_api_key=chapter_api_key,
        custom_provider_url="",
    )


class TestRunPipelineYouTube:
    """Tests for the YouTube branch of run_pipeline."""

    @patch("podcast_reader.pipeline._wsl_path", return_value=None)
    @patch("podcast_reader.pipeline.build_html", return_value="<html>test</html>")
    @patch("podcast_reader.pipeline.fetch_transcript")
    @patch("podcast_reader.pipeline.snippets_to_whisper_segments")
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

        run_pipeline(
            _request(input_arg=_YT_URL, output_dir=tmp_path),
            on_event=lambda e: None,
        )

        mock_fetch.assert_called_once_with("abc123XYZqq")
        json_path = tmp_path / "abc123XYZqq.json"
        assert json_path.exists()
        assert json.loads(json_path.read_text()) == _SAMPLE_SEGMENTS

        html_path = tmp_path / "abc123XYZqq.html"
        assert html_path.exists()
        assert html_path.read_text() == "<html>test</html>"
        mock_build_html.assert_called_once()

    @patch("podcast_reader.pipeline._wsl_path", return_value=None)
    @patch("podcast_reader.pipeline.build_html", return_value="<html>cached</html>")
    @patch("podcast_reader.pipeline.fetch_transcript")
    def test_skips_fetch_when_json_exists(
        self,
        mock_fetch: MagicMock,
        mock_build_html: MagicMock,
        _mock_wsl: MagicMock,
        tmp_path: Path,
    ) -> None:
        json_path = tmp_path / "abc123XYZqq.json"
        json_path.write_text(json.dumps(_SAMPLE_SEGMENTS))

        run_pipeline(
            _request(input_arg=_YT_URL, output_dir=tmp_path),
            on_event=lambda e: None,
        )

        mock_fetch.assert_not_called()
        mock_build_html.assert_called_once()

    def test_raises_on_invalid_video_id(self, tmp_path: Path) -> None:
        with pytest.raises(PipelineError, match="Could not extract video ID"):
            run_pipeline(
                _request(
                    input_arg="https://www.youtube.com/watch?v=",
                    output_dir=tmp_path,
                ),
                on_event=lambda e: None,
            )

    @patch("podcast_reader.pipeline._wsl_path", return_value=None)
    @patch("podcast_reader.pipeline.build_html", return_value="<html></html>")
    @patch("podcast_reader.pipeline.fetch_transcript")
    @patch("podcast_reader.pipeline.snippets_to_whisper_segments", return_value=_SAMPLE_SEGMENTS)
    @patch("podcast_reader.pipeline.fetch_video_title", return_value="Auto Title")
    def test_fetches_title_when_none(
        self,
        mock_title: MagicMock,
        _mock_snippets: MagicMock,
        _mock_fetch: MagicMock,
        mock_build_html: MagicMock,
        _mock_wsl: MagicMock,
        tmp_path: Path,
    ) -> None:
        run_pipeline(
            _request(input_arg=_YT_URL, output_dir=tmp_path, title=None),
            on_event=lambda e: None,
        )

        mock_title.assert_called_once_with("abc123XYZqq")
        # build_html should receive the auto-fetched title
        call_args = mock_build_html.call_args
        assert call_args[0][1] == "Auto Title"

    @patch("podcast_reader.pipeline._wsl_path", return_value=None)
    @patch("podcast_reader.pipeline.build_html", return_value="<html>refetched</html>")
    @patch("podcast_reader.pipeline.fetch_transcript")
    @patch("podcast_reader.pipeline.snippets_to_whisper_segments", return_value=_SAMPLE_SEGMENTS)
    def test_corrupt_json_cache_refetches(
        self,
        _mock_snippets: MagicMock,
        mock_fetch: MagicMock,
        mock_build_html: MagicMock,
        _mock_wsl: MagicMock,
        tmp_path: Path,
    ) -> None:
        """A truncated cached JSON is discarded and the transcript is re-fetched."""
        json_path = tmp_path / "abc123XYZqq.json"
        json_path.write_text('{"segments": [{"start": 0.0,')  # truncated

        run_pipeline(
            _request(input_arg=_YT_URL, output_dir=tmp_path),
            on_event=lambda e: None,
        )

        mock_fetch.assert_called_once_with("abc123XYZqq")
        assert json.loads(json_path.read_text()) == _SAMPLE_SEGMENTS
        assert (tmp_path / "abc123XYZqq.html").exists()
        mock_build_html.assert_called_once()


class TestRunPipelineURL:
    """Tests for the generic URL (yt-dlp) branch of run_pipeline."""

    @patch("podcast_reader.pipeline._wsl_path", return_value=None)
    @patch("podcast_reader.pipeline.build_html", return_value="<html>url</html>")
    @patch("podcast_reader.pipeline.transcribe")
    @patch("podcast_reader.pipeline.download_audio")
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

        def fake_download(
            url: str, out_dir: Path, *, cookies: Path | None = None, on_event: object = None
        ) -> Path:
            audio_path.write_text("fake audio")
            return audio_path

        mock_download.side_effect = fake_download

        def write_json(**kwargs: object) -> None:
            json_path.write_text(json.dumps(_SAMPLE_SEGMENTS))

        mock_transcribe.side_effect = write_json

        run_pipeline(
            _request(input_arg="https://x.com/user/status/123456", output_dir=tmp_path),
            on_event=lambda e: None,
        )

        mock_download.assert_called_once_with(
            "https://x.com/user/status/123456", tmp_path, cookies=None, on_event=ANY
        )
        mock_transcribe.assert_called_once()
        assert (tmp_path / "video_id.html").exists()

    @patch("podcast_reader.pipeline._wsl_path", return_value=None)
    @patch("podcast_reader.pipeline.build_html", return_value="<html>cached</html>")
    @patch("podcast_reader.pipeline.transcribe")
    @patch("podcast_reader.pipeline.download_audio")
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

        run_pipeline(
            _request(input_arg="https://x.com/user/status/123456", output_dir=tmp_path),
            on_event=lambda e: None,
        )

        mock_download.assert_not_called()
        mock_transcribe.assert_called_once()

    @patch("podcast_reader.pipeline._wsl_path", return_value=None)
    @patch("podcast_reader.pipeline.build_html", return_value="<html>cached</html>")
    @patch("podcast_reader.pipeline.transcribe")
    @patch("podcast_reader.pipeline.download_audio")
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

        run_pipeline(
            _request(input_arg="https://x.com/user/status/123456", output_dir=tmp_path),
            on_event=lambda e: None,
        )

        mock_transcribe.assert_not_called()
        mock_build_html.assert_called_once()

    @patch("podcast_reader.pipeline._wsl_path", return_value=None)
    @patch("podcast_reader.pipeline.build_html", return_value="<html>redownload</html>")
    @patch("podcast_reader.pipeline.transcribe")
    @patch("podcast_reader.pipeline.download_audio")
    def test_redownloads_when_mp3_deleted_but_marker_remains(
        self,
        mock_download: MagicMock,
        mock_transcribe: MagicMock,
        mock_build_html: MagicMock,
        _mock_wsl: MagicMock,
        tmp_path: Path,
    ) -> None:
        # Orphaned marker — mp3 was deleted by user
        (tmp_path / "video_id.ytdlp").write_text("https://x.com/user/status/123456")
        # No video_id.mp3

        audio_path = tmp_path / "video_id.mp3"

        def fake_download(
            url: str, out_dir: Path, *, cookies: Path | None = None, on_event: object = None
        ) -> Path:
            audio_path.write_text("fake audio")
            return audio_path

        mock_download.side_effect = fake_download

        json_path = tmp_path / "video_id.json"

        def write_json(**kwargs: object) -> None:
            json_path.write_text(json.dumps(_SAMPLE_SEGMENTS))

        mock_transcribe.side_effect = write_json

        run_pipeline(
            _request(input_arg="https://x.com/user/status/123456", output_dir=tmp_path),
            on_event=lambda e: None,
        )

        mock_download.assert_called_once()
        # Orphaned marker should have been cleaned up by _find_ytdlp_marker
        # and a new one created by download_audio
        assert (tmp_path / "video_id.html").exists()

    @patch("podcast_reader.pipeline._wsl_path", return_value=None)
    @patch("podcast_reader.pipeline.build_html", return_value="<html></html>")
    @patch("podcast_reader.pipeline.transcribe")
    @patch("podcast_reader.pipeline.download_audio")
    @patch("podcast_reader.pipeline.fetch_title", return_value="X Post Title")
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

        run_pipeline(
            _request(
                input_arg="https://x.com/user/status/123456",
                output_dir=tmp_path,
                title=None,
            ),
            on_event=lambda e: None,
        )

        mock_title.assert_called_once_with("https://x.com/user/status/123456")
        call_args = mock_build_html.call_args
        assert call_args[0][1] == "X Post Title"

    @patch("podcast_reader.pipeline._wsl_path", return_value=None)
    @patch("podcast_reader.pipeline.build_html", return_value="<html></html>")
    @patch("podcast_reader.pipeline.transcribe")
    @patch("podcast_reader.pipeline.download_audio")
    @patch("podcast_reader.pipeline.fetch_title", side_effect=RuntimeError("no title"))
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

        run_pipeline(
            _request(
                input_arg="https://x.com/user/status/123456",
                output_dir=tmp_path,
                title=None,
            ),
            on_event=lambda e: None,
        )

        # Should fall back to stem-derived title
        call_args = mock_build_html.call_args
        assert call_args[0][1] == "Video Id"


class TestFindYtdlpMarker:
    """Tests for the _find_ytdlp_marker helper."""

    def test_matches_by_url(self, tmp_path: Path) -> None:
        url = "https://x.com/user/status/111"
        (tmp_path / "abc.ytdlp").write_text(url)
        (tmp_path / "abc.mp3").write_text("audio")

        result = _find_ytdlp_marker(tmp_path, url)
        assert result == tmp_path / "abc.ytdlp"

    def test_ignores_marker_with_different_url(self, tmp_path: Path) -> None:
        (tmp_path / "abc.ytdlp").write_text("https://x.com/other/status/999")
        (tmp_path / "abc.mp3").write_text("audio")

        result = _find_ytdlp_marker(tmp_path, "https://x.com/user/status/111")
        assert result is None

    def test_selects_correct_marker_among_multiple(self, tmp_path: Path) -> None:
        (tmp_path / "aaa.ytdlp").write_text("https://x.com/other")
        (tmp_path / "aaa.mp3").write_text("audio")
        (tmp_path / "bbb.ytdlp").write_text("https://x.com/target")
        (tmp_path / "bbb.mp3").write_text("audio")

        result = _find_ytdlp_marker(tmp_path, "https://x.com/target")
        assert result == tmp_path / "bbb.ytdlp"

    def test_removes_orphaned_marker(self, tmp_path: Path) -> None:
        orphan = tmp_path / "deleted.ytdlp"
        orphan.write_text("https://x.com/gone")
        # No corresponding .mp3

        result = _find_ytdlp_marker(tmp_path, "https://x.com/gone")
        assert result is None
        assert not orphan.exists(), "Orphaned marker should be cleaned up"

    def test_returns_none_when_no_markers(self, tmp_path: Path) -> None:
        result = _find_ytdlp_marker(tmp_path, "https://x.com/user/status/111")
        assert result is None

    @pytest.mark.skipif(
        sys.platform == "win32" or os.geteuid() == 0,
        reason="chmod 0o000 does not block reads on Windows or for root",
    )
    def test_skips_unreadable_marker(self, tmp_path: Path) -> None:
        """An unreadable marker is skipped (cache miss), not fatal (C4)."""
        unreadable = tmp_path / "broken.ytdlp"
        unreadable.write_text("https://x.com/target")
        (tmp_path / "broken.mp3").write_text("audio")
        unreadable.chmod(0o000)
        try:
            result = _find_ytdlp_marker(tmp_path, "https://x.com/target")
        finally:
            unreadable.chmod(0o644)
        assert result is None


class TestValidArtifact:
    """Tests for the _valid_artifact cache check."""

    def test_unremovable_invalid_artifact_returns_false(self, tmp_path: Path) -> None:
        """A failing cleanup unlink must not crash the cache check (C5).

        A directory named like the artifact raises OSError on both read and
        unlink; the check must still report a cache miss.
        """
        path = tmp_path / "a.json"
        path.mkdir()
        assert _valid_artifact(path) is False
        assert path.exists()  # cleanup failed, but the check stayed graceful


class TestRunPipelineLocalFile:
    """Tests for the local file branch of run_pipeline."""

    @patch("podcast_reader.pipeline._wsl_path", return_value=None)
    @patch("podcast_reader.pipeline.build_html", return_value="<html>local</html>")
    @patch("podcast_reader.pipeline.transcribe")
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

        run_pipeline(
            _request(input_arg=str(audio_path), output_dir=tmp_path),
            on_event=lambda e: None,
        )

        mock_transcribe.assert_called_once()
        assert (tmp_path / "episode.html").exists()

    def test_raises_on_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(PipelineError, match="File not found"):
            run_pipeline(
                _request(input_arg=str(tmp_path / "nonexistent.mp3"), output_dir=tmp_path),
                on_event=lambda e: None,
            )

    @patch("podcast_reader.pipeline._wsl_path", return_value=None)
    @patch("podcast_reader.pipeline.build_html", return_value="<html>cached</html>")
    @patch("podcast_reader.pipeline.transcribe")
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

        run_pipeline(
            _request(input_arg=str(audio_path), output_dir=tmp_path),
            on_event=lambda e: None,
        )

        mock_transcribe.assert_not_called()
        mock_build_html.assert_called_once()


class TestRunPipelineChapters:
    """Tests for chapter generation within run_pipeline."""

    @patch("podcast_reader.pipeline._wsl_path", return_value=None)
    @patch("podcast_reader.pipeline.build_html", return_value="<html></html>")
    @patch("podcast_reader.pipeline.snap_chapters_to_segments", return_value=_SAMPLE_CHAPTERS)
    @patch("podcast_reader.pipeline.generate_chapters", return_value=_SAMPLE_CHAPTERS)
    @patch("podcast_reader.pipeline.format_transcript", return_value="[0.0] Hello.")
    @patch("podcast_reader.pipeline.fetch_transcript")
    @patch("podcast_reader.pipeline.snippets_to_whisper_segments", return_value=_SAMPLE_SEGMENTS)
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
        run_pipeline(
            _request(input_arg=_YT_URL, output_dir=tmp_path, chapter_api_key="test-key"),
            on_event=lambda e: None,
        )

        mock_format.assert_called_once()
        mock_generate.assert_called_once()
        mock_snap.assert_called_once()

        chapters_path = tmp_path / "abc123XYZqq_chapters.json"
        assert chapters_path.exists()
        assert json.loads(chapters_path.read_text()) == _SAMPLE_CHAPTERS

        # build_html should receive the chapters as keyword arg
        assert mock_build_html.call_args.kwargs["chapters"] == _SAMPLE_CHAPTERS

    @patch("podcast_reader.pipeline._wsl_path", return_value=None)
    @patch("podcast_reader.pipeline.build_html", return_value="<html></html>")
    @patch("podcast_reader.pipeline.generate_chapters")
    @patch("podcast_reader.pipeline.fetch_transcript")
    @patch("podcast_reader.pipeline.snippets_to_whisper_segments", return_value=_SAMPLE_SEGMENTS)
    def test_skips_chapters_without_api_key(
        self,
        _mock_snippets: MagicMock,
        _mock_fetch: MagicMock,
        mock_generate: MagicMock,
        mock_build_html: MagicMock,
        _mock_wsl: MagicMock,
        tmp_path: Path,
    ) -> None:
        run_pipeline(
            _request(input_arg=_YT_URL, output_dir=tmp_path),
            on_event=lambda e: None,
        )

        mock_generate.assert_not_called()
        # build_html should receive None for chapters
        assert mock_build_html.call_args.kwargs["chapters"] is None

    @patch("podcast_reader.pipeline._wsl_path", return_value=None)
    @patch("podcast_reader.pipeline.build_html", return_value="<html></html>")
    @patch("podcast_reader.pipeline.generate_chapters")
    @patch("podcast_reader.pipeline.fetch_transcript")
    @patch("podcast_reader.pipeline.snippets_to_whisper_segments", return_value=_SAMPLE_SEGMENTS)
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

        run_pipeline(
            _request(input_arg=_YT_URL, output_dir=tmp_path),
            on_event=lambda e: None,
        )

        mock_generate.assert_not_called()
        assert mock_build_html.call_args.kwargs["chapters"] == _SAMPLE_CHAPTERS


class TestChaptersFaultIsolation:
    @patch("podcast_reader.pipeline._wsl_path", return_value=None)
    @patch("podcast_reader.pipeline.build_html", return_value="<html></html>")
    @patch("podcast_reader.pipeline.generate_chapters", side_effect=RuntimeError("provider down"))
    @patch("podcast_reader.pipeline.format_transcript", return_value="[0.0] Hi.")
    @patch("podcast_reader.pipeline.fetch_transcript")
    @patch("podcast_reader.pipeline.snippets_to_whisper_segments", return_value=_SAMPLE_SEGMENTS)
    def test_chapter_failure_still_renders_html(
        self,
        _s: MagicMock,
        _f: MagicMock,
        _fmt: MagicMock,
        _gen: MagicMock,
        mock_build_html: MagicMock,
        _w: MagicMock,
        tmp_path: Path,
    ) -> None:
        events: list[PipelineEvent] = []
        run_pipeline(
            _request(input_arg=_YT_URL, output_dir=tmp_path, chapter_api_key="test-key"),
            on_event=events.append,
        )
        assert (tmp_path / "abc123XYZqq.html").exists()
        assert any(
            e["kind"] == "warning" and e["data"].get("code") == "chapters_failed" for e in events
        )
        assert mock_build_html.call_args.kwargs["chapters"] is None


class TestChaptersKeysAndRedaction:
    """Spec: Key resolution and skip semantics + Key redaction."""

    @patch("podcast_reader.pipeline._wsl_path", return_value=None)
    @patch("podcast_reader.pipeline.build_html", return_value="<html></html>")
    @patch("podcast_reader.pipeline.generate_chapters")
    @patch("podcast_reader.pipeline.fetch_transcript")
    @patch("podcast_reader.pipeline.snippets_to_whisper_segments", return_value=_SAMPLE_SEGMENTS)
    def test_missing_key_skips_with_provider_aware_hint(
        self,
        _s: MagicMock,
        _f: MagicMock,
        mock_generate: MagicMock,
        _b: MagicMock,
        _w: MagicMock,
        tmp_path: Path,
    ) -> None:
        events: list[PipelineEvent] = []
        run_pipeline(
            _request(input_arg=_YT_URL, output_dir=tmp_path, chapter_provider="deepseek"),
            on_event=events.append,
        )
        mock_generate.assert_not_called()
        skips = [
            e
            for e in events
            if e["kind"] == "warning" and e["data"].get("code") == "chapters_skipped"
        ]
        assert len(skips) == 1
        assert "DEEPSEEK_API_KEY" in skips[0]["message"]
        assert "push a key" in skips[0]["message"]

    @patch("podcast_reader.pipeline._wsl_path", return_value=None)
    @patch("podcast_reader.pipeline.build_html", return_value="<html></html>")
    @patch("podcast_reader.pipeline.format_transcript", return_value="[0.0] Hi.")
    @patch("podcast_reader.pipeline.fetch_transcript")
    @patch("podcast_reader.pipeline.snippets_to_whisper_segments", return_value=_SAMPLE_SEGMENTS)
    def test_failure_messages_are_generic_wrapped(
        self,
        _s: MagicMock,
        _f: MagicMock,
        _fmt: MagicMock,
        _b: MagicMock,
        _w: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Per K4: exception text never reaches events — only a generic message."""
        secret = "sk-test-leaky-key-0123456789"
        events: list[PipelineEvent] = []
        with patch(
            "podcast_reader.pipeline.generate_chapters",
            side_effect=RuntimeError(f"401 body said: invalid key {secret}"),
        ):
            run_pipeline(
                _request(input_arg=_YT_URL, output_dir=tmp_path, chapter_api_key=secret),
                on_event=events.append,
            )
        failures = [
            e
            for e in events
            if e["kind"] == "warning" and e["data"].get("code") == "chapters_failed"
        ]
        assert len(failures) == 1
        assert "Chapter generation failed" in failures[0]["message"]
        assert secret not in json.dumps(events)
        assert "401 body said" not in json.dumps(events)

    @patch("podcast_reader.pipeline._wsl_path", return_value=None)
    @patch("podcast_reader.pipeline.build_html", return_value="<html></html>")
    @patch("podcast_reader.pipeline.format_transcript", return_value="[0.0] Hi.")
    @patch("podcast_reader.pipeline.fetch_transcript")
    @patch("podcast_reader.pipeline.snippets_to_whisper_segments", return_value=_SAMPLE_SEGMENTS)
    def test_redaction_sweep_after_mocked_401_echoing_key(
        self,
        _s: MagicMock,
        _f: MagicMock,
        _fmt: MagicMock,
        _b: MagicMock,
        _w: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Spec scenario: a 401 whose body echoes the key leaks neither the full
        key nor its first 12 characters into any emitted event or persisted file."""
        import httpx

        from podcast_reader.chapters import generate_chapters as real_generate_chapters

        key = "sk-test-redaction-0123456789abcdef"

        def echoing_401(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                401, json={"error": {"message": f"Incorrect API key provided: {key}"}}
            )

        def via_mock_transport(transcript_text: str, **kwargs: object) -> object:
            kwargs.pop("transport", None)
            return real_generate_chapters(
                transcript_text,
                transport=httpx.MockTransport(echoing_401),
                **kwargs,  # type: ignore[arg-type]
            )

        events: list[PipelineEvent] = []
        with patch("podcast_reader.pipeline.generate_chapters", side_effect=via_mock_transport):
            run_pipeline(
                _request(input_arg=_YT_URL, output_dir=tmp_path, chapter_api_key=key),
                on_event=events.append,
            )

        assert any(e["data"].get("code") == "chapters_failed" for e in events)
        serialized = json.dumps(events)
        assert key not in serialized
        assert key[:12] not in serialized
        for path in tmp_path.rglob("*"):
            if path.is_file():
                content = path.read_text(errors="replace")
                assert key not in content, f"key leaked into {path}"
                assert key[:12] not in content, f"key fragment leaked into {path}"


class TestChapterErrorDiagnostics:
    """M2: self-authored ChapterError messages reach the warning verbatim;
    everything else keeps the generic class-name wrap (key redaction)."""

    @patch("podcast_reader.pipeline._wsl_path", return_value=None)
    @patch("podcast_reader.pipeline.build_html", return_value="<html></html>")
    @patch("podcast_reader.pipeline.format_transcript", return_value="[0.0] Hi.")
    @patch("podcast_reader.pipeline.fetch_transcript")
    @patch("podcast_reader.pipeline.snippets_to_whisper_segments", return_value=_SAMPLE_SEGMENTS)
    def test_truncation_message_reaches_warning_verbatim(
        self,
        _s: MagicMock,
        _f: MagicMock,
        _fmt: MagicMock,
        _b: MagicMock,
        _w: MagicMock,
        tmp_path: Path,
    ) -> None:
        """A truncated response surfaces the chapters.py diagnostic verbatim,
        not an opaque '(ChapterError)' wrap."""
        import httpx

        from podcast_reader.chapters import generate_chapters as real_generate_chapters

        def truncated_200(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"finish_reason": "length", "message": {"content": '[{"title": "cut'}}
                    ]
                },
            )

        def via_mock_transport(transcript_text: str, **kwargs: object) -> object:
            kwargs.pop("transport", None)
            return real_generate_chapters(
                transcript_text,
                transport=httpx.MockTransport(truncated_200),
                **kwargs,  # type: ignore[arg-type]
            )

        events: list[PipelineEvent] = []
        with patch("podcast_reader.pipeline.generate_chapters", side_effect=via_mock_transport):
            run_pipeline(
                _request(input_arg=_YT_URL, output_dir=tmp_path, chapter_api_key="sk-test"),
                on_event=events.append,
            )
        failures = [
            e
            for e in events
            if e["kind"] == "warning" and e["data"].get("code") == "chapters_failed"
        ]
        assert len(failures) == 1
        assert "Chapter response was truncated" in failures[0]["message"]
        assert "max_tokens cap" in failures[0]["message"]
        assert "ChapterError" not in failures[0]["message"]  # message, not class name

    @patch("podcast_reader.pipeline._wsl_path", return_value=None)
    @patch("podcast_reader.pipeline.build_html", return_value="<html></html>")
    @patch("podcast_reader.pipeline.generate_chapters")
    @patch("podcast_reader.pipeline.fetch_transcript")
    @patch("podcast_reader.pipeline.snippets_to_whisper_segments", return_value=_SAMPLE_SEGMENTS)
    def test_custom_misconfig_message_reaches_warning_verbatim(
        self,
        _s: MagicMock,
        _f: MagicMock,
        mock_generate: MagicMock,
        _b: MagicMock,
        _w: MagicMock,
        tmp_path: Path,
    ) -> None:
        """A missing custom base URL surfaces providers.py's self-authored
        diagnostic, never an opaque '(ValueError)'."""
        events: list[PipelineEvent] = []
        run_pipeline(
            _request(
                input_arg=_YT_URL,
                output_dir=tmp_path,
                chapter_provider="custom",
                chapter_api_key="sk-test",
            ),
            on_event=events.append,
        )
        mock_generate.assert_not_called()
        failures = [
            e
            for e in events
            if e["kind"] == "warning" and e["data"].get("code") == "chapters_failed"
        ]
        assert len(failures) == 1
        assert "custom provider requires a base URL" in failures[0]["message"]
        assert "(ValueError)" not in failures[0]["message"]
        # html still rendered (fault isolation unchanged)
        assert (tmp_path / "abc123XYZqq.html").exists()


class TestEvents:
    @patch("podcast_reader.pipeline._wsl_path", return_value=None)
    @patch("podcast_reader.pipeline.build_html", return_value="<html></html>")
    @patch("podcast_reader.pipeline.fetch_transcript")
    @patch("podcast_reader.pipeline.snippets_to_whisper_segments", return_value=_SAMPLE_SEGMENTS)
    def test_step_events_emitted_in_order(
        self,
        _s: MagicMock,
        _f: MagicMock,
        _b: MagicMock,
        _w: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        events: list[PipelineEvent] = []
        run_pipeline(_request(input_arg=_YT_URL, output_dir=tmp_path), on_event=events.append)
        started = [e["step"] for e in events if e["kind"] == "step_started"]
        assert started[0] == "resolve" and "render" in started
        assert events[-1]["kind"] == "job_done"

    @patch("podcast_reader.pipeline._wsl_path", return_value=None)
    @patch("podcast_reader.pipeline.build_html", return_value="<html></html>")
    @patch("podcast_reader.pipeline.fetch_transcript")
    def test_cached_captions_and_chapters_emit_paired_finished(
        self,
        mock_fetch: MagicMock,
        _b: MagicMock,
        _w: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Cache hits must close their step: every step_started gets a step_finished,
        so SSE consumers tracking step lifecycle never see a step stuck open."""
        (tmp_path / "abc123XYZqq.json").write_text(json.dumps(_SAMPLE_SEGMENTS))
        (tmp_path / "abc123XYZqq_chapters.json").write_text(json.dumps(_SAMPLE_CHAPTERS))
        events: list[PipelineEvent] = []
        run_pipeline(_request(input_arg=_YT_URL, output_dir=tmp_path), on_event=events.append)
        mock_fetch.assert_not_called()
        started = [e["step"] for e in events if e["kind"] == "step_started"]
        finished = [e["step"] for e in events if e["kind"] == "step_finished"]
        assert "captions" in started and "chapters" in started  # the cache-hit steps
        assert sorted(started) == sorted(finished)

    @patch("podcast_reader.pipeline._wsl_path", return_value=None)
    @patch("podcast_reader.pipeline.build_html", return_value="<html></html>")
    @patch("podcast_reader.pipeline.transcribe")
    @patch("podcast_reader.pipeline.download_audio")
    def test_cached_download_and_transcribe_emit_paired_finished(
        self,
        mock_download: MagicMock,
        mock_transcribe: MagicMock,
        _b: MagicMock,
        _w: MagicMock,
        tmp_path: Path,
    ) -> None:
        """The cached download and transcribe paths must also pair their events."""
        url = "https://x.com/user/status/123456"
        (tmp_path / "video_id.mp3").write_text("fake audio")
        (tmp_path / "video_id.ytdlp").write_text(url)
        (tmp_path / "video_id.json").write_text(json.dumps(_SAMPLE_SEGMENTS))
        events: list[PipelineEvent] = []
        run_pipeline(_request(input_arg=url, output_dir=tmp_path), on_event=events.append)
        mock_download.assert_not_called()
        mock_transcribe.assert_not_called()
        started = [e["step"] for e in events if e["kind"] == "step_started"]
        finished = [e["step"] for e in events if e["kind"] == "step_finished"]
        assert "download" in started and "transcribe" in started  # the cache-hit steps
        assert sorted(started) == sorted(finished)


class TestRunPipelineTranscriptSource:
    """Verify the correct transcript source label is passed to build_html."""

    @patch("podcast_reader.pipeline._wsl_path", return_value=None)
    @patch("podcast_reader.pipeline.build_html", return_value="<html></html>")
    @patch("podcast_reader.pipeline.fetch_transcript")
    @patch("podcast_reader.pipeline.snippets_to_whisper_segments", return_value=_SAMPLE_SEGMENTS)
    def test_youtube_source_label(
        self,
        _mock_snippets: MagicMock,
        _mock_fetch: MagicMock,
        mock_build_html: MagicMock,
        _mock_wsl: MagicMock,
        tmp_path: Path,
    ) -> None:
        run_pipeline(
            _request(input_arg=_YT_URL, output_dir=tmp_path),
            on_event=lambda e: None,
        )

        call_kwargs = mock_build_html.call_args[1]
        assert call_kwargs["source"] == "youtube-captions"

    @patch("podcast_reader.pipeline._wsl_path", return_value=None)
    @patch("podcast_reader.pipeline.build_html", return_value="<html></html>")
    @patch("podcast_reader.pipeline.transcribe")
    @patch("podcast_reader.pipeline.download_audio")
    def test_url_source_label(
        self,
        mock_download: MagicMock,
        _mock_transcribe: MagicMock,
        mock_build_html: MagicMock,
        _mock_wsl: MagicMock,
        tmp_path: Path,
    ) -> None:
        audio_path = tmp_path / "video_id.mp3"
        audio_path.write_text("fake audio")
        mock_download.return_value = audio_path

        json_path = tmp_path / "video_id.json"
        json_path.write_text(json.dumps(_SAMPLE_SEGMENTS))

        run_pipeline(
            _request(input_arg="https://x.com/user/status/123456", output_dir=tmp_path),
            on_event=lambda e: None,
        )

        call_kwargs = mock_build_html.call_args[1]
        assert call_kwargs["source"] == "whisper-ctranslate2"


class TestRunPipelineFrozenWorkerPath:
    """Frozen-path wiring (tasks 3.2/3.3): the pipeline's on_event consumer
    receives worker step_progress events, and the step message names the
    engine actually used."""

    def test_step_progress_events_flow_through_on_event(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import subprocess

        from podcast_reader.engine.packs import MANIFEST_FILE, REGISTRY, pack_dir

        data_dir = tmp_path / "data"
        monkeypatch.setenv("PODCAST_READER_DATA_DIR", str(data_dir))
        entry = REGISTRY["model-tiny"]
        target = pack_dir(data_dir, entry)
        target.mkdir(parents=True)
        (target / "model.bin").write_bytes(b"0123456789")
        (target / MANIFEST_FILE).write_text(
            json.dumps(
                {
                    "pack_schema": 1,
                    "id": entry["id"],
                    "version": entry["version"],
                    "component_versions": dict(entry["component_versions"]),
                    "files": [{"path": "model.bin", "sha256": "0" * 64, "size": 10}],
                    "licenses": [],
                }
            )
        )
        audio_path = tmp_path / "episode.mp3"
        audio_path.write_text("fake audio")
        json_path = tmp_path / "episode.json"

        def scripted(
            args: list[str],
            *,
            on_stderr_line: object,
            env: dict[str, str] | None = None,
        ) -> subprocess.CompletedProcess[str]:
            emit_line = on_stderr_line
            assert callable(emit_line)
            emit_line("progress duration=10.00\n")
            emit_line("progress segment_end=4.00\n")
            json_path.write_text(json.dumps(_SAMPLE_SEGMENTS))
            return subprocess.CompletedProcess(args, 0, stdout=str(json_path), stderr="")

        events: list[PipelineEvent] = []
        with (
            patch(
                "podcast_reader.transcribe.resolve_bundled_worker",
                return_value="/bundle/whisper-worker",
            ),
            patch("podcast_reader.transcribe.run_child_streaming", side_effect=scripted),
            patch("podcast_reader.pipeline._wsl_path", return_value=None),
        ):
            request = _request(input_arg=str(audio_path), output_dir=tmp_path)
            request["whisper_model"] = "tiny"
            run_pipeline(request, on_event=events.append)

        progress = [e for e in events if e["kind"] == "step_progress"]
        assert [(e["step"], e["data"]["seconds"]) for e in progress] == [
            ("transcribe", 0.0),
            ("transcribe", 4.0),
        ]
        started = next(
            e for e in events if e["kind"] == "step_started" and e["step"] == "transcribe"
        )
        assert "whisper-worker" in started["message"]


class TestRunPipelineDownloadSelfHeal:
    """Task 4.3: the failure-triggered yt-dlp self-update through the real
    pipeline — the heal happens inside the download step and the warning
    reaches the pipeline's on_event consumer."""

    @patch("podcast_reader.pipeline._wsl_path", return_value=None)
    @patch("podcast_reader.pipeline.build_html", return_value="<html>healed</html>")
    @patch("podcast_reader.pipeline.transcribe")
    def test_extractor_breakage_heals_with_managed_ytdlp(
        self,
        mock_transcribe: MagicMock,
        _mock_build_html: MagicMock,
        _mock_wsl: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import subprocess

        data = tmp_path / "data"
        monkeypatch.setenv("PODCAST_READER_DATA_DIR", str(data))
        binary = data / "tools" / "yt-dlp"
        binary.parent.mkdir(parents=True)
        binary.touch()
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        downloads: list[list[str]] = []

        def fake_download_run(args: list[str]) -> subprocess.CompletedProcess[str]:
            downloads.append(args)
            if len(downloads) == 1:
                return subprocess.CompletedProcess(args, 1, "", "ERROR: unable to extract")
            (out_dir / "123456.mp3").touch()
            return subprocess.CompletedProcess(args, 0, "", "")

        def write_json(**kwargs: object) -> None:
            (out_dir / "123456.json").write_text(json.dumps(_SAMPLE_SEGMENTS))

        mock_transcribe.side_effect = write_json
        events: list[PipelineEvent] = []
        with (
            patch("podcast_reader.ytdlp.resolve_tool", return_value=str(binary)),
            patch("podcast_reader.ytdlp.run_child", side_effect=fake_download_run),
            patch(
                "podcast_reader.engine.managed_tools.run_child",
                return_value=subprocess.CompletedProcess([], 0, "2026.06.06\n", ""),
            ),
        ):
            result = run_pipeline(
                _request(input_arg="https://x.com/user/status/123456", output_dir=out_dir),
                on_event=events.append,
            )

        assert result["html_path"] == str(out_dir / "123456.html")
        assert len(downloads) == 2
        warnings = [e for e in events if e["kind"] == "warning"]
        assert any(e["data"].get("code") == "ytdlp_self_update" for e in warnings)


class TestRunPipelineYtdlpIntegration:
    """End-to-end integration test for the yt-dlp download path.

    Downloads a small public video, mocks only whisper (which needs a GPU)
    and chapter generation. Verifies that yt-dlp download, caching, and
    HTML generation work together.
    """

    @pytest.mark.integration
    @patch("podcast_reader.pipeline._wsl_path", return_value=None)
    @patch("podcast_reader.pipeline.transcribe")
    def test_ytdlp_downloads_and_produces_html(
        self,
        mock_transcribe: MagicMock,
        _mock_wsl: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Download a short public video via yt-dlp, mock whisper, verify HTML output."""
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
            run_pipeline(
                _request(
                    input_arg=url,
                    output_dir=tmp_path,
                    title="Integration Test",
                ),
                on_event=lambda e: None,
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
