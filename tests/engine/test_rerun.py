"""Tests for rerun cache-clearing (podcast_reader.engine.process._clear_rerun_artifacts)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from podcast_reader.engine.process import _clear_rerun_artifacts

if TYPE_CHECKING:
    from pathlib import Path


def _seed(staging: Path) -> None:
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "ep.json").write_text("{}")  # whisper transcript
    (staging / "ep_chapters.json").write_text("[]")  # chapters
    (staging / "ep.html").write_text("<html></html>")  # render
    (staging / "ep.mp3").write_text("audio")  # downloaded source audio


class TestClearRerunArtifacts:
    def test_whisper_override_clears_transcript_and_render_but_keeps_audio(
        self, tmp_path: Path
    ) -> None:
        _seed(tmp_path)
        _clear_rerun_artifacts(tmp_path, {"whisper_model": "medium"})
        # Re-transcribe: every JSON (whisper + chapters) and the HTML go...
        assert not (tmp_path / "ep.json").exists()
        assert not (tmp_path / "ep_chapters.json").exists()
        assert not (tmp_path / "ep.html").exists()
        # ...but the downloaded audio is kept (no needless re-download).
        assert (tmp_path / "ep.mp3").exists()

    def test_chapter_override_keeps_transcript_and_audio(self, tmp_path: Path) -> None:
        _seed(tmp_path)
        _clear_rerun_artifacts(tmp_path, {"chapter_provider": "xai"})
        # Re-chapter + re-render only: the whisper JSON and audio survive.
        assert (tmp_path / "ep.json").exists()
        assert (tmp_path / "ep.mp3").exists()
        assert not (tmp_path / "ep_chapters.json").exists()
        assert not (tmp_path / "ep.html").exists()

    def test_no_overrides_clears_nothing(self, tmp_path: Path) -> None:
        _seed(tmp_path)
        _clear_rerun_artifacts(tmp_path, {})
        for name in ("ep.json", "ep_chapters.json", "ep.html", "ep.mp3"):
            assert (tmp_path / name).exists()

    def test_whisper_takes_precedence_over_chapter_fields(self, tmp_path: Path) -> None:
        # Both changed → full re-transcribe (whisper wins the branch).
        _seed(tmp_path)
        _clear_rerun_artifacts(tmp_path, {"whisper_model": "small", "chapter_provider": "openai"})
        assert not (tmp_path / "ep.json").exists()
        assert (tmp_path / "ep.mp3").exists()
