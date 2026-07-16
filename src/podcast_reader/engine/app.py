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
import re
import threading
from pathlib import Path
from typing import TYPE_CHECKING, cast
from urllib.parse import urlsplit

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict
from starlette.background import BackgroundTask

from podcast_reader.chapters import verify_key

# CookieJarInfo backs a FastAPI response model, so it must be importable at
# runtime (a TYPE_CHECKING import leaves unresolvable ForwardRefs).
from podcast_reader.engine.cookies import (
    CookieJarError,
    CookieJarInfo,  # noqa: TC001 — runtime response model
    delete_jar,
    list_jars,
    store_jar,
    validate_jar,
)
from podcast_reader.engine.embed import build_embed_page, is_valid_video_id
from podcast_reader.engine.jobs import JobStateError
from podcast_reader.engine.library import get_entry, list_entries
from podcast_reader.engine.pack_manager import (
    PackInstallingError,
    PackUnavailableError,
    UnknownPackError,
)

# PacksResponse backs a FastAPI response model, so it must be importable at
# runtime (a TYPE_CHECKING import leaves unresolvable ForwardRefs).
from podcast_reader.engine.packs import PacksResponse  # noqa: TC001 — runtime response model
from podcast_reader.engine.pairing import PairingState
from podcast_reader.engine.search import search_library
from podcast_reader.engine.settings import (
    engine_version,
    load_engine_state,
    load_settings,
    save_settings,
    token_fingerprint,
)
from podcast_reader.engine.web_session import SESSION_LIFETIME_S, WebSessionSigner
from podcast_reader.engine.web_surface import SHELL_CSP, asset_bytes, transcript_csp
from podcast_reader.html import without_legacy_remote_font_import
from podcast_reader.providers import (
    build_provider_registry,
    canonicalize_custom_providers,
    resolve_provider,
    validate_custom_url,
)

