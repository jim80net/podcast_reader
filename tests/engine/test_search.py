from __future__ import annotations

import io
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from podcast_reader.engine.search import SearchLimits, search_library
from podcast_reader.html import build_html
from podcast_reader.types import LibraryEntry

if TYPE_CHECKING:
    from collections.abc import Callable


def _entry(tmp_path: Path, source_id: str, html: str | bytes, *, title: str) -> LibraryEntry:
    path = tmp_path / f"{source_id}.html"
    if isinstance(html, bytes):
        path.write_bytes(html)
    else:
        path.write_text(html, encoding="utf-8")
    return LibraryEntry(
        source_id=source_id,
        source="https://example.com/private",
        title=title,
        html_path=str(path),
        created_at=1.0,
    )


def _html(*texts: str, title: str = "Episode") -> str:
    segments = [
        {"start": float(index), "end": float(index + 1), "text": text}
        for index, text in enumerate(texts)
    ]
    return build_html(segments, title, sentences_per_para=1)


def test_searches_only_title_and_timestamped_transcript_paragraphs(tmp_path: Path) -> None:
    chapters = [
        {
            "start": 0.0,
            "end": 1.0,
            "title": "Chapter navigation needle",
            "abstract": "Summary-only decoy",
            "type": "content",
            "key_points": ["Key-point decoy"],
        }
    ]
    html = build_html(
        [{"start": 0.0, "end": 1.0, "text": "The durable browser marker lives here."}],
        "Searchable episode",
        chapters=chapters,
    )
    entry = _entry(tmp_path, "a" * 64, html, title="Searchable episode")

    match = search_library([entry], "browser marker")
    summary_decoy = search_library([entry], "summary-only")
    navigation_decoy = search_library([entry], "navigation needle")

    assert [result.source_id for result in match.results] == ["a" * 64]
    assert match.results[0].excerpt == "The durable browser marker lives here."
    assert summary_decoy.results == ()
    assert navigation_decoy.results == ()


def test_normalizes_unicode_and_chooses_best_evidence_paragraph(tmp_path: Path) -> None:
    html = _html(
        "An unrelated introduction.",
        "Another unrelated sentence.",
        "Fußball tactics are the complete subject of this paragraph.",
        title="Notes from the cafe\N{COMBINING ACUTE ACCENT}",
    )
    entry = _entry(tmp_path, "b" * 64, html, title="Notes from the café")

    result = search_library([entry], "CAFÉ fussball")

    assert len(result.results) == 1
    assert result.results[0].excerpt == (
        "Fußball tactics are the complete subject of this paragraph."
    )


def test_title_only_match_uses_fixed_nonreflective_excerpt(tmp_path: Path) -> None:
    entry = _entry(
        tmp_path,
        "c" * 64,
        _html("Nothing relevant in the transcript.", title="Packet Gardening"),
        title="Packet Gardening",
    )

    result = search_library([entry], "packet")

    assert result.results[0].excerpt == "Matches the episode title."


def test_invalid_artifacts_are_skipped_and_mark_response_partial(tmp_path: Path) -> None:
    invalid_utf8 = _entry(tmp_path, "d" * 64, b"\xff\xfe", title="Invalid bytes")
    truncated = _entry(
        tmp_path,
        "e" * 64,
        _html("A searchable needle.").replace("</main>", "", 1),
        title="Truncated",
    )
    valid = _entry(tmp_path, "f" * 64, _html("A searchable needle."), title="Valid")

    result = search_library([invalid_utf8, truncated, valid], "needle")

    assert [match.title for match in result.results] == ["Valid"]
    assert result.partial is True


def test_per_file_and_aggregate_budgets_bound_reads(tmp_path: Path) -> None:
    oversized = _entry(tmp_path, "1" * 64, _html("hidden oversize needle"), title="Large")
    valid = _entry(tmp_path, "2" * 64, _html("visible needle"), title="Small")
    valid_size = Path(valid["html_path"]).stat().st_size

    result = search_library(
        [oversized, valid],
        "needle",
        limits=SearchLimits(
            max_artifacts=10,
            max_artifact_bytes=valid_size,
            max_total_bytes=valid_size,
            max_seconds=10.0,
            max_results=20,
            chunk_bytes=64,
        ),
    )

    assert [match.title for match in result.results] == ["Small"]
    assert result.partial is True


