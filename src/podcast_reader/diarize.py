"""Engine-side diarization glue: ffmpeg pre-convert, worker spawn, speaker merge.

Design decision 6: the worker is dumb by design — the engine pre-converts the
input audio to 16 kHz mono WAV with its managed ffmpeg (staged in a temp
directory, never retained), spawns the diarization pack's frozen worker
(``diarization-worker AUDIO.wav --output turns.json``), and performs the
max-overlap speaker assignment itself in pure stdlib interval math so the
merge is unit-testable without torch.

Fault isolation mirrors the chapters step: a missing/unusable pack skips with
a warning naming the pack, and a worker or pre-convert failure degrades to a
warning — a transcript without speakers beats a dead job. The enriched JSON
is rewritten atomically in place; segments already carrying speakers make the
step a cache hit (idempotent re-runs never re-spawn the worker).
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from podcast_reader.engine import packs
from podcast_reader.engine.settings import atomic_write_json, data_dir
from podcast_reader.tools import resolve_tool, run_child
from podcast_reader.types import PipelineEvent

if TYPE_CHECKING:
    from collections.abc import Callable

    from podcast_reader.types import EventKind

#: Name of the worker executable inside the diarization pack directory.
WORKER_NAME = "diarization-worker"

_SKIP_HINT = "Install the speaker diarization pack from Settings → Packs to enable it."


def assign_speakers(segments: list[dict[str, Any]], turns: list[dict[str, Any]]) -> int:
    """Assign each segment the speaker with maximal positive time-overlap.

    The spike's merge sketch (§4): per segment, sum positive intersections
    with every turn per speaker and pick the largest total (ties break
    lexically for determinism). Segments with no positive overlap keep no
    speaker. Mutates *segments* in place; returns how many got a speaker.
    """
    assigned = 0
    for segment in segments:
        overlaps: dict[str, float] = {}
        for turn in turns:
            overlap = min(segment["end"], turn["end"]) - max(segment["start"], turn["start"])
            if overlap > 0:
                speaker = str(turn["speaker"])
                overlaps[speaker] = overlaps.get(speaker, 0.0) + overlap
        if overlaps:
            segment["speaker"] = min(overlaps, key=lambda s: (-overlaps[s], s))
            assigned += 1
    return assigned


def diarize_step(
    *,
    audio_path: Path,
    json_path: Path,
    on_event: Callable[[PipelineEvent], None],
) -> None:
    """Enrich the transcript JSON at *json_path* with speaker labels.

    Never raises for diarization-specific problems: pack absent/unusable and
    worker/pre-convert failures all degrade to warnings (spec: graceful skip,
    worker failure does not kill the job).
    """
    data = json.loads(json_path.read_text())
    segments: list[dict[str, Any]] = data.get("segments", [])
    if any("speaker" in segment for segment in segments):
        _emit(
            on_event,
            "step_started",
            f"Speakers already assigned in {json_path} (delete to re-diarize)",
            {"cached": True},
        )
        _emit(on_event, "step_finished", "", {"cached": True})
        return

    worker, problem = _resolve_worker(data_dir())
    if worker is None:
        _emit(
            on_event,
            "warning",
            f"Diarization skipped: {problem}. {_SKIP_HINT}",
            {"code": "diarization_skipped"},
        )
        return

    _emit(on_event, "step_started", "Diarizing speakers...", {})
    turns = _run_worker(worker, audio_path, on_event)
    if turns is None:
        return  # _run_worker already emitted the warning
    assigned = assign_speakers(segments, turns)
    atomic_write_json(json_path, data)
    _emit(
        on_event,
        "step_finished",
        f"Assigned speakers to {assigned} of {len(segments)} segments",
        {},
    )


def _run_worker(
    worker: str,
    audio_path: Path,
    on_event: Callable[[PipelineEvent], None],
) -> list[dict[str, Any]] | None:
    """Pre-convert + spawn the worker; the parsed turns, or ``None`` after a warning."""
    with tempfile.TemporaryDirectory(prefix="podcast-reader-diarize-") as staging:
        staged_wav = Path(staging) / f"{audio_path.stem}.wav"
        convert = run_child(
            [
                resolve_tool("ffmpeg"),
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(audio_path),
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(staged_wav),
            ]
        )
        if convert.returncode != 0:
            _warn_failed(on_event, "audio pre-conversion failed", convert.stderr)
            return None
        turns_path = Path(staging) / "turns.json"
        result = run_child([worker, str(staged_wav), "--output", str(turns_path)])
        if result.returncode != 0:
            _warn_failed(on_event, "the diarization worker failed", result.stderr)
            return None
        try:
            turns = json.loads(turns_path.read_text())["turns"]
        except (OSError, ValueError, KeyError):
            _warn_failed(on_event, "the diarization worker produced unreadable output", "")
            return None
    if not isinstance(turns, list) or not all(_turn_shape_ok(turn) for turn in turns):
        _warn_failed(on_event, "the diarization worker produced unreadable output", "")
        return None
    return turns


def _turn_shape_ok(turn: object) -> bool:
    """True when a worker-emitted turn has everything the merge dereferences.

    :func:`assign_speakers` reads numeric ``start``/``end`` and ``speaker``
    from every turn; a malformed item would raise KeyError/TypeError there,
    breaking the step's never-raises contract — so it must read as
    unreadable output instead.
    """
    return (
        isinstance(turn, dict)
        and isinstance(turn.get("start"), (int, float))
        and isinstance(turn.get("end"), (int, float))
        and "speaker" in turn
    )


def _resolve_worker(base: Path) -> tuple[str | None, str]:
    """The installed pack's worker executable, or ``(None, reason)``.

    The pack manifest is validated at step start (same discipline as the
    whisper model packs, per S1): missing, incompatible, and integrity-failed
    packs are all treated as absent — at worst a racing uninstall reads as
    "not installed", never as a partial read.
    """
    entry = packs.REGISTRY["diarization"]
    target = packs.pack_dir(base, entry)
    manifest = packs.read_manifest(target)
    if manifest is None:
        return None, "the speaker diarization pack is not installed"
    error = packs.compat_error(entry, manifest) or packs.files_error(target, manifest)
    if error is not None:
        return None, f"the installed speaker diarization pack is unusable ({error})"
    worker = shutil.which(WORKER_NAME, path=str(target))
    if worker is None:
        return None, "the speaker diarization pack is missing its worker executable"
    return worker, ""


def _warn_failed(on_event: Callable[[PipelineEvent], None], summary: str, stderr: str) -> None:
    """Structured ``diarization_failed`` warning with a short stderr tail."""
    tail = "\n".join(stderr.strip().splitlines()[-3:])
    detail = f": {tail}" if tail else ""
    _emit(
        on_event,
        "warning",
        f"Diarization failed ({summary}{detail}); rendering without speakers",
        {"code": "diarization_failed"},
    )


def _emit(
    on_event: Callable[[PipelineEvent], None],
    kind: EventKind,
    message: str,
    data: dict[str, Any],
) -> None:
    on_event(PipelineEvent(kind=kind, step="diarize", message=message, data=data))
