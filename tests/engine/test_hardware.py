"""Tests for podcast_reader.engine.hardware (probe + recommendations).

Spec: runtime-packs "Hardware detection" / "Hardware-derived
recommendations" — the probe is mocked everywhere; no test touches a GPU.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

from podcast_reader.engine import hardware
from podcast_reader.engine.hardware import (
    detect_hardware,
    recommended_pack_ids,
    reset_hardware_cache,
)
from podcast_reader.engine.packs import REGISTRY, HardwareInfo

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    reset_hardware_cache()
    yield
    reset_hardware_cache()


def _completed(returncode: int, stdout: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["nvidia-smi"], returncode=returncode, stdout=stdout)


class TestProbe:
    def test_nvidia_machine_detected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Spec scenario: NVIDIA machine detected."""
        monkeypatch.setattr(
            hardware.subprocess,
            "run",
            lambda *args, **kwargs: _completed(0, "NVIDIA GeForce RTX 4090\n"),
        )
        info = detect_hardware("win32")
        assert info == HardwareInfo(
            platform="win32", nvidia_gpu=True, gpu_names=["NVIDIA GeForce RTX 4090"]
        )

    def test_multiple_gpus_listed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            hardware.subprocess,
            "run",
            lambda *args, **kwargs: _completed(0, "RTX 4090\nRTX 3060\n"),
        )
        assert detect_hardware("linux")["gpu_names"] == ["RTX 4090", "RTX 3060"]

    def test_absent_binary_degrades_cleanly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Spec scenario: Probe failure degrades cleanly."""

        def missing(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            raise FileNotFoundError("nvidia-smi")

        monkeypatch.setattr(hardware.subprocess, "run", missing)
        info = detect_hardware("linux")
        assert info == HardwareInfo(platform="linux", nvidia_gpu=False, gpu_names=[])

    def test_nonzero_exit_degrades_cleanly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            hardware.subprocess,
            "run",
            lambda *args, **kwargs: _completed(9, "driver not loaded"),
        )
        assert detect_hardware("linux")["nvidia_gpu"] is False

    def test_timeout_degrades_cleanly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def hangs(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            raise subprocess.TimeoutExpired(cmd="nvidia-smi", timeout=1)

        monkeypatch.setattr(hardware.subprocess, "run", hangs)
        assert detect_hardware("win32")["nvidia_gpu"] is False

    def test_empty_output_is_no_gpu(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(hardware.subprocess, "run", lambda *args, **kwargs: _completed(0, "\n"))
        assert detect_hardware("linux")["nvidia_gpu"] is False

    def test_result_is_cached_for_process_lifetime(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = {"n": 0}

        def counting(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls["n"] += 1
            return _completed(0, "RTX 4090\n")

        monkeypatch.setattr(hardware.subprocess, "run", counting)
        first = detect_hardware("win32")
        second = detect_hardware("win32")
        assert first == second
        assert calls["n"] == 1

    def test_windows_candidates_include_system32(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The driver installs nvidia-smi under %SystemRoot%\\System32 even
        when PATH does not carry it."""
        monkeypatch.setenv("SYSTEMROOT", r"C:\Windows")
        candidates = hardware._nvidia_smi_candidates("win32")
        assert candidates[0] == "nvidia-smi"
        assert r"C:\Windows" in candidates[1]
        assert "System32" in candidates[1]

    def test_posix_candidates_are_path_only(self) -> None:
        assert hardware._nvidia_smi_candidates("linux") == ["nvidia-smi"]

    def test_second_candidate_used_when_first_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[str] = []

        def first_missing(
            cmd: list[str], *args: object, **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            seen.append(cmd[0])
            if len(seen) == 1:
                raise FileNotFoundError(cmd[0])
            return _completed(0, "RTX 4090\n")

        monkeypatch.setattr(hardware.subprocess, "run", first_missing)
        monkeypatch.setenv("SYSTEMROOT", r"C:\Windows")
        info = detect_hardware("win32")
        assert info["nvidia_gpu"] is True
        assert len(seen) == 2


class TestRecommendations:
    def _hw(self, platform: str, gpu: bool) -> HardwareInfo:
        return HardwareInfo(
            platform=platform, nvidia_gpu=gpu, gpu_names=["RTX 4090"] if gpu else []
        )

    def test_windows_gpu_machine(self) -> None:
        """Spec scenario: GPU machine recommendation — CUDA pack and
        large-v3 on Windows with NVIDIA."""
        assert recommended_pack_ids(self._hw("win32", True)) == {
            "cuda-runtime",
            "model-large-v3",
        }

    def test_linux_gpu_machine_gets_no_cuda_pack(self) -> None:
        """The CUDA pack is Windows-only; a GPU elsewhere still earns large-v3."""
        assert recommended_pack_ids(self._hw("linux", True)) == {"model-large-v3"}

    def test_cpu_machine(self) -> None:
        """Spec scenario: CPU machine recommendation."""
        assert recommended_pack_ids(self._hw("win32", False)) == {"model-small"}
        assert recommended_pack_ids(self._hw("darwin", False)) == {"model-small"}

    def test_diarization_never_recommended(self) -> None:
        for platform in ("win32", "darwin", "linux"):
            for gpu in (True, False):
                assert "diarization" not in recommended_pack_ids(self._hw(platform, gpu))

    def test_recommended_ids_exist_in_registry(self) -> None:
        for platform in ("win32", "darwin", "linux"):
            for gpu in (True, False):
                assert recommended_pack_ids(self._hw(platform, gpu)) <= set(REGISTRY)
