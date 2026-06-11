"""Tests for podcast_reader.types."""

from __future__ import annotations

from podcast_reader.types import JOB_STATES, PipelineEvent, new_job_record


def test_pipeline_event_shape() -> None:
    e = PipelineEvent(kind="step_started", step="transcribe", message="", data={})
    assert e["kind"] == "step_started"


def test_new_job_record_defaults() -> None:
    rec = new_job_record(job_id="j1", source="https://x", title=None)
    assert rec["state"] == "queued"
    assert rec["events"] == []
    assert set(JOB_STATES) >= {
        "queued",
        "running",
        "done",
        "failed",
        "interrupted",
        "awaiting-confirmation",
    }
