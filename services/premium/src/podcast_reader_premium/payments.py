from __future__ import annotations

import hashlib
from dataclasses import dataclass
from urllib.parse import urlsplit

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from .billing import (
    BillingAdapter,
    BillingProviderError,
    CheckoutSnapshot,
    VerifiedWebhook,
)
from .config import Settings
from .db import begin_immediate
from .entitlements import apply_entitlement_event
from .models import (
    CheckoutAttempt,
    EntitlementEvent,
    PaymentEvent,
    StripeCustomer,
    User,
)
from .security import now_epoch, record_id

ALLOWED_EVENT_TYPES = {"checkout.session.completed", "checkout.session.expired"}


@dataclass(frozen=True)
class CheckoutRedirect:
    attempt_id: str
    session_id: str
    url: str


def create_checkout_attempt(
    database: Session,
    billing: BillingAdapter,
    settings: Settings,
    user: User,
) -> CheckoutRedirect:
    timestamp = now_epoch()
    attempt = CheckoutAttempt(
        id=record_id("chk"),
        user_id=user.id,
        checkout_session_id=None,
        status="created",
        created_at=timestamp,
        updated_at=timestamp,
    )
    begin_immediate(database)
    customer = database.get(StripeCustomer, user.id)
    database.add(attempt)
    database.commit()
    try:
        checkout = billing.create_checkout(
            attempt_id=attempt.id,
            user_id=user.id,
            email=user.email,
            customer_id=customer.customer_id if customer is not None else None,
            success_url=f"{settings.public_origin}/account/billing/success",
            cancel_url=f"{settings.public_origin}/account/billing/cancel",
        )
        if checkout.livemode or checkout.url is None or urlsplit(checkout.url).scheme != "https":
            raise BillingProviderError("billing provider returned an unsafe Checkout Session")
    except BillingProviderError:
        begin_immediate(database)
        stored = database.get(CheckoutAttempt, attempt.id)
        if stored is not None:
            stored.status = "failed"
            stored.updated_at = now_epoch()
        database.commit()
        raise
    begin_immediate(database)
    stored = database.get(CheckoutAttempt, attempt.id)
    if stored is None:
        database.rollback()
        raise RuntimeError("checkout attempt disappeared before provider response")
    stored.checkout_session_id = checkout.id
    stored.status = "session_created"
    stored.updated_at = now_epoch()
    database.commit()
    return CheckoutRedirect(attempt.id, checkout.id, checkout.url)


def ingest_verified_webhook(
    database: Session,
    verified: VerifiedWebhook,
    payload: bytes,
) -> bool:
    """Insert a minimal inbox row; return False for an exact duplicate."""
    if (
        not verified.id
        or len(verified.id) > 128
        or not verified.type
        or len(verified.type) > 64
        or not verified.object_id
        or len(verified.object_id) > 128
    ):
        raise ValueError("provider webhook identifiers are invalid")
    digest = hashlib.sha256(payload).hexdigest()
    begin_immediate(database)
    existing = database.get(PaymentEvent, verified.id)
    if existing is not None:
        exact = (
            existing.event_type == verified.type
            and existing.object_id == verified.object_id
            and existing.payload_digest == digest
            and existing.livemode == verified.livemode
        )
        database.rollback()
        if not exact:
            raise ValueError("provider event ID was reused with different content")
        return False
    database.add(
        PaymentEvent(
            provider_event_id=verified.id,
            event_type=verified.type,
            object_id=verified.object_id,
            payload_digest=digest,
            livemode=verified.livemode,
            state="pending",
            received_at=now_epoch(),
            claimed_at=None,
            processed_at=None,
            result_code=None,
        )
    )
    database.commit()
    return True


