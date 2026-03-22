#!/usr/bin/env python3
"""Generate chapter markers with abstracts from a whisper transcript using Claude."""

import argparse
import json
import os
import sys
from pathlib import Path

import anthropic


def _nearest_segment_time(target: float, seg_starts: list[float]) -> float:
    """Return the segment start time closest to *target*."""
    if not seg_starts:
        return target
    best = seg_starts[0]
    best_dist = abs(target - best)
    for t in seg_starts[1:]:
        d = abs(target - t)
        if d < best_dist:
            best, best_dist = t, d
    return best


def snap_chapters_to_segments(chapters: list[dict], segments: list[dict]) -> list[dict]:
    """Snap all chapter timestamps to the nearest real segment timestamp.

    LLMs sometimes hallucinate timestamps that don't exist in the transcript.
    This post-processing step ensures every chapter boundary aligns with an
    actual segment, preventing empty chapters and misplaced content.
    """
    if not chapters or not segments:
        return chapters

    seg_starts = sorted({s["start"] for s in segments})

    snapped = []
    for ch in chapters:
        ch = dict(ch)  # shallow copy
        ch["start"] = _nearest_segment_time(ch["start"], seg_starts)
        ch["end"] = _nearest_segment_time(ch["end"], seg_starts)
        if ch.get("paragraph_breaks"):
            ch["paragraph_breaks"] = [
                _nearest_segment_time(t, seg_starts) for t in ch["paragraph_breaks"]
            ]
        if ch.get("pull_quote_start") is not None:
            ch["pull_quote_start"] = _nearest_segment_time(ch["pull_quote_start"], seg_starts)
        snapped.append(ch)
    return snapped


SYSTEM_PROMPT = """\
You are a podcast analyst. Given a timestamped transcript, identify the natural \
chapter boundaries and produce a JSON array of chapters.

Each transcript line is formatted as [<seconds>] text. Use these seconds values \
directly in your output — copy them exactly from the transcript.

For each chapter, provide:
- "title": A concise, descriptive chapter title
- "start": Start time in seconds (copy from the first segment in the chapter)
- "end": End time in seconds (copy from the last segment in the chapter)
- "abstract": A 2-3 sentence summary of what is discussed in this chapter
- "type": One of "intro", "housekeeping", "content", "sponsor", "outro"
- "paragraph_breaks": An array of seconds-timestamps where a new paragraph should \
begin within this chapter. Each value is the seconds value from the transcript line \
of the first segment in that paragraph. The first value must equal the chapter's "start" time.
- "key_points": An array of strings — concise bullet points capturing the main arguments, \
claims, or facts in the chapter. May be an empty array for thin chapters (e.g. short intros \
or outros). Aim for 2-5 points per substantive chapter.
- "pull_quote": A standout phrase from the chapter suitable for a magazine-style callout, \
or null if nothing in the chapter merits highlighting. May be verbatim from the transcript \
or lightly edited to clean up filler words and spoken grammar while preserving the speaker's intent.
- "pull_quote_start": The seconds value from the transcript line where the pull \
quote begins. Required when "pull_quote" is non-null, omit or set to null otherwise.

Guidelines:
- Identify sponsor reads, ad segments, or promotional plugs as type "sponsor"
- Introductory greetings, theme music descriptions, or "welcome to the show" segments are "intro"
- Housekeeping like announcements, schedule updates, or meta-discussion about the podcast is "housekeeping"
- Closing remarks, sign-offs, or "thanks for listening" are "outro"
- Everything else is "content"
- Aim for chapters that represent meaningful topic shifts, not every minor tangent
- A typical 60-minute podcast has 5-15 chapters
- Chapters must be contiguous — every second of the podcast belongs to exactly one chapter

Key points guidelines:
- Key points should be substantive claims or arguments, not summaries \
(e.g. "80% of casualties in Ukraine are now drone-inflicted" not "Discusses drone casualties")
- Include specific numbers, names, or facts when the speaker provides them
- If a chapter lists items (e.g. "myth number one... myth number two..."), \
capture each item as a separate key point

Pull quote guidelines:
- Pick the single most striking, quotable statement — something that makes a reader \
want to read the section
- Not every chapter needs a pull quote — only include one if something genuinely stands out
- Prefer vivid, self-contained statements over ones that need surrounding context

Paragraph break guidelines:
- Break paragraphs at thematic boundaries — when the speaker shifts to a new point, \
example, argument, or sub-topic
- One coherent thought or argument per paragraph
- Do NOT break mechanically by sentence count — some paragraphs may be 2 sentences, \
others may be 8, depending on the content
- Use the seconds values from the transcript lines to identify where breaks should occur
- Each break must use an exact seconds value that appears in the transcript

Return ONLY the JSON array, no other text."""


def format_transcript(segments: list[dict]) -> str:
    """Format segments with timestamps in seconds for the LLM prompt."""
    lines = []
    for seg in segments:
        start = seg["start"]
        text = seg["text"].strip()
        if not text:
            continue
        lines.append(f"[{start:.1f}] {text}")
    return "\n".join(lines)


def generate_chapters(transcript_text: str, model: str = "claude-haiku-4-5-20251001") -> list[dict]:
    """Send transcript to Claude and get back structured chapters."""
    client = anthropic.Anthropic()

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"Here is the transcript:\n\n{transcript_text}",
            }
        ],
    )

    raw = response.content[0].text.strip()

    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        if raw.endswith("```"):
            raw = raw.rsplit("```", 1)[0]

    return json.loads(raw)


def main():
    parser = argparse.ArgumentParser(description="Generate chapter markers from a whisper transcript")
    parser.add_argument("json_file", help="Path to the whisper JSON output")
    parser.add_argument("--model", default="claude-haiku-4-5-20251001",
                        help="Claude model to use (default: claude-haiku-4-5-20251001)")
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Error: ANTHROPIC_API_KEY environment variable is required (API key or OAuth token)", file=sys.stderr)
        sys.exit(1)

    json_path = Path(args.json_file)
    data = json.loads(json_path.read_text())

    print(f"Formatting {len(data['segments'])} segments...")
    transcript_text = format_transcript(data["segments"])

    print(f"Sending to {args.model} for chapter analysis...")
    chapters = generate_chapters(transcript_text, model=args.model)

    segments = [s for s in data["segments"] if s.get("text", "").strip()]
    chapters = snap_chapters_to_segments(chapters, segments)
    print(f"Snapped chapter timestamps to nearest transcript segments")

    out_path = json_path.with_name(json_path.stem + "_chapters.json")
    out_path.write_text(json.dumps(chapters, indent=2))
    print(f"Written {len(chapters)} chapters to {out_path}")

    for ch in chapters:
        m, s = divmod(int(ch["start"]), 60)
        h, m = divmod(m, 60)
        badge = f"[{ch['type']}]" if ch["type"] != "content" else ""
        print(f"  {h:02d}:{m:02d}:{s:02d}  {ch['title']} {badge}")


if __name__ == "__main__":
    main()
