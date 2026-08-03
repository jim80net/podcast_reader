from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, cast
from urllib.parse import urlencode

from fastapi import Cookie, Depends, FastAPI, Form, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session, sessionmaker
from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles

from .admin import CSRF_COOKIE, TEMPLATES
from .admin import router as admin_router
from .ads import inventory_for_slot
from .billing import (
    BillingAdapter,
    BillingConfigurationError,
    BillingProviderError,
    FakeBillingAdapter,
    StripeBillingAdapter,
    WebhookVerificationError,
)
from .config import Settings
from .contracts import (
    EMAIL_REQUEST_MAX_BYTES,
    AdInventoryV1,
    AdSlot,
    DeviceAuthorizationStartV1,
    EmailDeliveryRequestV1,
    EmailDeliveryV1,
    EntitlementV1,
    TokenResponseV1,
    TokenRevokeRequestV1,
)
from .db import begin_immediate, create_database, require_current_schema
from .email_delivery import DevMaildirSink, EmailDeliveryError, EmailRelay
from .entitlements import (
    AD_SLOTS,
    ensure_projection,
    entitlement_etag,
    evaluate_entitlements,
    require_entitlement_configuration,
)
from .models import (
    AccessToken,
    BrowserSession,
    DeviceAuthorization,
    RefreshToken,
    TokenFamily,
    User,
)
from .payments import (
    CheckoutRedirect,
    PaymentWorker,
    create_checkout_attempt,
    ingest_verified_webhook,
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
LOGGER = logging.getLogger(__name__)


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
    _validate_browser_mutation(request, csrf_token or "")
    return user


def _validate_browser_mutation(request: Request, csrf_token: str) -> None:
    settings: Settings = request.app.state.settings
    origin = request.headers.get("origin")
    host = request.headers.get("host", "").lower()
    if not secrets.compare_digest(origin or "", settings.public_origin):
        raise ApiError(403, "origin_rejected", "Request origin was rejected")
    if not secrets.compare_digest(host, settings.expected_host):
        raise ApiError(403, "host_rejected", "Request host was rejected")
    browser_session: BrowserSession = request.state.browser_session
    supplied = csrf_digest(csrf_token)
    if not secrets.compare_digest(supplied, browser_session.csrf_digest):
        raise ApiError(403, "csrf_rejected", "CSRF token was rejected")


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


def _revoke_token_family(database: Session, family: TokenFamily, timestamp: int) -> None:
    family.revoked_at = timestamp
    database.execute(
        update(AccessToken)
        .where(AccessToken.family_id == family.id, AccessToken.revoked_at.is_(None))
        .values(revoked_at=timestamp)
    )


def create_app(
    settings: Settings,
    *,
    engine: Engine | None = None,
    billing: BillingAdapter | None = None,
) -> FastAPI:
    database_engine = engine or create_database(settings)
    limiter = RateLimiter()
    session_factory = sessionmaker(database_engine, expire_on_commit=False)
    billing_adapter = billing
    if billing_adapter is None:
        if settings.environment == "test":
            billing_adapter = FakeBillingAdapter(
                price_id=settings.expected_stripe_price_id,
                currency=settings.premium_currency,
                unit_amount=settings.premium_unit_amount,
            )
        else:
            billing_adapter = StripeBillingAdapter(settings)
    payment_worker = PaymentWorker(session_factory, billing_adapter, settings)
    if settings.email_maildir_path is None or settings.email_delivery_hmac_key is None:
        raise RuntimeError("DEV email relay configuration is required")
    email_relay = EmailRelay(
        DevMaildirSink(settings.email_maildir_path), settings.email_delivery_hmac_key
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        stop = asyncio.Event()
        worker_task: asyncio.Task[None] | None = None

        async def payment_loop() -> None:
            retry_delay = 1
            while True:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=retry_delay)
                    return
                except TimeoutError:
                    try:
                        await asyncio.to_thread(payment_worker.run_once)
                        retry_delay = 1
                    except Exception as exc:
                        LOGGER.exception(
                            "payment_worker_iteration_failed event_id=%s cause=%s",
                            getattr(exc, "event_id", "unknown"),
                            type(exc.__cause__ or exc).__name__,
                        )
                        retry_delay = min(retry_delay * 2, 60)

        try:
            require_current_schema(database_engine)
            with Session(database_engine) as database:
                require_entitlement_configuration(database)
            product = await asyncio.to_thread(billing_adapter.preflight)
            if (
                product.livemode
                or product.price_id != settings.expected_stripe_price_id
                or product.currency != settings.premium_currency
                or product.unit_amount != settings.premium_unit_amount
            ):
                raise BillingConfigurationError(
                    "billing preflight does not match configured product"
                )
            worker_task = (
                asyncio.create_task(payment_loop()) if settings.environment != "test" else None
            )
            yield
        finally:
            stop.set()
            try:
                if worker_task is not None:
                    await worker_task
            finally:
                database_engine.dispose()

    app = FastAPI(title="Podcast Reader premium dev service", version="1", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = database_engine
    app.state.session_factory = session_factory
    app.state.billing = billing_adapter
    app.state.payment_worker = payment_worker
    app.state.email_relay = email_relay
    app.mount(
        "/premium-static",
        StaticFiles(directory=Path(__file__).with_name("static")),
        name="premium-static",
    )
    app.include_router(admin_router)

    def authenticate_credentials(
        email_value: str,
        password: str,
        request: Request,
        database: Session,
    ) -> User:
        try:
            email = normalize_email(email_value)
        except ValueError:
            email = "invalid"
        source = request.client.host if request.client else "unknown"
        limiter_key = hashlib.sha256(f"{source}\0{email}".encode()).hexdigest()
        if not limiter.allow(limiter_key):
            raise ApiError(429, "rate_limited", "Too many sign-in attempts")
        user = database.scalar(select(User).where(User.email == email))
        encoded = user.password_hash if user is not None else DUMMY_PASSWORD_HASH
        password_valid = verify_password(encoded, password)
        if user is None or user.status != "active" or not password_valid:
            raise ApiError(401, "login_failed", GENERIC_LOGIN_MESSAGE)
        return user

    def start_browser_session(user: User, database: Session) -> tuple[str, str]:
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
        return session_raw, csrf_raw

    def set_browser_cookies(response: Response, session_raw: str, csrf_raw: str) -> None:
        response.set_cookie(
            SESSION_COOKIE,
            session_raw,
            max_age=settings.session_ttl_seconds,
            secure=True,
            httponly=True,
            samesite="lax",
            path="/",
        )
        response.set_cookie(
            CSRF_COOKIE,
            csrf_raw,
            max_age=settings.session_ttl_seconds,
            secure=True,
            httponly=False,
            samesite="lax",
            path="/",
        )

    def validate_login_origin(request: Request) -> None:
        if not secrets.compare_digest(request.headers.get("origin", ""), settings.public_origin):
            raise ApiError(403, "origin_rejected", "Request origin was rejected")
        if not secrets.compare_digest(
            request.headers.get("host", "").lower(), settings.expected_host
        ):
            raise ApiError(403, "host_rejected", "Request host was rejected")

    def approve_code(database: Session, user: User, visible_code: str) -> None:
        try:
            digest = user_code_digest(visible_code, settings.user_code_pepper)
        except ValueError as exc:
            raise ApiError(422, "invalid_user_code", "User code is invalid") from exc
        begin_immediate(database)
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
        if request.url.path.startswith(("/admin", "/device", "/account")):
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self'; "
                "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
            )
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
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
    async def http_error(request: Request, error: HTTPException) -> Response:
        if error.status_code == 401 and request.url.path.startswith("/admin"):
            return RedirectResponse("/admin/login", status_code=303)
        code = "not_found" if error.status_code == 404 else "http_error"
        message = "Route not found" if error.status_code == 404 else "Request was rejected"
        return _error(request, ApiError(error.status_code, code, message))

    @app.get("/healthz")
    def health() -> dict[str, object]:
        return {"status": "ok", "schema": 3, "build_sha": settings.build_sha}

    @app.get("/admin/login", response_class=HTMLResponse)
    def admin_login_page(request: Request) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(
            request,
            "login.html",
            {"title": "Administrator sign in", "action": "/admin/login", "user_code": ""},
        )

    @app.post("/admin/login")
    def admin_login(
        request: Request,
        email: Annotated[str, Form(max_length=320)],
        password: Annotated[str, Form(min_length=1, max_length=1024)],
        database: Session = Depends(_database_session),
    ) -> RedirectResponse:
        validate_login_origin(request)
        user = authenticate_credentials(email, password, request, database)
        if user.role != "admin":
            raise ApiError(401, "login_failed", GENERIC_LOGIN_MESSAGE)
        session_raw, csrf_raw = start_browser_session(user, database)
        response = RedirectResponse("/admin/", status_code=303)
        set_browser_cookies(response, session_raw, csrf_raw)
        return response

    @app.get("/account", response_class=HTMLResponse)
    def account_page(
        request: Request,
        user: User = Depends(_browser_user),
        database: Session = Depends(_database_session),
    ) -> HTMLResponse:
        entitlement = evaluate_entitlements(database, user.id)
        return TEMPLATES.TemplateResponse(
            request,
            "account.html",
            {
                "user": user,
                "entitlement": entitlement,
                "csrf_token": request.cookies.get(CSRF_COOKIE, ""),
                "notice": None,
            },
        )

    @app.get("/account/billing/success", response_class=HTMLResponse)
    def billing_success_page(
        request: Request,
        user: User = Depends(_browser_user),
        database: Session = Depends(_database_session),
    ) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(
            request,
            "account.html",
            {
                "user": user,
                "entitlement": evaluate_entitlements(database, user.id),
                "csrf_token": request.cookies.get(CSRF_COOKIE, ""),
                "notice": "Checkout returned. Your account reflects verified payment status.",
            },
        )

    @app.get("/account/billing/cancel", response_class=HTMLResponse)
    def billing_cancel_page(
        request: Request,
        user: User = Depends(_browser_user),
        database: Session = Depends(_database_session),
    ) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(
            request,
            "account.html",
            {
                "user": user,
                "entitlement": evaluate_entitlements(database, user.id),
                "csrf_token": request.cookies.get(CSRF_COOKIE, ""),
                "notice": "Checkout closed. Your account reflects verified payment status.",
            },
        )

    def start_checkout(database: Session, user: User) -> CheckoutRedirect:
        try:
            return create_checkout_attempt(database, billing_adapter, settings, user)
        except BillingProviderError as exc:
            raise ApiError(503, "billing_unavailable", "Test Checkout is unavailable") from exc

    @app.post("/account/billing/checkout")
    def account_checkout(
        request: Request,
        csrf_token: Annotated[str, Form(min_length=20, max_length=256)],
        user: User = Depends(_browser_user),
        database: Session = Depends(_database_session),
    ) -> RedirectResponse:
        _validate_browser_mutation(request, csrf_token)
        checkout = start_checkout(database, user)
        return RedirectResponse(checkout.url, status_code=303)

    @app.post("/v1/billing/checkout-sessions", status_code=201)
    def create_checkout_session(
        user: User = Depends(_require_browser_mutation),
        database: Session = Depends(_database_session),
    ) -> dict[str, str]:
        checkout = start_checkout(database, user)
        return {
            "attempt_id": checkout.attempt_id,
            "checkout_url": checkout.url,
        }

    @app.post("/v1/webhooks/stripe", status_code=204)
    async def stripe_webhook(
        request: Request,
        database: Session = Depends(_database_session),
    ) -> Response:
        signature = request.headers.get("Stripe-Signature", "")
        if not signature or len(signature) > 512:
            raise ApiError(400, "webhook_invalid", "Webhook signature is invalid")
        payload = bytearray()
        async for chunk in request.stream():
            payload.extend(chunk)
            if len(payload) > 64 * 1024:
                raise ApiError(413, "request_too_large", "Webhook body is too large")
        raw_payload = bytes(payload)
        try:
            verified = billing_adapter.verify_webhook(raw_payload, signature)
            ingest_verified_webhook(database, verified, raw_payload)
        except WebhookVerificationError as exc:
            raise ApiError(400, "webhook_invalid", "Webhook signature is invalid") from exc
        except ValueError as exc:
            raise ApiError(
                400, "webhook_conflict", "Webhook event conflicts with prior data"
            ) from exc
        return Response(status_code=204)

    @app.get("/device", response_class=HTMLResponse)
    def device_page(
        request: Request,
        user_code_value: Annotated[str, Query(alias="user_code", max_length=16)] = "",
        database: Session = Depends(_database_session),
        session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> HTMLResponse:
        user: User | None = None
        approved = False
        csrf_raw = request.cookies.get(CSRF_COOKIE, "")
        if session_token:
            browser_session = database.get(BrowserSession, token_digest(session_token))
            timestamp = now_epoch()
            if (
                browser_session is not None
                and browser_session.revoked_at is None
                and browser_session.expires_at > timestamp
                and secrets.compare_digest(csrf_digest(csrf_raw), browser_session.csrf_digest)
            ):
                candidate = database.get(User, browser_session.user_id)
                if candidate is not None and candidate.status == "active":
                    user = candidate
                    request.state.browser_session = browser_session
        if user is not None and user_code_value:
            try:
                digest = user_code_digest(user_code_value, settings.user_code_pepper)
            except ValueError:
                pass
            else:
                authorization = database.scalar(
                    select(DeviceAuthorization).where(
                        DeviceAuthorization.user_code_digest == digest,
                        DeviceAuthorization.approving_user_id == user.id,
                        DeviceAuthorization.state.in_(("approved", "consumed")),
                    )
                )
                approved = authorization is not None and (
                    authorization.state == "consumed"
                    or (
                        authorization.state == "approved" and authorization.expires_at > now_epoch()
                    )
                )
        return TEMPLATES.TemplateResponse(
            request,
            "device.html",
            {
                "user": user,
                "user_code": user_code_value,
                "csrf_token": csrf_raw,
                "approved": approved,
            },
        )

    @app.post("/device/login")
    def device_login(
        request: Request,
        email: Annotated[str, Form(max_length=320)],
        password: Annotated[str, Form(min_length=1, max_length=1024)],
        user_code_value: Annotated[str, Form(alias="user_code", max_length=16)] = "",
        database: Session = Depends(_database_session),
    ) -> RedirectResponse:
        validate_login_origin(request)
        user = authenticate_credentials(email, password, request, database)
        session_raw, csrf_raw = start_browser_session(user, database)
        response = RedirectResponse(
            "/device?" + urlencode({"user_code": user_code_value}),
            status_code=303,
        )
        set_browser_cookies(response, session_raw, csrf_raw)
        return response

    @app.post("/device/approve")
    def device_approve_page(
        request: Request,
        user_code_value: Annotated[str, Form(alias="user_code", min_length=8, max_length=16)],
        csrf_token: Annotated[str, Form(min_length=20, max_length=256)],
        user: User = Depends(_browser_user),
        database: Session = Depends(_database_session),
    ) -> RedirectResponse:
        _validate_browser_mutation(request, csrf_token)
        approve_code(database, user, user_code_value)
        return RedirectResponse(
            "/device?" + urlencode({"user_code": user_code_value}), status_code=303
        )

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
        database.flush()
        ensure_projection(database, user.id, timestamp=timestamp)
        database.commit()
        return {"id": user.id, "email": user.email, "verification": user.verification}

    @app.post("/v1/browser-sessions", status_code=201)
    def create_browser_session(
        payload: AccountInput,
        request: Request,
        response: Response,
        database: Session = Depends(_database_session),
    ) -> dict[str, object]:
        user = authenticate_credentials(payload.email, payload.password, request, database)
        session_raw, csrf_raw = start_browser_session(user, database)
        set_browser_cookies(response, session_raw, csrf_raw)
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
        response.delete_cookie(CSRF_COOKIE, secure=True, httponly=False, samesite="lax", path="/")

    @app.post(
        "/v1/device-authorizations",
        status_code=201,
        response_model=DeviceAuthorizationStartV1,
    )
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
        approve_code(database, user, payload.user_code)

    @app.post("/v1/device-authorizations/token", response_model=TokenResponseV1)
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
        if authorization.state == "denied":
            database.commit()
            raise ApiError(400, "access_denied", "Device authorization was denied")
        if authorization.state != "approved" or authorization.approving_user_id is None:
            database.commit()
            raise ApiError(400, "authorization_pending", "Authorization is still pending")
        user = database.get(User, authorization.approving_user_id)
        if user is None or user.status != "active":
            authorization.state = "denied"
            authorization.consumed_at = timestamp
            database.commit()
            raise ApiError(400, "access_denied", "Device authorization was denied")
        authorization.state = "consumed"
        authorization.consumed_at = timestamp
        tokens = _issue_tokens(database, settings, user, authorization.client_kind)
        database.commit()
        return tokens

    @app.post("/v1/tokens/refresh", response_model=TokenResponseV1)
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
            _revoke_token_family(database, family, timestamp)
            database.commit()
            raise ApiError(401, "refresh_token_reused", "The token family has been revoked")
        user = database.get(User, family.user_id)
        if user is None or user.status != "active":
            _revoke_token_family(database, family, timestamp)
            database.commit()
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

    @app.post("/v1/tokens/revoke", status_code=204)
    def revoke_token_family(
        payload: TokenRevokeRequestV1,
        database: Session = Depends(_database_session),
    ) -> None:
        begin_immediate(database)
        refresh = database.get(RefreshToken, token_digest(payload.refresh_token))
        if refresh is not None:
            family = database.get(TokenFamily, refresh.family_id)
            if family is not None and family.revoked_at is None:
                _revoke_token_family(database, family, now_epoch())
        database.commit()

    @app.get("/v1/me")
    def current_user(user: User = Depends(_bearer_user)) -> dict[str, object]:
        return {"id": user.id, "email": user.email, "verification": user.verification}

    @app.get("/v1/me/entitlements", response_model=EntitlementV1)
    def current_entitlements(
        response: Response,
        user: User = Depends(_bearer_user),
        database: Session = Depends(_database_session),
    ) -> EntitlementV1:
        value = evaluate_entitlements(database, user.id, at=datetime.now(UTC))
        response.headers["ETag"] = entitlement_etag(value)
        return value

    @app.post("/v1/email-deliveries", response_model=EmailDeliveryV1)
    async def create_email_delivery(
        request: Request,
        user: User = Depends(_bearer_user),
        database: Session = Depends(_database_session),
    ) -> EmailDeliveryV1:
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > EMAIL_REQUEST_MAX_BYTES:
                raise ApiError(413, "delivery_too_large", "Transcript email content is too large")
        try:
            payload = EmailDeliveryRequestV1.model_validate_json(bytes(body))
        except ValidationError as exc:
            too_large = any(
                error["loc"] == ("transcript_text",) and "delivery bound" in str(error["msg"])
                for error in exc.errors()
            )
            if too_large:
                raise ApiError(
                    413, "delivery_too_large", "Transcript email content is too large"
                ) from exc
            raise ApiError(422, "invalid_request", "Request data is invalid") from exc
        try:
            return email_relay.deliver(database, user.id, payload)
        except EmailDeliveryError as exc:
            raise ApiError(exc.status, exc.code, exc.message) from exc

    @app.get(
        "/v1/ads/inventory/{slot}",
        response_model=AdInventoryV1,
        responses={204: {"description": "No eligible house inventory"}},
    )
    def current_ad_inventory(
        slot: str,
        user: User = Depends(_bearer_user),
        database: Session = Depends(_database_session),
    ) -> AdInventoryV1 | Response:
        if slot not in AD_SLOTS:
            raise ApiError(404, "invalid_slot", "Ad slot was not found")
        value = inventory_for_slot(database, user.id, cast("AdSlot", slot))
        if value is None:
            return Response(status_code=204)
        return value

    return app
