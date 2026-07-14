"""Tests for podcast_reader.html paragraph grouping."""

import json
from pathlib import Path

from podcast_reader.html import _count_sentences, segments_to_paragraphs

FIXTURES = Path(__file__).parent / "fixtures"


class TestCountSentences:
    def test_counts_periods(self) -> None:
        assert _count_sentences("Hello. World.") == 2

    def test_counts_mixed_punctuation(self) -> None:
        assert _count_sentences("Really? Yes! Done.") == 3

    def test_no_punctuation(self) -> None:
        assert _count_sentences("hello world") == 0


class TestSegmentsToParapraphs:
    def test_does_not_break_mid_sentence_when_segment_has_period_then_words(self) -> None:
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
                f"Paragraph {i + 1} ends mid-sentence: ...{p['text'][-40:]!r}"
            )

    def test_youtube_style_overlapping_segments(self) -> None:
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
                f"Paragraph {i + 1} ends mid-sentence: ...{p['text'][-40:]!r}"
            )

    def test_basic_grouping_still_works(self) -> None:
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

    def test_safety_valve_prevents_infinite_paragraphs(self) -> None:
        """If no sentence-ending punctuation exists, paragraphs still break
        when the character count exceeds the safety limit (800 chars)."""
        # Each segment ~50 chars, 30 segments = ~1500 chars total
        segments = [
            {
                "start": float(i * 3),
                "end": float(i * 3 + 3),
                "text": f"and then we talked about topic number {i} for a while",
            }
            for i in range(30)
        ]
        paras = segments_to_paragraphs(segments, sentences_per_para=3)
        # Should not produce a single massive paragraph
        assert len(paras) > 1


class TestSpeakerParagraphs:
    """Speaker-aware paragraph grouping (diarization-worker spec)."""

    def test_breaks_paragraph_at_speaker_change(self) -> None:
        segments = [
            {"start": 0.0, "end": 2.0, "text": "Hello there", "speaker": "SPEAKER_00"},
            {"start": 2.0, "end": 4.0, "text": "and welcome.", "speaker": "SPEAKER_00"},
            {"start": 4.0, "end": 6.0, "text": "Thanks for having me.", "speaker": "SPEAKER_01"},
        ]
        paras = segments_to_paragraphs(segments, sentences_per_para=5)
        assert len(paras) == 2
        assert paras[0]["speaker"] == "SPEAKER_00"
        assert paras[0]["text"] == "Hello there and welcome."
        assert paras[1]["speaker"] == "SPEAKER_01"

    def test_speakerless_segments_produce_no_speaker_keys(self) -> None:
        """The field is optional end to end: no speakers in, no keys out."""
        segments = [
            {"start": 0.0, "end": 2.0, "text": "Hello there."},
            {"start": 2.0, "end": 4.0, "text": "More text."},
        ]
        paras = segments_to_paragraphs(segments, sentences_per_para=5)
        assert all("speaker" not in p for p in paras)

    def test_partial_speakers_break_against_unlabeled_segments(self) -> None:
        """A labeled→unlabeled transition is a speaker change too."""
        segments = [
            {"start": 0.0, "end": 2.0, "text": "Labeled.", "speaker": "SPEAKER_00"},
            {"start": 2.0, "end": 4.0, "text": "Unlabeled."},
        ]
        paras = segments_to_paragraphs(segments, sentences_per_para=5)
        assert len(paras) == 2
        assert paras[0]["speaker"] == "SPEAKER_00"
        assert "speaker" not in paras[1]

    def test_carry_after_split_stays_with_its_speaker(self) -> None:
        """A sentence-boundary split's leftover fragment must never leak into
        the next speaker's paragraph (OCR review on PR #11)."""
        segments = [
            {"start": 0.0, "end": 2.0, "text": "First sentence.", "speaker": "SPEAKER_00"},
            {
                "start": 2.0,
                "end": 4.0,
                "text": "Second sentence. And a trailing fragment",
                "speaker": "SPEAKER_00",
            },
            {"start": 4.0, "end": 6.0, "text": "Hello from the guest.", "speaker": "SPEAKER_01"},
        ]
        paras = segments_to_paragraphs(segments, sentences_per_para=2)
        assert len(paras) == 2
        assert paras[0]["speaker"] == "SPEAKER_00"
        assert paras[0]["text"] == "First sentence. Second sentence. And a trailing fragment"
        assert paras[1]["speaker"] == "SPEAKER_01"
        assert paras[1]["text"] == "Hello from the guest."

    def test_carry_after_split_still_prepends_for_same_speaker(self) -> None:
        """Same-speaker continuation keeps the original carry behavior."""
        segments = [
            {"start": 0.0, "end": 2.0, "text": "First sentence.", "speaker": "SPEAKER_00"},
            {
                "start": 2.0,
                "end": 4.0,
                "text": "Second sentence. And a trailing fragment",
                "speaker": "SPEAKER_00",
            },
            {"start": 4.0, "end": 6.0, "text": "that continues here.", "speaker": "SPEAKER_00"},
        ]
        paras = segments_to_paragraphs(segments, sentences_per_para=2)
        assert len(paras) == 2
        assert paras[0]["text"] == "First sentence. Second sentence."
        assert paras[1]["text"] == "And a trailing fragment that continues here."
        assert paras[1]["speaker"] == "SPEAKER_00"


