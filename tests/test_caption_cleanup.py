"""Tests for fail-closed caption spelling/casing cleanup."""

from podcast_reader.caption_cleanup import apply_caption_corrections


def test_applies_single_token_spelling_and_casing_corrections() -> None:
    segments = [{"start": 12.5, "end": 20.0, "text": "Beleiveing in iphone was hard."}]
    cleaned, count = apply_caption_corrections(
        segments,
        [
            {"segment_start": 12.5, "original": "Beleiveing", "replacement": "Believing"},
            {"segment_start": 12.5, "original": "iphone", "replacement": "iPhone"},
        ],
    )

    assert cleaned[0]["text"] == "Believing in iPhone was hard."
    assert count == 2
    assert segments[0]["text"] == "Beleiveing in iphone was hard."


def test_rejects_rewording_punctuation_short_edits_and_ambiguous_tokens() -> None:
    segments = [{"start": 0.0, "end": 4.0, "text": "bad bad recieve."}]
    cleaned, count = apply_caption_corrections(
        segments,
        [
            {"segment_start": 0.0, "original": "bad", "replacement": "sad"},
            {"segment_start": 0.0, "original": "recieve", "replacement": "receive it"},
            {"segment_start": 0.0, "original": "recieve", "replacement": "receive!"},
            {"segment_start": 0.0, "original": "bad", "replacement": "Bad"},
        ],
    )

    assert cleaned == segments
    assert count == 0


def test_ignores_unknown_timestamps_large_edits_and_valid_word_substitutions() -> None:
    segments = [{"start": 1.0, "end": 2.0, "text": "colour form remains."}]
    cleaned, count = apply_caption_corrections(
        segments,
        [
            {"segment_start": 9.0, "original": "colour", "replacement": "color"},
            {"segment_start": 1.0, "original": "colour", "replacement": "meaning"},
            {"segment_start": 1.0, "original": "form", "replacement": "from"},
        ],
    )

    assert cleaned == segments
    assert count == 0
