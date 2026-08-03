"""Verify Android's frozen house-ad consumer inputs against the backend handoff."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ADS = ROOT / "services" / "premium" / "contracts" / "v1" / "ads"
HANDOFF = ADS / "consumer-handoff.json"

EXPECTED_ROUTE = {
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
EXPECTED_FIXTURES = {
    "eligible-library.json",
    "eligible-reader.json",
    "forward-additive.json",
    "hostile-text.json",
    "malformed.json",
    "no-content.json",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def main() -> None:
    handoff = json.loads(HANDOFF.read_text())
    require(set(handoff) == {"handoff_version", "route", "fixture_sha256"}, "unexpected ad handoff shape")
    require(handoff["handoff_version"] == 1, "unsupported ad handoff version")
    require(handoff["route"] == EXPECTED_ROUTE, "Android ad route/status/slot contract drift")

    fixture_hashes = handoff["fixture_sha256"]
    require(set(fixture_hashes) == EXPECTED_FIXTURES, "unexpected frozen ad fixture set")
    for name in sorted(EXPECTED_FIXTURES):
        expected = fixture_hashes[name]
        require(isinstance(expected, str) and len(expected) == 64, f"invalid SHA-256 pin for {name}")
        actual = hashlib.sha256((ADS / name).read_bytes()).hexdigest()
        require(actual == expected, f"frozen ad fixture digest mismatch: {name}")


if __name__ == "__main__":
    main()
