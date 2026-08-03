from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from podcast_reader_premium.ads import inventory_for_slot
from podcast_reader_premium.contracts import AdInventoryV1
from podcast_reader_premium.entitlements import apply_entitlement_event
from podcast_reader_premium.models import (
    AdConfig,
    EntitlementProjection,
    FeatureFlag,
    HouseAd,
    User,
)

CONTRACTS = Path(__file__).parents[1] / "contracts" / "v1" / "ads"


def _app(client: TestClient) -> Any:
    return cast("Any", client.app)


def _fixture(name: str) -> dict[str, object]:
    return cast("dict[str, object]", json.loads((CONTRACTS / name).read_text()))


def _bearer(client: TestClient, browser_auth: dict[str, str]) -> dict[str, str]:
    started = client.post("/v1/device-authorizations", json={"client": "android"}).json()
    approved = client.post(
        "/v1/device-authorizations/approve",
        json={"user_code": started["user_code"]},
        headers=browser_auth,
    )
    assert approved.status_code == 204
    issued = client.post(
        "/v1/device-authorizations/token", json={"device_code": started["device_code"]}
    )
    assert issued.status_code == 200
    return {"Authorization": f"Bearer {issued.json()['access_token']}"}


def _enable_slot(database: Session, slot: str) -> None:
    flag = database.get(FeatureFlag, "ad_system")
    config = database.get(AdConfig, 1)
    assert flag is not None and config is not None
    flag.audience = "free"
    flag.revision = 1
    config.enabled = True
    config.enabled_slots_json = json.dumps([slot])
    config.revision = 2


def _add_fixture_ad(database: Session, fixture: dict[str, object], *, timestamp: int) -> None:
    item = cast("dict[str, object]", cast("list[object]", fixture["items"])[0])
    database.add(
        HouseAd(
            id=cast("str", item["id"]),
            status="active",
            title=cast("str", item["title"]),
            body=cast("str", item["body"]),
            cta_url=cast("str", item["cta_url"]),
            starts_at=None,
            ends_at=None,
            revision=cast("int", item["revision"]),
            created_at=timestamp,
            updated_at=timestamp,
        )
    )


@pytest.mark.parametrize(
    "name",
    ["eligible-library.json", "eligible-reader.json", "hostile-text.json"],
)
def test_valid_inventory_fixtures_are_strict_v1_documents(name: str) -> None:
    value = AdInventoryV1.model_validate_json((CONTRACTS / name).read_text())
    assert value.schema_version == 1
    assert 1 <= len(value.items) <= 10


@pytest.mark.parametrize("name", ["malformed.json", "forward-additive.json"])
def test_server_contract_rejects_malformed_or_unowned_additive_output(name: str) -> None:
    with pytest.raises(ValidationError):
        AdInventoryV1.model_validate_json((CONTRACTS / name).read_text())


def test_no_content_fixture_freezes_an_empty_204() -> None:
    assert _fixture("no-content.json") == {
        "schema_version": 1,
        "status": 204,
        "body": None,
        "meaning": "authenticated but ineligible, disabled, unscheduled, or empty",
    }


def test_inventory_contract_rejects_raw_url_whitespace() -> None:
    fixture = _fixture("eligible-library.json")
    item = cast("dict[str, object]", cast("list[object]", fixture["items"])[0])
    item["cta_url"] = "https://example.com/safe\nignored"
    with pytest.raises(ValidationError):
        AdInventoryV1.model_validate_json(json.dumps(fixture))


@pytest.mark.parametrize(
    ("slot", "fixture_name"),
    [("library", "eligible-library.json"), ("reader", "eligible-reader.json")],
)
def test_live_inventory_matches_backend_owned_fixture(
    client: TestClient,
    account: dict[str, object],
    browser_auth: dict[str, str],
    slot: str,
    fixture_name: str,
) -> None:
    fixture = _fixture(fixture_name)
    with Session(_app(client).state.engine) as database:
        _enable_slot(database, slot)
        _add_fixture_ad(database, fixture, timestamp=int(datetime.now(UTC).timestamp()))
        database.commit()

    response = client.get(f"/v1/ads/inventory/{slot}", headers=_bearer(client, browser_auth))
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    fixture["inventory_revision"] = payload["inventory_revision"]
    fixture["expires_at"] = payload["expires_at"]
    assert payload == fixture
    assert "subject" not in response.text
    assert cast("str", account["id"]) not in response.text


def test_inventory_is_bearer_only_and_ineligible_states_are_empty(
    client: TestClient,
    account: dict[str, object],
    browser_auth: dict[str, str],
) -> None:
    assert client.get("/v1/ads/inventory/library").status_code == 401
    bearer = _bearer(client, browser_auth)
    default_off = client.get("/v1/ads/inventory/library", headers=bearer)
    assert default_off.status_code == 204 and default_off.content == b""

    with Session(_app(client).state.engine) as database:
        _enable_slot(database, "library")
        fixture = _fixture("eligible-library.json")
        _add_fixture_ad(database, fixture, timestamp=int(datetime.now(UTC).timestamp()))
        database.commit()
    assert client.get("/v1/ads/inventory/library", headers=bearer).status_code == 200
    assert client.get("/v1/ads/inventory/reader", headers=bearer).status_code == 204

    with Session(_app(client).state.engine) as database:
        apply_entitlement_event(
            database,
            user_id=cast("str", account["id"]),
            event_type="override_set",
            tier="premium",
            actor_user_id=None,
            reason="inventory premium suppression test",
        )
        database.commit()
    premium = client.get("/v1/ads/inventory/library", headers=bearer)
    assert premium.status_code == 204 and premium.content == b""

    unknown = client.get("/v1/ads/inventory/not-a-slot", headers=bearer)
    assert unknown.status_code == 404
    assert unknown.json()["code"] == "invalid_slot"


