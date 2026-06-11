"""Whisper worker entry point for the packaging spike.

Invocation contract under test (candidate for the real engine):

    whisper-worker AUDIO_PATH --model tiny --device cpu --output-dir DIR \
        [--language en] [--compute-type int8]

Writes `<output-dir>/<audio stem>.json` in whisper-ctranslate2-compatible
shape ({"text", "segments": [...], "language"}) and prints progress lines
to stderr, final JSON path to stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def transcribe(
    audio_path: Path,
    output_dir: Path,
    model: str,
    device: str,
    language: str | None,
    compute_type: str,
) -> Path:
    from faster_whisper import WhisperModel

    whisper = WhisperModel(model, device=device, compute_type=compute_type)
    segments_iter, info = whisper.transcribe(str(audio_path), language=language)

    segments: list[dict[str, Any]] = []
    for seg in segments_iter:
        segments.append(
            {
                "id": seg.id,
                "seek": seg.seek,
                "start": seg.start,
                "end": seg.end,
                "text": seg.text,
                "tokens": list(seg.tokens),
                "temperature": seg.temperature,
                "avg_logprob": seg.avg_logprob,
                "compression_ratio": seg.compression_ratio,
                "no_speech_prob": seg.no_speech_prob,
                # whisper-ctranslate2 emits "words": null unless word
                # timestamps are requested; mirror it for byte-level shape
                # parity (SPIKE_REPORT.md section 2 recommendation).
                "words": None,
            }
        )
        print(f"progress segment_end={seg.end:.2f}", file=sys.stderr, flush=True)

    result = {
        "text": "".join(s["text"] for s in segments),
        "segments": segments,
        "language": info.language,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{audio_path.stem}.json"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return json_path


def main() -> None:
    parser = argparse.ArgumentParser(prog="whisper-worker")
    parser.add_argument("audio", type=Path)
    parser.add_argument("--model", default="tiny")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--language", default=None)
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--output-dir", type=Path, default=Path.cwd())
    args = parser.parse_args()

    json_path = transcribe(
        args.audio, args.output_dir, args.model, args.device, args.language, args.compute_type
    )
    print(str(json_path), flush=True)


if __name__ == "__main__":
    main()