def test_result_cap_sets_has_more_and_keeps_newest_first(tmp_path: Path) -> None:
    entries = [
        _entry(tmp_path, f"{index:064x}", _html("shared needle"), title=f"Episode {index}")
        for index in range(4)
    ]

    result = search_library(
        entries,
        "needle",
        limits=SearchLimits(max_results=2),
    )

    assert [match.title for match in result.results] == ["Episode 3", "Episode 2"]
    assert result.has_more is True


def test_cooperative_deadline_stops_before_next_artifact(tmp_path: Path) -> None:
    entries = [
        _entry(tmp_path, f"{index + 10:064x}", _html("deadline needle"), title=str(index))
        for index in range(3)
    ]
    ticks = iter([0.0, 0.0, 0.0, 2.0])

    result = search_library(
        entries,
        "needle",
        limits=SearchLimits(max_seconds=1.0),
        clock=lambda: next(ticks, 2.0),
    )

    assert result.partial is True
    assert len(result.results) < len(entries)


def test_noncanonical_nested_script_discards_whole_artifact(tmp_path: Path) -> None:
    document = _html("safe needle").replace("safe needle", "safe <script>needle</script>", 1)
    entry = _entry(tmp_path, "9" * 64, document, title="Invalid nesting")

    result = search_library([entry], "needle")

    assert result.results == ()
    assert result.partial is True


@pytest.mark.parametrize(
    "descendant",
    [
        "<span hidden>hidden needle</span>",
        "<template>hidden needle</template>",
        '<span class="other">hidden needle</span>',
    ],
)
def test_noncanonical_paragraph_descendant_discards_whole_artifact(
    tmp_path: Path, descendant: str
) -> None:
    document = _html("ordinary transcript").replace("ordinary transcript", descendant, 1)
    entry = _entry(tmp_path, "0" * 64, document, title="Invalid descendant")

    result = search_library([entry], "hidden needle")

    assert result.results == ()
    assert result.partial is True


def test_hidden_paragraph_ancestor_discards_whole_artifact(tmp_path: Path) -> None:
    hidden = '<section hidden><p data-start="9" data-end="10">hidden needle</p></section>'
    document = _html("ordinary transcript").replace("<main>", f"<main>{hidden}", 1)
    entry = _entry(tmp_path, "1" * 64, document, title="Hidden ancestor")

    result = search_library([entry], "hidden needle")

    assert result.results == ()
    assert result.partial is True


@pytest.mark.parametrize(
    ("original", "replacement"),
    [
        ('<html lang="en">', '<html lang="en" hidden>'),
        ("<head>", "<head hidden>"),
        ("<body>", "<body hidden>"),
        ('<div id="content">', '<div id="content" hidden>'),
        ("<main>", "<main hidden>"),
    ],
)
def test_hidden_canonical_ancestor_discards_whole_artifact(
    tmp_path: Path, original: str, replacement: str
) -> None:
    document = _html("hidden ancestor needle").replace(original, replacement, 1)
    entry = _entry(tmp_path, "2" * 64, document, title="Hidden canonical ancestor")

    result = search_library([entry], "hidden ancestor needle")

    assert result.results == ()
    assert result.partial is True


@pytest.mark.parametrize(
    "injection",
    [
        '<main hidden><p data-start="0" data-end="1">hidden needle</p></main>',
        "<title>hidden needle</title>",
    ],
)
def test_out_of_place_searchable_content_discards_whole_artifact(
    tmp_path: Path, injection: str
) -> None:
    document = _html("ordinary transcript").replace("<body>", f"<body>{injection}", 1)
    entry = _entry(tmp_path, "7" * 64, document, title="Ordinary")

    result = search_library([entry], "hidden needle")

    assert result.results == ()
    assert result.partial is True


