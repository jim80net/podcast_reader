"""Production diarization worker: pre-converted WAV in, speaker turns JSON out.

Contract (diarization-worker spec, adopted from the packaging spike §4):

    diarization-worker AUDIO.wav --output turns.json [--num-speakers N]
    output: {"turns": [{"start": float, "end": float, "speaker": str}]}
    exit:   0 ok / non-zero with a human-readable stderr tail

The worker reads the WAV with the stdlib ``wave`` module and feeds pyannote
an **in-memory waveform** — no torchcodec/FFmpeg shared-library dependency
(the engine pre-converts input to 16 kHz mono WAV with its managed ffmpeg,
neutralizing the torchcodec pathing problem entirely; spike evidence).

torch and pyannote.audio are imported lazily inside :func:`diarize_wav` —
the module itself imports without the ``diarization`` extra, and the main
package never imports this module at all. In the packaged pack the pipeline
loads **offline** from the pre-seeded Hugging Face cache shipped next to the
frozen executable (``<pack dir>/cache``); users need no Hugging Face account.

Two flags beyond the spec's required surface (used by the pack build, the
freeze smoke, and dev runs): ``--model`` (pipeline id or local config path)
and ``--cache-dir`` (overrides the frozen-sibling cache default).
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import sys
import wave
from pathlib import Path

#: pyannote.audio 4.x default pipeline — what the published pack pre-seeds.
DEFAULT_MODEL = "pyannote/speaker-diarization-community-1"


def read_wav(path: Path) -> tuple[bytes, int, int]:
    """Decode a PCM WAV with the stdlib: ``(s16le frames, sample_rate, channels)``.

    The engine pre-converts to 16 kHz mono s16le; any other sample width is a
    contract violation worth a readable failure instead of garbage audio.
    """
    with wave.open(str(path), "rb") as wav_file:
        if wav_file.getsampwidth() != 2:
            raise ValueError(
                f"expected 16-bit PCM WAV, got {wav_file.getsampwidth() * 8}-bit: {path}"
            )
        frames = wav_file.readframes(wav_file.getnframes())
        return frames, wav_file.getframerate(), wav_file.getnchannels()


def diarize_wav(
    audio_path: Path,
    output_path: Path,
    *,
    model: str,
    num_speakers: int | None,
    cache_dir: Path | None,
) -> None:
    """Run the pyannote pipeline over *audio_path* and write turns JSON.

    The waveform is built in memory (``torch.frombuffer`` over the stdlib WAV
    frames; stereo mixed down) and handed to the pipeline as
    ``{"waveform", "sample_rate"}`` — the exact in-memory path the spike
    verified to work without torchcodec/FFmpeg.
    """
    import torch  # lazy: the `diarization` extra only
    from pyannote.audio import Pipeline  # lazy: the `diarization` extra only

    frames, sample_rate, channels = read_wav(audio_path)
    samples = torch.frombuffer(bytearray(frames), dtype=torch.int16).to(torch.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(dim=1)
    waveform = samples[None, :]

    pipeline = Pipeline.from_pretrained(model, cache_dir=cache_dir)
    result = pipeline({"waveform": waveform, "sample_rate": sample_rate}, num_speakers=num_speakers)
    # pyannote 4.x community pipelines return an output object carrying
    # `speaker_diarization`; 3.x-style pipelines return the Annotation bare.
    annotation = getattr(result, "speaker_diarization", result)
    turns = [
        {"start": float(turn.start), "end": float(turn.end), "speaker": str(speaker)}
        for turn, _track, speaker in annotation.itertracks(yield_label=True)
    ]
    output_path.write_text(json.dumps({"turns": turns}, indent=2))


def resolve_cache_dir(explicit: Path | None) -> Path | None:
    """The HF cache to load the pipeline from.

    Explicit flag wins; a frozen worker defaults to the ``cache`` directory
    the pack ships next to the executable; unfrozen dev runs default to the
    regular Hugging Face cache (``None``).
    """
    if explicit is not None:
        return explicit
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "cache"
    return None


def prepare_offline_cache(cache_dir: Path | None) -> None:
    """Force offline hub loading from *cache_dir* when it exists.

    Must run before the lazy pyannote import (huggingface_hub reads these at
    import time). ``setdefault`` keeps explicit operator overrides working.
    A missing cache leaves the environment alone so unfrozen dev runs can
    still download.
    """
    if cache_dir is None or not cache_dir.is_dir():
        return
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(cache_dir))
    os.environ.setdefault("HF_HUB_OFFLINE", "1")


def main() -> None:
    """Console / frozen entry point implementing the worker contract."""
    # FIRST: in frozen bundles on Windows/macOS multiprocessing re-executes
    # this binary; without freeze_support a re-exec would re-run main.
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser(
        prog="diarization-worker",
        description="Diarize one pre-converted 16 kHz mono WAV into speaker turns JSON.",
    )
    parser.add_argument("audio", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="turns JSON output path")
    parser.add_argument("--num-speakers", type=int, default=None)
    parser.add_argument("--model", default=DEFAULT_MODEL, help="pipeline id or local config path")
    parser.add_argument("--cache-dir", type=Path, default=None)
    args = parser.parse_args()

    cache_dir = resolve_cache_dir(args.cache_dir)
    prepare_offline_cache(cache_dir)  # before the lazy pyannote import below
    try:
        diarize_wav(
            args.audio,
            args.output,
            model=args.model,
            num_speakers=args.num_speakers,
            cache_dir=cache_dir,
        )
    except Exception as exc:
        # Non-zero exit with a human-readable stderr tail (worker contract).
        print(f"diarization-worker error: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1) from exc
    print(str(args.output.resolve()), flush=True)


if __name__ == "__main__":
    main()
