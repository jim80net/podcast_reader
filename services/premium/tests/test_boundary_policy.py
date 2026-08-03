from __future__ import annotations

import json
from pathlib import Path

import pytest

from podcast_reader_premium.boundary_policy import (
    POLICY_DIRECTORY,
    PolicyError,
    check_surface_root_completeness,
    compile_projections,
    discover_network_capable_roots,
    load_json,
    validate_conformance_vectors,
    validate_policy,
    write_or_check,
)

ROOT = Path(__file__).resolve().parents[3]
POLICY = ROOT / POLICY_DIRECTORY / "policy-v1.json"
VECTORS = ROOT / POLICY_DIRECTORY / "conformance-v1.json"


def test_policy_vectors_and_committed_projections_are_current() -> None:
    policy = load_json(POLICY)
    validate_policy(policy, ROOT)
    validate_conformance_vectors(load_json(VECTORS), policy, ROOT)
    write_or_check(ROOT, check=True)
    projections = compile_projections(policy)
    assert set(projections) == {"android", "backend", "desktop", "extension", "private-web"}
    assert all(projection["operations"] for projection in projections.values())
    assert all(projection["legacy_fences"] for projection in projections.values())


def test_surface_root_completeness_rejects_a_brand_new_network_capable_root(
    tmp_path: Path,
) -> None:
    new_root = tmp_path / "brand_new_client"
    new_root.mkdir()
    (new_root / "transport.ts").write_text("export const call = () => fetch('/v1/new')\n")
    assert discover_network_capable_roots(tmp_path) == {"brand_new_client"}
    with pytest.raises(PolicyError, match="undeclared network-capable roots.*brand_new_client"):
        check_surface_root_completeness(tmp_path, [])


def test_surface_root_completeness_rejects_stale_declarations(tmp_path: Path) -> None:
    (tmp_path / "declared_but_empty").mkdir()
    roots = [{"path": "declared_but_empty", "network_capable": True}]
    with pytest.raises(PolicyError, match="declared network-capable roots have no network owner"):
        check_surface_root_completeness(tmp_path, roots)


def test_legacy_fence_map_is_complete_and_retains_every_assertion() -> None:
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    fences = policy["surface_enforcement"]["legacy_fences"]
    expected = {
        "android.account-secret-ui",
        "android.ad-dependency-denylist",
        "android.ad-native-rendering",
        "android.ad-permission-denylist",
        "android.ad-source-denylist",
        "desktop.ad-dependency-denylist",
        "desktop.ad-renderer-denylist",
        "desktop.bridge-exact-methods",
        "desktop.iframe-isolation",
        "desktop.local-premium-dependency-exclusion",
        "desktop.premium-consumer-file-allowlist",
        "desktop.renderer-bearer-haystack",
        "desktop.renderer-secret-field-denylist",
        "extension.engine-route-inventory",
        "extension.local-storage-only",
        "private-web.route-method-inventory",
        "private-web.session-and-response-security",
        "public-copy.approved-email-disclosure",
        "public-copy.contradiction-negative-proof",
        "public-copy.contradiction-regexes",
    }
    assert {fence["id"] for fence in fences} == expected
    assert all(fence["retained_as_defense_in_depth"] is True for fence in fences)
    assert all((ROOT / fence["path"]).is_file() for fence in fences)
