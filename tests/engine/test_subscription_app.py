from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from podcast_reader.engine.app import create_app
from podcast_reader.engine.jobs import JobStore
from podcast_reader.engine.settings import load_engine_state
from podcast_reader.engine.subscription_feed import FeedResponse
from podcast_reader.engine.subscription_store import SubscriptionStore
from podcast_reader.engine.subscriptions import SubscriptionManager
from podcast_reader.types import PipelineResult

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from podcast_reader.types import JobRecord

_FEED = b"""
<rss><channel><title>Fixture Show</title>
  <item><guid>old</guid><title>Old</title>
    <enclosure type="audio/mpeg" url="https://93.184.216.34/old.mp3"/></item>
</channel></rss>
"""


def _unused_runner(_record: JobRecord, _on_event: object) -> PipelineResult:
    return PipelineResult(json_path="", chapters_path=None, html_path="", title="unused")


class _Fetcher:
    def fetch(
        self,
        _url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        should_continue: Callable[[], bool] = lambda: True,
    ) -> FeedResponse:
        assert should_continue()
        return FeedResponse(
            status=200,
            final_url="https://93.184.216.34/show.xml",
            content=_FEED,
            etag='"v1"',
            last_modified=None,
        )


def _capability(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "schema_version": 1,
        "subject": "usr_test_01",
        "entitlement_revision": 7,
        "flags_revision": 12,
        "podcast_subscriptions": True,
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
    }
    body.update(overrides)
    return body


def test_subscription_routes_are_bearer_gated_and_reads_survive_capability_loss(
    tmp_path: Path,
) -> None:
    job_store = JobStore(tmp_path, _unused_runner)
    manager = SubscriptionManager(SubscriptionStore(tmp_path), fetcher=_Fetcher())
    client = TestClient(create_app(tmp_path, job_store, subscription_manager=manager))
    headers = {"Authorization": f"Bearer {load_engine_state(tmp_path)['token']}"}
    try:
        assert client.get("/v1/subscriptions").status_code == 401
        assert client.put("/v1/online-capabilities", json=_capability()).status_code == 401
        assert (
            client.put("/v1/online-capabilities", json=_capability(), headers=headers).status_code
            == 204
        )
        created = client.post(
            "/v1/subscriptions",
            json={"feed_url": "https://93.184.216.34/show.xml"},
            headers=headers,
        )
        assert created.status_code == 201
        subscription_id = created.json()["id"]

        assert (
            client.put(
                "/v1/online-capabilities",
                json=_capability(podcast_subscriptions=False),
                headers=headers,
            ).status_code
            == 204
        )
        listing = client.get("/v1/subscriptions", headers=headers)
        assert [item["id"] for item in listing.json()] == [subscription_id]
        denied = client.delete(f"/v1/subscriptions/{subscription_id}", headers=headers)
        assert denied.status_code == 409
        assert denied.json() == {"detail": "premium_feature_unavailable"}
    finally:
        manager.shutdown()


def test_invalid_capability_update_clears_previous_live_snapshot(tmp_path: Path) -> None:
    job_store = JobStore(tmp_path, _unused_runner)
    manager = SubscriptionManager(SubscriptionStore(tmp_path), fetcher=_Fetcher())
    client = TestClient(create_app(tmp_path, job_store, subscription_manager=manager))
    headers = {"Authorization": f"Bearer {load_engine_state(tmp_path)['token']}"}
    try:
        assert (
            client.put("/v1/online-capabilities", json=_capability(), headers=headers).status_code
            == 204
        )
        invalid = _capability(unexpected="reject-me")
        response = client.put("/v1/online-capabilities", json=invalid, headers=headers)
        assert response.status_code == 400
        assert response.json() == {"detail": "invalid online capability"}
        denied = client.post(
            "/v1/subscriptions",
            json={"feed_url": "https://93.184.216.34/show.xml"},
            headers=headers,
        )
        assert denied.status_code == 409
        assert denied.json() == {"detail": "premium_feature_unavailable"}
    finally:
        manager.shutdown()
