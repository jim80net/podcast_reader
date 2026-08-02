from __future__ import annotations

import json
import secrets
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal, cast
from urllib.parse import urlsplit

from fastapi import APIRouter, Cookie, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker
from starlette.exceptions import HTTPException

from .config import Settings
from .db import begin_immediate
from .entitlements import (
    AD_SLOTS,
    FLAG_AUDIENCES,
    FLAG_DEFAULTS,
    EventType,
    apply_entitlement_event,
    canonical_state,
    ensure_projection,
    next_config_revision,
    record_audit,
)
from .models import (
    AccessToken,
    AdConfig,
    AuditLog,
    BrowserSession,
    EntitlementEvent,
    EntitlementProjection,
    FeatureFlag,
    HouseAd,
    TokenFamily,
    User,
)
from .security import csrf_digest, now_epoch, record_id, token_digest

SESSION_COOKIE = "__Host-pr_session"
CSRF_COOKIE = "__Host-pr_csrf"
TEMPLATES = Jinja2Templates(directory=Path(__file__).with_name("templates"))
router = APIRouter(prefix="/admin")


def _session_factory(request: Request) -> sessionmaker[Session]:
    return cast("sessionmaker[Session]", request.app.state.session_factory)


def _database_session(request: Request) -> Iterator[Session]:
    with _session_factory(request)() as database:
        yield database


