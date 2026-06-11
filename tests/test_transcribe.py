"""Tests for podcast_reader.transcribe module."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from podcast_reader.transcribe import build_whisper_args, transcribe


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
