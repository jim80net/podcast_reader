from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "src"))

from podcast_reader.engine import library  # type: ignore[import-untyped]  # noqa: E402
from podcast_reader.engine.email_outbox import (  # type: ignore[import-untyped]  # noqa: E402
    EmailCapabilitySnapshot,
    EmailFeatureUnavailableError,
    EmailOutboxManager,
)
from podcast_reader.engine.jobs import JobStore  # type: ignore[import-untyped]  # noqa: E402
from podcast_reader.engine.subscription_feed import (  # type: ignore[import-untyped]  # noqa: E402
    FeedResponse,
)
from podcast_reader.engine.subscription_store import (  # type: ignore[import-untyped]  # noqa: E402
    SubscriptionStore,
)
from podcast_reader.engine.subscriptions import (  # type: ignore[import-untyped]  # noqa: E402
    OnlineCapabilitySnapshot,
    SubscriptionManager,
)
from podcast_reader.types import (  # type: ignore[import-untyped]  # noqa: E402
    LibraryEntry,
    PipelineEvent,
    PipelineResult,
)

from podcast_reader_premium.entitlements import apply_entitlement_event  # noqa: E402
from podcast_reader_premium.models import EmailDeliveryReceipt, FeatureFlag  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable

    from podcast_reader.types import JobRecord


SUBJECT = "usr_email_acceptance"
BASELINE_FEED = b"""
<rss><channel><title>Acceptance Show</title>
  <item><guid>old</guid><title>Old episode</title>
    <enclosure type="audio/mpeg" url="https://93.184.216.34/old.mp3"/></item>
</channel></rss>
"""
UPDATED_FEED = b"""
<rss><channel><title>Acceptance Show</title>
  <item><guid>new</guid><title>Acceptance episode</title>
    <enclosure type="audio/mpeg" url="https://93.184.216.34/new.mp3"/></item>
  <item><guid>old</guid><title>Old episode</title>
    <enclosure type="audio/mpeg" url="https://93.184.216.34/old.mp3"/></item>
</channel></rss>
"""
TRANSCRIPT = "A private acceptance transcript."


@dataclass
class Clock:
    now: datetime

    def __call__(self) -> datetime:
        return self.now


class FeedFetcher:
    def __init__(self) -> None:
        self.responses = [
            FeedResponse(200, "https://93.184.216.34/show.xml", BASELINE_FEED, '"v1"', None)
        ]

    def fetch(
        self,
        _url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        should_continue: Callable[[], bool] = lambda: True,
    ) -> FeedResponse:
        del etag, last_modified
        assert should_continue()
        return self.responses.pop(0)


def _app(client: TestClient) -> Any:
    return client.app


def _premium_bearer(
    client: TestClient, browser_auth: dict[str, str], user_id: str
) -> dict[str, str]:
    with Session(_app(client).state.engine) as database:
        apply_entitlement_event(
            database,
            user_id=user_id,
            event_type="override_set",
            tier="premium",
            actor_user_id=None,
            reason="email end-to-end acceptance",
        )
        flag = database.get(FeatureFlag, "transcript_email")
        assert flag is not None
        flag.audience = "premium"
        flag.revision = 1
        database.commit()

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


def _capabilities(clock: Clock) -> tuple[OnlineCapabilitySnapshot, EmailCapabilitySnapshot]:
    expires_at = (clock.now + timedelta(minutes=5)).isoformat()
    return (
        OnlineCapabilitySnapshot(
            schema_version=1,
            subject=SUBJECT,
            entitlement_revision=7,
            flags_revision=12,
            podcast_subscriptions=True,
            expires_at=expires_at,
        ),
        EmailCapabilitySnapshot(
            schema_version=1,
            subject=SUBJECT,
            entitlement_revision=7,
            flags_revision=12,
            transcript_email=True,
            expires_at=expires_at,
        ),
    )


def _runner(
    library_dir: Path,
) -> Callable[[JobRecord, Callable[[PipelineEvent], None]], PipelineResult]:
    def run(record: JobRecord, on_event: Callable[[PipelineEvent], None]) -> PipelineResult:
        source_id = library.source_identity(record["source"])
        directory = library.entry_dir(library_dir, source_id)
        directory.mkdir(parents=True, exist_ok=True)
        transcript_path = directory / "episode.json"
        html_path = directory / "episode.html"
        transcript_path.write_text(
            json.dumps({"segments": [{"start": 0, "text": TRANSCRIPT}]}), encoding="utf-8"
        )
        html_path.write_text(f"<p>{TRANSCRIPT}</p>", encoding="utf-8")
        library.add_entry(
            library_dir,
            LibraryEntry(
                source_id=source_id,
                source=record["source"],
                title="Acceptance episode",
                html_path=str(html_path),
                created_at=time.time(),
            ),
        )
        on_event(PipelineEvent(kind="job_done", step=None, message="Done", data={}))
        return PipelineResult(
            json_path=str(transcript_path),
            chapters_path=None,
            html_path=str(html_path),
            title="Acceptance episode",
        )

    return run


def _deliver(
    client: TestClient, bearer: dict[str, str], outbox: EmailOutboxManager
) -> dict[str, object]:
    claim = outbox.claim()
    assert claim is not None
    generation = claim["claim_generation"]
    request = dict(claim)
    del request["claim_generation"]
    response = client.post("/v1/email-deliveries", json=request, headers=bearer)
    assert response.status_code == 200, response.text
    delivered = cast("dict[str, object]", response.json())
    outbox.complete(
        client_delivery_id=claim["client_delivery_id"],
        claim_generation=generation,
        delivery_id=cast("str", delivered["delivery_id"]),
        delivered_at=cast("str", delivered["delivered_at"]),
    )
    return delivered


