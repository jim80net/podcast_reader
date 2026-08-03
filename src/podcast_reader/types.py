"""Typed boundaries shared by the pipeline, CLI, and engine."""

from __future__ import annotations

from typing import Literal

# pydantic (FastAPI response models) requires typing_extensions.TypedDict on
# Python < 3.12; typing.TypedDict raises PydanticUserError there.
from typing_extensions import NotRequired, TypedDict

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


class StepStartedData(TypedDict, total=False):
    cached: bool


class StepProgressData(TypedDict):
    seconds: float
    duration: float | None


class StepFinishedData(TypedDict, total=False):
    cached: bool
    caption_corrections: int


class WarningData(TypedDict):
    code: str
    reason: NotRequired[str]


class JobDoneData(TypedDict):
    pass


class JobFailedData(TypedDict):
    code: str
    hint: str
    detail: str


class StepStartedEvent(TypedDict):
    kind: Literal["step_started"]
    step: StepName
    message: str
    data: StepStartedData


class StepProgressEvent(TypedDict):
    kind: Literal["step_progress"]
    step: StepName
    message: str
    data: StepProgressData


class StepFinishedEvent(TypedDict):
    kind: Literal["step_finished"]
    step: StepName
    message: str
    data: StepFinishedData


class WarningEvent(TypedDict):
    kind: Literal["warning"]
    step: StepName
    message: str
    data: WarningData


class JobDoneEvent(TypedDict):
    kind: Literal["job_done"]
    step: None
    message: str
    data: JobDoneData


class JobFailedEvent(TypedDict):
    kind: Literal["job_failed"]
    step: None
    message: str
    data: JobFailedData


# Events emitted inside the pipeline are not routable until JobStore attaches
# their job identity. They never cross the engine's SSE boundary directly.
PipelineRunEvent = (
    StepStartedEvent
    | StepProgressEvent
    | StepFinishedEvent
    | WarningEvent
    | JobDoneEvent
    | JobFailedEvent
)


class JobStepStartedData(StepStartedData):
    job_id: str


class JobStepProgressData(StepProgressData):
    job_id: str


class JobStepFinishedData(StepFinishedData):
    job_id: str


class JobWarningData(WarningData):
    job_id: str


class RoutedJobDoneData(JobDoneData):
    job_id: str


class RoutedJobFailedData(JobFailedData):
    job_id: str


class JobStepStartedEvent(TypedDict):
    kind: Literal["step_started"]
    step: StepName
    message: str
    data: JobStepStartedData


class JobStepProgressEvent(TypedDict):
    kind: Literal["step_progress"]
    step: StepName
    message: str
    data: JobStepProgressData


class JobStepFinishedEvent(TypedDict):
    kind: Literal["step_finished"]
    step: StepName
    message: str
    data: JobStepFinishedData


class JobWarningEvent(TypedDict):
    kind: Literal["warning"]
    step: StepName
    message: str
    data: JobWarningData


class RoutedJobDoneEvent(TypedDict):
    kind: Literal["job_done"]
    step: None
    message: str
    data: RoutedJobDoneData


class RoutedJobFailedEvent(TypedDict):
    kind: Literal["job_failed"]
    step: None
    message: str
    data: RoutedJobFailedData


class PackInstallEventError(TypedDict):
    code: str
    message: str


PackEventState = Literal[
    "not-installed",
    "resumable",
    "installing",
    "installed",
    "incompatible",
    "failed",
    "unavailable",
]


class PackStateEventData(TypedDict):
    pack_id: str
    state: PackEventState
    error: NotRequired[PackInstallEventError]


class PackProgressEventData(TypedDict):
    pack_id: str
    bytes: int
    total: int


class MediaStateEventData(TypedDict):
    source_id: str
    state: Literal["ready", "preparing", "unavailable"]


class MediaProgressEventData(TypedDict):
    source_id: str


class PackStateEvent(TypedDict):
    kind: Literal["pack_state"]
    step: None
    message: str
    data: PackStateEventData


class PackProgressEvent(TypedDict):
    kind: Literal["pack_progress"]
    step: None
    message: str
    data: PackProgressEventData


class MediaStateEvent(TypedDict):
    kind: Literal["media_state"]
    step: None
    message: str
    data: MediaStateEventData


