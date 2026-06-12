"""Tests for podcast_reader.transcribe module."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from podcast_reader.engine.packs import MANIFEST_FILE, REGISTRY, HardwareInfo, pack_dir
from podcast_reader.transcribe import (
    _effective_device,
    build_whisper_args,
    transcribe,
    transcription_engine,
)
from podcast_reader.types import PipelineError

if TYPE_CHECKING:
    from collections.abc import Callable

    from podcast_reader.types import PipelineEvent


@patch("podcast_reader.transcribe.resolve_tool", return_value="whisper-ctranslate2")
class TestBuildWhisperArgs:
    def test_basic_args(self, _mock_resolve: MagicMock) -> None:
        result = build_whisper_args(
            audio_path=Path("/tmp/episode.mp3"),
            output_dir=Path("/tmp/out"),
            model="large-v3",
            lang="en",
            device="cuda",
        )
        assert result == [
            "whisper-ctranslate2",
            "/tmp/episode.mp3",
            "--model",
            "large-v3",
            "--language",
            "en",
            "--device",
            "cuda",
            "--output_format",
            "json",
            "--output_dir",
            "/tmp/out",
            "--print_colors",
            "False",
        ]

    def test_with_hf_token(self, _mock_resolve: MagicMock) -> None:
        result = build_whisper_args(
            audio_path=Path("/tmp/episode.mp3"),
            output_dir=Path("/tmp/out"),
            model="large-v3",
            lang="en",
            device="cuda",
            hf_token="hf_abc123",
        )
        assert "--hf_token" in result
        idx = result.index("--hf_token")
        assert result[idx + 1] == "hf_abc123"

    def test_without_hf_token(self, _mock_resolve: MagicMock) -> None:
        result = build_whisper_args(
            audio_path=Path("/tmp/episode.mp3"),
            output_dir=Path("/tmp/out"),
            model="large-v3",
            lang="en",
            device="cpu",
        )
        assert "--hf_token" not in result


class TestTranscribe:
    def test_returns_json_path(self, tmp_path: Path) -> None:
        audio_file = tmp_path / "episode.mp3"
        audio_file.touch()
        expected_json = tmp_path / "episode.json"
        expected_json.write_text('{"segments": []}')

        with patch("podcast_reader.transcribe.run_child") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            result = transcribe(
                audio_path=audio_file,
                output_dir=tmp_path,
                model="large-v3",
                lang="en",
                device="cpu",
            )

        assert result == expected_json

    def test_raises_on_failure(self, tmp_path: Path) -> None:
        audio_file = tmp_path / "episode.mp3"
        audio_file.touch()

        with patch("podcast_reader.transcribe.run_child") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="CUDA error"
            )
            with pytest.raises(RuntimeError, match="whisper-ctranslate2 failed"):
                transcribe(
                    audio_path=audio_file,
                    output_dir=tmp_path,
                    model="large-v3",
                    lang="en",
                    device="cuda",
                )

    def test_missing_executable_suggests_whisper_extra(self, tmp_path: Path) -> None:
        """whisper-ctranslate2 is an optional extra; a missing binary should
        explain how to install it instead of raising a bare FileNotFoundError."""
        audio_file = tmp_path / "episode.mp3"
        audio_file.touch()

        with (
            patch(
                "podcast_reader.transcribe.run_child",
                side_effect=FileNotFoundError(2, "No such file or directory"),
            ),
            pytest.raises(RuntimeError, match="whisper"),
        ):
            transcribe(
                audio_path=audio_file,
                output_dir=tmp_path,
                model="large-v3",
                lang="en",
                device="cpu",
            )


# ---------------------------------------------------------------------------
# Frozen worker path (tasks 3.2/3.3)
# ---------------------------------------------------------------------------


def _install_model_pack(
    base: Path, model: str = "tiny", *, pack_schema: int = 1, truncate: bool = False
) -> Path:
    """Write a syntactically valid installed model pack under *base*.

    The manifest records what is on disk (not the registry pins), so small
    placeholder files with matching recorded sizes pass ``files_error``.
    *truncate* records a wrong size (integrity failure); *pack_schema*
    overrides the schema (compat failure).
    """
    entry = REGISTRY[f"model-{model}"]
    target = pack_dir(base, entry)
    target.mkdir(parents=True, exist_ok=True)
    payload = b"0123456789"
    (target / "model.bin").write_bytes(payload)
    manifest = {
        "pack_schema": pack_schema,
        "id": entry["id"],
        "version": entry["version"],
        "component_versions": dict(entry["component_versions"]),
        "files": [
            {
                "path": "model.bin",
                "sha256": "0" * 64,
                "size": 999 if truncate else len(payload),
            }
        ],
        "licenses": [],
    }
    (target / MANIFEST_FILE).write_text(json.dumps(manifest))
    return target


def _ok_proc(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args, 0, stdout="", stderr="")


@pytest.fixture
def base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "data"
    monkeypatch.setenv("PODCAST_READER_DATA_DIR", str(data))
    return data


@pytest.fixture
def audio(tmp_path: Path) -> Path:
    audio_file = tmp_path / "episode.mp3"
    audio_file.touch()
    return audio_file


class TestTranscriptionEngine:
    def test_unfrozen_names_whisper_ctranslate2(self) -> None:
        """Spec scenario: unfrozen path unchanged — same engine name as today."""
        assert transcription_engine() == "whisper-ctranslate2"

    def test_frozen_names_the_bundled_worker(self) -> None:
        with patch(
            "podcast_reader.transcribe.resolve_bundled_worker", return_value="/b/whisper-worker"
        ):
            assert transcription_engine() == "whisper-worker"


class TestWorkerPathSwitch:
    def test_frozen_spawns_worker_with_model_pack_dir(self, base: Path, audio: Path) -> None:
        """Spec scenarios: frozen engine uses the bundled worker; installed
        model resolves to its pack directory passed as --model."""
        model_dir = _install_model_pack(base, "tiny")
        out_dir = audio.parent / "out"

        with (
            patch(
                "podcast_reader.transcribe.resolve_bundled_worker",
                return_value="/bundle/whisper-worker",
            ),
            patch("podcast_reader.transcribe.run_child_streaming", side_effect=_ok_proc) as run,
        ):
            result = transcribe(
                audio_path=audio,
                output_dir=out_dir,
                model="tiny",
                lang="en",
                device="cpu",
            )

        assert result == out_dir / "episode.json"
        args = run.call_args.args[0]
        assert args == [
            "/bundle/whisper-worker",
            str(audio),
            "--model",
            str(model_dir),
            "--device",
            "cpu",
            "--compute-type",
            "int8",
            "--language",
            "en",
            "--output-dir",
            str(out_dir),
        ]

    def test_unfrozen_keeps_the_console_script_path(self, audio: Path) -> None:
        """resolve_bundled_worker is consulted first; None (unfrozen) keeps
        the whisper-ctranslate2 shell-out byte-identical."""
        (audio.parent / "episode.json").write_text('{"segments": []}')
        with (
            patch("podcast_reader.transcribe.resolve_bundled_worker", return_value=None) as rbw,
            patch("podcast_reader.transcribe.run_child", side_effect=_ok_proc) as run,
            patch("podcast_reader.transcribe.run_child_streaming") as streaming,
        ):
            transcribe(
                audio_path=audio, output_dir=audio.parent, model="tiny", lang="en", device="cpu"
            )
        rbw.assert_called_once_with("whisper-worker")
        streaming.assert_not_called()
        assert run.call_args.args[0][2:4] == ["--model", "tiny"]  # name, not a pack dir

    @pytest.mark.parametrize(
        ("prepare", "match"),
        [
            (lambda base: None, "not installed"),
            (lambda base: _install_model_pack(base, "tiny", pack_schema=99), "schema"),
            (lambda base: _install_model_pack(base, "tiny", truncate=True), "size mismatch"),
        ],
        ids=["absent", "incompatible", "truncated"],
    )
    def test_unusable_model_pack_fails_structured(
        self, base: Path, audio: Path, prepare: Callable[[Path], object], match: str
    ) -> None:
        """Spec scenario: missing model fails `model_missing` with a hint and
        no network download; incompatible/failed packs are treated as absent
        (per S1/S8) — and the worker is never spawned."""
        prepare(base)
        with (
            patch(
                "podcast_reader.transcribe.resolve_bundled_worker",
                return_value="/bundle/whisper-worker",
            ),
            patch("podcast_reader.transcribe.run_child_streaming") as run,
            pytest.raises(PipelineError) as excinfo,
        ):
            transcribe(
                audio_path=audio, output_dir=audio.parent, model="tiny", lang="en", device="cpu"
            )
        assert excinfo.value.code == "model_missing"
        assert "tiny" in excinfo.value.message
        assert match in excinfo.value.message or match in excinfo.value.hint
        assert "pack" in excinfo.value.hint.lower() or "Packs" in excinfo.value.hint
        run.assert_not_called()

    def test_unknown_model_name_fails_structured(self, base: Path, audio: Path) -> None:
        with (
            patch(
                "podcast_reader.transcribe.resolve_bundled_worker",
                return_value="/bundle/whisper-worker",
            ),
            pytest.raises(PipelineError) as excinfo,
        ):
            transcribe(
                audio_path=audio,
                output_dir=audio.parent,
                model="large-v2",
                lang="en",
                device="cpu",
            )
        assert excinfo.value.code == "model_missing"

    def test_cuda_unavailable_platform_degrades_silently(self, base: Path, audio: Path) -> None:
        """Spec scenario (per S4): no warning where the CUDA pack is
        registry-unavailable — this test runs on POSIX, where the win32-only
        cuda-runtime entry cannot be installed."""
        _install_model_pack(base, "tiny")
        events: list[PipelineEvent] = []

        with (
            patch(
                "podcast_reader.transcribe.resolve_bundled_worker",
                return_value="/bundle/whisper-worker",
            ),
            patch("podcast_reader.transcribe.run_child_streaming", side_effect=_ok_proc) as run,
            patch("podcast_reader.transcribe.detect_hardware") as detect,
        ):
            transcribe(
                audio_path=audio,
                output_dir=audio.parent,
                model="tiny",
                lang="en",
                device="cuda",
                on_event=events.append,
            )

        args = run.call_args.args[0]
        assert args[args.index("--device") + 1] == "cpu"
        assert args[args.index("--compute-type") + 1] == "int8"
        assert [e for e in events if e["kind"] == "warning"] == []
        detect.assert_not_called()  # nothing installable — the probe never runs

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX LD_LIBRARY_PATH semantics")
    def test_posix_spawn_injects_runtime_into_ld_library_path(
        self, base: Path, audio: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spec: on POSIX the engine sets LD_LIBRARY_PATH at spawn."""
        _install_model_pack(base, "tiny")
        monkeypatch.setenv("LD_LIBRARY_PATH", "/existing/libs")

        with (
            patch(
                "podcast_reader.transcribe.resolve_bundled_worker",
                return_value="/bundle/whisper-worker",
            ),
            patch("podcast_reader.transcribe.run_child_streaming", side_effect=_ok_proc) as run,
        ):
            transcribe(
                audio_path=audio, output_dir=audio.parent, model="tiny", lang="en", device="cpu"
            )

        env = run.call_args.kwargs["env"]
        assert env["LD_LIBRARY_PATH"] == f"{base / 'runtime'}{os.pathsep}/existing/libs"

    def test_worker_failure_raises_with_stderr_tail(self, base: Path, audio: Path) -> None:
        _install_model_pack(base, "tiny")

        def failing(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                args, 1, stdout="", stderr="loading model\nwhisper-worker error: boom\n"
            )

        with (
            patch(
                "podcast_reader.transcribe.resolve_bundled_worker",
                return_value="/bundle/whisper-worker",
            ),
            patch("podcast_reader.transcribe.run_child_streaming", side_effect=failing),
            pytest.raises(RuntimeError, match="boom"),
        ):
            transcribe(
                audio_path=audio, output_dir=audio.parent, model="tiny", lang="en", device="cpu"
            )


