"""Tests for podcast_reader.diarize (engine-side merge glue, torch-free).

The max-overlap merge is pure stdlib interval math (spike §4) so it is
tested directly; the step orchestration is tested with ``run_child`` mocked
(ffmpeg pre-convert + worker spawn) against a real installed-pack layout
under a temp data dir.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from podcast_reader import diarize
from podcast_reader.engine import packs

if TYPE_CHECKING:
    from podcast_reader.types import PipelineEvent


class TestAssignSpeakers:
    def test_assigns_max_overlap_speaker(self) -> None:
        """Spec: each segment gets the speaker with maximal positive
        time-overlap across turns."""
        segments = [
            {"start": 0.0, "end": 4.0, "text": "a"},
            {"start": 4.0, "end": 8.0, "text": "b"},
        ]
        turns = [
            {"start": 0.0, "end": 3.0, "speaker": "SPEAKER_00"},
            {"start": 3.0, "end": 8.0, "speaker": "SPEAKER_01"},
        ]

        assigned = diarize.assign_speakers(segments, turns)

        assert assigned == 2
        assert segments[0]["speaker"] == "SPEAKER_00"  # 3.0 vs 1.0 overlap
        assert segments[1]["speaker"] == "SPEAKER_01"  # 0.0 vs 4.0 overlap

    def test_overlaps_sum_across_turns_of_the_same_speaker(self) -> None:
        segments = [{"start": 0.0, "end": 10.0, "text": "a"}]
        turns = [
            {"start": 0.0, "end": 3.0, "speaker": "SPEAKER_00"},
            {"start": 3.0, "end": 7.0, "speaker": "SPEAKER_01"},
            {"start": 7.0, "end": 10.0, "speaker": "SPEAKER_00"},
        ]

        diarize.assign_speakers(segments, turns)

        assert segments[0]["speaker"] == "SPEAKER_00"  # 3+3 beats 4

    def test_segment_with_no_overlap_keeps_no_speaker(self) -> None:
        """Spec: segments with no overlap keep no speaker."""
        segments = [{"start": 20.0, "end": 25.0, "text": "a"}]
        turns = [{"start": 0.0, "end": 3.0, "speaker": "SPEAKER_00"}]

        assigned = diarize.assign_speakers(segments, turns)

        assert assigned == 0
        assert "speaker" not in segments[0]

    def test_touching_intervals_do_not_count_as_overlap(self) -> None:
        """Zero-length intersections are not positive overlap."""
        segments = [{"start": 3.0, "end": 5.0, "text": "a"}]
        turns = [{"start": 0.0, "end": 3.0, "speaker": "SPEAKER_00"}]

        assert diarize.assign_speakers(segments, turns) == 0

    def test_no_turns_assigns_nothing(self) -> None:
        segments = [{"start": 0.0, "end": 5.0, "text": "a"}]

        assert diarize.assign_speakers(segments, []) == 0
        assert "speaker" not in segments[0]

    def test_tie_breaks_deterministically_by_label(self) -> None:
        segments = [{"start": 0.0, "end": 4.0, "text": "a"}]
        turns = [
            {"start": 0.0, "end": 2.0, "speaker": "SPEAKER_01"},
            {"start": 2.0, "end": 4.0, "speaker": "SPEAKER_00"},
        ]

        diarize.assign_speakers(segments, turns)

        assert segments[0]["speaker"] == "SPEAKER_00"


def _write_transcript(path: Path, *, with_speakers: bool = False) -> None:
    segments: list[dict[str, Any]] = [
        {"start": 0.0, "end": 4.0, "text": "Hello."},
        {"start": 4.0, "end": 8.0, "text": "World."},
    ]
    if with_speakers:
        for seg in segments:
            seg["speaker"] = "SPEAKER_00"
    path.write_text(json.dumps({"text": "Hello. World.", "segments": segments, "language": "en"}))


def _install_pack(base: Path) -> Path:
    """Lay out an installed diarization pack: worker file + valid manifest."""
    entry = packs.REGISTRY["diarization"]
    pack_dir = packs.pack_dir(base, entry)
    pack_dir.mkdir(parents=True)
    worker = pack_dir / "diarization-worker"
    worker.write_bytes(b"#!/bin/sh\n")
    worker.chmod(0o755)
    manifest = packs.PackManifest(
        pack_schema=packs.PACK_SCHEMA,
        id="diarization",
        version=entry["version"],
        component_versions=dict(entry["component_versions"]),
        files=[
            packs.ManifestFile(
                path="diarization-worker",
                sha256="0" * 64,
                size=worker.stat().st_size,
            )
        ],
        licenses=[],
    )
    packs.manifest_path(pack_dir).write_text(json.dumps(manifest))
    return pack_dir


def _fake_run_child(turns: list[dict[str, Any]], *, worker_returncode: int = 0) -> Any:
    """A run_child stand-in dispatching on the spawned executable.

    ffmpeg: creates the staged WAV it was asked to produce. Worker: writes
    *turns* to the ``--output`` path (or fails with *worker_returncode*).
    """

    def run(args: list[str]) -> subprocess.CompletedProcess[str]:
        if "ffmpeg" in Path(args[0]).name:
            Path(args[-1]).write_bytes(b"RIFF")
            return subprocess.CompletedProcess(args, 0, "", "")
        if worker_returncode == 0:
            output = Path(args[args.index("--output") + 1])
            output.write_text(json.dumps({"turns": turns}))
        return subprocess.CompletedProcess(
            args, worker_returncode, "", "diarization-worker error: boom\n"
        )

    return run


_TURNS = [
    {"start": 0.0, "end": 4.0, "speaker": "SPEAKER_00"},
    {"start": 4.0, "end": 8.0, "speaker": "SPEAKER_01"},
]


class TestDiarizeStep:
    @pytest.fixture(autouse=True)
    def _data_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        base = tmp_path / "data"
        base.mkdir()
        monkeypatch.setenv("PODCAST_READER_DATA_DIR", str(base))
        return base

    def _run(
        self, tmp_path: Path, *, with_speakers: bool = False, run_child: Any = None
    ) -> tuple[Path, list[PipelineEvent]]:
        audio = tmp_path / "episode.mp3"
        audio.write_bytes(b"ID3")
        json_path = tmp_path / "episode.json"
        _write_transcript(json_path, with_speakers=with_speakers)
        events: list[PipelineEvent] = []
        fake = run_child if run_child is not None else _fake_run_child(list(_TURNS))
        with patch("podcast_reader.diarize.run_child", side_effect=fake):
            diarize.diarize_step(audio_path=audio, json_path=json_path, on_event=events.append)
        return json_path, events

    def test_enriches_segments_and_finishes(self, tmp_path: Path, _data_dir: Path) -> None:
        """Spec scenario: segments enriched with speakers by maximal overlap."""
        _install_pack(_data_dir)

        json_path, events = self._run(tmp_path)

        data = json.loads(json_path.read_text())
        assert [s["speaker"] for s in data["segments"]] == ["SPEAKER_00", "SPEAKER_01"]
        assert [e["kind"] for e in events] == ["step_started", "step_finished"]
        assert all(e["step"] == "diarize" for e in events)

    def test_cache_hit_when_speakers_already_present(self, tmp_path: Path, _data_dir: Path) -> None:
        """Spec scenario: merge is idempotent — the worker is not invoked."""
        _install_pack(_data_dir)
        spawns: list[list[str]] = []

        def recording(args: list[str]) -> subprocess.CompletedProcess[str]:
            spawns.append(list(args))
            return subprocess.CompletedProcess(args, 0, "", "")

        json_path, events = self._run(tmp_path, with_speakers=True, run_child=recording)

        assert spawns == []
        assert [e["kind"] for e in events] == ["step_started", "step_finished"]
        assert events[0]["data"] == {"cached": True}

    def test_missing_pack_warns_and_skips(self, tmp_path: Path) -> None:
        """Spec scenario: enabled without the pack — warning naming the pack,
        never a failure."""
        json_path, events = self._run(tmp_path)

        data = json.loads(json_path.read_text())
        assert all("speaker" not in s for s in data["segments"])
        (event,) = events
        assert event["kind"] == "warning"
        assert event["data"]["code"] == "diarization_skipped"
        assert "diarization" in event["message"]

    def test_incompatible_pack_warns_and_skips(self, tmp_path: Path, _data_dir: Path) -> None:
        pack_dir = _install_pack(_data_dir)
        manifest = json.loads(packs.manifest_path(pack_dir).read_text())
        manifest["pack_schema"] = 999
        packs.manifest_path(pack_dir).write_text(json.dumps(manifest))

        _json_path, events = self._run(tmp_path)

        (event,) = events
        assert event["kind"] == "warning"
        assert event["data"]["code"] == "diarization_skipped"

    def test_worker_failure_warns_but_does_not_raise(self, tmp_path: Path, _data_dir: Path) -> None:
        """Spec scenario: worker failure does not kill the job — structured
        warning, transcript left un-enriched."""
        _install_pack(_data_dir)

        json_path, events = self._run(tmp_path, run_child=_fake_run_child([], worker_returncode=1))

        data = json.loads(json_path.read_text())
        assert all("speaker" not in s for s in data["segments"])
        assert [e["kind"] for e in events] == ["step_started", "warning"]
        assert events[1]["data"]["code"] == "diarization_failed"
        assert "boom" in events[1]["message"]

    @pytest.mark.parametrize(
        "bad_turns",
        [
            ["not-a-dict"],
            [{"start": 0.0, "speaker": "SPEAKER_00"}],  # missing end
            [{"start": "0", "end": 4.0, "speaker": "SPEAKER_00"}],  # non-numeric
            [{"start": True, "end": 4.0, "speaker": "SPEAKER_00"}],  # bool is not numeric
            [{"start": 0.0, "end": 4.0}],  # missing speaker
        ],
    )
    def test_malformed_turns_warn_but_do_not_raise(
        self, tmp_path: Path, _data_dir: Path, bad_turns: list[Any]
    ) -> None:
        """The never-raises contract holds for malformed worker output:
        turn items the merge cannot consume degrade to the unreadable-output
        warning instead of a KeyError/TypeError killing the job."""
        _install_pack(_data_dir)

        json_path, events = self._run(tmp_path, run_child=_fake_run_child(bad_turns))

        data = json.loads(json_path.read_text())
        assert all("speaker" not in s for s in data["segments"])
        assert [e["kind"] for e in events] == ["step_started", "warning"]
        assert events[1]["data"]["code"] == "diarization_failed"
        assert "unreadable output" in events[1]["message"]

    def test_ffmpeg_failure_warns_but_does_not_raise(self, tmp_path: Path, _data_dir: Path) -> None:
        _install_pack(_data_dir)

        def failing_ffmpeg(args: list[str]) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args, 1, "", "unknown codec\n")

        _json_path, events = self._run(tmp_path, run_child=failing_ffmpeg)

        assert [e["kind"] for e in events] == ["step_started", "warning"]
        assert events[1]["data"]["code"] == "diarization_failed"

    def test_preconvert_targets_16khz_mono_wav(self, tmp_path: Path, _data_dir: Path) -> None:
        """Spec: pre-convert to 16 kHz mono WAV with the managed ffmpeg;
        staged file not retained."""
        _install_pack(_data_dir)
        spawns: list[list[str]] = []
        inner = _fake_run_child(list(_TURNS))

        def recording(args: list[str]) -> subprocess.CompletedProcess[str]:
            spawns.append(list(args))
            return inner(args)

        self._run(tmp_path, run_child=recording)

        ffmpeg_args, worker_args = spawns
        ac = ffmpeg_args.index("-ac")
        ar = ffmpeg_args.index("-ar")
        assert ffmpeg_args[ac : ac + 2] == ["-ac", "1"]
        assert ffmpeg_args[ar : ar + 2] == ["-ar", "16000"]
        assert "pcm_s16le" in ffmpeg_args
        staged_wav = ffmpeg_args[-1]
        assert staged_wav.endswith(".wav")
        assert not Path(staged_wav).exists()  # staged, not retained
        assert worker_args[1] == staged_wav

    def test_worker_resolved_from_the_pack_dir(self, tmp_path: Path, _data_dir: Path) -> None:
        pack_dir = _install_pack(_data_dir)
        spawns: list[list[str]] = []
        inner = _fake_run_child(list(_TURNS))

        def recording(args: list[str]) -> subprocess.CompletedProcess[str]:
            spawns.append(list(args))
            return inner(args)

        self._run(tmp_path, run_child=recording)

        assert spawns[1][0] == str(pack_dir / "diarization-worker")
