from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

import stripe

from .config import Settings


class BillingConfigurationError(RuntimeError):
    pass


class BillingProviderError(RuntimeError):
    pass


class WebhookVerificationError(ValueError):
    pass


@dataclass(frozen=True)
class ProductSnapshot:
    price_id: str
    currency: str
    unit_amount: int
    livemode: bool


@dataclass(frozen=True)
class CheckoutLine:
    price_id: str
    quantity: int


@dataclass(frozen=True)
class CheckoutSnapshot:
    id: str
    url: str | None
    livemode: bool
    mode: str
    payment_status: str
    customer_id: str | None
    metadata: dict[str, str]
    amount_total: int | None
    currency: str | None
    lines: tuple[CheckoutLine, ...]
    lines_complete: bool


@dataclass(frozen=True)
class VerifiedWebhook:
    id: str
    type: str
    object_id: str
    livemode: bool


class BillingAdapter(Protocol):
    def preflight(self) -> ProductSnapshot: ...

    def create_checkout(
        self,
        *,
        attempt_id: str,
        user_id: str,
        email: str,
        customer_id: str | None,
        success_url: str,
        cancel_url: str,
    ) -> CheckoutSnapshot: ...

    def retrieve_checkout(self, session_id: str) -> CheckoutSnapshot: ...

    def verify_webhook(self, payload: bytes, signature: str) -> VerifiedWebhook: ...


def _required(value: str | None, name: str) -> str:
    if not value:
        raise BillingConfigurationError(f"{name} is required for the test billing adapter")
    return value


def is_safe_checkout_url(url: str, *, allow_test_host: bool = False) -> bool:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return False
    host = parsed.hostname or ""
    stripe_host = host == "stripe.com" or host.endswith(".stripe.com")
    test_host = allow_test_host and host == "checkout.stripe.test"
    return (
        parsed.scheme == "https"
        and port in {None, 443}
        and parsed.username is None
        and parsed.password is None
        and (stripe_host or test_host)
    )


def _event_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise WebhookVerificationError(f"Stripe webhook {name} is invalid")
    return value


def _event_livemode(value: object) -> bool:
    if not isinstance(value, bool):
        raise WebhookVerificationError("Stripe webhook livemode is invalid")
    return value


class StripeBillingAdapter:
    def __init__(self, settings: Settings) -> None:
        secret_key = _required(settings.stripe_secret_key, "STRIPE_SECRET_KEY")
        if secret_key.startswith("sk_live_") or not secret_key.startswith("sk_test_"):
            raise BillingConfigurationError("Stripe secret key must be an sk_test_ key")
        self._price_id = _required(settings.stripe_price_id, "STRIPE_PRICE_ID")
        self._webhook_secret = _required(settings.stripe_webhook_secret, "STRIPE_WEBHOOK_SECRET")
        if not self._webhook_secret.startswith("whsec_"):
            raise BillingConfigurationError("Stripe webhook secret must be a whsec_ secret")
        self._currency = settings.premium_currency
        self._unit_amount = settings.premium_unit_amount
        self._client = stripe.StripeClient(secret_key, max_network_retries=2)

    def preflight(self) -> ProductSnapshot:
        try:
            price = self._client.v1.prices.retrieve(self._price_id)
        except stripe.StripeError as exc:
            raise BillingProviderError("Stripe Price preflight failed") from exc
        snapshot = ProductSnapshot(
            price_id=str(price.id),
            currency=str(price.currency),
            unit_amount=int(price.unit_amount or -1),
            livemode=bool(price.livemode),
        )
        if snapshot.livemode:
            raise BillingConfigurationError("Stripe Price must not be live mode")
        if snapshot.currency != self._currency or snapshot.unit_amount != self._unit_amount:
            raise BillingConfigurationError("Stripe Price does not match configured test product")
        if not bool(price.active) or price.type != "one_time":
            raise BillingConfigurationError("Stripe Price must be active and one-time")
        return snapshot

    def create_checkout(
        self,
        *,
        attempt_id: str,
        user_id: str,
        email: str,
        customer_id: str | None,
        success_url: str,
        cancel_url: str,
    ) -> CheckoutSnapshot:
        params: dict[str, Any] = {
            "mode": "payment",
            "line_items": [{"price": self._price_id, "quantity": 1}],
            "metadata": {"attempt_id": attempt_id, "user_id": user_id},
            "client_reference_id": attempt_id,
            "success_url": success_url,
            "cancel_url": cancel_url,
        }
        if customer_id is None:
            params.update({"customer_creation": "always", "customer_email": email})
        else:
            params["customer"] = customer_id
        try:
            session = self._client.v1.checkout.sessions.create(
                cast("Any", params), options={"idempotency_key": attempt_id}
            )
        except stripe.StripeError as exc:
            raise BillingProviderError("Stripe Checkout creation failed") from exc
        return self._snapshot(session)

    def retrieve_checkout(self, session_id: str) -> CheckoutSnapshot:
        try:
            session = self._client.v1.checkout.sessions.retrieve(
                session_id, {"expand": ["line_items.data.price"]}
            )
        except stripe.StripeError as exc:
            raise BillingProviderError("Stripe Checkout retrieval failed") from exc
        return self._snapshot(session)

    def verify_webhook(self, payload: bytes, signature: str) -> VerifiedWebhook:
        try:
            construct_event = cast("Any", stripe.Webhook.construct_event)
            event = construct_event(payload, signature, self._webhook_secret)
        except (ValueError, stripe.SignatureVerificationError) as exc:
            raise WebhookVerificationError("Stripe webhook signature is invalid") from exc
        data_object = event.data.object
        object_id = _event_string(getattr(data_object, "id", None), "object ID")
        return VerifiedWebhook(
            id=_event_string(getattr(event, "id", None), "event ID"),
            type=_event_string(getattr(event, "type", None), "event type"),
            object_id=object_id,
            livemode=_event_livemode(getattr(event, "livemode", None)),
        )

    @staticmethod
    def _snapshot(session: Any) -> CheckoutSnapshot:
        metadata = {str(key): str(value) for key, value in dict(session.metadata or {}).items()}
        lines: list[CheckoutLine] = []
        line_items = getattr(session, "line_items", None)
        for item in getattr(line_items, "data", []) if line_items is not None else []:
            price = getattr(item, "price", None)
            price_id = getattr(price, "id", price)
            if isinstance(price_id, str):
                lines.append(CheckoutLine(price_id=price_id, quantity=int(item.quantity or 0)))
        customer = getattr(session, "customer", None)
        customer_id = getattr(customer, "id", customer)
        return CheckoutSnapshot(
            id=str(session.id),
            url=str(session.url) if session.url else None,
            livemode=bool(session.livemode),
            mode=str(session.mode or ""),
            payment_status=str(session.payment_status or ""),
            customer_id=str(customer_id) if customer_id else None,
            metadata=metadata,
            amount_total=int(session.amount_total) if session.amount_total is not None else None,
            currency=str(session.currency) if session.currency else None,
            lines=tuple(lines),
            lines_complete=line_items is not None
            and not bool(getattr(line_items, "has_more", True)),
        )


