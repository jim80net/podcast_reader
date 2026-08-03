from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import delete, inspect, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from podcast_reader_premium.cli import _repair_entitlements
from podcast_reader_premium.entitlements import (
    apply_entitlement_event,
    evaluate_entitlements,
    rebuild_projection,
    require_entitlement_configuration,
)
from podcast_reader_premium.models import (
    AdConfig,
    AuditLog,
    BrowserSession,
    DeviceAuthorization,
    EntitlementEvent,
    EntitlementProjection,
    FeatureFlag,
    HouseAd,
    TokenFamily,
    User,
)


def _app(client: TestClient) -> Any:
    return cast("Any", client.app)


def _make_admin(client: TestClient, account: dict[str, object]) -> dict[str, str]:
    with Session(_app(client).state.engine) as database:
        user = database.scalar(select(User).where(User.id == account["id"]))
        assert user is not None
        user.role = "admin"
        database.commit()
    login = client.post(
        "/v1/browser-sessions",
        json={"email": account["email"], "password": "correct horse battery"},
    )
    assert login.status_code == 201
    assert "__Host-pr_csrf=" in login.headers.get_list("set-cookie")[1]
    return {
        "Origin": "https://premium.test",
        "csrf_token": cast("str", login.json()["csrf_token"]),
    }


def _issue_bearer(client: TestClient, auth: dict[str, str]) -> str:
    started = client.post("/v1/device-authorizations", json={"client": "desktop"}).json()
    approved = client.post(
        "/v1/device-authorizations/approve",
        json={"user_code": started["user_code"]},
        headers={"Origin": auth["Origin"], "X-CSRF-Token": auth["csrf_token"]},
    )
    assert approved.status_code == 204
    issued = client.post(
        "/v1/device-authorizations/token", json={"device_code": started["device_code"]}
    )
    assert issued.status_code == 200
    return cast("str", issued.json()["access_token"])


def _admin_post(
    client: TestClient,
    path: str,
    auth: dict[str, str],
    data: dict[str, object],
    *,
    follow_redirects: bool = False,
) -> Response:
    return cast(
        "Response",
        client.post(
            path,
            data=cast("Any", {**data, "csrf_token": auth["csrf_token"]}),
            headers={"Origin": auth["Origin"]},
            follow_redirects=follow_redirects,
        ),
    )


def test_entitlement_endpoint_preserves_frozen_v1_shape_and_changes_etag(
    client: TestClient, account: dict[str, object]
) -> None:
    auth = _make_admin(client, account)
    token = _issue_bearer(client, auth)
    bearer = {"Authorization": f"Bearer {token}"}
    free = client.get("/v1/me/entitlements", headers=bearer)
    assert free.status_code == 200
    assert free.headers["cache-control"] == "no-store"
    free_payload = free.json()
    fixture = __import__("json").loads(
        (Path(__file__).parents[1] / "contracts" / "entitlements-v1-free.json").read_text()
    )
    fixture["subject"] = account["id"]
    fixture["evaluated_at"] = free_payload["evaluated_at"]
    fixture["refresh_after"] = free_payload["refresh_after"]
    assert free_payload == fixture

    override = _admin_post(
        client,
        f"/admin/users/{account['id']}/override",
        auth,
        {"action": "premium", "reason": "support test premium"},
    )
    assert override.status_code == 303
    premium = client.get("/v1/me/entitlements", headers=bearer)
    assert premium.status_code == 200
    assert premium.json()["tier"] == "premium"
    assert premium.json()["entitlement"] == {"source": "admin", "revision": 1}
    assert premium.json()["capabilities"]["ad_policy"] == "none"
    assert premium.json()["capabilities"]["mobile_ad_free"] is True
    assert premium.headers["etag"] != free.headers["etag"]

    cleared = _admin_post(
        client,
        f"/admin/users/{account['id']}/override",
        auth,
        {"action": "clear", "reason": "resume provider truth"},
    )
    assert cleared.status_code == 303
    restored = client.get("/v1/me/entitlements", headers=bearer).json()
    assert restored["tier"] == "free"
    assert restored["entitlement"] == {"source": "none", "revision": 2}


