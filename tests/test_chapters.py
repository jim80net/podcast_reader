"""Tests for chapter timestamp snapping in podcast_reader.chapters."""

from __future__ import annotations

from typing import Any

from podcast_reader.chapters import snap_chapters_to_segments


def _ch(
    title: str = "Ch1",
    start: float = 0,
    end: float = 30,
    paragraph_breaks: list[float] | None = None,
    pull_quote: str | None = None,
    pull_quote_start: float | None = None,
) -> dict[str, Any]:
    """Build a chapter dict with sensible defaults."""
    return {
        "title": title,
        "start": start,
        "end": end,
        "abstract": "",
        "type": "content",
        "paragraph_breaks": paragraph_breaks or [start],
        "key_points": [],
        "pull_quote": pull_quote,
        "pull_quote_start": pull_quote_start,
    }


class TestSnapChaptersToSegments:
    """Verify that LLM-generated chapter timestamps are snapped to real segment timestamps."""

    SEGMENTS: list[dict[str, Any]] = [
        {"start": 0.0, "end": 5.0, "text": "Hello."},
        {"start": 5.0, "end": 10.0, "text": "Topic one."},
        {"start": 10.0, "end": 15.0, "text": "More topic one."},
        {"start": 15.0, "end": 20.0, "text": "Topic two begins."},
        {"start": 20.0, "end": 25.0, "text": "Topic two continues."},
        {"start": 25.0, "end": 30.0, "text": "Final words."},
    ]

    def test_snaps_start_to_nearest_segment(self) -> None:
        """Chapter start not matching any segment gets snapped to nearest."""
        chapters = [
            _ch(start=0, end=12, paragraph_breaks=[0]),
            _ch(title="Ch2", start=17, end=30, paragraph_breaks=[17]),
        ]
        result = snap_chapters_to_segments(chapters, self.SEGMENTS)
        # 17 is closest to segment at 15.0
        assert result[1]["start"] == 15.0
        assert result[1]["paragraph_breaks"][0] == 15.0

    def test_snaps_end_to_nearest_segment(self) -> None:
        """Chapter end not matching any segment gets snapped to nearest."""
        chapters = [_ch(start=0, end=12, paragraph_breaks=[0])]
        result = snap_chapters_to_segments(chapters, self.SEGMENTS)
        # 12 is closest to segment at 10.0
        assert result[0]["end"] == 10.0

    def test_snaps_paragraph_breaks(self) -> None:
        """paragraph_breaks timestamps are snapped to nearest segments."""
        chapters = [_ch(paragraph_breaks=[0, 11, 22])]
        result = snap_chapters_to_segments(chapters, self.SEGMENTS)
        assert result[0]["paragraph_breaks"] == [0.0, 10.0, 20.0]

    def test_snaps_pull_quote_start(self) -> None:
        """pull_quote_start is snapped to nearest segment."""
        chapters = [
            _ch(
                pull_quote="Topic two begins.",
                pull_quote_start=16,
            )
        ]
        result = snap_chapters_to_segments(chapters, self.SEGMENTS)
        assert result[0]["pull_quote_start"] == 15.0

    def test_exact_timestamps_unchanged(self) -> None:
        """Timestamps that already match segments are not modified."""
        chapters = [_ch(start=0.0, end=15.0, paragraph_breaks=[0.0, 10.0])]
        result = snap_chapters_to_segments(chapters, self.SEGMENTS)
        assert result[0]["start"] == 0.0
        assert result[0]["end"] == 15.0
        assert result[0]["paragraph_breaks"] == [0.0, 10.0]

    def test_hallucinated_timestamp_beyond_transcript(self) -> None:
        """Timestamp beyond last segment snaps to last segment."""
        chapters = [
            _ch(title="Earlier", start=0, end=24, paragraph_breaks=[0]),
            _ch(
                title="Beyond transcript",
                start=35,
                end=50,
                paragraph_breaks=[35],
            ),
        ]
        result = snap_chapters_to_segments(chapters, self.SEGMENTS)
        # 35 and 50 are beyond last segment (25.0), should snap to 25.0
        assert result[1]["start"] == 25.0
        assert result[1]["end"] == 25.0

    def test_empty_chapters(self) -> None:
        """Empty chapter list returns empty."""
        assert snap_chapters_to_segments([], self.SEGMENTS) == []

    def test_empty_segments(self) -> None:
        """No segments means no snapping — returns chapters unchanged."""
        chapters = [_ch(start=10, end=20, paragraph_breaks=[10])]
        result = snap_chapters_to_segments(chapters, [])
        assert result[0]["start"] == 10
