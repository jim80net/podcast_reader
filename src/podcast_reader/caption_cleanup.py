"""Fail-closed caption spelling and casing cleanup.

The chapter model may suggest corrections, but this module is the authority on
what can reach a transcript.  It accepts only one-token casing changes or small
spelling edits; phrase rewrites, punctuation changes, insertions, and deletions
are rejected mechanically.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from spellchecker import SpellChecker

_WORD = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*", re.UNICODE)


@lru_cache(maxsize=1)
def _spelling() -> SpellChecker:
    """Load the English dictionary only when cleanup is actually enabled."""
    return SpellChecker(distance=2)


def _edit_distance(left: str, right: str) -> int:
    """Return Damerau-Levenshtein distance, including adjacent swaps."""
    previous_previous: list[int] | None = None
    previous = list(range(len(right) + 1))
    for i, left_char in enumerate(left, 1):
        current = [i]
        for j, right_char in enumerate(right, 1):
            current.append(
                min(
                    current[j - 1] + 1,
                    previous[j] + 1,
                    previous[j - 1] + (left_char != right_char),
                )
            )
            if (
                previous_previous is not None
                and i > 1
                and j > 1
                and left_char == right[j - 2]
                and left[i - 2] == right_char
            ):
                current[j] = min(current[j], previous_previous[j - 2] + 1)
        previous_previous, previous = previous, current
    return previous[-1]


def _safe_pair(original: str, replacement: str) -> bool:
    """Whether a proposed pair is structurally a casing/spelling correction."""
    if not _WORD.fullmatch(original) or not _WORD.fullmatch(replacement):
        return False
    if original == replacement:
        return False
    if original.casefold() == replacement.casefold():
        return True
    if min(len(original), len(replacement)) < 4:
        return False
    original_folded = original.casefold()
    replacement_folded = replacement.casefold()
    spelling = _spelling()
    if original_folded not in spelling.unknown([original_folded]):
        return False
    if replacement_folded not in spelling.known([replacement_folded]):
        return False
    return _edit_distance(original_folded, replacement_folded) <= 2


def apply_caption_corrections(
    segments: list[dict[str, Any]], corrections: object
) -> tuple[list[dict[str, Any]], int]:
    """Apply only validated, unambiguous corrections to copied segments.

    Each suggestion must identify an exact segment timestamp and an exact token
    that occurs once in that segment. Invalid or ambiguous model output is
    ignored rather than guessed at.
    """
    cleaned = [dict(segment) for segment in segments]
    if not isinstance(corrections, list):
        return cleaned, 0

    applied = 0
    for correction in corrections:
        if not isinstance(correction, dict):
            continue
        try:
            segment_start = float(correction["segment_start"])
            original = correction["original"]
            replacement = correction["replacement"]
        except (KeyError, TypeError, ValueError):
            continue
        if not isinstance(original, str) or not isinstance(replacement, str):
            continue
        if not _safe_pair(original, replacement):
            continue

        matches = [
            segment
            for segment in cleaned
            if abs(float(segment.get("start", -1.0)) - segment_start) < 0.0005
        ]
        if len(matches) != 1:
            continue
        segment = matches[0]
        text = str(segment.get("text", ""))
        token_matches = [match for match in _WORD.finditer(text) if match.group() == original]
        if len(token_matches) != 1:
            continue
        match = token_matches[0]
        segment["text"] = text[: match.start()] + replacement + text[match.end() :]
        applied += 1

    return cleaned, applied