@pytest.mark.parametrize(
    "mutate",
    [
        lambda document: document.replace("<body>", "</html><body>", 1),
        lambda document: document.replace("<body>", '<div id="content">', 1),
        lambda document: document.replace("<body>", '<body><head><div id="content">', 1),
    ],
)
def test_noncanonical_document_ancestry_discards_whole_artifact(
    tmp_path: Path, mutate: Callable[[str], str]
) -> None:
    document = mutate(_html("ancestry needle"))
    entry = _entry(tmp_path, "a" * 64, document, title="Invalid ancestry")

    result = search_library([entry], "ancestry needle")

    assert result.results == ()
    assert result.partial is True


@pytest.mark.parametrize(
    ("token", "starts_with_ellipsis", "ends_with_ellipsis"),
    [
        (f"needle{'x' * 500}", False, True),
        (f"{'x' * 250}needle{'x' * 250}", True, True),
        (f"{'x' * 500}needle", True, False),
    ],
)
def test_oversized_matching_token_keeps_evidence_within_excerpt_limit(
    tmp_path: Path, token: str, starts_with_ellipsis: bool, ends_with_ellipsis: bool
) -> None:
    entry = _entry(tmp_path, "6" * 64, _html(token), title="Long token")

    result = search_library([entry], "needle")

    excerpt = result.results[0].excerpt
    assert len(excerpt) <= 180
    assert "needle" in excerpt.casefold()
    assert excerpt.startswith("…") is starts_with_ellipsis
    assert excerpt.endswith("…") is ends_with_ellipsis


def test_file_growth_after_stat_cannot_cross_read_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = _entry(tmp_path, "8" * 64, _html("growth needle"), title="Growing")
    path = Path(entry["html_path"])
    original = path.read_bytes()
    original_open = Path.open

    def growing_open(candidate: Path, *args: object, **kwargs: object) -> io.BytesIO:
        if candidate == path:
            return io.BytesIO(original + b"x" * 100)
        return original_open(candidate, *args, **kwargs)  # type: ignore[return-value]

    monkeypatch.setattr(Path, "open", growing_open)

    result = search_library(
        [entry],
        "needle",
        limits=SearchLimits(max_artifact_bytes=len(original)),
    )

    assert result.results == ()
    assert result.partial is True


def test_unexpected_stat_and_parser_failures_are_isolated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stat_failure = _entry(tmp_path, "3" * 64, _html("stat needle"), title="Stat")
    parse_failure = _entry(tmp_path, "4" * 64, _html("explode needle"), title="Parse")
    valid = _entry(tmp_path, "5" * 64, _html("valid needle"), title="Valid")
    stat_path = Path(stat_failure["html_path"])
    original_stat = Path.stat

    def selective_stat(path: Path, *args: object, **kwargs: object) -> object:
        if path == stat_path:
            raise RuntimeError("unexpected stat failure")
        return original_stat(path, *args, **kwargs)

    from podcast_reader.engine import search as search_module

    original_feed = search_module._ArtifactParser.feed

    def selective_feed(parser: object, data: str) -> None:
        if "explode needle" in data:
            raise RuntimeError("unexpected parser failure")
        original_feed(parser, data)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "stat", selective_stat)
    monkeypatch.setattr(search_module._ArtifactParser, "feed", selective_feed)

    result = search_library([stat_failure, parse_failure, valid], "needle")

    assert [match.title for match in result.results] == ["Valid"]
    assert result.partial is True


@pytest.mark.parametrize(
    "ticks",
    [
        [0.0, 0.0, 0.0, 2.0],
        [0.0, 0.0, 0.0, 0.0, 2.0],
    ],
)
def test_deadline_crossed_during_finalization_or_matching_discards_result(
    tmp_path: Path, ticks: list[float]
) -> None:
    entry = _entry(tmp_path, "0" * 64, _html("deadline needle"), title="Deadline")
    times = iter(ticks)

    result = search_library(
        [entry],
        "needle",
        limits=SearchLimits(max_seconds=1.0),
        clock=lambda: next(times, 2.0),
    )

    assert result.results == ()
    assert result.partial is True
