from __future__ import annotations

import json
from pathlib import Path

import pytest

from podcast_reader_premium.contracts import EntitlementV1


@pytest.mark.parametrize("name", ["entitlements-v1-free.json", "entitlements-v1-premium.json"])
def test_v1_entitlement_fixtures_are_strict_and_round_trip(name: str) -> None:
    path = Path(__file__).parents[1] / "contracts" / name
    source = json.loads(path.read_text(encoding="utf-8"))
    parsed = EntitlementV1.model_validate(source)
    assert parsed.schema_version == 1
    assert parsed.model_dump(mode="json") == source