class TestEffectiveDevice:
    """The cuda→cpu fallback matrix on a platform where the pack is
    registry-available (win32), per S4: degrade with a reason-naming warning."""

    def _hw(self, nvidia: bool) -> HardwareInfo:
        return HardwareInfo(
            platform="win32", nvidia_gpu=nvidia, gpu_names=["RTX"] if nvidia else []
        )

    def _cuda_pack(self, base: Path, *, pack_schema: int = 1) -> None:
        entry = REGISTRY["cuda-runtime"]
        target = pack_dir(base, entry)
        target.mkdir(parents=True, exist_ok=True)
        (target / "cudnn64_9.dll").write_bytes(b"dll")
        manifest = {
            "pack_schema": pack_schema,
            "id": entry["id"],
            "version": entry["version"],
            "component_versions": dict(entry["component_versions"]),
            "files": [{"path": "cudnn64_9.dll", "sha256": "0" * 64, "size": 3}],
            "licenses": [],
        }
        (target / MANIFEST_FILE).write_text(json.dumps(manifest))

    def test_non_cuda_device_passes_through(self, base: Path) -> None:
        events: list[PipelineEvent] = []
        assert _effective_device(base, "cpu", events.append, platform="win32") == "cpu"
        assert events == []

    def test_no_gpu_degrades_with_reason(self, base: Path) -> None:
        events: list[PipelineEvent] = []
        with patch("podcast_reader.transcribe.detect_hardware", return_value=self._hw(False)):
            device = _effective_device(base, "cuda", events.append, platform="win32")
        assert device == "cpu"
        (warning,) = events
        assert warning["kind"] == "warning"
        assert warning["data"]["code"] == "cuda_unavailable"
        assert "GPU" in warning["message"]

    def test_pack_not_installed_degrades_with_reason(self, base: Path) -> None:
        events: list[PipelineEvent] = []
        with patch("podcast_reader.transcribe.detect_hardware", return_value=self._hw(True)):
            device = _effective_device(base, "cuda", events.append, platform="win32")
        assert device == "cpu"
        (warning,) = events
        assert "not installed" in warning["message"]

    def test_incompatible_pack_degrades_with_reason(self, base: Path) -> None:
        self._cuda_pack(base, pack_schema=99)
        events: list[PipelineEvent] = []
        with patch("podcast_reader.transcribe.detect_hardware", return_value=self._hw(True)):
            device = _effective_device(base, "cuda", events.append, platform="win32")
        assert device == "cpu"
        (warning,) = events
        assert "incompatible" in warning["message"]

    def test_usable_pack_keeps_cuda(self, base: Path) -> None:
        self._cuda_pack(base)
        events: list[PipelineEvent] = []
        with patch("podcast_reader.transcribe.detect_hardware", return_value=self._hw(True)):
            device = _effective_device(base, "cuda", events.append, platform="win32")
        assert device == "cuda"
        assert events == []