def _admin_user(
    request: Request,
    database: Session = Depends(_database_session),
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> User:
    if session_token is None:
        raise HTTPException(401, "Sign in to continue")
    timestamp = now_epoch()
    browser_session = database.get(BrowserSession, token_digest(session_token))
    if (
        browser_session is None
        or browser_session.revoked_at is not None
        or browser_session.expires_at <= timestamp
    ):
        raise HTTPException(401, "Sign in to continue")
    user = database.get(User, browser_session.user_id)
    if user is None or user.status != "active" or user.role != "admin":
        raise HTTPException(403, "Administrator access is required")
    request.state.admin_session = browser_session
    return user


def _csrf_for_page(request: Request) -> str:
    raw = request.cookies.get(CSRF_COOKIE, "")
    browser_session = cast("BrowserSession", request.state.admin_session)
    if not raw or not secrets.compare_digest(csrf_digest(raw), browser_session.csrf_digest):
        raise HTTPException(403, "Sign in again to continue")
    return raw


def _require_mutation(request: Request, csrf_token: str) -> None:
    settings: Settings = request.app.state.settings
    if not secrets.compare_digest(request.headers.get("origin", ""), settings.public_origin):
        raise HTTPException(403, "Request origin was rejected")
    if not secrets.compare_digest(request.headers.get("host", "").lower(), settings.expected_host):
        raise HTTPException(403, "Request host was rejected")
    browser_session = cast("BrowserSession", request.state.admin_session)
    if not secrets.compare_digest(csrf_digest(csrf_token), browser_session.csrf_digest):
        raise HTTPException(403, "CSRF token was rejected")


def _reason(value: str) -> str:
    cleaned = value.strip()
    if len(cleaned) < 3 or len(cleaned) > 500:
        raise HTTPException(422, "Reason must contain between 3 and 500 characters")
    return cleaned


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def _active_admin_count(database: Session) -> int:
    return (
        database.scalar(
            select(func.count())
            .select_from(User)
            .where(User.role == "admin", User.status == "active")
        )
        or 0
    )


def _is_last_active_admin(database: Session, user: User) -> bool:
    return user.role == "admin" and user.status == "active" and _active_admin_count(database) <= 1


@router.get("/", response_class=HTMLResponse)
def users_page(
    request: Request,
    q: Annotated[str, Query(max_length=320)] = "",
    status: Annotated[str, Query(pattern="^(all|active|disabled)$")] = "all",
    tier: Annotated[str, Query(pattern="^(all|free|premium)$")] = "all",
    page: Annotated[int, Query(ge=1, le=10_000)] = 1,
    admin: User = Depends(_admin_user),
    database: Session = Depends(_database_session),
) -> HTMLResponse:
    query = select(User, EntitlementProjection).join(
        EntitlementProjection, EntitlementProjection.user_id == User.id
    )
    normalized = q.strip().casefold()
    if normalized:
        if normalized.endswith("*"):
            query = query.where(User.email.like(normalized[:-1] + "%"))
        else:
            query = query.where(User.email == normalized)
    if status != "all":
        query = query.where(User.status == status)
    if tier != "all":
        query = query.where(EntitlementProjection.effective_tier == tier)
    rows = database.execute(query.order_by(User.email).offset((page - 1) * 50).limit(51)).all()
    return TEMPLATES.TemplateResponse(
        request,
        "users.html",
        {
            "admin": admin,
            "csrf_token": _csrf_for_page(request),
            "rows": rows[:50],
            "has_next": len(rows) > 50,
            "page": page,
            "q": q,
            "status": status,
            "tier": tier,
        },
    )


@router.get("/users/{user_id}", response_class=HTMLResponse)
def user_detail_page(
    user_id: str,
    request: Request,
    admin: User = Depends(_admin_user),
    database: Session = Depends(_database_session),
) -> HTMLResponse:
    user = database.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    projection = ensure_projection(database, user.id)
    events = database.scalars(
        select(EntitlementEvent)
        .where(EntitlementEvent.user_id == user.id)
        .order_by(EntitlementEvent.revision.desc())
        .limit(50)
    ).all()
    audits = database.scalars(
        select(AuditLog)
        .where(AuditLog.target_kind == "user", AuditLog.target_id == user.id)
        .order_by(AuditLog.created_at.desc())
        .limit(50)
    ).all()
    browser_count = (
        database.scalar(
            select(func.count())
            .select_from(BrowserSession)
            .where(BrowserSession.user_id == user.id, BrowserSession.revoked_at.is_(None))
        )
        or 0
    )
    family_count = (
        database.scalar(
            select(func.count())
            .select_from(TokenFamily)
            .where(TokenFamily.user_id == user.id, TokenFamily.revoked_at.is_(None))
        )
        or 0
    )
    return TEMPLATES.TemplateResponse(
        request,
        "user_detail.html",
        {
            "admin": admin,
            "csrf_token": _csrf_for_page(request),
            "user": user,
            "projection": projection,
            "events": events,
            "audits": audits,
            "browser_count": browser_count,
            "family_count": family_count,
        },
    )


@router.post("/users/{user_id}/override")
def set_user_override(
    user_id: str,
    request: Request,
    action: Annotated[str, Form(pattern="^(premium|free|clear)$")],
    reason: Annotated[str, Form(min_length=3, max_length=500)],
    csrf_token: Annotated[str, Form(min_length=20, max_length=256)],
    admin: User = Depends(_admin_user),
    database: Session = Depends(_database_session),
) -> RedirectResponse:
    _require_mutation(request, csrf_token)
    begin_immediate(database)
    user = database.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    projection = ensure_projection(database, user.id)
    before: dict[str, object] = {
        "provider_tier": projection.provider_tier,
        "admin_override": projection.admin_override,
        "effective_tier": projection.effective_tier,
        "revision": projection.revision,
    }
    event_type: EventType = "override_clear" if action == "clear" else "override_set"
    tier = None if action == "clear" else cast("Literal['free', 'premium']", action)
    apply_entitlement_event(
        database,
        user_id=user.id,
        event_type=event_type,
        tier=tier,
        actor_user_id=admin.id,
        reason=_reason(reason),
    )
    after: dict[str, object] = {
        "provider_tier": projection.provider_tier,
        "admin_override": projection.admin_override,
        "effective_tier": projection.effective_tier,
        "revision": projection.revision,
    }
    record_audit(
        database,
        actor_user_id=admin.id,
        action=f"entitlement.{event_type}",
        target_kind="user",
        target_id=user.id,
        before=before,
        after=after,
        reason=_reason(reason),
        request_id=cast("str", request.state.request_id),
    )
    database.commit()
    return _redirect(f"/admin/users/{user.id}")


@router.post("/users/{user_id}/status")
def set_user_status(
    user_id: str,
    request: Request,
    status: Annotated[str, Form(pattern="^(active|disabled)$")],
    reason: Annotated[str, Form(min_length=3, max_length=500)],
    csrf_token: Annotated[str, Form(min_length=20, max_length=256)],
    admin: User = Depends(_admin_user),
    database: Session = Depends(_database_session),
) -> RedirectResponse:
    _require_mutation(request, csrf_token)
    begin_immediate(database)
    user = database.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    if status == "disabled" and _is_last_active_admin(database, user):
        raise HTTPException(409, "The last active administrator cannot be disabled")
    before: dict[str, object] = {"status": user.status}
    user.status = status
    timestamp = now_epoch()
    if status == "disabled":
        database.execute(
            update(BrowserSession)
            .where(BrowserSession.user_id == user.id, BrowserSession.revoked_at.is_(None))
            .values(revoked_at=timestamp)
        )
        database.execute(
            update(TokenFamily)
            .where(TokenFamily.user_id == user.id, TokenFamily.revoked_at.is_(None))
            .values(revoked_at=timestamp)
        )
        database.execute(
            update(AccessToken)
            .where(AccessToken.user_id == user.id, AccessToken.revoked_at.is_(None))
            .values(revoked_at=timestamp)
        )
    record_audit(
        database,
        actor_user_id=admin.id,
        action=f"user.{status}",
        target_kind="user",
        target_id=user.id,
        before=before,
        after={"status": user.status},
        reason=_reason(reason),
        request_id=cast("str", request.state.request_id),
    )
    database.commit()
    return _redirect(f"/admin/users/{user.id}")


@router.post("/users/{user_id}/sessions/revoke")
def revoke_user_sessions(
    user_id: str,
    request: Request,
    reason: Annotated[str, Form(min_length=3, max_length=500)],
    csrf_token: Annotated[str, Form(min_length=20, max_length=256)],
    admin: User = Depends(_admin_user),
    database: Session = Depends(_database_session),
) -> RedirectResponse:
    _require_mutation(request, csrf_token)
    begin_immediate(database)
    user = database.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    if _is_last_active_admin(database, user):
        raise HTTPException(409, "The last active administrator's sessions cannot be revoked")
    timestamp = now_epoch()
    browser_count = (
        database.scalar(
            select(func.count())
            .select_from(BrowserSession)
            .where(BrowserSession.user_id == user.id, BrowserSession.revoked_at.is_(None))
        )
        or 0
    )
    family_count = (
        database.scalar(
            select(func.count())
            .select_from(TokenFamily)
            .where(TokenFamily.user_id == user.id, TokenFamily.revoked_at.is_(None))
        )
        or 0
    )
    database.execute(
        update(BrowserSession)
        .where(BrowserSession.user_id == user.id, BrowserSession.revoked_at.is_(None))
        .values(revoked_at=timestamp)
    )
    database.execute(
        update(TokenFamily)
        .where(TokenFamily.user_id == user.id, TokenFamily.revoked_at.is_(None))
        .values(revoked_at=timestamp)
    )
    database.execute(
        update(AccessToken)
        .where(AccessToken.user_id == user.id, AccessToken.revoked_at.is_(None))
        .values(revoked_at=timestamp)
    )
    state = {"browser_sessions_revoked": browser_count, "token_families_revoked": family_count}
    record_audit(
        database,
        actor_user_id=admin.id,
        action="user.sessions_revoke",
        target_kind="user",
        target_id=user.id,
        before={},
        after=state,
        reason=_reason(reason),
        request_id=cast("str", request.state.request_id),
    )
    database.commit()
    return _redirect(f"/admin/users/{user.id}")


@router.get("/flags", response_class=HTMLResponse)
def flags_page(
    request: Request,
    admin: User = Depends(_admin_user),
    database: Session = Depends(_database_session),
) -> HTMLResponse:
    flags = database.scalars(select(FeatureFlag).order_by(FeatureFlag.key)).all()
    previews: dict[str, dict[str, str]] = {}
    for flag in flags:
        if flag.key == "ad_system":
            previews[flag.key] = {
                "free": "house eligible" if flag.audience in {"all", "free"} else "none",
                "premium": "none",
            }
        else:
            previews[flag.key] = {
                "free": "disabled",
                "premium": "enabled" if flag.audience in {"all", "premium"} else "disabled",
            }
    return TEMPLATES.TemplateResponse(
        request,
        "flags.html",
        {
            "admin": admin,
            "csrf_token": _csrf_for_page(request),
            "flags": flags,
            "previews": previews,
        },
    )


@router.post("/flags/{key}")
def update_flag(
    key: str,
    request: Request,
    audience: Annotated[str, Form(max_length=16)],
    config_json: Annotated[str, Form(max_length=2048)],
    reason: Annotated[str, Form(min_length=3, max_length=500)],
    csrf_token: Annotated[str, Form(min_length=20, max_length=256)],
    admin: User = Depends(_admin_user),
    database: Session = Depends(_database_session),
) -> RedirectResponse:
    _require_mutation(request, csrf_token)
    if key not in FLAG_DEFAULTS or audience not in FLAG_AUDIENCES:
        raise HTTPException(422, "Unknown flag or audience")
    try:
        config = json.loads(config_json)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "Flag config must be valid JSON") from exc
    if config != {}:
        raise HTTPException(422, "This flag does not accept configuration")
    begin_immediate(database)
    flag = database.get(FeatureFlag, key)
    if flag is None:
        raise HTTPException(500, "Registered feature flag is missing")
    before = {"audience": flag.audience, "config": json.loads(flag.config_json)}
    flag.audience = audience
    flag.config_json = canonical_state(config)
    flag.revision = next_config_revision(database)
    flag.actor_user_id = admin.id
    flag.updated_at = now_epoch()
    after = {"audience": flag.audience, "config": config, "revision": flag.revision}
    record_audit(
        database,
        actor_user_id=admin.id,
        action="feature_flag.update",
        target_kind="feature_flag",
        target_id=key,
        before=before,
        after=after,
        reason=_reason(reason),
        request_id=cast("str", request.state.request_id),
    )
    database.commit()
    return _redirect("/admin/flags")