@pytest.mark.parametrize(
    "events, expected",
    [
        (["premium", "free", "clear"], "free"),
        (["free", "premium", "clear"], "free"),
        (["premium", "clear", "free"], "free"),
        (["free", "clear", "premium"], "premium"),
    ],
)
def test_ledger_rebuild_matches_projection_for_override_event_orders(
    client: TestClient,
    account: dict[str, object],
    events: list[str],
    expected: str,
) -> None:
    with Session(_app(client).state.engine) as database:
        for action in events:
            apply_entitlement_event(
                database,
                user_id=cast("str", account["id"]),
                event_type="override_clear" if action == "clear" else "override_set",
                tier=None if action == "clear" else cast("Any", action),
                actor_user_id=None,
                reason="projection property test",
            )
        database.commit()
        evaluate_entitlements(database, cast("str", account["id"]))
        rebuilt = rebuild_projection(database, cast("str", account["id"]))
        assert rebuilt["effective_tier"] == expected
        revisions = database.scalars(
            select(EntitlementEvent.revision)
            .where(EntitlementEvent.user_id == account["id"])
            .order_by(EntitlementEvent.revision)
        ).all()
        assert revisions == list(range(1, len(events) + 1))


def test_flags_cannot_grant_premium_capabilities_to_free_and_house_ads_need_both_gates(
    client: TestClient, account: dict[str, object]
) -> None:
    auth = _make_admin(client, account)
    token = _issue_bearer(client, auth)
    bearer = {"Authorization": f"Bearer {token}"}
    flag = _admin_post(
        client,
        "/admin/flags/topic_corpus",
        auth,
        {"audience": "all", "config_json": "{}", "reason": "matrix regression"},
    )
    assert flag.status_code == 303
    ad_flag = _admin_post(
        client,
        "/admin/flags/ad_system",
        auth,
        {"audience": "free", "config_json": "{}", "reason": "house ads test"},
    )
    assert ad_flag.status_code == 303
    before_config = client.get("/v1/me/entitlements", headers=bearer).json()
    assert before_config["capabilities"]["topic_corpus"] is False
    assert before_config["capabilities"]["ad_policy"] == "none"
    config = _admin_post(
        client,
        "/admin/ads/config",
        auth,
        {"enabled": "on", "slots": ["library", "mobile_home"], "reason": "enable house"},
    )
    assert config.status_code == 303
    after_config = client.get("/v1/me/entitlements", headers=bearer).json()
    assert after_config["capabilities"]["ad_policy"] == "house"
    assert after_config["capabilities"]["topic_corpus"] is False
    assert after_config["flags_revision"] > before_config["flags_revision"]

    unknown = _admin_post(
        client,
        "/admin/flags/third_party_network",
        auth,
        {"audience": "all", "config_json": "{}", "reason": "must fail"},
    )
    assert unknown.status_code == 422
    with Session(_app(client).state.engine) as database:
        assert database.get(FeatureFlag, "third_party_network") is None
        stored = database.get(AdConfig, 1)
        assert stored is not None and stored.source == "house"


def test_admin_requires_role_origin_csrf_and_protects_last_active_admin(
    client: TestClient, account: dict[str, object], browser_auth: dict[str, str]
) -> None:
    assert client.get("/admin/").status_code == 403
    with Session(_app(client).state.engine) as database:
        user = database.get(User, cast("str", account["id"]))
        assert user is not None
        user.role = "admin"
        database.commit()
    admin = _make_admin(client, account)
    page = client.get("/admin/")
    assert page.status_code == 200
    assert "default-src 'none'" in page.headers["content-security-policy"]
    assert page.headers["strict-transport-security"] == "max-age=31536000"
    assert page.headers["x-content-type-options"] == "nosniff"
    assert page.headers["referrer-policy"] == "no-referrer"

    missing_origin = client.post(
        f"/admin/users/{account['id']}/status",
        data={"status": "disabled", "reason": "security test", "csrf_token": admin["csrf_token"]},
    )
    wrong_csrf = client.post(
        f"/admin/users/{account['id']}/status",
        data={"status": "disabled", "reason": "security test", "csrf_token": "x" * 32},
        headers={"Origin": admin["Origin"]},
    )
    last_admin = _admin_post(
        client,
        f"/admin/users/{account['id']}/status",
        admin,
        {"status": "disabled", "reason": "security test"},
    )
    revoke_last = _admin_post(
        client,
        f"/admin/users/{account['id']}/sessions/revoke",
        admin,
        {"reason": "security test"},
    )
    assert missing_origin.status_code == 403
    assert wrong_csrf.status_code == 403
    assert last_admin.status_code == 409
    assert revoke_last.status_code == 409


