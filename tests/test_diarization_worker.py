"""Tests for podcast_reader.workers.diarization_worker (torch/pyannote mocked).

The worker module must be importable — and its argv/file contract testable —
without torch or pyannote.audio installed: the imports are lazy (inside the
diarization function), so tests inject fake ``torch`` and ``pyannote.audio``
modules into ``sys.modules`` instead of mocking top-level imports.
"""

from __future__ import annotations

import importlib
import json
import struct
import sys
import types
import wave
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from podcast_reader.workers import diarization_worker


def write_wav(
    path: Path,
    *,
    sample_rate: int = 16000,
    channels: int = 1,
    samples: list[int] | None = None,
    sampwidth: int = 2,
) -> None:
    """Write a small PCM WAV using only the stdlib."""
    values = samples if samples is not None else [0, 1000, -1000, 2000]
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(sampwidth)
        wav.setframerate(sample_rate)
        if sampwidth == 2:
            wav.writeframes(struct.pack(f"<{len(values)}h", *values))
        else:
            wav.writeframes(bytes(max(0, v) % 256 for v in values))


class FakeTensor:
    """Minimal tensor standing in for the torch ops the worker uses."""

    def __init__(self, values: list[float], channels: int = 1) -> None:
        self.values = list(values)
        self.channels = channels

    def to(self, _dtype: object) -> FakeTensor:
        return FakeTensor([float(v) for v in self.values], self.channels)

    def __truediv__(self, divisor: float) -> FakeTensor:
        return FakeTensor([v / divisor for v in self.values], self.channels)

    def reshape(self, _rows: int, channels: int) -> FakeTensor:
        return FakeTensor(self.values, channels)

    def mean(self, dim: int) -> FakeTensor:
        assert dim == 1
        ch = self.channels
        mixed = [sum(self.values[i : i + ch]) / ch for i in range(0, len(self.values), ch)]
        return FakeTensor(mixed)

    def __getitem__(self, key: object) -> FakeTensor:
        # waveform[None, :] — adds the batch dimension; values unchanged.
        return FakeTensor(self.values, self.channels)


def make_fake_torch() -> types.ModuleType:
    module = types.ModuleType("torch")
    module.int16 = "int16"  # type: ignore[attr-defined]
    module.float32 = "float32"  # type: ignore[attr-defined]

    def frombuffer(buffer: bytearray, dtype: str) -> FakeTensor:
        assert dtype == "int16"
        count = len(buffer) // 2
        return FakeTensor(list(struct.unpack(f"<{count}h", bytes(buffer))))

    module.frombuffer = frombuffer  # type: ignore[attr-defined]
    return module


@dataclass
class FakeTurn:
    start: float
    end: float


class FakeAnnotation:
    def __init__(self, tracks: list[tuple[FakeTurn, str, str]]) -> None:
        self.tracks = tracks

    def itertracks(self, yield_label: bool = False) -> list[tuple[FakeTurn, str, str]]:
        assert yield_label is True
        return self.tracks


DEFAULT_TRACKS = [
    (FakeTurn(0.0, 2.5), "A", "SPEAKER_00"),
    (FakeTurn(2.5, 5.0), "B", "SPEAKER_01"),
]


class FakePipeline:
    """Records from_pretrained/call args; returns scripted speaker turns."""

    instances: list[FakePipeline] = []
    result_factory: Any = staticmethod(lambda: FakeAnnotation(list(DEFAULT_TRACKS)))

    def __init__(self, checkpoint: str, cache_dir: object) -> None:
        self.checkpoint = checkpoint
        self.cache_dir = cache_dir
        self.call_args: tuple[dict[str, Any], int | None] | None = None
        FakePipeline.instances.append(self)

    @classmethod
    def from_pretrained(cls, checkpoint: str, cache_dir: object = None) -> FakePipeline:
        return cls(checkpoint, cache_dir)

    def __call__(self, file: dict[str, Any], num_speakers: int | None = None) -> Any:
        self.call_args = (file, num_speakers)
        return FakePipeline.result_factory()


@pytest.fixture
def fake_diarization_stack(monkeypatch: pytest.MonkeyPatch) -> type[FakePipeline]:
    FakePipeline.instances = []
    FakePipeline.result_factory = staticmethod(lambda: FakeAnnotation(list(DEFAULT_TRACKS)))
    monkeypatch.setitem(sys.modules, "torch", make_fake_torch())
    pyannote_pkg = types.ModuleType("pyannote")
    audio_mod = types.ModuleType("pyannote.audio")
    audio_mod.Pipeline = FakePipeline  # type: ignore[attr-defined]
    pyannote_pkg.audio = audio_mod  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pyannote", pyannote_pkg)
    monkeypatch.setitem(sys.modules, "pyannote.audio", audio_mod)
    return FakePipeline


