from __future__ import annotations

import hashlib
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import cast

from fastapi import Cookie, Depends, FastAPI, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session, sessionmaker
from starlette.exceptions import HTTPException

from .config import Settings
from .contracts import EntitlementV1, default_free_entitlement
from .db import begin_immediate, create_database, require_current_schema
from .models import (
    AccessToken,
    BrowserSession,
    DeviceAuthorization,
    RefreshToken,
    TokenFamily,
    User,
)
from .security import (
    DUMMY_PASSWORD_HASH,
    RateLimiter,
    csrf_digest,
    hash_password,
    normalize_email,
    now_epoch,
    opaque_token,
    record_id,
    token_digest,
    user_code,
    user_code_digest,
    verify_password,
)

SESSION_COOKIE = "__Host-pr_session"
GENERIC_LOGIN_MESSAGE = "Email or password is incorrect"


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        self.status = status
        self.code = code
        self.message = message


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AccountInput(StrictModel):
    email: str = Field(max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class DeviceInput(StrictModel):
    client: str


class DevicePollInput(StrictModel):
    device_code: str = Field(min_length=20, max_length=256)


class DeviceApproveInput(StrictModel):
    user_code: str = Field(min_length=8, max_length=16)


class RefreshInput(StrictModel):
    refresh_token: str = Field(min_length=20, max_length=256)


def _request_id(request: Request) -> str:
    return cast("str", request.state.request_id)


def _error(request: Request, error: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status,
        content={"code": error.code, "message": error.message, "request_id": _request_id(request)},
    )


def _session_factory(request: Request) -> sessionmaker[Session]:
    return cast("sessionmaker[Session]", request.app.state.session_factory)


def _database_session(request: Request) -> Iterator[Session]:
    with _session_factory(request)() as database:
        yield database


def _browser_user(
    request: Request,
    database: Session = Depends(_database_session),
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> User:
    if session_token is None:
        raise ApiError(401, "browser_session_required", "Sign in to continue")
    timestamp = now_epoch()
    browser_session = database.get(BrowserSession, token_digest(session_token))
    if (
        browser_session is None
        or browser_session.revoked_at is not None
        or browser_session.expires_at <= timestamp
    ):
        raise ApiError(401, "browser_session_required", "Sign in to continue")
    user = database.get(User, browser_session.user_id)
    if user is None or user.status != "active":
        raise ApiError(401, "browser_session_required", "Sign in to continue")
    request.state.browser_session = browser_session
    request.state.browser_session_digest = browser_session.token_digest
    return user


def _require_browser_mutation(
    request: Request,
    user: User = Depends(_browser_user),
    csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> User:
    settings: Settings = request.app.state.settings
    origin = request.headers.get("origin")
    host = request.headers.get("host", "").lower()
    if not secrets.compare_digest(origin or "", settings.public_origin):
        raise ApiError(403, "origin_rejected", "Request origin was rejected")
    if not secrets.compare_digest(host, settings.expected_host):
        raise ApiError(403, "host_rejected", "Request host was rejected")
    browser_session: BrowserSession = request.state.browser_session
    supplied = csrf_digest(csrf_token or "")
    if not secrets.compare_digest(supplied, browser_session.csrf_digest):
        raise ApiError(403, "csrf_rejected", "CSRF token was rejected")
    return user


def _bearer_user(
    request: Request,
    authorization: str | None = Header(default=None),
    database: Session = Depends(_database_session),
) -> User:
    if authorization is None or not authorization.startswith("Bearer "):
        raise ApiError(401, "access_token_required", "A bearer access token is required")
    raw = authorization.removeprefix("Bearer ")
    if not raw or " " in raw:
        raise ApiError(401, "access_token_invalid", "The access token is invalid")
    timestamp = now_epoch()
    access = database.get(AccessToken, token_digest(raw))
    if access is None or access.revoked_at is not None or access.expires_at <= timestamp:
        raise ApiError(401, "access_token_invalid", "The access token is invalid")
    family = database.get(TokenFamily, access.family_id)
    if family is None or family.revoked_at is not None or family.expires_at <= timestamp:
        raise ApiError(401, "access_token_invalid", "The access token is invalid")
    user = database.get(User, access.user_id)
    if user is None or user.status != "active":
        raise ApiError(401, "access_token_invalid", "The access token is invalid")
    return user


def _issue_tokens(
    database: Session,
    settings: Settings,
    user: User,
    client_kind: str,
    *,
    family: TokenFamily | None = None,
    generation: int = 0,
) -> dict[str, object]:
    timestamp = now_epoch()
    if family is None:
        family = TokenFamily(
            id=record_id("fam"),
            user_id=user.id,
            client_kind=client_kind,
            expires_at=timestamp + settings.refresh_ttl_seconds,
            revoked_at=None,
            created_at=timestamp,
        )
        database.add(family)
        database.flush()
    access_raw = opaque_token()
    refresh_raw = opaque_token()
    database.add(
        AccessToken(
            token_digest=token_digest(access_raw),
            family_id=family.id,
            user_id=user.id,
            expires_at=timestamp + settings.access_ttl_seconds,
            revoked_at=None,
            created_at=timestamp,
        )
    )
    database.add(
        RefreshToken(
            token_digest=token_digest(refresh_raw),
            family_id=family.id,
            generation=generation,
            expires_at=timestamp + settings.refresh_ttl_seconds,
            used_at=None,
            replacement_digest=None,
            created_at=timestamp,
        )
    )
    return {
        "access_token": access_raw,
        "token_type": "Bearer",
        "expires_in": settings.access_ttl_seconds,
        "refresh_token": refresh_raw,
    }


def create_app(settings: Settings, *, engine: Engine | None = None) -> FastAPI:
    database_engine = engine or create_database(settings)
    limiter = RateLimiter()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        require_current_schema(database_engine)
        yield
        database_engine.dispose()

    app = FastAPI(title="Podcast Reader premium dev service", version="1", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = database_engine
    app.state.session_factory = sessionmaker(database_engine, expire_on_commit=False)

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.state.request_id = record_id("req")
        try:
            response = await call_next(request)
        except Exception:
            response = _error(
                request, ApiError(500, "internal_error", "The request could not be completed")
            )
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ApiError)
    async def api_error(request: Request, error: ApiError) -> JSONResponse:
        return _error(request, error)

    @app.exception_handler(RequestValidationError)
    async def validation_error(
        request: Request, _error_value: RequestValidationError
    ) -> JSONResponse:
        return _error(request, ApiError(422, "invalid_request", "Request data is invalid"))

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> JSONResponse:
        code = "not_found" if error.status_code == 404 else "http_error"
        message = "Route not found" if error.status_code == 404 else "Request was rejected"
        return _error(request, ApiError(error.status_code, code, message))

    @app.get("/healthz")
    def health() -> dict[str, object]:
        return {"status": "ok", "schema": 1, "build_sha": settings.build_sha}

    @app.post("/v1/accounts", status_code=201)
    def create_account(
        payload: AccountInput, database: Session = Depends(_database_session)
    ) -> dict[str, object]:
        if settings.environment not in {"dev", "test"}:
            raise ApiError(404, "not_found", "Route not found")
        try:
            email = normalize_email(payload.email)
            encoded = hash_password(payload.password)
        except ValueError as exc:
            raise ApiError(422, "invalid_account", str(exc)) from exc
        if database.scalar(select(User.id).where(User.email == email)) is not None:
            raise ApiError(409, "account_exists", "An account already exists for this email")
        timestamp = now_epoch()
        user = User(
            id=record_id("usr"),
            email=email,
            password_hash=encoded,
            role="user",
            status="active",
            verification="unverified_test",
            created_at=timestamp,
        )
        database.add(user)
        database.commit()
        return {"id": user.id, "email": user.email, "verification": user.verification}

    @app.post("/v1/browser-sessions", status_code=201)
    def create_browser_session(
        payload: AccountInput,
        request: Request,
        response: Response,
        database: Session = Depends(_database_session),
    ) -> dict[str, object]:
        try:
            email = normalize_email(payload.email)
        except ValueError:
            email = "invalid"
        source = request.client.host if request.client else "unknown"
        limiter_key = hashlib.sha256(f"{source}\0{email}".encode()).hexdigest()
        if not limiter.allow(limiter_key):
            raise ApiError(429, "rate_limited", "Too many sign-in attempts")
        user = database.scalar(select(User).where(User.email == email))
        encoded = user.password_hash if user is not None else DUMMY_PASSWORD_HASH
        password_valid = verify_password(encoded, payload.password)
        valid = user is not None and user.status == "active" and password_valid
        if not valid or user is None:
            raise ApiError(401, "login_failed", GENERIC_LOGIN_MESSAGE)
        timestamp = now_epoch()
        session_raw = opaque_token()
        csrf_raw = opaque_token()
        database.add(
            BrowserSession(
                token_digest=token_digest(session_raw),
                user_id=user.id,
                csrf_digest=csrf_digest(csrf_raw),
                expires_at=timestamp + settings.session_ttl_seconds,
                revoked_at=None,
                created_at=timestamp,
            )
        )
        database.commit()
        response.set_cookie(
            SESSION_COOKIE,
            session_raw,
            max_age=settings.session_ttl_seconds,
            secure=True,
            httponly=True,
            samesite="lax",
            path="/",
        )
        return {
            "csrf_token": csrf_raw,
            "account": {"id": user.id, "email": user.email, "verification": user.verification},
        }

    @app.delete("/v1/browser-sessions/current", status_code=204)
    def delete_browser_session(
        request: Request,
        response: Response,
        _user: User = Depends(_require_browser_mutation),
        database: Session = Depends(_database_session),
    ) -> None:
        browser_session = database.get(BrowserSession, request.state.browser_session_digest)
        if browser_session is None:
            raise ApiError(401, "browser_session_required", "Sign in to continue")
        browser_session.revoked_at = now_epoch()
        database.commit()
        response.delete_cookie(SESSION_COOKIE, secure=True, httponly=True, samesite="lax", path="/")

    @app.post("/v1/device-authorizations", status_code=201)
    def create_device_authorization(
        payload: DeviceInput, database: Session = Depends(_database_session)
    ) -> dict[str, object]:
        if payload.client not in {"desktop", "android"}:
            raise ApiError(422, "invalid_client", "Client must be desktop or android")
        timestamp = now_epoch()
        device_raw = opaque_token()
        visible_code = user_code()
        database.add(
            DeviceAuthorization(
                id=record_id("dev"),
                device_code_digest=token_digest(device_raw),
                user_code_digest=user_code_digest(visible_code, settings.user_code_pepper),
                client_kind=payload.client,
                state="pending",
                approving_user_id=None,
                interval_seconds=settings.device_poll_interval_seconds,
                poll_count=0,
                last_polled_at=None,
                expires_at=timestamp + settings.device_ttl_seconds,
                consumed_at=None,
                created_at=timestamp,
            )
        )
        database.commit()
        return {
            "device_code": device_raw,
            "user_code": visible_code,
            "verification_uri": f"{settings.public_origin}/device",
            "expires_in": settings.device_ttl_seconds,
            "interval": settings.device_poll_interval_seconds,
        }

    @app.post("/v1/device-authorizations/approve", status_code=204)
    def approve_device_authorization(
        payload: DeviceApproveInput,
        user: User = Depends(_require_browser_mutation),
        database: Session = Depends(_database_session),
    ) -> None:
        digest = user_code_digest(payload.user_code, settings.user_code_pepper)
        authorization = database.scalar(
            select(DeviceAuthorization).where(DeviceAuthorization.user_code_digest == digest)
        )
        timestamp = now_epoch()
        if (
            authorization is None
            or authorization.state != "pending"
            or authorization.expires_at <= timestamp
        ):
            raise ApiError(
                404, "device_authorization_not_found", "Device code is invalid or expired"
            )
        authorization.state = "approved"
        authorization.approving_user_id = user.id
        database.commit()

    @app.post("/v1/device-authorizations/token")
    def poll_device_authorization(
        payload: DevicePollInput, database: Session = Depends(_database_session)
    ) -> dict[str, object]:
        begin_immediate(database)
        authorization = database.scalar(
            select(DeviceAuthorization).where(
                DeviceAuthorization.device_code_digest == token_digest(payload.device_code)
            )
        )
        timestamp = now_epoch()
        if authorization is None or authorization.expires_at <= timestamp:
            raise ApiError(400, "expired_token", "Device authorization is invalid or expired")
        if authorization.consumed_at is not None:
            raise ApiError(400, "expired_token", "Device authorization is invalid or expired")
        if authorization.poll_count >= settings.device_max_polls:
            authorization.state = "denied"
            database.commit()
            raise ApiError(400, "access_denied", "Device authorization was denied")
        authorization.poll_count += 1
        if (
            authorization.last_polled_at is not None
            and timestamp - authorization.last_polled_at < authorization.interval_seconds
        ):
            authorization.interval_seconds = min(authorization.interval_seconds + 5, 30)
            authorization.last_polled_at = timestamp
            database.commit()
            raise ApiError(400, "slow_down", "Poll less frequently")
        authorization.last_polled_at = timestamp
        if authorization.state != "approved" or authorization.approving_user_id is None:
            database.commit()
            raise ApiError(400, "authorization_pending", "Authorization is still pending")
        user = database.get(User, authorization.approving_user_id)
        if user is None:
            authorization.state = "denied"
            database.commit()
            raise ApiError(400, "access_denied", "Device authorization was denied")
        authorization.state = "consumed"
        authorization.consumed_at = timestamp
        tokens = _issue_tokens(database, settings, user, authorization.client_kind)
        database.commit()
        return tokens

    @app.post("/v1/tokens/refresh")
    def refresh_access_token(
        payload: RefreshInput, database: Session = Depends(_database_session)
    ) -> dict[str, object]:
        begin_immediate(database)
        digest = token_digest(payload.refresh_token)
        refresh = database.get(RefreshToken, digest)
        timestamp = now_epoch()
        if refresh is None or refresh.expires_at <= timestamp:
            raise ApiError(401, "refresh_token_invalid", "The refresh token is invalid")
        family = database.get(TokenFamily, refresh.family_id)
        if family is None or family.revoked_at is not None or family.expires_at <= timestamp:
            raise ApiError(401, "refresh_token_invalid", "The refresh token is invalid")
        if refresh.used_at is not None:
            family.revoked_at = timestamp
            database.execute(
                update(AccessToken)
                .where(AccessToken.family_id == family.id, AccessToken.revoked_at.is_(None))
                .values(revoked_at=timestamp)
            )
            database.commit()
            raise ApiError(401, "refresh_token_reused", "The token family has been revoked")
        user = database.get(User, family.user_id)
        if user is None:
            raise ApiError(401, "refresh_token_invalid", "The refresh token is invalid")
        refresh.used_at = timestamp
        tokens = _issue_tokens(
            database,
            settings,
            user,
            family.client_kind,
            family=family,
            generation=refresh.generation + 1,
        )
        refresh.replacement_digest = token_digest(str(tokens["refresh_token"]))
        database.commit()
        return tokens

    @app.get("/v1/me")
    def current_user(user: User = Depends(_bearer_user)) -> dict[str, object]:
        return {"id": user.id, "email": user.email, "verification": user.verification}

    @app.get("/v1/me/entitlements", response_model=EntitlementV1)
    def current_entitlements(user: User = Depends(_bearer_user)) -> EntitlementV1:
        return default_free_entitlement(user.id, datetime.now(UTC))

    return app