# JobRecord/LibraryEntry back FastAPI response models, so they must be
# importable at runtime (a TYPE_CHECKING import leaves unresolvable ForwardRefs).
from podcast_reader.types import (
    CustomProviderConfig,
    EngineSettings,
    JobOverrides,  # noqa: TC001 — used in a runtime cast
    JobRecord,  # noqa: TC001 — runtime response model
    LibraryEntry,  # noqa: TC001 — runtime response model
    MediaInfo,  # noqa: TC001 — runtime response model
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator

    from podcast_reader.engine.jobs import JobStore
    from podcast_reader.engine.media import MediaManager
    from podcast_reader.engine.pack_manager import PackManager

#: A library source_id is a sha256 hexdigest (library.source_identity). Media
#: routes validate against it so a path param can never reach a cache path as
#: traversal — defense in depth atop the app-side app:// scheme validation.
_SOURCE_ID_RE = re.compile(r"^[0-9a-f]{64}$")

#: The tokenless GET exemption matches the embed route EXACTLY (valid id only),
#: so a path like /v1/embed/ or /v1/embed/a/b never bypasses auth — minimizing
#: the unauthenticated surface as routes are added under the prefix.
_EMBED_PATH = re.compile(r"^/v1/embed/[A-Za-z0-9_-]{1,32}$")

_WEB_SESSION_COOKIE = "__Secure-podcast_reader_web"
_WEB_SESSION_GENERATION = 1
_WEB_MUTATIONS = frozenset(
    {
        ("POST", "/web/api/pair/claim"),
        ("POST", "/web/api/session"),
        ("POST", "/web/api/logout"),
        ("POST", "/web/api/search"),
    }
)
_WEB_BEARER_EXEMPT = frozenset(
    {
        ("POST", "/web/api/pair/claim"),
        ("POST", "/web/api/logout"),
        ("POST", "/web/api/search"),
    }
)
_WEB_PUBLIC_GET = frozenset(
    {
        ("GET", "/web/"),
        ("GET", "/web/assets/app.js"),
        ("GET", "/web/assets/app.css"),
    }
)
_WEB_COOKIE_GET = frozenset({("GET", "/web/api/library")})
_WEB_TRANSCRIPT_PATH = re.compile(r"^/web/api/transcripts/[^/]{1,256}\.html$")
_SEARCH_LOCK = threading.Lock()


def _https_authority(value: str, *, origin: bool) -> tuple[str, int] | None:
    """Normalize an HTTPS Origin or request Host to ``(hostname, port)``."""
    try:
        parsed = urlsplit(value if origin else f"//{value}")
        port = parsed.port
    except ValueError:
        return None
    if origin:
        if parsed.scheme.lower() != "https" or parsed.path or parsed.query or parsed.fragment:
            return None
    elif parsed.scheme or parsed.path or parsed.query or parsed.fragment:
        return None
    if parsed.username is not None or parsed.password is not None or parsed.hostname is None:
        return None
    return parsed.hostname.lower(), port if port is not None else 443


def _trusted_web_mutation(request: Request) -> bool:
    """Fail-closed header gate for browser claims and session mutations."""
    media_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
    raw_content_length = request.headers.get("content-length", "").strip()
    content_length = (
        int(raw_content_length)
        if 0 < len(raw_content_length) <= 10
        and raw_content_length.isascii()
        and raw_content_length.isdigit()
        else None
    )
    origin = _https_authority(request.headers.get("origin", ""), origin=True)
    host = _https_authority(request.headers.get("host", ""), origin=False)
    return (
        media_type == "application/json"
        and content_length is not None
        and 0 <= content_length <= MAX_CLAIM_BODY_BYTES
        and request.headers.get("sec-fetch-site", "").lower() == "same-origin"
        and origin is not None
        and origin == host
    )


def _web_rejection() -> JSONResponse:
    """Return the one response used for every browser request gate failure."""
    return JSONResponse(
        {"detail": "web request rejected"},
        status_code=403,
        headers={"Cache-Control": "no-store"},
    )


def _apply_web_security_headers(path: str, response: Response) -> Response:
    """Apply the mandatory headers to every response under ``/web/``."""
    if path.startswith("/web/"):
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        if path.startswith("/web/api/"):
            response.headers["Cache-Control"] = "no-store"
    return response


class JobOverridesBody(BaseModel):
    """Optional per-job model overrides for a rerun (omit a field to keep the
    setting). Present fields drive both the override and the cache-clearing:
    ``whisper_model`` forces a re-transcribe; chapter fields re-run chapters."""

    whisper_model: str | None = None
    chapter_provider: str | None = None
    chapter_model: str | None = None
    custom_provider_url: str | None = None


class JobSubmission(BaseModel):
    """Body of ``POST /v1/jobs``.

    ``requires_confirmation`` defaults to false so pre-change clients are
    unchanged; true journals the job in ``awaiting-confirmation`` without
    enqueueing it (it runs only after ``POST /v1/jobs/{id}/confirm``).
    ``overrides`` carries rerun model choices (absent = a plain submission).
    """

    source: str
    title: str | None = None
    requires_confirmation: bool = False
    overrides: JobOverridesBody | None = None


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


class ProviderInfo(BaseModel):
    """One ``GET /v1/providers`` entry (per P4).

    ``key_available`` is a boolean only — never key material in any form (no
    values, prefixes, lengths, or fingerprints).
    """

    id: str
    default_model: str
    key_available: bool


class CustomProviderBody(BaseModel):
    """Strict nonsecret configuration for one user-defined provider."""

    model_config = ConfigDict(extra="forbid", strict=True)

    name: str
    base_url: str
    default_model: str
    max_tokens: int


class SettingsBody(BaseModel):
    """Body of ``PUT /v1/settings`` — mirrors :class:`EngineSettings`.

    Fields added after Phase 1 default to ``None`` ("keep the current value"),
    so PUTs from pre-change clients keep succeeding without resetting them.
    """

    model_config = ConfigDict(extra="forbid")

    whisper_model: str
    whisper_lang: str
    whisper_device: str
    sentences: int
    library_dir: str
    chapter_model: str
    chapter_provider: str | None = None
    custom_provider_url: str | None = None
    custom_providers: list[CustomProviderBody] | None = None
    diarize: bool | None = None
    caption_cleanup: bool | None = None
    media_cache_max_bytes: int | None = None

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
            custom_providers=(
                cast(
                    "list[CustomProviderConfig]",
                    [entry.model_dump() for entry in self.custom_providers],
                )
                if self.custom_providers is not None
                else current["custom_providers"]
            ),
            diarize=self.diarize if self.diarize is not None else current["diarize"],
            caption_cleanup=(
                self.caption_cleanup
                if self.caption_cleanup is not None
                else current["caption_cleanup"]
            ),
            media_cache_max_bytes=(
                self.media_cache_max_bytes
                if self.media_cache_max_bytes is not None
                else current["media_cache_max_bytes"]
            ),
        )


