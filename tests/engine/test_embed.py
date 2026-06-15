"""Tests for the engine-hosted YouTube embed page (podcast_reader.engine.embed)."""

from __future__ import annotations

from podcast_reader.engine.embed import (
    EMBED_COMMAND_SOURCE,
    EMBED_EVENT_SOURCE,
    build_embed_page,
    is_valid_video_id,
)


class TestVideoIdValidation:
    def test_accepts_typical_youtube_ids(self) -> None:
        assert is_valid_video_id("dQw4w9WgXcQ")
        assert is_valid_video_id("a_b-c123")

    def test_rejects_empty_traversal_and_injection(self) -> None:
        assert not is_valid_video_id("")
        assert not is_valid_video_id("../../etc/passwd")
        assert not is_valid_video_id("a/b")
        assert not is_valid_video_id('"><script>')
        assert not is_valid_video_id("a" * 33)  # over the length cap


class TestBuildEmbedPage:
    def test_contains_the_video_id_and_nocookie_host(self) -> None:
        page = build_embed_page("dQw4w9WgXcQ")
        assert '"dQw4w9WgXcQ"' in page
        assert "youtube-nocookie.com" in page
        assert "iframe_api" in page

    def test_pins_the_protocol_source_tags_shared_with_the_app(self) -> None:
        # The literals must match app/src/renderer/src/embed-protocol.ts; this
        # locks the Python side of that contract.
        page = build_embed_page("dQw4w9WgXcQ")
        assert EMBED_EVENT_SOURCE == "pr-embed"
        assert EMBED_COMMAND_SOURCE == "pr-embed-cmd"
        assert '"pr-embed"' in page
        assert '"pr-embed-cmd"' in page

    def test_sets_origin_to_the_page_origin_for_the_153_152_fix(self) -> None:
        # origin: location.origin is what makes YouTube accept the loopback host.
        assert "origin: location.origin" in build_embed_page("dQw4w9WgXcQ")

    def test_escapes_a_crafted_id_as_defense_in_depth(self) -> None:
        # Even though the route validates first, the id is HTML/quote-escaped so
        # a stray quote can't break out of the JS string literal.
        page = build_embed_page('a"b')
        assert 'a"b' not in page
        assert "&quot;" in page
