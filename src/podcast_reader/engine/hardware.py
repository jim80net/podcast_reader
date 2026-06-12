"""NVIDIA GPU detection and hardware-derived pack recommendations.

Detection mechanism — why ``nvidia-smi`` (design decision 9): the binary
ships with the display driver itself on both Windows and Linux, so a
successful probe proves a *working driver*, which is exactly what the CUDA
pack needs — unlike device enumeration (WMI, registry keys, lspci), which
happily lists a GPU whose driver is absent or broken, the precise case where
CUDA would fail at model load. It also needs no native Python dependency
(pynvml would be one more frozen wheel to ship) and degrades to a clean
"absent or nonzero exit" on every failure mode. The probe runs once and is
cached for the process lifetime; failure of any kind degrades to
``nvidia_gpu: false`` — detection must never break the packs endpoint.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading

from podcast_reader.engine.packs import HardwareInfo

logger = logging.getLogger(__name__)

_PROBE_TIMEOUT_S = 10.0

_cache: HardwareInfo | None = None
_cache_lock = threading.Lock()


def detect_hardware(platform: str = sys.platform) -> HardwareInfo:
    """Detected hardware, probed once and cached for the process lifetime."""
    global _cache
    with _cache_lock:
        if _cache is None:
            gpu_names = _probe_gpu_names(platform)
            _cache = HardwareInfo(
                platform=platform, nvidia_gpu=bool(gpu_names), gpu_names=gpu_names
            )
        return _cache


def reset_hardware_cache() -> None:
    """Drop the cached probe result (tests only)."""
    global _cache
    with _cache_lock:
        _cache = None


def recommended_pack_ids(hw: HardwareInfo) -> set[str]:
    """Hardware-derived recommendations (engine-side, per design decision 9).

    CUDA pack iff Windows with an NVIDIA GPU; ``large-v3`` with a GPU, the
    CPU-appropriate ``small`` otherwise; diarization never by default
    (strictly opt-in).
    """
    recommended: set[str] = set()
    if hw["nvidia_gpu"]:
        recommended.add("model-large-v3")
        if hw["platform"] == "win32":
            recommended.add("cuda-runtime")
    else:
        recommended.add("model-small")
    return recommended


def _nvidia_smi_candidates(platform: str) -> list[str]:
    """Probe locations: PATH, plus the standard Windows System32 install.

    The Windows driver installs ``nvidia-smi.exe`` under
    ``%SystemRoot%\\System32`` without necessarily adding it to PATH.
    """
    candidates = ["nvidia-smi"]
    if platform == "win32":
        # os.environ is case-insensitive on Windows, so the canonical
        # "SystemRoot" spelling is reachable via the upcased key.
        system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
        candidates.append(os.path.join(system_root, "System32", "nvidia-smi.exe"))
    return candidates


def _probe_gpu_names(platform: str) -> list[str]:
    """GPU names reported by ``nvidia-smi``, or ``[]`` on any failure."""
    for candidate in _nvidia_smi_candidates(platform):
        try:
            probe = subprocess.run(
                [candidate, "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=_PROBE_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.debug("nvidia-smi probe via %s failed: %s", candidate, exc)
            continue
        if probe.returncode != 0:
            logger.debug(
                "nvidia-smi at %s exited %s; treating as no NVIDIA GPU",
                candidate,
                probe.returncode,
            )
            continue
        names = [line.strip() for line in probe.stdout.splitlines() if line.strip()]
        if names:
            return names
    return []
