"""Strict compiler and check mode for the cross-product boundary policy.

This module is build-time contract tooling. Product runtimes consume their own
contracts; they do not import the premium service to make boundary decisions.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, NoReturn

POLICY_DIRECTORY = Path("services/premium/contracts/v1/boundary-policy")
POLICY_PATH = POLICY_DIRECTORY / "policy-v1.json"
VECTORS_PATH = POLICY_DIRECTORY / "conformance-v1.json"
SCHEMA_PATH = POLICY_DIRECTORY / "schema-v1.json"
PROJECTION_PREFIX = "projection-"
SURFACES = ("android", "backend", "desktop", "extension", "private-web")
REQUIRED_LEGACY_FENCES = frozenset(
    {
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
)
UNRESOLVED_OPERATOR_FORK_MARKERS = frozenset(
    {
        "custom-premium-origin",
        "off-device-embedding",
        "production-email",
        "topic-corpus",
        "topic_corpus",
    }
)
ISSUE_PATTERN = re.compile(r"^https://github\.com/jim80net/podcast_reader/issues/[1-9][0-9]*$")
IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
ROUTE_PATTERN = re.compile(r"^/(?:[A-Za-z0-9._~{}:-]+/?)*$")
NETWORK_MARKERS = (
    re.compile(r"\bfetch\b"),
    re.compile(r"\bfetch\s*\("),
    re.compile(r"\bXMLHttpRequest\b"),
    re.compile(r"\bWebSocket\s*\("),
    re.compile(r"\bOkHttpClient\b"),
    re.compile(r"\bURLSession\b"),
    re.compile(r"\bFastAPI\s*\("),
    re.compile(r"\brequests\.(?:get|post|put|delete|request|Session)\b"),
    re.compile(r"\bhttpx\.(?:get|post|put|delete|request|Client|AsyncClient)\b"),
    re.compile(r"\burllib\.request\b"),
)
NETWORK_SOURCE_SUFFIXES = frozenset({".cjs", ".js", ".jsx", ".kt", ".mjs", ".py", ".ts", ".tsx"})
IGNORED_SCAN_PARTS = frozenset(
    {
        ".git",
        ".gitnexus",
        ".venv",
        "build",
        "dist",
        "fixtures",
        "node_modules",
        "tests",
    }
)


class PolicyError(ValueError):
    """A fail-closed policy, vector, projection, or repository coverage error."""


def _fail(path: str, message: str) -> NoReturn:
    raise PolicyError(f"{path}: {message}")


def _object(value: object, path: str, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(path, "must be an object")
    actual = set(value)
    if actual != keys:
        _fail(path, f"expected exact keys {sorted(keys)}, got {sorted(actual)}")
    return value


def _array(value: object, path: str) -> list[Any]:
    if not isinstance(value, list):
        _fail(path, "must be an array")
    return value


def _string(value: object, path: str, *, identifier: bool = False) -> str:
    if not isinstance(value, str) or not value:
        _fail(path, "must be a non-empty string")
    if identifier and IDENTIFIER_PATTERN.fullmatch(value) is None:
        _fail(path, "must be a stable lowercase identifier")
    if "*" in value:
        _fail(path, "wildcards are forbidden")
    return value


def _string_array(value: object, path: str, *, identifier: bool = False) -> list[str]:
    values = _array(value, path)
    result = [
        _string(item, f"{path}[{index}]", identifier=identifier)
        for index, item in enumerate(values)
    ]
    if len(result) != len(set(result)):
        _fail(path, "must not contain duplicates")
    return result


def _issue(value: object, path: str) -> str:
    issue = _string(value, path)
    if ISSUE_PATTERN.fullmatch(issue) is None:
        _fail(path, "must be a full jim80net/podcast_reader issue URL")
    return issue


def _positive_integer(value: object, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        _fail(path, "must be a positive integer")
    return value


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PolicyError(f"{path}: {error}") from error


def validate_policy(value: object, repo_root: Path) -> dict[str, Any]:
    policy = _object(
        value,
        "$",
        {
            "schema_version",
            "contract",
            "policy_revision",
            "data_classes",
            "zones",
            "operations",
            "copy_claims",
            "surface_enforcement",
            "exceptions",
        },
    )
    if policy["schema_version"] != 1:
        _fail("$.schema_version", "unsupported policy schema")
    if policy["contract"] != "podcast-reader-boundary-policy":
        _fail("$.contract", "unexpected contract identifier")
    _positive_integer(policy["policy_revision"], "$.policy_revision")

    data_classes = _validate_named_definitions(
        policy["data_classes"], "$.data_classes", "sensitivity"
    )
    zones = _validate_named_definitions(policy["zones"], "$.zones", "retention")
    operations = _validate_operations(policy["operations"], data_classes, zones)
    claims = _validate_copy_claims(policy["copy_claims"], operations)
    enforcement = _validate_enforcement(
        policy["surface_enforcement"], operations, claims, repo_root
    )
    _validate_exceptions(policy["exceptions"], operations)
    _validate_unresolved_operator_forks(policy)
    check_surface_root_completeness(repo_root, enforcement["declared_roots"])
    return policy


def _validate_unresolved_operator_forks(policy: Mapping[str, Any]) -> None:
    governed = json.dumps(
        {
            "operations": policy["operations"],
            "copy_claims": policy["copy_claims"],
            "exceptions": policy["exceptions"],
        },
        sort_keys=True,
    ).casefold()
    present = sorted(marker for marker in UNRESOLVED_OPERATOR_FORK_MARKERS if marker in governed)
    if present:
        _fail("$", f"unresolved operator fork cannot enter v1 policy: {present}")


def _validate_named_definitions(value: object, path: str, property_name: str) -> set[str]:
    definitions = _array(value, path)
    identifiers: list[str] = []
    for index, item in enumerate(definitions):
        item_path = f"{path}[{index}]"
        definition = _object(item, item_path, {"id", property_name, "description"})
        identifiers.append(_string(definition["id"], f"{item_path}.id", identifier=True))
        _string(definition[property_name], f"{item_path}.{property_name}", identifier=True)
        _string(definition["description"], f"{item_path}.description")
    if not identifiers:
        _fail(path, "must not be empty")
    if identifiers != sorted(identifiers) or len(identifiers) != len(set(identifiers)):
        _fail(path, "IDs must be unique and sorted")
    return set(identifiers)


def _validate_operations(
    value: object, data_classes: set[str], zones: set[str]
) -> dict[str, dict[str, Any]]:
    items = _array(value, "$.operations")
    operations: dict[str, dict[str, Any]] = {}
    expected_keys = {
        "id",
        "surface",
        "from_zone",
        "to_zone",
        "transport",
        "method",
        "route",
        "request_contract",
        "response_contract",
        "request_data",
        "response_data",
        "destination_persistence",
        "logging",
        "guards",
        "admitting_issue",
        "enforcement_ids",
    }
    previous = ""
    for index, item in enumerate(items):
        path = f"$.operations[{index}]"
        operation = _object(item, path, expected_keys)
        identifier = _string(operation["id"], f"{path}.id", identifier=True)
        if identifier <= previous or identifier in operations:
            _fail("$.operations", "operation IDs must be unique and sorted")
        previous = identifier
        surface = _string(operation["surface"], f"{path}.surface", identifier=True)
        if surface not in SURFACES:
            _fail(f"{path}.surface", "unknown projection surface")
        for field in ("from_zone", "to_zone"):
            zone = _string(operation[field], f"{path}.{field}", identifier=True)
            if zone not in zones:
                _fail(f"{path}.{field}", "unknown zone")
        _string(operation["transport"], f"{path}.transport", identifier=True)
        _string(operation["method"], f"{path}.method")
        route = _string(operation["route"], f"{path}.route")
        if not route.startswith("/") or ROUTE_PATTERN.fullmatch(route) is None:
            _fail(f"{path}.route", "must be an exact route or named placeholder route")
        for field in ("request_contract", "response_contract"):
            contract = operation[field]
            if contract is not None:
                _string(contract, f"{path}.{field}", identifier=True)
        for field in ("request_data", "response_data", "destination_persistence", "logging"):
            for data_class in _string_array(operation[field], f"{path}.{field}", identifier=True):
                if data_class not in data_classes:
                    _fail(f"{path}.{field}", f"unknown data class {data_class}")
        _string_array(operation["guards"], f"{path}.guards", identifier=True)
        _issue(operation["admitting_issue"], f"{path}.admitting_issue")
        _string_array(operation["enforcement_ids"], f"{path}.enforcement_ids", identifier=True)
        if not operation["enforcement_ids"]:
            _fail(f"{path}.enforcement_ids", "must name at least one checker")
        _validate_operation_semantics(operation, path)
        operations[identifier] = operation
    if not operations:
        _fail("$.operations", "must not be empty")
    return operations


def _validate_operation_semantics(operation: Mapping[str, Any], path: str) -> None:
    moved = set(operation["request_data"]) | set(operation["response_data"])
    retained = set(operation["destination_persistence"]) | set(operation["logging"])
    content = {item for item in moved | retained if item.startswith("content.")}
    if operation["from_zone"].startswith("local-engine.") and operation["to_zone"].startswith(
        "premium-service."
    ):
        _fail(path, "local engine must not create premium-service operations")
    if operation["to_zone"] == "premium-service.memory" and "email-delivery" not in operation["id"]:
        forbidden = content | (moved & {"locator.feed-url", "locator.media-url"})
        if forbidden:
            _fail(path, f"premium account/ad operation carries local content: {sorted(forbidden)}")
    if operation["to_zone"] == "premium-service.database" and content:
        _fail(path, f"premium database cannot retain content: {sorted(content)}")
    if operation["from_zone"] in {"desktop.renderer", "transcript-frame"} or operation[
        "to_zone"
    ] in {"desktop.renderer", "transcript-frame"}:
        secrets = {item for item in moved | retained if item.startswith("secret.")}
        if secrets:
            _fail(path, f"renderer boundary cannot carry secrets: {sorted(secrets)}")
    if operation["id"].endswith("premium.house-ads"):
        allowed = {"locator.external-cta-url", "metadata.house-creative"}
        unexpected = set(operation["response_data"]) - allowed
        if unexpected:
            _fail(path, f"house creative response is not native-only: {sorted(unexpected)}")
    if operation["id"] in {"backend.premium.email-delivery", "desktop.premium.email-delivery"}:
        guards = set(operation["guards"])
        required = {
            "explicit-consent",
            "fresh-email-entitlement",
            "no-recipient-field",
            "subject-binding",
        }
        missing = required - guards
        if missing:
            _fail(path, f"email delivery is missing required guards: {sorted(missing)}")


def _validate_copy_claims(
    value: object, operations: Mapping[str, object]
) -> dict[str, dict[str, Any]]:
    claims: dict[str, dict[str, Any]] = {}
    previous = ""
    for index, item in enumerate(_array(value, "$.copy_claims")):
        path = f"$.copy_claims[{index}]"
        claim = _object(
            item,
            path,
            {"id", "operation_ids", "surfaces", "canonical_facts", "admitting_issue"},
        )
        identifier = _string(claim["id"], f"{path}.id", identifier=True)
        if identifier <= previous or identifier in claims:
            _fail("$.copy_claims", "claim IDs must be unique and sorted")
        previous = identifier
        operation_ids = _string_array(
            claim["operation_ids"], f"{path}.operation_ids", identifier=True
        )
        if not operation_ids:
            _fail(f"{path}.operation_ids", "must not be empty")
        for operation_id in operation_ids:
            if operation_id not in operations:
                _fail(f"{path}.operation_ids", f"unknown operation {operation_id}")
        _string_array(claim["surfaces"], f"{path}.surfaces", identifier=True)
        facts = _string_array(claim["canonical_facts"], f"{path}.canonical_facts")
        if not facts:
            _fail(f"{path}.canonical_facts", "must not be empty")
        _issue(claim["admitting_issue"], f"{path}.admitting_issue")
        claims[identifier] = claim
    return claims


def _validate_enforcement(
    value: object,
    operations: Mapping[str, dict[str, Any]],
    claims: Mapping[str, object],
    repo_root: Path,
) -> dict[str, Any]:
    enforcement = _object(
        value, "$.surface_enforcement", {"declared_roots", "surfaces", "legacy_fences"}
    )
    declared_roots = _array(enforcement["declared_roots"], "$.surface_enforcement.declared_roots")
    root_paths: list[str] = []
    for index, item in enumerate(declared_roots):
        path = f"$.surface_enforcement.declared_roots[{index}]"
        root = _object(
            item, path, {"path", "surface", "kind", "network_capable", "admitting_issue"}
        )
        root_path = _string(root["path"], f"{path}.path")
        if Path(root_path).is_absolute() or len(Path(root_path).parts) != 1:
            _fail(f"{path}.path", "must name one repository-root directory")
        if not (repo_root / root_path).is_dir():
            _fail(f"{path}.path", "declared root does not exist")
        root_paths.append(root_path)
        surface = _string(root["surface"], f"{path}.surface", identifier=True)
        if surface not in SURFACES:
            _fail(f"{path}.surface", "unknown surface")
        _string(root["kind"], f"{path}.kind", identifier=True)
        if not isinstance(root["network_capable"], bool):
            _fail(f"{path}.network_capable", "must be a boolean")
        _issue(root["admitting_issue"], f"{path}.admitting_issue")
    if root_paths != sorted(root_paths) or len(root_paths) != len(set(root_paths)):
        _fail("$.surface_enforcement.declared_roots", "paths must be unique and sorted")

    surfaces = _object(enforcement["surfaces"], "$.surface_enforcement.surfaces", set(SURFACES))
    all_checkers: set[str] = {"repo.surface-root-completeness"}
    for surface in SURFACES:
        path = f"$.surface_enforcement.surfaces.{surface}"
        config = _object(surfaces[surface], path, {"projection", "checker_ids", "consuming_tests"})
        expected_projection = f"projection-{surface}-v1.json"
        if config["projection"] != expected_projection:
            _fail(f"{path}.projection", f"must be {expected_projection}")
        checker_ids = _string_array(config["checker_ids"], f"{path}.checker_ids", identifier=True)
        tests = _string_array(config["consuming_tests"], f"{path}.consuming_tests")
        if not checker_ids or not tests:
            _fail(path, "must have checkers and consuming tests")
        for test in tests:
            if not (repo_root / test).is_file():
                _fail(f"{path}.consuming_tests", f"missing test {test}")
        all_checkers.update(checker_ids)

    for operation in operations.values():
        for checker in operation["enforcement_ids"]:
            if checker not in all_checkers:
                _fail(
                    f"$.operations.{operation['id']}.enforcement_ids", f"unknown checker {checker}"
                )

    legacy = _array(enforcement["legacy_fences"], "$.surface_enforcement.legacy_fences")
    previous = ""
    legacy_ids: set[str] = set()
    for index, item in enumerate(legacy):
        path = f"$.surface_enforcement.legacy_fences[{index}]"
        fence = _object(
            item,
            path,
            {
                "id",
                "path",
                "assertion",
                "operation_ids",
                "replacement_checker",
                "retained_as_defense_in_depth",
                "admitting_issue",
            },
        )
        identifier = _string(fence["id"], f"{path}.id", identifier=True)
        if identifier <= previous:
            _fail("$.surface_enforcement.legacy_fences", "IDs must be unique and sorted")
        previous = identifier
        legacy_ids.add(identifier)
        source = _string(fence["path"], f"{path}.path")
        if not (repo_root / source).is_file():
            _fail(f"{path}.path", "legacy fence path does not exist")
        _string(fence["assertion"], f"{path}.assertion")
        operation_ids = _string_array(
            fence["operation_ids"], f"{path}.operation_ids", identifier=True
        )
        if not operation_ids:
            _fail(f"{path}.operation_ids", "must map to at least one operation")
        for operation_id in operation_ids:
            if operation_id not in operations:
                _fail(f"{path}.operation_ids", f"unknown operation {operation_id}")
        checker = _string(
            fence["replacement_checker"], f"{path}.replacement_checker", identifier=True
        )
        if checker not in all_checkers:
            _fail(f"{path}.replacement_checker", f"unknown checker {checker}")
        if fence["retained_as_defense_in_depth"] is not True:
            _fail(
                f"{path}.retained_as_defense_in_depth", "stage 1 may not delete or weaken a fence"
            )
        _issue(fence["admitting_issue"], f"{path}.admitting_issue")

    if legacy_ids != REQUIRED_LEGACY_FENCES:
        missing = sorted(REQUIRED_LEGACY_FENCES - legacy_ids)
        unexpected = sorted(legacy_ids - REQUIRED_LEGACY_FENCES)
        _fail(
            "$.surface_enforcement.legacy_fences",
            f"complete stage-1 mapping required; missing={missing}, unexpected={unexpected}",
        )

    if not claims:
        _fail("$.copy_claims", "stage 1 requires policy-backed public claims")
    return enforcement


def _validate_exceptions(value: object, operations: Mapping[str, object]) -> None:
    previous = ""
    for index, item in enumerate(_array(value, "$.exceptions")):
        path = f"$.exceptions[{index}]"
        exception = _object(
            item,
            path,
            {
                "id",
                "operation_id",
                "reason",
                "owner",
                "expires_on",
                "removal_condition",
                "admitting_issue",
            },
        )
        identifier = _string(exception["id"], f"{path}.id", identifier=True)
        if identifier <= previous:
            _fail("$.exceptions", "IDs must be unique and sorted")
        previous = identifier
        operation_id = _string(exception["operation_id"], f"{path}.operation_id", identifier=True)
        if operation_id not in operations:
            _fail(f"{path}.operation_id", "unknown operation")
        _string(exception["reason"], f"{path}.reason")
        _string(exception["owner"], f"{path}.owner", identifier=True)
        expires = _string(exception["expires_on"], f"{path}.expires_on")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", expires) is None:
            _fail(f"{path}.expires_on", "must be an ISO calendar date")
        _string(exception["removal_condition"], f"{path}.removal_condition")
        _issue(exception["admitting_issue"], f"{path}.admitting_issue")


def discover_network_capable_roots(repo_root: Path) -> set[str]:
    roots: set[str] = set()
    for path in repo_root.rglob("*"):
        if not path.is_file() or path.suffix not in NETWORK_SOURCE_SUFFIXES:
            continue
        relative = path.relative_to(repo_root)
        if len(relative.parts) < 2 or any(part in IGNORED_SCAN_PARTS for part in relative.parts):
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if any(marker.search(source) for marker in NETWORK_MARKERS):
            roots.add(relative.parts[0])
    return roots


def check_surface_root_completeness(repo_root: Path, roots: Sequence[object]) -> None:
    declared = {
        str(root["path"])
        for root in roots
        if isinstance(root, dict) and root.get("network_capable") is True
    }
    discovered = discover_network_capable_roots(repo_root)
    undeclared = sorted(discovered - declared)
    stale = sorted(declared - discovered)
    if undeclared:
        _fail(
            "$.surface_enforcement.declared_roots",
            f"undeclared network-capable roots: {undeclared}",
        )
    if stale:
        _fail(
            "$.surface_enforcement.declared_roots",
            f"declared network-capable roots have no network owner: {stale}",
        )


def compile_projections(policy: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    enforcement = policy["surface_enforcement"]
    result: dict[str, dict[str, Any]] = {}
    for surface in SURFACES:
        operations = [item for item in policy["operations"] if item["surface"] == surface]
        operation_ids = {item["id"] for item in operations}
        claims = [
            item
            for item in policy["copy_claims"]
            if operation_ids.intersection(item["operation_ids"])
        ]
        fences = [
            item
            for item in enforcement["legacy_fences"]
            if operation_ids.intersection(item["operation_ids"])
        ]
        roots = [item for item in enforcement["declared_roots"] if item["surface"] == surface]
        result[surface] = {
            "schema_version": 1,
            "contract": "podcast-reader-boundary-projection",
            "policy_revision": policy["policy_revision"],
            "surface": surface,
            "roots": roots,
            "operations": operations,
            "copy_claims": claims,
            "legacy_fences": fences,
            "checker_ids": enforcement["surfaces"][surface]["checker_ids"],
        }
    return result


def canonical_json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=False, ensure_ascii=False) + "\n"


def _resolve_pointer(document: Any, pointer: str) -> tuple[Any, str]:
    if not pointer.startswith("/"):
        _fail("$.mutation.path", "must be an absolute JSON pointer")
    parts = [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]
    current = document
    for part in parts[:-1]:
        if isinstance(current, list):
            current = current[int(part)]
        elif isinstance(current, dict) and part in current:
            current = current[part]
        else:
            _fail("$.mutation.path", f"does not resolve at {part}")
    return current, parts[-1]


def apply_mutation(document: object, mutation_value: object) -> object:
    mutation = _object(mutation_value, "$.mutation", {"op", "path", "value"})
    operation = _string(mutation["op"], "$.mutation.op", identifier=True)
    if operation not in {"add", "remove", "replace"}:
        _fail("$.mutation.op", "must be add, remove, or replace")
    result = copy.deepcopy(document)
    parent, key = _resolve_pointer(result, _string(mutation["path"], "$.mutation.path"))
    if isinstance(parent, list):
        index = len(parent) if key == "-" else int(key)
        if operation == "add":
            parent.insert(index, mutation["value"])
        elif operation == "remove":
            parent.pop(index)
        else:
            parent[index] = mutation["value"]
    elif isinstance(parent, dict):
        if operation == "add":
            if key in parent:
                _fail("$.mutation.path", "add target already exists")
            parent[key] = mutation["value"]
        elif operation == "remove":
            if key not in parent:
                _fail("$.mutation.path", "remove target does not exist")
            del parent[key]
        else:
            if key not in parent:
                _fail("$.mutation.path", "replace target does not exist")
            parent[key] = mutation["value"]
    else:
        _fail("$.mutation.path", "parent must be an object or array")
    return result


def validate_conformance_vectors(value: object, policy: object, repo_root: Path) -> None:
    vectors = _object(value, "$vectors", {"schema_version", "contract", "valid", "invalid"})
    if vectors["schema_version"] != 1 or vectors["contract"] != "boundary-policy-v1":
        _fail("$vectors", "unsupported conformance contract")
    names: list[str] = []
    for index, item in enumerate(_array(vectors["valid"], "$vectors.valid")):
        path = f"$vectors.valid[{index}]"
        vector = _object(item, path, {"name", "document"})
        names.append(_string(vector["name"], f"{path}.name", identifier=True))
        if vector["document"] != "policy-v1.json":
            _fail(f"{path}.document", "must name the authoritative policy")
        validate_policy(policy, repo_root)
    for index, item in enumerate(_array(vectors["invalid"], "$vectors.invalid")):
        path = f"$vectors.invalid[{index}]"
        vector = _object(item, path, {"name", "mutation", "error_contains"})
        names.append(_string(vector["name"], f"{path}.name", identifier=True))
        expected = _string(vector["error_contains"], f"{path}.error_contains")
        mutated = apply_mutation(policy, vector["mutation"])
        try:
            validate_policy(mutated, repo_root)
        except PolicyError as error:
            if expected not in str(error):
                _fail(path, f"failed for the wrong reason: {error}")
        else:
            _fail(path, "invalid vector was accepted")
    if names != sorted(names) or len(names) != len(set(names)):
        _fail("$vectors", "vector names must be unique and sorted across both groups")


def write_or_check(repo_root: Path, *, check: bool) -> None:
    policy = load_json(repo_root / POLICY_PATH)
    validate_policy(policy, repo_root)
    validate_conformance_vectors(load_json(repo_root / VECTORS_PATH), policy, repo_root)
    schema = _object(
        load_json(repo_root / SCHEMA_PATH),
        "$schema",
        {
            "$schema",
            "$id",
            "title",
            "type",
            "additionalProperties",
            "required",
            "properties",
            "$defs",
        },
    )
    if schema["additionalProperties"] is not False:
        _fail("$schema.additionalProperties", "must fail closed")
    projections = compile_projections(policy)
    for surface, projection in projections.items():
        path = repo_root / POLICY_DIRECTORY / f"{PROJECTION_PREFIX}{surface}-v1.json"
        expected = canonical_json(projection)
        if check:
            if not path.is_file() or path.read_text(encoding="utf-8") != expected:
                raise PolicyError(f"{path}: stale or missing generated projection")
        else:
            path.write_text(expected, encoding="utf-8")
    from .boundary_inventory import check_surface_inventory

    check_surface_inventory(repo_root)


def repository_root() -> Path:
    return Path(__file__).resolve().parents[4]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--check", action="store_true", help="validate policy and committed projections"
    )
    mode.add_argument(
        "--write", action="store_true", help="validate policy and regenerate projections"
    )
    args = parser.parse_args(argv)
    write_or_check(repository_root(), check=bool(args.check))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
