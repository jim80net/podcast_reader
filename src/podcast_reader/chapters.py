"""Generate chapter markers with abstracts from a whisper transcript via an LLM.

All providers are reached through one OpenAI-compatible ``/chat/completions``
request (see :mod:`podcast_reader.providers`); Anthropic goes through its
OpenAI-compat endpoint, so no provider SDK is required.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from podcast_reader.providers import ProviderSpec

REQUEST_TIMEOUT_S = 300.0


class ChapterError(Exception):
    """Chapter-generation failure whose message is safe to surface verbatim.

    Messages are constructed only from our own constants — never from the
    provider's response body — so the pipeline may emit them into events and
    the job journal without violating the key-redaction spec. Anything that
    might carry response content (HTTP errors) stays a plain ``RuntimeError``
    and is wrapped generically by the pipeline.
    """


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


def snap_chapters_to_segments(
    chapters: list[dict[str, Any]], segments: list[dict[str, Any]]
) -> list[dict[str, Any]]:
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
- Housekeeping like announcements, schedule updates, or meta-discussion about the podcast \
is "housekeeping"
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


def format_transcript(segments: list[dict[str, Any]]) -> str:
    """Format segments with timestamps in seconds for the LLM prompt."""
    lines = []
    for seg in segments:
        start = seg["start"]
        text = seg["text"].strip()
        if not text:
            continue
        lines.append(f"[{start:.1f}] {text}")
    return "\n".join(lines)


def generate_chapters(
    transcript_text: str,
    *,
    spec: ProviderSpec,
    api_key: str,
    model: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> list[dict[str, Any]]:
    """Send the transcript to *spec*'s ``/chat/completions`` and parse chapters.

    *model* ``None`` (or empty) selects the provider's default model. *transport*
    lets tests inject an ``httpx.MockTransport`` — production uses the default.

    Error messages deliberately never include the provider's response body:
    auth-error bodies echo key fragments (the practical key-leak vector).
    """
    payload = {
        "model": model or spec["default_model"],
        "max_tokens": spec["max_tokens"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Here is the transcript:\n\n{transcript_text}"},
        ],
    }
    url = spec["base_url"].rstrip("/") + "/chat/completions"
    with httpx.Client(transport=transport, timeout=REQUEST_TIMEOUT_S) as client:
        response = client.post(url, json=payload, headers={"Authorization": f"Bearer {api_key}"})
    if response.status_code >= 400:
        # Never include the response body in the message (key-redaction spec).
        raise RuntimeError(f"Chapter provider request failed: HTTP {response.status_code}")

    body: dict[str, Any] = response.json()
    choice = body["choices"][0]
    if choice.get("finish_reason") == "length":
        # Self-authored message (no body content) — safe to surface verbatim.
        raise ChapterError(
            "Chapter response was truncated (hit the provider's max_tokens cap). "
            "The transcript may be too long for a single request."
        )
    # Unknown finish_reason values are treated as success-with-parse-attempt.
    raw = str(choice["message"]["content"]).strip()

    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        if raw.endswith("```"):
            raw = raw.rsplit("```", 1)[0]

    return json.loads(raw)  # type: ignore[no-any-return]