def test_admin_authentication_failure_redirects_to_sign_in(client: TestClient) -> None:
    response = client.get("/admin/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


def test_admin_prefix_search_treats_sql_wildcards_as_literals(
    client: TestClient, account: dict[str, object]
) -> None:
    _make_admin(client, account)
    for email in ("percent%literal@example.test", "percentXliteral@example.test"):
        created = client.post(
            "/v1/accounts", json={"email": email, "password": "correct horse battery"}
        )
        assert created.status_code == 201

    page = client.get("/admin/", params={"q": "percent%*"})

    assert page.status_code == 200
    assert "percent%literal@example.test" in page.text
    assert "percentXliteral@example.test" not in page.text


def test_admin_active_counts_exclude_expired_credentials(
    client: TestClient, account: dict[str, object]
) -> None:
    auth = _make_admin(client, account)
    _issue_bearer(client, auth)
    with Session(_app(client).state.engine) as database:
        database.add(
            BrowserSession(
                token_digest="e" * 64,
                user_id=cast("str", account["id"]),
                csrf_digest="f" * 64,
                expires_at=1,
                revoked_at=None,
                created_at=0,
            )
        )
        database.add(
            TokenFamily(
                id="fam_expired",
                user_id=cast("str", account["id"]),
                client_kind="desktop",
                expires_at=1,
                revoked_at=None,
                created_at=0,
            )
        )
        database.commit()

    page = client.get(f"/admin/users/{account['id']}")

    assert "<dt>Active browser sessions</dt><dd>1</dd>" in page.text
    assert "<dt>Active token families</dt><dd>1</dd>" in page.text


def test_server_rendered_admin_login_and_device_approval(
    client: TestClient, account: dict[str, object]
) -> None:
    with Session(_app(client).state.engine) as database:
        user = database.get(User, cast("str", account["id"]))
        assert user is not None
        user.role = "admin"
        database.commit()
    login_page = client.get("/admin/login")
    assert login_page.status_code == 200
    assert "Administrator sign in" in login_page.text
    assert "default-src 'none'" in login_page.headers["content-security-policy"]
    wrong_origin = client.post(
        "/admin/login",
        data={"email": cast("str", account["email"]), "password": "correct horse battery"},
        headers={"Origin": "https://attacker.test"},
    )
    assert wrong_origin.status_code == 403
    login = client.post(
        "/admin/login",
        data={"email": cast("str", account["email"]), "password": "correct horse battery"},
        headers={"Origin": "https://premium.test"},
        follow_redirects=False,
    )
    assert login.status_code == 303
    assert login.headers["location"] == "/admin/"
    assert client.get("/admin/").status_code == 200

    started = client.post("/v1/device-authorizations", json={"client": "android"}).json()
    device_page = client.get(f"/device?user_code={started['user_code']}")
    assert device_page.status_code == 200
    assert "Your app never receives your password" not in device_page.text
    assert started["user_code"] in device_page.text
    forged = client.get("/device?approved=true")
    assert "Device approved." not in forged.text
    csrf_token = client.cookies.get("__Host-pr_csrf")
    assert csrf_token
    approved = client.post(
        "/device/approve",
        data={"user_code": started["user_code"], "csrf_token": csrf_token},
        headers={"Origin": "https://premium.test"},
        follow_redirects=False,
    )
    assert approved.status_code == 303
    assert approved.headers["location"] == f"/device?user_code={started['user_code']}"
    confirmed = client.get(approved.headers["location"])
    assert "Device approved." in confirmed.text
    issued = client.post(
        "/v1/device-authorizations/token", json={"device_code": started["device_code"]}
    )
    assert issued.status_code == 200
    with Session(_app(client).state.engine) as database:
        consumed = database.scalar(
            select(DeviceAuthorization).where(
                DeviceAuthorization.approving_user_id == account["id"],
                DeviceAuthorization.state == "consumed",
            )
        )
        assert consumed is not None
        consumed.expires_at = 1
        database.commit()
    assert "Device approved." in client.get(approved.headers["location"]).text

    expiring = client.post("/v1/device-authorizations", json={"client": "android"}).json()
    approved_unconsumed = client.post(
        "/device/approve",
        data={"user_code": expiring["user_code"], "csrf_token": csrf_token},
        headers={"Origin": "https://premium.test"},
        follow_redirects=False,
    )
    with Session(_app(client).state.engine) as database:
        authorization = database.scalar(
            select(DeviceAuthorization).where(DeviceAuthorization.state == "approved")
        )
        assert authorization is not None
        authorization.expires_at = 1
        database.commit()
    expired_page = client.get(approved_unconsumed.headers["location"])
    assert "Device approved." not in expired_page.text


def test_house_ad_fields_are_text_https_only_and_audited(
    client: TestClient, account: dict[str, object]
) -> None:
    auth = _make_admin(client, account)
    rejected = _admin_post(
        client,
        "/admin/ads/house",
        auth,
        {
            "title": "bad",
            "body": "bad",
            "cta_url": "javascript:alert(1)",
            "status": "active",
            "reason": "hostile URL test",
        },
    )
    assert rejected.status_code == 422
    malformed = _admin_post(
        client,
        "/admin/ads/house",
        auth,
        {
            "title": "bad port",
            "body": "must not persist",
            "cta_url": "https://example.test:not-a-port/read",
            "status": "active",
            "reason": "malformed URL test",
        },
    )
    assert malformed.status_code == 422
    with Session(_app(client).state.engine) as database:
        assert database.scalar(select(HouseAd)) is None
    invalid_schedule = _admin_post(
        client,
        "/admin/ads/house",
        auth,
        {
            "title": "Schedule",
            "body": "Schedule check",
            "cta_url": "https://example.test/read",
            "status": "active",
            "starts_at": "2026-08-02T11:00",
            "ends_at": "2026-08-02T10:00",
            "reason": "schedule test",
        },
    )
    assert invalid_schedule.status_code == 422
    hostile = '<script>alert("x")</script>'
    created = _admin_post(
        client,
        "/admin/ads/house",
        auth,
        {
            "title": hostile,
            "body": "Plain <b>text</b> only",
            "cta_url": "https://example.test/read",
            "status": "active",
            "starts_at": "2026-08-02T10:00",
            "ends_at": "2026-08-02T11:00",
            "reason": "render escaping test",
        },
    )
    assert created.status_code == 303
    page = client.get("/admin/ads")
    assert page.status_code == 200
    assert hostile not in page.text
    assert "&lt;script&gt;" in page.text
    assert "Plain &lt;b&gt;text&lt;/b&gt; only" in page.text
    assert "third-party network, script, pixel" in page.text
    assert 'value="2026-08-02T10:00"' in page.text
    assert 'value="2026-08-02T11:00"' in page.text
    with Session(_app(client).state.engine) as database:
        ad = database.scalar(select(HouseAd))
        assert ad is not None and ad.title == hostile
        assert ad.starts_at == 1_785_664_800
        assert ad.ends_at == 1_785_668_400
        audit = database.scalar(select(AuditLog).where(AuditLog.action == "house_ad.create"))
        assert audit is not None
        assert audit.reason == "render escaping test"
        assert len(audit.before_digest) == len(audit.after_digest) == 64


def test_migration_seeds_only_registered_flags_and_house_source(client: TestClient) -> None:
    assert client.get("/healthz").json() == {
        "status": "ok",
        "schema": 3,
        "build_sha": "test-sha",
    }
    engine = _app(client).state.engine
    assert {
        "entitlement_events",
        "entitlement_projection",
        "feature_flags",
        "ad_config",
        "house_ads",
        "audit_log",
        "stripe_customers",
        "checkout_attempts",
        "payment_events",
    }.issubset(inspect(engine).get_table_names())
    with Session(engine) as database:
        assert set(database.scalars(select(FeatureFlag.key))) == {
            "ad_system",
            "mobile_ad_free",
            "podcast_subscriptions",
            "topic_corpus",
            "transcript_email",
        }
        config = database.get(AdConfig, 1)
        assert config is not None
        assert config.source == "house"
        assert config.enabled is False


def test_entitlement_and_audit_ledgers_are_database_append_only(
    client: TestClient, account: dict[str, object]
) -> None:
    auth = _make_admin(client, account)
    changed = _admin_post(
        client,
        f"/admin/users/{account['id']}/override",
        auth,
        {"action": "premium", "reason": "append only proof"},
    )
    assert changed.status_code == 303
    with Session(_app(client).state.engine) as database:
        event_id = database.scalar(select(EntitlementEvent.id))
        audit_id = database.scalar(select(AuditLog.id))
        assert event_id and audit_id
        with pytest.raises(IntegrityError, match="append-only"):
            database.execute(
                update(EntitlementEvent)
                .where(EntitlementEvent.id == event_id)
                .values(reason="rewritten")
            )
            database.commit()
        database.rollback()
        with pytest.raises(IntegrityError, match="append-only"):
            database.execute(delete(AuditLog).where(AuditLog.id == audit_id))
            database.commit()


@pytest.mark.parametrize(
    ("event_type", "tier"),
    [
        ("provider_grant", "free"),
        ("provider_revoke", "premium"),
        ("override_set", None),
        ("override_clear", "free"),
    ],
)
def test_database_rejects_invalid_entitlement_event_tier_pairs(
    client: TestClient,
    account: dict[str, object],
    event_type: str,
    tier: str | None,
) -> None:
    with Session(_app(client).state.engine) as database:
        database.add(
            EntitlementEvent(
                id=f"evt_invalid_{event_type}_{tier}",
                user_id=cast("str", account["id"]),
                event_type=event_type,
                tier=tier,
                source_reference=None,
                actor_user_id=None,
                reason="constraint proof",
                revision=1,
                created_at=0,
            )
        )
        with pytest.raises(IntegrityError, match="ck_entitlement_events_type_tier"):
            database.commit()


def test_projection_source_for_provider_truth_is_test_purchase(
    client: TestClient, account: dict[str, object]
) -> None:
    with Session(_app(client).state.engine) as database:
        apply_entitlement_event(
            database,
            user_id=cast("str", account["id"]),
            event_type="provider_grant",
            tier="premium",
            actor_user_id=None,
            reason="verified test checkout",
            source_reference="cs_test_fixture",
        )
        database.commit()
        value = evaluate_entitlements(
            database,
            cast("str", account["id"]),
            at=datetime(2026, 8, 2, tzinfo=UTC),
        )
        assert value.tier == "premium"
        assert value.entitlement.source == "test_purchase"
        projection = database.get(EntitlementProjection, account["id"])
        assert projection is not None and projection.provider_source == "test_purchase"


def test_entitlement_endpoint_fails_closed_when_projection_is_missing(
    client: TestClient, account: dict[str, object]
) -> None:
    auth = _make_admin(client, account)
    token = _issue_bearer(client, auth)
    with Session(_app(client).state.engine) as database:
        projection = database.get(EntitlementProjection, account["id"])
        assert projection is not None
        database.delete(projection)
        database.commit()
    with Session(_app(client).state.engine) as database:
        with pytest.raises(ValueError, match="projection is missing"):
            evaluate_entitlements(database, cast("str", account["id"]))
        assert database.get(EntitlementProjection, account["id"]) is None
    response = client.get("/v1/me/entitlements", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    with Session(_app(client).state.engine) as database:
        assert database.get(EntitlementProjection, account["id"]) is None


def test_entitlement_endpoint_fails_closed_when_projection_disagrees_with_ledger(
    client: TestClient, account: dict[str, object]
) -> None:
    auth = _make_admin(client, account)
    token = _issue_bearer(client, auth)
    with Session(_app(client).state.engine) as database:
        projection = database.get(EntitlementProjection, account["id"])
        assert projection is not None
        projection.effective_tier = "premium"
        database.commit()

    response = client.get("/v1/me/entitlements", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    with Session(_app(client).state.engine) as database:
        projection = database.get(EntitlementProjection, account["id"])
        assert projection is not None and projection.effective_tier == "premium"


def test_cli_repair_persists_missing_projection_and_audits(
    client: TestClient, account: dict[str, object], capsys: pytest.CaptureFixture[str]
) -> None:
    app = _app(client)
    with Session(app.state.engine) as database:
        projection = database.get(EntitlementProjection, account["id"])
        assert projection is not None
        database.delete(projection)
        database.commit()

    _repair_entitlements(app.state.settings)

    assert capsys.readouterr().out == "repaired 1 entitlement projection(s)\n"
    with Session(app.state.engine) as database:
        projection = database.get(EntitlementProjection, account["id"])
        assert projection is not None
        assert projection.effective_tier == "free"
        audit = database.scalar(
            select(AuditLog).where(AuditLog.action == "entitlement_projection.repair")
        )
        assert audit is not None
        assert audit.target_id == account["id"]


def test_startup_configuration_check_rejects_unknown_flags_and_non_house_ads(
    client: TestClient,
) -> None:
    with Session(_app(client).state.engine) as database:
        database.add(
            FeatureFlag(
                key="third_party_network",
                audience="off",
                config_json="{}",
                revision=1,
                actor_user_id=None,
                updated_at=0,
            )
        )
        database.commit()
        with pytest.raises(RuntimeError, match="code-owned keys"):
            require_entitlement_configuration(database)
        unknown = database.get(FeatureFlag, "third_party_network")
        assert unknown is not None
        database.delete(unknown)
        config = database.get(AdConfig, 1)
        assert config is not None
        database.delete(config)
        database.flush()
        with pytest.raises(RuntimeError, match="house ad configuration"):
            require_entitlement_configuration(database)
