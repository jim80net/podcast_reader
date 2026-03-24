"""Tests for chapter timestamp snapping in podcast_reader.chapters."""

from podcast_reader.chapters import snap_chapters_to_segments


class TestSnapChaptersToSegments:
    """Verify that LLM-generated chapter timestamps are snapped to real segment timestamps."""

    SEGMENTS = [
        {"start": 0.0, "end": 5.0, "text": "Hello."},
        {"start": 5.0, "end": 10.0, "text": "Topic one."},
        {"start": 10.0, "end": 15.0, "text": "More topic one."},
        {"start": 15.0, "end": 20.0, "text": "Topic two begins."},
        {"start": 20.0, "end": 25.0, "text": "Topic two continues."},
        {"start": 25.0, "end": 30.0, "text": "Final words."},
    ]

    def test_snaps_start_to_nearest_segment(self):
        """Chapter start that doesn't match any segment gets snapped to nearest."""
        chapters = [
            {"title": "Ch1", "start": 0, "end": 12, "abstract": "", "type": "content",
             "paragraph_breaks": [0], "key_points": [], "pull_quote": None, "pull_quote_start": None},
            {"title": "Ch2", "start": 17, "end": 30, "abstract": "", "type": "content",
             "paragraph_breaks": [17], "key_points": [], "pull_quote": None, "pull_quote_start": None},
        ]
        result = snap_chapters_to_segments(chapters, self.SEGMENTS)
        # 17 is closest to segment at 15.0
        assert result[1]["start"] == 15.0
        assert result[1]["paragraph_breaks"][0] == 15.0

    def test_snaps_end_to_nearest_segment(self):
        """Chapter end that doesn't match any segment gets snapped to nearest."""
        chapters = [
            {"title": "Ch1", "start": 0, "end": 12, "abstract": "", "type": "content",
             "paragraph_breaks": [0], "key_points": [], "pull_quote": None, "pull_quote_start": None},
        ]
        result = snap_chapters_to_segments(chapters, self.SEGMENTS)
        # 12 is closest to segment at 10.0
        assert result[0]["end"] == 10.0

    def test_snaps_paragraph_breaks(self):
        """paragraph_breaks timestamps are snapped to nearest segments."""
        chapters = [
            {"title": "Ch1", "start": 0, "end": 30, "abstract": "", "type": "content",
             "paragraph_breaks": [0, 11, 22], "key_points": [], "pull_quote": None, "pull_quote_start": None},
        ]
        result = snap_chapters_to_segments(chapters, self.SEGMENTS)
        assert result[0]["paragraph_breaks"] == [0.0, 10.0, 20.0]

    def test_snaps_pull_quote_start(self):
        """pull_quote_start is snapped to nearest segment."""
        chapters = [
            {"title": "Ch1", "start": 0, "end": 30, "abstract": "", "type": "content",
             "paragraph_breaks": [0], "key_points": [], "pull_quote": "Topic two begins.",
             "pull_quote_start": 16},
        ]
        result = snap_chapters_to_segments(chapters, self.SEGMENTS)
        assert result[0]["pull_quote_start"] == 15.0

    def test_exact_timestamps_unchanged(self):
        """Timestamps that already match segments are not modified."""
        chapters = [
            {"title": "Ch1", "start": 0.0, "end": 15.0, "abstract": "", "type": "content",
             "paragraph_breaks": [0.0, 10.0], "key_points": [], "pull_quote": None, "pull_quote_start": None},
        ]
        result = snap_chapters_to_segments(chapters, self.SEGMENTS)
        assert result[0]["start"] == 0.0
        assert result[0]["end"] == 15.0
        assert result[0]["paragraph_breaks"] == [0.0, 10.0]

    def test_hallucinated_timestamp_beyond_transcript(self):
        """Timestamp beyond last segment snaps to last segment.

        This is the real-world bug: LLM generates chapter start=920
        but transcript ends at 892.48.
        """
        chapters = [
            {"title": "Earlier", "start": 0, "end": 24, "abstract": "", "type": "content",
             "paragraph_breaks": [0], "key_points": [], "pull_quote": None, "pull_quote_start": None},
            {"title": "Beyond transcript", "start": 35, "end": 50, "abstract": "", "type": "content",
             "paragraph_breaks": [35], "key_points": [], "pull_quote": None, "pull_quote_start": None},
        ]
        result = snap_chapters_to_segments(chapters, self.SEGMENTS)
        # 35 and 50 are beyond last segment (25.0), should snap to 25.0
        assert result[1]["start"] == 25.0
        assert result[1]["end"] == 25.0

    def test_empty_chapters(self):
        """Empty chapter list returns empty."""
        assert snap_chapters_to_segments([], self.SEGMENTS) == []

    def test_empty_segments(self):
        """No segments means no snapping targets — returns chapters unchanged."""
        chapters = [
            {"title": "Ch1", "start": 10, "end": 20, "abstract": "", "type": "content",
             "paragraph_breaks": [10], "key_points": [], "pull_quote": None, "pull_quote_start": None},
        ]
        result = snap_chapters_to_segments(chapters, [])
        assert result[0]["start"] == 10