class TestLazyImport:
    def test_module_imports_without_torch_or_pyannote(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spec: the main package never pulls torch — the worker module itself
        must import cleanly when the diarization extra is absent."""
        monkeypatch.setitem(sys.modules, "torch", None)
        monkeypatch.setitem(sys.modules, "pyannote.audio", None)
        reloaded = importlib.reload(diarization_worker)
        assert reloaded is not None

    def test_diarization_requires_the_stack(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setitem(sys.modules, "torch", None)
        monkeypatch.setitem(sys.modules, "pyannote.audio", None)
        write_wav(tmp_path / "a.wav")
        with pytest.raises(ImportError):
            diarization_worker.diarize_wav(
                tmp_path / "a.wav",
                tmp_path / "turns.json",
                model="pyannote/speaker-diarization-community-1",
                num_speakers=None,
                cache_dir=None,
            )


class TestReadWav:
    def test_reads_mono_16khz_pcm(self, tmp_path: Path) -> None:
        path = tmp_path / "a.wav"
        write_wav(path, samples=[0, 16384, -16384])

        frames, sample_rate, channels = diarization_worker.read_wav(path)

        assert sample_rate == 16000
        assert channels == 1
        assert struct.unpack("<3h", frames) == (0, 16384, -16384)

    def test_rejects_non_16bit_samples(self, tmp_path: Path) -> None:
        """The engine pre-converts to s16le; anything else is a contract bug
        worth a readable failure, not garbage audio."""
        path = tmp_path / "a.wav"
        write_wav(path, samples=[0, 1, 2, 3], sampwidth=1)

        with pytest.raises(ValueError, match="16-bit"):
            diarization_worker.read_wav(path)


class TestDiarizeWav:
    def test_writes_turns_json(
        self, fake_diarization_stack: type[FakePipeline], tmp_path: Path
    ) -> None:
        """Spec scenario: worker emits turns JSON — start/end floats plus
        speaker labels under a top-level "turns" key."""
        audio = tmp_path / "a.wav"
        write_wav(audio)
        output = tmp_path / "turns.json"

        diarization_worker.diarize_wav(
            audio,
            output,
            model="pyannote/speaker-diarization-community-1",
            num_speakers=None,
            cache_dir=None,
        )

        data = json.loads(output.read_text())
        assert data == {
            "turns": [
                {"start": 0.0, "end": 2.5, "speaker": "SPEAKER_00"},
                {"start": 2.5, "end": 5.0, "speaker": "SPEAKER_01"},
            ]
        }

    def test_feeds_pipeline_an_in_memory_waveform(
        self, fake_diarization_stack: type[FakePipeline], tmp_path: Path
    ) -> None:
        """Spec: stdlib WAV decoding feeding an in-memory waveform — no
        torchcodec/FFmpeg file decoding inside the worker."""
        audio = tmp_path / "a.wav"
        write_wav(audio, samples=[16384, -16384])
        output = tmp_path / "turns.json"

        diarization_worker.diarize_wav(
            audio,
            output,
            model="pyannote/speaker-diarization-community-1",
            num_speakers=3,
            cache_dir=None,
        )

        (pipeline,) = fake_diarization_stack.instances
        assert pipeline.call_args is not None
        file, num_speakers = pipeline.call_args
        assert num_speakers == 3
        assert file["sample_rate"] == 16000
        assert file["waveform"].values == [0.5, -0.5]

    def test_stereo_input_is_mixed_down(
        self, fake_diarization_stack: type[FakePipeline], tmp_path: Path
    ) -> None:
        audio = tmp_path / "a.wav"
        write_wav(audio, channels=2, samples=[16384, 0, 0, -16384])
        output = tmp_path / "turns.json"

        diarization_worker.diarize_wav(
            audio,
            output,
            model="pyannote/speaker-diarization-community-1",
            num_speakers=None,
            cache_dir=None,
        )

        (pipeline,) = fake_diarization_stack.instances
        assert pipeline.call_args is not None
        file, _ = pipeline.call_args
        assert file["waveform"].values == [0.25, -0.25]

    def test_pipeline_loaded_from_cache_dir(
        self, fake_diarization_stack: type[FakePipeline], tmp_path: Path
    ) -> None:
        audio = tmp_path / "a.wav"
        write_wav(audio)

        diarization_worker.diarize_wav(
            audio,
            tmp_path / "turns.json",
            model="custom/model",
            num_speakers=None,
            cache_dir=tmp_path / "cache",
        )

        (pipeline,) = fake_diarization_stack.instances
        assert pipeline.checkpoint == "custom/model"
        assert pipeline.cache_dir == tmp_path / "cache"

    def test_unwraps_community_pipeline_output(
        self, fake_diarization_stack: type[FakePipeline], tmp_path: Path
    ) -> None:
        """pyannote 4.x community pipelines return an output object carrying
        ``speaker_diarization``; 3.x pipelines return the Annotation bare.
        Both shapes must produce turns."""

        class CommunityOutput:
            speaker_diarization = FakeAnnotation([(FakeTurn(1.0, 4.0), "A", "SPEAKER_00")])

        fake_diarization_stack.result_factory = staticmethod(CommunityOutput)
        audio = tmp_path / "a.wav"
        write_wav(audio)
        output = tmp_path / "turns.json"

        diarization_worker.diarize_wav(
            audio,
            output,
            model="pyannote/speaker-diarization-community-1",
            num_speakers=None,
            cache_dir=None,
        )

        data = json.loads(output.read_text())
        assert data == {"turns": [{"start": 1.0, "end": 4.0, "speaker": "SPEAKER_00"}]}


class TestCacheResolution:
    def test_explicit_cache_dir_wins(self, tmp_path: Path) -> None:
        assert diarization_worker.resolve_cache_dir(tmp_path) == tmp_path

    def test_unfrozen_default_is_none(self) -> None:
        assert diarization_worker.resolve_cache_dir(None) is None

    def test_frozen_default_is_executable_sibling_cache(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Spec: the pack pre-seeds the HF cache next to the frozen worker."""
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", str(tmp_path / "diarization-worker"))
        assert diarization_worker.resolve_cache_dir(None) == tmp_path / "cache"

    def test_existing_cache_forces_offline_hub(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Spec scenario: offline pipeline load — with a seeded cache the hub
        must not be consulted."""
        monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
        monkeypatch.delenv("HUGGINGFACE_HUB_CACHE", raising=False)
        cache = tmp_path / "cache"
        cache.mkdir()

        diarization_worker.prepare_offline_cache(cache)

        import os

        assert os.environ["HF_HUB_OFFLINE"] == "1"
        assert os.environ["HUGGINGFACE_HUB_CACHE"] == str(cache)

    def test_missing_cache_leaves_environment_alone(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
        monkeypatch.delenv("HUGGINGFACE_HUB_CACHE", raising=False)

        diarization_worker.prepare_offline_cache(tmp_path / "absent")
        diarization_worker.prepare_offline_cache(None)

        import os

        assert "HF_HUB_OFFLINE" not in os.environ
        assert "HUGGINGFACE_HUB_CACHE" not in os.environ


class TestMain:
    def _argv(self, audio: Path, output: Path, *extra: str) -> list[str]:
        return ["diarization-worker", str(audio), "--output", str(output), *extra]

    def test_success_prints_output_path_on_stdout(
        self,
        fake_diarization_stack: type[FakePipeline],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        audio = tmp_path / "a.wav"
        write_wav(audio)
        output = tmp_path / "turns.json"
        monkeypatch.setattr(sys, "argv", self._argv(audio, output))

        diarization_worker.main()

        out = capsys.readouterr().out.strip()
        assert out == str(output.resolve())
        assert json.loads(output.read_text())["turns"]

    def test_num_speakers_flag_reaches_the_pipeline(
        self,
        fake_diarization_stack: type[FakePipeline],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        audio = tmp_path / "a.wav"
        write_wav(audio)
        output = tmp_path / "turns.json"
        monkeypatch.setattr(sys, "argv", self._argv(audio, output, "--num-speakers", "2"))

        diarization_worker.main()

        (pipeline,) = fake_diarization_stack.instances
        assert pipeline.call_args is not None
        assert pipeline.call_args[1] == 2

    def test_failure_exits_nonzero_with_readable_stderr_tail(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Spec: exit non-zero with a human-readable stderr tail on failure."""

        class ExplodingPipeline:
            @classmethod
            def from_pretrained(cls, *args: object, **kwargs: object) -> None:
                raise ValueError("pipeline checkpoint not found in cache")

        monkeypatch.setitem(sys.modules, "torch", make_fake_torch())
        audio_mod = types.ModuleType("pyannote.audio")
        audio_mod.Pipeline = ExplodingPipeline  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "pyannote.audio", audio_mod)
        audio = tmp_path / "a.wav"
        write_wav(audio)
        monkeypatch.setattr(sys, "argv", self._argv(audio, tmp_path / "turns.json"))

        with pytest.raises(SystemExit) as excinfo:
            diarization_worker.main()

        assert excinfo.value.code == 1
        err = capsys.readouterr().err.strip().splitlines()
        assert "pipeline checkpoint not found" in err[-1]

    def test_freeze_support_called_first(
        self,
        fake_diarization_stack: type[FakePipeline],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Spec: every frozen entry point calls multiprocessing.freeze_support()
        first (Windows re-exec safety)."""
        audio = tmp_path / "a.wav"
        write_wav(audio)
        monkeypatch.setattr(sys, "argv", self._argv(audio, tmp_path / "turns.json"))
        calls: list[str] = []
        monkeypatch.setattr(
            diarization_worker.multiprocessing, "freeze_support", lambda: calls.append("freeze")
        )

        diarization_worker.main()

        assert calls == ["freeze"]


def test_main_package_never_imports_the_worker() -> None:
    """Spec: the main package SHALL NOT import the worker module."""
    with patch.dict(sys.modules):
        for name in list(sys.modules):
            if name.startswith("podcast_reader"):
                del sys.modules[name]
        importlib.import_module("podcast_reader.pipeline")
        importlib.import_module("podcast_reader.cli")
        assert "podcast_reader.workers.diarization_worker" not in sys.modules
        assert "torch" not in sys.modules
