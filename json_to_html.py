#!/usr/bin/env python3
"""Convert whisper-ctranslate2 JSON output to a styled HTML transcript."""

import argparse
import json
from pathlib import Path


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


def segments_to_paragraphs(segments: list[dict], sentences_per_para: int = 5) -> list[dict]:
    """Group segments into paragraphs of roughly N sentences each.

    Breaks occur at sentence boundaries to avoid splitting mid-sentence.
    YouTube captions have short, overlapping segments where sentence boundaries
    rarely align with segment boundaries. When a paragraph exceeds the sentence
    threshold, the text is split at the last sentence boundary, carrying any
    trailing fragment into the next paragraph.

    A character-count safety valve ensures paragraphs still break even when
    there is no sentence-ending punctuation in the text.
    """
    paragraphs = []
    current = None
    carry = ""  # text after the last sentence boundary, carried to next paragraph

    for seg in segments:
        text = seg["text"].strip()
        if not text:
            continue

        if current is None:
            combined = (carry + " " + text).strip() if carry else text
            current = {"start": seg["start"], "end": seg["end"], "text": combined}
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
                    para_text = current["text"][:boundary + 1].rstrip()
                    carry = current["text"][boundary + 1:].strip()
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


def segments_to_paragraphs_themed(segments: list[dict], break_times: list[float]) -> list[dict]:
    """Group segments into paragraphs using LLM-provided thematic break timestamps."""
    if not break_times:
        return segments_to_paragraphs(segments)

    sorted_breaks = sorted(break_times)
    paragraphs = []
    current = None
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

        if current is None:
            current = {"start": seg["start"], "end": seg["end"], "text": text}
        elif starts_new:
            paragraphs.append(current)
            current = {"start": seg["start"], "end": seg["end"], "text": text}
        else:
            current["end"] = seg["end"]
            current["text"] += " " + text

    if current:
        paragraphs.append(current)

    return paragraphs


def _slug(text: str) -> str:
    """Turn a chapter title into a URL-safe anchor ID."""
    return "ch-" + "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")


TYPE_LABELS = {
    "sponsor": "Sponsor",
    "intro": "Intro",
    "outro": "Outro",
    "housekeeping": "Housekeeping",
}


def build_sidebar_nav(chapters: list[dict]) -> str:
    """Build the fixed sidebar chapter navigator."""
    items = []
    for ch in chapters:
        anchor = _slug(ch["title"])
        ts = fmt_time(ch["start"])
        label = TYPE_LABELS.get(ch["type"])
        badge = f' <span class="nav-badge nav-badge-{ch["type"]}">{label}</span>' if label else ""
        css_class = f'nav-item type-{ch["type"]}'
        items.append(
            f'<a href="#{anchor}" class="{css_class}" data-section="{anchor}">'
            f'<span class="nav-ts">{ts}</span>'
            f'<span class="nav-title">{ch["title"]}{badge}</span>'
            f'</a>'
        )
    return '<aside id="sidebar">\n<div class="sidebar-inner">\n' + "\n".join(items) + "\n</div>\n</aside>"


