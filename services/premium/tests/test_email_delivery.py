from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from podcast_reader_premium import email_delivery
from podcast_reader_premium.contracts import (
    EmailDeliveryErrorV1,
    EmailDeliveryRequestV1,
    EmailDeliveryV1,
)
from podcast_reader_premium.email_delivery import DevMaildirSink, EmailRelay
from podcast_reader_premium.entitlements import apply_entitlement_event
from podcast_reader_premium.models import EmailDeliveryReceipt, FeatureFlag, User

CONTRACTS = Path(__file__).parents[1] / "contracts" / "v1" / "email"


def _app(client: TestClient) -> Any:
    return cast("Any", client.app)


def _fixture(name: str) -> dict[str, object]:
    return cast("dict[str, object]", json.loads((CONTRACTS / name).read_text()))


def _bearer(client: TestClient, browser_auth: dict[str, str]) -> dict[str, str]:
    started = client.post("/v1/device-authorizations", json={"client": "desktop"}).json()
    assert (
        client.post(
            "/v1/device-authorizations/approve",
            json={"user_code": started["user_code"]},
            headers=browser_auth,
        ).status_code
        == 204
    )
    issued = client.post(
        "/v1/device-authorizations/token", json={"device_code": started["device_code"]}
    )
    assert issued.status_code == 200
    return {"Authorization": f"Bearer {issued.json()['access_token']}"}


def _enable_email(client: TestClient, user_id: str) -> None:
    with Session(_app(client).state.engine) as database:
        apply_entitlement_event(
            database,
            user_id=user_id,
            event_type="override_set",
            tier="premium",
            actor_user_id=None,
            reason="email relay test premium",
        )
        flag = database.get(FeatureFlag, "transcript_email")
        assert flag is not None
        flag.audience = "premium"
        flag.revision = 1
        database.commit()


@pytest.mark.parametrize("name", ["request-subscription.json", "request-manual.json"])
def test_request_fixtures_are_strict_and_digest_bound(name: str) -> None:
    source = _fixture(name)
    parsed = EmailDeliveryRequestV1.model_validate(source)
    assert parsed.model_dump(mode="json") == source
    source["extra"] = "not frozen"
    with pytest.raises(ValidationError):
        EmailDeliveryRequestV1.model_validate(source)


def test_success_and_replay_fixtures_are_identical_strict_documents() -> None:
    delivered = (CONTRACTS / "delivered.json").read_bytes()
    replay = (CONTRACTS / "idempotent-replay.json").read_bytes()
    assert replay == delivered
    assert EmailDeliveryV1.model_validate_json(delivered).state == "delivered"


def test_error_fixture_freezes_the_complete_bounded_code_set() -> None:
    source = json.loads((CONTRACTS / "errors.json").read_text())
    parsed = TypeAdapter(list[EmailDeliveryErrorV1]).validate_python(source)
    assert {item.code for item in parsed} == {
        "premium_feature_unavailable",
        "delivery_too_large",
        "idempotency_conflict",
        "delivery_unavailable",
        "email_not_verified",
    }


def test_receipt_schema_has_only_the_content_free_allowlist(client: TestClient) -> None:
    columns = {
        item["name"]
        for item in inspect(_app(client).state.engine).get_columns("email_delivery_receipts")
    }
    assert columns == {
        "id",
        "user_id",
        "client_delivery_id",
        "consent_kind",
        "content_bytes",
        "payload_hmac",
        "sink",
        "state",
        "error_code",
        "attempts",
        "created_at",
        "updated_at",
        "delivered_at",
    }
    forbidden = {"title", "transcript", "email", "recipient", "source", "feed", "body"}
    assert all(not any(term in column for term in forbidden) for column in columns)


def test_maildir_rejects_checkout_and_symlink_paths(tmp_path: Path) -> None:
    checkout_path = Path(__file__).parents[1] / "unsafe-maildir"
    with pytest.raises(RuntimeError, match="outside the source checkout"):
        DevMaildirSink(checkout_path).prepare()
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    with pytest.raises(RuntimeError, match="symlinks"):
        DevMaildirSink(linked).prepare()