class FakeBillingAdapter:
    """Deterministic test adapter that still exercises signed raw webhook bytes."""

    def __init__(
        self,
        *,
        price_id: str = "price_test_premium",
        currency: str = "usd",
        unit_amount: int = 999,
        webhook_secret: str = "whsec_fake_test_secret",
    ) -> None:
        self.product = ProductSnapshot(price_id, currency, unit_amount, False)
        self.webhook_secret = webhook_secret
        self.sessions: dict[str, CheckoutSnapshot] = {}
        self.retrieve_calls: list[str] = []

    def preflight(self) -> ProductSnapshot:
        return self.product

    def create_checkout(
        self,
        *,
        attempt_id: str,
        user_id: str,
        email: str,
        customer_id: str | None,
        success_url: str,
        cancel_url: str,
    ) -> CheckoutSnapshot:
        del email, success_url, cancel_url
        session_id = f"cs_test_{attempt_id}"
        snapshot = CheckoutSnapshot(
            id=session_id,
            url=f"https://checkout.stripe.test/{session_id}",
            livemode=False,
            mode="payment",
            payment_status="unpaid",
            customer_id=customer_id,
            metadata={"attempt_id": attempt_id, "user_id": user_id},
            amount_total=self.product.unit_amount,
            currency=self.product.currency,
            lines=(CheckoutLine(self.product.price_id, 1),),
            lines_complete=True,
        )
        self.sessions[session_id] = snapshot
        return snapshot

    def retrieve_checkout(self, session_id: str) -> CheckoutSnapshot:
        self.retrieve_calls.append(session_id)
        try:
            return self.sessions[session_id]
        except KeyError as exc:
            raise BillingProviderError("fake Checkout Session was not found") from exc

    def complete(self, session_id: str, *, customer_id: str = "cus_test_reader") -> None:
        current = self.retrieve_checkout(session_id)
        self.sessions[session_id] = CheckoutSnapshot(
            **{
                **current.__dict__,
                "payment_status": "paid",
                "customer_id": customer_id,
            }
        )

    def verify_webhook(self, payload: bytes, signature: str) -> VerifiedWebhook:
        try:
            fields = dict(item.split("=", 1) for item in signature.split(","))
            timestamp = int(fields["t"])
            supplied = fields["v1"]
        except (KeyError, ValueError) as exc:
            raise WebhookVerificationError("fake webhook signature is invalid") from exc
        if abs(int(time.time()) - timestamp) > 300:
            raise WebhookVerificationError("fake webhook timestamp is outside tolerance")
        expected = hmac.new(
            self.webhook_secret.encode(),
            str(timestamp).encode() + b"." + payload,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(supplied, expected):
            raise WebhookVerificationError("fake webhook signature is invalid")
        try:
            event = json.loads(payload)
            event_id = _event_string(event["id"], "event ID")
            event_type = _event_string(event["type"], "event type")
            object_id = _event_string(event["data"]["object"]["id"], "object ID")
            livemode = _event_livemode(event["livemode"])
            return VerifiedWebhook(
                id=event_id,
                type=event_type,
                object_id=object_id,
                livemode=livemode,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise WebhookVerificationError("fake webhook payload is invalid") from exc

    def sign(self, payload: bytes, *, timestamp: int | None = None) -> str:
        when = int(time.time()) if timestamp is None else timestamp
        digest = hmac.new(
            self.webhook_secret.encode(), str(when).encode() + b"." + payload, hashlib.sha256
        ).hexdigest()
        return f"t={when},v1={digest}"
