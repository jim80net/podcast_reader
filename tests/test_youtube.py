"""Tests for podcast_reader.youtube module."""

from podcast_reader.youtube import extract_video_id, snippets_to_whisper_segments


class TestExtractVideoId:
    def test_standard_url(self):
        assert extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_short_url(self):
        assert extract_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_url_with_extra_params(self):
        assert extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=120") == "dQw4w9WgXcQ"

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
