from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from podcast_reader_premium.contracts import (
    CurrentUserV1,
    DeviceAuthorizationStartV1,
    EntitlementV1,
    NativeAuthErrorV1,
    TokenResponseV1,
    TokenRevokeRequestV1,
)

CONTRACTS = Path(__file__).parents[1] / "contracts"
ENTITLEMENT_CONFORMANCE = CONTRACTS / "v1" / "entitlements" / "conformance-v1.json"
CURRENT_USER_CONFORMANCE = CONTRACTS / "v1" / "current-user" / "conformance-v1.json"


def test_v1_current_user_fixture_is_strict_and_round_trips() -> None:
    path = CONTRACTS / "v1" / "current-user" / "current-user-v1.json"
    source = json.loads(path.read_text(encoding="utf-8"))
    parsed = CurrentUserV1.model_validate(source)
    assert parsed.model_dump(mode="json") == source


def test_shared_current_user_conformance_vectors() -> None:
    vectors = json.loads(CURRENT_USER_CONFORMANCE.read_text(encoding="utf-8"))
    assert set(vectors) == {"schema_version", "contract", "valid", "invalid"}
    assert vectors["schema_version"] == 1
    assert vectors["contract"] == "current-user-v1"
    names = [item["name"] for group in ("valid", "invalid") for item in vectors[group]]
    assert len(names) == len(set(names))
    assert "email-is-not-a-consumer-field" in names

    for vector in vectors["valid"]:
        CurrentUserV1.model_validate(vector["document"])
    for vector in vectors["invalid"]:
        with pytest.raises(ValidationError, match=".+"):
            CurrentUserV1.model_validate(vector["document"])


@pytest.mark.parametrize("name", ["entitlements-v1-free.json", "entitlements-v1-premium.json"])
def test_v1_entitlement_fixtures_are_strict_and_round_trip(name: str) -> None:
    path = CONTRACTS / name
    source = json.loads(path.read_text(encoding="utf-8"))
    parsed = EntitlementV1.model_validate(source)
    assert parsed.schema_version == 1
    assert parsed.model_dump(mode="json") == source


def test_shared_entitlement_conformance_vectors() -> None:
    vectors = json.loads(ENTITLEMENT_CONFORMANCE.read_text(encoding="utf-8"))
    assert set(vectors) == {
        "schema_version",
        "contract",
        "expected_subject",
        "now",
        "valid",
        "invalid",
    }
    assert vectors["schema_version"] == 1
    assert vectors["contract"] == "entitlements-v1"
    names = [item["name"] for group in ("valid", "invalid") for item in vectors[group]]
    assert len(names) == len(set(names))
    assert "boolean-capability-as-integer" in names

    for vector in vectors["valid"]:
        parsed = EntitlementV1.model_validate(vector["document"])
        assert f"online-{parsed.tier}" == vector["expected_state"], vector["name"]
    for vector in vectors["invalid"]:
        with pytest.raises(ValidationError, match=".+"):
            EntitlementV1.model_validate(vector["document"])


@pytest.mark.parametrize(
    ("field", "value"),
    [("flags_revision", -1), ("flags_revision", 9_223_372_036_854_775_808)],
)
def test_v1_entitlement_revisions_are_bounded(field: str, value: int) -> None:
    path = CONTRACTS / "entitlements-v1-free.json"
    source = json.loads(path.read_text(encoding="utf-8"))
    source[field] = value
    with pytest.raises(ValidationError):
        EntitlementV1.model_validate(source)


def test_v1_entitlement_source_revision_is_bounded() -> None:
    path = CONTRACTS / "entitlements-v1-free.json"
    source = json.loads(path.read_text(encoding="utf-8"))
    source["entitlement"]["revision"] = -1
    with pytest.raises(ValidationError):
        EntitlementV1.model_validate(source)


def test_v1_refresh_time_must_follow_evaluation_time() -> None:
    path = CONTRACTS / "entitlements-v1-free.json"
    source = json.loads(path.read_text(encoding="utf-8"))
    source["refresh_after"] = source["evaluated_at"]
    with pytest.raises(ValidationError):
        EntitlementV1.model_validate(source)


@pytest.mark.parametrize(
    ("name", "model"),
    [
        ("native-auth-v1-device-start.json", DeviceAuthorizationStartV1),
        ("native-auth-v1-token-response.json", TokenResponseV1),
        ("native-auth-v1-revoke-request.json", TokenRevokeRequestV1),
    ],
)
def test_native_auth_v1_object_fixtures_are_strict_and_round_trip(
    name: str, model: type[BaseModel]
) -> None:
    source = json.loads((CONTRACTS / name).read_text())
    parsed = model.model_validate(source)
    assert parsed.model_dump(mode="json") == source


def test_native_auth_v1_error_fixtures_are_strict_and_complete() -> None:
    path = CONTRACTS / "native-auth-v1-errors.json"
    source = json.loads(path.read_text(encoding="utf-8"))
    parsed = TypeAdapter(list[NativeAuthErrorV1]).validate_python(source)
    assert [item.model_dump(mode="json") for item in parsed] == source
    assert {item.code for item in parsed} == {
        "authorization_pending",
        "slow_down",
        "expired_token",
        "access_denied",
        "refresh_token_reused",
    }


def test_native_auth_v1_revoke_response_is_empty_204() -> None:
    path = CONTRACTS / "native-auth-v1-revoke-response.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {"status": 204, "body": None}