class HealthInfo(BaseModel):
    """Body of ``GET /v1/health``."""

    version: str
    token_fingerprint: str


class CookieJarBody(BaseModel):
    """Body of ``PUT /v1/cookies`` — write-only; no endpoint returns jar content.

    *domain* is the registrable domain declared by the capturing client
    (per U4); *jar* is the Netscape-format cookie file content.
    """

    domain: str
    jar: str


class PairMintResponse(BaseModel):
    """Body of the ``POST /v1/pair`` response.

    *expires_at* is epoch seconds; the code itself lives only in process
    memory and in this one response — never in any file or log.
    """

    code: str
    expires_at: float


class PairClaimResponse(BaseModel):
    """Body of a successful ``POST /v1/pair/claim`` — the engine bearer token."""

    token: str


class EmptyWebBody(BaseModel):
    """An explicit empty JSON object for browser session mutations."""

    model_config = ConfigDict(extra="forbid")


class WebLibraryEntry(BaseModel):
    """The deliberately minimized browser projection of a library entry."""

    source_id: str
    title: str
    created_at: float


class WebSearchBody(BaseModel):
    """A private query body that is never reflected by validation errors."""

    model_config = ConfigDict(extra="forbid")

    query: str


class WebSearchResult(BaseModel):
    """The minimized projection for one matching transcript."""

    source_id: str
    title: str
    excerpt: str


class WebSearchResponse(BaseModel):
    """Bounded search results plus aggregate completeness signals."""

    results: list[WebSearchResult]
    has_more: bool
    partial: bool


#: Cap on the unauthenticated claim body (per V4): a legitimate claim is a
#: tiny JSON object, so anything declaring more — or declaring nothing
#: (chunked) — is rejected before the body is read.
MAX_CLAIM_BODY_BYTES = 4096


