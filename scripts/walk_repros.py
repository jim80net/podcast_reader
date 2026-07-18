"""Capture ephemeral product-walk repros and verify their durable disposition.

Capture is deliberately explicit: only paths named with ``--include`` are copied
from the temporary walk directory into a gitignored quarantine. Verification is
source-independent so the final manifest remains useful after ``/tmp`` expires.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
MAX_ARTIFACT_BYTES = 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
WALK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,79}$")
FINAL_DISPOSITIONS = {
    "regression-test",
    "retained-evidence",
    "environment-recipe",
    "discarded",
}
SENSITIVE_BASENAMES = {
    ".env",
    "cookies.txt",
    "credentials.json",
    "flotilla-secrets.env",
    "id_rsa",
    "id_ed25519",
}
SENSITIVE_CONTENT = (
    re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(rb"(?i)(?:api[_-]?key|access[_-]?token|password)\s*[:=]\s*['\"]?[^\s'\"]{12,}"),
    re.compile(rb"(?:github_pat_|gh[opsu]_|sk-)[A-Za-z0-9_-]{16,}"),
)


class WalkReproError(ValueError):
    """A capture request is unsafe or malformed."""


def _relative_path(raw: str) -> Path:
    """Return a normalized, traversal-free relative path."""
    candidate = PurePosixPath(raw.replace("\\", "/"))
    if candidate.is_absolute() or not candidate.parts:
        raise WalkReproError(f"artifact path must be relative: {raw!r}")
    if any(part in {"", ".", ".."} for part in candidate.parts):
        raise WalkReproError(f"artifact path contains traversal: {raw!r}")
    return Path(*candidate.parts)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _reject_sensitive(path: Path, data: bytes) -> None:
    basename = path.name.lower()
    if basename in SENSITIVE_BASENAMES or basename.startswith(".env."):
        raise WalkReproError(f"refusing sensitive-looking artifact name: {path}")
    if any(pattern.search(data) for pattern in SENSITIVE_CONTENT):
        raise WalkReproError(f"refusing secret-like content in artifact: {path}")


def _has_symlink_component(root: Path, relative: Path) -> bool:
    candidate = root
    for part in relative.parts:
        candidate /= part
        if candidate.is_symlink():
            return True
    return False


def capture_walk(
    *,
    source_root: Path,
    output: Path,
    walk_id: str,
    issue: int,
    includes: Sequence[str],
) -> Path:
    """Copy selected artifacts atomically and create a draft disposition manifest."""
    if WALK_ID_RE.fullmatch(walk_id) is None:
        raise WalkReproError("walk id must be 3-80 lowercase URL-safe characters")
    if issue <= 0:
        raise WalkReproError("issue must be a positive integer")
    if not includes:
        raise WalkReproError("capture requires at least one explicit --include")

    source_root = source_root.resolve()
    if not source_root.is_dir():
        raise WalkReproError(f"source directory does not exist: {source_root}")
    output = output.resolve()
    if output.exists():
        raise WalkReproError(f"output already exists: {output}")
    if output.is_relative_to(source_root):
        raise WalkReproError("output must not be inside the ephemeral source directory")

    relative_paths = [_relative_path(item) for item in includes]
    if len(set(relative_paths)) != len(relative_paths):
        raise WalkReproError("duplicate --include paths are not allowed")

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        artifacts: list[dict[str, Any]] = []
        for relative in relative_paths:
            source = source_root / relative
            if _has_symlink_component(source_root, relative):
                raise WalkReproError(f"refusing symlinked artifact path: {relative}")
            if not source.resolve().is_relative_to(source_root):
                raise WalkReproError(f"artifact escapes source directory: {relative}")
            if not source.is_file():
                raise WalkReproError(f"included artifact is not a file: {relative}")
            data = source.read_bytes()
            if len(data) > MAX_ARTIFACT_BYTES:
                raise WalkReproError(
                    f"artifact exceeds {MAX_ARTIFACT_BYTES} bytes; summarize or trim it: {relative}"
                )
            _reject_sensitive(relative, data)
            destination = temporary / "artifacts" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            destination.chmod(0o600)
            artifacts.append(
                {
                    "name": relative.as_posix(),
                    "sha256": _sha256(data),
                    "size_bytes": len(data),
                    "disposition": "pending",
                    "durable_paths": [],
                    "rationale": "",
                    "prior_failure_verified": False,
                }
            )

        manifest = {
            "schema": 1,
            "walk_id": walk_id,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "source": {"kind": "temporary", "path_hint": str(source_root)},
            "findings": [
                {
                    "issue": issue,
                    "summary": "",
                    "integrity_sensitive": False,
                    "artifacts": artifacts,
                    "scenarios": [],
                    "hostile_controls": [],
                }
            ],
        }
        manifest_path = temporary / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        manifest_path.chmod(0o600)
        temporary.rename(output)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return output / "manifest.json"


def _safe_durable_file(repo_root: Path, raw: object, field: str, errors: list[str]) -> None:
    if not isinstance(raw, str):
        errors.append(f"{field} must be a string")
        return
    try:
        relative = _relative_path(raw)
    except WalkReproError as exc:
        errors.append(f"{field}: {exc}")
        return
    if relative.parts[0] == ".walk-repros":
        errors.append(f"{field} points at gitignored quarantine, not durable evidence: {raw}")
        return
    repo_root = repo_root.resolve()
    candidate = repo_root / relative
    if _has_symlink_component(repo_root, relative) or not candidate.resolve().is_relative_to(
        repo_root
    ):
        errors.append(f"{field} uses a symlink outside durable repository evidence: {raw}")
        return
    if not candidate.is_file():
        errors.append(f"{field} does not exist: {raw}")


def verify_manifest(
    manifest_path: Path,
    *,
    repo_root: Path = REPO_ROOT,
    source_root: Path | None = None,
) -> list[str]:
    """Return all validation errors; an empty list means the manifest is durable."""
    try:
        document: object = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"cannot read manifest: {exc}"]
    if not isinstance(document, dict):
        return ["manifest root must be an object"]

    errors: list[str] = []
    if document.get("schema") != 1:
        errors.append("schema must equal 1")
    walk_id = document.get("walk_id")
    if not isinstance(walk_id, str) or WALK_ID_RE.fullmatch(walk_id) is None:
        errors.append("walk_id must be 3-80 lowercase URL-safe characters")
    findings = document.get("findings")
    if not isinstance(findings, list) or not findings:
        errors.append("findings must be a non-empty array")
        return errors

    resolved_source = source_root.resolve() if source_root is not None else None
    for finding_index, finding in enumerate(findings):
        prefix = f"findings[{finding_index}]"
        if not isinstance(finding, dict):
            errors.append(f"{prefix} must be an object")
            continue
        if not isinstance(finding.get("issue"), int) or finding["issue"] <= 0:
            errors.append(f"{prefix}.issue must be a positive integer")
        if not isinstance(finding.get("summary"), str) or not finding["summary"].strip():
            errors.append(f"{prefix}.summary must be non-empty")
        scenarios = finding.get("scenarios")
        if not isinstance(scenarios, list) or not scenarios:
            errors.append(f"{prefix}.scenarios must inventory at least one verified scenario")
        elif any(not isinstance(item, str) or not item.strip() for item in scenarios):
            errors.append(f"{prefix}.scenarios entries must be non-empty strings")

        controls = finding.get("hostile_controls")
        if finding.get("integrity_sensitive") is True and (
            not isinstance(controls, list) or not controls
        ):
            errors.append(f"{prefix} is integrity-sensitive and requires a hostile control")
        if isinstance(controls, list):
            for control_index, control in enumerate(controls):
                control_prefix = f"{prefix}.hostile_controls[{control_index}]"
                if not isinstance(control, dict):
                    errors.append(f"{control_prefix} must be an object")
                    continue
                if (
                    not isinstance(control.get("description"), str)
                    or not control["description"].strip()
                ):
                    errors.append(f"{control_prefix}.description must be non-empty")
                _safe_durable_file(
                    repo_root, control.get("durable_path"), f"{control_prefix}.durable_path", errors
                )

        artifacts = finding.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            errors.append(f"{prefix}.artifacts must be a non-empty array")
            continue
        for artifact_index, artifact in enumerate(artifacts):
            artifact_prefix = f"{prefix}.artifacts[{artifact_index}]"
            if not isinstance(artifact, dict):
                errors.append(f"{artifact_prefix} must be an object")
                continue
            name = artifact.get("name")
            try:
                relative = _relative_path(name) if isinstance(name, str) else None
            except WalkReproError as exc:
                errors.append(f"{artifact_prefix}.name: {exc}")
                relative = None
            if relative is None:
                errors.append(f"{artifact_prefix}.name must be a relative path")
            digest = artifact.get("sha256")
            if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
                errors.append(f"{artifact_prefix}.sha256 must be lowercase SHA-256")
            disposition = artifact.get("disposition")
            if disposition not in FINAL_DISPOSITIONS:
                errors.append(f"{artifact_prefix}.disposition must be final, not {disposition!r}")
            rationale = artifact.get("rationale")
            if not isinstance(rationale, str) or not rationale.strip():
                errors.append(f"{artifact_prefix}.rationale must be non-empty")
            durable_paths = artifact.get("durable_paths")
            if disposition != "discarded" and (
                not isinstance(durable_paths, list) or not durable_paths
            ):
                errors.append(f"{artifact_prefix} requires at least one durable path")
            if isinstance(durable_paths, list):
                for path_index, durable_path in enumerate(durable_paths):
                    _safe_durable_file(
                        repo_root,
                        durable_path,
                        f"{artifact_prefix}.durable_paths[{path_index}]",
                        errors,
                    )
            if (
                disposition == "regression-test"
                and artifact.get("prior_failure_verified") is not True
            ):
                errors.append(
                    f"{artifact_prefix} regression test must be proven against "
                    "the prior implementation"
                )
            if resolved_source is not None and relative is not None and isinstance(digest, str):
                source = resolved_source / relative
                if source.is_symlink() or not source.is_file():
                    errors.append(f"{artifact_prefix} source artifact is missing: {relative}")
                elif _sha256(source.read_bytes()) != digest:
                    errors.append(f"{artifact_prefix} source hash changed: {relative}")
    return errors


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    capture = subparsers.add_parser("capture", help="copy selected files into quarantine")
    capture.add_argument("--source", type=Path, required=True)
    capture.add_argument("--output", type=Path, required=True)
    capture.add_argument("--walk-id", required=True)
    capture.add_argument("--issue", type=int, required=True)
    capture.add_argument("--include", action="append", default=[])
    verify = subparsers.add_parser("verify", help="validate checked-in disposition manifests")
    verify.add_argument("manifests", type=Path, nargs="+")
    verify.add_argument("--source-root", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "capture":
        try:
            manifest = capture_walk(
                source_root=args.source,
                output=args.output,
                walk_id=args.walk_id,
                issue=args.issue,
                includes=args.include,
            )
        except WalkReproError as exc:
            print(f"capture refused: {exc}", file=sys.stderr)
            return 2
        print(manifest)
        return 0

    failed = False
    for manifest in args.manifests:
        errors = verify_manifest(manifest, source_root=args.source_root)
        if errors:
            failed = True
            for error in errors:
                print(f"{manifest}: {error}", file=sys.stderr)
        else:
            print(f"{manifest}: durable")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