class PaymentWorker:
    def __init__(
        self,
        factory: sessionmaker[Session],
        billing: BillingAdapter,
        settings: Settings,
    ) -> None:
        self._factory = factory
        self._billing = billing
        self._settings = settings

    def run_once(self) -> bool:
        event_id = self._claim_one()
        if event_id is None:
            return False
        with self._factory() as database:
            event = database.get(PaymentEvent, event_id)
            if event is None:
                return True
            livemode = event.livemode
            event_type = event.event_type
            object_id = event.object_id
        if livemode:
            self._reject_by_id(event_id, "livemode_rejected")
            return True
        if event_type not in ALLOWED_EVENT_TYPES:
            self._reject_by_id(event_id, "event_type_rejected")
            return True
        try:
            checkout = self._billing.retrieve_checkout(object_id)
        except BillingProviderError:
            with self._factory() as database:
                self._retry(database, event_id)
            return True
        with self._factory() as database:
            self._apply(database, event_id, checkout)
        return True

    def _claim_one(self) -> str | None:
        timestamp = now_epoch()
        with self._factory() as database:
            begin_immediate(database)
            database.execute(
                update(PaymentEvent)
                .where(
                    PaymentEvent.state == "processing",
                    PaymentEvent.claimed_at.is_not(None),
                    PaymentEvent.claimed_at <= timestamp - self._settings.payment_claim_ttl_seconds,
                )
                .values(state="pending", claimed_at=None, result_code="stale_claim_recovered")
            )
            event = database.scalar(
                select(PaymentEvent)
                .where(PaymentEvent.state == "pending")
                .order_by(PaymentEvent.received_at, PaymentEvent.provider_event_id)
                .limit(1)
            )
            if event is None:
                database.commit()
                return None
            event.state = "processing"
            event.claimed_at = timestamp
            event.result_code = None
            event_id = event.provider_event_id
            database.commit()
            return event_id

    def _apply(self, database: Session, event_id: str, checkout: CheckoutSnapshot) -> None:
        begin_immediate(database)
        event = database.get(PaymentEvent, event_id)
        if event is None or event.state != "processing":
            database.rollback()
            return
        reason, attempt = self._validate(database, event, checkout)
        if reason is not None or attempt is None:
            self._reject(database, event, reason or "checkout_rejected")
            return
        timestamp = now_epoch()
        if event.event_type == "checkout.session.expired":
            if attempt.status != "completed":
                attempt.status = "expired"
            attempt.updated_at = timestamp
            event.state = "processed"
            event.processed_at = timestamp
            event.result_code = "checkout_expired"
            database.commit()
            return

        existing_grant = database.scalar(
            select(EntitlementEvent).where(
                EntitlementEvent.source_reference == checkout.id,
                EntitlementEvent.event_type == "provider_grant",
            )
        )
        if existing_grant is not None and existing_grant.user_id != attempt.user_id:
            self._reject(database, event, "source_reference_conflict")
            return
        if existing_grant is None:
            apply_entitlement_event(
                database,
                user_id=attempt.user_id,
                event_type="provider_grant",
                tier="premium",
                actor_user_id=None,
                reason="verified Stripe test Checkout",
                source_reference=checkout.id,
                timestamp=timestamp,
            )
        customer = database.get(StripeCustomer, attempt.user_id)
        if customer is None:
            database.add(
                StripeCustomer(
                    user_id=attempt.user_id,
                    customer_id=checkout.customer_id or "",
                    created_at=timestamp,
                )
            )
        attempt.status = "completed"
        attempt.updated_at = timestamp
        event.state = "processed"
        event.processed_at = timestamp
        event.result_code = "premium_granted" if existing_grant is None else "already_granted"
        database.commit()

    def _validate(
        self,
        database: Session,
        event: PaymentEvent,
        checkout: CheckoutSnapshot,
    ) -> tuple[str | None, CheckoutAttempt | None]:
        if checkout.id != event.object_id or checkout.livemode:
            return "checkout_mode_or_id_mismatch", None
        attempt_id = checkout.metadata.get("attempt_id")
        user_id = checkout.metadata.get("user_id")
        if not attempt_id or not user_id:
            return "checkout_metadata_missing", None
        attempt = database.get(CheckoutAttempt, attempt_id)
        if (
            attempt is None
            or attempt.user_id != user_id
            or attempt.checkout_session_id != checkout.id
        ):
            return "checkout_attempt_mismatch", None
        if event.event_type == "checkout.session.expired":
            return None, attempt
        if checkout.payment_status != "paid" or checkout.customer_id is None:
            return "checkout_not_paid", None
        if (
            checkout.amount_total != self._settings.premium_unit_amount
            or checkout.currency != self._settings.premium_currency
            or not checkout.lines_complete
            or len(checkout.lines) != 1
            or checkout.lines[0].price_id != self._settings.stripe_price_id
            or checkout.lines[0].quantity != 1
        ):
            return "checkout_product_mismatch", None
        customer = database.get(StripeCustomer, attempt.user_id)
        if customer is not None and customer.customer_id != checkout.customer_id:
            return "checkout_customer_mismatch", None
        conflicting_customer = database.scalar(
            select(StripeCustomer).where(StripeCustomer.customer_id == checkout.customer_id)
        )
        if conflicting_customer is not None and conflicting_customer.user_id != attempt.user_id:
            return "checkout_customer_conflict", None
        return None, attempt

    @staticmethod
    def _reject(
        database: Session,
        event: PaymentEvent,
        result_code: str,
    ) -> None:
        event.state = "rejected"
        event.processed_at = now_epoch()
        event.result_code = result_code
        database.commit()

    def _reject_by_id(self, event_id: str, result_code: str) -> None:
        with self._factory() as database:
            begin_immediate(database)
            event = database.get(PaymentEvent, event_id)
            if event is not None and event.state == "processing":
                self._reject(database, event, result_code)
            else:
                database.rollback()

    @staticmethod
    def _retry(database: Session, event_id: str) -> None:
        begin_immediate(database)
        event = database.get(PaymentEvent, event_id)
        if event is not None and event.state == "processing":
            event.state = "pending"
            event.claimed_at = None
            event.result_code = "provider_unavailable"
        database.commit()
