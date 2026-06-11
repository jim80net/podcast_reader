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
import queue
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from podcast_reader.engine.library import get_entry, list_entries
from podcast_reader.engine.settings import (
    engine_version,
    load_engine_state,
    load_settings,
    save_settings,
    token_fingerprint,
)
from podcast_reader.providers import PROVIDERS

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
    """Body of ``POST /v1/jobs``."""

    source: str
    title: str | None = None


class KeyBody(BaseModel):
    """Body of ``PUT /v1/keys`` — write-only; no endpoint ever returns a key."""

    provider: str
    api_key: str


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
) -> FastAPI:
    """Build the engine's FastAPI app bound to *store* and *data_dir*.

    *key_store* is the process-memory chapter-API-key dict shared with the job
    runner (created in ``serve_engine``); keys live only there — never in any
    file or response.
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

    @app.post("/v1/jobs", status_code=status.HTTP_201_CREATED)
    def submit_job(body: JobSubmission) -> JobRecord:
        return store.submit(body.source, body.title)

    @app.get("/v1/jobs")
    def list_jobs() -> list[JobRecord]:
        return store.list_jobs()

    @app.get("/v1/jobs/{job_id}")
    def get_job(job_id: str) -> JobRecord:
        try:
            return store.get(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="job not found") from exc

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
        if body.provider not in PROVIDERS:
            raise HTTPException(
                status_code=400, detail=f"unknown chapter provider: {body.provider!r}"
            )
        keys[body.provider] = body.api_key

    @app.get("/v1/settings")
    def get_settings() -> EngineSettings:
        return load_settings(data_dir)

    @app.put("/v1/settings")
    def put_settings(body: SettingsBody) -> EngineSettings:
        settings = body.to_settings(load_settings(data_dir))
        save_settings(data_dir, settings)
        return settings

    return app


def _library_dir(data_dir: Path) -> Path:
    """The library directory from the current user settings."""
    return Path(load_settings(data_dir)["library_dir"])
