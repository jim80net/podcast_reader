from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast

CONTRACTS = Path(__file__).parents[1] / "contracts" / "v1" / "ads"


def test_android_consumer_handoff_freezes_route_and_fixture_hashes() -> None:
    handoff = cast(
        "dict[str, object]",
        json.loads((CONTRACTS / "consumer-handoff.json").read_text()),
    )

    assert handoff["handoff_version"] == 1
    assert handoff["route"] == {
        "method": "GET",
        "path": "/v1/ads/inventory/{slot}",
        "authentication": "bearer",
        "slots": ["library", "reader", "mobile_home"],
        "responses": {
            "eligible": 200,
            "authenticated_ineligible": 204,
            "malformed_authentication": 401,
            "invalid_slot": 404,
        },
    }

    fixture_sha256 = cast("dict[str, str]", handoff["fixture_sha256"])
    assert set(fixture_sha256) == {
        "eligible-library.json",
        "eligible-reader.json",
        "forward-additive.json",
        "hostile-text.json",
        "malformed.json",
        "no-content.json",
    }
    for name, expected_digest in fixture_sha256.items():
        assert hashlib.sha256((CONTRACTS / name).read_bytes()).hexdigest() == expected_digest
