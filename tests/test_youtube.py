"""Tests for podcast_reader.youtube module."""

from unittest.mock import MagicMock, patch

import pytest
from youtube_transcript_api import NoTranscriptFound

from podcast_reader.youtube import (
    NoTranscriptError,
    extract_video_id,
    fetch_transcript,
    snippets_to_whisper_segments,
)


class TestExtractVideoId:
    def test_standard_url(self):
        assert extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_short_url(self):
        assert extract_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_url_with_extra_params(self):
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=120"
        assert extract_video_id(url) == "dQw4w9WgXcQ"

    def test_embed_url(self):
        assert extract_video_id("https://www.youtube.com/embed/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_no_www(self):
        assert extract_video_id("https://youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_not_youtube(self):
        assert extract_video_id("https://example.com/podcast.mp3") is None

    def test_plain_file_path(self):
        assert extract_video_id("/home/user/podcast.mp3") is None


class TestSnippetsToWhisperSegments:
    def test_converts_format(self):
        snippets = [
            {"text": "Hello world.", "start": 0.0, "duration": 2.5},
            {"text": "How are you?", "start": 2.5, "duration": 3.0},
        ]
        result = snippets_to_whisper_segments(snippets)
        assert result == {
            "segments": [
                {"start": 0.0, "end": 2.5, "text": "Hello world."},
                {"start": 2.5, "end": 5.5, "text": "How are you?"},
            ]
        }

    def test_empty_input(self):
        assert snippets_to_whisper_segments([]) == {"segments": []}

    def test_strips_whitespace(self):
        snippets = [{"text": "  Hello.  ", "start": 1.0, "duration": 2.0}]
        result = snippets_to_whisper_segments(snippets)
        assert result["segments"][0]["text"] == "Hello."

    def test_skips_empty_text(self):
        snippets = [
            {"text": "Hello.", "start": 0.0, "duration": 1.0},
            {"text": "   ", "start": 1.0, "duration": 1.0},
            {"text": "World.", "start": 2.0, "duration": 1.0},
        ]
        result = snippets_to_whisper_segments(snippets)
        assert len(result["segments"]) == 2


class TestFetchTranscript:
    @patch("podcast_reader.youtube.YouTubeTranscriptApi")
    def test_no_english_transcript_raises_domain_error_not_systemexit(
        self, mock_api: MagicMock
    ) -> None:
        """A missing transcript must raise NoTranscriptError, never SystemExit (D1).

        SystemExit would escape the engine worker's ``except Exception`` and
        kill the only job thread.
        """
        transcript_list = MagicMock()
        transcript_list.find_transcript.side_effect = NoTranscriptFound(
            "abc123XYZqq", ["en"], MagicMock()
        )
        mock_api.return_value.list.return_value = transcript_list

        with pytest.raises(NoTranscriptError, match="abc123XYZqq"):
            fetch_transcript("abc123XYZqq")

    def test_no_transcript_error_is_a_plain_exception(self) -> None:
        assert issubclass(NoTranscriptError, Exception)
        assert not issubclass(NoTranscriptError, SystemExit)
