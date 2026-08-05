#!/usr/bin/env python3
"""Fail closed when published screenshots or their UI source drift from evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parents[1]
SCREENSHOT_DIR = Path("site/assets/screenshots")
MANIFEST_PATH = SCREENSHOT_DIR / "provenance.json"
PROVENANCE_PATH = SCREENSHOT_DIR / "PROVENANCE.md"
WAIVER_PATH = SCREENSHOT_DIR / "DRIFT-WAIVERS.md"
CAPTURE_METADATA_PATH = SCREENSHOT_DIR / "capture-metadata.json"
UI_PREFIXES = ("app/src/renderer/", "app/tests/install/")
UI_FILES = {"app/package.json", "app/package-lock.json"}
SHA256 = re.compile(r"[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(root: Path) -> dict[str, Any]:
    manifest = json.loads((root / MANIFEST_PATH).read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("screenshot provenance manifest must be a JSON object")
    return cast("dict[str, Any]", manifest)


def render_provenance(manifest: dict[str, Any]) -> str:
    source = manifest["source"]
    lines = [
        "# Installed-app screenshot provenance",
        "",
        "These files are byte-for-byte copies from the authoritative installed Windows",
        "walkthrough on `main`. They have not been cropped, retouched, or recompressed.",
        "",
        f"- Source commit: `{source['commit']}`",
        f"- GitHub Actions run: `{source['workflow_run']}`",
        f"- Artifact: `{source['artifact']}`",
        f"- Artifact SHA-256: `{source['artifact_digest'].removeprefix('sha256:')}`",
        f"- Capture metadata SHA-256: `{source['capture_metadata_sha256']}`",
        f"- Capture validation: {source['inventory_frames']}/24 frames; "
        f"{source['renderer_console']} renderer console; decoded PNGs with exact",
        "  physical dimensions and pixel variance at or above 16",
        "",
        "| Committed filename | Source filename | SHA-256 |",
        "|---|---|---|",
    ]
    for filename, evidence in manifest["files"].items():
        lines.append(f"| `{filename}` | `{evidence['source_filename']}` | `{evidence['sha256']}` |")
    return "\n".join(lines) + "\n"


def validate_provenance(root: Path) -> None:
    manifest = load_manifest(root)
    if manifest.get("schema") != 1:
        raise ValueError("screenshot provenance schema must be 1")
    source = manifest.get("source", {})
    commit = source.get("commit", "")
    run = source.get("workflow_run")
    digest = source.get("artifact_digest", "")
    if not COMMIT.fullmatch(commit):
        raise ValueError("source commit must be a full lowercase commit SHA")
    if not isinstance(run, int) or run <= 0:
        raise ValueError("workflow_run must be a positive integer")
    if source.get("workflow_url") != (
        f"https://github.com/jim80net/podcast_reader/actions/runs/{run}"
    ):
        raise ValueError("workflow URL does not agree with workflow_run")
    if source.get("artifact") != f"walkthrough-{commit}":
        raise ValueError("artifact name does not agree with source commit")
    if (
        not isinstance(digest, str)
        or not digest.startswith("sha256:")
        or not SHA256.fullmatch(digest.removeprefix("sha256:"))
    ):
        raise ValueError("artifact digest must be a SHA-256")
    if source.get("inventory_frames") != 24 or source.get("renderer_console") != "empty":
        raise ValueError("source walkthrough must prove 24 frames and an empty renderer console")

    metadata_hash = source.get("capture_metadata_sha256", "")
    metadata_path = root / CAPTURE_METADATA_PATH
    if not SHA256.fullmatch(metadata_hash) or sha256_file(metadata_path) != metadata_hash:
        raise ValueError("capture metadata does not match its provenance SHA-256")
    capture_records = json.loads(metadata_path.read_text(encoding="utf-8"))
    if not isinstance(capture_records, list) or len(capture_records) != 24:
        raise ValueError("capture metadata must contain exactly 24 frames")
    records_by_filename = {record.get("filename"): record for record in capture_records}
    if len(records_by_filename) != 24 or None in records_by_filename:
        raise ValueError("capture metadata filenames must be present and unique")
    if {record.get("surface") for record in capture_records} != {
        "first-run-wizard",
        "empty-library",
        "new-view-submitted",
        "new-view-job-done",
        "reader-transcript",
        "settings",
    }:
        raise ValueError("capture metadata must cover all six installed-app surfaces")
    for record in capture_records:
        expected_dpr = {"100pct": 1, "125pct": 1.25}.get(record.get("scale"))
        evidence = record.get("evidence", {})
        if (
            record.get("devicePixelRatio") != expected_dpr
            or record.get("theme") not in {"light", "dark"}
            or evidence.get("width", 0) <= 0
            or evidence.get("height", 0) <= 0
            or evidence.get("pixelVariance", 0) < 16
        ):
            raise ValueError(f"{record.get('filename')}: invalid capture metadata evidence")

    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("provenance must list published screenshots")
    actual_pngs = {path.name for path in (root / SCREENSHOT_DIR).glob("*.png")}
    if actual_pngs != set(files):
        raise ValueError("published PNG inventory does not match provenance manifest")
    for filename, evidence in files.items():
        if evidence.get("source_filename") != filename:
            raise ValueError(f"{filename}: source filename must match committed filename")
        expected_hash = evidence.get("sha256", "")
        if not SHA256.fullmatch(expected_hash):
            raise ValueError(f"{filename}: invalid SHA-256")
        if sha256_file(root / SCREENSHOT_DIR / filename) != expected_hash:
            raise ValueError(f"{filename}: bytes do not match provenance SHA-256")
        if evidence.get("width") != 1008 or evidence.get("height") != 655:
            raise ValueError(f"{filename}: published capture must be 1008x655")
        if evidence.get("device_pixel_ratio") != 1:
            raise ValueError(f"{filename}: published capture must record DPR 1")
        if evidence.get("pixel_variance", 0) < 16:
            raise ValueError(f"{filename}: capture fails the pixel-variance threshold")
        source_record = records_by_filename.get(filename, {})
        source_evidence = source_record.get("evidence", {})
        if (
            evidence.get("width") != source_evidence.get("width")
            or evidence.get("height") != source_evidence.get("height")
            or evidence.get("device_pixel_ratio") != source_record.get("devicePixelRatio")
            or evidence.get("pixel_variance") != source_evidence.get("pixelVariance")
        ):
            raise ValueError(f"{filename}: manifest evidence does not match capture metadata")

    expected_markdown = render_provenance(manifest)
    actual_markdown = (root / PROVENANCE_PATH).read_text(encoding="utf-8")
    if actual_markdown != expected_markdown:
        raise ValueError("PROVENANCE.md is not the exact rendering of provenance.json")


def changed_paths(root: Path, base_ref: str) -> set[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base_ref}...HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return {line for line in result.stdout.splitlines() if line}


def enforce_drift_policy(paths: set[str]) -> None:
    ui_changes = {
        path
        for path in paths
        if path in UI_FILES or any(path.startswith(prefix) for prefix in UI_PREFIXES)
    }
    if not ui_changes:
        return
    if str(MANIFEST_PATH) in paths or str(WAIVER_PATH) in paths:
        return
    changed = "\n".join(f"  - {path}" for path in sorted(ui_changes))
    raise ValueError(
        "UI-affecting files changed without refreshed screenshot provenance or an explicit "
        f"drift waiver:\n{changed}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-ref", help="base commit/ref for UI drift enforcement")
    args = parser.parse_args()
    validate_provenance(REPO_ROOT)
    if args.base_ref:
        enforce_drift_policy(changed_paths(REPO_ROOT, args.base_ref))
    print("published screenshot provenance is valid")


if __name__ == "__main__":
    main()
