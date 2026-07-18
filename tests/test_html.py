"""Tests for podcast_reader.html paragraph grouping."""

import json
import re
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


class TestTranscriptSearch:
    _SEGS = [
        {"start": 0.0, "end": 2.0, "text": "First searchable passage."},
        {"start": 2.0, "end": 4.0, "text": "두 번째 회복탄력성 구절."},
    ]

    def test_emits_private_accessible_search_controls_and_script(self) -> None:
        from podcast_reader.html import _SEARCH_SCRIPT, build_html

        html = build_html(list(self._SEGS), title="T", sentences_per_para=1, source="test")

        assert 'role="search"' in html
        assert 'aria-keyshortcuts="/"' in html
        assert 'aria-controls="transcript-search-panel"' in html
        assert 'type="search"' in html
        assert "&uarr;" in html and "&darr;" in html
        assert "↑" not in html and "↓" not in html
        assert 'autocomplete="off"' in html
        assert 'spellcheck="false"' in html
        assert 'autocorrect="off"' in html
        assert 'autocapitalize="none"' in html
        assert 'name="' not in html.split('id="transcript-search-input"', 1)[1].split(">", 1)[0]
        assert f"<script>\n{_SEARCH_SCRIPT}</script>" in html

    def test_search_precedes_main_and_uses_reviewed_motion_and_state_tokens(self) -> None:
        from podcast_reader.html import build_html

        html = build_html(list(self._SEGS), title="T", sentences_per_para=1, source="test")

        assert html.index('class="transcript-search"') < html.index("<main>")
        assert "html { scroll-behavior: auto; }" in html
        assert ".search-match-active.sync-active" in html
        assert "--transcript-search-height" in html


class TestTranscriptExport:
    def test_emits_accessible_default_private_export_controls_and_script(self) -> None:
        from podcast_reader.html import _EXPORT_SCRIPT, build_html

        html = build_html(
            [{"start": 0.0, "end": 2.0, "text": "그대로 보존합니다."}],
            title="한국어",
            sentences_per_para=1,
            source="test",
        )

        assert 'aria-controls="transcript-export-panel"' in html
        assert '<option value="text">Plain text</option>' in html
        assert '<option value="all">Whole transcript</option>' in html
        assert 'class="transcript-export-include-timestamps"' in html
        assert 'class="transcript-export-include-timestamps" checked' not in html
        assert 'aria-label="Transcript export text"' in html
        assert f"<script>\n{_EXPORT_SCRIPT}</script>" in html
        for network_primitive in ("fetch(", "XMLHttpRequest", "WebSocket", "sendBeacon"):
            assert network_primitive not in _EXPORT_SCRIPT


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
        for anchor, ts in [
            ("t-0", "00:00:00"),
            ("t-305000", "00:05:05"),
            ("t-605000", "00:10:05"),
            ("t-905000", "00:15:05"),
        ]:
            assert f'<a href="#{anchor}"><span class="timeline-ts">{ts}</span>' in html
        # Content-aware labels: the marker's opening words, not bare clock time.
        assert '<span class="timeline-snippet">Five minutes one. Five minutes two.</span>' in html
        # Stop #1 is labeled "Start", never its (often boilerplate) opening words.
        assert (
            '<span class="timeline-ts">00:00:00</span>'
            '<span class="timeline-snippet">Start</span>' in html
        )
        assert '<span class="timeline-snippet">Opening one.' not in html
        # Stops wrap instead of scrolling out of view behind a clipped edge (#57).
        links_rule = html.split(".timeline-links {", 1)[1].split("}", 1)[0]
        assert "flex-wrap: wrap" in links_rule
        assert "overflow-x" not in links_rule
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