def test_inventory_route_fails_closed_when_projection_disagrees_with_ledger(
    client: TestClient,
    account: dict[str, object],
    browser_auth: dict[str, str],
) -> None:
    bearer = _bearer(client, browser_auth)
    with Session(_app(client).state.engine) as database:
        projection = database.get(EntitlementProjection, account["id"])
        assert projection is not None
        projection.effective_tier = "premium"
        database.commit()

    response = client.get("/v1/ads/inventory/library", headers=bearer)

    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"


def test_inventory_is_bounded_ordered_scheduled_and_revision_sensitive(
    client: TestClient, account: dict[str, object]
) -> None:
    now = datetime(2026, 8, 3, tzinfo=UTC)
    timestamp = int(now.timestamp())
    with Session(_app(client).state.engine) as database:
        _enable_slot(database, "mobile_home")
        for index in range(12):
            database.add(
                HouseAd(
                    id=f"ad_order_{index:02d}",
                    status="active",
                    title=f"Title {index}",
                    body=f"Body {index}",
                    cta_url=f"https://example.com/{index}",
                    starts_at=None if index == 0 else timestamp - 100 + index,
                    ends_at=timestamp + 60 if index == 0 else None,
                    revision=1,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )
        database.add(
            HouseAd(
                id="ad_future",
                status="active",
                title="Future",
                body="Not active yet",
                cta_url="https://example.com/future",
                starts_at=timestamp + 1,
                ends_at=None,
                revision=1,
                created_at=timestamp,
                updated_at=timestamp,
            )
        )
        database.commit()

        first = inventory_for_slot(database, cast("str", account["id"]), "mobile_home", at=now)
        assert first is not None
        assert len(first.items) == 10
        assert first.items[0].id == "ad_order_00"
        assert all(item.id != "ad_future" for item in first.items)
        assert first.expires_at == now + timedelta(seconds=60)

        first_item = database.get(HouseAd, first.items[0].id)
        assert first_item is not None
        first_item.revision += 1
        database.flush()
        changed = inventory_for_slot(database, cast("str", account["id"]), "mobile_home", at=now)
        assert changed is not None
        assert changed.inventory_revision != first.inventory_revision


def test_ad_system_admin_and_startup_accept_only_off_or_free(
    client: TestClient,
    account: dict[str, object],
) -> None:
    with Session(_app(client).state.engine) as database:
        user = database.get(User, cast("str", account["id"]))
        assert user is not None
        user.role = "admin"
        database.commit()
    login = client.post(
        "/v1/browser-sessions",
        json={"email": account["email"], "password": "correct horse battery"},
    )
    csrf = cast("str", login.json()["csrf_token"])
    page = client.get("/admin/flags")
    ad_form = page.text.split('action="/admin/flags/ad_system"', 1)[1].split("</form>", 1)[0]
    assert 'value="all"' not in ad_form
    assert 'value="premium"' not in ad_form

    rejected = client.post(
        "/admin/flags/ad_system",
        data={
            "audience": "all",
            "config_json": "{}",
            "reason": "must be rejected",
            "csrf_token": csrf,
        },
        headers={"Origin": "https://premium.test"},
    )
    assert rejected.status_code == 422


def test_house_ad_admin_rejects_raw_url_whitespace(
    client: TestClient,
    account: dict[str, object],
) -> None:
    with Session(_app(client).state.engine) as database:
        user = database.get(User, cast("str", account["id"]))
        assert user is not None
        user.role = "admin"
        database.commit()
    login = client.post(
        "/v1/browser-sessions",
        json={"email": account["email"], "password": "correct horse battery"},
    )
    rejected = client.post(
        "/admin/ads/house",
        data={
            "title": "Bad CTA",
            "body": "Must not persist",
            "cta_url": "https://example.com/safe\nignored",
            "status": "active",
            "reason": "raw whitespace rejection",
            "csrf_token": login.json()["csrf_token"],
        },
        headers={"Origin": "https://premium.test"},
    )
    assert rejected.status_code == 422
    with Session(_app(client).state.engine) as database:
        assert database.scalar(select(HouseAd)) is None


def test_migration_normalizes_existing_ad_system_all_to_free(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "migration.sqlite3"
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("prepend_sys_path", str(root / "src"))
    config.attributes["database_path"] = database_path
    command.upgrade(config, "0003_test_buy")
    engine = create_engine(f"sqlite:///{database_path}")
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE feature_flags SET audience = 'all' WHERE key = 'ad_system'")
        )
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database_path}")
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT audience FROM feature_flags WHERE key = 'ad_system'")
            ).scalar_one()
            == "free"
        )
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0005_email_delivery_relay"
        )
    engine.dispose()