def test_relay_is_entitlement_gated_content_stateless_and_idempotent(
    client: TestClient,
    account: dict[str, object],
    browser_auth: dict[str, str],
) -> None:
    bearer = _bearer(client, browser_auth)
    request = _fixture("request-subscription.json")
    unavailable = client.post("/v1/email-deliveries", json=request, headers=bearer)
    assert unavailable.status_code == 403
    assert unavailable.json()["code"] == "premium_feature_unavailable"

    user_id = cast("str", account["id"])
    _enable_email(client, user_id)
    delivered = client.post("/v1/email-deliveries", json=request, headers=bearer)
    assert delivered.status_code == 200
    assert set(delivered.json()) == {
        "schema_version",
        "delivery_id",
        "client_delivery_id",
        "state",
        "destination",
        "delivered_at",
    }
    replay = client.post("/v1/email-deliveries", json=request, headers=bearer)
    assert replay.status_code == 200
    assert replay.content == delivered.content

    messages = list(_app(client).state.settings.email_maildir_path.glob("new/*.eml"))
    assert len(messages) == 1
    message = messages[0].read_text()
    assert request["transcript_text"] in message.replace("\n", "\n")
    assert "dev-mailbox@podcast-reader.invalid" in message
    assert "Reader@Example.COM" not in message and "reader@example.com" not in message

    with Session(_app(client).state.engine) as database:
        receipts = database.scalars(select(EmailDeliveryReceipt)).all()
        assert len(receipts) == 1
        receipt = receipts[0]
        assert receipt.state == "delivered" and receipt.attempts == 1
        stored = " ".join(str(value) for value in vars(receipt).values())
        assert cast("str", request["title"]) not in stored
        assert cast("str", request["transcript_text"]).strip() not in stored
        assert cast("str", account["email"]) not in stored


def test_same_client_id_with_different_content_conflicts(
    client: TestClient, account: dict[str, object], browser_auth: dict[str, str]
) -> None:
    bearer = _bearer(client, browser_auth)
    _enable_email(client, cast("str", account["id"]))
    request = _fixture("request-manual.json")
    assert client.post("/v1/email-deliveries", json=request, headers=bearer).status_code == 200
    request["title"] = "Different title"
    conflict = client.post("/v1/email-deliveries", json=request, headers=bearer)
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "idempotency_conflict"


def test_request_rejects_oversize_and_control_content(
    client: TestClient, account: dict[str, object], browser_auth: dict[str, str]
) -> None:
    bearer = _bearer(client, browser_auth)
    _enable_email(client, cast("str", account["id"]))
    request = _fixture("request-manual.json")
    request["transcript_text"] = "x" * (512 * 1024 + 1)
    request["content_sha256"] = "0" * 64
    too_large = client.post("/v1/email-deliveries", json=request, headers=bearer)
    assert too_large.status_code == 413
    assert too_large.json()["code"] == "delivery_too_large"
    request = _fixture("request-manual.json")
    request["title"] = "unsafe\r\nBcc: victim@example.com"
    invalid = client.post("/v1/email-deliveries", json=request, headers=bearer)
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "invalid_request"