class MediaProgressEvent(TypedDict):
    kind: Literal["media_progress"]
    step: None
    message: str
    data: MediaProgressEventData


# The public engine event is a routed sum type: its kind selects one exact
# identity/data shape, so job, pack, and media identities cannot be combined.
JobPipelineEvent = (
    JobStepStartedEvent
    | JobStepProgressEvent
    | JobStepFinishedEvent
    | JobWarningEvent
    | RoutedJobDoneEvent
    | RoutedJobFailedEvent
)
PipelineEvent = (
    JobPipelineEvent | PackStateEvent | PackProgressEvent | MediaStateEvent | MediaProgressEvent
)


class PipelineError(Exception):
    """Unrecoverable pipeline failure with a structured code/message/hint/detail.

    The exception twin of :class:`JobError`. Lives here (bottom of the import
    graph) so step modules below ``pipeline.py`` — ``ytdlp.py``
    (``download_failed``, per S7) and ``transcribe.py`` (``model_missing``) —
    can raise it without an import cycle; ``pipeline.py`` re-exports it for
    its existing consumers (CLI, engine job store).
    """

    def __init__(self, code: str, message: str, hint: str = "", detail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.detail = detail


class JobError(TypedDict):
    code: str
    message: str
    hint: str
    detail: str


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
    custom_providers: list[CustomProviderConfig]  # request-local nonsecret snapshot
    diarize: bool  # run the diarization pack's worker after transcription
    caption_cleanup: bool  # opt-in spelling/casing cleanup via the chapter provider


class PipelineResult(TypedDict):
    json_path: str
    chapters_path: str | None
    html_path: str
    title: str


class JobOverrides(TypedDict, total=False):
    """Per-job model overrides for a rerun (each absent key = use the setting).

    The runner merges these over the settings snapshot and clears the cached
    artifacts a change invalidates: ``whisper_model`` forces a full re-transcribe
    (json + chapters + html); a chapter-only change (``chapter_provider`` /
    ``chapter_model`` / ``custom_provider_url``) re-runs chapters + render only.
    """

    whisper_model: str
    chapter_provider: str
    chapter_model: str
    custom_provider_url: str


class JobModels(TypedDict):
    """The models a job actually ran with (recorded at dequeue), so the UI can
    show what produced a transcript rather than the current default. Whisper is
    irrelevant for caption sources (YouTube) — the UI derives the transcription
    source from the step timeline and uses ``whisper_model`` only otherwise."""

    whisper_model: str | None
    chapter_provider: str | None
    chapter_model: str | None


class JobRecord(TypedDict):
    id: str
    source: str
    title: str | None
    state: JobState
    error: JobError | None
    events: list[JobPipelineEvent]
    result: PipelineResult | None
    overrides: JobOverrides | None
    models: JobModels | None
    created_at: float
    updated_at: float


class LibraryEntry(TypedDict):
    source_id: str
    source: str
    title: str
    html_path: str
    created_at: float


class CustomProviderConfig(TypedDict):
    """Persisted, nonsecret configuration for one user-defined provider."""

    name: str
    base_url: str
    default_model: str
    max_tokens: int


class EngineSettings(TypedDict):
    whisper_model: str
    whisper_lang: str
    whisper_device: str
    sentences: int
    library_dir: str
    chapter_model: str  # "" means: the chapter provider's default model
    chapter_provider: str  # a podcast_reader.providers.PROVIDERS key
    custom_provider_url: str  # base URL for the "custom" provider ("" otherwise)
    custom_providers: list[CustomProviderConfig]  # named providers; never credentials
    diarize: bool  # default false; warn-and-skip when the pack is absent
    caption_cleanup: bool  # opt-in, provider-assisted spelling/casing cleanup
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


def new_job_record(
    *, job_id: str, source: str, title: str | None, overrides: JobOverrides | None = None
) -> JobRecord:
    """Create a queued JobRecord with empty history (timestamps set by the store)."""
    return JobRecord(
        id=job_id,
        source=source,
        title=title,
        state="queued",
        error=None,
        events=[],
        result=None,
        overrides=overrides,
        models=None,
        created_at=0.0,
        updated_at=0.0,
    )
