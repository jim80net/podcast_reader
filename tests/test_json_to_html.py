"""Tests for json_to_html.py paragraph grouping."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from json_to_html import segments_to_paragraphs, _count_sentences


class TestCountSentences:
    def test_counts_periods(self):
        assert _count_sentences("Hello. World.") == 2

    def test_counts_mixed_punctuation(self):
        assert _count_sentences("Really? Yes! Done.") == 3

    def test_no_punctuation(self):
        assert _count_sentences("hello world") == 0


class TestSegmentsToParapraphs:
    def test_does_not_break_mid_sentence_when_segment_has_period_then_words(self):
        """Bug: A segment like 'civil. How many things' gets APPENDED to the
        current paragraph, pushing the sentence count over the threshold.
        The next segment triggers the break, saving a paragraph ending with
        'How many things' — mid-sentence.

        The fix: when threshold is reached, only break at a sentence boundary."""
        segments = [
            {"start": 0.0, "end": 3.0, "text": "First sentence."},
            {"start": 2.0, "end": 5.0, "text": "Second one."},
            # After these 2 segments: current has 2 sentence-enders.
            # Next segment has a period mid-text — appending it crosses threshold:
            {"start": 4.0, "end": 8.0, "text": "Very civil. How many things"},
            # current is now "First sentence. Second one. Very civil. How many things"
            # which has 3 sentence-enders (>= 3 threshold). But text ends with "things".
            {"start": 7.0, "end": 10.0, "text": "is that?"},
            # ^^ Check fires: current has 3 >= 3 → BREAK
            # Para 1 saved as: "...Very civil. How many things" — ends mid-sentence!
            {"start": 9.0, "end": 12.0, "text": "Good question."},
        ]
        paras = segments_to_paragraphs(segments, sentences_per_para=3)

        # No paragraph (except the last) should end mid-sentence
        for i, p in enumerate(paras[:-1]):
            last_char = p["text"].rstrip()[-1]
            assert last_char in ".!?", (
                f"Paragraph {i+1} ends mid-sentence: ...{p['text'][-40:]!r}"
            )

    def test_youtube_style_overlapping_segments(self):
        """Real YouTube caption pattern: segment 'sort of 5:00. And the fact'
        gets appended, pushing count past threshold. Next segment triggers
        break, saving paragraph ending with 'And the fact'."""
        segments = [
            {"start": 0.0, "end": 4.0, "text": "I love that it's quiet."},
            {"start": 2.0, "end": 6.0, "text": "And it's very refined"},
            {"start": 4.0, "end": 8.0, "text": "because it's electric."},
            # current: "I love that it's quiet. And it's very refined because it's electric."
            # 2 sentence-enders so far. Not yet at threshold of 3.
            {"start": 6.0, "end": 10.0, "text": "It's polite. I leave early,"},
            # current appended: "...electric. It's polite. I leave early,"
            # 3 sentence-enders now (quiet. electric. polite.) — threshold reached!
            # But text ends with "I leave early," — mid-sentence.
            {"start": 9.0, "end": 13.0, "text": "sort of 5:00."},
            # ^^ Check fires: 3 >= 3 → BREAK. Para ends with "I leave early,"
            {"start": 12.0, "end": 16.0, "text": "That is good."},
        ]
        paras = segments_to_paragraphs(segments, sentences_per_para=3)

        for i, p in enumerate(paras[:-1]):
            last_char = p["text"].rstrip()[-1]
            assert last_char in ".!?", (
                f"Paragraph {i+1} ends mid-sentence: ...{p['text'][-40:]!r}"
            )

    def test_basic_grouping_still_works(self):
        """Segments with clean sentence boundaries still group correctly."""
        segments = [
            {"start": 0.0, "end": 3.0, "text": "First sentence."},
            {"start": 3.0, "end": 6.0, "text": "Second sentence."},
            {"start": 6.0, "end": 9.0, "text": "Third sentence."},
            {"start": 9.0, "end": 12.0, "text": "Fourth sentence."},
            {"start": 12.0, "end": 15.0, "text": "Fifth sentence."},
            {"start": 15.0, "end": 18.0, "text": "Sixth sentence."},
        ]
        paras = segments_to_paragraphs(segments, sentences_per_para=3)
        assert len(paras) == 2
        assert paras[0]["text"].endswith("Third sentence.")
        assert paras[1]["text"].endswith("Sixth sentence.")

    def test_safety_valve_prevents_infinite_paragraphs(self):
        """If no sentence-ending punctuation exists, paragraphs still break
        when the character count exceeds the safety limit (800 chars)."""
        # Each segment ~50 chars, 30 segments = ~1500 chars total
        segments = [
            {"start": float(i * 3), "end": float(i * 3 + 3),
             "text": f"and then we talked about topic number {i} for a while"}
            for i in range(30)
        ]
        paras = segments_to_paragraphs(segments, sentences_per_para=3)
        # Should not produce a single massive paragraph
        assert len(paras) > 1
