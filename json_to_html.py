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


def segments_to_paragraphs(segments: list[dict], sentences_per_para: int = 5) -> list[dict]:
    """Group segments into paragraphs of roughly N sentences each."""
    paragraphs = []
    current = None

    for seg in segments:
        text = seg["text"].strip()
        if not text:
            continue

        if current is None:
            current = {"start": seg["start"], "end": seg["end"], "text": text}
        elif _count_sentences(current["text"]) >= sentences_per_para:
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


def build_toc(chapters: list[dict]) -> str:
    """Build an HTML table of contents from chapters."""
    rows = []
    for ch in chapters:
        anchor = _slug(ch["title"])
        ts = fmt_time(ch["start"])
        badge = ""
        label = TYPE_LABELS.get(ch["type"])
        if label:
            rows.append(
                f'<tr class="toc-{ch["type"]}">'
                f'<td class="toc-ts">{ts}</td>'
                f'<td><a href="#{anchor}">{ch["title"]}</a>'
                f' <span class="badge badge-{ch["type"]}">{label}</span>'
                f'<div class="toc-abstract">{ch["abstract"]}</div></td></tr>'
            )
        else:
            rows.append(
                f'<tr>'
                f'<td class="toc-ts">{ts}</td>'
                f'<td><a href="#{anchor}">{ch["title"]}</a>'
                f'<div class="toc-abstract">{ch["abstract"]}</div></td></tr>'
            )

    return (
        '<nav class="toc"><h2>Chapters</h2>'
        '<table>' + "\n".join(rows) + '</table></nav>'
    )


def assign_paragraphs_to_chapters(paragraphs: list[dict], chapters: list[dict]) -> str:
    """Wrap paragraphs in chapter sections."""
    if not chapters:
        parts = []
        for p in paragraphs:
            ts = fmt_time(p["start"])
            parts.append(f'<p><span class="ts">{ts}</span> {p["text"]}</p>')
        return "\n".join(parts)

    sorted_chapters = sorted(chapters, key=lambda c: c["start"])
    parts = []
    ch_idx = 0

    for i, ch in enumerate(sorted_chapters):
        anchor = _slug(ch["title"])
        section_class = f'chapter-section type-{ch["type"]}'
        parts.append(f'<section id="{anchor}" class="{section_class}">')
        parts.append(f'<h2><span class="ts">{fmt_time(ch["start"])}</span> {ch["title"]}')
        label = TYPE_LABELS.get(ch["type"])
        if label:
            parts.append(f' <span class="badge badge-{ch["type"]}">{label}</span>')
        parts.append(f'</h2>')
        parts.append(f'<p class="chapter-abstract">{ch["abstract"]}</p>')

        ch_end = sorted_chapters[i + 1]["start"] if i + 1 < len(sorted_chapters) else float("inf")

        for p in paragraphs:
            if p["start"] >= ch["start"] and p["start"] < ch_end:
                ts = fmt_time(p["start"])
                parts.append(f'<p><span class="ts">{ts}</span> {p["text"]}</p>')

        parts.append(f'</section>')

    return "\n".join(parts)