def test_sink_failure_is_bounded_and_retry_recovers_without_duplicate(
    client: TestClient,
    account: dict[str, object],
    browser_auth: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bearer = _bearer(client, browser_auth)
    _enable_email(client, cast("str", account["id"]))
    request = _fixture("request-manual.json")
    original = DevMaildirSink.deliver

    def fail(*_args: object, **_kwargs: object) -> None:
        raise OSError("sensitive sink detail")

    monkeypatch.setattr(DevMaildirSink, "deliver", fail)
    failed = client.post("/v1/email-deliveries", json=request, headers=bearer)
    assert failed.status_code == 503
    assert failed.json()["code"] == "delivery_unavailable"
    assert "sensitive" not in failed.text
    monkeypatch.setattr(DevMaildirSink, "deliver", original)
    recovered = client.post("/v1/email-deliveries", json=request, headers=bearer)
    assert recovered.status_code == 200
    assert len(list(_app(client).state.settings.email_maildir_path.glob("new/*.eml"))) == 1
    with Session(_app(client).state.engine) as database:
        receipt = database.scalar(select(EmailDeliveryReceipt))
        assert receipt is not None and receipt.attempts == 2 and receipt.error_code is None


def test_relay_enforces_its_own_terminal_eight_attempt_cap(
    client: TestClient,
    account: dict[str, object],
    browser_auth: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bearer = _bearer(client, browser_auth)
    _enable_email(client, cast("str", account["id"]))
    request = _fixture("request-manual.json")
    sink_calls = 0

    def fail(*_args: object, **_kwargs: object) -> None:
        nonlocal sink_calls
        sink_calls += 1
        raise OSError("sink unavailable")

    monkeypatch.setattr(DevMaildirSink, "deliver", fail)
    for _ in range(9):
        failed = client.post("/v1/email-deliveries", json=request, headers=bearer)
        assert failed.status_code == 503
        assert failed.json()["code"] == "delivery_unavailable"

    assert sink_calls == 8
    with Session(_app(client).state.engine) as database:
        receipt = database.scalar(select(EmailDeliveryReceipt))
        assert receipt is not None
        assert (receipt.state, receipt.error_code, receipt.attempts) == (
            "failed",
            "delivery_unavailable",
            8,
        )


def test_route_delivers_off_the_event_loop_with_a_worker_session(
    client: TestClient,
    account: dict[str, object],
    browser_auth: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bearer = _bearer(client, browser_auth)
    _enable_email(client, cast("str", account["id"]))
    original = EmailRelay.deliver
    observed: dict[str, object] = {}

    def inspect_worker(
        self: EmailRelay,
        database: Session,
        user_id: str,
        payload: EmailDeliveryRequestV1,
    ) -> EmailDeliveryV1:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            observed["off_event_loop"] = True
        observed["database"] = database
        return original(self, database, user_id, payload)

    monkeypatch.setattr(EmailRelay, "deliver", inspect_worker)
    response = client.post(
        "/v1/email-deliveries", json=_fixture("request-manual.json"), headers=bearer
    )
    assert response.status_code == 200
    assert observed["off_event_loop"] is True
    assert isinstance(observed["database"], Session)


def test_delivered_timestamp_is_captured_after_the_sink_returns(
    client: TestClient,
    account: dict[str, object],
    browser_auth: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bearer = _bearer(client, browser_auth)
    _enable_email(client, cast("str", account["id"]))
    timestamps = iter((100, 200))
    monkeypatch.setattr(email_delivery, "now_epoch", lambda: next(timestamps))

    response = client.post(
        "/v1/email-deliveries", json=_fixture("request-manual.json"), headers=bearer
    )
    assert response.status_code == 200
    with Session(_app(client).state.engine) as database:
        receipt = database.scalar(select(EmailDeliveryReceipt))
        assert receipt is not None
        assert receipt.created_at == 100
        assert receipt.delivered_at == receipt.updated_at == 200


def test_receipt_health_order_has_a_matching_pagination_index(client: TestClient) -> None:
    indexes = {
        item["name"]: item["column_names"]
        for item in inspect(_app(client).state.engine).get_indexes("email_delivery_receipts")
    }
    assert indexes["ix_email_receipts_created_id"] == ["created_at", "id"]


def test_admin_health_contains_receipts_but_not_message_content(
    client: TestClient,
    account: dict[str, object],
    browser_auth: dict[str, str],
) -> None:
    bearer = _bearer(client, browser_auth)
    _enable_email(client, cast("str", account["id"]))
    request = _fixture("request-manual.json")
    assert client.post("/v1/email-deliveries", json=request, headers=bearer).status_code == 200
    with Session(_app(client).state.engine) as database:
        user = database.get(User, cast("str", account["id"]))
        assert user is not None
        user.role = "admin"
        database.commit()
    page = client.get("/admin/email-deliveries")
    assert page.status_code == 200
    assert "Delivered: 1" in page.text
    assert cast("str", request["title"]) not in page.text
    assert cast("str", request["transcript_text"]).strip() not in page.text