class TestByline:
    def test_reports_duration_words_and_reading_time(self) -> None:
        from podcast_reader.html import _byline

        # 90 x 10s segments = 15 min; 25 words each = 2,250 words.
        segments = [
            {"start": float(i * 10), "end": float(i * 10 + 10), "text": "word " * 25}
            for i in range(90)
        ]
        line = _byline(segments, "youtube-captions")
        assert line == (
            "15 min of audio &middot; ~2,200 words &middot; "
            "about 11 min to read &middot; Auto-transcribed with youtube-captions"
        )

    def test_hour_plus_durations_use_hr_min(self) -> None:
        from podcast_reader.html import _byline

        line = _byline([{"start": 0.0, "end": 4500.0, "text": "hi"}], "test")
        assert line.startswith("1 hr 15 min of audio")
        exact_hour = _byline([{"start": 0.0, "end": 3600.0, "text": "hi"}], "test")
        assert exact_hour.startswith("1 hr of audio &middot;")

    def test_small_word_counts_are_exact_not_rounded(self) -> None:
        from podcast_reader.html import _byline

        line = _byline([{"start": 0.0, "end": 30.0, "text": "one two three four"}], "test")
        assert "4 words" in line
        assert "~" not in line
        assert "about 1 min to read" in line

    def test_empty_segments_keep_provenance_only(self) -> None:
        from podcast_reader.html import _byline

        assert _byline([], "test") == "Auto-transcribed with test"

    def test_byline_renders_in_meta_slot_on_both_paths(self) -> None:
        from podcast_reader.html import build_html

        segs = [{"start": 0.0, "end": 120.0, "text": "hello there general kenobi"}]
        keyless = build_html(list(segs), title="T", source="test")
        assert '<div class="meta">2 min of audio &middot; 4 words' in keyless
        chaptered = build_html(
            list(segs),
            title="T",
            chapters=[
                {
                    "title": "Only",
                    "start": 0.0,
                    "end": 120.0,
                    "abstract": "All.",
                    "type": "content",
                    "key_points": [],
                }
            ],
            source="test",
        )
        assert '<div class="meta">2 min of audio &middot; 4 words' in chaptered


class TestKeylessLandmarks:
    _SEGMENTS = [
        {"start": 0.0, "end": 10.0, "text": "Opening one."},
        {"start": 10.0, "end": 20.0, "text": "Opening two."},
        {"start": 305.0, "end": 315.0, "text": "Five minutes one."},
        {"start": 315.0, "end": 325.0, "text": "Five minutes two."},
        {"start": 605.0, "end": 615.0, "text": "Ten minutes one."},
        {"start": 615.0, "end": 625.0, "text": "Ten minutes two."},
        {"start": 905.0, "end": 915.0, "text": "Fifteen minutes one."},
        {"start": 915.0, "end": 925.0, "text": "Fifteen minutes two."},
    ]

    def test_landmarks_at_rail_marker_boundaries_except_first(self) -> None:
        from podcast_reader.html import build_html

        html = build_html(list(self._SEGMENTS), title="T", sentences_per_para=1, source="test")
        for ts in ("00:05:05", "00:10:05", "00:15:05"):
            assert f'<div class="landmark"><span class="landmark-ts">{ts}</span>' in html
        # No landmark under the masthead for the first stop.
        assert '<span class="landmark-ts">00:00:00</span>' not in html
        assert html.count('class="landmark"') == 3

    def test_landmark_carries_the_rail_label(self) -> None:
        """Issue #64: sections have names, not a bare timestamp duplicate."""
        from podcast_reader.html import build_html

        html = build_html(list(self._SEGMENTS), title="T", sentences_per_para=1, source="test")
        assert (
            '<span class="landmark-ts">00:05:05</span>'
            '<span class="landmark-label">Five minutes one. Five minutes two.</span>' in html
        )

    def test_landmark_precedes_its_marker_paragraph(self) -> None:
        from podcast_reader.html import build_html

        html = build_html(list(self._SEGMENTS), title="T", sentences_per_para=1, source="test")
        landmark = '<div class="landmark"><span class="landmark-ts">00:05:05</span>'
        assert html.index(landmark) < html.index('id="t-305000"')

    def test_chaptered_artifact_has_no_landmarks(self) -> None:
        from podcast_reader.html import build_html

        html = build_html(
            list(self._SEGMENTS),
            title="T",
            chapters=[
                {
                    "title": "All",
                    "start": 0.0,
                    "end": 925.0,
                    "abstract": "Everything.",
                    "type": "content",
                    "key_points": [],
                }
            ],
            sentences_per_para=1,
            source="test",
        )
        assert 'class="landmark"' not in html


class TestTimelineInterval:
    """Issue #64: tiers one notch denser — ~2-3 min stops for short talks."""

    def test_interval_tiers(self) -> None:
        from podcast_reader.html import _timeline_interval

        assert _timeline_interval(10 * 60) == 2 * 60
        assert _timeline_interval(15 * 60) == 3 * 60
        assert _timeline_interval(30 * 60) == 3 * 60
        assert _timeline_interval(45 * 60) == 5 * 60
        assert _timeline_interval(45 * 60 + 0.001) == 10 * 60
        assert _timeline_interval(59 * 60 + 56) == 10 * 60
        assert _timeline_interval(2 * 60 * 60) == 10 * 60

    def test_near_hour_transcript_emits_six_markers(self) -> None:
        from podcast_reader.html import _timeline_markers

        paragraphs = [
            {
                "start": float(minute * 60),
                "end": float(59 * 60 + 56 if minute == 59 else (minute + 1) * 60),
                "text": f"Minute {minute} introduces a distinct idea for the reader.",
            }
            for minute in range(60)
        ]

        markers = _timeline_markers(paragraphs)

        assert [marker["start"] for marker in markers] == [
            0.0,
            600.0,
            1200.0,
            1800.0,
            2400.0,
            3000.0,
        ]