def create_app(
    data_dir: Path,
    store: JobStore,
    *,
    key_store: dict[str, str] | None = None,
    heartbeat_s: float = 15.0,
    on_shutdown: Callable[[], None] | None = None,
    key_test_transport: httpx.BaseTransport | None = None,
    pack_manager: PackManager | None = None,
    pairing: PairingState | None = None,
    media_manager: MediaManager | None = None,
    web_session_signer: WebSessionSigner | None = None,
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

    *pack_manager* backs the ``/v1/packs`` routes; ``serve_engine`` constructs
    one sharing the store's :class:`EventBus` so pack events ride the same
    SSE stream (per S6). Without one, the routes answer 503 (the shutdown-hook
    pattern) — never a silent no-op.

    *pairing* is the in-memory pairing-code state backing ``POST /v1/pair``
    and ``POST /v1/pair/claim``; tests inject one with a settable clock.

    *web_session_signer* is the test seam for the stateless browser-session
    clock and revoke generation. Production derives it from the engine bearer.
    """
    app = FastAPI(title="podcast-reader engine", version=engine_version())
    expected_token = load_engine_state(data_dir)["token"].encode()
    keys: dict[str, str] = key_store if key_store is not None else {}
    pairing_state = pairing if pairing is not None else PairingState()
    web_sessions = (
        web_session_signer
        if web_session_signer is not None
        else WebSessionSigner(expected_token, generation=_WEB_SESSION_GENERATION)
    )

    def _search(body: WebSearchBody) -> WebSearchResponse | JSONResponse:
        """Run the one bounded search contract shared by desktop and web."""
        query = body.query.strip()
        terms = query.split()
        if not 2 <= len(query) <= 100 or not 1 <= len(terms) <= 8:
            raise HTTPException(
                status_code=422,
                detail="query must be 2 to 100 characters and contain at most 8 terms",
            )
        if not _SEARCH_LOCK.acquire(blocking=False):
            return JSONResponse(
                {"detail": "search busy"},
                status_code=429,
                headers={"Retry-After": "1", "Cache-Control": "no-store"},
            )
        try:
            outcome = search_library(list_entries(_library_dir(data_dir)), query)
        finally:
            _SEARCH_LOCK.release()
        return WebSearchResponse(
            results=[
                WebSearchResult(
                    source_id=result.source_id,
                    title=result.title,
                    excerpt=result.excerpt,
                )
                for result in outcome.results
            ],
            has_more=outcome.has_more,
            partial=outcome.partial,
        )

    provider_config_lock = threading.RLock()

    @app.exception_handler(RequestValidationError)
    async def _validation_error_without_inputs(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Return useful validation locations without echoing submitted secrets.

        Pydantic's default 422 body includes each rejected ``input`` value. A
        misspelled key field in settings (or a malformed key request) would
        therefore reflect credential material even though the route never ran.
        """
        safe_errors = [
            {key: value for key, value in error.items() if key not in {"input", "ctx", "url"}}
            for error in exc.errors()
        ]
        headers = {"Cache-Control": "no-store"} if request.url.path.startswith("/web/") else None
        return JSONResponse({"detail": safe_errors}, status_code=422, headers=headers)

    @app.middleware("http")
    async def _require_bearer_token(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        route = (request.method, request.url.path)
        if route in _WEB_MUTATIONS and not _trusted_web_mutation(request):
            return _web_rejection()

        # The engine's unauthenticated routes:
        #  - POST /v1/pair/claim issues the token to a not-yet-authenticated
        #    extension (matched exactly — any other method still needs the token).
        #  - GET /v1/embed/<id> serves a YouTube embed page loaded by the Reader
        #    iframe, which holds no token; it returns only a static, video-id-
        #    parameterized page (no secrets, no library/job data) so it is safe
        #    to expose, and it MUST be tokenless so YouTube sees the loopback
        #    http origin (the Error 152/153 fix).
        if request.method == "POST" and request.url.path == "/v1/pair/claim":
            return await call_next(request)
        if (
            route in _WEB_BEARER_EXEMPT
            or route in _WEB_PUBLIC_GET
            or route in _WEB_COOKIE_GET
            or (request.method == "GET" and _WEB_TRANSCRIPT_PATH.match(request.url.path))
        ):
            return await call_next(request)
        if request.method == "GET" and _EMBED_PATH.match(request.url.path):
            return await call_next(request)
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
                headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"},
            )
        return await call_next(request)

    @app.middleware("http")
    async def _secure_web_responses(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Apply privacy headers to successes and framework-generated errors."""
        try:
            response = await call_next(request)
        except Exception:
            if not request.url.path.startswith("/web/"):
                raise
            response = JSONResponse({"detail": "internal server error"}, status_code=500)
        return _apply_web_security_headers(request.url.path, response)

    @app.get("/v1/health")
    def health() -> HealthInfo:
        state = load_engine_state(data_dir)
        return HealthInfo(
            version=engine_version(),
            token_fingerprint=token_fingerprint(state["token"]),
        )

    @app.post("/v1/pair")
    def pair_mint() -> PairMintResponse:
        """Mint a pairing code (bearer-authed: only token-holders can mint).

        The code is single-use with a 300 s TTL and replaces any pending one;
        it is held exclusively in process memory.
        """
        code, expires_at = pairing_state.mint()
        return PairMintResponse(code=code, expires_at=expires_at)

    @app.post("/v1/pair/claim")
    async def pair_claim(request: Request) -> PairClaimResponse:
        """Exchange a pending pairing code for the engine bearer token.

        The single unauthenticated route (exempted by the middleware's
        (method, path) match). Per U3, two gates keep in-browser attackers
        from burning the attempt budget: a non-``application/json`` content
        type is rejected (a page-initiated JSON request is non-simple, so the
        browser preflights it and it never arrives), and an ``http``/``https``
        scheme ``Origin`` is rejected as the simple-request backstop —
        ``chrome-extension://`` origins pass. A third gate (per V4) bounds
        the unauthenticated body read: a missing (chunked) or oversized
        ``Content-Length`` is rejected before the body is touched. Gate
        rejections never reach the pairing state, so they cannot burn the
        attempt budget. Every rejection is the same self-authored 403: no
        oracle distinguishes wrong, expired, exhausted, or absent codes from
        gated requests.
        """
        rejection = HTTPException(status_code=403, detail="pairing claim rejected")
        media_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
        if media_type != "application/json":
            raise rejection
        origin_scheme = request.headers.get("origin", "").partition(":")[0].strip().lower()
        if origin_scheme in ("http", "https"):
            raise rejection
        content_length = request.headers.get("content-length", "").strip()
        if not content_length.isdigit() or int(content_length) > MAX_CLAIM_BODY_BYTES:
            raise rejection
        try:
            body = json.loads(await request.body())
        except ValueError:
            body = None
        code = body.get("code") if isinstance(body, dict) else None
        if not isinstance(code, str) or not pairing_state.claim(code):
            raise rejection
        return PairClaimResponse(token=expected_token.decode())

    @app.post("/web/api/pair/claim", response_model=PairClaimResponse)
    async def web_pair_claim(request: Request, response: Response) -> PairClaimResponse | Response:
        """Claim the shared pairing code from the exact tailnet HTTPS origin.

        The middleware applies the bounded-JSON and same-origin gate before the
        body is read. Every body/code rejection remains uniform and never
        reveals whether a pending code exists.
        """
        try:
            body = json.loads(await request.body())
        except ValueError:
            body = None
        code = body.get("code") if isinstance(body, dict) else None
        if not isinstance(code, str) or not pairing_state.claim(code):
            return _web_rejection()
        response.headers["Cache-Control"] = "no-store"
        return PairClaimResponse(token=expected_token.decode())

    @app.post("/web/api/session", status_code=status.HTTP_204_NO_CONTENT)
    def web_session_create(_body: EmptyWebBody) -> Response:
        """Exchange a verified candidate bearer for a scoped HttpOnly session."""
        response = Response(status_code=status.HTTP_204_NO_CONTENT)
        response.set_cookie(
            key=_WEB_SESSION_COOKIE,
            value=web_sessions.issue(),
            max_age=SESSION_LIFETIME_S,
            secure=True,
            httponly=True,
            samesite="strict",
            path="/web/",
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/web/api/logout", status_code=status.HTTP_204_NO_CONTENT)
    def web_logout(request: Request, _body: EmptyWebBody) -> Response:
        """Clear this browser's session after validating its current cookie."""
        credential = request.cookies.get(_WEB_SESSION_COOKIE, "")
        if not web_sessions.verify(credential):
            return JSONResponse(
                {"detail": "unauthorized"},
                status_code=401,
                headers={"Cache-Control": "no-store"},
            )
        response = Response(status_code=status.HTTP_204_NO_CONTENT)
        response.delete_cookie(
            key=_WEB_SESSION_COOKIE,
            secure=True,
            httponly=True,
            samesite="strict",
            path="/web/",
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    def _require_web_session(request: Request) -> None:
        credential = request.cookies.get(_WEB_SESSION_COOKIE, "")
        if not web_sessions.verify(credential):
            raise HTTPException(status_code=401, detail="unauthorized")

    @app.get("/web/")
    def web_shell() -> Response:
        return Response(
            asset_bytes("shell.html"),
            media_type="text/html",
            headers={"Content-Security-Policy": SHELL_CSP},
        )

    @app.get("/web/assets/app.js")
    def web_script() -> Response:
        return Response(asset_bytes("app.js"), media_type="text/javascript")

    @app.get("/web/assets/app.css")
    def web_stylesheet() -> Response:
        return Response(asset_bytes("app.css"), media_type="text/css")

    @app.get("/web/api/library", response_model=list[WebLibraryEntry])
    def web_library(request: Request) -> list[WebLibraryEntry]:
        _require_web_session(request)
        return [
            WebLibraryEntry(
                source_id=entry["source_id"],
                title=entry["title"],
                created_at=entry["created_at"],
            )
            for entry in list_entries(_library_dir(data_dir))
        ]

    @app.get("/web/api/transcripts/{source_id}.html")
    def web_transcript(request: Request, source_id: str) -> Response:
        _require_web_session(request)
        if not _SOURCE_ID_RE.fullmatch(source_id):
            raise HTTPException(status_code=404, detail="transcript not found")
        entry = get_entry(_library_dir(data_dir), source_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="transcript not found")
        path = Path(entry["html_path"])
        if not path.is_file():
            raise HTTPException(status_code=404, detail="transcript not found")
        document = without_legacy_remote_font_import(path.read_bytes())
        return Response(
            document,
            media_type="text/html",
            headers={"Content-Security-Policy": transcript_csp(document)},
        )

    @app.post("/web/api/search", response_model=WebSearchResponse)
    def web_search(request: Request, body: WebSearchBody) -> WebSearchResponse | JSONResponse:
        """Search bounded local artifacts without putting terms in a URL or cache."""
        _require_web_session(request)
        return _search(body)

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
        # Keep only the set override fields (exclude_none), so the runner clears
        # exactly the categories the user chose to change.
        overrides = (
            cast("JobOverrides", body.overrides.model_dump(exclude_none=True))
            if body.overrides is not None
            else None
        )
        with provider_config_lock:
            if overrides is not None:
                # Fail fast (like PUT /v1/settings) so a bad rerun input is a 400,
                # not a job that runs and degrades. Only present fields are checked
                # (a custom base URL may legitimately come from the settings).
                prov = overrides.get("chapter_provider")
                registry = build_provider_registry(load_settings(data_dir)["custom_providers"])
                if prov is not None and prov not in registry:
                    raise HTTPException(
                        status_code=400, detail=f"unknown chapter provider: {prov!r}"
                    )
                custom_url = overrides.get("custom_provider_url")
                if custom_url:
                    try:
                        validate_custom_url(custom_url)
                    except ValueError as exc:
                        raise HTTPException(status_code=400, detail=str(exc)) from exc
            return store.submit(
                body.source,
                body.title,
                requires_confirmation=body.requires_confirmation,
                overrides=overrides,
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

        # The finally above only runs when the generator is CLOSED. On client
        # disconnect starlette cancels the stream without closing the sync
        # generator (iterate_in_threadpool holds it in a reference cycle), so
        # closure waits for the cyclic GC — unbounded unsubscribe latency and
        # a host-dependent leak (issue #48; red locally / green in CI came
        # down to GC timing). The background task is starlette's deterministic
        # after-response hook — it runs on normal completion AND after a
        # disconnect cancellation. unsubscribe is idempotent, so the pair is
        # belt-and-suspenders, not a double-free.
        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            background=BackgroundTask(store.unsubscribe, client_queue),
        )

    def _require_pack_manager() -> PackManager:
        if pack_manager is None:
            raise HTTPException(status_code=503, detail="pack manager not configured")
        return pack_manager

    @app.get("/v1/packs")
    def list_packs() -> PacksResponse:
        """Hardware block + per-pack status — the hydration source of truth
        for clients that missed pack events (the job-record pattern)."""
        return _require_pack_manager().packs_response()

    @app.post("/v1/packs/{pack_id}/install", status_code=status.HTTP_202_ACCEPTED)
    def install_pack(pack_id: str) -> None:
        """Start (or idempotently re-request) an async pack install.

        202 always when the pack is installable — including while it is
        already installing or installed (no duplicate work). 404 for unknown
        ids; 409 for unpublished (per S5) or platform-gated packs.
        """
        try:
            _require_pack_manager().request_install(pack_id)
        except UnknownPackError as exc:
            raise HTTPException(status_code=404, detail="pack not found") from exc
        except PackUnavailableError as exc:  # self-authored message, safe to echo
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.delete("/v1/packs/{pack_id}", status_code=status.HTTP_204_NO_CONTENT)
    def uninstall_pack(pack_id: str) -> None:
        """Uninstall a pack (manifest first, per S1).

        A running job is no reason to refuse (the pipeline validates pack
        manifests at step start); 409 only while that pack is installing.
        """
        try:
            _require_pack_manager().uninstall(pack_id)
        except UnknownPackError as exc:
            raise HTTPException(status_code=404, detail="pack not found") from exc
        except PackInstallingError as exc:  # self-authored message, safe to echo
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    def _require_media_manager() -> MediaManager:
        if media_manager is None:
            raise HTTPException(status_code=503, detail="media manager not configured")
        return media_manager

    def _valid_source_id(source_id: str) -> str:
        # A non-matching id can never reach a cache path; 404 (not 400) keeps the
        # surface uniform with the not-found cases below.
        if not _SOURCE_ID_RE.match(source_id):
            raise HTTPException(status_code=404, detail="media not found")
        return source_id

    @app.get("/v1/embed/{video_id}")
    def youtube_embed(video_id: str) -> HTMLResponse:
        """Tokenless YouTube embed page (loaded by the Reader iframe).

        Served from the loopback origin so the player gets a valid http origin
        (the Error 152/153 fix). Returns only a static, id-parameterized page.
        """
        if not is_valid_video_id(video_id):
            raise HTTPException(status_code=404, detail="invalid video id")
        return HTMLResponse(build_embed_page(video_id))

    @app.get("/v1/media/{source_id}/info")
    def media_info(source_id: str) -> MediaInfo:
        """Player kind + preparation status; kicks off a lazy remote download."""
        return _require_media_manager().media_info(_valid_source_id(source_id))

    @app.get("/v1/media/{source_id}")
    def media_bytes(source_id: str) -> FileResponse:
        """Serve the ready media bytes with Range (FileResponse, per F5)."""
        path = _require_media_manager().ready_path(_valid_source_id(source_id))
        if path is None:
            raise HTTPException(status_code=404, detail="media not found")
        return FileResponse(path)

    @app.get("/v1/library")
    def library() -> list[LibraryEntry]:
        return list_entries(_library_dir(data_dir))

    @app.post("/v1/search", response_model=WebSearchResponse)
    def desktop_search(response: Response, body: WebSearchBody) -> WebSearchResponse | JSONResponse:
        """Search private artifacts without exposing the query in a URL or cache."""
        response.headers["Cache-Control"] = "no-store"
        return _search(body)

    @app.get("/v1/transcripts/{source_id}.html")
    def transcript_html(source_id: str) -> Response:
        entry = get_entry(_library_dir(data_dir), source_id)
        if entry is None or not Path(entry["html_path"]).exists():
            raise HTTPException(status_code=404, detail="transcript not found")
        document = without_legacy_remote_font_import(Path(entry["html_path"]).read_bytes())
        return Response(document, media_type="text/html")

    @app.put("/v1/keys", status_code=status.HTTP_204_NO_CONTENT)
    def put_key(body: KeyBody) -> None:
        """Store a chapter API key in process memory (write-only).

        An empty ``api_key`` clears the pushed key for that provider, restoring
        the env-variable fallback: key resolution at job dequeue treats a falsy
        stored value as "no pushed key" (intentional truthiness in
        ``_resolve_chapter_key``) and reads the provider's ``key_env`` instead.
        """
        with provider_config_lock:
            registry = build_provider_registry(load_settings(data_dir)["custom_providers"])
            if body.provider not in registry and body.api_key:
                raise HTTPException(
                    status_code=400, detail=f"unknown chapter provider: {body.provider!r}"
                )
            if body.api_key:
                keys[body.provider] = body.api_key
            else:
                keys.pop(body.provider, None)

    @app.post("/v1/keys/test")
    def test_key(body: KeyTestBody) -> KeyTestResult:
        """Minimal completion round-trip validating a key (never storing it).

        Key resolution order: supplied > pushed > the provider's env variable
        (an empty pushed value means "cleared", falling through to env — the
        same truthiness as job-time resolution). The result detail is always
        self-authored: provider response bodies echo key fragments, so they
        never reach the response or the logs (K4).
        """
        settings = load_settings(data_dir)
        registry = build_provider_registry(settings["custom_providers"])
        if body.provider not in registry:
            raise HTTPException(
                status_code=400, detail=f"unknown chapter provider: {body.provider!r}"
            )
        try:
            spec = resolve_provider(
                body.provider,
                custom_base_url=settings["custom_provider_url"],
                custom_providers=settings["custom_providers"],
            )
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

    @app.get("/v1/providers")
    def list_providers() -> list[ProviderInfo]:
        """The chapter-provider registry, so it has exactly one home (per P4).

        ``key_available`` mirrors job-time key resolution: a pushed key counts
        (empty = cleared), else the provider's env variable.
        """
        settings = load_settings(data_dir)
        registry = build_provider_registry(settings["custom_providers"])
        return [
            ProviderInfo(
                id=name,
                default_model=spec["default_model"],
                key_available=bool(keys.get(name) or os.environ.get(spec["key_env"])),
            )
            for name, spec in registry.items()
        ]

    @app.put("/v1/cookies", status_code=status.HTTP_204_NO_CONTENT)
    def put_cookies(body: CookieJarBody) -> None:
        """Validate and store a Netscape cookie jar for one declared domain.

        Validation (cookie-management spec): Netscape parse incl.
        ``#HttpOnly_`` lines, per-cookie domain suffix-match with leading
        dots stripped (per U4), 1 MB cap. The error detail is self-authored
        (line numbers, never cookie names/values), so safe to echo.
        """
        try:
            validate_jar(body.domain, body.jar)
        except CookieJarError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        store_jar(data_dir, body.domain, body.jar)

    @app.get("/v1/cookies")
    def get_cookies() -> list[CookieJarInfo]:
        """Stored-jar metadata only (``[{domain, created_at}]``) — never values."""
        return list_jars(data_dir)

    @app.delete("/v1/cookies/{domain}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_cookies(domain: str) -> None:
        """Remove a stored jar (404 when absent or not a valid domain name)."""
        if not delete_jar(data_dir, domain):
            raise HTTPException(status_code=404, detail="cookie jar not found")

    @app.get("/v1/settings")
    def get_settings() -> EngineSettings:
        return load_settings(data_dir)

    @app.put("/v1/settings")
    def put_settings(body: SettingsBody) -> EngineSettings:
        # Validate at write time (symmetric with PUT /v1/keys) so a bad value
        # fails the request, not a later job with an opaque warning. The
        # validator messages are self-authored, so safe to echo as detail.
        with provider_config_lock:
            current = load_settings(data_dir)
            settings = body.to_settings(current)
            try:
                settings["custom_providers"] = canonicalize_custom_providers(
                    settings["custom_providers"]
                )
                registry = build_provider_registry(settings["custom_providers"])
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if settings["chapter_provider"] not in registry:
                raise HTTPException(
                    status_code=400,
                    detail=f"unknown chapter provider: {settings['chapter_provider']!r}",
                )
            removed = {entry["name"] for entry in current["custom_providers"]} - {
                entry["name"] for entry in settings["custom_providers"]
            }
            if removed:
                for job in store.list_jobs():
                    overrides = job.get("overrides") or {}
                    provider = overrides.get("chapter_provider")
                    if (
                        job["state"] not in {"done", "failed", "interrupted"}
                        and provider in removed
                    ):
                        raise HTTPException(
                            status_code=409,
                            detail=(
                                f"provider {provider!r} is referenced by "
                                f"nonterminal job {job['id']!r}; "
                                "finish or discard that job before removing the provider"
                            ),
                        )
            # Validate the EFFECTIVE values (body merged over current settings):
            # the custom provider needs a non-empty, valid base URL no matter
            # whether it arrives in this PUT or was persisted earlier, and any
            # explicitly supplied URL must be valid regardless of provider.
            if settings["chapter_provider"] == "custom" or settings["custom_provider_url"]:
                try:
                    validate_custom_url(settings["custom_provider_url"])
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
            save_settings(data_dir, settings)
            for name in removed:
                keys.pop(name, None)
            return settings

    return app


def _library_dir(data_dir: Path) -> Path:
    """The library directory from the current user settings."""
    return Path(load_settings(data_dir)["library_dir"])
