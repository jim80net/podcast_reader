from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from podcast_reader_premium.models import DeviceAuthorization
from podcast_reader_premium.security import token_digest

CONTRACTS = Path(__file__).parents[1] / "contracts"


def _fixture(name: str) -> Any:
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


def _normalize_token_response(payload: dict[str, object]) -> dict[str, object]:
    normalized = dict(payload)
    fixture = cast("dict[str, object]", _fixture("native-auth-v1-token-response.json"))
    normalized["access_token"] = fixture["access_token"]
    normalized["refresh_token"] = fixture["refresh_token"]
    return normalized


def test_native_auth_route_inventory_and_success_fixtures_match_live_shapes(
    client: TestClient, browser_auth: dict[str, str]
) -> None:
    app = cast("Any", client.app)
    route_inventory = {
        (method, route.path)
        for route in app.routes
        for method in cast("set[str]", getattr(route, "methods", set()))
    }
    assert {
        ("POST", "/v1/accounts"),
        ("POST", "/v1/browser-sessions"),
        ("DELETE", "/v1/browser-sessions/current"),
        ("POST", "/v1/device-authorizations"),
        ("POST", "/v1/device-authorizations/token"),
        ("POST", "/v1/tokens/refresh"),
        ("POST", "/v1/tokens/revoke"),
        ("GET", "/v1/me"),
        ("GET", "/v1/me/entitlements"),
    }.issubset(route_inventory)

    started_response = client.post("/v1/device-authorizations", json={"client": "android"})
    assert started_response.status_code == 201
    started = cast("dict[str, object]", started_response.json())
    normalized_start = dict(started)
    start_fixture = cast("dict[str, object]", _fixture("native-auth-v1-device-start.json"))
    normalized_start["device_code"] = start_fixture["device_code"]
    normalized_start["user_code"] = start_fixture["user_code"]
    assert normalized_start == start_fixture

    approved = client.post(
        "/v1/device-authorizations/approve",
        json={"user_code": started["user_code"]},
        headers=browser_auth,
    )
    assert approved.status_code == 204
    exchanged_response = client.post(
        "/v1/device-authorizations/token", json={"device_code": started["device_code"]}
    )
    assert exchanged_response.status_code == 200
    exchanged = cast("dict[str, object]", exchanged_response.json())
    token_fixture = _fixture("native-auth-v1-token-response.json")
    assert _normalize_token_response(exchanged) == token_fixture
    current_response = client.get(
        "/v1/me", headers={"Authorization": f"Bearer {exchanged['access_token']}"}
    )
    assert current_response.status_code == 200
    current = cast("dict[str, object]", current_response.json())
    current_fixture = cast("dict[str, object]", _fixture("v1/current-user/current-user-v1.json"))
    assert set(current) == {"id"}
    assert isinstance(current["id"], str) and current["id"]
    current["id"] = current_fixture["id"]
    assert current == current_fixture

    refreshed_response = client.post(
        "/v1/tokens/refresh", json={"refresh_token": exchanged["refresh_token"]}
    )
    assert refreshed_response.status_code == 200
    refreshed = cast("dict[str, object]", refreshed_response.json())
    assert _normalize_token_response(refreshed) == token_fixture

    revoke_request = cast("dict[str, object]", _fixture("native-auth-v1-revoke-request.json"))
    assert set(revoke_request) == {"refresh_token"}
    revoked = client.post("/v1/tokens/revoke", json={"refresh_token": refreshed["refresh_token"]})
    revoke_response = _fixture("native-auth-v1-revoke-response.json")
    assert {"status": revoked.status_code, "body": None if not revoked.content else "present"} == (
        revoke_response
    )


def test_native_auth_error_fixtures_match_live_envelopes(
    client: TestClient, browser_auth: dict[str, str]
) -> None:
    pending_start = client.post("/v1/device-authorizations", json={"client": "desktop"}).json()
    pending = client.post(
        "/v1/device-authorizations/token",
        json={"device_code": pending_start["device_code"]},
    )
    slow_down = client.post(
        "/v1/device-authorizations/token",
        json={"device_code": pending_start["device_code"]},
    )

    expired_start = client.post("/v1/device-authorizations", json={"client": "desktop"}).json()
    denied_start = client.post("/v1/device-authorizations", json={"client": "android"}).json()
    app = cast("Any", client.app)
    with Session(app.state.engine) as database:
        expired_row = database.scalar(
            select(DeviceAuthorization).where(
                DeviceAuthorization.device_code_digest == token_digest(expired_start["device_code"])
            )
        )
        denied_row = database.scalar(
            select(DeviceAuthorization).where(
                DeviceAuthorization.device_code_digest == token_digest(denied_start["device_code"])
            )
        )
        assert expired_row is not None and denied_row is not None
        expired_row.expires_at = 0
        denied_row.state = "denied"
        database.commit()
    expired = client.post(
        "/v1/device-authorizations/token",
        json={"device_code": expired_start["device_code"]},
    )
    denied = client.post(
        "/v1/device-authorizations/token",
        json={"device_code": denied_start["device_code"]},
    )

    reuse_start = client.post("/v1/device-authorizations", json={"client": "desktop"}).json()
    client.post(
        "/v1/device-authorizations/approve",
        json={"user_code": reuse_start["user_code"]},
        headers=browser_auth,
    )
    issued = client.post(
        "/v1/device-authorizations/token",
        json={"device_code": reuse_start["device_code"]},
    ).json()
    client.post("/v1/tokens/refresh", json={"refresh_token": issued["refresh_token"]})
    reused = client.post("/v1/tokens/refresh", json={"refresh_token": issued["refresh_token"]})

    responses = [pending, slow_down, expired, denied, reused]
    assert [response.status_code for response in responses] == [400, 400, 400, 400, 401]
    actual: list[dict[str, object]] = []
    for response in responses:
        envelope = cast("dict[str, object]", response.json())
        assert set(envelope) == {"code", "message", "request_id"}
        envelope["request_id"] = "req_fixture"
        actual.append(envelope)
    assert actual == _fixture("native-auth-v1-errors.json")