def test_subscription_and_manual_delivery_cross_real_outbox_relay_and_maildir(
    client: TestClient,
    account: dict[str, object],
    browser_auth: dict[str, str],
    tmp_path: Path,
) -> None:
    """P3 #122 headline and secondary flows cross every frozen boundary."""
    bearer = _premium_bearer(client, browser_auth, cast("str", account["id"]))
    clock = Clock(datetime(2026, 8, 3, tzinfo=UTC))
    engine_dir = tmp_path / "engine"
    library_dir = tmp_path / "library"
    store = SubscriptionStore(engine_dir)
    jobs = JobStore(engine_dir, _runner(library_dir))
    outbox = EmailOutboxManager(store, library_dir=lambda: library_dir, clock=clock)
    fetcher = FeedFetcher()
    subscriptions = SubscriptionManager(
        store, fetcher=fetcher, clock=clock, job_store=jobs, email_outbox=outbox
    )
    try:
        subscription_capability, email_capability = _capabilities(clock)
        subscriptions.update_capability(subscription_capability)
        subscription = subscriptions.create_subscription("https://93.184.216.34/show.xml")
        outbox.update_capability(email_capability)
        outbox.set_subscription_preference(subscription["id"], subject=SUBJECT, enabled=True)

        fetcher.responses.append(
            FeedResponse(200, subscription["feed_url"], UPDATED_FEED, '"v2"', None)
        )
        clock.now += timedelta(minutes=1)
        subscriptions.poll_subscription(subscription["id"])
        jobs.start_worker()
        deadline = time.monotonic() + 3
        while jobs.list_jobs()[0]["state"] != "done" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert jobs.list_jobs()[0]["state"] == "done"
        subscriptions._reconcile_jobs()

        automatic = _deliver(client, bearer, outbox)
        assert automatic["state"] == "delivered"
        assert outbox.list_status()[0]["state"] == "delivered"

        source_id = library.source_identity("https://93.184.216.34/new.mp3")
        first_manual = outbox.create_manual(
            action_id="act_AAAAAAAAAAAAAAAAAAAAAAAA", source_id=source_id
        )
        repeated_manual = outbox.create_manual(
            action_id="act_AAAAAAAAAAAAAAAAAAAAAAAA", source_id=source_id
        )
        assert repeated_manual["client_delivery_id"] == first_manual["client_delivery_id"]
        manual = _deliver(client, bearer, outbox)
        assert manual["state"] == "delivered"

        messages = sorted(_app(client).state.settings.email_maildir_path.glob("new/*.eml"))
        assert len(messages) == 2
        parsed = [
            BytesParser(policy=policy.default).parsebytes(path.read_bytes()) for path in messages
        ]
        assert {message["To"] for message in parsed} == {"dev-mailbox@podcast-reader.invalid"}
        account_email = cast("str", account["email"]).casefold()
        assert all(
            account_email not in path.read_text(encoding="utf-8").casefold() for path in messages
        )
        bodies = [message.get_body() for message in parsed]
        assert all(body is not None and TRANSCRIPT in body.get_content() for body in bodies)
        assert {item["consent_kind"] for item in outbox.list_status()} == {
            "subscription_completion",
            "manual",
        }

        premium_database = _app(client).state.settings.database_path
        persisted = premium_database.read_bytes()
        assert TRANSCRIPT.encode() not in persisted
        assert b"Acceptance episode" not in persisted
        with Session(_app(client).state.engine) as database:
            receipts = database.scalars(select(EmailDeliveryReceipt)).all()
            receipt_rows = [
                {
                    column.name: getattr(receipt, column.name)
                    for column in EmailDeliveryReceipt.__table__.columns
                }
                for receipt in receipts
            ]
        assert account_email not in json.dumps(receipt_rows, default=str).casefold()
    finally:
        jobs.shutdown()
        subscriptions.shutdown()


def test_failure_and_privacy_matrix_fails_closed_without_extra_mail(
    client: TestClient,
    account: dict[str, object],
    browser_auth: dict[str, str],
    tmp_path: Path,
) -> None:
    bearer = _premium_bearer(client, browser_auth, cast("str", account["id"]))
    request = json.loads(
        (Path(__file__).parents[1] / "contracts/v1/email/request-manual.json").read_text()
    )
    delivered = client.post("/v1/email-deliveries", json=request, headers=bearer)
    assert delivered.status_code == 200
    maildir = _app(client).state.settings.email_maildir_path
    assert len(list(maildir.glob("new/*.eml"))) == 1

    replay = client.post("/v1/email-deliveries", json=request, headers=bearer)
    assert replay.json() == delivered.json()
    changed = {**request, "title": "Changed replay"}
    conflict = client.post("/v1/email-deliveries", json=changed, headers=bearer)
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "idempotency_conflict"

    with Session(_app(client).state.engine) as database:
        flag = database.get(FeatureFlag, "transcript_email")
        assert flag is not None
        flag.audience = "off"
        flag.revision += 1
        database.commit()
    denied = client.post(
        "/v1/email-deliveries",
        json={**request, "client_delivery_id": "eml_ZZZZZZZZZZZZZZZZZZZZZZZZ"},
        headers=bearer,
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "premium_feature_unavailable"
    assert len(list(maildir.glob("new/*.eml"))) == 1

    clock = Clock(datetime(2026, 8, 3, tzinfo=UTC))
    store = SubscriptionStore(tmp_path / "subject-engine")
    outbox = EmailOutboxManager(store, library_dir=lambda: tmp_path / "absent", clock=clock)
    _, capability = _capabilities(clock)
    outbox.update_capability(capability)
    outbox.clear_capability()
    try:
        with pytest.raises(EmailFeatureUnavailableError):
            outbox.claim()
    finally:
        store.close()
