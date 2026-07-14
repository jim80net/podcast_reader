"""Orchestrate transcription: bundled whisper worker (frozen) or whisper-ctranslate2.

The freeze-aware switch (design decision 5): when
``resolve_bundled_worker("whisper-worker")`` resolves — only inside a frozen
onedir bundle — transcription runs through the worker contract (model packs
resolved from the engine data dir, per-segment progress streamed from worker
stderr). When it returns ``None`` (dev, CLI, headless serve), the existing
``whisper-ctranslate2`` shell-out runs byte-identical to before, including HF
auto-download and ``--hf_token`` diarization.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path  # noqa: TC003 — used at runtime in path operations
from typing import TYPE_CHECKING

from podcast_reader.engine import packs
from podcast_reader.engine.hardware import detect_hardware
from podcast_reader.engine.settings import data_dir
from podcast_reader.tools import (
    resolve_bundled_worker,
    resolve_tool,
    run_child,
    run_child_streaming,
)
from podcast_reader.types import PipelineError, PipelineEvent

if TYPE_CHECKING:
    from collections.abc import Callable

    from podcast_reader.engine.packs import PackEntry

#: Name of the bundled worker executable (a sibling of the frozen engine).
WORKER_NAME = "whisper-worker"

_MODEL_HINT = (
    "Download the model from the app's setup wizard or Settings → Packs, then retry the job."
)

_CUDA_REPAIR_HINT = (
    "To keep working, set Settings → Device to CPU and retry. To repair GPU transcription, "
    "uninstall and reinstall NVIDIA CUDA runtime (cuBLAS + cuDNN 9) in Settings → Packs."
)

_CUDA_LOAD_ERROR_MARKERS = ("cublas64_", "cublaslt64_", "cudnn64_", "cudnn_")

#: Worker progress line protocol: `progress duration=<sec>` once after model
#: load, `progress segment_end=<sec>` per transcribed segment.
_PROGRESS_RE = re.compile(r"^progress (duration|segment_end)=(\d+(?:\.\d+)?)$")


def transcription_engine() -> str:
    """Name of the engine the transcribe step will use.

    ``whisper-worker`` inside a frozen bundle, ``whisper-ctranslate2``
    everywhere else — feeds the step message and the HTML source label, so
    unfrozen output stays byte-identical to before the switch.
    """
    if resolve_bundled_worker(WORKER_NAME) is not None:
        return WORKER_NAME
    return "whisper-ctranslate2"


def build_whisper_args(
    audio_path: Path,
    output_dir: Path,
    model: str,
    lang: str,
    device: str,
    hf_token: str | None = None,
) -> list[str]:
    """Build the whisper-ctranslate2 command-line arguments."""
    args = [
        resolve_tool("whisper-ctranslate2"),
        str(audio_path),
        "--model",
        model,
        "--language",
        lang,
        "--device",
        device,
        "--output_format",
        "json",
        "--output_dir",
        str(output_dir),
        "--print_colors",
        "False",
    ]
    if hf_token is not None:
        args.extend(["--hf_token", hf_token])
    return args


def transcribe(
    audio_path: Path,
    output_dir: Path,
    model: str,
    lang: str,
    device: str,
    hf_token: str | None = None,
    on_event: Callable[[PipelineEvent], None] | None = None,
) -> Path:
    """Transcribe an audio file and return the path to the JSON output.

    Prefers the bundled ``whisper-worker`` (frozen path; *on_event* receives
    ``step_progress`` and device-fallback ``warning`` events); otherwise runs
    whisper-ctranslate2 exactly as before this switch existed.
    """
    worker = resolve_bundled_worker(WORKER_NAME)
    if worker is not None:
        return _transcribe_via_worker(
            worker,
            audio_path=audio_path,
            output_dir=output_dir,
            model=model,
            lang=lang,
            device=device,
            on_event=on_event,
        )
    args = build_whisper_args(audio_path, output_dir, model, lang, device, hf_token)
    try:
        result = run_child(args)
    except FileNotFoundError as exc:
        raise RuntimeError(
            "whisper-ctranslate2 not found — install the 'whisper' extra, e.g. "
            "uv tool install 'podcast-reader[whisper]' or uv sync --extra whisper"
        ) from exc
    if result.returncode != 0:
        raise RuntimeError(f"whisper-ctranslate2 failed: {result.stderr.strip()}")

    json_path = output_dir / f"{audio_path.stem}.json"
    return json_path


def _transcribe_via_worker(
    worker: str,
    *,
    audio_path: Path,
    output_dir: Path,
    model: str,
    lang: str,
    device: str,
    on_event: Callable[[PipelineEvent], None] | None,
) -> Path:
    """Spawn the bundled whisper worker with streamed per-segment progress.

    The model pack manifest is validated at step start (per S1): a pack whose
    manifest is gone — including one removed by a concurrent uninstall — is a
    missing pack, so the worst case of any race is the structured
    ``model_missing`` failure, never a partial read. No mid-job download is
    ever attempted. The effective device degrades ``cuda`` → ``cpu`` with a
    reason-naming warning (per S4); compute type derives from the effective
    device.
    """
    base = data_dir()
    model_dir = _validated_model_dir(base, model)
    effective_device = _effective_device(base, device, on_event)
    compute_type = "float16" if effective_device == "cuda" else "int8"
    args = [
        worker,
        str(audio_path),
        "--model",
        str(model_dir),
        "--device",
        effective_device,
        "--compute-type",
        compute_type,
        "--language",
        lang,
        "--output-dir",
        str(output_dir),
    ]
    duration: float | None = None

    def handle_stderr_line(line: str) -> None:
        nonlocal duration
        match = _PROGRESS_RE.match(line.strip())
        if match is None or on_event is None:
            return
        value = float(match.group(2))
        if match.group(1) == "duration":
            duration = value
            seconds = 0.0
        else:
            seconds = value
        on_event(
            PipelineEvent(
                kind="step_progress",
                step="transcribe",
                message="",
                data={"seconds": seconds, "duration": duration},
            )
        )

    result = run_child_streaming(args, on_stderr_line=handle_stderr_line, env=_worker_env(base))
    if result.returncode != 0:
        tail = "\n".join(result.stderr.strip().splitlines()[-5:])
        if effective_device == "cuda" and _cuda_runtime_load_failed(result.stderr):
            raise PipelineError(
                "cuda_runtime_unavailable",
                "The NVIDIA CUDA runtime could not be loaded for GPU transcription",
                _CUDA_REPAIR_HINT,
                tail,
            )
        raise RuntimeError(f"whisper-worker failed: {tail}")
    return output_dir / f"{audio_path.stem}.json"


def _validated_model_dir(base: Path, model: str) -> Path:
    """Resolve *model* to its installed pack directory, validating the manifest.

    Missing, incompatible, and integrity-failed packs are all treated as
    absent by the pipeline (per S8) and fail structured ``model_missing``
    with an install hint — never a mid-job download.
    """
    entry = packs.REGISTRY.get(f"model-{model}")
    if entry is None:
        raise PipelineError(
            "model_missing",
            f"No downloadable model pack exists for whisper model {model!r}",
            "Pick a packaged model (tiny, small, medium, large-v3) in Settings.",
        )
    target = packs.pack_dir(base, entry)
    manifest = packs.read_manifest(target)
    if manifest is None:
        raise PipelineError(
            "model_missing",
            f"Whisper model {model!r} is not installed",
            _MODEL_HINT,
        )
    error = packs.compat_error(entry, manifest) or packs.pack_files_error(entry, target, manifest)
    if error is not None:
        raise PipelineError(
            "model_missing",
            f"The installed whisper model {model!r} is unusable: {error}",
            _MODEL_HINT,
        )
    return target


def _effective_device(
    base: Path,
    device: str,
    on_event: Callable[[PipelineEvent], None] | None,
    platform: str = sys.platform,
) -> str:
    """Degrade ``cuda`` to ``cpu`` when CUDA cannot work — warn, don't fail.

    The warning names the specific reason (no GPU / pack missing / pack
    unusable) and is suppressed on platforms where the CUDA pack is
    registry-unavailable (per S4): when nothing is installable, the warning
    would be noise. The hardware probe runs only where the pack could exist.
    """
    if device != "cuda":
        return device
    entry = packs.REGISTRY["cuda-runtime"]
    if not (packs.is_published(entry) and packs.platform_supported(entry, platform)):
        return "cpu"
    reason = _cuda_unavailable_reason(base, entry, platform)
    if reason is None:
        return "cuda"
    if on_event is not None:
        on_event(
            PipelineEvent(
                kind="warning",
                step="transcribe",
                message=f"CUDA requested but unavailable ({reason}); transcribing on CPU",
                data={"code": "cuda_unavailable", "reason": reason},
            )
        )
    return "cpu"


def _cuda_unavailable_reason(base: Path, entry: PackEntry, platform: str) -> str | None:
    """Why CUDA cannot be used right now, or ``None`` when it can."""
    if not detect_hardware(platform)["nvidia_gpu"]:
        return "no NVIDIA GPU detected"
    target = packs.pack_dir(base, entry)
    manifest = packs.read_manifest(target)
    if manifest is None:
        return "the CUDA runtime pack is not installed"
    if packs.compat_error(entry, manifest) is not None:
        return "the installed CUDA runtime pack is incompatible with this engine build"
    integrity_error = packs.pack_files_error(entry, target, manifest)
    if integrity_error is not None:
        return (
            "the installed CUDA runtime pack is incomplete or damaged "
            f"({integrity_error}); reinstall it in Settings → Packs"
        )
    return None


def _cuda_runtime_load_failed(stderr: str) -> bool:
    """True when the worker names a CUDA runtime DLL loader failure."""
    lowered = stderr.casefold()
    return any(marker in lowered for marker in _CUDA_LOAD_ERROR_MARKERS) and any(
        phrase in lowered
        for phrase in ("not found", "cannot be loaded", "could not load", "failed to load")
    )


def _worker_env(base: Path) -> dict[str, str] | None:
    """Child environment for the worker spawn.

    POSIX: ``LD_LIBRARY_PATH`` gains ``<data_dir>/runtime`` (the spec's
    spawn-time contract — an in-process mutation cannot affect an
    already-running loader). Windows: ``None`` (inherit) — before importing
    faster-whisper, the worker prepends the runtime directory to its own
    ``PATH`` for ctranslate2's plain ``LoadLibraryA`` calls and also registers
    it with ``os.add_dll_directory`` for flagged loads.
    """
    if sys.platform == "win32":
        return None
    env = dict(os.environ)
    runtime = str(base / "runtime")
    existing = env.get("LD_LIBRARY_PATH")
    env["LD_LIBRARY_PATH"] = f"{runtime}{os.pathsep}{existing}" if existing else runtime
    return env
