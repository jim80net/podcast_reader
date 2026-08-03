from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from email.header import Header
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .contracts import EmailDeliveryRequestV1, EmailDeliveryV1
from .db import begin_immediate
from .entitlements import evaluate_entitlements
from .models import EmailDeliveryReceipt
from .security import now_epoch, record_id

PROCESSING_LEASE_SECONDS = 30
DEV_SENDER = "transcripts@podcast-reader.invalid"
DEV_RECIPIENT = "dev-mailbox@podcast-reader.invalid"


class EmailDeliveryError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True)
class DevMaildirSink:
    root: Path

    def prepare(self) -> None:
        if not self.root.is_absolute():
            raise RuntimeError("DEV Maildir path must be absolute")
        source_checkout = Path(__file__).resolve().parents[2]
        if (source_checkout / "pyproject.toml").is_file() and self.root.resolve(
            strict=False
        ).is_relative_to(source_checkout):
            raise RuntimeError("DEV Maildir must be outside the source checkout")
        for parent in (self.root, *self.root.parents):
            if parent.exists() and parent.is_symlink():
                raise RuntimeError("DEV Maildir path must not contain symlinks")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        for name in ("cur", "new", "tmp"):
            child = self.root / name
            child.mkdir(mode=0o700, exist_ok=True)
            if child.is_symlink() or not child.is_dir():
                raise RuntimeError("DEV Maildir structure is unsafe")
            child.chmod(0o700)
        self.root.chmod(0o700)

    def deliver(
        self, *, delivery_id: str, title: str, transcript_text: str, timestamp: int
    ) -> None:
        self.prepare()
        destination = self.root / "new" / f"{delivery_id}.eml"
        if destination.exists():
            if destination.is_symlink() or not destination.is_file():
                raise RuntimeError("DEV Maildir destination is unsafe")
            return
        date = datetime.fromtimestamp(timestamp, UTC).strftime("%a, %d %b %Y %H:%M:%S +0000")
        subject = Header(title, "utf-8", maxlinelen=76).encode()
        body = transcript_text.replace("\n", "\r\n").encode("utf-8")
        message = (
            f"From: {DEV_SENDER}\r\n"
            f"To: {DEV_RECIPIENT}\r\n"
            f"Subject: {subject}\r\n"
            f"Date: {date}\r\n"
            f"Message-ID: <{delivery_id}@podcast-reader.invalid>\r\n"
            f"X-Podcast-Reader-Delivery-ID: {delivery_id}\r\n"
            "MIME-Version: 1.0\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n"
            "Content-Transfer-Encoding: 8bit\r\n\r\n"
        ).encode("ascii") + body
        temporary = self.root / "tmp" / f"{delivery_id}.{os.getpid()}.tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(message)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
            os.replace(temporary, destination)
            directory = os.open(self.root / "new", os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if temporary.exists():
                temporary.unlink()


class EmailRelay:
    def __init__(self, sink: DevMaildirSink, hmac_key: bytes) -> None:
        if len(hmac_key) < 32:
            raise RuntimeError("email delivery HMAC key must contain at least 32 bytes")
        sink.prepare()
        self._sink = sink
        self._hmac_key = hmac_key

    def _payload_hmac(self, payload: EmailDeliveryRequestV1) -> str:
        canonical = json.dumps(
            payload.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hmac.new(self._hmac_key, canonical, hashlib.sha256).hexdigest()

    @staticmethod
    def _response(receipt: EmailDeliveryReceipt) -> EmailDeliveryV1:
        if receipt.delivered_at is None:
            raise RuntimeError("delivered receipt has no timestamp")
        return EmailDeliveryV1(
            delivery_id=receipt.id,
            client_delivery_id=receipt.client_delivery_id,
            state="delivered",
            destination="dev_maildir",
            delivered_at=datetime.fromtimestamp(receipt.delivered_at, UTC),
        )

    def deliver(
        self, database: Session, user_id: str, payload: EmailDeliveryRequestV1
    ) -> EmailDeliveryV1:
        entitlement = evaluate_entitlements(database, user_id)
        if not entitlement.capabilities.transcript_email:
            raise EmailDeliveryError(
                403, "premium_feature_unavailable", "Transcript email is unavailable"
            )
        payload_hmac = self._payload_hmac(payload)
        timestamp = now_epoch()
        begin_immediate(database)
        receipt = database.scalar(
            select(EmailDeliveryReceipt).where(
                EmailDeliveryReceipt.user_id == user_id,
                EmailDeliveryReceipt.client_delivery_id == payload.client_delivery_id,
            )
        )
        if receipt is not None:
            if not hmac.compare_digest(receipt.payload_hmac, payload_hmac):
                database.rollback()
                raise EmailDeliveryError(
                    409,
                    "idempotency_conflict",
                    "Delivery identifier conflicts with prior content",
                )
            if receipt.state == "delivered":
                database.rollback()
                return self._response(receipt)
            if (
                receipt.state == "processing"
                and timestamp - receipt.updated_at < PROCESSING_LEASE_SECONDS
            ):
                database.rollback()
                raise EmailDeliveryError(
                    503, "delivery_unavailable", "Transcript email could not be delivered"
                )
            receipt.state = "processing"
            receipt.error_code = None
            receipt.attempts += 1
            receipt.updated_at = timestamp
        else:
            receipt = EmailDeliveryReceipt(
                id=record_id("del"),
                user_id=user_id,
                client_delivery_id=payload.client_delivery_id,
                consent_kind=payload.consent_kind,
                content_bytes=len(payload.transcript_text.encode("utf-8")),
                payload_hmac=payload_hmac,
                sink="dev_maildir",
                state="processing",
                error_code=None,
                attempts=1,
                created_at=timestamp,
                updated_at=timestamp,
                delivered_at=None,
            )
            database.add(receipt)
        database.commit()
        try:
            self._sink.deliver(
                delivery_id=receipt.id,
                title=payload.title,
                transcript_text=payload.transcript_text,
                timestamp=timestamp,
            )
        except Exception as exc:
            database.rollback()
            begin_immediate(database)
            failed = database.get(EmailDeliveryReceipt, receipt.id)
            if failed is not None:
                failed.state = "failed"
                failed.error_code = "delivery_unavailable"
                failed.updated_at = now_epoch()
                database.commit()
            raise EmailDeliveryError(
                503, "delivery_unavailable", "Transcript email could not be delivered"
            ) from exc
        database.rollback()
        begin_immediate(database)
        delivered = database.get(EmailDeliveryReceipt, receipt.id)
        if delivered is None:
            database.rollback()
            raise EmailDeliveryError(
                503, "delivery_unavailable", "Transcript email could not be delivered"
            )
        delivered.state = "delivered"
        delivered.error_code = None
        delivered.updated_at = timestamp
        delivered.delivered_at = timestamp
        database.commit()
        return self._response(delivered)
