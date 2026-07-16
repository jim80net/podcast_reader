"""Regenerate the golden HTML fixtures from their whisper-JSON sources.

Run after any intentional renderer change, then review the diff:

    uv run python tests/regen_goldens.py

Goldens:
- ``sample_expected_nochapters.html`` / ``sample_expected.html`` — the short
  fixture, keyless and chaptered (byte-compared by ``tests/test_html.py``).
- ``sample_expected_longform.html`` — a 15-minute keyless fixture with a
  multi-stop jump rail; byte-compared by ``tests/test_html.py`` AND measured
  in a real browser by ``app/tests/e2e/artifact-geometry.spec.ts`` (the #63
  anchor-offset regression gate), which is why it must stay committed and
  current.
- ``sample_expected_near_hour.html`` — a deterministic 59:56 keyless fixture
  that proves the #72 at-rest density boundary in the same browser suite.
- ``sample_expected_search.html`` — multilingual repeated passages for the
  renderer-level search interaction/privacy suite (#88).
"""

from __future__ import annotations

import json
from pathlib import Path

from podcast_reader.html import build_html

FIXTURES = Path(__file__).parent / "fixtures"


def _load_segments(name: str) -> list[dict[str, object]]:
    data = json.loads((FIXTURES / name).read_text())
    return [s for s in data["segments"] if str(s.get("text", "")).strip()]


def _near_hour_segments() -> list[dict[str, object]]:
    """One realistic labeled paragraph per minute through 59:56 (#72)."""
    return [
        {
            "start": float(minute * 60),
            "end": float(59 * 60 + 56 if minute == 59 else (minute + 1) * 60),
            "text": (
                f"Minute {minute} introduces a distinct idea and enough opening words "
                "to exercise the jump rail label width in a realistic transcript."
            ),
        }
        for minute in range(60)
    ]


def main() -> None:
    segments = _load_segments("sample_whisper.json")
    (FIXTURES / "sample_expected_nochapters.html").write_text(
        build_html(segments, title="Test Episode", sentences_per_para=5, source="test")
    )
    chapters = json.loads((FIXTURES / "sample_chapters.json").read_text())
    (FIXTURES / "sample_expected.html").write_text(
        build_html(
            segments, title="Test Episode", chapters=chapters, sentences_per_para=5, source="test"
        )
    )
    longform = _load_segments("longform_whisper.json")
    (FIXTURES / "sample_expected_longform.html").write_text(
        build_html(longform, title="Longform Test Episode", source="test")
    )
    (FIXTURES / "sample_expected_near_hour.html").write_text(
        build_html(
            _near_hour_segments(),
            title="Near-hour Test Episode",
            sentences_per_para=1,
            source="test",
        )
    )
    (FIXTURES / "sample_expected_search.html").write_text(
        build_html(
            [
                {"start": 0.0, "end": 10.0, "text": "Resilience begins with deliberate practice."},
                {"start": 10.0, "end": 20.0, "text": "A bridge passage keeps ideas separate."},
                {"start": 20.0, "end": 30.0, "text": "회복탄력성은 다시 시작하는 힘입니다."},
                {"start": 30.0, "end": 40.0, "text": "Another bridge passage preserves spacing."},
                {"start": 40.0, "end": 50.0, "text": "RESILIENCE returns in the closing evidence."},
            ],
            title="Search Test Episode",
            sentences_per_para=1,
            source="test",
        )
    )
    print("goldens regenerated")


if __name__ == "__main__":
    main()