def build_chapter_body(segments: list[dict], chapters: list[dict], sentences_per_para: int = 5) -> str:
    """Build main content with chapter sections, using themed paragraph breaks when available."""
    if not chapters:
        paragraphs = segments_to_paragraphs(segments, sentences_per_para)
        parts = []
        for p in paragraphs:
            ts = fmt_time(p["start"])
            parts.append(f'<p><span class="ts">{ts}</span> {p["text"]}</p>')
        return "\n".join(parts)

    sorted_chapters = sorted(chapters, key=lambda c: c["start"])
    parts = []

    for i, ch in enumerate(sorted_chapters):
        anchor = _slug(ch["title"])
        key_points = ch.get("key_points", [])
        has_gutter = bool(key_points)
        section_class = f'chapter-section type-{ch["type"]}'
        if not has_gutter:
            section_class += " no-gutter"
        label = TYPE_LABELS.get(ch["type"])
        badge_html = f' <span class="badge badge-{ch["type"]}">{label}</span>' if label else ""
        parts.append(f'<section id="{anchor}" class="{section_class}">')
        parts.append('<div class="chapter-main">')
        parts.append(f'<h2><span class="ts">{fmt_time(ch["start"])}</span> {ch["title"]}{badge_html}</h2>')
        parts.append(
            '<div class="chapter-abstract">\n'
            '<h3 class="chapter-abstract-heading">Summary</h3>\n'
            f'<p>{ch["abstract"]}</p>\n'
            '</div>'
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

        # Determine where to insert the pull quote (by timestamp)
        pull_quote = ch.get("pull_quote")
        pull_quote_start = ch.get("pull_quote_start")
        pull_quote_inserted = False

        for p in paragraphs:
            # Insert pull quote before the paragraph that follows its timestamp
            if pull_quote and not pull_quote_inserted and pull_quote_start is not None:
                if p["start"] >= pull_quote_start:
                    parts.append(f'<p class="pull-quote"><strong>{pull_quote}</strong></p>')
                    pull_quote_inserted = True
            ts = fmt_time(p["start"])
            parts.append(f'<p><span class="ts">{ts}</span> {p["text"]}</p>')

        # If pull quote wasn't inserted (timestamp past all paragraphs), append at end
        if pull_quote and not pull_quote_inserted:
            parts.append(f'<p class="pull-quote"><strong>{pull_quote}</strong></p>')

        parts.append('</div>')  # close chapter-main

        # Key points in right gutter
        if has_gutter:
            items = "\n".join(f"<li>{point}</li>" for point in key_points)
            parts.append(
                '<div class="chapter-gutter">\n'
                '<div class="key-points">\n'
                '<h3 class="key-points-heading">Key Points</h3>\n'
                f'<ul>\n{items}\n</ul>\n'
                '</div>\n'
                '</div>'
            )

        parts.append('</section>')

    return "\n".join(parts)


# CSS and JS are kept as plain strings (no f-string) to avoid brace escaping issues.
_STYLESHEET = """\
@import url('https://fonts.googleapis.com/css2?family=Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,600;0,8..60,700;1,8..60,400&family=JetBrains+Mono:wght@400;600&family=Oswald:wght@400;500;600&display=swap');

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
  --link: #5ba4cf;
  --link-hover: #7ec4f0;
  --red: #c0503a;
  --green: #5a9a6a;
  --purple: #8a6abf;
  --sidebar-w: 280px;
}

* { margin: 0; padding: 0; box-sizing: border-box; }

html { scroll-behavior: smooth; scroll-padding-top: 1.5rem; }

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
.nav-item.type-sponsor { opacity: 0.4; }
.nav-item.type-sponsor:hover, .nav-item.type-sponsor.active { opacity: 1; }
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
.nav-badge-intro, .nav-badge-outro { background: rgba(50, 80, 110, 0.5); color: #6a9ab8; }
.nav-badge-housekeeping { background: rgba(80, 60, 100, 0.5); color: #9a85b5; }

/* ---- MAIN CONTENT ---- */
#content {
  margin-left: var(--sidebar-w);
  flex: 1;
  padding: 2.5rem 3rem 4rem;
}

header {
  margin-bottom: 2.5rem;
  padding-bottom: 1.5rem;
  border-bottom: 2px solid var(--accent);
  max-width: 56rem;
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

/* ---- CHAPTER SECTIONS ---- */
.chapter-section {
  display: grid;
  grid-template-columns: minmax(0, 56rem) 24rem;
  gap: 2.5rem;
  margin-bottom: 3rem;
  scroll-margin-top: 1.5rem;
}
.chapter-section.no-gutter {
  grid-template-columns: minmax(0, 56rem);
}
.chapter-main {
  min-width: 0;
}
.chapter-gutter {
  align-self: start;
  position: sticky;
  top: 2rem;
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
.type-sponsor {
  opacity: 0.35;
  transition: opacity 0.25s ease;
}
.type-sponsor:hover { opacity: 1; }

/* ---- BODY TEXT ---- */
p {
  margin-bottom: 1.3rem;
  text-align: justify;
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
.badge-intro, .badge-outro { background: rgba(50, 80, 110, 0.4); color: #7ab0d0; }
.badge-housekeeping { background: rgba(80, 60, 100, 0.4); color: #a890c0; }

/* ---- LINKS ---- */
a { color: var(--link); text-decoration: none; transition: color 0.15s; }
a:hover { color: var(--link-hover); }

/* ---- FOOTER ---- */
footer {
  margin-top: 4rem;
  padding-top: 1.2rem;
  border-top: 1px solid var(--border);
  color: var(--muted);
  font-size: 0.75rem;
  text-align: center;
  font-family: 'JetBrains Mono', monospace;
  max-width: 56rem;
}

/* ---- PULL QUOTE (inline bold) ---- */
.pull-quote {
  color: var(--text-bright);
  font-size: 1.05rem;
  line-height: 1.65;
  margin-bottom: 1.5rem;
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

/* ---- RESPONSIVE ---- */
@media (max-width: 1200px) {
  .chapter-section { grid-template-columns: minmax(0, 56rem); }
  .chapter-gutter { display: none; }
}
@media (max-width: 900px) {
  :root { --sidebar-w: 0px; }
  #sidebar { display: none; }
  #content { margin-left: 0; padding: 1.5rem; }
}
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


def build_html(segments: list[dict], title: str, chapters: list[dict] | None = None,
               sentences_per_para: int = 5, source: str = "whisper-ctranslate2") -> str:
    """Build a styled HTML document from segments, optionally with chapters."""
    sidebar_html = build_sidebar_nav(chapters) if chapters else ""
    body = build_chapter_body(segments, chapters, sentences_per_para) if chapters else "\n".join(
        f'<p><span class="ts">{fmt_time(p["start"])}</span> {p["text"]}</p>'
        for p in segments_to_paragraphs(segments, sentences_per_para)
    )
    script_tag = f"<script>\n{_SCROLL_SCRIPT}</script>" if chapters else ""

    # Use string concatenation instead of f-string to avoid CSS brace escaping
    parts = [
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
        '<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        f'<title>{title}</title>\n'
        f'<style>\n{_STYLESHEET}</style>\n'
        '</head>\n<body>\n',
        sidebar_html,
        '\n<div id="content">\n<header>\n'
        f'  <h1>{title}</h1>\n'
        f'  <div class="meta">Auto-transcribed with {source}</div>\n'
        '</header>\n<main>\n',
        body,
        '\n</main>\n'
        '<footer>\n'
        f'  Transcript generated by {source} &middot; Timestamps are approximate\n'
        '</footer>\n</div>\n',
        script_tag,
        '\n</body>\n</html>',
    ]
    return "".join(parts)


def main():
    parser = argparse.ArgumentParser(description="Convert whisper JSON to styled HTML")
    parser.add_argument("json_file", help="Path to the whisper JSON output")
    parser.add_argument("--title", default=None, help="Document title")
    parser.add_argument("--sentences", type=int, default=5, help="Sentences per paragraph (default: 5)")
    parser.add_argument("--chapters", default=None, help="Path to chapters JSON (from generate_chapters.py)")
    parser.add_argument("--source", default="whisper-ctranslate2",
                        help="Transcript source for meta line (default: whisper-ctranslate2)")
    args = parser.parse_args()

    json_path = Path(args.json_file)
    data = json.loads(json_path.read_text())

    chapters = None
    if args.chapters:
        chapters_path = Path(args.chapters)
        if chapters_path.exists():
            chapters = json.loads(chapters_path.read_text())
            print(f"Loaded {len(chapters)} chapters from {chapters_path}")

    title = args.title or json_path.stem.replace("_", " ").title()
    segments = [s for s in data["segments"] if s.get("text", "").strip()]
    html = build_html(segments, title, chapters=chapters, sentences_per_para=args.sentences, source=args.source)

    out_path = json_path.with_suffix(".html")
    out_path.write_text(html)
    print(f"Written to {out_path}")


if __name__ == "__main__":
    main()
