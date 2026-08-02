from __future__ import annotations

from typing import Any, cast

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from podcast_reader_premium.models import AccessToken, BrowserSession, RefreshToken, User


def test_account_normalization_argon2_and_generic_login_errors(
    client: TestClient, account: dict[str, object]
) -> None:
    assert account["email"] == "reader@example.com"
    assert account["verification"] == "unverified_test"
    app = cast("Any", client.app)
    with Session(app.state.engine) as database:
        user = database.scalar(select(User).where(User.email == "reader@example.com"))
        assert user is not None
        assert user.password_hash.startswith("$argon2id$")
        assert "correct horse battery" not in user.password_hash
    wrong_password = client.post(
        "/v1/browser-sessions",
        json={"email": "reader@example.com", "password": "wrong password"},
    )
    missing_account = client.post(
        "/v1/browser-sessions",
        json={"email": "missing@example.com", "password": "wrong password"},
    )
    assert wrong_password.status_code == missing_account.status_code == 401
    assert wrong_password.json()["message"] == missing_account.json()["message"]


def test_api_errors_use_the_bounded_envelope(client: TestClient) -> None:
    response = client.get("/v1/does-not-exist")
    assert response.status_code == 404
    assert set(response.json()) == {"code", "message", "request_id"}
    assert response.json()["code"] == "not_found"


def test_browser_cookie_csrf_origin_and_host_are_enforced(
    client: TestClient, account: dict[str, object]
) -> None:
    login = client.post(
        "/v1/browser-sessions",
        json={"email": account["email"], "password": "correct horse battery"},
    )
    cookie = login.headers["set-cookie"]
    assert "__Host-pr_session=" in cookie
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/" in cookie
    assert "Domain=" not in cookie
    browser_auth = {
        "Origin": "https://premium.test",
        "X-CSRF-Token": login.json()["csrf_token"],
    }
    assert client.delete("/v1/browser-sessions/current").status_code == 403
    assert (
        client.delete(
            "/v1/browser-sessions/current",
            headers={**browser_auth, "Origin": "https://attacker.test"},
        ).status_code
        == 403
    )
    assert client.delete("/v1/browser-sessions/current", headers=browser_auth).status_code == 204
    app = cast("Any", client.app)
    with Session(app.state.engine) as database:
        stored = database.scalars(select(BrowserSession)).all()
        assert stored and all(item.revoked_at is not None for item in stored)


def test_device_authorization_is_one_use_and_persists_only_digests(
    client: TestClient, browser_auth: dict[str, str]
) -> None:
    started = client.post("/v1/device-authorizations", json={"client": "android"})
    assert started.status_code == 201
    payload = started.json()
    assert payload["expires_in"] == 600
    approved = client.post(
        "/v1/device-authorizations/approve",
        json={"user_code": payload["user_code"]},
        headers=browser_auth,
    )
    assert approved.status_code == 204
    token_response = client.post(
        "/v1/device-authorizations/token", json={"device_code": payload["device_code"]}
    )
    assert token_response.status_code == 200
    tokens = token_response.json()
    assert (
        client.get(
            "/v1/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
        ).status_code
        == 200
    )
    entitlement = client.get(
        "/v1/me/entitlements",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert entitlement.status_code == 200
    assert entitlement.json()["schema_version"] == 1
    assert entitlement.json()["tier"] == "free"
    assert (
        client.post(
            "/v1/device-authorizations/token", json={"device_code": payload["device_code"]}
        ).json()["code"]
        == "expired_token"
    )
    app = cast("Any", client.app)
    with Session(app.state.engine) as database:
        access_digests = set(database.scalars(select(AccessToken.token_digest)))
        refresh_digests = set(database.scalars(select(RefreshToken.token_digest)))
        assert tokens["access_token"] not in access_digests
        assert tokens["refresh_token"] not in refresh_digests


def test_fast_poll_slows_down(client: TestClient) -> None:
    started = client.post("/v1/device-authorizations", json={"client": "desktop"}).json()
    first = client.post(
        "/v1/device-authorizations/token", json={"device_code": started["device_code"]}
    )
    second = client.post(
        "/v1/device-authorizations/token", json={"device_code": started["device_code"]}
    )
    assert first.json()["code"] == "authorization_pending"
    assert second.json()["code"] == "slow_down"


def test_refresh_rotation_and_reuse_revoke_the_family(
    client: TestClient, browser_auth: dict[str, str]
) -> None:
    started = client.post("/v1/device-authorizations", json={"client": "desktop"}).json()
    client.post(
        "/v1/device-authorizations/approve",
        json={"user_code": started["user_code"]},
        headers=browser_auth,
    )
    original = client.post(
        "/v1/device-authorizations/token", json={"device_code": started["device_code"]}
    ).json()
    rotated = client.post("/v1/tokens/refresh", json={"refresh_token": original["refresh_token"]})
    assert rotated.status_code == 200
    reuse = client.post("/v1/tokens/refresh", json={"refresh_token": original["refresh_token"]})
    assert reuse.status_code == 401
    assert reuse.json()["code"] == "refresh_token_reused"
    assert (
        client.get(
            "/v1/me", headers={"Authorization": f"Bearer {rotated.json()['access_token']}"}
        ).status_code
        == 401
    )