class TestSpeakerRendering:
    _SEGMENTS = [
        {"start": 0.0, "end": 2.0, "text": "Hello and welcome.", "speaker": "SPEAKER_00"},
        {"start": 2.0, "end": 4.0, "text": "Glad to be here.", "speaker": "SPEAKER_01"},
        {"start": 4.0, "end": 6.0, "text": "Continuing that thought.", "speaker": "SPEAKER_01"},
        {"start": 6.0, "end": 8.0, "text": "Back to you.", "speaker": "SPEAKER_00"},
    ]

    def test_speaker_labels_visible_at_changes(self) -> None:
        """Spec scenario: rendered transcript displays attribution at
        speaker changes."""
        from podcast_reader.html import build_html

        result = build_html(list(self._SEGMENTS), title="T", sentences_per_para=1)

        assert '<span class="speaker">Speaker 1</span>' in result
        assert '<span class="speaker">Speaker 2</span>' in result
        # consecutive same-speaker paragraphs are labeled once: SPEAKER_01
        # speaks two paragraphs but gets one label
        assert result.count('<span class="speaker">Speaker 2</span>') == 1

    def test_speaker_css_present_only_with_speakers(self) -> None:
        from podcast_reader.html import build_html

        with_speakers = build_html(list(self._SEGMENTS), title="T", sentences_per_para=1)
        without = build_html(
            [{k: v for k, v in s.items() if k != "speaker"} for s in self._SEGMENTS],
            title="T",
            sentences_per_para=1,
        )
        assert ".speaker {" in with_speakers
        assert ".speaker {" not in without
        assert "speaker" not in without

    def test_chapters_path_renders_speaker_labels(self) -> None:
        from podcast_reader.html import build_html

        chapters = [
            {
                "title": "Only Chapter",
                "start": 0.0,
                "end": 8.0,
                "abstract": "All of it.",
                "type": "content",
                "key_points": [],
            }
        ]
        result = build_html(
            list(self._SEGMENTS), title="T", chapters=chapters, sentences_per_para=1
        )
        assert '<span class="speaker">Speaker 1</span>' in result
        assert '<span class="speaker">Speaker 2</span>' in result


class TestSyncMetadata:
    """media-playback / job-pipeline: the artifact carries playback-sync
    metadata (data-start on passages) and an inert-when-standalone sync script."""

    # sentences_per_para=1 splits at the last sentence boundary after the
    # threshold is met, so two one-sentence segments collapse into one passage;
    # three segments yield two passages (starts 0.0 and 4.0).
    _SEGS = [
        {"start": 0.0, "end": 2.0, "text": "First passage here."},
        {"start": 2.0, "end": 4.0, "text": "Still the first passage."},
        {"start": 4.0, "end": 6.0, "text": "Now the second passage."},
    ]

    def test_passages_carry_data_start(self) -> None:
        from podcast_reader.html import build_html

        html = build_html(list(self._SEGS), title="T", sentences_per_para=1, source="test")
        # Two passages with distinct, gap-free starts.
        assert 'data-start="0.000"' in html
        assert 'data-start="4.000"' in html
        # data-end is emitted too.
        assert "data-end=" in html

    def test_sync_script_present_and_inert_standalone(self) -> None:
        from podcast_reader.html import build_html

        html = build_html(list(self._SEGS), title="T", sentences_per_para=1, source="test")
        # No-op when opened standalone (no parent player).
        assert "window.parent === window" in html
        # Channel-tagged protocol both sides agree on.
        assert "pr-sync" in html

    def test_sync_script_present_without_chapters(self) -> None:
        from podcast_reader.html import build_html

        html = build_html(list(self._SEGS), title="T", sentences_per_para=1, source="test")
        # Sync must work even when there are no chapters (unlike the sidebar
        # scroll script, which is chapter-gated).
        assert "pr-sync" in html

    def test_chapter_sections_carry_data_start(self) -> None:
        from podcast_reader.html import build_html

        chapters = [
            {
                "title": "Only Chapter",
                "start": 0.0,
                "end": 6.0,
                "abstract": "All of it.",
                "type": "content",
                "key_points": [],
            }
        ]
        html = build_html(
            list(self._SEGS), title="T", chapters=chapters, sentences_per_para=1, source="test"
        )
        # The <section> carries a machine-readable start for chapter-level seeking.
        assert 'data-start="0.000"' in html


