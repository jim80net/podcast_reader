"""Typed boundaries shared by the pipeline, CLI, and engine."""

from __future__ import annotations

from typing import Any, Literal

# pydantic (FastAPI response models) requires typing_extensions.TypedDict on
# Python < 3.12; typing.TypedDict raises PydanticUserError there.
from typing_extensions import TypedDict

StepName = Literal["resolve", "captions", "download", "transcribe", "diarize", "chapters", "render"]
# step_progress: incremental in-step progress (whisper worker, group 3).
# pack_state / pack_progress: pack installer events on the shared SSE stream
# (per S6); they carry data.pack_id and MUST NOT carry job_id (per Q5 —
# job_id presence is the renderer's job/pack discriminator).
# media_state / media_progress: lazy media-prep events (media-playback); they
# carry data.source_id and MUST NOT carry job_id, mirroring the pack split.
EventKind = Literal[
    "step_started",
    "step_progress",
    "step_finished",
    "warning",
    "job_done",
    "job_failed",
    "pack_state",
    "pack_progress",
    "media_state",
    "media_progress",
]
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


class PipelineError(Exception):
    """Unrecoverable pipeline failure with a structured code/message/hint.

    The exception twin of :class:`JobError`. Lives here (bottom of the import
    graph) so step modules below ``pipeline.py`` — ``ytdlp.py``
    (``download_failed``, per S7) and ``transcribe.py`` (``model_missing``) —
    can raise it without an import cycle; ``pipeline.py`` re-exports it for
    its existing consumers (CLI, engine job store).
    """

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint


class JobError(TypedDict):
    code: str
    message: str
    hint: str


class PipelineRequest(TypedDict):
    source: str  # URL or local file path
    title: str | None
    output_dir: str
    model: str | None  # None/empty: the chapter provider's default model
    whisper_model: str
    whisper_lang: str
    whisper_device: str
    hf_token: str | None
    sentences: int
    cookies: str | None
    chapter_provider: str  # a podcast_reader.providers.PROVIDERS key
    chapter_api_key: str | None  # None: skip chapter generation
    custom_provider_url: str  # base URL for the "custom" provider ("" otherwise)
    diarize: bool  # run the diarization pack's worker after transcription


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
    chapter_model: str  # "" means: the chapter provider's default model
    chapter_provider: str  # a podcast_reader.providers.PROVIDERS key
    custom_provider_url: str  # base URL for the "custom" provider ("" otherwise)
    diarize: bool  # default false; warn-and-skip when the pack is absent
    media_cache_max_bytes: int  # LRU cap for the lazy media cache (media-playback)


#: A library entry's playback classification (media-playback). ``youtube`` plays
#: via a cross-origin embed (no bytes through the engine); ``video``/``audio``
#: stream from the engine; ``unavailable`` leaves the Reader transcript-only.
MediaKind = Literal["youtube", "video", "audio", "unavailable"]
#: Preparation status: ``ready`` to serve, ``preparing`` while a lazy download
#: runs, ``unavailable`` when no playable media can be produced.
MediaStatus = Literal["ready", "preparing", "unavailable"]


class MediaInfo(TypedDict):
    kind: MediaKind
    youtube_id: str  # "" unless kind == "youtube"
    duration_s: float  # 0.0 when unknown
    status: MediaStatus
    progress: float  # 0.0..1.0 while preparing; 1.0 when ready


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
