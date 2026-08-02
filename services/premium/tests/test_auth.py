from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event, Lock
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

import podcast_reader_premium.app as app_module
import podcast_reader_premium.db as db_module
from podcast_reader_premium.models import (
    AccessToken,
    BrowserSession,
    RefreshToken,
    TokenFamily,
    User,
)


def _approved_device(
    client: TestClient, browser_auth: dict[str, str], client_kind: str = "desktop"
) -> dict[str, object]:
    started = client.post("/v1/device-authorizations", json={"client": client_kind}).json()
    response = client.post(
        "/v1/device-authorizations/approve",
        json={"user_code": started["user_code"]},
        headers=browser_auth,
    )
    assert response.status_code == 204
    return cast("dict[str, object]", started)


def _gate_first_immediate_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Event, Event, Event]:
    original = db_module.begin_immediate
    first_acquired = Event()
    release_first = Event()
    second_entered = Event()
    calls = 0
    calls_lock = Lock()

    def controlled_begin(database: Session) -> None:
        nonlocal calls
        with calls_lock:
            calls += 1
            call_number = calls
        if call_number == 1:
            original(database)
            first_acquired.set()
            assert release_first.wait(timeout=5)
        else:
            second_entered.set()
            original(database)

    monkeypatch.setattr(app_module, "begin_immediate", controlled_begin)
    return first_acquired, release_first, second_entered


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


def test_concurrent_device_exchange_mints_exactly_one_family(
    client: TestClient,
    browser_auth: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = _approved_device(client, browser_auth)
    first_acquired, release_first, second_entered = _gate_first_immediate_transaction(monkeypatch)

    def exchange() -> Response:
        return cast(
            "Response",
            client.post(
                "/v1/device-authorizations/token",
                json={"device_code": started["device_code"]},
            ),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first: Future[Response] = executor.submit(exchange)
        assert first_acquired.wait(timeout=5)
        second: Future[Response] = executor.submit(exchange)
        assert second_entered.wait(timeout=5)
        assert not second.done()
        release_first.set()
        responses = [first.result(timeout=5), second.result(timeout=5)]

    assert sorted(response.status_code for response in responses) == [200, 400]
    assert {response.json().get("code") for response in responses} == {None, "expired_token"}
    app = cast("Any", client.app)
    with Session(app.state.engine) as database:
        assert len(database.scalars(select(TokenFamily)).all()) == 1


def test_concurrent_refresh_replay_revokes_the_family(
    client: TestClient,
    browser_auth: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = _approved_device(client, browser_auth)
    issued = client.post(
        "/v1/device-authorizations/token", json={"device_code": started["device_code"]}
    ).json()
    first_acquired, release_first, second_entered = _gate_first_immediate_transaction(monkeypatch)

    def refresh() -> Response:
        return cast(
            "Response",
            client.post("/v1/tokens/refresh", json={"refresh_token": issued["refresh_token"]}),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first: Future[Response] = executor.submit(refresh)
        assert first_acquired.wait(timeout=5)
        second: Future[Response] = executor.submit(refresh)
        assert second_entered.wait(timeout=5)
        assert not second.done()
        release_first.set()
        responses = [first.result(timeout=5), second.result(timeout=5)]

    assert sorted(response.status_code for response in responses) == [200, 401]
    assert {response.json().get("code") for response in responses} == {
        None,
        "refresh_token_reused",
    }
    app = cast("Any", client.app)
    with Session(app.state.engine) as database:
        family = database.scalar(select(TokenFamily))
        assert family is not None
        assert family.revoked_at is not None


def test_disabled_user_is_rejected_by_browser_bearer_and_login(
    client: TestClient,
    account: dict[str, object],
    browser_auth: dict[str, str],
) -> None:
    started = _approved_device(client, browser_auth)
    issued = client.post(
        "/v1/device-authorizations/token", json={"device_code": started["device_code"]}
    ).json()
    app = cast("Any", client.app)
    with Session(app.state.engine) as database:
        user = database.scalar(select(User))
        assert user is not None
        assert user.status == "active"
        user.status = "disabled"
        database.commit()

    browser = client.delete("/v1/browser-sessions/current", headers=browser_auth)
    bearer = client.get("/v1/me", headers={"Authorization": f"Bearer {issued['access_token']}"})
    login = client.post(
        "/v1/browser-sessions",
        json={"email": account["email"], "password": "correct horse battery"},
    )
    assert browser.status_code == 401
    assert bearer.status_code == 401
    assert login.status_code == 401
    assert login.json()["message"] == "Email or password is incorrect"
