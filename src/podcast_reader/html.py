"""Convert whisper-ctranslate2 JSON output to a styled HTML transcript."""

from __future__ import annotations

import html
from typing import Any


def _esc(text: str) -> str:
    """Escape untrusted text for interpolation into markup text nodes (#56).

    Applied to transcript-derived strings (segment text, rail labels,
    speaker labels) and provider-derived strings (titles, abstracts, key
    points, pull quotes). ``quote=False``: quotes are inert in text nodes,
    and generated attribute values (slug anchors, times) never carry this
    text.
    """
    return html.escape(text, quote=False)


def fmt_time(seconds: float) -> str:
    """Format seconds as HH:MM:SS."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _count_sentences(text: str) -> int:
    """Count sentence-ending punctuation marks."""
    return sum(1 for ch in text if ch in ".!?")


def _last_sentence_boundary(text: str) -> int | None:
    """Find the index of the last sentence-ending punctuation (.!?) in text.

    Returns None if no sentence boundary exists.
    """
    for i in range(len(text) - 1, -1, -1):
        if text[i] in ".!?":
            return i
    return None


_MAX_PARAGRAPH_CHARS = 800


def segments_to_paragraphs(
    segments: list[dict[str, Any]], sentences_per_para: int = 5
) -> list[dict[str, Any]]:
    """Group segments into paragraphs of roughly N sentences each.

    Breaks occur at sentence boundaries to avoid splitting mid-sentence.
    YouTube captions have short, overlapping segments where sentence boundaries
    rarely align with segment boundaries. When a paragraph exceeds the sentence
    threshold, the text is split at the last sentence boundary, carrying any
    trailing fragment into the next paragraph.

    A character-count safety valve ensures paragraphs still break even when
    there is no sentence-ending punctuation in the text.
    """
    paragraphs: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    carry = ""  # text after the last sentence boundary, carried to next paragraph

    for seg in segments:
        text = seg["text"].strip()
        if not text:
            continue

        # A speaker change always starts a new paragraph (visible attribution
        # at speaker changes). Speakerless segments compare None == None, so
        # transcripts without diarization group exactly as before. The carry
        # is empty whenever a paragraph is open, so nothing is dropped here.
        if current is not None and seg.get("speaker") != current.get("speaker"):
            paragraphs.append(current)
            current = None

        if current is None:
            # A non-empty carry is leftover text from the paragraph appended
            # by the last sentence-boundary split (always paragraphs[-1]). It
            # belongs to that paragraph's speaker: prepend it here only when
            # the speaker matches, otherwise reattach it to its own paragraph
            # so a speaker change never misattributes the fragment.
            if carry and paragraphs and seg.get("speaker") != paragraphs[-1].get("speaker"):
                paragraphs[-1]["text"] += " " + carry
                carry = ""
            combined = (carry + " " + text).strip() if carry else text
            current = {"start": seg["start"], "end": seg["end"], "text": combined}
            if seg.get("speaker") is not None:
                current["speaker"] = seg["speaker"]
            carry = ""
        else:
            current["end"] = seg["end"]
            current["text"] += " " + text

            threshold_met = _count_sentences(current["text"]) >= sentences_per_para
            too_long = len(current["text"]) >= _MAX_PARAGRAPH_CHARS

            if threshold_met or too_long:
                # Find the last sentence boundary to split at
                boundary = _last_sentence_boundary(current["text"])
                if boundary is not None:
                    para_text = current["text"][: boundary + 1].rstrip()
                    carry = current["text"][boundary + 1 :].strip()
                    current["text"] = para_text
                    paragraphs.append(current)
                    current = None
                elif too_long:
                    # No sentence boundary at all — force break at segment boundary
                    paragraphs.append(current)
                    current = None
                    carry = ""

    if current:
        paragraphs.append(current)

    return paragraphs


def segments_to_paragraphs_themed(
    segments: list[dict[str, Any]], break_times: list[float]
) -> list[dict[str, Any]]:
    """Group segments into paragraphs using LLM-provided thematic break timestamps."""
    if not break_times:
        return segments_to_paragraphs(segments)

    sorted_breaks = sorted(break_times)
    paragraphs: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    break_idx = 1  # skip first break (it's the chapter start, already handled)

    for seg in segments:
        text = seg["text"].strip()
        if not text:
            continue

        # Check if this segment crosses the next paragraph break
        starts_new = False
        while break_idx < len(sorted_breaks) and seg["start"] >= sorted_breaks[break_idx] - 0.5:
            starts_new = True
            break_idx += 1

        if current is not None and seg.get("speaker") != current.get("speaker"):
            starts_new = True  # speaker changes break paragraphs here too

        if current is None or starts_new:
            if current is not None:
                paragraphs.append(current)
            current = {"start": seg["start"], "end": seg["end"], "text": text}
            if seg.get("speaker") is not None:
                current["speaker"] = seg["speaker"]
        else:
            current["end"] = seg["end"]
            current["text"] += " " + text

    if current:
        paragraphs.append(current)

    return paragraphs


def _slug(text: str) -> str:
    """Turn a chapter title into a URL-safe anchor ID."""
    return "ch-" + "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")


def _speaker_label(speaker: str) -> str:
    """Friendly display name for a diarization label (SPEAKER_00 -> Speaker 1)."""
    prefix, _, number = speaker.rpartition("_")
    if prefix == "SPEAKER" and number.isdigit():
        return f"Speaker {int(number) + 1}"
    return speaker


def _speaker_prefix(paragraph: dict[str, Any], last_speaker: str | None) -> str:
    """The attribution span when this paragraph changes speaker, else ``""``.

    Speakerless paragraphs render exactly as before (empty prefix) — the
    field is optional end to end.
    """
    speaker = paragraph.get("speaker")
    if speaker is None or speaker == last_speaker:
        return ""
    return f'<span class="speaker">{_esc(_speaker_label(speaker))}</span> '


TYPE_LABELS = {
    "sponsor": "Sponsor",
    "intro": "Intro",
    "outro": "Outro",
    "housekeeping": "Housekeeping",
}


def build_sidebar_nav(chapters: list[dict[str, Any]]) -> str:
    """Build the fixed sidebar chapter navigator."""
    items = []
    for ch in chapters:
        anchor = _slug(ch["title"])
        ts = fmt_time(ch["start"])
        label = TYPE_LABELS.get(ch["type"])
        badge = f' <span class="nav-badge nav-badge-{ch["type"]}">{label}</span>' if label else ""
        css_class = f"nav-item type-{ch['type']}"
        items.append(
            f'<a href="#{anchor}" class="{css_class}" data-section="{anchor}">'
            f'<span class="nav-ts">{ts}</span>'
            f'<span class="nav-title">{_esc(ch["title"])}{badge}</span>'
            f"</a>"
        )
    return (
        '<aside id="sidebar">\n<div class="sidebar-inner">\n'
        + "\n".join(items)
        + "\n</div>\n</aside>"
    )


def _time_anchor(seconds: float) -> str:
    """Stable fragment identifier for a timestamped keyless passage."""
    return f"t-{round(seconds * 1000)}"


def _timeline_interval(duration: float) -> float:
    """Choose a marker interval dense enough to navigate by (#64).

    Every ~2-3 minutes for short talks; the wrapped rail absorbs the extra
    stops by design (#57).
    """
    if duration <= 10 * 60:
        return 2 * 60
    if duration <= 30 * 60:
        return 3 * 60
    if duration <= 45 * 60:
        return 5 * 60
    return 10 * 60


_TIMELINE_LABEL_MAX_CHARS = 60

#: Clause boundaries a label may end on. Sentence enders stay in the label;
#: soft breaks (comma/semicolon/colon/dashes) are trimmed off the end.
_LABEL_SENTENCE_ENDERS = ".!?"
_LABEL_SOFT_BREAKS = ",;:—–"
#: Don't cut at a boundary so early the label carries no meaning.
_LABEL_MIN_CUT = 20


def _timeline_label(text: str) -> str:
    """The opening clause of a marker paragraph, as a rail/landmark label.

    Prefer completing the clause or sentence within the budget — a label
    that ends at real punctuation reads as editorial, not as a machine cut.
    An ellipsis appears only when no usable boundary exists (#64). No
    meaning is invented — the label IS the transcript text.
    """
    clean = " ".join(text.split())
    if len(clean) <= _TIMELINE_LABEL_MAX_CHARS:
        return clean

    # Last clause boundary inside the budget: punctuation followed by a
    # space (so "5:00" or "U.S." interiors never match).
    window = clean[: _TIMELINE_LABEL_MAX_CHARS + 1]
    boundary_chars = _LABEL_SENTENCE_ENDERS + _LABEL_SOFT_BREAKS
    for i in range(len(window) - 2, _LABEL_MIN_CUT - 1, -1):
        if window[i] in boundary_chars and window[i + 1] == " ":
            label = clean[: i + 1]
            return label.rstrip(_LABEL_SOFT_BREAKS)

    # No boundary: whole words up to the budget, ellipsis marks the cut.
    words = clean.split()
    label = ""
    for word in words:
        candidate = f"{label} {word}" if label else word
        if len(candidate) > _TIMELINE_LABEL_MAX_CHARS:
            break
        label = candidate
    if not label and words:
        # A single word longer than the budget: hard-truncate rather than
        # emit an empty label.
        label = words[0][:_TIMELINE_LABEL_MAX_CHARS]
    return label + "…"


def _timeline_markers(paragraphs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pick the coarse-interval marker paragraphs.

    Shared by the jump rail and the body's section landmarks so the
    rule-and-timestamp rhythm in the text matches the stops the rail
    navigates (identity-comparable: the returned dicts ARE items of
    *paragraphs*).
    """
    if not paragraphs:
        return []
    duration = max(float(p["end"]) for p in paragraphs)
    interval = _timeline_interval(duration)
    markers = [paragraphs[0]]
    threshold = interval
    while threshold < duration:
        marker = next((p for p in paragraphs if float(p["start"]) >= threshold), None)
        if marker is not None and marker is not markers[-1]:
            markers.append(marker)
        threshold += interval
    return markers


def build_timeline_nav(segments: list[dict[str, Any]], sentences_per_para: int = 5) -> str:
    """Build a compact time-based landmark rail for a keyless transcript."""
    paragraphs = segments_to_paragraphs(segments, sentences_per_para)
    if not paragraphs:
        return ""

    markers = _timeline_markers(paragraphs)

    # Stop #1 is always t=0, whose opening words are frequently boilerplate
    # (sponsor reads, intro jingles) — the least useful label on the page.
    # "Start" is deterministic and honest; content labels begin at stop #2.
    links = "\n".join(
        f'<a href="#{_time_anchor(float(p["start"]))}">'
        f'<span class="timeline-ts">{fmt_time(float(p["start"]))}</span>'
        f'<span class="timeline-snippet">'
        f"{'Start' if i == 0 else _esc(_timeline_label(p['text']))}</span>"
        f"</a>"
        for i, p in enumerate(markers)
    )
    return (
        '<nav class="timeline-nav" aria-label="Transcript timeline">\n'
        '  <span class="timeline-label">Jump to</span>\n'
        f'  <span class="timeline-links">\n{links}\n  </span>\n'
        "</nav>"
    )


_READING_WPM = 200


def _byline(segments: list[dict[str, Any]], source: str) -> str:
    """Reader-facing byline: duration, word count, reading time, provenance.

    Everything is derived from data already in hand at render time (#58).
    Word counts are rounded to honest precision (captions are approximate);
    an empty artifact keeps the provenance-only line it always had.
    """
    parts: list[str] = []
    if segments:
        minutes = max(1, round(max(float(s["end"]) for s in segments) / 60))
        hours, rem = divmod(minutes, 60)
        if hours and rem:
            parts.append(f"{hours} hr {rem} min of audio")
        elif hours:
            parts.append(f"{hours} hr of audio")
        else:
            parts.append(f"{minutes} min of audio")
        words = sum(len(s["text"].split()) for s in segments)
        if words >= 1000:
            parts.append(f"~{round(words, -2):,} words")
        elif words >= 100:
            parts.append(f"~{round(words, -1):,} words")
        elif words:
            parts.append(f"{words} words")
        if words:
            parts.append(f"about {max(1, round(words / _READING_WPM))} min to read")
    parts.append(f"Auto-transcribed with {source}")
    return " &middot; ".join(parts)


def build_chapter_body(
    segments: list[dict[str, Any]],
    chapters: list[dict[str, Any]],
    sentences_per_para: int = 5,
) -> str:
    """Build main content with chapter sections, using themed paragraph breaks when available."""
    if not chapters:
        paragraphs = segments_to_paragraphs(segments, sentences_per_para)
        # Section landmarks share the rail's marker computation (issue #59):
        # a quiet rule + timestamp gives the eye the same coarse sections the
        # rail navigates. The first marker is skipped — it sits directly
        # under the masthead.
        landmark_ids = {id(p) for p in _timeline_markers(paragraphs)[1:]}
        parts = []
        last_speaker: str | None = None
        for p in paragraphs:
            ts = fmt_time(p["start"])
            if id(p) in landmark_ids:
                # The landmark carries the rail's label so sections have
                # names, not a bare duplicate of the paragraph chip (#64).
                parts.append(
                    f'<div class="landmark"><span class="landmark-ts">{ts}</span>'
                    f'<span class="landmark-label">{_esc(_timeline_label(p["text"]))}</span></div>'
                )
            prefix = _speaker_prefix(p, last_speaker)
            last_speaker = p.get("speaker")
            anchor = _time_anchor(float(p["start"]))
            attrs = f' id="{anchor}" data-start="{p["start"]:.3f}" data-end="{p["end"]:.3f}"'
            parts.append(f'<p{attrs}>{prefix}<span class="ts">{ts}</span> {_esc(p["text"])}</p>')
        return "\n".join(parts)

    sorted_chapters = sorted(chapters, key=lambda c: c["start"])
    parts = []
    last_speaker = None  # tracked across chapters: label only at changes

    for i, ch in enumerate(sorted_chapters):
        anchor = _slug(ch["title"])
        key_points = ch.get("key_points", [])
        has_gutter = bool(key_points)
        section_class = f"chapter-section type-{ch['type']}"
        if not has_gutter:
            section_class += " no-gutter"
        label = TYPE_LABELS.get(ch["type"])
        badge_html = f' <span class="badge badge-{ch["type"]}">{label}</span>' if label else ""
        sec_start = f"{ch['start']:.3f}"
        parts.append(f'<section id="{anchor}" class="{section_class}" data-start="{sec_start}">')
        parts.append('<div class="chapter-main">')
        parts.append(
            f'<h2><span class="ts">{fmt_time(ch["start"])}</span> '
            f"{_esc(ch['title'])}{badge_html}</h2>"
        )
        parts.append(
            '<div class="chapter-abstract">\n'
            '<h3 class="chapter-abstract-heading">Summary</h3>\n'
            f"<p>{_esc(ch['abstract'])}</p>\n"
            "</div>"
        )

        # Collect segments belonging to this chapter
        ch_end = sorted_chapters[i + 1]["start"] if i + 1 < len(sorted_chapters) else float("inf")
        ch_segments = [s for s in segments if s["start"] >= ch["start"] and s["start"] < ch_end]

        # Use themed breaks if available, otherwise fall back to sentence counting
        breaks = ch.get("paragraph_breaks")
        if breaks:
            paragraphs = segments_to_paragraphs_themed(ch_segments, breaks)
        else:
            paragraphs = segments_to_paragraphs(ch_segments, sentences_per_para)

        pull_quote = ch.get("pull_quote")
        pull_quote_start = ch.get("pull_quote_start")
        pull_quote_applied = not pull_quote  # skip if no quote

        for p in paragraphs:
            ts = fmt_time(p["start"])
            text = _esc(p["text"])

            # Bold the pull quote text inline within the matching paragraph.
            # Matched on the raw text, wrapped on the escaped text: escaping
            # is a per-character substitution, so a raw substring match
            # guarantees the escaped quote appears in the escaped paragraph.
            if (
                not pull_quote_applied
                and pull_quote is not None
                and pull_quote_start is not None
                and p["start"] >= pull_quote_start
                and pull_quote in p["text"]
            ):
                quote = _esc(pull_quote)
                text = text.replace(quote, f"<strong>{quote}</strong>", 1)
                pull_quote_applied = True

            prefix = _speaker_prefix(p, last_speaker)
            last_speaker = p.get("speaker")
            attrs = f' data-start="{p["start"]:.3f}" data-end="{p["end"]:.3f}"'
            parts.append(f'<p{attrs}>{prefix}<span class="ts">{ts}</span> {text}</p>')

        parts.append("</div>")  # close chapter-main

        # Key points in right gutter
        if has_gutter:
            items = "\n".join(f"<li>{_esc(point)}</li>" for point in key_points)
            parts.append(
                '<div class="chapter-gutter">\n'
                '<div class="key-points">\n'
                '<h3 class="key-points-heading">Key Points</h3>\n'
                f"<ul>\n{items}\n</ul>\n"
                "</div>\n"
                "</div>"
            )

        parts.append("</section>")

    return "\n".join(parts)


# CSS and JS are kept as plain strings (no f-string) to avoid brace escaping issues.
_LEGACY_REMOTE_FONT_IMPORT = (
    b"@import url('https://fonts.googleapis.com/css2?"
    b"family=Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,600;"
    b"0,8..60,700;1,8..60,400&family=JetBrains+Mono:wght@400;600&"
    b"family=Oswald:wght@400;500;600&display=swap');"
)
_LEGACY_REMOTE_FONT_MARKER = b"<style>\n" + _LEGACY_REMOTE_FONT_IMPORT + b"\n\n"


def without_legacy_remote_font_import(document: bytes) -> bytes:
    """Remove the one remote import emitted by pre-#81 transcript artifacts.

    The exact-match rewrite keeps every other stored artifact byte intact while
    ensuring both engine reader surfaces share the no-third-party-font policy.
    """
    return document.replace(_LEGACY_REMOTE_FONT_MARKER, b"<style>\n", 1)


_STYLESHEET = """\
:root {
  --bg: #111318;
  --bg-warm: #14161c;
  --surface: #1c1f28;
  --surface-hover: #252935;
  --border: #2a2e3a;
  --text: #c8cad0;
  --text-bright: #e8eaef;
  --muted: #6b7084;
  --accent: #d4a04a;
  --accent-dim: #9a7535;
  --accent-glow: rgba(212, 160, 74, 0.12);
  --search-edge: #9a7535;
  --link: #5ba4cf;
  --link-hover: #7ec4f0;
  --red: #c0503a;
  --green: #5a9a6a;
  --purple: #8a6abf;
  --section-badge-bg: #31465a;
  --section-badge-text: #f2f6f8;
  --sidebar-w: 280px;
  --reading-column: 68ch;
}

/* Explicit app themes always win over the standalone OS-preference fallback. */
:root[data-theme='dark'] {
  --bg: #111318;
  --bg-warm: #14161c;
  --surface: #1c1f28;
  --surface-hover: #252935;
  --border: #2a2e3a;
  --text: #c8cad0;
  --text-bright: #e8eaef;
  --muted: #6b7084;
  --accent: #d4a04a;
  --accent-dim: #9a7535;
  --accent-glow: rgba(212, 160, 74, 0.12);
  --search-edge: #9a7535;
  --link: #5ba4cf;
  --link-hover: #7ec4f0;
  --red: #c0503a;
  --green: #5a9a6a;
  --purple: #8a6abf;
  --section-badge-bg: #31465a;
  --section-badge-text: #f2f6f8;
}

/* Warm-paper light palette, matching the desktop app's light theme. */
:root[data-theme='light'] {
  --bg: #f7f4ee;
  --bg-warm: #f1ede5;
  --surface: #ffffff;
  --surface-hover: #f1ede5;
  --border: #e3ddd2;
  --text: #3a342c;
  --text-bright: #20201d;
  --muted: #6b6157;
  --accent: #9a3b2e;
  --accent-dim: #b8705f;
  --accent-glow: rgba(154, 59, 46, 0.1);
  --search-edge: #a45b4a;
  --link: #2a6f97;
  --link-hover: #1f5675;
  --red: #b23a26;
  --green: #2f7d45;
  --purple: #6b4fa0;
  --section-badge-bg: #d8e6ed;
  --section-badge-text: #29495a;
}

@media (prefers-color-scheme: light) {
  :root:not([data-theme='dark']) {
    --bg: #f7f4ee;
    --bg-warm: #f1ede5;
    --surface: #ffffff;
    --surface-hover: #f1ede5;
    --border: #e3ddd2;
    --text: #3a342c;
    --text-bright: #20201d;
    --muted: #6b6157;
    --accent: #9a3b2e;
    --accent-dim: #b8705f;
    --accent-glow: rgba(154, 59, 46, 0.1);
    --search-edge: #a45b4a;
    --link: #2a6f97;
    --link-hover: #1f5675;
    --red: #b23a26;
    --green: #2f7d45;
    --purple: #6b4fa0;
    --section-badge-bg: #d8e6ed;
    --section-badge-text: #29495a;
  }
}

* { margin: 0; padding: 0; box-sizing: border-box; }

html { scroll-behavior: smooth; scroll-padding-top: 4rem; }

body {
  font-family: 'Source Serif 4', 'Georgia', serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.85;
  font-size: 16px;
  display: flex;
  min-height: 100vh;
}

/* ---- SIDEBAR ---- */
#sidebar {
  position: fixed;
  top: 0;
  left: 0;
  width: var(--sidebar-w);
  height: 100vh;
  background: var(--bg-warm);
  border-right: 1px solid var(--border);
  overflow-y: auto;
  z-index: 100;
  scrollbar-width: thin;
  scrollbar-color: var(--border) transparent;
}
.sidebar-inner {
  padding: 1.5rem 0;
}
.nav-item {
  display: block;
  padding: 0.6rem 1.2rem;
  text-decoration: none;
  border-left: 3px solid transparent;
  transition: all 0.15s ease;
  color: var(--muted);
}
.nav-item:hover {
  background: var(--surface);
  color: var(--text-bright);
  border-left-color: var(--accent-dim);
}
.nav-item.active {
  background: var(--accent-glow);
  color: var(--accent);
  border-left-color: var(--accent);
}
.nav-ts {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.7rem;
  display: block;
  color: var(--accent-dim);
  margin-bottom: 0.1rem;
  letter-spacing: 0.03em;
}
.nav-item.active .nav-ts { color: var(--accent); }
.nav-title {
  font-family: 'Oswald', sans-serif;
  font-size: 0.82rem;
  font-weight: 500;
  letter-spacing: 0.02em;
  line-height: 1.3;
  display: block;
}
.nav-badge {
  font-size: 0.55rem;
  padding: 0.08rem 0.35rem;
  border-radius: 2px;
  text-transform: uppercase;
  font-weight: 600;
  letter-spacing: 0.06em;
  vertical-align: middle;
  margin-left: 0.3rem;
  font-family: 'JetBrains Mono', monospace;
}
.nav-badge-sponsor { background: rgba(90, 100, 50, 0.5); color: #a0a860; }
.nav-badge-intro, .nav-badge-outro {
  background: var(--section-badge-bg);
  color: var(--section-badge-text);
}
.nav-badge-housekeeping { background: rgba(80, 60, 100, 0.5); color: #9a85b5; }

/* ---- MAIN CONTENT ---- */
#content {
  flex: 1;
  min-width: 0;
  padding: 2.5rem 3rem 4rem;
}
/* The sidebar margin is paid only when a sidebar exists: keyless artifacts
   have no chapters, emit no sidebar, and center the reading column instead. */
.has-sidebar #content {
  margin-left: var(--sidebar-w);
}

header {
  margin-bottom: 2.5rem;
  margin-inline: auto;
  padding-bottom: 1.5rem;
  border-bottom: 2px solid var(--accent);
  max-width: var(--reading-column);
}
h1 {
  font-family: 'Oswald', sans-serif;
  font-size: 2rem;
  font-weight: 600;
  color: var(--text-bright);
  letter-spacing: 0.01em;
  line-height: 1.2;
  margin-bottom: 0.4rem;
}
.meta {
  font-family: 'JetBrains Mono', monospace;
  color: var(--muted);
  font-size: 0.75rem;
  letter-spacing: 0.04em;
}

/* ---- KEYLESS TIMELINE ---- */
.timeline-nav {
  position: sticky;
  top: var(--transcript-search-height, 0px);
  z-index: 20;
  display: flex;
  align-items: center;
  gap: 0.8rem;
  margin: -1rem 0 2rem;
  padding: 0.55rem 0.75rem;
  background: var(--bg-warm);
  border: 1px solid var(--border);
  border-radius: 3px;
  /* Tight shadow: the solid rail occludes a passing line crisply instead of
     fading it out early under a long blur. */
  box-shadow: 0 0.15rem 0.35rem var(--bg);
}
.timeline-label {
  flex: none;
  color: var(--muted);
  font-family: 'Oswald', sans-serif;
  font-size: 0.72rem;
  font-weight: 500;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}
.timeline-links {
  display: flex;
  flex: 1 1 auto;
  /* Wrap, never scroll: with 3-5 stops, two quiet rows beat stops hidden
     behind an invisible scrollbar or glyphs clipped at the container edge. */
  flex-wrap: wrap;
  gap: 0.1rem 0.35rem;
  min-width: 0;
}
.timeline-links a {
  flex: none;
  display: inline-flex;
  align-items: baseline;
  gap: 0.4rem;
  /* Two capped links fit per wrapped row inside the reading column. */
  max-width: 13rem;
  padding: 0.12rem 0.45rem;
  border-radius: 2px;
}
.timeline-links a:hover,
.timeline-links a:focus-visible {
  background: var(--accent-glow);
}
.timeline-ts {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.68rem;
  color: var(--accent-dim);
  letter-spacing: 0.02em;
}
.timeline-snippet {
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: 0.78rem;
  color: var(--muted);
}
.timeline-links a:hover .timeline-ts,
.timeline-links a:focus-visible .timeline-ts { color: var(--accent); }
.timeline-links a:hover .timeline-snippet,
.timeline-links a:focus-visible .timeline-snippet { color: var(--text-bright); }
/* Collapsed sticky state (#63): once scrolled, the rail shrinks to compact
   timestamp chips — every stop stays tappable while occluding less text.
   Toggled by the rail script; without JS the rail simply stays expanded. */
.timeline-nav.stuck { padding: 0.4rem 0.75rem; }
.timeline-nav.stuck .timeline-snippet { display: none; }

/* ---- KEYLESS SECTION LANDMARKS ---- */
/* Quiet structural rhythm at the rail's marker boundaries: same visual
   language as a chapter heading's top rule, minus the heading. */
.landmark {
  border-top: 1px solid var(--border);
  margin: 2.4rem 0 1.3rem;
  padding-top: 0.45rem;
}
.landmark-ts {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.68rem;
  color: var(--accent-dim);
  letter-spacing: 0.03em;
  user-select: none;
}
.landmark-label {
  font-family: 'Oswald', sans-serif;
  font-size: 0.8rem;
  font-weight: 500;
  letter-spacing: 0.02em;
  color: var(--muted);
  margin-left: 0.6rem;
}

/* ---- CHAPTER SECTIONS ---- */
main {
  margin-inline: auto;
  max-width: var(--reading-column);
}
.chapter-section {
  position: relative;
  margin-bottom: 3rem;
  scroll-margin-top: 1.5rem;
}
.chapter-main {
  min-width: 0;
}
.chapter-gutter {
  position: absolute;
  left: calc(100% + 2.5rem);
  top: 2rem;
  width: 24rem;
}
.chapter-section h2 {
  font-family: 'Oswald', sans-serif;
  font-size: 1.35rem;
  font-weight: 500;
  color: var(--text-bright);
  margin-bottom: 0.4rem;
  padding-top: 1.5rem;
  border-top: 1px solid var(--border);
  letter-spacing: 0.01em;
}
.chapter-section:first-child h2 {
  border-top: none;
  padding-top: 0;
}
.chapter-abstract {
  color: var(--muted);
  font-style: italic;
  font-size: 0.92rem;
  margin-bottom: 1.2rem;
  line-height: 1.55;
  border-left: 2px solid var(--accent-dim);
  padding-left: 1rem;
}
.chapter-abstract p {
  margin-bottom: 0;
}
.chapter-abstract-heading {
  font-family: 'Oswald', sans-serif;
  font-size: 0.7rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--accent-dim);
  margin-bottom: 0.3rem;
  font-style: normal;
}
/* ---- BODY TEXT ---- */
p {
  margin-bottom: 1.3rem;
  text-align: left;
  hyphens: auto;
}
.ts {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.68rem;
  color: var(--accent);
  background: var(--surface);
  padding: 0.12rem 0.4rem;
  border-radius: 2px;
  margin-right: 0.5rem;
  user-select: none;
  vertical-align: middle;
  letter-spacing: 0.02em;
}

/* ---- BADGES ---- */
.badge {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.6rem;
  padding: 0.1rem 0.4rem;
  border-radius: 2px;
  text-transform: uppercase;
  font-weight: 600;
  letter-spacing: 0.06em;
  vertical-align: middle;
  margin-left: 0.5rem;
}
.badge-sponsor { background: rgba(90, 100, 50, 0.4); color: #b8c060; }
.badge-intro, .badge-outro {
  background: var(--section-badge-bg);
  color: var(--section-badge-text);
}
.badge-housekeeping { background: rgba(80, 60, 100, 0.4); color: #a890c0; }

/* ---- LINKS ---- */
a { color: var(--link); text-decoration: none; transition: color 0.15s; }
a:hover { color: var(--link-hover); }

/* ---- FOOTER ---- */
footer {
  margin-top: 4rem;
  margin-inline: auto;
  padding-top: 1.2rem;
  border-top: 1px solid var(--border);
  color: var(--muted);
  font-size: 0.75rem;
  text-align: center;
  font-family: 'JetBrains Mono', monospace;
  max-width: var(--reading-column);
}
.chapters-note {
  margin-top: 0.55rem;
  line-height: 1.55;
}
.cleanup-note {
  margin-top: 0.55rem;
  line-height: 1.55;
}

/* ---- KEY POINTS (gutter) ---- */
.key-points {
  border-left: 2px solid var(--accent);
  padding-left: 1.2rem;
}
.key-points-heading {
  font-family: 'Oswald', sans-serif;
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--accent);
  margin-bottom: 0.6rem;
}
.key-points ul {
  list-style: none;
  padding: 0;
}
.key-points li {
  position: relative;
  padding-left: 1.1rem;
  margin-bottom: 0.45rem;
  font-size: 0.88rem;
  line-height: 1.5;
  color: var(--text-bright);
}
.key-points li::before {
  content: '';
  position: absolute;
  left: 0;
  top: 0.55em;
  width: 5px;
  height: 5px;
  background: var(--accent);
  border-radius: 50%;
}

/* ---- MEDIA SYNC (active passage highlight; inert without a host player) ---- */
.sync-active {
  background: rgba(123, 155, 229, 0.18);
  border-radius: 4px;
  box-shadow: inset 0 -3px 0 var(--link);
  transition: background 0.2s ease, box-shadow 0.2s ease;
}

/* ---- IN-TRANSCRIPT SEARCH ---- */
.transcript-search {
  position: sticky;
  top: 0;
  z-index: 30;
  max-width: var(--reading-column);
  margin: 0 auto 1rem;
  padding: 0.4rem;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 4px;
}
.transcript-search button,
.transcript-search input {
  min-height: 44px;
  color: var(--text-bright);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 3px;
}
.transcript-search button { padding: 0.55rem 0.75rem; cursor: pointer; }
.transcript-search button:focus-visible,
.transcript-search input:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.transcript-search-toggle { width: 100%; text-align: left; }
.transcript-search-panel[hidden] { display: none; }
.transcript-search-panel { display: grid; gap: 0.45rem; }
.transcript-search-label { font-family: 'Oswald', sans-serif; color: var(--text-bright); }
.transcript-search-row {
  display: grid;
  grid-template-columns: minmax(10rem, 1fr) repeat(4, auto);
  gap: 0.4rem;
}
.transcript-search-input { width: 100%; min-width: 0; padding: 0.55rem 0.7rem; }
.transcript-search-status { min-height: 1.3rem; color: var(--muted); font-size: 0.82rem; }
.search-match {
  background: var(--accent-glow);
  border-left: 3px dashed var(--search-edge);
  border-radius: 4px;
}
.search-match-active {
  border-left-style: solid;
  border-left-color: var(--accent);
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
.search-match.sync-active {
  box-shadow: inset 0 -3px 0 var(--link);
}
.search-match-active.sync-active {
  box-shadow: inset 0 -3px 0 var(--link);
}
@media (prefers-reduced-motion: reduce) {
  html { scroll-behavior: auto; }
  .sync-active, .search-match, .search-match-active { transition: none; }
}

/* ---- RESPONSIVE ---- */
@media (max-width: 1700px) {
  .chapter-gutter { display: none; }
}
@media (max-width: 900px) {
  :root { --sidebar-w: 0px; }
  #sidebar { display: none; }
  #content { margin-left: 0; padding: 1.5rem; }
  .transcript-search-row { grid-template-columns: repeat(4, minmax(44px, 1fr)); }
  .transcript-search-input { grid-column: 1 / -1; }
  .transcript-search-row button { padding-inline: 0.45rem; }
  .transcript-search-label {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
    white-space: nowrap;
  }
  body.transcript-search-active .timeline-label { display: none; }
}
"""

# Appended to the stylesheet only when segments carry speakers, so
# speakerless output stays byte-identical to pre-diarization releases.
_SPEAKER_STYLESHEET = """\

/* ---- SPEAKER ATTRIBUTION (diarization) ---- */
.speaker {
  font-family: 'Oswald', sans-serif;
  font-size: 0.78rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--accent);
  display: block;
  margin-bottom: 0.15rem;
}
"""

_SEARCH_HTML = """\
<div class="transcript-search" role="search">
  <button type="button" class="transcript-search-toggle"
          aria-controls="transcript-search-panel" aria-expanded="false"
          aria-keyshortcuts="/">Find in transcript <span aria-hidden="true">/</span></button>
  <div id="transcript-search-panel" class="transcript-search-panel" hidden>
    <label class="transcript-search-label" for="transcript-search-input">Find in transcript</label>
    <div class="transcript-search-row">
      <input id="transcript-search-input" class="transcript-search-input" type="search"
             autocomplete="off" spellcheck="false" autocorrect="off"
             autocapitalize="none" inputmode="search" placeholder="Find in transcript">
      <button type="button" class="transcript-search-prev"
              aria-label="Previous match" disabled>&uarr;</button>
      <button type="button" class="transcript-search-next"
              aria-label="Next match" disabled>&darr;</button>
      <button type="button" class="transcript-search-read"
              aria-label="Read current passage" disabled>Read</button>
      <button type="button" class="transcript-search-clear"
              aria-label="Clear and close search">Clear</button>
    </div>
    <p class="transcript-search-status" role="status" aria-live="polite"></p>
  </div>
</div>
"""

_SCROLL_SCRIPT = """\
document.addEventListener('DOMContentLoaded', function() {
  var sections = document.querySelectorAll('.chapter-section');
  var navItems = document.querySelectorAll('.nav-item');
  if (!sections.length || !navItems.length) return;

  var observer = new IntersectionObserver(function(entries) {
    entries.forEach(function(entry) {
      if (entry.isIntersecting) {
        navItems.forEach(function(n) { n.classList.remove('active'); });
        var active = document.querySelector('.nav-item[data-section="' + entry.target.id + '"]');
        if (active) {
          active.classList.add('active');
          active.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        }
      }
    });
  }, { rootMargin: '-10% 0px -80% 0px', threshold: 0 });

  sections.forEach(function(s) { observer.observe(s); });
});
"""

# Keyless jump-rail geometry (#63). A fixed scroll-padding cannot track a
# variable-height sticky rail (the #57 wrap made its height depend on stop
# count and viewport width; the 4rem constant left jump targets 11-39px
# under it). This script keeps two invariants:
#   1. scroll-padding-top always equals the rail's CURRENT height + a gap,
#      re-measured on load, resize, and stuck-state changes — so an anchor
#      jump can never land the target under the rail. (Collapse only ever
#      SHRINKS the rail mid-scroll, so a jump computed against the taller
#      at-rest measurement still clears it.)
#   2. Once scrolled (a zero-height sentinel above the rail leaves the
#      viewport), the rail collapses to a compact timestamps-only state
#      (.stuck hides snippets) — less mid-scroll text occlusion.
# The static 4rem in the stylesheet stays as the no-JS fallback.
_RAIL_SCRIPT_V1 = """\
(function() {
  var rail = document.querySelector('.timeline-nav');
  if (!rail) return;
  var sentinel = document.createElement('div');
  rail.parentNode.insertBefore(sentinel, rail);
  function pad() {
    document.documentElement.style.scrollPaddingTop = (rail.offsetHeight + 8) + 'px';
  }
  var stuck = false;
  function setStuck(v) {
    if (v === stuck) return;
    stuck = v;
    rail.classList.toggle('stuck', v);
    pad();
  }
  if ('IntersectionObserver' in window) {
    new IntersectionObserver(function(entries) {
      setStuck(!entries[0].isIntersecting);
    }).observe(sentinel);
  }
  window.addEventListener('resize', pad);
  pad();
})();
"""

_RAIL_SCRIPT = """\
(function() {
  var rail = document.querySelector('.timeline-nav');
  if (!rail) return;
  var sentinel = document.createElement('div');
  rail.parentNode.insertBefore(sentinel, rail);
  var stuck = false;
  function notify() { window.dispatchEvent(new Event('pr-rail-layout')); }
  function setStuck(v) {
    if (v === stuck) return;
    stuck = v;
    rail.classList.toggle('stuck', v);
    notify();
  }
  if ('IntersectionObserver' in window) {
    new IntersectionObserver(function(entries) {
      setStuck(!entries[0].isIntersecting);
    }).observe(sentinel);
  }
  window.addEventListener('resize', notify);
  notify();
})();
"""

# Bidirectional transcript<->media sync (media-playback). Inert when the
# artifact is opened standalone (no parent player): the very first guard
# returns, so a directly-opened file behaves exactly as before. Inside the
# app's Reader the artifact runs in an opaque-origin sandboxed iframe, so it
# can only reach the host player over postMessage. Active-passage selection is
# gap-free: the current passage is the last `[data-start]` element whose start
# is <= the playback position, so silence between passages never drops the
# highlight (per design F6). The channel tag `pr-sync` lets the host
# distinguish these from the YouTube iframe's own control messages.
_SYNC_SCRIPT_V1 = """\
(function() {
  if (window.parent === window) return;
  var CH = 'pr-sync';
  // Theme: the host (Reader) posts the resolved app theme so the transcript
  // matches light/dark live. Registered before the passage logic so it works
  // even for a transcript with no sync targets.
  window.addEventListener('message', function(e) {
    var d = e.data;
    if (d && d.ch === 'pr-theme' && (d.theme === 'light' || d.theme === 'dark')) {
      document.documentElement.dataset.theme = d.theme;
    }
  });
  // Only <p> passages are sync targets — NOT the chapter <section> containers
  // (which also carry data-start as anchors). Including a section would let the
  // highlight/seek land on a whole-chapter container instead of a passage.
  var nodes = Array.prototype.slice.call(document.querySelectorAll('p[data-start]'));
  var items = nodes.map(function(el) {
    return { el: el, start: parseFloat(el.getAttribute('data-start')) };
  }).filter(function(it) { return !isNaN(it.start); });
  if (!items.length) return;
  items.sort(function(a, b) { return a.start - b.start; });

  items.forEach(function(it) {
    it.el.style.cursor = 'pointer';
    it.el.addEventListener('click', function(e) {
      e.stopPropagation();
      window.parent.postMessage({ ch: CH, type: 'seek', t: it.start }, '*');
    });
  });

  var active = null;
  function highlight(t) {
    var found = null;
    for (var i = 0; i < items.length; i++) {
      if (items[i].start <= t) { found = items[i]; } else { break; }
    }
    if (found === active) return;
    if (active) active.el.classList.remove('sync-active');
    active = found;
    if (active) {
      active.el.classList.add('sync-active');
      active.el.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
  }

  window.addEventListener('message', function(e) {
    var d = e.data;
    if (!d || d.ch !== CH) return;
    if (d.type === 'time' && typeof d.t === 'number') highlight(d.t);
  });

  window.parent.postMessage({ ch: CH, type: 'ready' }, '*');
})();
"""

_SYNC_SCRIPT = """\
(function() {
  if (window.parent === window) return;
  var CH = 'pr-sync';
  var reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  window.addEventListener('message', function(e) {
    var d = e.data;
    if (d && d.ch === 'pr-theme' && (d.theme === 'light' || d.theme === 'dark')) {
      document.documentElement.dataset.theme = d.theme;
    }
  });
  var nodes = [];
  var visits = 0;
  var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
  while (walker.nextNode()) {
    visits += 1;
    if (visits > 100000) return;
    var node = walker.currentNode;
    if (node.matches('p[data-start]')) {
      nodes.push(node);
      if (nodes.length > 10000) return;
    }
  }
  var items = nodes.map(function(el) {
    return { el: el, start: parseFloat(el.getAttribute('data-start')) };
  }).filter(function(it) { return !isNaN(it.start); });
  if (!items.length) return;
  items.sort(function(a, b) { return a.start - b.start; });

  items.forEach(function(it) {
    it.el.style.cursor = 'pointer';
    it.el.addEventListener('click', function(e) {
      e.stopPropagation();
      window.parent.postMessage({ ch: CH, type: 'seek', t: it.start }, '*');
    });
  });

  var active = null;
  function highlight(t) {
    var found = null;
    for (var i = 0; i < items.length; i++) {
      if (items[i].start <= t) { found = items[i]; } else { break; }
    }
    if (found === active) return;
    if (active) active.el.classList.remove('sync-active');
    active = found;
    if (active) {
      active.el.classList.add('sync-active');
      if (!document.body.classList.contains('transcript-search-active')) {
        active.el.scrollIntoView({
          block: 'center',
          behavior: reduced.matches ? 'auto' : 'smooth'
        });
      }
    }
  }

  window.addEventListener('message', function(e) {
    var d = e.data;
    if (!d || d.ch !== CH) return;
    if (d.type === 'time' && typeof d.t === 'number') highlight(d.t);
  });

  window.parent.postMessage({ ch: CH, type: 'ready' }, '*');
})();
"""

_SEARCH_SCRIPT_V1 = """\
(function() {
  var root = document.querySelector('.transcript-search');
  var content = document.querySelector('div#content');
  var main = content && content.querySelector(':scope > main');
  if (!root || !content || !main) return;
  var opener = root.querySelector('.transcript-search-toggle');
  var panel = root.querySelector('.transcript-search-panel');
  var input = root.querySelector('.transcript-search-input');
  var previous = root.querySelector('.transcript-search-prev');
  var next = root.querySelector('.transcript-search-next');
  var read = root.querySelector('.transcript-search-read');
  var clear = root.querySelector('.transcript-search-clear');
  var status = root.querySelector('.transcript-search-status');
  if (!opener || !panel || !input || !previous || !next || !read || !clear || !status) {
    if (status) status.textContent = 'This transcript cannot be searched.';
    return;
  }

  var MAX_PASSAGES = 10000;
  var MAX_PASSAGE_UNITS = 100000;
  var MAX_TOTAL_UNITS = 4000000;
  var MAX_VISITS = 100000;
  var generation = 0;
  var timer = null;
  var composing = false;
  var invalid = false;
  var invalidMessage = '';
  var index = [];
  var matches = [];
  var current = -1;
  var observerStarted = false;
  var ownedDepth = 0;
  var rail = document.querySelector('.timeline-nav');
  var sidebar = document.querySelector('#sidebar');
  var reduced = window.matchMedia('(prefers-reduced-motion: reduce)');

  function hiddenInChain(node) {
    for (var item = node; item; item = item.parentElement) {
      if (item.hidden || item.getAttribute('aria-hidden') === 'true') return true;
      var style = window.getComputedStyle(item);
      if (style.display === 'none' || style.visibility === 'hidden') return true;
      if (item === document.documentElement) break;
    }
    return false;
  }

  function exactAttributes(node, allowed) {
    var names = Array.prototype.map.call(node.attributes, function(attr) { return attr.name; });
    return names.every(function(name) { return allowed.indexOf(name) !== -1; });
  }

  function exactAttributeValues(node, expected) {
    var names = Object.keys(expected);
    return node.attributes.length === names.length && names.every(function(name) {
      return node.getAttribute(name) === expected[name];
    });
  }

  function canonicalDocument() {
    var htmlTheme = document.documentElement.getAttribute('data-theme');
    return document.documentElement.getAttribute('lang') === 'en' &&
      exactAttributes(document.documentElement, ['lang', 'data-theme', 'style']) &&
      (htmlTheme === null || htmlTheme === 'light' || htmlTheme === 'dark') &&
      exactAttributes(document.body, ['class']) &&
      Array.prototype.every.call(document.body.classList, function(name) {
        return name === 'has-sidebar' || name === 'transcript-search-active';
      }) &&
      exactAttributes(content, ['id']) && exactAttributes(main, []);
  }

  function canonicalSearchRoot() {
    var label = root.querySelector('.transcript-search-label');
    var row = root.querySelector('.transcript-search-row');
    var openerAttributes = ['type', 'class', 'aria-controls', 'aria-expanded', 'aria-keyshortcuts'];
    if (opener.hidden) openerAttributes.push('hidden');
    var panelAttributes = ['id', 'class'];
    if (panel.hidden) panelAttributes.push('hidden');
    var expanded = opener.getAttribute('aria-expanded') === 'true';
    var navigationDisabled = invalid || matches.length === 0;
    var buttonShape = function(button, className, label, canDisable) {
      var attributes = ['type', 'class', 'aria-label'];
      if (canDisable && button.disabled) attributes.push('disabled');
      return exactAttributes(button, attributes) && button.type === 'button' &&
        button.getAttribute('class') === className && button.getAttribute('aria-label') === label;
    };
    return root.parentElement === content && root.children.length === 2 &&
      exactAttributeValues(root, { 'class': 'transcript-search', 'role': 'search' }) &&
      opener.parentElement === root && exactAttributes(opener, openerAttributes) &&
      opener.type === 'button' && opener.getAttribute('class') === 'transcript-search-toggle' &&
      opener.getAttribute('aria-controls') === 'transcript-search-panel' &&
      opener.getAttribute('aria-keyshortcuts') === '/' &&
      (opener.getAttribute('aria-expanded') === 'true' ||
        opener.getAttribute('aria-expanded') === 'false') &&
      panel.hidden === !expanded && opener.hidden === expanded &&
      panel.parentElement === root && label && label.parentElement === panel &&
      panel.children.length === 3 && exactAttributes(panel, panelAttributes) &&
      panel.id === 'transcript-search-panel' && panel.className === 'transcript-search-panel' &&
      exactAttributeValues(label,
        { 'class': 'transcript-search-label', 'for': 'transcript-search-input' }) &&
      row && row.parentElement === panel && status.parentElement === panel &&
      row.children.length === 5 &&
      exactAttributeValues(row, { 'class': 'transcript-search-row' }) &&
      input.parentElement === row && previous.parentElement === row && next.parentElement === row &&
      read.parentElement === row && clear.parentElement === row &&
      exactAttributeValues(input, { 'id': 'transcript-search-input',
        'class': 'transcript-search-input', 'type': 'search', 'autocomplete': 'off',
        'spellcheck': 'false', 'autocorrect': 'off', 'autocapitalize': 'none',
        'inputmode': 'search', 'placeholder': 'Find in transcript' }) &&
      buttonShape(previous, 'transcript-search-prev', 'Previous match', true) &&
      buttonShape(next, 'transcript-search-next', 'Next match', true) &&
      buttonShape(read, 'transcript-search-read', 'Read current passage', true) &&
      buttonShape(clear, 'transcript-search-clear', 'Clear and close search', false) &&
      previous.disabled === navigationDisabled && next.disabled === navigationDisabled &&
      read.disabled === navigationDisabled &&
      exactAttributeValues(status,
        { 'class': 'transcript-search-status', 'role': 'status', 'aria-live': 'polite' }) &&
      root.querySelectorAll('.transcript-search-toggle').length === 1 &&
      root.querySelectorAll('.transcript-search-panel').length === 1 &&
      root.querySelectorAll('.transcript-search-input').length === 1 &&
      root.querySelectorAll('.transcript-search-prev').length === 1 &&
      root.querySelectorAll('.transcript-search-next').length === 1 &&
      root.querySelectorAll('.transcript-search-read').length === 1 &&
      root.querySelectorAll('.transcript-search-clear').length === 1 &&
      root.querySelectorAll('.transcript-search-status').length === 1;
  }

  function canonicalPassageChildren(passage) {
    var children = Array.prototype.slice.call(passage.childNodes);
    var position = 0;
    if (children[position] && children[position].nodeType === Node.ELEMENT_NODE &&
        children[position].getAttribute('class') === 'speaker') {
      var speakerNode = children[position];
      if (speakerNode.tagName !== 'SPAN' || !exactAttributes(speakerNode, ['class']) ||
          speakerNode.childNodes.length !== 1 ||
          speakerNode.firstChild.nodeType !== Node.TEXT_NODE) return false;
      position += 1;
      if (!children[position] || children[position].nodeType !== Node.TEXT_NODE ||
          children[position].data !== ' ') return false;
      position += 1;
    }
    var timestampNode = children[position];
    if (!timestampNode || timestampNode.nodeType !== Node.ELEMENT_NODE ||
        timestampNode.tagName !== 'SPAN' || timestampNode.getAttribute('class') !== 'ts' ||
        !exactAttributes(timestampNode, ['class']) || timestampNode.childNodes.length !== 1 ||
        timestampNode.firstChild.nodeType !== Node.TEXT_NODE ||
        !/^\\d{2}:\\d{2}:\\d{2}$/.test(timestampNode.textContent)) return false;
    position += 1;
    var tail = children.slice(position);
    var strongCount = 0;
    if (!tail.length || tail[0].nodeType !== Node.TEXT_NODE || tail[0].data.charAt(0) !== ' ') {
      return false;
    }
    return tail.every(function(child) {
      if (child.nodeType === Node.TEXT_NODE) return true;
      if (child.nodeType !== Node.ELEMENT_NODE || child.tagName !== 'STRONG' ||
          !exactAttributes(child, []) || child.childNodes.length !== 1 ||
          child.firstChild.nodeType !== Node.TEXT_NODE) return false;
      strongCount += 1;
      return strongCount <= 1;
    });
  }

  function canonicalPassage(passage) {
    if (!canonicalDocument() || passage.tagName !== 'P' || !passage.hasAttribute('data-start') ||
        !passage.hasAttribute('data-end') || hiddenInChain(passage)) return false;
    if (!/^\\d+\\.\\d{3}$/.test(passage.getAttribute('data-start')) ||
        !/^\\d+\\.\\d{3}$/.test(passage.getAttribute('data-end'))) return false;
    if (!allowedPassageClass(passage) || !canonicalPassageChildren(passage)) return false;
    var ariaCurrent = passage.getAttribute('aria-current');
    var tabIndex = passage.getAttribute('tabindex');
    var styleValid = !passage.hasAttribute('style') ||
      (passage.style.length === 1 && passage.style.cursor === 'pointer');
    if ((ariaCurrent !== null && ariaCurrent !== 'location') ||
        (tabIndex !== null && tabIndex !== '-1') || !styleValid) return false;
    var parent = passage.parentElement;
    var dynamic = ['class', 'aria-current', 'tabindex', 'style'];
    var keyless = parent === main && /^t-\\d+$/.test(passage.getAttribute('id') || '') &&
      exactAttributes(passage, ['id', 'data-start', 'data-end'].concat(dynamic));
    var chaptered = parent && parent.matches('div.chapter-main') &&
      exactAttributes(parent, ['class']) &&
      parent.parentElement && parent.parentElement.matches('section.chapter-section') &&
      exactAttributes(parent.parentElement, ['id', 'class', 'data-start']) &&
      parent.parentElement.parentElement === main &&
      exactAttributes(passage, ['data-start', 'data-end'].concat(dynamic));
    return keyless || chaptered;
  }

  function canonicalChain(passage) {
    return canonicalPassage(passage) && main.parentElement === content &&
      content.parentElement === document.body &&
      document.body.parentElement === document.documentElement;
  }

  function buildIndex() {
    var visits = 0;
    var candidates = [];
    var walker = document.createTreeWalker(
      document.body,
      NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT
    );
    while (walker.nextNode()) {
      visits += 1;
      if (visits > MAX_VISITS) throw new Error('capacity');
      var node = walker.currentNode;
      if (node.nodeType === Node.ELEMENT_NODE && node.tagName === 'TEMPLATE') {
        throw new Error('shape');
      }
      if (node.nodeType === Node.ELEMENT_NODE && node.matches('p[data-start]')) {
        if (!canonicalPassage(node)) throw new Error('shape');
        candidates.push(node);
        if (candidates.length > MAX_PASSAGES) throw new Error('capacity');
      }
    }
    var totalRaw = 0;
    var totalNormalized = 0;
    var built = [];
    candidates.forEach(function(passage) {
      var pieces = [];
      var raw = 0;
      Array.prototype.forEach.call(passage.childNodes, function(child) {
        if (child.nodeType === Node.TEXT_NODE || child.tagName === 'STRONG') {
          var value = child.textContent || '';
          raw += value.length;
          totalRaw += value.length;
          if (raw > MAX_PASSAGE_UNITS || totalRaw > MAX_TOTAL_UNITS) throw new Error('capacity');
          pieces.push(value);
        }
      });
      var normalized = pieces.join('').normalize('NFKC').replace(/\\s+/g, ' ').trim().toLowerCase();
      totalNormalized += normalized.length;
      if (normalized.length > MAX_TOTAL_UNITS || totalNormalized > MAX_TOTAL_UNITS) {
        throw new Error('capacity');
      }
      var timestamp = passage.querySelector('.ts');
      built.push({
        el: passage,
        text: normalized,
        timestamp: timestamp ? timestamp.textContent.trim() : ''
      });
    });
    return built;
  }

  function layout() {
    beginOwnedMutations();
    var searchHeight = root.offsetHeight;
    document.documentElement.style.setProperty('--transcript-search-height', searchHeight + 'px');
    if (rail) rail.style.top = searchHeight + 'px';
    document.documentElement.style.scrollPaddingTop =
      (searchHeight + (rail ? rail.offsetHeight : 0) + 8) + 'px';
    acceptOwnedMutations();
  }

  function beginOwnedMutations() {
    if (ownedDepth === 0 && observerStarted) {
      var pending = mutationObserver.takeRecords();
      if (!invalid && pending.some(function(record) { return !allowedMutation(record); })) {
        forceInvalid('Transcript changed; reopen it to search.');
      }
    }
    ownedDepth += 1;
  }

  function acceptOwnedMutations() {
    ownedDepth -= 1;
    if (ownedDepth === 0 && observerStarted) {
      var records = mutationObserver.takeRecords();
      if (!invalid && records.some(function(record) { return !allowedOwnedMutation(record); })) {
        forceInvalid('Transcript changed; reopen it to search.');
      }
    }
  }

  function forceInvalid(message) {
    invalid = true;
    invalidMessage = message;
    generation += 1;
    if (timer !== null) window.clearTimeout(timer);
    timer = null;
    index.forEach(function(item) {
      item.el.classList.remove('search-match', 'search-match-active');
      item.el.removeAttribute('aria-current');
      item.el.removeAttribute('tabindex');
    });
    matches = [];
    current = -1;
    previous.disabled = true;
    next.disabled = true;
    read.disabled = true;
    document.body.classList.remove('transcript-search-active');
    status.textContent = message;
    if (opener.hidden || !panel.hidden) {
      panel.hidden = false;
      opener.hidden = true;
      opener.setAttribute('aria-expanded', 'true');
    }
    if (observerStarted) mutationObserver.takeRecords();
  }

  function setInvalid(message) {
    beginOwnedMutations();
    forceInvalid(message);
    acceptOwnedMutations();
  }

  function allowedPassageClass(node) {
    return Array.prototype.every.call(node.classList, function(name) {
      return name === 'sync-active' || name === 'search-match' || name === 'search-match-active';
    });
  }

  function allowedMutation(record) {
    var target = record.target.nodeType === Node.ELEMENT_NODE ?
      record.target : record.target.parentElement;
    if (!target) return false;
    if (rail && (target === rail || rail.contains(target))) return true;
    if (sidebar && (target === sidebar || sidebar.contains(target))) return true;
    if (target === document.documentElement) {
      if (record.attributeName === 'data-theme') {
        var theme = target.getAttribute('data-theme');
        return theme === null || theme === 'light' || theme === 'dark';
      }
    }
    if (target === document.body && record.attributeName === 'class') {
      return false;
    }
    if (target.tagName === 'P') {
      if (record.attributeName === 'class') {
        return coherentPassageState(target);
      }
    }
    return false;
  }

  function coherentPassageState(target) {
    var matchPosition = matches.findIndex(function(item) { return item.el === target; });
    var isMatch = matchPosition !== -1;
    var isActive = isMatch && matchPosition === current;
    return Array.prototype.every.call(target.classList, function(name) {
      return name === 'sync-active' || name === 'search-match' ||
        name === 'search-match-active';
    }) && target.classList.contains('search-match') === isMatch &&
      target.classList.contains('search-match-active') === isActive &&
      target.hasAttribute('aria-current') === isActive &&
      target.hasAttribute('tabindex') === isActive;
  }

  function allowedOwnedMutation(record) {
    var target = record.target.nodeType === Node.ELEMENT_NODE ?
      record.target : record.target.parentElement;
    if (!target) return false;
    if (allowedMutation(record)) return true;
    if (target === status || status.contains(target)) return canonicalSearchRoot();
    if (root.contains(target)) return record.type === 'attributes' && canonicalSearchRoot();
    if (target.tagName === 'P') return record.type === 'attributes' && coherentPassageState(target);
    if (target === document.body && record.attributeName === 'class') {
      var expected = document.body.classList.contains('has-sidebar') ?
        ['has-sidebar'] : [];
      if (matches.length && !invalid) expected.push('transcript-search-active');
      return Array.prototype.every.call(document.body.classList, function(name, index) {
        return name === expected[index];
      }) && document.body.classList.length === expected.length;
    }
    if (target === document.documentElement && record.attributeName === 'style') {
      return Array.prototype.every.call(target.style, function(name) {
        var value = target.style.getPropertyValue(name);
        return (name === 'scroll-padding-top' || name === '--transcript-search-height') &&
          /^\\d+(?:\\.\\d+)?px$/.test(value);
      });
    }
    return false;
  }

  var mutationObserver = new MutationObserver(function(records) {
    if (invalid) return;
    if (records.some(function(record) { return !allowedMutation(record); })) {
      setInvalid('Transcript changed; reopen it to search.');
    }
  });

  function cancelPending() {
    generation += 1;
    if (timer !== null) window.clearTimeout(timer);
    timer = null;
  }

  function clearResults() {
    beginOwnedMutations();
    index.forEach(function(item) {
      item.el.classList.remove('search-match', 'search-match-active');
      item.el.removeAttribute('aria-current');
      item.el.removeAttribute('tabindex');
    });
    matches = [];
    current = -1;
    previous.disabled = true;
    next.disabled = true;
    read.disabled = true;
    document.body.classList.remove('transcript-search-active');
    if (invalidMessage) status.textContent = invalidMessage;
    acceptOwnedMutations();
  }

  function updateStatus() {
    if (!matches.length || current < 0) return;
    var timestamp = matches[current].timestamp;
    status.textContent = (current + 1) + ' of ' + matches.length +
      (timestamp ? ' · ' + timestamp : '');
  }

  function activate(position, scroll) {
    if (invalid || !matches.length) return;
    var candidate = matches[((position % matches.length) + matches.length) % matches.length];
    if (!candidate || !canonicalChain(candidate.el)) {
      setInvalid('Transcript changed; reopen it to search.');
      return;
    }
    beginOwnedMutations();
    matches.forEach(function(item) {
      item.el.classList.remove('search-match-active');
      item.el.removeAttribute('aria-current');
      item.el.removeAttribute('tabindex');
    });
    current = matches.indexOf(candidate);
    candidate.el.classList.add('search-match-active');
    candidate.el.setAttribute('aria-current', 'location');
    candidate.el.setAttribute('tabindex', '-1');
    updateStatus();
    acceptOwnedMutations();
    if (scroll) candidate.el.scrollIntoView({
      block: 'center',
      behavior: reduced.matches ? 'auto' : 'smooth'
    });
  }

  function boundedQuery(value) {
    var compact = '';
    var pendingSpace = false;
    var compactCount = 0;
    var rawCount = 0;
    for (var iterator = value[Symbol.iterator](), step; !(step = iterator.next()).done;) {
      rawCount += 1;
      if (rawCount > 1000) return { compact: compact, tooLong: true };
      var character = step.value;
      if (/\\s/u.test(character)) {
        if (compact) pendingSpace = true;
      } else {
        if (pendingSpace) {
          compact += ' ';
          compactCount += 1;
        }
        compact += character;
        compactCount += 1;
        if (compactCount > 100) return { compact: compact, tooLong: true };
        pendingSpace = false;
      }
    }
    return { compact: compact, tooLong: false };
  }

  function run(query, expectedGeneration) {
    if (invalid || expectedGeneration !== generation) return;
    var bounded = boundedQuery(query);
    var compact = bounded.compact;
    if (!compact) {
      clearResults();
      beginOwnedMutations();
      status.textContent = '';
      acceptOwnedMutations();
      return;
    }
    if (bounded.tooLong) {
      clearResults();
      beginOwnedMutations();
      status.textContent = 'Search is limited to 100 characters.';
      acceptOwnedMutations();
      return;
    }
    var normalized = compact.normalize('NFKC').toLowerCase();
    var found = index.filter(function(item) { return item.text.indexOf(normalized) !== -1; });
    if (expectedGeneration !== generation) return;
    clearResults();
    beginOwnedMutations();
    matches = found;
    if (!matches.length) {
      status.textContent = 'No matches.';
      acceptOwnedMutations();
      return;
    }
    matches.forEach(function(item) { item.el.classList.add('search-match'); });
    document.body.classList.add('transcript-search-active');
    previous.disabled = false;
    next.disabled = false;
    read.disabled = false;
    activate(0, true);
    acceptOwnedMutations();
  }

  function schedule() {
    beginOwnedMutations();
    cancelPending();
    clearResults();
    if (invalid) {
      acceptOwnedMutations();
      return;
    }
    var expected = generation;
    var query = input.value;
    status.textContent = boundedQuery(query).compact ? 'Searching…' : '';
    acceptOwnedMutations();
    timer = window.setTimeout(function() {
      timer = null;
      run(query, expected);
    }, 150);
  }

  function openSearch() {
    beginOwnedMutations();
    panel.hidden = false;
    opener.hidden = true;
    opener.setAttribute('aria-expanded', 'true');
    status.textContent = invalidMessage;
    layout();
    input.focus();
    acceptOwnedMutations();
  }

  function dismiss(restoreFocus) {
    beginOwnedMutations();
    cancelPending();
    input.value = '';
    clearResults();
    status.textContent = invalidMessage;
    panel.hidden = true;
    opener.hidden = false;
    opener.setAttribute('aria-expanded', 'false');
    layout();
    if (restoreFocus) opener.focus();
    acceptOwnedMutations();
  }

  try {
    var preseededSearchState = Array.prototype.some.call(
      main.querySelectorAll('p[data-start]'),
      function(passage) {
        return passage.classList.contains('search-match') ||
          passage.classList.contains('search-match-active') ||
          passage.hasAttribute('aria-current') || passage.hasAttribute('tabindex');
      }
    );
    if (!canonicalSearchRoot() || preseededSearchState ||
        document.body.classList.contains('transcript-search-active') ||
        !panel.hidden || opener.hidden || opener.getAttribute('aria-expanded') !== 'false' ||
        !previous.disabled || !next.disabled || !read.disabled || status.textContent !== '') {
      throw new Error('shape');
    }
    index = buildIndex();
  } catch (error) {
    setInvalid(error && error.message === 'capacity' ?
      'This transcript is too large to search.' : 'This transcript cannot be searched.');
  }
  function observeMutations() {
    mutationObserver.observe(document.documentElement, {
      subtree: true,
      childList: true,
      characterData: true,
      attributes: true
    });
    observerStarted = true;
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', observeMutations, { once: true });
  } else {
    observeMutations();
  }

  opener.addEventListener('click', openSearch);
  clear.addEventListener('click', function() { dismiss(true); });
  previous.addEventListener('click', function() { activate(current - 1, true); });
  next.addEventListener('click', function() { activate(current + 1, true); });
  read.addEventListener('click', function() {
    if (current >= 0 && matches[current] && canonicalChain(matches[current].el)) {
      matches[current].el.focus({ preventScroll: true });
    }
  });
  input.addEventListener('compositionstart', function() {
    beginOwnedMutations();
    composing = true;
    cancelPending();
    clearResults();
    status.textContent = '';
    acceptOwnedMutations();
  });
  input.addEventListener('compositionend', function() { composing = false; schedule(); });
  input.addEventListener('input', function(e) { if (!e.isComposing && !composing) schedule(); });
  input.addEventListener('keydown', function(e) {
    if (e.defaultPrevented || e.isComposing || composing) return;
    if (e.key === 'Enter') {
      e.preventDefault();
      activate(current + (e.shiftKey ? -1 : 1), true);
    }
  });
  document.addEventListener('keydown', function(e) {
    var target = e.target;
    var editable = target &&
      (target.matches('input, textarea, select') || target.isContentEditable);
    if (e.defaultPrevented || e.isComposing || composing ||
        e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === 'Escape' && !panel.hidden &&
        (root.contains(target) ||
          (current >= 0 && matches[current] && matches[current].el === target))) {
      e.preventDefault();
      dismiss(true);
      return;
    }
    if (e.key === '/' && !editable) {
      e.preventDefault();
      openSearch();
    }
  });
  window.addEventListener('pr-rail-layout', layout);
  window.addEventListener('resize', layout);
  window.addEventListener('pagehide', function() { dismiss(false); });
  window.addEventListener('pageshow', function(e) { if (e.persisted) dismiss(false); });
  if ('ResizeObserver' in window) {
    var resizeObserver = new ResizeObserver(layout);
    resizeObserver.observe(root);
    if (rail) resizeObserver.observe(rail);
  }
  layout();
})();
"""

# Keep the first search release byte-for-byte available for private-web CSP
# compatibility.  The corrected script is derived with narrow, reviewable
# substitutions so the legacy text cannot drift unnoticed (its digest is pinned
# in the web-surface tests).
_SEARCH_SCRIPT = (
    _SEARCH_SCRIPT_V1.replace(
        """  function exactAttributeValues(node, expected) {
    var names = Object.keys(expected);
    return node.attributes.length === names.length && names.every(function(name) {
      return node.getAttribute(name) === expected[name];
    });
  }
""",
        """  function exactAttributeValues(node, expected) {
    var names = Object.keys(expected);
    return node.attributes.length === names.length && names.every(function(name) {
      return node.getAttribute(name) === expected[name];
    });
  }

  function decoratedAttributeValues(node, expected) {
    var names = Object.keys(expected);
    return names.every(function(name) { return node.getAttribute(name) === expected[name]; }) &&
      Array.prototype.every.call(node.attributes, function(attribute) {
        return names.indexOf(attribute.name) !== -1 || /^data-/.test(attribute.name);
      });
  }
""",
    )
    .replace(
        """  function canonicalDocument() {
    var htmlTheme = document.documentElement.getAttribute('data-theme');
    return document.documentElement.getAttribute('lang') === 'en' &&
      exactAttributes(document.documentElement, ['lang', 'data-theme', 'style']) &&
      (htmlTheme === null || htmlTheme === 'light' || htmlTheme === 'dark') &&
      exactAttributes(document.body, ['class']) &&
      Array.prototype.every.call(document.body.classList, function(name) {
        return name === 'has-sidebar' || name === 'transcript-search-active';
      }) &&
      exactAttributes(content, ['id']) && exactAttributes(main, []);
  }
""",
        """  function canonicalDocument() {
    return exactAttributes(content, ['id']) && exactAttributes(main, []) &&
      main.parentElement === content && content.parentElement === document.body;
  }
""",
    )
    .replace(
        """      exactAttributeValues(input, { 'id': 'transcript-search-input',
        'class': 'transcript-search-input', 'type': 'search', 'autocomplete': 'off',
        'spellcheck': 'false', 'autocorrect': 'off', 'autocapitalize': 'none',
        'inputmode': 'search', 'placeholder': 'Find in transcript' }) &&
""",
        """      decoratedAttributeValues(input, { 'id': 'transcript-search-input',
        'class': 'transcript-search-input', 'type': 'search', 'autocomplete': 'off',
        'spellcheck': 'false', 'autocorrect': 'off', 'autocapitalize': 'none',
        'inputmode': 'search', 'placeholder': 'Find in transcript' }) &&
""",
    )
    .replace(
        """    var walker = document.createTreeWalker(
      document.body,
""",
        """    var walker = document.createTreeWalker(
      main,
""",
    )
    .replace(
        """    if (!target) return false;
    if (rail && (target === rail || rail.contains(target))) return true;
    if (sidebar && (target === sidebar || sidebar.contains(target))) return true;
    if (target === document.documentElement) {
      if (record.attributeName === 'data-theme') {
        var theme = target.getAttribute('data-theme');
        return theme === null || theme === 'light' || theme === 'dark';
      }
    }
    if (target === document.body && record.attributeName === 'class') {
      return false;
    }
    if (target.tagName === 'P') {
""",
        """    if (!target) return false;
    if (target === content) {
      return record.type === 'childList' && root.parentElement === content &&
        main.parentElement === content;
    }
    if (target === root || root.contains(target)) {
      return record.type === 'attributes' && /^data-/.test(record.attributeName || '') &&
        canonicalSearchRoot();
    }
    if (rail && (target === rail || rail.contains(target))) return true;
    if (target.tagName === 'P') {
""",
    )
    .replace(
        """  function observeMutations() {
    mutationObserver.observe(document.documentElement, {
      subtree: true,
      childList: true,
      characterData: true,
      attributes: true
    });
    observerStarted = true;
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', observeMutations, { once: true });
  } else {
    observeMutations();
  }
""",
        """  function observeMutations() {
    mutationObserver.observe(content, { childList: true });
    mutationObserver.observe(main, {
      subtree: true,
      childList: true,
      characterData: true,
      attributes: true
    });
    mutationObserver.observe(root, {
      subtree: true,
      childList: true,
      characterData: true,
      attributes: true
    });
    observerStarted = true;
  }
  observeMutations();
""",
    )
)


def build_html(
    segments: list[dict[str, Any]],
    title: str,
    chapters: list[dict[str, Any]] | None = None,
    sentences_per_para: int = 5,
    source: str = "whisper-ctranslate2",
    caption_cleanup: bool = False,
) -> str:
    """Build a styled HTML document from segments, optionally with chapters.

    Segments may carry optional ``speaker`` labels (diarization): paragraphs
    then break at speaker changes and show attribution where the speaker
    changes. Without them the output is byte-identical to before speaker
    rendering existed (regression-tested against golden files).
    """
    has_speakers = any("speaker" in s for s in segments)
    stylesheet = _STYLESHEET + _SPEAKER_STYLESHEET if has_speakers else _STYLESHEET
    sidebar_html = build_sidebar_nav(chapters) if chapters else ""
    timeline_html = "" if chapters else build_timeline_nav(segments, sentences_per_para)
    body = build_chapter_body(segments, chapters or [], sentences_per_para)
    chapters_note = (
        ""
        if chapters
        else '  <div class="chapters-note">Chapters, key points, and pull quotes are available '
        "when a chapter provider key is configured (Settings &rarr; AI model in the app).</div>\n"
    )
    cleanup_note = (
        '  <div class="cleanup-note">AI-assisted spelling/casing cleanup enabled; '
        "wording is preserved.</div>\n"
        if caption_cleanup
        else ""
    )
    # The sidebar scroll script is chapter-gated; media sync and local search
    # always present (it keys off [data-start] passages, which exist in both
    # paths) and is inert when the file is opened standalone.
    scroll_tag = f"<script>\n{_SCROLL_SCRIPT}</script>\n" if chapters else ""
    rail_tag = f"<script>\n{_RAIL_SCRIPT}</script>\n" if timeline_html else ""
    script_tag = (
        f"{scroll_tag}{rail_tag}<script>\n{_SYNC_SCRIPT}</script>\n"
        f"<script>\n{_SEARCH_SCRIPT}</script>"
    )

    # The sidebar and the margin that reserves space for it travel together:
    # keyless artifacts emit neither (issue #52).
    body_tag = '<body class="has-sidebar">' if chapters else "<body>"

    # Use string concatenation instead of f-string to avoid CSS brace escaping
    parts = [
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
        '<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        f"<title>{_esc(title)}</title>\n"
        f"<style>\n{stylesheet}</style>\n"
        f"</head>\n{body_tag}\n",
        sidebar_html,
        '\n<div id="content">\n<header>\n'
        f"  <h1>{_esc(title)}</h1>\n"
        f'  <div class="meta">{_byline(segments, source)}</div>\n'
        "</header>\n",
        _SEARCH_HTML,
        "<main>\n",
        timeline_html,
        "\n" if timeline_html else "",
        body,
        "\n</main>\n"
        "<footer>\n"
        f"  Transcript generated by {source} &middot; Timestamps are approximate\n",
        chapters_note,
        cleanup_note,
        "</footer>\n</div>\n",
        script_tag,
        "\n</body>\n</html>",
    ]
    return "".join(parts)
