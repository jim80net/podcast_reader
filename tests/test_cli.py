"""Tests for podcast_reader.cli module."""

from __future__ import annotations

from podcast_reader.cli import InputType, classify_input


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
