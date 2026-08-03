"""Projection-backed source inventories for boundary-policy stage two.

The committed inventory binding records what the source tree contains; it does
not admit behavior.  Every binding must resolve to an operation or copy claim
in a generated projection, so the boundary policy remains the sole authority.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, NoReturn

from .boundary_policy import POLICY_DIRECTORY, PolicyError, load_json

INVENTORY_PATH = POLICY_DIRECTORY / "surface-inventory-v1.json"
HTTP_METHODS = frozenset({"delete", "get", "patch", "post", "put"})
NETWORK_PATTERNS: Mapping[str, tuple[re.Pattern[str], ...]] = {
    "android": (
        re.compile(r"\bOkHttpClient\.Builder\s*\("),
        re.compile(r"\bRequest\.Builder\s*\("),
        re.compile(r"\bIntent\s*\(\s*Intent\.ACTION_VIEW\b"),
    ),
    "backend": (
        re.compile(r"\bhttpx\.Client\s*\("),
        re.compile(r"\bhttp\.client\.HTTPSConnection\b"),
        re.compile(r"\burllib\.request\.urlopen\s*\("),
        re.compile(r"\bresolve_tool\s*\(\s*[\"']yt-dlp[\"']\s*\)"),
        re.compile(r"\bStripeClient\s*\("),
    ),
    "desktop": (
        re.compile(r"\bfetch\s*\("),
        re.compile(r"\bnet\.request\s*\("),
        re.compile(r"\b(?:http|https)\.request\s*\("),
        re.compile(r"\bshell\.openExternal\s*\("),
    ),
    "extension": (re.compile(r"\bfetchFn\s*\("),),
    "private-web": (re.compile(r"\bfetch\s*\("),),
}
NETWORK_ROOTS: Mapping[str, tuple[Path, ...]] = {
    "android": (Path("android/app/src/main/java"),),
    "backend": (Path("src/podcast_reader"), Path("services/premium/src")),
    "desktop": (Path("app/src/main"),),
    "extension": (Path("extension/src"),),
    "private-web": (Path("src/podcast_reader/web_assets"),),
}
NETWORK_SOURCE_SUFFIXES = frozenset(
    {".cjs", ".js", ".jsx", ".kt", ".mjs", ".py", ".ts", ".tsx"}
)


def _fail(path: str, message: str) -> NoReturn:
    raise PolicyError(f"{path}: {message}")


def _exact(value: object, path: str, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(path, "must be an object")
    if set(value) != keys:
        _fail(path, f"expected exact keys {sorted(keys)}, got {sorted(value)}")
    return value


def _strings(value: object, path: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        _fail(path, "must be an array of non-empty strings")
    if len(value) != len(set(value)) or value != sorted(value):
        _fail(path, "must be unique and sorted")
    return value


def _objects(value: object, path: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        _fail(path, "must be an array of objects")
    return value


def _source(repo_root: Path, relative: str) -> str:
    path = repo_root / relative
    if not path.is_file():
        _fail(relative, "inventory source does not exist")
    return path.read_text(encoding="utf-8")


def discover_python_routes(repo_root: Path, sources: Sequence[object]) -> set[str]:
    routes: set[str] = set()
    for index, raw in enumerate(sources):
        item = _exact(raw, f"route_sources[{index}]", {"path", "prefix"})
        relative = item["path"]
        prefix = item["prefix"]
        if not isinstance(relative, str) or not isinstance(prefix, str):
            _fail(f"route_sources[{index}]", "path and prefix must be strings")
        tree = ast.parse(_source(repo_root, relative), filename=relative)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(
                    decorator.func, ast.Attribute
                ):
                    continue
                method = decorator.func.attr.lower()
                if method not in HTTP_METHODS or not decorator.args:
                    continue
                route = decorator.args[0]
                if isinstance(route, ast.Constant) and isinstance(route.value, str):
                    routes.add(f"{method.upper()} {prefix}{route.value}")
    return routes


def discover_ts_object_values(source: str, object_name: str) -> set[str]:
    match = re.search(
        rf"export const {re.escape(object_name)}\s*=\s*\{{(?P<body>.*?)\}} as const",
        source,
        re.S,
    )
    if match is None:
        _fail(object_name, "TypeScript object was not found")
    return set(re.findall(r"^\s*[A-Za-z][A-Za-z0-9]*:\s*'([^']+)'", match["body"], re.M))


def discover_preload_api_methods(source: str) -> set[str]:
    match = re.search(r"const api:\s*PodcastReaderApi\s*=\s*\{(?P<body>.*?)\n\}", source, re.S)
    if match is None:
        _fail("desktop.api_methods", "preload API object was not found")
    return set(re.findall(r"^\s{2}([A-Za-z][A-Za-z0-9]*):", match["body"], re.M))


def _normalize_template_route(route: str) -> str:
    return re.sub(r"\$\{[^}]+\}", "{parameter}", route)


def discover_script_routes(source: str, prefix: str) -> set[str]:
    literals = re.findall(r"['\"]([^'\"]*)['\"]", source)
    literals.extend(re.findall(r"`([^`]*)`", source))
    routes: set[str] = set()
    for literal in literals:
        start = literal.find(prefix)
        if start >= 0:
            candidate = re.split(r"[,\s]", literal[start:], maxsplit=1)[0].rstrip("`")
            routes.add(_normalize_template_route(candidate))
    return routes


def discover_network_owner_files(repo_root: Path, surface: str) -> set[str]:
    owners: set[str] = set()
    for root in NETWORK_ROOTS[surface]:
        absolute = repo_root / root
        if not absolute.is_dir():
            continue
        for path in absolute.rglob("*"):
            if not path.is_file() or path.suffix not in NETWORK_SOURCE_SUFFIXES:
                continue
            if ".test." in path.name or path.name.endswith("_test.py"):
                continue
            source = path.read_text(encoding="utf-8")
            if any(pattern.search(source) for pattern in NETWORK_PATTERNS[surface]):
                owners.add(path.relative_to(repo_root).as_posix())
    return owners


def discover_storage_areas(source: str) -> set[str]:
    return set(re.findall(r"\bchrome\.storage\.(local|sync|managed|session)\b", source))


def discover_model_fields(source: str, class_name: str) -> set[str]:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                child.target.id
                for child in node.body
                if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name)
            }
    _fail(class_name, "persistence model was not found")


def _binding_map(value: object, path: str) -> dict[str, str]:
    bindings: dict[str, str] = {}
    for index, raw in enumerate(_objects(value, path)):
        item = _exact(raw, f"{path}[{index}]", {"item", "operation_id"})
        observed = item["item"]
        operation_id = item["operation_id"]
        if not isinstance(observed, str) or not observed:
            _fail(f"{path}[{index}].item", "must be a non-empty string")
        if not isinstance(operation_id, str) or not operation_id:
            _fail(f"{path}[{index}].operation_id", "must be a non-empty string")
        if observed in bindings:
            _fail(path, f"duplicate binding for {observed}")
        bindings[observed] = operation_id
    if list(bindings) != sorted(bindings):
        _fail(path, "bindings must be sorted by item")
    return bindings


def _multi_binding_map(value: object, path: str) -> dict[str, list[str]]:
    bindings: dict[str, list[str]] = {}
    for index, raw in enumerate(_objects(value, path)):
        item = _exact(raw, f"{path}[{index}]", {"item", "operation_ids"})
        observed = item["item"]
        if not isinstance(observed, str) or not observed:
            _fail(f"{path}[{index}].item", "must be a non-empty string")
        operation_ids = _strings(item["operation_ids"], f"{path}[{index}].operation_ids")
        if not operation_ids:
            _fail(f"{path}[{index}].operation_ids", "must not be empty")
        if observed in bindings:
            _fail(path, f"duplicate binding for {observed}")
        bindings[observed] = operation_ids
    if list(bindings) != sorted(bindings):
        _fail(path, "bindings must be sorted by item")
    return bindings


def discover_android_runtime_dependencies(lock_source: str) -> set[str]:
    dependencies: set[str] = set()
    for line in lock_source.splitlines():
        coordinate, separator, configurations = line.partition("=")
        if separator and "releaseRuntimeClasspath" in configurations.split(","):
            dependencies.add(coordinate)
    return dependencies


def _check_snapshot(path: str, actual: set[str], bindings: Mapping[str, str]) -> None:
    expected = set(bindings)
    if actual != expected:
        missing = sorted(expected - actual)
        new = sorted(actual - expected)
        _fail(
            path,
            f"inventory drift; missing={missing}, new={new}",
        )


def _operation_ids(projections: Mapping[str, object]) -> set[str]:
    result: set[str] = set()
    for surface, raw in projections.items():
        projection = _exact(
            raw,
            f"projection.{surface}",
            {
                "schema_version",
                "contract",
                "policy_revision",
                "surface",
                "roots",
                "operations",
                "copy_claims",
                "checker_ids",
                "legacy_fences",
            },
        )
        for operation in _objects(projection["operations"], f"projection.{surface}.operations"):
            identifier = operation.get("id")
            if isinstance(identifier, str):
                result.add(identifier)
    return result


def _claim_ids(projections: Mapping[str, object]) -> set[str]:
    result: set[str] = set()
    for surface, raw in projections.items():
        projection = raw if isinstance(raw, dict) else {}
        for claim in _objects(projection.get("copy_claims"), f"projection.{surface}.copy_claims"):
            identifier = claim.get("id")
            if isinstance(identifier, str):
                result.add(identifier)
    return result


def load_projections(repo_root: Path) -> dict[str, object]:
    return {
        surface: load_json(repo_root / POLICY_DIRECTORY / f"projection-{surface}-v1.json")
        for surface in ("android", "backend", "desktop", "extension", "private-web")
    }


def validate_surface_inventory(
    value: object, repo_root: Path, projections: Mapping[str, object]
) -> None:
    inventory = _exact(
        value,
        "$",
        {
            "schema_version",
            "contract",
            "policy_revision",
            "android",
            "backend",
            "desktop",
            "extension",
            "private_web",
            "copy",
        },
    )
    if inventory["schema_version"] != 1:
        _fail("$.schema_version", "unsupported inventory schema")
    if inventory["contract"] != "podcast-reader-boundary-inventory-bindings":
        _fail("$.contract", "unexpected inventory contract")
    revisions = {
        raw.get("policy_revision") for raw in projections.values() if isinstance(raw, dict)
    }
    if revisions != {inventory["policy_revision"]}:
        _fail("$.policy_revision", "inventory and projections must have one matching revision")
    operations = _operation_ids(projections)

    android = _exact(
        inventory["android"],
        "$.android",
        {
            "dependency_lock",
            "production_dependencies",
            "network_owners",
            "admitting_issue",
        },
    )
    admitting_issue = android["admitting_issue"]
    if not isinstance(admitting_issue, str) or not admitting_issue.startswith(
        "https://github.com/jim80net/podcast_reader/issues/"
    ):
        _fail("$.android.admitting_issue", "must name the admitting issue")
    dependencies = _strings(
        android["production_dependencies"], "$.android.production_dependencies"
    )
    _check_snapshot(
        "$.android.production_dependencies",
        discover_android_runtime_dependencies(
            _source(repo_root, android["dependency_lock"])
        ),
        {dependency: "android.dependency-allowlist" for dependency in dependencies},
    )
    android_network = _multi_binding_map(
        android["network_owners"], "$.android.network_owners"
    )
    _check_snapshot(
        "$.android.network_owners",
        discover_network_owner_files(repo_root, "android"),
        {owner: operation_ids[0] for owner, operation_ids in android_network.items()},
    )
    android_operations = {
        operation.get("id")
        for operation in _objects(
            projections["android"]["operations"], "projection.android.operations"
        )
        if isinstance(operation.get("id"), str)
    }
    unknown_android_operations = sorted(
        {
            operation_id
            for operation_ids in android_network.values()
            for operation_id in operation_ids
            if operation_id not in android_operations
        }
    )
    if unknown_android_operations:
        _fail(
            "$.android.network_owners",
            "bindings name operations absent from the Android projection: "
            f"{unknown_android_operations}",
        )

    backend = _exact(
        inventory["backend"],
        "$.backend",
        {"route_sources", "routes", "network_owners", "persistence"},
    )
    backend_routes = _binding_map(backend["routes"], "$.backend.routes")
    _check_snapshot(
        "$.backend.routes",
        discover_python_routes(
            repo_root, _objects(backend["route_sources"], "$.backend.route_sources")
        ),
        backend_routes,
    )
    backend_network = _binding_map(backend["network_owners"], "$.backend.network_owners")
    _check_snapshot(
        "$.backend.network_owners",
        discover_network_owner_files(repo_root, "backend"),
        backend_network,
    )
    persistence = _exact(
        backend["persistence"],
        "$.backend.persistence",
        {"path", "model", "fields", "operation_id"},
    )
    model_path = persistence["path"]
    model_name = persistence["model"]
    if not isinstance(model_path, str) or not isinstance(model_name, str):
        _fail("$.backend.persistence", "path and model must be strings")
    _check_snapshot(
        "$.backend.persistence.fields",
        discover_model_fields(_source(repo_root, model_path), model_name),
        {
            field: persistence["operation_id"]
            for field in _strings(persistence["fields"], "$.backend.persistence.fields")
        },
    )

    desktop = _exact(
        inventory["desktop"],
        "$.desktop",
        {
            "channel_source",
            "request_channels",
            "push_channels",
            "preload_source",
            "api_methods",
            "network_owners",
            "persistence_owners",
        },
    )
    channel_source = _source(repo_root, desktop["channel_source"])
    for key, object_name in (("request_channels", "CHANNELS"), ("push_channels", "PUSH_CHANNELS")):
        bindings = _binding_map(desktop[key], f"$.desktop.{key}")
        _check_snapshot(
            f"$.desktop.{key}", discover_ts_object_values(channel_source, object_name), bindings
        )
    api_bindings = _binding_map(desktop["api_methods"], "$.desktop.api_methods")
    _check_snapshot(
        "$.desktop.api_methods",
        discover_preload_api_methods(_source(repo_root, desktop["preload_source"])),
        api_bindings,
    )
    desktop_network = _binding_map(desktop["network_owners"], "$.desktop.network_owners")
    _check_snapshot(
        "$.desktop.network_owners",
        discover_network_owner_files(repo_root, "desktop"),
        desktop_network,
    )
    desktop_persistence = _binding_map(
        desktop["persistence_owners"], "$.desktop.persistence_owners"
    )
    _check_snapshot(
        "$.desktop.persistence_owners",
        {
            path
            for path in ("app/src/main/premium/credentials.ts", "app/src/main/vault.ts")
            if (repo_root / path).is_file()
        },
        desktop_persistence,
    )

    extension = _exact(
        inventory["extension"],
        "$.extension",
        {"manifest_path", "manifest", "client_source", "routes", "network_owners", "storage_areas"},
    )
    manifest = load_json(repo_root / extension["manifest_path"])
    expected_manifest = _exact(
        extension["manifest"],
        "$.extension.manifest",
        {"permissions", "host_permissions", "optional_permissions", "optional_host_permissions"},
    )
    for key in expected_manifest:
        actual = manifest.get(key) if isinstance(manifest, dict) else None
        if actual != expected_manifest[key]:
            _fail(f"$.extension.manifest.{key}", f"manifest drift: {actual!r}")
    extension_source = _source(repo_root, extension["client_source"])
    extension_routes = _binding_map(extension["routes"], "$.extension.routes")
    _check_snapshot(
        "$.extension.routes", discover_script_routes(extension_source, "/v1/"), extension_routes
    )
    extension_network = _binding_map(extension["network_owners"], "$.extension.network_owners")
    _check_snapshot(
        "$.extension.network_owners",
        discover_network_owner_files(repo_root, "extension"),
        extension_network,
    )
    storage_bindings = _binding_map(extension["storage_areas"], "$.extension.storage_areas")
    extension_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (repo_root / "extension/src").glob("*.ts")
        if ".test." not in path.name
    )
    _check_snapshot(
        "$.extension.storage_areas", discover_storage_areas(extension_sources), storage_bindings
    )

    private_web = _exact(
        inventory["private_web"],
        "$.private_web",
        {"route_sources", "routes", "browser_source", "browser_routes", "network_owners"},
    )
    private_routes = _binding_map(private_web["routes"], "$.private_web.routes")
    discovered_private_routes = {
        route
        for route in discover_python_routes(
            repo_root, _objects(private_web["route_sources"], "$.private_web.route_sources")
        )
        if route.partition(" ")[2].startswith("/web/")
    }
    _check_snapshot("$.private_web.routes", discovered_private_routes, private_routes)
    browser_bindings = _binding_map(private_web["browser_routes"], "$.private_web.browser_routes")
    _check_snapshot(
        "$.private_web.browser_routes",
        discover_script_routes(_source(repo_root, private_web["browser_source"]), "/web/"),
        browser_bindings,
    )
    private_network = _binding_map(private_web["network_owners"], "$.private_web.network_owners")
    _check_snapshot(
        "$.private_web.network_owners",
        discover_network_owner_files(repo_root, "private-web"),
        private_network,
    )

    all_bindings = (
        {owner: operation_ids[0] for owner, operation_ids in android_network.items()}
        | backend_routes
        | backend_network
        | {field: persistence["operation_id"] for field in persistence["fields"]}
        | api_bindings
        | desktop_network
        | desktop_persistence
        | extension_routes
        | extension_network
        | storage_bindings
        | private_routes
        | browser_bindings
        | private_network
    )
    unknown_operations = sorted(set(all_bindings.values()) - operations)
    if unknown_operations:
        _fail(
            "$", f"inventory bindings name operations absent from projections: {unknown_operations}"
        )

    claims = _claim_ids(projections)
    declared_claims: set[str] = set()
    copy = _exact(inventory["copy"], "$.copy", {"surfaces"})
    for index, raw in enumerate(_objects(copy["surfaces"], "$.copy.surfaces")):
        path = f"$.copy.surfaces[{index}]"
        surface = _exact(raw, path, {"id", "claim_ids", "files"})
        claim_ids = _strings(surface["claim_ids"], f"{path}.claim_ids")
        declared_claims.update(claim_ids)
        for file_index, file_raw in enumerate(_objects(surface["files"], f"{path}.files")):
            file_path = f"{path}.files[{file_index}]"
            declaration = _exact(file_raw, file_path, {"path", "fragments"})
            source = _source(repo_root, declaration["path"])
            for fragment in _strings(declaration["fragments"], f"{file_path}.fragments"):
                if fragment not in source:
                    _fail(
                        file_path, f"declared policy-backed copy fragment is absent: {fragment!r}"
                    )
    if declared_claims != claims:
        _fail("$.copy", f"copy declarations must cover every projected claim: {sorted(claims)}")
    site_html = sorted((repo_root / "site").glob("*.html"))
    if not site_html:
        _fail("$.copy", "site/*.html discovery must not be empty")
    declared_files = {
        file["path"]
        for surface in copy["surfaces"]
        for file in surface["files"]
        if isinstance(file, dict) and isinstance(file.get("path"), str)
    }
    undeclared_site = [
        path.relative_to(repo_root).as_posix()
        for path in site_html
        if path.relative_to(repo_root).as_posix() not in declared_files
    ]
    if undeclared_site:
        _fail("$.copy", f"site HTML is not declared: {undeclared_site}")


def check_surface_inventory(repo_root: Path) -> None:
    validate_surface_inventory(
        load_json(repo_root / INVENTORY_PATH), repo_root, load_projections(repo_root)
    )
