"""Bounded, local-only full-text search over canonical transcript artifacts."""

from __future__ import annotations

import codecs
import re
import stat
import time
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from podcast_reader.types import LibraryEntry

_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
_WORD_RE = re.compile(r"\S+")


@dataclass(frozen=True, slots=True)
class SearchLimits:
    """Hard byte/count limits plus a cooperative wall-clock deadline."""

    max_artifacts: int = 500
    max_artifact_bytes: int = 2 * 1024 * 1024
    max_total_bytes: int = 32 * 1024 * 1024
    max_seconds: float = 1.5
    max_results: int = 20
    chunk_bytes: int = 64 * 1024


@dataclass(frozen=True, slots=True)
class SearchMatch:
    source_id: str
    title: str
    excerpt: str


@dataclass(frozen=True, slots=True)
class SearchOutcome:
    results: tuple[SearchMatch, ...]
    has_more: bool
    partial: bool


_DEFAULT_LIMITS = SearchLimits()


@dataclass(frozen=True, slots=True)
class _StackTag:
    name: str
    timestamp: bool = False


class _ArtifactParser(HTMLParser):
    """Strictly extract canonical title and timestamped paragraphs."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[_StackTag] = []
        self.invalid = False
        self.html_count = 0
        self.head_count = 0
        self.body_count = 0
        self.content_count = 0
        self.main_count = 0
        self.title_count = 0
        self.html_closed = 0
        self.head_closed = 0
        self.body_closed = 0
        self.content_closed = 0
        self.main_closed = 0
        self.title_closed = 0
        self.title_parts: list[str] = []
        self.paragraphs: list[str] = []
        self._paragraph_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = {name.lower(): value for name, value in attrs}
        if self._paragraph_parts is not None:
            parent = self.stack[-1].name if self.stack else ""
            canonical_child = (
                tag == "span"
                and parent == "p"
                and set(attributes) == {"class"}
                and attributes["class"] in {"speaker", "ts"}
            ) or (tag == "strong" and parent == "p" and not attributes)
            if not canonical_child:
                self.invalid = True
        if tag == "html":
            self.html_count += 1
            if self.stack or attributes != {"lang": "en"}:
                self.invalid = True
        elif tag == "head":
            self.head_count += 1
            if attributes or not self.stack or self.stack[-1].name != "html":
                self.invalid = True
        elif tag == "body":
            self.body_count += 1
            if (
                attributes not in ({}, {"class": "has-sidebar"})
                or not self.stack
                or self.stack[-1].name != "html"
            ):
                self.invalid = True
        elif tag == "div" and attributes.get("id") == "content":
            self.content_count += 1
            if attributes != {"id": "content"} or not self.stack or self.stack[-1].name != "body":
                self.invalid = True
        elif tag == "main":
            if not attributes and self.stack and self.stack[-1].name == "div#content":
                self.main_count += 1
            else:
                self.invalid = True
        elif tag == "title":
            if not attributes and self.stack and self.stack[-1].name == "head":
                self.title_count += 1
            else:
                self.invalid = True

        if tag == "div" and attributes.get("id") == "content":
            stack_name = "div#content"
        elif tag == "main" and self.stack and self.stack[-1].name == "div#content":
            stack_name = "main#content"
        elif (
            tag == "section"
            and self.stack
            and self.stack[-1].name == "main#content"
            and "chapter-section" in (attributes.get("class") or "").split()
            and set(attributes) == {"id", "class", "data-start"}
        ):
            stack_name = "section#chapter"
        elif (
            tag == "div"
            and self.stack
            and self.stack[-1].name == "section#chapter"
            and attributes == {"class": "chapter-main"}
        ):
            stack_name = "div#chapter-main"
        elif tag == "title" and self.stack and self.stack[-1].name == "head":
            stack_name = "title#head"
        else:
            stack_name = tag
        timestamp = tag == "span" and "ts" in (attributes.get("class") or "").split()
        if tag not in _VOID_TAGS:
            self.stack.append(_StackTag(stack_name, timestamp))

        if tag == "p" and "data-start" in attributes:
            canonical = (
                "data-end" in attributes
                and set(attributes) <= {"id", "data-start", "data-end"}
                and len(self.stack) >= 2
                and self.stack[-2].name in {"main#content", "div#chapter-main"}
            )
            if canonical:
                self._paragraph_parts = []
            else:
                self.invalid = True

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _VOID_TAGS:
            return
        if tag == "div" and self.stack and self.stack[-1].name == "div#content":
            expected = "div#content"
        elif tag == "main" and self.stack and self.stack[-1].name == "main#content":
            expected = "main#content"
        elif tag == "section" and self.stack and self.stack[-1].name == "section#chapter":
            expected = "section#chapter"
        elif tag == "div" and self.stack and self.stack[-1].name == "div#chapter-main":
            expected = "div#chapter-main"
        elif tag == "title" and self.stack and self.stack[-1].name == "title#head":
            expected = "title#head"
        else:
            expected = tag
        if not self.stack or self.stack[-1].name != expected:
            self.invalid = True
            return
        closed = self.stack.pop()
        if closed.name == "html":
            self.html_closed += 1
        elif closed.name == "head":
            self.head_closed += 1
        elif closed.name == "body":
            self.body_closed += 1
        elif closed.name == "div#content":
            self.content_closed += 1
        elif closed.name == "main#content":
            self.main_closed += 1
        elif closed.name == "title#head":
            self.title_closed += 1
        elif closed.name == "p" and self._paragraph_parts is not None:
            paragraph = " ".join("".join(self._paragraph_parts).split())
            if paragraph:
                self.paragraphs.append(paragraph)
            self._paragraph_parts = None

    def handle_data(self, data: str) -> None:
        if self.stack and self.stack[-1].name == "title#head":
            self.title_parts.append(data)
        if self._paragraph_parts is not None and not any(item.timestamp for item in self.stack):
            self._paragraph_parts.append(data)

    def extracted(self) -> tuple[str, tuple[str, ...]] | None:
        valid = (
            not self.invalid
            and not self.stack
            and self._paragraph_parts is None
            and self.html_count == self.html_closed == 1
            and self.head_count == self.head_closed == 1
            and self.body_count == self.body_closed == 1
            and self.content_count == self.content_closed == 1
            and self.main_count == self.main_closed == 1
            and self.title_count == self.title_closed == 1
        )
        if not valid:
            return None
        return " ".join("".join(self.title_parts).split()), tuple(self.paragraphs)


def _normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _clip_excerpt(paragraph: str, terms: tuple[str, ...], limit: int = 180) -> str:
    if len(paragraph) <= limit:
        return paragraph
    words = list(_WORD_RE.finditer(paragraph))
    match_index = next(
        (
            index
            for index, word in enumerate(words)
            if any(term in _normalize(word.group()) for term in terms)
        ),
        0,
    )
    left = right = match_index
    while True:
        moved = False
        if left > 0:
            candidate = paragraph[words[left - 1].start() : words[right].end()]
            if len(candidate) + 2 <= limit:
                left -= 1
                moved = True
        if right + 1 < len(words):
            candidate = paragraph[words[left].start() : words[right + 1].end()]
            if len(candidate) + 2 <= limit:
                right += 1
                moved = True
        if not moved:
            break
    excerpt = paragraph[words[left].start() : words[right].end()]
    if left > 0:
        excerpt = f"…{excerpt}"
    if right + 1 < len(words):
        excerpt = f"{excerpt}…"
    if len(excerpt) > limit:
        word = words[match_index]
        direct_match = next(
            (
                found
                for term in terms
                if (found := re.search(re.escape(term), word.group(), re.IGNORECASE)) is not None
            ),
            None,
        )
        if direct_match is None:
            return "Match occurs inside a long transcript token."
        content_limit = limit - 2
        match_start = word.start() + direct_match.start()
        match_end = word.start() + direct_match.end()
        start = max(word.start(), match_start - (content_limit - (match_end - match_start)) // 2)
        start = min(start, word.end() - content_limit)
        end = min(word.end(), start + content_limit)
        leading = start > 0
        trailing = end < len(paragraph)
        excerpt = paragraph[start:end]
        if leading:
            excerpt = f"…{excerpt}"
        if trailing:
            excerpt = f"{excerpt}…"
    return excerpt


def _match(
    entry: LibraryEntry, document_title: str, paragraphs: tuple[str, ...], terms: tuple[str, ...]
) -> SearchMatch | None:
    title_norm = _normalize(f"{entry['title']} {document_title}")
    normalized_paragraphs = tuple(_normalize(paragraph) for paragraph in paragraphs)
    corpus = " ".join((title_norm, *normalized_paragraphs))
    if not all(term in corpus for term in terms):
        return None

    scores = tuple(sum(term in paragraph for term in terms) for paragraph in normalized_paragraphs)
    if not scores or max(scores) == 0:
        excerpt = "Matches the episode title."
    else:
        best = next(
            (index for index, score in enumerate(scores) if score == len(terms)),
            scores.index(max(scores)),
        )
        excerpt = _clip_excerpt(paragraphs[best], terms)
    return SearchMatch(entry["source_id"], entry["title"], excerpt)


def _parse_artifact(
    path: Path,
    *,
    limits: SearchLimits,
    clock: Callable[[], float],
    deadline: float,
    max_bytes: int,
) -> tuple[tuple[str, tuple[str, ...]] | None, int, bool]:
    """Return extracted text, bytes read, and whether the time budget expired."""
    parser = _ArtifactParser()
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    read = 0
    try:
        with path.open("rb") as artifact:
            while True:
                if clock() > deadline:
                    return None, read, True
                remaining = max_bytes - read
                chunk = artifact.read(min(limits.chunk_bytes, remaining + 1))
                if not chunk:
                    break
                if len(chunk) > remaining:
                    return None, read, False
                read += len(chunk)
                parser.feed(decoder.decode(chunk))
            parser.feed(decoder.decode(b"", final=True))
            parser.close()
        if clock() > deadline:
            return None, read, True
    except Exception:
        return None, read, False
    return parser.extracted(), read, False


def search_library(
    entries: Sequence[LibraryEntry],
    query: str,
    *,
    limits: SearchLimits = _DEFAULT_LIMITS,
    clock: Callable[[], float] = time.monotonic,
) -> SearchOutcome:
    """Search newest artifacts first within deterministic resource budgets."""
    terms = tuple(_normalize(term) for term in query.split() if term)
    if not terms:
        return SearchOutcome((), False, False)
    started = clock()
    deadline = started + limits.max_seconds
    results: list[SearchMatch] = []
    total_bytes = 0
    partial = False
    visited = 0

    for entry in reversed(entries):
        if visited >= limits.max_artifacts or clock() > deadline:
            partial = True
            break
        visited += 1
        path = Path(entry["html_path"])
        try:
            file_stat = path.stat()
        except Exception:
            partial = True
            continue
        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_size > limits.max_artifact_bytes:
            partial = True
            continue
        if file_stat.st_size > limits.max_total_bytes - total_bytes:
            partial = True
            break
        extracted, bytes_read, expired = _parse_artifact(
            path,
            limits=limits,
            clock=clock,
            deadline=deadline,
            max_bytes=min(
                limits.max_artifact_bytes,
                limits.max_total_bytes - total_bytes,
            ),
        )
        total_bytes += bytes_read
        if expired:
            partial = True
            break
        if extracted is None:
            partial = True
            continue
        document_title, paragraphs = extracted
        try:
            match = _match(entry, document_title, paragraphs, terms)
        except Exception:
            partial = True
            continue
        if clock() > deadline:
            partial = True
            break
        if match is not None:
            results.append(match)
            if len(results) > limits.max_results:
                return SearchOutcome(tuple(results[: limits.max_results]), True, partial)

    if visited < len(entries) and not partial:
        partial = True
    return SearchOutcome(tuple(results), False, partial)
