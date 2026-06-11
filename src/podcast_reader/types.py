"""Typed boundaries shared by the pipeline, CLI, and engine."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

StepName = Literal["resolve", "captions", "download", "transcribe", "chapters", "render"]
EventKind = Literal["step_started", "step_finished", "warning", "job_done", "job_failed"]
JobState = Literal["queued", "awaiting-confirmation", "running", "done", "failed", "interrupted"]

JOB_STATES: tuple[JobState, ...] = (
    "queued",
    "awaiting-confirmation",
    "running",
    "done",
    "failed",
    "interrupted",
)


class PipelineEvent(TypedDict):
    kind: EventKind
    step: StepName | None
    message: str
    data: dict[str, Any]


class JobError(TypedDict):
    code: str
    message: str
    hint: str


class PipelineRequest(TypedDict):
    source: str  # URL or local file path
    title: str | None
    output_dir: str
    model: str
    whisper_model: str
    whisper_lang: str
    whisper_device: str
    hf_token: str | None
    sentences: int
    cookies: str | None


class PipelineResult(TypedDict):
    json_path: str
    chapters_path: str | None
    html_path: str
    title: str


class JobRecord(TypedDict):
    id: str
    source: str
    title: str | None
    state: JobState
    error: JobError | None
    events: list[PipelineEvent]
    result: PipelineResult | None
    created_at: float
    updated_at: float


class LibraryEntry(TypedDict):
    source_id: str
    source: str
    title: str
    html_path: str
    created_at: float


class EngineSettings(TypedDict):
    whisper_model: str
    whisper_lang: str
    whisper_device: str
    sentences: int
    library_dir: str
    chapter_model: str


def new_job_record(*, job_id: str, source: str, title: str | None) -> JobRecord:
    """Create a queued JobRecord with empty history (timestamps set by the store)."""
    return JobRecord(
        id=job_id,
        source=source,
        title=title,
        state="queued",
        error=None,
        events=[],
        result=None,
        created_at=0.0,
        updated_at=0.0,
    )
