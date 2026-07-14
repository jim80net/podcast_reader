"""Tests for podcast_reader.workers.whisper_worker (faster-whisper mocked).

The worker module must be importable — and its argv/file/progress contract
testable — without faster-whisper installed: the import is lazy (inside the
transcription function), so tests inject a fake ``faster_whisper`` module
into ``sys.modules`` instead of mocking a top-level import.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from podcast_reader.workers import whisper_worker

if TYPE_CHECKING:
    from collections.abc import Iterator


@dataclass
class FakeSegment:
    id: int = 1
    seek: int = 0
    start: float = 0.0
    end: float = 2.5
    text: str = " Hello world."
    tokens: list[int] = field(default_factory=lambda: [1, 2, 3])
    temperature: float = 0.0
    avg_logprob: float = -0.2
    compression_ratio: float = 1.1
    no_speech_prob: float = 0.01


@dataclass
class FakeInfo:
    language: str = "en"
    duration: float = 5.0


class FakeWhisperModel:
    """Records constructor args; yields two scripted segments."""

    instances: list[FakeWhisperModel] = []

    def __init__(self, model: str, device: str, compute_type: str) -> None:
        self.model = model
        self.device = device
        self.compute_type = compute_type
        self.transcribe_args: tuple[str, str | None] | None = None
        FakeWhisperModel.instances.append(self)

    def transcribe(
        self, audio: str, language: str | None = None
    ) -> tuple[Iterator[FakeSegment], FakeInfo]:
        self.transcribe_args = (audio, language)
        segments = iter(
            [
                FakeSegment(id=1, start=0.0, end=2.5, text=" Hello"),
                FakeSegment(id=2, start=2.5, end=5.0, text=" world."),
            ]
        )
        return segments, FakeInfo()


@pytest.fixture
def fake_faster_whisper(monkeypatch: pytest.MonkeyPatch) -> type[FakeWhisperModel]:
    FakeWhisperModel.instances = []
    module = types.ModuleType("faster_whisper")
    module.WhisperModel = FakeWhisperModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    return FakeWhisperModel


class TestLazyImport:
    def test_module_imports_without_faster_whisper(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Spec: the main package never pulls faster-whisper — the worker
        module itself must import cleanly when the extra is absent."""
        monkeypatch.setitem(sys.modules, "faster_whisper", None)
        reloaded = importlib.reload(whisper_worker)
        assert reloaded is not None

    def test_transcription_requires_faster_whisper(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setitem(sys.modules, "faster_whisper", None)
        with pytest.raises(ImportError):
            whisper_worker.transcribe_audio(
                tmp_path / "a.wav",
                tmp_path,
                model="tiny",
                device="cpu",
                compute_type="int8",
                language="en",
            )


class TestTranscribeAudio:
    def test_writes_json_artifact_with_ctranslate2_shape(
        self,
        fake_faster_whisper: type[FakeWhisperModel],
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Spec scenarios: JSON at <output-dir>/<stem>.json; words present as null."""
        audio = tmp_path / "episode.wav"
        audio.write_bytes(b"RIFF")
        out_dir = tmp_path / "out"

        json_path = whisper_worker.transcribe_audio(
            audio, out_dir, model="tiny", device="cpu", compute_type="int8", language="en"
        )

        assert json_path == (out_dir / "episode.json").resolve()
        data = json.loads(json_path.read_text())
        assert sorted(data) == ["language", "segments", "text"]
        assert data["language"] == "en"
        assert data["text"] == " Hello world."
        assert [s["text"] for s in data["segments"]] == [" Hello", " world."]
        for segment in data["segments"]:
            assert segment["words"] is None
            assert sorted(segment) == [
                "avg_logprob",
                "compression_ratio",
                "end",
                "id",
                "no_speech_prob",
                "seek",
                "start",
                "temperature",
                "text",
                "tokens",
                "words",
            ]

    def test_model_invocation_carries_argv_settings(
        self, fake_faster_whisper: type[FakeWhisperModel], tmp_path: Path
    ) -> None:
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"RIFF")

        whisper_worker.transcribe_audio(
            audio,
            tmp_path,
            model="/packs/models/tiny",
            device="cuda",
            compute_type="float16",
            language="de",
        )

        (instance,) = fake_faster_whisper.instances
        assert instance.model == "/packs/models/tiny"
        assert instance.device == "cuda"
        assert instance.compute_type == "float16"
        assert instance.transcribe_args == (str(audio), "de")

    def test_emits_progress_line_protocol_on_stderr(
        self,
        fake_faster_whisper: type[FakeWhisperModel],
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Spec: `progress duration=` once after model load, then
        `progress segment_end=` per transcribed segment."""
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"RIFF")

        whisper_worker.transcribe_audio(
            audio, tmp_path, model="tiny", device="cpu", compute_type="int8", language=None
        )

        err_lines = capsys.readouterr().err.splitlines()
        assert err_lines == [
            "progress duration=5.00",
            "progress segment_end=2.50",
            "progress segment_end=5.00",
        ]


class TestMain:
    def _argv(self, audio: Path, out_dir: Path) -> list[str]:
        return [
            "whisper-worker",
            str(audio),
            "--model",
            "tiny",
            "--device",
            "cpu",
            "--compute-type",
            "int8",
            "--language",
            "en",
            "--output-dir",
            str(out_dir),
        ]

    def test_success_prints_absolute_json_path_on_stdout(
        self,
        fake_faster_whisper: type[FakeWhisperModel],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        audio = tmp_path / "episode.wav"
        audio.write_bytes(b"RIFF")
        monkeypatch.setattr(sys, "argv", self._argv(audio, tmp_path))

        whisper_worker.main()

        out = capsys.readouterr().out.strip()
        assert out == str((tmp_path / "episode.json").resolve())
        assert Path(out).is_absolute()
        assert json.loads(Path(out).read_text())["language"] == "en"

    def test_failure_exits_nonzero_with_readable_stderr_tail(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Spec scenario: failure is diagnosable — non-zero exit, stderr ends
        with a human-readable error."""

        class ExplodingModel:
            def __init__(self, *args: object, **kwargs: object) -> None:
                raise ValueError("bad model directory: not a CTranslate2 model")

        module = types.ModuleType("faster_whisper")
        module.WhisperModel = ExplodingModel  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "faster_whisper", module)
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"RIFF")
        monkeypatch.setattr(sys, "argv", self._argv(audio, tmp_path))

        with pytest.raises(SystemExit) as excinfo:
            whisper_worker.main()

        assert excinfo.value.code == 1
        err = capsys.readouterr().err.strip().splitlines()
        assert "bad model directory" in err[-1]

    def test_freeze_support_called_first(
        self,
        fake_faster_whisper: type[FakeWhisperModel],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Spec: every frozen entry point calls multiprocessing.freeze_support()
        first (Windows re-exec safety)."""
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"RIFF")
        monkeypatch.setattr(sys, "argv", self._argv(audio, tmp_path))
        calls: list[str] = []
        monkeypatch.setattr(
            whisper_worker.multiprocessing, "freeze_support", lambda: calls.append("freeze")
        )
        original_prepare = whisper_worker._prepare_windows_dll_path

        def spy_prepare() -> whisper_worker.DllDirectoryHandle | None:
            calls.append("dll-path")
            return original_prepare()

        monkeypatch.setattr(whisper_worker, "_prepare_windows_dll_path", spy_prepare)

        whisper_worker.main()

        assert calls[0] == "freeze"
        assert "dll-path" in calls

    def test_missing_runtime_dir_is_harmless(
        self,
        fake_faster_whisper: type[FakeWhisperModel],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Spec scenario: no runtime directory — the worker starts normally on
        the CPU path (the DLL-path prep is a no-op)."""
        monkeypatch.setenv("PODCAST_READER_DATA_DIR", str(tmp_path / "nonexistent"))
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"RIFF")
        monkeypatch.setattr(sys, "argv", self._argv(audio, tmp_path))

        whisper_worker.main()  # must not raise

        assert (tmp_path / "episode.json").exists() is False  # stem is a.json
        assert (tmp_path / "a.json").exists()

    def test_cuda_preflight_fallback_reaches_model_as_cpu_int8(
        self,
        fake_faster_whisper: type[FakeWhisperModel],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"RIFF")
        argv = self._argv(audio, tmp_path)
        argv[argv.index("cpu")] = "cuda"
        monkeypatch.setattr(sys, "argv", argv)
        monkeypatch.setattr(whisper_worker, "_preflight_cuda", lambda *_args: ("cpu", "int8"))

        whisper_worker.main()

        (instance,) = fake_faster_whisper.instances
        assert instance.device == "cpu"
        assert instance.compute_type == "int8"

    def test_cuda_runtime_check_mode_needs_no_audio_or_model(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["whisper-worker", "--check-cuda-runtime"])
        monkeypatch.setattr(whisper_worker, "_prepare_windows_dll_path", lambda: None)
        checked: list[bool] = []
        monkeypatch.setattr(
            whisper_worker, "_check_cuda_runtime_loadable", lambda: checked.append(True)
        )

        whisper_worker.main()

        assert checked == [True]
        assert capsys.readouterr().out.strip() == "cuda-runtime ready"


class TestWindowsDllPath:
    def test_noop_on_posix(self) -> None:
        assert whisper_worker._prepare_windows_dll_path(platform="linux") is None

    def test_adds_runtime_dir_when_present_on_windows(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spec scenario: CUDA pack DLLs resolvable at model load — the
        runtime dir joins the DLL search path before faster-whisper import."""
        runtime = tmp_path / "runtime"
        runtime.mkdir()
        monkeypatch.setenv("PODCAST_READER_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("PATH", "existing-tools")
        added: list[str] = []
        handle = object()

        def add(path: str) -> object:
            added.append(path)
            return handle

        monkeypatch.setattr(whisper_worker.os, "add_dll_directory", add, raising=False)

        result = whisper_worker._prepare_windows_dll_path(platform="win32")

        assert added == [str(runtime)]
        assert result is handle
        assert os.environ["PATH"] == f"{runtime}{os.pathsep}existing-tools"

    def test_skips_absent_runtime_dir_on_windows(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PODCAST_READER_DATA_DIR", str(tmp_path))
        added: list[str] = []
        monkeypatch.setattr(whisper_worker.os, "add_dll_directory", added.append, raising=False)

        result = whisper_worker._prepare_windows_dll_path(platform="win32")

        assert added == []
        assert result is None


class TestCudaPreflight:
    @pytest.mark.parametrize(
        ("device", "platform"),
        [("cpu", "win32"), ("cuda", "linux")],
    )
    def test_non_windows_or_non_cuda_does_not_load_libraries(
        self,
        device: str,
        platform: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        loaded: list[str] = []
        monkeypatch.setattr(whisper_worker.ctypes, "CDLL", loaded.append)

        result = whisper_worker._preflight_cuda(device, "float16", platform=platform)

        assert result == (device, "float16")
        assert loaded == []

    def test_windows_cuda_loads_roots_before_preserving_gpu_settings(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        loaded: list[tuple[str, int]] = []

        def load(name: str, *, winmode: int) -> None:
            loaded.append((name, winmode))

        monkeypatch.setattr(whisper_worker.ctypes, "CDLL", load)

        result = whisper_worker._preflight_cuda("cuda", "float16", platform="win32")

        assert result == ("cuda", "float16")
        assert loaded == [("cublas64_12.dll", 0), ("cudnn64_9.dll", 0)]

    def test_windows_cuda_load_failure_warns_without_raw_dll_and_uses_cpu(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def fail(_name: str, *, winmode: int) -> None:
            assert winmode == 0
            raise OSError("Library cublas64_12.dll is not found or cannot be loaded")

        monkeypatch.setattr(whisper_worker.ctypes, "CDLL", fail)

        result = whisper_worker._preflight_cuda("cuda", "float16", platform="win32")

        assert result == ("cpu", "int8")
        warning = capsys.readouterr().err.strip()
        assert warning.startswith("warning cuda_unavailable ")
        assert "Settings → Packs" in warning
        assert "cublas" not in warning.casefold()
        assert ".dll" not in warning.casefold()


class TestDataDirPath:
    def test_does_not_create_the_directory(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from podcast_reader.engine.settings import data_dir_path

        target = tmp_path / "never-created"
        monkeypatch.setenv("PODCAST_READER_DATA_DIR", str(target))
        assert data_dir_path() == target
        assert not target.exists()


def test_main_package_never_imports_the_worker() -> None:
    """Spec: the main package SHALL NOT import the worker module."""
    with patch.dict(sys.modules):
        for name in list(sys.modules):
            if name.startswith("podcast_reader"):
                del sys.modules[name]
        importlib.import_module("podcast_reader.pipeline")
        importlib.import_module("podcast_reader.cli")
        assert "podcast_reader.workers.whisper_worker" not in sys.modules
        assert "faster_whisper" not in sys.modules
