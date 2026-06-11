"""FastAPI engine app: bearer auth, jobs, SSE events, library, settings.

Every route — health included — requires ``Authorization: Bearer <token>``;
the token is never accepted via query parameter. ``GET /v1/events`` streams
pipeline events as SSE with comment heartbeats so client disconnects are
observable; the job record (``GET /v1/jobs/{id}``) remains the source of
truth for clients that missed events.
"""

from __future__ import annotations

import hmac
import json
import os
import queue
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from podcast_reader.chapters import verify_key
from podcast_reader.engine.jobs import JobStateError
from podcast_reader.engine.library import get_entry, list_entries
from podcast_reader.engine.settings import (
    engine_version,
    load_engine_state,
    load_settings,
    save_settings,
    token_fingerprint,
)
from podcast_reader.providers import PROVIDERS, resolve_provider, validate_custom_url

# JobRecord/LibraryEntry back FastAPI response models, so they must be
# importable at runtime (a TYPE_CHECKING import leaves unresolvable ForwardRefs).
from podcast_reader.types import (
    EngineSettings,
    JobRecord,  # noqa: TC001 — runtime response model
    LibraryEntry,  # noqa: TC001 — runtime response model
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator

    from podcast_reader.engine.jobs import JobStore


class JobSubmission(BaseModel):
    """Body of ``POST /v1/jobs``.

    ``requires_confirmation`` defaults to false so pre-change clients are
    unchanged; true journals the job in ``awaiting-confirmation`` without
    enqueueing it (it runs only after ``POST /v1/jobs/{id}/confirm``).
    """

    source: str
    title: str | None = None
    requires_confirmation: bool = False


class KeyBody(BaseModel):
    """Body of ``PUT /v1/keys`` — write-only; no endpoint ever returns a key."""

    provider: str
    api_key: str


class KeyTestBody(BaseModel):
    """Body of ``POST /v1/keys/test`` — *api_key* absent tests the stored key."""

    provider: str
    api_key: str | None = None


class KeyTestResult(BaseModel):
    """Body of the ``POST /v1/keys/test`` response.

    *detail* is always self-authored (HTTP status, transport error class, or
    our own missing-key text) — never provider response content, never a key.
    """

    ok: bool
    detail: str | None = None


class SettingsBody(BaseModel):
    """Body of ``PUT /v1/settings`` — mirrors :class:`EngineSettings`.

    Fields added after Phase 1 default to ``None`` ("keep the current value"),
    so PUTs from pre-change clients keep succeeding without resetting them.
    """

    whisper_model: str
    whisper_lang: str
    whisper_device: str
    sentences: int
    library_dir: str
    chapter_model: str
    chapter_provider: str | None = None
    custom_provider_url: str | None = None

    def to_settings(self, current: EngineSettings) -> EngineSettings:
        return EngineSettings(
            whisper_model=self.whisper_model,
            whisper_lang=self.whisper_lang,
            whisper_device=self.whisper_device,
            sentences=self.sentences,
            library_dir=str(Path(self.library_dir).expanduser()),
            chapter_model=self.chapter_model,
            chapter_provider=(
                self.chapter_provider
                if self.chapter_provider is not None
                else current["chapter_provider"]
            ),
            custom_provider_url=(
                self.custom_provider_url
                if self.custom_provider_url is not None
                else current["custom_provider_url"]
            ),
        )


class HealthInfo(BaseModel):
    """Body of ``GET /v1/health``."""

    version: str
    token_fingerprint: str


def create_app(
    data_dir: Path,
    store: JobStore,
    *,
    key_store: dict[str, str] | None = None,
    heartbeat_s: float = 15.0,
    on_shutdown: Callable[[], None] | None = None,
    key_test_transport: httpx.BaseTransport | None = None,
) -> FastAPI:
    """Build the engine's FastAPI app bound to *store* and *data_dir*.

    *key_store* is the process-memory chapter-API-key dict shared with the job
    runner (created in ``serve_engine``); keys live only there — never in any
    file or response.

    *on_shutdown* backs ``POST /v1/shutdown``: ``serve_engine`` injects a hook
    that sets the uvicorn server's ``should_exit`` (the server object exists
    only there). Without one, the endpoint answers 503 — never a silent no-op.

    *key_test_transport* lets tests route ``POST /v1/keys/test`` traffic
    through an ``httpx.MockTransport``; production uses the default transport.
    """
    app = FastAPI(title="podcast-reader engine", version=engine_version())
    expected_token = load_engine_state(data_dir)["token"].encode()
    keys: dict[str, str] = key_store if key_store is not None else {}

    @app.middleware("http")
    async def _require_bearer_token(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # RFC 7235: the scheme token is case-insensitive; only the credentials
        # are secret, so the constant-time comparison covers just the token.
        scheme, _, credentials = request.headers.get("authorization", "").partition(" ")
        authorized = scheme.lower() == "bearer" and hmac.compare_digest(
            credentials.strip().encode(), expected_token
        )
        if not authorized:
            return JSONResponse(
                {"detail": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)

    @app.get("/v1/health")
    def health() -> HealthInfo:
        state = load_engine_state(data_dir)
        return HealthInfo(
            version=engine_version(),
            token_fingerprint=token_fingerprint(state["token"]),
        )

    @app.post("/v1/shutdown", status_code=status.HTTP_202_ACCEPTED)
    def shutdown(background: BackgroundTasks) -> None:
        """Request graceful engine shutdown (portable: Windows has no SIGTERM).

        Responds 202 first — the hook runs as a background task after the
        response is sent, so the reply never races the server's exit.
        """
        if on_shutdown is None:
            raise HTTPException(status_code=503, detail="shutdown hook not configured")
        background.add_task(on_shutdown)

    @app.post("/v1/jobs", status_code=status.HTTP_201_CREATED)
    def submit_job(body: JobSubmission) -> JobRecord:
        return store.submit(
            body.source, body.title, requires_confirmation=body.requires_confirmation
        )

    @app.get("/v1/jobs")
    def list_jobs() -> list[JobRecord]:
        return store.list_jobs()

    @app.get("/v1/jobs/{job_id}")
    def get_job(job_id: str) -> JobRecord:
        try:
            return store.get(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="job not found") from exc

    @app.post("/v1/jobs/{job_id}/confirm")
    def confirm_job(job_id: str) -> JobRecord:
        """Transition an awaiting-confirmation job to ``queued`` and enqueue it."""
        try:
            return store.confirm(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="job not found") from exc
        except JobStateError as exc:  # self-authored message, safe to echo
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.delete("/v1/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
    def discard_job(job_id: str) -> None:
        """Discard a job — allowed only while it awaits confirmation."""
        try:
            store.discard(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="job not found") from exc
        except JobStateError as exc:  # self-authored message, safe to echo
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/v1/events")
    def events() -> StreamingResponse:
        client_queue = store.subscribe()

        def stream() -> Iterator[bytes]:
            try:
                while True:
                    try:
                        event = client_queue.get(timeout=heartbeat_s)
                    except queue.Empty:
                        yield b": keepalive\n\n"
                        continue
                    yield f"data: {json.dumps(event)}\n\n".encode()
            finally:
                store.unsubscribe(client_queue)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.get("/v1/library")
    def library() -> list[LibraryEntry]:
        return list_entries(_library_dir(data_dir))

    @app.get("/v1/transcripts/{source_id}.html")
    def transcript_html(source_id: str) -> FileResponse:
        entry = get_entry(_library_dir(data_dir), source_id)
        if entry is None or not Path(entry["html_path"]).exists():
            raise HTTPException(status_code=404, detail="transcript not found")
        return FileResponse(entry["html_path"], media_type="text/html")

    @app.put("/v1/keys", status_code=status.HTTP_204_NO_CONTENT)
    def put_key(body: KeyBody) -> None:
        """Store a chapter API key in process memory (write-only).

        An empty ``api_key`` clears the pushed key for that provider, restoring
        the env-variable fallback: key resolution at job dequeue treats a falsy
        stored value as "no pushed key" (intentional truthiness in
        ``_resolve_chapter_key``) and reads the provider's ``key_env`` instead.
        """
        if body.provider not in PROVIDERS:
            raise HTTPException(
                status_code=400, detail=f"unknown chapter provider: {body.provider!r}"
            )
        keys[body.provider] = body.api_key

    @app.post("/v1/keys/test")
    def test_key(body: KeyTestBody) -> KeyTestResult:
        """Minimal completion round-trip validating a key (never storing it).

        Key resolution order: supplied > pushed > the provider's env variable
        (an empty pushed value means "cleared", falling through to env — the
        same truthiness as job-time resolution). The result detail is always
        self-authored: provider response bodies echo key fragments, so they
        never reach the response or the logs (K4).
        """
        if body.provider not in PROVIDERS:
            raise HTTPException(
                status_code=400, detail=f"unknown chapter provider: {body.provider!r}"
            )
        settings = load_settings(data_dir)
        try:
            spec = resolve_provider(body.provider, custom_base_url=settings["custom_provider_url"])
        except ValueError as exc:  # missing/invalid custom URL — self-authored (per P9)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        api_key = body.api_key or keys.get(body.provider) or os.environ.get(spec["key_env"]) or ""
        if not api_key:
            return KeyTestResult(
                ok=False,
                detail=(
                    f"no API key available for provider {body.provider!r} "
                    f"(supply one, push one via PUT /v1/keys, or set {spec['key_env']})"
                ),
            )
        # Mirror job-time model selection: the configured chapter_model applies
        # to the configured provider only; other providers use their default.
        model = settings["chapter_model"] if body.provider == settings["chapter_provider"] else ""
        try:
            verify_key(
                spec=spec, api_key=api_key, model=model or None, transport=key_test_transport
            )
        except RuntimeError as exc:  # HTTP >= 400; message is status-only
            return KeyTestResult(ok=False, detail=str(exc))
        except httpx.HTTPError as exc:  # transport failure; keep detail self-authored
            return KeyTestResult(
                ok=False, detail=f"connection to provider failed ({type(exc).__name__})"
            )
        return KeyTestResult(ok=True)

    @app.get("/v1/settings")
    def get_settings() -> EngineSettings:
        return load_settings(data_dir)

    @app.put("/v1/settings")
    def put_settings(body: SettingsBody) -> EngineSettings:
        # Validate at write time (symmetric with PUT /v1/keys) so a bad value
        # fails the request, not a later job with an opaque warning. The
        # validator messages are self-authored, so safe to echo as detail.
        if body.chapter_provider is not None and body.chapter_provider not in PROVIDERS:
            raise HTTPException(
                status_code=400, detail=f"unknown chapter provider: {body.chapter_provider!r}"
            )
        if body.custom_provider_url:
            try:
                validate_custom_url(body.custom_provider_url)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        settings = body.to_settings(load_settings(data_dir))
        save_settings(data_dir, settings)
        return settings

    return app


def _library_dir(data_dir: Path) -> Path:
    """The library directory from the current user settings."""
    return Path(load_settings(data_dir)["library_dir"])