@router.get("/ads", response_class=HTMLResponse)
def ads_page(
    request: Request,
    admin: User = Depends(_admin_user),
    database: Session = Depends(_database_session),
) -> HTMLResponse:
    config = database.get(AdConfig, 1)
    ads = database.scalars(select(HouseAd).order_by(HouseAd.created_at.desc())).all()
    return TEMPLATES.TemplateResponse(
        request,
        "ads.html",
        {
            "admin": admin,
            "csrf_token": _csrf_for_page(request),
            "config": config,
            "slots": sorted(AD_SLOTS),
            "enabled_slots": set(json.loads(config.enabled_slots_json)) if config else set(),
            "ads": ads,
            "datetime_local": _datetime_local,
        },
    )


@router.post("/ads/config")
def update_ad_config(
    request: Request,
    enabled: Annotated[str | None, Form()] = None,
    slots: Annotated[list[str] | None, Form()] = None,
    reason: Annotated[str, Form(min_length=3, max_length=500)] = "",
    csrf_token: Annotated[str, Form(min_length=20, max_length=256)] = "",
    admin: User = Depends(_admin_user),
    database: Session = Depends(_database_session),
) -> RedirectResponse:
    _require_mutation(request, csrf_token)
    selected = sorted(set(slots or []))
    if set(selected) - AD_SLOTS:
        raise HTTPException(422, "Unknown ad slot")
    begin_immediate(database)
    config = database.get(AdConfig, 1)
    if config is None or config.source != "house":
        raise HTTPException(500, "House ad configuration is missing")
    before = {
        "enabled": config.enabled,
        "source": config.source,
        "slots": json.loads(config.enabled_slots_json),
    }
    config.enabled = enabled == "on"
    config.enabled_slots_json = canonical_state(selected)
    config.revision = next_config_revision(database)
    config.actor_user_id = admin.id
    config.updated_at = now_epoch()
    after = {
        "enabled": config.enabled,
        "source": config.source,
        "slots": selected,
        "revision": config.revision,
    }
    record_audit(
        database,
        actor_user_id=admin.id,
        action="ad_config.update",
        target_kind="ad_config",
        target_id="1",
        before=before,
        after=after,
        reason=_reason(reason),
        request_id=cast("str", request.state.request_id),
    )
    database.commit()
    return _redirect("/admin/ads")


