"""Pipeline error-mapping tests (kept separate from test_pipeline.py).

Expected failures from fetch paths must surface as :class:`PipelineError`
so the engine worker can mark the job ``failed`` with a structured error
instead of dying to an escaping ``SystemExit`` (cubic D1).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from podcast_reader.pipeline import PipelineError, run_pipeline
from podcast_reader.types import PipelineRequest
from podcast_reader.youtube import NoTranscriptError

if TYPE_CHECKING:
    from pathlib import Path

_YT_URL = "https://www.youtube.com/watch?v=abc123XYZqq"


def _request(*, input_arg: str, output_dir: Path) -> PipelineRequest:
    return PipelineRequest(
        source=input_arg,
        title="Test Title",
        output_dir=str(output_dir),
        model="claude-haiku-4-5-20251001",
        whisper_model="large-v3",
        whisper_lang="en",
        whisper_device="cpu",
        hf_token=None,
        sentences=5,
        cookies=None,
        chapter_provider="anthropic",
        chapter_api_key=None,
        custom_provider_url="",
        custom_providers=[],
        diarize=False,
        caption_cleanup=False,
    )


class TestYouTubeFetchErrorMapping:
    @patch("podcast_reader.pipeline.fetch_transcript")
    def test_missing_transcript_maps_to_pipeline_error(
        self, mock_fetch: MagicMock, tmp_path: Path
    ) -> None:
        mock_fetch.side_effect = NoTranscriptError(
            "No English transcript available for abc123XYZqq"
        )
        with pytest.raises(PipelineError) as excinfo:
            run_pipeline(_request(input_arg=_YT_URL, output_dir=tmp_path), on_event=lambda e: None)
        assert excinfo.value.code == "no_transcript"
        assert "abc123XYZqq" in excinfo.value.message
        assert excinfo.value.hint