class TestProgressStreaming:
    def test_progress_lines_map_to_step_progress_events(self, base: Path, audio: Path) -> None:
        """Spec: stderr `progress` lines map onto transcribe step_progress
        events carrying seconds and the total duration."""
        _install_model_pack(base, "tiny")
        events: list[PipelineEvent] = []

        def scripted(
            args: list[str],
            *,
            on_stderr_line: Callable[[str], None],
            env: dict[str, str] | None = None,
        ) -> subprocess.CompletedProcess[str]:
            on_stderr_line("progress duration=10.00\n")
            on_stderr_line("loading checkpoint shards\n")  # non-protocol noise
            on_stderr_line("progress segment_end=4.00\n")
            on_stderr_line("progress segment_end=10.00\n")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with (
            patch(
                "podcast_reader.transcribe.resolve_bundled_worker",
                return_value="/bundle/whisper-worker",
            ),
            patch("podcast_reader.transcribe.run_child_streaming", side_effect=scripted),
        ):
            transcribe(
                audio_path=audio,
                output_dir=audio.parent,
                model="tiny",
                lang="en",
                device="cpu",
                on_event=events.append,
            )

        progress = [e for e in events if e["kind"] == "step_progress"]
        assert [e["step"] for e in progress] == ["transcribe"] * 3
        assert [e["data"]["seconds"] for e in progress] == [0.0, 4.0, 10.0]
        assert [e["data"]["duration"] for e in progress] == [10.0, 10.0, 10.0]

    @pytest.mark.skipif(sys.platform == "win32", reason="shebang script worker")
    def test_scripted_fake_worker_process_end_to_end(
        self, base: Path, audio: Path, tmp_path: Path
    ) -> None:
        """Task 3.3: a real scripted worker process — progress consumed
        incrementally from stderr, JSON artifact produced."""
        _install_model_pack(base, "tiny")
        script = tmp_path / "fake-whisper-worker"
        script.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            "from pathlib import Path\n"
            "args = sys.argv[1:]\n"
            "audio = Path(args[0])\n"
            "opts = dict(zip(args[1::2], args[2::2]))\n"
            "out_dir = Path(opts['--output-dir'])\n"
            "print('progress duration=5.00', file=sys.stderr, flush=True)\n"
            "print('progress segment_end=2.50', file=sys.stderr, flush=True)\n"
            "print('progress segment_end=5.00', file=sys.stderr, flush=True)\n"
            "out_dir.mkdir(parents=True, exist_ok=True)\n"
            "json_path = out_dir / (audio.stem + '.json')\n"
            "json_path.write_text(json.dumps({'text': ' hi', 'segments': "
            "[{'start': 0.0, 'end': 5.0, 'text': ' hi', 'words': None}], 'language': 'en'}))\n"
            "print(str(json_path.resolve()), flush=True)\n"
        )
        script.chmod(0o755)
        events: list[PipelineEvent] = []

        with patch("podcast_reader.transcribe.resolve_bundled_worker", return_value=str(script)):
            result = transcribe(
                audio_path=audio,
                output_dir=tmp_path / "out",
                model="tiny",
                lang="en",
                device="cpu",
                on_event=events.append,
            )

        assert json.loads(result.read_text())["language"] == "en"
        progress = [e for e in events if e["kind"] == "step_progress"]
        assert [e["data"]["seconds"] for e in progress] == [0.0, 2.5, 5.0]
        assert all(e["data"]["duration"] == 5.0 for e in progress)
