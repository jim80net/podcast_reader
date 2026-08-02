from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import replace
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from podcast_reader_premium.app import create_app
from podcast_reader_premium.billing import (
    BillingConfigurationError,
    CheckoutLine,
    FakeBillingAdapter,
    ProductSnapshot,
    StripeBillingAdapter,
    WebhookVerificationError,
    is_safe_checkout_url,
)
from podcast_reader_premium.config import Settings
from podcast_reader_premium.db import create_database
from podcast_reader_premium.models import (
    CheckoutAttempt,
    EntitlementEvent,
    EntitlementProjection,
    PaymentEvent,
    StripeCustomer,
    User,
)
from podcast_reader_premium.payments import PaymentWorker


def _app(client: TestClient) -> Any:
    return cast("Any", client.app)


def _fake(client: TestClient) -> FakeBillingAdapter:
    return cast("FakeBillingAdapter", _app(client).state.billing)


def _worker(client: TestClient) -> PaymentWorker:
    return cast("PaymentWorker", _app(client).state.payment_worker)


def _start_checkout(client: TestClient, browser_auth: dict[str, str]) -> dict[str, str]:
    response = client.post("/v1/billing/checkout-sessions", headers=browser_auth)
    assert response.status_code == 201
    return cast("dict[str, str]", response.json())


def _event_payload(
    event_id: str,
    session_id: str,
    *,
    event_type: str = "checkout.session.completed",
    livemode: bool = False,
) -> bytes:
    return json.dumps(
        {
            "id": event_id,
            "type": event_type,
            "livemode": livemode,
            "data": {"object": {"id": session_id}},
        },
        separators=(",", ":"),
    ).encode()


def _post_event(client: TestClient, payload: bytes, *, signature: str | None = None) -> Any:
    fake = _fake(client)
    return client.post(
        "/v1/webhooks/stripe",
        content=payload,
        headers={"Stripe-Signature": signature or fake.sign(payload)},
    )


def test_checkout_is_csrf_protected_and_redirect_never_grants(
    client: TestClient,
    account: dict[str, object],
    browser_auth: dict[str, str],
) -> None:
    assert client.post("/v1/billing/checkout-sessions").status_code == 403
    checkout = _start_checkout(client, browser_auth)
    assert checkout["checkout_url"].startswith("https://checkout.stripe.test/")
    with Session(_app(client).state.engine) as database:
        attempt = database.get(CheckoutAttempt, checkout["attempt_id"])
        projection = database.get(EntitlementProjection, account["id"])
        assert attempt is not None and attempt.status == "session_created"
        assert projection is not None and projection.effective_tier == "free"
        assert database.scalar(select(EntitlementEvent)) is None


