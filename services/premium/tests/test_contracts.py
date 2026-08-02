from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from podcast_reader_premium.contracts import EntitlementV1


@pytest.mark.parametrize("name", ["entitlements-v1-free.json", "entitlements-v1-premium.json"])
def test_v1_entitlement_fixtures_are_strict_and_round_trip(name: str) -> None:
    path = Path(__file__).parents[1] / "contracts" / name
    source = json.loads(path.read_text(encoding="utf-8"))
    parsed = EntitlementV1.model_validate(source)
    assert parsed.schema_version == 1
    assert parsed.model_dump(mode="json") == source


@pytest.mark.parametrize(
    ("field", "value"),
    [("flags_revision", -1), ("flags_revision", 9_223_372_036_854_775_808)],
)
def test_v1_entitlement_revisions_are_bounded(field: str, value: int) -> None:
    path = Path(__file__).parents[1] / "contracts" / "entitlements-v1-free.json"
    source = json.loads(path.read_text(encoding="utf-8"))
    source[field] = value
    with pytest.raises(ValidationError):
        EntitlementV1.model_validate(source)


def test_v1_entitlement_source_revision_is_bounded() -> None:
    path = Path(__file__).parents[1] / "contracts" / "entitlements-v1-free.json"
    source = json.loads(path.read_text(encoding="utf-8"))
    source["entitlement"]["revision"] = -1
    with pytest.raises(ValidationError):
        EntitlementV1.model_validate(source)


def test_v1_refresh_time_must_follow_evaluation_time() -> None:
    path = Path(__file__).parents[1] / "contracts" / "entitlements-v1-free.json"
    source = json.loads(path.read_text(encoding="utf-8"))
    source["refresh_after"] = source["evaluated_at"]
    with pytest.raises(ValidationError):
        EntitlementV1.model_validate(source)
