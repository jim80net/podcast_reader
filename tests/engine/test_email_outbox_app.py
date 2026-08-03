from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from podcast_reader.engine import library
from podcast_reader.engine.app import create_app
from podcast_reader.engine.email_outbox import EmailOutboxManager
from podcast_reader.engine.jobs import JobStore
from podcast_reader.engine.settings import load_engine_state
from podcast_reader.engine.subscription_store import SubscriptionStore
from podcast_reader.types import LibraryEntry, PipelineResult

if TYPE_CHECKING:
    from pathlib import Path

    from podcast_reader.types import JobRecord

SUBJECT = "usr_email_api"
ACTION_ID = "act_AAAAAAAAAAAAAAAAAAAAAAAA"
DELIVERY_ID = "del_BBBBBBBBBBBBBBBBBBBBBBBB"


def _unused_runner(_record: JobRecord, _on_event: object) -> PipelineResult:
    return PipelineResult(json_path="", chapters_path=None, html_path="", title="unused")


def _capability(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "schema_version": 1,
        "subject": SUBJECT,
        "entitlement_revision": 7,
        "flags_revision": 12,
        "transcript_email": True,
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
    }
    body.update(overrides)
    return body


def _seed_library(library_dir: Path, source: str = "https://example.com/email-api.mp3") -> str:
    source_id = library.source_identity(source)
    directory = library.entry_dir(library_dir, source_id)
    directory.mkdir(parents=True)
    transcript = directory / "episode.json"
    transcript.write_text(
        json.dumps({"segments": [{"start": 0, "text": "API fixture"}]}), encoding="utf-8"
    )
    library.add_entry(
        library_dir,
        LibraryEntry(
            source_id=source_id,
            source=source,
            title="API episode",
            html_path=str(directory / "episode.html"),
            created_at=1_700_000_000.0,
        ),
    )
    return source_id


def test_email_routes_are_bearer_gated_exact_and_round_trip(tmp_path: Path) -> None:
    job_store = JobStore(tmp_path, _unused_runner)
    subscription_store = SubscriptionStore(tmp_path)
    library_dir = tmp_path / "library"
    manager = EmailOutboxManager(subscription_store, library_dir=lambda: library_dir)
    client = TestClient(create_app(tmp_path, job_store, email_outbox_manager=manager))
    headers = {"Authorization": f"Bearer {load_engine_state(tmp_path)['token']}"}
    source_id = _seed_library(library_dir)
    try:
        capability_path = "/v1/email/online-capability"
        assert client.put(capability_path, json=_capability()).status_code == 401
        assert client.put(capability_path, json=_capability(), headers=headers).status_code == 204

        valid_capability = json.dumps(_capability()).encode()
        oversized = client.put(
            capability_path,
            content=valid_capability + (b" " * (4097 - len(valid_capability))),
            headers={**headers, "Content-Type": "application/json"},
        )
        assert oversized.status_code == 400
        assert not manager.is_available()
        assert client.put(capability_path, json=_capability(), headers=headers).status_code == 204

        rejected = client.put(
            capability_path,
            json=_capability(unexpected="reject-me"),
            headers=headers,
        )
        assert rejected.status_code == 400
        denied = client.post(
            "/v1/email-outbox/manual",
            json={"schema_version": 1, "action_id": ACTION_ID, "source_id": source_id},
            headers=headers,
        )
        assert denied.status_code == 409
        assert denied.json() == {"detail": "premium_feature_unavailable"}

        assert client.put(capability_path, json=_capability(), headers=headers).status_code == 204
        malformed = client.post(
            "/v1/email-outbox/manual",
            json={
                "schema_version": 1,
                "action_id": ACTION_ID,
                "source_id": source_id,
                "extra": "secret-value",
            },
            headers=headers,
        )
        assert malformed.status_code == 422
        assert "secret-value" not in malformed.text

        created = client.post(
            "/v1/email-outbox/manual",
            json={"schema_version": 1, "action_id": ACTION_ID, "source_id": source_id},
            headers=headers,
        )
        assert created.status_code == 201
        other_source_id = _seed_library(library_dir, "https://example.com/email-api-other.mp3")
        conflict = client.post(
            "/v1/email-outbox/manual",
            json={
                "schema_version": 1,
                "action_id": ACTION_ID,
                "source_id": other_source_id,
            },
            headers=headers,
        )
        assert conflict.status_code == 409
        assert conflict.json() == {"detail": "idempotency_conflict"}
        claim = client.post("/v1/email-outbox/claim", headers=headers)
        assert claim.status_code == 200
        payload = claim.json()
        assert payload["transcript_text"] == "00:00 API fixture\n"

        completed = client.post(
            "/v1/email-outbox/complete",
            json={
                "schema_version": 1,
                "client_delivery_id": payload["client_delivery_id"],
                "claim_generation": payload["claim_generation"],
                "delivery_id": DELIVERY_ID,
                "delivered_at": datetime.now(timezone.utc).isoformat(),
            },
            headers=headers,
        )
        assert completed.status_code == 200
        assert completed.json()["state"] == "delivered"
        listing = client.get("/v1/email-outbox", headers=headers)
        assert listing.status_code == 200
        assert listing.json() == [completed.json()]
        assert (
            client.put(
                capability_path,
                json=_capability(subject="usr_other_api"),
                headers=headers,
            ).status_code
            == 204
        )
        assert client.get("/v1/email-outbox", headers=headers).json() == []
        assert client.post("/v1/email-outbox/claim", headers=headers).status_code == 204
    finally:
        subscription_store.close()
