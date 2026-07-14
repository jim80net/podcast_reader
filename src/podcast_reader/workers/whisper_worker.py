"""Production whisper worker: argv in, file out, line-protocol progress on stderr.

Contract (design decision 4, adopted verbatim from the packaging spike):

    whisper-worker AUDIO --model <name-or-dir> --device cpu|cuda \\
        --compute-type int8|float16 [--language xx] --output-dir DIR
    stderr: "warning cuda_unavailable <message>" if CUDA preflight falls back,
            "progress duration=<sec>" once after model load,
            "progress segment_end=<sec>" per transcribed segment
    stdout: absolute path of the written JSON on success
    exit:   0 ok / non-zero with a human-readable stderr tail

The JSON is whisper-ctranslate2-shaped (top-level ``{text, segments,
language}``; per-segment ``"words": null`` when word timestamps are not
computed) so ``html.py`` and the chapters step consume it unchanged.

faster-whisper is imported lazily inside :func:`transcribe_audio` — the
module itself imports without the ``worker`` extra, and the main package
never imports this module at all. On Windows the CUDA runtime pack's
directory is prepended to ``PATH`` for ctranslate2's plain ``LoadLibraryA``
calls and registered with ``os.add_dll_directory`` for flagged loads, both
*before* that import. On POSIX the spawner sets ``LD_LIBRARY_PATH`` instead
(an in-process mutation cannot affect an already-running loader).
"""

from __future__ import annotations

import argparse
import ctypes
import json
import multiprocessing
import os
import sys
from pathlib import Path
from typing import Protocol, cast

from typing_extensions import TypedDict

from podcast_reader.engine.settings import data_dir_path

_CUDA_WARNING_PREFIX = "warning cuda_unavailable "
_CUDA_WARNING_MESSAGE = (
    "CUDA acceleration could not start; transcribing on CPU. "
    "Reinstall the NVIDIA CUDA runtime in Settings → Packs before retrying GPU."
)


class DllDirectoryHandle(Protocol):
    """Closeable token returned by ``os.add_dll_directory``."""

    def close(self) -> None:
        """Remove the registered directory from the DLL search path."""


class WindowsOs(Protocol):
    """Windows-only ``os`` surface absent from POSIX typeshed."""

    def add_dll_directory(self, path: str) -> DllDirectoryHandle:
        """Register *path* with the flagged-load DLL search path."""


class WorkerSegment(TypedDict):
    """One segment in whisper-ctranslate2's JSON shape (``words`` always null)."""

    id: int
    seek: int
    start: float
    end: float
    text: str
    tokens: list[int]
    temperature: float
    avg_logprob: float
    compression_ratio: float
    no_speech_prob: float
    words: None


class WorkerResult(TypedDict):
    """Top-level whisper-ctranslate2-shaped transcription result."""

    text: str
    segments: list[WorkerSegment]
    language: str


def transcribe_audio(
    audio_path: Path,
    output_dir: Path,
    *,
    model: str,
    device: str,
    compute_type: str,
    language: str | None,
) -> Path:
    """Transcribe *audio_path* in-process and write ``<output-dir>/<stem>.json``.

    Emits the progress line protocol on stderr (``duration`` once after the
    transcription is prepared, ``segment_end`` per segment — faster-whisper's
    segment iterator is lazy, so each line tracks real progress). Returns the
    absolute path of the written JSON.
    """
    from faster_whisper import WhisperModel  # lazy: the `worker` extra only

    whisper = WhisperModel(model, device=device, compute_type=compute_type)
    segments_iter, info = whisper.transcribe(str(audio_path), language=language)
    print(f"progress duration={info.duration:.2f}", file=sys.stderr, flush=True)

    segments: list[WorkerSegment] = []
    for seg in segments_iter:
        segments.append(
            WorkerSegment(
                id=seg.id,
                seek=seg.seek,
                start=seg.start,
                end=seg.end,
                text=seg.text,
                tokens=list(seg.tokens),
                temperature=seg.temperature,
                avg_logprob=seg.avg_logprob,
                compression_ratio=seg.compression_ratio,
                no_speech_prob=seg.no_speech_prob,
                # whisper-ctranslate2 emits "words": null unless word
                # timestamps are requested; mirrored for byte-shape parity
                # (spike §2 recommendation).
                words=None,
            )
        )
        print(f"progress segment_end={seg.end:.2f}", file=sys.stderr, flush=True)

    result = WorkerResult(
        text="".join(s["text"] for s in segments),
        segments=segments,
        language=info.language,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = (output_dir / f"{audio_path.stem}.json").resolve()
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return json_path


def main() -> None:
    """Console / frozen entry point implementing the worker contract."""
    # FIRST: in frozen bundles on Windows/macOS multiprocessing re-executes
    # this binary; without freeze_support a re-exec would re-run main.
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser(
        prog="whisper-worker",
        description="Transcribe one audio file to whisper-ctranslate2-shaped JSON.",
    )
    parser.add_argument("audio", type=Path)
    parser.add_argument("--model", required=True, help="model name or local model directory")
    parser.add_argument("--device", default="cpu", choices=("cpu", "cuda"))
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--language", default=None)
    parser.add_argument("--output-dir", type=Path, default=Path.cwd())
    args = parser.parse_args()

    dll_directory = _prepare_windows_dll_path()  # before faster_whisper/ctypes loads
    try:
        device, compute_type = _preflight_cuda(args.device, args.compute_type)
        json_path = transcribe_audio(
            args.audio,
            args.output_dir,
            model=args.model,
            device=device,
            compute_type=compute_type,
            language=args.language,
        )
    except Exception as exc:
        # Non-zero exit with a human-readable stderr tail (worker contract).
        print(f"whisper-worker error: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1) from exc
    finally:
        if dll_directory is not None:
            dll_directory.close()
    print(str(json_path), flush=True)


def _prepare_windows_dll_path(platform: str = sys.platform) -> DllDirectoryHandle | None:
    """Join ``<data_dir>/runtime`` to the DLL search path (Windows, iff present).

    The CUDA pack installs cuBLAS/cuDNN DLLs there. ctranslate2 4.8.0 loads
    cuBLAS with plain ``LoadLibraryA``, whose legacy search order consults
    ``PATH`` but not ``os.add_dll_directory`` registrations. Prepending PATH
    covers that call; retaining the registration covers flagged dependent
    loads. Both happen before faster-whisper imports ctranslate2. A missing
    directory is harmless — the CPU path needs no extra DLLs.
    """
    if platform != "win32":
        return None
    runtime = data_dir_path() / "runtime"
    if runtime.is_dir():
        runtime_str = str(runtime)
        existing = os.environ.get("PATH")
        os.environ["PATH"] = f"{runtime_str}{os.pathsep}{existing}" if existing else runtime_str
        return cast("WindowsOs", os).add_dll_directory(runtime_str)
    return None


def _preflight_cuda(
    device: str, compute_type: str, platform: str = sys.platform
) -> tuple[str, str]:
    """Load CUDA roots before model construction; warn and use CPU on failure."""
    if device != "cuda" or platform != "win32":
        return device, compute_type
    try:
        ctypes.CDLL("cublas64_12.dll")
        ctypes.CDLL("cudnn64_9.dll")
    except OSError:
        print(f"{_CUDA_WARNING_PREFIX}{_CUDA_WARNING_MESSAGE}", file=sys.stderr, flush=True)
        return "cpu", "int8"
    return device, compute_type


if __name__ == "__main__":
    main()