class TestSectionBadgeContrast:
    @staticmethod
    def _luminance(hex_color: str) -> float:
        channels = [int(hex_color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [
            channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
            for channel in channels
        ]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    @classmethod
    def _contrast(cls, first: str, second: str) -> float:
        brighter, darker = sorted((cls._luminance(first), cls._luminance(second)), reverse=True)
        return (brighter + 0.05) / (darker + 0.05)

    @staticmethod
    def _theme_tokens(scope: str) -> tuple[str, str]:
        from podcast_reader.html import _STYLESHEET

        match = re.search(rf"{re.escape(scope)}\s*\{{(?P<body>.*?)\n\}}", _STYLESHEET, re.DOTALL)
        assert match is not None, f"missing theme scope: {scope}"
        body = match.group("body")
        background = re.search(r"--section-badge-bg:\s*(#[0-9a-fA-F]{6})", body)
        foreground = re.search(r"--section-badge-text:\s*(#[0-9a-fA-F]{6})", body)
        assert background is not None and foreground is not None
        return background.group(1).lower(), foreground.group(1).lower()

    def test_all_theme_paths_assign_an_aa_pair(self) -> None:
        dark = ("#31465a", "#f2f6f8")
        light = ("#d8e6ed", "#29495a")
        assignments = {
            ":root": dark,
            ":root[data-theme='dark']": dark,
            ":root[data-theme='light']": light,
            ":root:not([data-theme='dark'])": light,
        }

        for scope, expected in assignments.items():
            actual = self._theme_tokens(scope)
            assert actual == expected
            assert self._contrast(*actual) >= 4.5

    def test_sidebar_and_heading_badges_share_the_theme_tokens(self) -> None:
        from podcast_reader.html import _STYLESHEET

        for selectors in (
            ".nav-badge-intro, .nav-badge-outro",
            ".badge-intro, .badge-outro",
        ):
            match = re.search(
                rf"{re.escape(selectors)}\s*\{{(?P<body>.*?)\}}", _STYLESHEET, re.DOTALL
            )
            assert match is not None
            assert "background: var(--section-badge-bg)" in match.group("body")
            assert "color: var(--section-badge-text)" in match.group("body")


class TestSearchContrast:
    @staticmethod
    def _blend(background: str, foreground: str, alpha: float) -> str:
        channels = []
        for index in (1, 3, 5):
            bg = int(background[index : index + 2], 16)
            fg = int(foreground[index : index + 2], 16)
            channels.append(round(bg * (1 - alpha) + fg * alpha))
        return "#" + "".join(f"{channel:02x}" for channel in channels)

    def test_light_and_dark_search_states_pin_text_and_edge_contrast(self) -> None:
        from podcast_reader.html import _STYLESHEET

        contrast = TestSectionBadgeContrast._contrast
        for scope in (":root", ":root[data-theme='light']"):
            match = re.search(
                rf"{re.escape(scope)}\s*\{{(?P<body>.*?)\n\}}", _STYLESHEET, re.DOTALL
            )
            assert match is not None
            body = match.group("body")

            def color(name: str, source: str) -> str:
                token = re.search(rf"--{name}:\s*(#[0-9a-fA-F]{{6}})", source)
                assert token is not None
                return token.group(1)

            glow = re.search(r"--accent-glow:\s*rgba\([^,]+,[^,]+,[^,]+,\s*([0-9.]+)\)", body)
            assert glow is not None
            background = color("bg", body)
            match_bg = self._blend(background, color("accent", body), float(glow.group(1)))
            assert contrast(color("text", body), match_bg) >= 4.5
            assert contrast(color("accent", body), background) >= 3.0
            assert contrast(color("search-edge", body), match_bg) >= 3.0
            assert contrast(color("link", body), background) >= 3.0


class TestTimelineLabel:
    def test_short_text_is_kept_whole_without_ellipsis(self) -> None:
        from podcast_reader.html import _timeline_label

        assert _timeline_label("I dropped out of Reed.") == "I dropped out of Reed."

    def test_clause_boundary_cut_drops_the_comma_and_needs_no_ellipsis(self) -> None:
        from podcast_reader.html import _timeline_label

        label = _timeline_label(
            "The second story is about love and loss, which changed everything for me"
        )
        assert label == "The second story is about love and loss"

    def test_sentence_boundary_cut_keeps_the_period(self) -> None:
        from podcast_reader.html import _timeline_label

        label = _timeline_label(
            "I dropped out of Reed College. The rest of this sentence runs well past the budget."
        )
        assert label == "I dropped out of Reed College."

    def test_no_boundary_falls_back_to_word_cut_with_ellipsis(self) -> None:
        from podcast_reader.html import _timeline_label

        label = _timeline_label(
            "I dropped out of Reed College after the first six months but then stayed around"
        )
        assert label == "I dropped out of Reed College after the first six months but…"
        assert len(label) <= 61  # budget + ellipsis

    def test_mid_word_punctuation_is_not_a_boundary(self) -> None:
        from podcast_reader.html import _timeline_label

        label = _timeline_label(
            "sort of 5:00 pm daily and then we kept going on and on well past the budget"
        )
        # The colon inside "5:00" must not be treated as a clause end: the
        # label keeps the whole token and falls back to a word cut.
        assert label != "sort of 5"
        assert "5:00 pm" in label
        assert label.endswith("…")

    def test_single_overlong_word_hard_truncates(self) -> None:
        from podcast_reader.html import _timeline_label

        label = _timeline_label("a" * 80)
        assert label == "a" * 60 + "…"

    def test_surrounding_whitespace_does_not_force_ellipsis(self) -> None:
        from podcast_reader.html import _timeline_label

        assert _timeline_label("  Short   text.  ") == "Short text."


class TestRailGeometryScript:
    """Issue #63: scroll offset is measured from the live rail height, and
    the rail collapses to timestamp chips once scrolled."""

    _SEGS = [
        {"start": 0.0, "end": 10.0, "text": "Opening one."},
        {"start": 305.0, "end": 315.0, "text": "Five minutes one."},
        {"start": 605.0, "end": 615.0, "text": "Ten minutes one."},
    ]

    def test_keyless_artifact_ships_the_rail_script(self) -> None:
        from podcast_reader.html import build_html

        html = build_html(list(self._SEGS), title="T", sentences_per_para=1, source="test")
        assert "scrollPaddingTop" in html
        assert "pr-rail-layout" in html
        assert "new ResizeObserver(layout)" in html
        assert "classList.toggle('stuck', v)" in html
        # Collapsed sticky state hides snippets, keeping timestamp chips.
        assert ".timeline-nav.stuck .timeline-snippet { display: none; }" in html

    def test_chaptered_artifact_omits_the_rail_script(self) -> None:
        from podcast_reader.html import _RAIL_SCRIPT, build_html

        html = build_html(
            list(self._SEGS),
            title="T",
            chapters=[
                {
                    "title": "All",
                    "start": 0.0,
                    "end": 615.0,
                    "abstract": "Everything.",
                    "type": "content",
                    "key_points": [],
                }
            ],
            source="test",
        )
        assert f"<script>\n{_RAIL_SCRIPT}</script>" not in html

    def test_empty_artifact_omits_the_rail_script(self) -> None:
        from podcast_reader.html import _RAIL_SCRIPT, build_html

        html = build_html([], title="T", source="test")
        assert f"<script>\n{_RAIL_SCRIPT}</script>" not in html


class TestSidebarMarginGating:
    """Issue #52: the 280px sidebar margin is reserved only when the sidebar
    is actually emitted (chapters exist)."""

    _SEGS = [{"start": 0.0, "end": 2.0, "text": "Hello there."}]
    _CHAPTERS = [
        {
            "title": "Only Chapter",
            "start": 0.0,
            "end": 2.0,
            "abstract": "All of it.",
            "type": "content",
            "key_points": [],
        }
    ]

    def test_keyless_body_has_no_sidebar_class(self) -> None:
        from podcast_reader.html import build_html

        html = build_html(list(self._SEGS), title="T", source="test")
        assert "<body>" in html
        assert '<body class="has-sidebar">' not in html
        assert "<aside" not in html

    def test_chaptered_body_carries_sidebar_class(self) -> None:
        from podcast_reader.html import build_html

        html = build_html(list(self._SEGS), title="T", chapters=self._CHAPTERS, source="test")
        assert '<body class="has-sidebar">' in html
        assert '<aside id="sidebar">' in html

    def test_margin_rule_is_scoped_to_has_sidebar(self) -> None:
        from podcast_reader.html import build_html

        html = build_html(list(self._SEGS), title="T", source="test")
        assert ".has-sidebar #content {" in html
        # The unscoped #content rule must not reserve the sidebar width.
        unscoped = html.split("#content {", 1)[1].split("}", 1)[0]
        assert "margin-left" not in unscoped


class TestEscaping:
    """Issue #56: transcript- and provider-derived text is escaped at every
    interpolation site; markup the renderer authors itself is unaffected."""

    def test_hostile_segment_text_renders_as_text_not_markup(self) -> None:
        from podcast_reader.html import build_html

        segs = [
            {
                "start": 0.0,
                "end": 2.0,
                "text": "<script>alert('x')</script> & <i>tags</i> stay text",
            }
        ]
        out = build_html(segs, title="T <unsafe> & Title", source="test")
        # The artifact's own <script> blocks (sync) are the renderer's; the
        # transcript's script tag must never appear unescaped.
        assert "<script>alert" not in out
        assert (
            "&lt;script&gt;alert('x')&lt;/script&gt; &amp; &lt;i&gt;tags&lt;/i&gt; stay text" in out
        )
        assert "<title>T &lt;unsafe&gt; &amp; Title</title>" in out
        assert "<h1>T &lt;unsafe&gt; &amp; Title</h1>" in out

    def test_rail_label_survives_hostile_text(self) -> None:
        from podcast_reader.html import build_html

        segs = [
            {"start": 0.0, "end": 10.0, "text": "Opening one."},
            {"start": 10.0, "end": 20.0, "text": "Opening two."},
            {"start": 305.0, "end": 315.0, "text": "Five <b>minutes</b> & one."},
            {"start": 315.0, "end": 325.0, "text": "Ok."},
            {"start": 605.0, "end": 615.0, "text": "Ten minutes one."},
            {"start": 615.0, "end": 625.0, "text": "Ten minutes two."},
        ]
        out = build_html(segs, title="T", sentences_per_para=1, source="test")
        assert '<span class="timeline-snippet">Five &lt;b&gt;minutes&lt;/b&gt; &amp; one.' in out
        assert "<b>minutes</b>" not in out

    def test_raw_caption_entities_and_quotes_render_literally(self) -> None:
        from podcast_reader.html import build_html

        # Some caption tracks carry raw entities in the text; the reader must
        # see the literal characters the source contained ("&amp;" displays
        # as "&amp;", not "&"). Quotes stay untouched: quote=False, inert in
        # text nodes, and no generated attribute carries transcript text.
        segs = [{"start": 0.0, "end": 2.0, "text": 'She said "don\'t" &amp; smiled.'}]
        out = build_html(segs, title="T", source="test")
        assert 'She said "don\'t" &amp;amp; smiled.' in out

    def test_hostile_speaker_label_is_escaped(self) -> None:
        from podcast_reader.html import build_html

        segs = [{"start": 0.0, "end": 2.0, "text": "Hi.", "speaker": "<img src=x>"}]
        out = build_html(segs, title="T", source="test")
        assert '<span class="speaker">&lt;img src=x&gt;</span>' in out
        assert "<img" not in out

    def test_hostile_chapter_fields_are_escaped_and_pull_quote_still_bolds(self) -> None:
        from podcast_reader.html import build_html

        segs = [{"start": 0.0, "end": 10.0, "text": "He said <hi> & bye now."}]
        chapters = [
            {
                "title": "Ch & <Title>",
                "start": 0.0,
                "end": 10.0,
                "abstract": "A & <b>bstract</b>",
                "type": "content",
                "key_points": ["P & <li>oint"],
                "pull_quote": "<hi> & bye",
                "pull_quote_start": 0.0,
            }
        ]
        out = build_html(segs, title="T", chapters=chapters, source="test")
        # Chapter title (h2 + sidebar nav), abstract, key point all inert.
        assert out.count("Ch &amp; &lt;Title&gt;") == 2
        assert "A &amp; &lt;b&gt;bstract&lt;/b&gt;" in out
        assert "<li>P &amp; &lt;li&gt;oint</li>" in out
        # The pull quote still bolds — matched raw, wrapped escaped.
        assert "<strong>&lt;hi&gt; &amp; bye</strong>" in out
        assert "<b>bstract" not in out


class TestBuildHtmlIntegration:
    def test_cleanup_label_is_separate_from_source_provenance(self) -> None:
        from podcast_reader.html import build_html

        html = build_html(
            [{"start": 0.0, "end": 1.0, "text": "Corrected text."}],
            title="T",
            source="youtube-captions",
            caption_cleanup=True,
        )

        assert "Auto-transcribed with youtube-captions" in html
        assert "AI-assisted spelling/casing cleanup enabled; wording is preserved." in html

    def test_os_light_theme_does_not_override_explicit_dark_theme(self) -> None:
        from podcast_reader.html import build_html

        result = build_html([], title="T", source="test")

        assert ":root[data-theme='dark'] {" in result
        assert ":root:not([data-theme='dark']) {" in result

    def test_artifact_uses_no_remote_font_provider(self) -> None:
        from podcast_reader.html import build_html

        result = build_html([], title="Private transcript", source="test")

        assert "fonts.googleapis.com" not in result
        assert "fonts.gstatic.com" not in result
        assert "@import url(" not in result

    def test_legacy_font_filter_does_not_rewrite_noncanonical_content(self) -> None:
        from podcast_reader.html import (
            _LEGACY_REMOTE_FONT_IMPORT,
            without_legacy_remote_font_import,
        )

        document = b"<p>quoted CSS: " + _LEGACY_REMOTE_FONT_IMPORT + b"</p>"

        assert without_legacy_remote_font_import(document) == document

    def test_speakerless_output_byte_identical_no_chapters(self) -> None:
        """Spec scenario: speakerless transcripts unchanged — golden file
        generated before speaker rendering existed."""
        from podcast_reader.html import build_html

        whisper_data = json.loads((FIXTURES / "sample_whisper.json").read_text())
        segments = [s for s in whisper_data["segments"] if s.get("text", "").strip()]

        result = build_html(segments, title="Test Episode", sentences_per_para=5, source="test")

        expected = (FIXTURES / "sample_expected_nochapters.html").read_text()
        assert result == expected

    def test_longform_keyless_golden_stays_current(self) -> None:
        """The longform golden is doubly load-bearing: byte-compared here AND
        measured in a real browser by app/tests/e2e/artifact-geometry.spec.ts
        (the #63 anchor-offset regression gate). Regenerate via
        ``uv run python tests/regen_goldens.py`` after intentional renderer
        changes so the browser gate always measures current output."""
        from podcast_reader.html import build_html

        whisper_data = json.loads((FIXTURES / "longform_whisper.json").read_text())
        segments = [s for s in whisper_data["segments"] if s.get("text", "").strip()]

        result = build_html(segments, title="Longform Test Episode", source="test")

        expected = (FIXTURES / "sample_expected_longform.html").read_text()
        assert result == expected

    def test_near_hour_keyless_golden_stays_current(self) -> None:
        """The 59:56 golden is the browser geometry proof for issue #72."""
        from podcast_reader.html import build_html
        from tests.regen_goldens import _near_hour_segments

        result = build_html(
            _near_hour_segments(),
            title="Near-hour Test Episode",
            sentences_per_para=1,
            source="test",
        )

        expected = (FIXTURES / "sample_expected_near_hour.html").read_text()
        assert result == expected

    def test_multilingual_search_golden_stays_current(self) -> None:
        from podcast_reader.html import build_html

        segments = [
            {"start": 0.0, "end": 10.0, "text": "Resilience begins with deliberate practice."},
            {"start": 10.0, "end": 20.0, "text": "A bridge passage keeps ideas separate."},
            {"start": 20.0, "end": 30.0, "text": "회복탄력성은 다시 시작하는 힘입니다."},
            {"start": 30.0, "end": 40.0, "text": "Another bridge passage preserves spacing."},
            {"start": 40.0, "end": 50.0, "text": "RESILIENCE returns in the closing evidence."},
        ]

        result = build_html(
            segments,
            title="Search Test Episode",
            sentences_per_para=1,
            source="test",
        )

        assert result == (FIXTURES / "sample_expected_search.html").read_text()

    def test_multilingual_export_golden_stays_current(self) -> None:
        from podcast_reader.html import build_html
        from tests.regen_goldens import _export_chapters, _export_segments

        result = build_html(
            _export_segments(),
            title="한국어 인용",
            chapters=_export_chapters(),
            sentences_per_para=1,
            source="test",
        )

        assert result == (FIXTURES / "sample_expected_export.html").read_text()

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