def test_checkout_rejects_non_stripe_redirect_before_return(
    client: TestClient,
    browser_auth: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _fake(client)
    original = fake.create_checkout

    def unsafe_checkout(**kwargs: Any) -> Any:
        return replace(original(**kwargs), url="https://evilstripe.com/pay")

    monkeypatch.setattr(fake, "create_checkout", unsafe_checkout)
    response = client.post("/v1/billing/checkout-sessions", headers=browser_auth)
    assert response.status_code == 503
    with Session(_app(client).state.engine) as database:
        attempt = database.scalar(select(CheckoutAttempt))
        assert attempt is not None and attempt.status == "failed"

    success = client.get("/account/billing/success")
    assert success.status_code == 200
    assert "Checkout returned. Your account reflects verified payment status." in success.text
    assert "Current online tier: <strong>free</strong>" in success.text
    with Session(_app(client).state.engine) as database:
        assert database.scalar(select(EntitlementEvent)) is None


def test_verified_event_is_durable_idempotent_and_grants_once(
    client: TestClient,
    account: dict[str, object],
    browser_auth: dict[str, str],
) -> None:
    checkout = _start_checkout(client, browser_auth)
    session_id = checkout["checkout_url"].rsplit("/", 1)[-1]
    fake = _fake(client)
    fake.complete(session_id)
    payload = _event_payload("evt_test_paid", session_id)

    first = _post_event(client, payload)
    duplicate = _post_event(client, payload)
    assert first.status_code == duplicate.status_code == 204
    with Session(_app(client).state.engine) as database:
        inbox = database.scalars(select(PaymentEvent)).all()
        assert len(inbox) == 1 and inbox[0].state == "pending"
        assert "payload" not in PaymentEvent.__table__.columns
        assert database.scalar(select(EntitlementEvent)) is None

    assert _worker(client).run_once() is True
    assert _worker(client).run_once() is False
    with Session(_app(client).state.engine) as database:
        event = database.get(PaymentEvent, "evt_test_paid")
        projection = database.get(EntitlementProjection, account["id"])
        attempts = database.scalars(select(CheckoutAttempt)).all()
        grants = database.scalars(select(EntitlementEvent)).all()
        customer = database.get(StripeCustomer, account["id"])
        assert event is not None and (event.state, event.result_code) == (
            "processed",
            "premium_granted",
        )
        assert projection is not None and projection.effective_tier == "premium"
        assert len(grants) == 1 and grants[0].source_reference == session_id
        assert len(attempts) == 1 and attempts[0].status == "completed"
        assert customer is not None and customer.customer_id == "cus_test_reader"

    _post_event(client, payload)
    assert _worker(client).run_once() is False
    with Session(_app(client).state.engine) as database:
        assert len(database.scalars(select(EntitlementEvent)).all()) == 1


def test_paid_orphaned_provider_session_reconciles_from_server_metadata(
    client: TestClient,
    account: dict[str, object],
    browser_auth: dict[str, str],
) -> None:
    checkout = _start_checkout(client, browser_auth)
    session_id = checkout["checkout_url"].rsplit("/", 1)[-1]
    with Session(_app(client).state.engine) as database:
        attempt = database.get(CheckoutAttempt, checkout["attempt_id"])
        assert attempt is not None
        attempt.checkout_session_id = None
        attempt.status = "created"
        database.commit()
    _fake(client).complete(session_id)
    response = _post_event(client, _event_payload("evt_orphan_reconcile", session_id))
    assert response.status_code == 204
    assert _worker(client).run_once() is True
    with Session(_app(client).state.engine) as database:
        attempt = database.get(CheckoutAttempt, checkout["attempt_id"])
        projection = database.get(EntitlementProjection, account["id"])
        assert attempt is not None and attempt.checkout_session_id == session_id
        assert attempt.status == "completed"
        assert projection is not None and projection.effective_tier == "premium"


def test_admin_user_detail_shows_bounded_checkout_history(
    client: TestClient,
    account: dict[str, object],
    browser_auth: dict[str, str],
) -> None:
    checkout = _start_checkout(client, browser_auth)
    session_id = checkout["checkout_url"].rsplit("/", 1)[-1]
    with Session(_app(client).state.engine) as database:
        user = database.get(User, account["id"])
        assert user is not None
        user.role = "admin"
        database.commit()
    login = client.post(
        "/admin/login",
        data={"email": cast("str", account["email"]), "password": "correct horse battery"},
        headers={"Origin": "https://premium.test"},
    )
    assert login.status_code == 200
    page = client.get(f"/admin/users/{account['id']}")
    assert checkout["attempt_id"] in page.text
    assert session_id in page.text
    assert "session_created" in page.text


@pytest.mark.parametrize(
    "failure",
    [
        "unpaid",
        "wrong_price",
        "wrong_quantity",
        "incomplete_lines",
        "wrong_amount",
        "wrong_currency",
        "wrong_metadata",
        "wrong_mode",
        "live",
    ],
)
def test_authoritative_checkout_validation_rejects_mismatches(
    client: TestClient,
    account: dict[str, object],
    browser_auth: dict[str, str],
    failure: str,
) -> None:
    checkout = _start_checkout(client, browser_auth)
    session_id = checkout["checkout_url"].rsplit("/", 1)[-1]
    fake = _fake(client)
    fake.complete(session_id)
    snapshot = fake.sessions[session_id]
    changes: dict[str, object] = {}
    if failure == "unpaid":
        changes["payment_status"] = "unpaid"
    elif failure == "wrong_price":
        changes["lines"] = (CheckoutLine("price_test_other", 1),)
    elif failure == "wrong_quantity":
        changes["lines"] = (CheckoutLine("price_test_premium", 2),)
    elif failure == "incomplete_lines":
        changes["lines_complete"] = False
    elif failure == "wrong_amount":
        changes["amount_total"] = 1000
    elif failure == "wrong_currency":
        changes["currency"] = "eur"
    elif failure == "wrong_metadata":
        changes["metadata"] = {"attempt_id": checkout["attempt_id"], "user_id": "usr_other"}
    elif failure == "wrong_mode":
        changes["mode"] = "subscription"
    else:
        changes["livemode"] = True
    fake.sessions[session_id] = replace(snapshot, **cast("Any", changes))
    payload = _event_payload(f"evt_{failure}", session_id)
    assert _post_event(client, payload).status_code == 204
    assert _worker(client).run_once() is True
    with Session(_app(client).state.engine) as database:
        event = database.get(PaymentEvent, f"evt_{failure}")
        projection = database.get(EntitlementProjection, account["id"])
        assert event is not None and event.state == "rejected"
        assert projection is not None and projection.effective_tier == "free"
        assert database.scalar(select(EntitlementEvent)) is None


def test_existing_customer_mismatch_is_rejected(
    client: TestClient,
    account: dict[str, object],
    browser_auth: dict[str, str],
) -> None:
    checkout = _start_checkout(client, browser_auth)
    session_id = checkout["checkout_url"].rsplit("/", 1)[-1]
    with Session(_app(client).state.engine) as database:
        database.add(
            StripeCustomer(
                user_id=cast("str", account["id"]),
                customer_id="cus_expected",
                created_at=0,
            )
        )
        database.commit()
    _fake(client).complete(session_id, customer_id="cus_different")
    payload = _event_payload("evt_wrong_customer", session_id)
    assert _post_event(client, payload).status_code == 204
    assert _worker(client).run_once() is True
    with Session(_app(client).state.engine) as database:
        event = database.get(PaymentEvent, "evt_wrong_customer")
        assert event is not None and event.result_code == "checkout_customer_mismatch"
        assert database.scalar(select(EntitlementEvent)) is None


def test_livemode_webhook_is_rejected_without_provider_lookup(
    client: TestClient, browser_auth: dict[str, str]
) -> None:
    checkout = _start_checkout(client, browser_auth)
    session_id = checkout["checkout_url"].rsplit("/", 1)[-1]
    payload = _event_payload("evt_live", session_id, livemode=True)
    fake = _fake(client)
    calls_before = list(fake.retrieve_calls)
    assert _post_event(client, payload).status_code == 204
    assert _worker(client).run_once() is True
    assert fake.retrieve_calls == calls_before
    with Session(_app(client).state.engine) as database:
        event = database.get(PaymentEvent, "evt_live")
        assert event is not None and event.result_code == "livemode_rejected"


def test_expired_then_completed_event_order_still_grants(
    client: TestClient,
    account: dict[str, object],
    browser_auth: dict[str, str],
) -> None:
    checkout = _start_checkout(client, browser_auth)
    session_id = checkout["checkout_url"].rsplit("/", 1)[-1]
    expired = _event_payload("evt_expired", session_id, event_type="checkout.session.expired")
    assert _post_event(client, expired).status_code == 204
    assert _worker(client).run_once() is True
    _fake(client).complete(session_id)
    completed = _event_payload("evt_completed_late", session_id)
    assert _post_event(client, completed).status_code == 204
    assert _worker(client).run_once() is True
    with Session(_app(client).state.engine) as database:
        attempt = database.get(CheckoutAttempt, checkout["attempt_id"])
        projection = database.get(EntitlementProjection, account["id"])
        assert attempt is not None and attempt.status == "completed"
        assert projection is not None and projection.effective_tier == "premium"
    late_expiry = _event_payload(
        "evt_expired_late", session_id, event_type="checkout.session.expired"
    )
    assert _post_event(client, late_expiry).status_code == 204
    assert _worker(client).run_once() is True
    with Session(_app(client).state.engine) as database:
        attempt = database.get(CheckoutAttempt, checkout["attempt_id"])
        assert attempt is not None and attempt.status == "completed"


def test_stale_processing_claim_is_recovered_after_restart_shape(
    client: TestClient, browser_auth: dict[str, str]
) -> None:
    checkout = _start_checkout(client, browser_auth)
    session_id = checkout["checkout_url"].rsplit("/", 1)[-1]
    _fake(client).complete(session_id)
    payload = _event_payload("evt_stale", session_id)
    assert _post_event(client, payload).status_code == 204
    with Session(_app(client).state.engine) as database:
        event = database.get(PaymentEvent, "evt_stale")
        assert event is not None
        event.state = "processing"
        event.claimed_at = 1
        database.commit()
    assert _worker(client).run_once() is True
    with Session(_app(client).state.engine) as database:
        event = database.get(PaymentEvent, "evt_stale")
        assert event is not None and event.state == "processed"


def test_transient_provider_failure_returns_event_to_pending(
    client: TestClient, browser_auth: dict[str, str]
) -> None:
    checkout = _start_checkout(client, browser_auth)
    session_id = checkout["checkout_url"].rsplit("/", 1)[-1]
    fake = _fake(client)
    snapshot = fake.sessions.pop(session_id)
    payload = _event_payload("evt_retry", session_id)
    assert _post_event(client, payload).status_code == 204
    assert _worker(client).run_once() is True
    with Session(_app(client).state.engine) as database:
        event = database.get(PaymentEvent, "evt_retry")
        assert event is not None and (event.state, event.result_code) == (
            "pending",
            "provider_unavailable",
        )
        assert event.retry_at is not None and event.attempts == 1
        event.retry_at = 0
        database.commit()
    fake.sessions[session_id] = replace(
        snapshot, payment_status="paid", customer_id="cus_test_retry"
    )
    assert _worker(client).run_once() is True
    with Session(_app(client).state.engine) as database:
        event = database.get(PaymentEvent, "evt_retry")
        assert event is not None and event.state == "processed"


def test_deferred_provider_retry_does_not_block_later_event_and_parks(
    client: TestClient,
    account: dict[str, object],
    browser_auth: dict[str, str],
) -> None:
    blocked = _start_checkout(client, browser_auth)
    blocked_id = blocked["checkout_url"].rsplit("/", 1)[-1]
    ready = _start_checkout(client, browser_auth)
    ready_id = ready["checkout_url"].rsplit("/", 1)[-1]
    fake = _fake(client)
    fake.sessions.pop(blocked_id)
    fake.complete(ready_id)
    assert _post_event(client, _event_payload("evt_blocked", blocked_id)).status_code == 204
    assert _post_event(client, _event_payload("evt_ready", ready_id)).status_code == 204
    worker = PaymentWorker(
        _app(client).state.session_factory,
        fake,
        replace(_app(client).state.settings, payment_max_attempts=2),
    )
    assert worker.run_once() is True
    assert worker.run_once() is True
    with Session(_app(client).state.engine) as database:
        blocked_event = database.get(PaymentEvent, "evt_blocked")
        ready_event = database.get(PaymentEvent, "evt_ready")
        projection = database.get(EntitlementProjection, account["id"])
        assert blocked_event is not None and blocked_event.state == "pending"
        assert ready_event is not None and ready_event.state == "processed"
        assert projection is not None and projection.effective_tier == "premium"
        blocked_event.retry_at = 0
        database.commit()
    assert worker.run_once() is True
    with Session(_app(client).state.engine) as database:
        blocked_event = database.get(PaymentEvent, "evt_blocked")
        assert blocked_event is not None
        assert (blocked_event.state, blocked_event.attempts, blocked_event.result_code) == (
            "parked",
            2,
            "provider_retry_exhausted",
        )
        user = database.get(User, account["id"])
        assert user is not None
        user.role = "admin"
        database.commit()
    admin_page = client.get("/admin/")
    assert admin_page.status_code == 200
    assert "provider_retry_exhausted" in admin_page.text


def test_webhook_requires_exact_recent_signature_and_bounded_body(
    client: TestClient, browser_auth: dict[str, str]
) -> None:
    checkout = _start_checkout(client, browser_auth)
    session_id = checkout["checkout_url"].rsplit("/", 1)[-1]
    payload = _event_payload("evt_signature", session_id)
    fake = _fake(client)
    missing = client.post("/v1/webhooks/stripe", content=payload)
    wrong = _post_event(client, payload, signature="t=1,v1=wrong")
    old = _post_event(
        client, payload, signature=fake.sign(payload, timestamp=int(time.time()) - 301)
    )
    oversized = client.post(
        "/v1/webhooks/stripe",
        content=b"x" * (64 * 1024 + 1),
        headers={"Stripe-Signature": "t=1,v1=x"},
    )
    assert missing.status_code == wrong.status_code == old.status_code == 400
    assert oversized.status_code == 413
    with Session(_app(client).state.engine) as database:
        assert database.scalar(select(PaymentEvent)) is None


def test_provider_event_id_content_collision_fails_closed(
    client: TestClient, browser_auth: dict[str, str]
) -> None:
    first = _start_checkout(client, browser_auth)
    second = _start_checkout(client, browser_auth)
    first_payload = _event_payload("evt_collision", first["checkout_url"].rsplit("/", 1)[-1])
    second_payload = _event_payload("evt_collision", second["checkout_url"].rsplit("/", 1)[-1])
    assert _post_event(client, first_payload).status_code == 204
    collision = _post_event(client, second_payload)
    assert collision.status_code == 400
    assert collision.json()["code"] == "webhook_conflict"


@pytest.mark.parametrize(
    "field,value",
    [("id", None), ("id", ""), ("type", None), ("type", ""), ("livemode", None)],
)
def test_signed_webhook_fields_require_nonempty_exact_types(
    client: TestClient,
    field: str,
    value: object,
) -> None:
    event: dict[str, object] = {
        "id": "evt_strict",
        "type": "checkout.session.completed",
        "livemode": False,
        "data": {"object": {"id": "cs_test_strict"}},
    }
    event[field] = value
    payload = json.dumps(event, separators=(",", ":")).encode()
    assert _post_event(client, payload).status_code == 400


@pytest.mark.parametrize(
    "url",
    [
        "https://evilstripe.com/session",
        "https://stripe.com.evil.example/session",
        "https://user:password@checkout.stripe.com/session",
        "https://checkout.stripe.com:444/session",
        "https://checkout.stripe.com:invalid/session",
        "http://checkout.stripe.com/session",
    ],
)
def test_checkout_url_allowlist_rejects_lookalikes_and_userinfo(url: str) -> None:
    assert is_safe_checkout_url(url) is False
    assert is_safe_checkout_url("https://checkout.stripe.com/c/pay/cs_test") is True


def test_live_or_non_test_stripe_keys_are_rejected_before_network(settings: Settings) -> None:
    with pytest.raises(BillingConfigurationError, match="sk_test"):
        StripeBillingAdapter(replace(settings, stripe_secret_key="sk_live_secret"))
    with pytest.raises(BillingConfigurationError, match="sk_test"):
        StripeBillingAdapter(replace(settings, stripe_secret_key="not_a_key"))


@pytest.mark.parametrize(
    "product",
    [
        ProductSnapshot("price_test_premium", "usd", 999, True),
        ProductSnapshot("price_wrong", "usd", 999, False),
        ProductSnapshot("price_test_premium", "eur", 999, False),
        ProductSnapshot("price_test_premium", "usd", 1000, False),
    ],
)
def test_app_startup_rejects_billing_product_mismatch(
    client: TestClient,
    settings: Settings,
    product: ProductSnapshot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeBillingAdapter()
    fake.product = product
    engine = create_database(settings)
    disposed = False
    real_dispose = engine.dispose

    def dispose() -> None:
        nonlocal disposed
        disposed = True
        real_dispose()

    monkeypatch.setattr(engine, "dispose", dispose)
    with (
        pytest.raises(BillingConfigurationError, match="preflight"),
        TestClient(create_app(settings, engine=engine, billing=fake)),
    ):
        pass
    assert disposed is True


def test_test_app_uses_one_default_price_id_when_setting_is_absent(
    client: TestClient, settings: Settings
) -> None:
    default_settings = replace(settings, stripe_price_id=None)
    fake = FakeBillingAdapter()
    with TestClient(
        create_app(default_settings, engine=_app(client).state.engine, billing=fake),
        base_url=default_settings.public_origin,
    ) as default_client:
        assert default_client.get("/healthz").status_code == 200
        assert _app(default_client).state.payment_worker._settings.expected_stripe_price_id == (
            fake.product.price_id
        )


def test_stripe_adapter_verifies_unmodified_raw_body(settings: Settings) -> None:
    webhook_secret = "whsec_unit_test_secret"
    adapter = StripeBillingAdapter(
        replace(
            settings,
            stripe_secret_key="sk_test_unit_test_key",
            stripe_price_id="price_test_premium",
            stripe_webhook_secret=webhook_secret,
        )
    )
    payload = _event_payload("evt_real_verifier", "cs_test_raw")
    timestamp = int(time.time())
    signature = hmac.new(
        webhook_secret.encode(),
        str(timestamp).encode() + b"." + payload,
        hashlib.sha256,
    ).hexdigest()
    header = f"t={timestamp},v1={signature}"
    verified = adapter.verify_webhook(payload, header)
    assert (verified.id, verified.object_id, verified.livemode) == (
        "evt_real_verifier",
        "cs_test_raw",
        False,
    )
    with pytest.raises(WebhookVerificationError):
        adapter.verify_webhook(payload + b" ", header)
