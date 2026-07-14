"""Production whisper worker: argv in, file out, line-protocol progress on stderr.

Contract (design decision 4, adopted verbatim from the packaging spike):

    whisper-worker AUDIO --model <name-or-dir> --device cpu|cuda \\
        --compute-type int8|float16 [--language xx] --output-dir DIR
    stderr: "progress duration=<sec>" once after model load,
            "progress segment_end=<sec>" per transcribed segment
    stdout: absolute path of the written JSON on success
    exit:   0 ok / non-zero with a human-readable stderr tail

The JSON is whisper-ctranslate2-shaped (top-level ``{text, segments,
language}``; per-segment ``"words": null`` when word timestamps are not
computed) so ``html.py`` and the chapters step consume it unchanged.

faster-whisper is imported lazily inside :func:`transcribe_audio` — the
module itself imports without the ``worker`` extra, and the main package
never imports this module at all. On Windows the CUDA runtime pack's
directory joins the DLL search path *before* that import; on POSIX the
spawner sets ``LD_LIBRARY_PATH`` instead (an in-process mutation cannot
affect an already-running loader).
"""

from __future__ import annotations

import argparse
import ctypes
import json
import multiprocessing
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from typing_extensions import TypedDict

from podcast_reader.engine.settings import data_dir_path

if TYPE_CHECKING:

    class DllDirectoryHandle(Protocol):
        """The closeable token returned by ``os.add_dll_directory``."""

        def close(self) -> None:
            """Remove the registered directory from the DLL search path."""


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
    parser.add_argument("audio", type=Path, nargs="?")
    parser.add_argument("--model", help="model name or local model directory")
    parser.add_argument("--device", default="cpu", choices=("cpu", "cuda"))
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--language", default=None)
    parser.add_argument("--output-dir", type=Path, default=Path.cwd())
    parser.add_argument(
        "--check-cuda-runtime",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()

    if not args.check_cuda_runtime and (args.audio is None or args.model is None):
        parser.error("audio and --model are required for transcription")

    # Keep the returned token alive until every CUDA load/transcription is
    # complete. CPython removes the directory when this object is closed (or
    # garbage-collected); discarding it here unregisters the pack immediately.
    dll_directory: DllDirectoryHandle | None = None
    try:
        dll_directory = _prepare_windows_dll_path()  # before faster_whisper/ctypes loads
        if args.check_cuda_runtime:
            _check_cuda_runtime_loadable()
            print("cuda-runtime ready", flush=True)
            return
        assert args.audio is not None
        assert args.model is not None
        json_path = transcribe_audio(
            args.audio,
            args.output_dir,
            model=args.model,
            device=args.device,
            compute_type=args.compute_type,
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


def _prepare_windows_dll_path() -> DllDirectoryHandle | None:
    """Join ``<data_dir>/runtime`` to the DLL search path (Windows, iff present).

    The CUDA pack installs cuBLAS/cuDNN DLLs there; ``os.add_dll_directory``
    must run before faster-whisper (and transitively ctranslate2) is
    imported, or CUDA model load fails. A missing directory is harmless —
    the CPU path needs no extra DLLs.
    """
    if sys.platform != "win32":
        return None
    runtime = data_dir_path() / "runtime"
    if runtime.is_dir():
        return os.add_dll_directory(str(runtime))
    return None


def _check_cuda_runtime_loadable() -> None:
    """Load the two root CUDA DLLs without requiring an NVIDIA GPU.

    The frozen Windows pack smoke invokes this after installing the runtime
    pack. Loading by basename proves the worker kept its private runtime
    directory registered with the Windows loader; the manifest separately
    proves the complete dependent cuDNN DLL set exists.
    """
    if sys.platform != "win32":
        raise RuntimeError("CUDA runtime loading is supported only on Windows")
    for name in ("cublas64_12.dll", "cudnn64_9.dll"):
        ctypes.CDLL(name)


if __name__ == "__main__":
    main()