def _schedule_epoch(value: str) -> int | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(422, "Ad schedule is invalid") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp())


def _datetime_local(value: int | None) -> str:
    if value is None:
        return ""
    return datetime.fromtimestamp(value, UTC).strftime("%Y-%m-%dT%H:%M")


def _validated_ad(
    title: str,
    body: str,
    cta_url: str,
    status: str,
    starts_at: str,
    ends_at: str,
) -> dict[str, object]:
    clean_title = title.strip()
    clean_body = body.strip()
    if not clean_title or len(clean_title) > 120 or not clean_body or len(clean_body) > 500:
        raise HTTPException(422, "Ad title or body is invalid")
    parsed = urlsplit(cta_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise HTTPException(422, "Ad CTA must be an HTTPS URL without credentials")
    if status not in {"draft", "active"}:
        raise HTTPException(422, "Ad status is invalid")
    starts_epoch = _schedule_epoch(starts_at)
    ends_epoch = _schedule_epoch(ends_at)
    if starts_epoch is not None and ends_epoch is not None and ends_epoch <= starts_epoch:
        raise HTTPException(422, "Ad end must be later than its start")
    return {
        "title": clean_title,
        "body": clean_body,
        "cta_url": cta_url,
        "status": status,
        "starts_at": starts_epoch,
        "ends_at": ends_epoch,
    }


@router.post("/ads/house")
def create_house_ad(
    request: Request,
    title: Annotated[str, Form(max_length=120)],
    body: Annotated[str, Form(max_length=500)],
    cta_url: Annotated[str, Form(max_length=2048)],
    status: Annotated[str, Form(max_length=16)],
    reason: Annotated[str, Form(min_length=3, max_length=500)],
    csrf_token: Annotated[str, Form(min_length=20, max_length=256)],
    starts_at: Annotated[str, Form(max_length=32)] = "",
    ends_at: Annotated[str, Form(max_length=32)] = "",
    admin: User = Depends(_admin_user),
    database: Session = Depends(_database_session),
) -> RedirectResponse:
    _require_mutation(request, csrf_token)
    values = _validated_ad(title, body, cta_url, status, starts_at, ends_at)
    begin_immediate(database)
    timestamp = now_epoch()
    ad = HouseAd(
        id=record_id("ad"),
        **values,
        revision=1,
        created_at=timestamp,
        updated_at=timestamp,
    )
    database.add(ad)
    record_audit(
        database,
        actor_user_id=admin.id,
        action="house_ad.create",
        target_kind="house_ad",
        target_id=ad.id,
        before={},
        after={**values, "revision": 1},
        reason=_reason(reason),
        request_id=cast("str", request.state.request_id),
    )
    database.commit()
    return _redirect("/admin/ads")


@router.post("/ads/house/{ad_id}")
def update_house_ad(
    ad_id: str,
    request: Request,
    title: Annotated[str, Form(max_length=120)],
    body: Annotated[str, Form(max_length=500)],
    cta_url: Annotated[str, Form(max_length=2048)],
    status: Annotated[str, Form(max_length=16)],
    reason: Annotated[str, Form(min_length=3, max_length=500)],
    csrf_token: Annotated[str, Form(min_length=20, max_length=256)],
    starts_at: Annotated[str, Form(max_length=32)] = "",
    ends_at: Annotated[str, Form(max_length=32)] = "",
    admin: User = Depends(_admin_user),
    database: Session = Depends(_database_session),
) -> RedirectResponse:
    _require_mutation(request, csrf_token)
    values = _validated_ad(title, body, cta_url, status, starts_at, ends_at)
    begin_immediate(database)
    ad = database.get(HouseAd, ad_id)
    if ad is None:
        raise HTTPException(404, "House ad not found")
    before = {
        "title": ad.title,
        "body": ad.body,
        "cta_url": ad.cta_url,
        "status": ad.status,
        "starts_at": ad.starts_at,
        "ends_at": ad.ends_at,
        "revision": ad.revision,
    }
    for key, value in values.items():
        setattr(ad, key, value)
    ad.revision += 1
    ad.updated_at = now_epoch()
    record_audit(
        database,
        actor_user_id=admin.id,
        action="house_ad.update",
        target_kind="house_ad",
        target_id=ad.id,
        before=before,
        after={**values, "revision": ad.revision},
        reason=_reason(reason),
        request_id=cast("str", request.state.request_id),
    )
    database.commit()
    return _redirect("/admin/ads")


@router.post("/ads/house/{ad_id}/delete")
def delete_house_ad(
    ad_id: str,
    request: Request,
    reason: Annotated[str, Form(min_length=3, max_length=500)],
    csrf_token: Annotated[str, Form(min_length=20, max_length=256)],
    admin: User = Depends(_admin_user),
    database: Session = Depends(_database_session),
) -> RedirectResponse:
    _require_mutation(request, csrf_token)
    begin_immediate(database)
    ad = database.get(HouseAd, ad_id)
    if ad is None:
        raise HTTPException(404, "House ad not found")
    before = {
        "title": ad.title,
        "body": ad.body,
        "cta_url": ad.cta_url,
        "status": ad.status,
        "starts_at": ad.starts_at,
        "ends_at": ad.ends_at,
        "revision": ad.revision,
    }
    database.delete(ad)
    record_audit(
        database,
        actor_user_id=admin.id,
        action="house_ad.delete",
        target_kind="house_ad",
        target_id=ad.id,
        before=before,
        after={"deleted": True},
        reason=_reason(reason),
        request_id=cast("str", request.state.request_id),
    )
    database.commit()
    return _redirect("/admin/ads")


@router.get("/audit", response_class=HTMLResponse)
def audit_page(
    request: Request,
    action: Annotated[str, Query(max_length=64)] = "",
    actor: Annotated[str, Query(max_length=64)] = "",
    target: Annotated[str, Query(max_length=64)] = "",
    since: Annotated[int | None, Query(ge=0)] = None,
    until: Annotated[int | None, Query(ge=0)] = None,
    page: Annotated[int, Query(ge=1, le=10_000)] = 1,
    admin: User = Depends(_admin_user),
    database: Session = Depends(_database_session),
) -> HTMLResponse:
    query = select(AuditLog)
    if action:
        query = query.where(AuditLog.action == action)
    if actor:
        query = query.where(AuditLog.actor_user_id == actor)
    if target:
        query = query.where(AuditLog.target_id == target)
    if since is not None:
        query = query.where(AuditLog.created_at >= since)
    if until is not None:
        query = query.where(AuditLog.created_at <= until)
    rows = database.scalars(
        query.order_by(AuditLog.created_at.desc()).offset((page - 1) * 100).limit(101)
    ).all()
    return TEMPLATES.TemplateResponse(
        request,
        "audit.html",
        {
            "admin": admin,
            "csrf_token": _csrf_for_page(request),
            "rows": rows[:100],
            "has_next": len(rows) > 100,
            "page": page,
            "action_filter": action,
            "actor_filter": actor,
            "target_filter": target,
            "since_filter": since if since is not None else "",
            "until_filter": until if until is not None else "",
        },
    )
