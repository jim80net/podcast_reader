from __future__ import annotations

import copy
from pathlib import Path

import pytest

from podcast_reader_premium.boundary_inventory import (
    INVENTORY_PATH,
    load_projections,
    validate_surface_inventory,
)
from podcast_reader_premium.boundary_policy import PolicyError, load_json

ROOT = Path(__file__).resolve().parents[3]


def _inventory() -> dict[str, object]:
    value = load_json(ROOT / INVENTORY_PATH)
    assert isinstance(value, dict)
    return value


def _validate(value: object) -> None:
    validate_surface_inventory(value, ROOT, load_projections(ROOT))


def test_all_stage_two_inventories_and_copy_claims_match_projections() -> None:
    _validate(_inventory())


@pytest.mark.parametrize(
    ("surface", "section", "new_item"),
    [
        ("backend", "routes", "ZZZ /new-backend-route"),
        ("desktop", "api_methods", "zzzzNewBridgeMethod"),
        ("extension", "routes", "/v1/zzz-new-extension-route"),
        ("private_web", "browser_routes", "/web/zzz-new-private-route"),
    ],
)
def test_each_surface_inventory_has_a_motivating_negative_mutation(
    surface: str, section: str, new_item: str
) -> None:
    value = copy.deepcopy(_inventory())
    surface_inventory = value[surface]
    assert isinstance(surface_inventory, dict)
    bindings = surface_inventory[section]
    assert isinstance(bindings, list)
    bindings.append({"item": new_item, "operation_id": "desktop.renderer.bridge"})
    with pytest.raises(PolicyError, match="inventory drift"):
        _validate(value)


def test_inventory_binding_cannot_name_an_operation_absent_from_projections() -> None:
    value = copy.deepcopy(_inventory())
    backend = value["backend"]
    assert isinstance(backend, dict)
    routes = backend["routes"]
    assert isinstance(routes, list)
    first = routes[0]
    assert isinstance(first, dict)
    first["operation_id"] = "missing.operation"
    with pytest.raises(PolicyError, match="operations absent from projections"):
        _validate(value)


def test_extension_storage_sync_mutation_fails_closed() -> None:
    value = copy.deepcopy(_inventory())
    extension = value["extension"]
    assert isinstance(extension, dict)
    areas = extension["storage_areas"]
    assert isinstance(areas, list)
    areas.append({"item": "sync", "operation_id": "extension.storage.local"})
    with pytest.raises(PolicyError, match="inventory drift"):
        _validate(value)


def test_copy_claim_declaration_has_a_negative_fragment_proof() -> None:
    value = copy.deepcopy(_inventory())
    copy_inventory = value["copy"]
    assert isinstance(copy_inventory, dict)
    surfaces = copy_inventory["surfaces"]
    assert isinstance(surfaces, list)
    desktop = surfaces[0]
    assert isinstance(desktop, dict)
    files = desktop["files"]
    assert isinstance(files, list)
    declaration = files[0]
    assert isinstance(declaration, dict)
    fragments = declaration["fragments"]
    assert isinstance(fragments, list)
    fragments.append("zzzz absent policy-backed disclosure")
    with pytest.raises(PolicyError, match="declared policy-backed copy fragment is absent"):
        _validate(value)


@pytest.mark.parametrize("surface", ["backend", "desktop"])
def test_network_owner_inventory_has_a_negative_mutation(surface: str) -> None:
    value = copy.deepcopy(_inventory())
    surface_inventory = value[surface]
    assert isinstance(surface_inventory, dict)
    owners = surface_inventory["network_owners"]
    assert isinstance(owners, list)
    owners.append(
        {
            "item": f"zzzz-new-{surface}-network-owner.ts",
            "operation_id": "desktop.engine.control",
        }
    )
    with pytest.raises(PolicyError, match="inventory drift"):
        _validate(value)


def test_extension_manifest_permission_has_a_negative_mutation() -> None:
    value = copy.deepcopy(_inventory())
    extension = value["extension"]
    assert isinstance(extension, dict)
    manifest = extension["manifest"]
    assert isinstance(manifest, dict)
    permissions = manifest["permissions"]
    assert isinstance(permissions, list)
    permissions.append("tabs")
    with pytest.raises(PolicyError, match="manifest drift"):
        _validate(value)


def test_copy_declaration_cannot_name_a_claim_absent_from_projections() -> None:
    value = copy.deepcopy(_inventory())
    copy_inventory = value["copy"]
    assert isinstance(copy_inventory, dict)
    surfaces = copy_inventory["surfaces"]
    assert isinstance(surfaces, list)
    site = surfaces[-1]
    assert isinstance(site, dict)
    claim_ids = site["claim_ids"]
    assert isinstance(claim_ids, list)
    claim_ids.append("zzzz.missing-claim")
    with pytest.raises(PolicyError, match="copy declarations must cover every projected claim"):
        _validate(value)


def test_android_resolved_production_dependency_has_a_negative_mutation() -> None:
    value = copy.deepcopy(_inventory())
    android = value["android"]
    assert isinstance(android, dict)
    dependencies = android["production_dependencies"]
    assert isinstance(dependencies, list)
    dependencies.append("zzzz.example:undeclared-network-sdk:1.0.0")
    with pytest.raises(PolicyError, match="inventory drift"):
        _validate(value)


def test_android_network_owner_has_a_negative_mutation() -> None:
    value = copy.deepcopy(_inventory())
    android = value["android"]
    assert isinstance(android, dict)
    owners = android["network_owners"]
    assert isinstance(owners, list)
    owners.append(
        {
            "item": "zzzz-new-android-network-owner.kt",
            "operation_ids": ["android.engine.health"],
        }
    )
    with pytest.raises(PolicyError, match="inventory drift"):
        _validate(value)


def test_android_network_owner_cannot_name_an_unprojected_operation() -> None:
    value = copy.deepcopy(_inventory())
    android = value["android"]
    assert isinstance(android, dict)
    owners = android["network_owners"]
    assert isinstance(owners, list)
    first = owners[0]
    assert isinstance(first, dict)
    first["operation_ids"] = ["desktop.engine.control"]
    with pytest.raises(PolicyError, match="absent from the Android projection"):
        _validate(value)
