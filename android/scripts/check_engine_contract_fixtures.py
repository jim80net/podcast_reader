"""Fail when Android's checked engine fixtures drift from Python boundaries."""

from __future__ import annotations

import json
from pathlib import Path
from typing import get_type_hints

from podcast_reader.engine.app import HealthInfo, PairClaimResponse
from podcast_reader.types import LibraryEntry

FIXTURES = Path(__file__).parents[1] / "app/src/test/resources/contracts/engine"


def _load(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _assert_exact_keys(actual: object, expected: set[str], name: str) -> None:
    if not isinstance(actual, dict) or set(actual) != expected:
        raise SystemExit(f"{name} keys drifted: expected {sorted(expected)}, got {actual!r}")


def main() -> None:
    health = _load("health.json")
    _assert_exact_keys(health, set(HealthInfo.model_fields), "health")
    HealthInfo.model_validate(health)

    claim = _load("pair_claim.json")
    _assert_exact_keys(claim, set(PairClaimResponse.model_fields), "pair claim")
    PairClaimResponse.model_validate(claim)

    library = _load("library.json")
    if not isinstance(library, list) or len(library) != 1:
        raise SystemExit("library fixture must contain exactly one representative entry")
    _assert_exact_keys(library[0], set(get_type_hints(LibraryEntry)), "library entry")


if __name__ == "__main__":
    main()
