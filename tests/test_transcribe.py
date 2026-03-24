"""Tests for podcast_reader.transcribe module."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from podcast_reader.transcribe import build_whisper_args, transcribe


class TestBuildWhisperArgs:
    def test_basic_args(self) -> None:
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

    def test_with_hf_token(self) -> None:
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

    def test_without_hf_token(self) -> None:
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

        with patch("podcast_reader.transcribe.subprocess.run") as mock_run:
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

        with patch("podcast_reader.transcribe.subprocess.run") as mock_run:
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