class TestKeylessTimeline:
    def test_no_chapters_gets_coarse_timestamp_landmarks_and_upsell(self) -> None:
        from podcast_reader.html import build_html

        segments = [
            {"start": 0.0, "end": 10.0, "text": "Opening one."},
            {"start": 10.0, "end": 20.0, "text": "Opening two."},
            {"start": 305.0, "end": 315.0, "text": "Five minutes one."},
            {"start": 315.0, "end": 325.0, "text": "Five minutes two."},
            {"start": 605.0, "end": 615.0, "text": "Ten minutes one."},
            {"start": 615.0, "end": 625.0, "text": "Ten minutes two."},
            {"start": 905.0, "end": 915.0, "text": "Fifteen minutes one."},
            {"start": 915.0, "end": 925.0, "text": "Fifteen minutes two."},
        ]

        html = build_html(segments, title="T", sentences_per_para=1, source="test")

        assert 'aria-label="Transcript timeline"' in html
        assert 'href="#t-0">00:00:00</a>' in html
        assert 'href="#t-305000">00:05:05</a>' in html
        assert 'href="#t-605000">00:10:05</a>' in html
        assert 'href="#t-905000">00:15:05</a>' in html
        assert 'id="t-305000" data-start="305.000"' in html
        assert "Chapters, key points, and pull quotes are available" in html
        assert "Settings &rarr; AI model in the app" in html

    def test_chaptered_artifact_omits_keyless_navigation_and_upsell(self) -> None:
        from podcast_reader.html import build_html

        chapters = [
            {
                "title": "Opening",
                "start": 0.0,
                "end": 20.0,
                "abstract": "The opening.",
                "type": "content",
                "key_points": [],
            }
        ]
        html = build_html(
            [{"start": 0.0, "end": 20.0, "text": "Opening."}],
            title="T",
            chapters=chapters,
            source="test",
        )

        assert 'aria-label="Transcript timeline"' not in html
        assert "Chapters, key points, and pull quotes are available" not in html


class TestBuildHtmlIntegration:
    def test_os_light_theme_does_not_override_explicit_dark_theme(self) -> None:
        from podcast_reader.html import build_html

        result = build_html([], title="T", source="test")

        assert ":root[data-theme='dark'] {" in result
        assert ":root:not([data-theme='dark']) {" in result

    def test_speakerless_output_byte_identical_no_chapters(self) -> None:
        """Spec scenario: speakerless transcripts unchanged — golden file
        generated before speaker rendering existed."""
        from podcast_reader.html import build_html

        whisper_data = json.loads((FIXTURES / "sample_whisper.json").read_text())
        segments = [s for s in whisper_data["segments"] if s.get("text", "").strip()]

        result = build_html(segments, title="Test Episode", sentences_per_para=5, source="test")

        expected = (FIXTURES / "sample_expected_nochapters.html").read_text()
        assert result == expected

    def test_full_pipeline_with_chapters(self) -> None:
        """Integration test: whisper JSON + chapters JSON -> HTML matches expected output."""
        from podcast_reader.html import build_html

        whisper_data = json.loads((FIXTURES / "sample_whisper.json").read_text())
        chapters = json.loads((FIXTURES / "sample_chapters.json").read_text())
        segments = [s for s in whisper_data["segments"] if s.get("text", "").strip()]

        result = build_html(
            segments,
            title="Test Episode",
            chapters=chapters,
            sentences_per_para=5,
            source="test",
        )

        expected_path = FIXTURES / "sample_expected.html"
        if not expected_path.exists():
            # First run: generate the expected output
            expected_path.write_text(result)
            raise AssertionError(
                f"Expected HTML fixture did not exist."
                f" Generated it at {expected_path}."
                " Review and re-run."
            )

        expected = expected_path.read_text()
        assert result == expected
