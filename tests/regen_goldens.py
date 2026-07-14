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
"""

from __future__ import annotations

import json
from pathlib import Path

from podcast_reader.html import build_html

FIXTURES = Path(__file__).parent / "fixtures"


def _load_segments(name: str) -> list[dict[str, object]]:
    data = json.loads((FIXTURES / name).read_text())
    return [s for s in data["segments"] if str(s.get("text", "")).strip()]


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
    print("goldens regenerated")


if __name__ == "__main__":
    main()