def build_html(paragraphs: list[dict], title: str, chapters: list[dict] | None = None) -> str:
    """Build a styled HTML document from paragraphs, optionally with chapters."""
    toc_html = build_toc(chapters) if chapters else ""
    body = assign_paragraphs_to_chapters(paragraphs, chapters) if chapters else "\n".join(
        f'<p><span class="ts">{fmt_time(p["start"])}</span> {p["text"]}</p>'
        for p in paragraphs
    )

    chapter_styles = """
  nav.toc {{
    background: var(--surface);
    border-radius: 8px;
    padding: 1.5rem;
    margin-bottom: 2.5rem;
  }}
  nav.toc h2 {{
    font-size: 1.2rem;
    color: var(--accent);
    margin-bottom: 1rem;
  }}
  nav.toc table {{
    width: 100%;
    border-collapse: collapse;
  }}
  nav.toc tr {{
    border-bottom: 1px solid rgba(255,255,255,0.05);
  }}
  nav.toc td {{
    padding: 0.5rem 0.25rem;
    vertical-align: top;
  }}
  .toc-ts {{
    font-family: 'Menlo', 'Consolas', monospace;
    font-size: 0.8rem;
    color: var(--accent);
    white-space: nowrap;
    padding-right: 1rem !important;
    width: 1%;
  }}
  nav.toc a {{
    color: var(--text);
    text-decoration: none;
    font-weight: 600;
  }}
  nav.toc a:hover {{
    color: var(--accent);
  }}
  .toc-abstract {{
    color: var(--muted);
    font-size: 0.85rem;
    margin-top: 0.2rem;
    line-height: 1.4;
  }}
  .toc-sponsor {{
    opacity: 0.5;
  }}
  .badge {{
    font-size: 0.65rem;
    padding: 0.1rem 0.4rem;
    border-radius: 3px;
    text-transform: uppercase;
    font-weight: 700;
    letter-spacing: 0.05em;
    vertical-align: middle;
    margin-left: 0.4rem;
  }}
  .badge-sponsor {{ background: #7f8c2c; color: #fff; }}
  .badge-intro, .badge-outro {{ background: #2c5f7f; color: #fff; }}
  .badge-housekeeping {{ background: #6b5b7b; color: #fff; }}
  .chapter-section {{
    margin-bottom: 2.5rem;
  }}
  .chapter-section h2 {{
    font-size: 1.3rem;
    color: #fff;
    margin-bottom: 0.5rem;
    padding-top: 1rem;
    border-top: 1px solid var(--surface);
  }}
  .chapter-abstract {{
    color: var(--muted);
    font-style: italic;
    font-size: 0.9rem;
    margin-bottom: 1rem;
    line-height: 1.5;
  }}
  .type-sponsor {{
    opacity: 0.45;
    transition: opacity 0.2s;
  }}
  .type-sponsor:hover {{
    opacity: 1;
  }}
  @media (prefers-color-scheme: light) {{
    nav.toc tr {{
      border-bottom: 1px solid rgba(0,0,0,0.08);
    }}
    .chapter-section h2 {{
      color: #1a1a1a;
    }}
  }}""" if chapters else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
  :root {{
    --bg: #1a1a2e;
    --surface: #16213e;
    --text: #e0e0e0;
    --muted: #8a8a9a;
    --accent: #e94560;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: 'Georgia', 'Times New Roman', serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.8;
    max-width: 52rem;
    margin: 0 auto;
    padding: 2rem 1.5rem;
  }}
  header {{
    border-bottom: 2px solid var(--accent);
    padding-bottom: 1.5rem;
    margin-bottom: 2rem;
  }}
  h1 {{
    font-size: 1.8rem;
    color: #fff;
    margin-bottom: 0.25rem;
  }}
  .meta {{
    color: var(--muted);
    font-size: 0.9rem;
  }}
  p {{
    margin-bottom: 1.2rem;
    text-align: justify;
  }}
  .ts {{
    font-family: 'Menlo', 'Consolas', monospace;
    font-size: 0.75rem;
    color: var(--accent);
    background: var(--surface);
    padding: 0.15rem 0.4rem;
    border-radius: 3px;
    margin-right: 0.5rem;
    user-select: none;
    vertical-align: middle;
  }}
  footer {{
    margin-top: 3rem;
    padding-top: 1rem;
    border-top: 1px solid var(--surface);
    color: var(--muted);
    font-size: 0.8rem;
    text-align: center;
  }}
  @media (prefers-color-scheme: light) {{
    :root {{
      --bg: #fafafa;
      --surface: #f0f0f0;
      --text: #2a2a2a;
      --muted: #777;
      --accent: #c0392b;
    }}
    h1 {{ color: #1a1a1a; }}
  }}
{chapter_styles}
</style>
</head>
<body>
<header>
  <h1>{title}</h1>
  <div class="meta">Auto-transcribed with whisper-ctranslate2 (large-v3)</div>
</header>
{toc_html}
<main>
{body}
</main>
<footer>
  Transcript generated by faster-whisper &middot; Timestamps are approximate
</footer>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser(description="Convert whisper JSON to styled HTML")
    parser.add_argument("json_file", help="Path to the whisper JSON output")
    parser.add_argument("--title", default=None, help="Document title")
    parser.add_argument("--sentences", type=int, default=5, help="Sentences per paragraph (default: 5)")
    parser.add_argument("--chapters", default=None, help="Path to chapters JSON (from generate_chapters.py)")
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
    paragraphs = segments_to_paragraphs(data["segments"], sentences_per_para=args.sentences)
    html = build_html(paragraphs, title, chapters=chapters)

    out_path = json_path.with_suffix(".html")
    out_path.write_text(html)
    print(f"Written {len(paragraphs)} paragraphs to {out_path}")


if __name__ == "__main__":
    main()
