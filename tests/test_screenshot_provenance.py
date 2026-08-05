from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from scripts.check_screenshot_provenance import (
    CAPTURE_METADATA_PATH,
    MANIFEST_PATH,
    PROVENANCE_PATH,
    WAIVER_PATH,
    enforce_drift_policy,
    render_provenance,
    validate_provenance,
)

REPO_ROOT = Path(__file__).parents[1]


def test_committed_screenshot_provenance_is_valid() -> None:
    validate_provenance(REPO_ROOT)


def test_modified_screenshot_fails_closed(tmp_path: Path) -> None:
    screenshot_dir = tmp_path / MANIFEST_PATH.parent
    screenshot_dir.mkdir(parents=True)
    shutil.copytree(REPO_ROOT / MANIFEST_PATH.parent, screenshot_dir, dirs_exist_ok=True)
    target = screenshot_dir / "01-first-run-wizard-100pct-light.png"
    target.write_bytes(target.read_bytes() + b"tampered")

    with pytest.raises(ValueError, match="bytes do not match"):
        validate_provenance(tmp_path)


def test_human_provenance_must_match_manifest(tmp_path: Path) -> None:
    screenshot_dir = tmp_path / MANIFEST_PATH.parent
    screenshot_dir.mkdir(parents=True)
    shutil.copytree(REPO_ROOT / MANIFEST_PATH.parent, screenshot_dir, dirs_exist_ok=True)
    manifest = json.loads((tmp_path / MANIFEST_PATH).read_text(encoding="utf-8"))
    (tmp_path / PROVENANCE_PATH).write_text("stale\n", encoding="utf-8")

    with pytest.raises(ValueError, match="exact rendering"):
        validate_provenance(tmp_path)
    assert render_provenance(manifest) == (REPO_ROOT / PROVENANCE_PATH).read_text(encoding="utf-8")


def test_modified_capture_metadata_fails_closed(tmp_path: Path) -> None:
    screenshot_dir = tmp_path / MANIFEST_PATH.parent
    screenshot_dir.mkdir(parents=True)
    shutil.copytree(REPO_ROOT / MANIFEST_PATH.parent, screenshot_dir, dirs_exist_ok=True)
    metadata = tmp_path / CAPTURE_METADATA_PATH
    metadata.write_bytes(metadata.read_bytes() + b"\n")

    with pytest.raises(ValueError, match="capture metadata does not match"):
        validate_provenance(tmp_path)


def test_ui_changes_require_refresh_or_reviewed_waiver() -> None:
    with pytest.raises(ValueError, match="UI-affecting files changed"):
        enforce_drift_policy({"app/src/renderer/src/App.tsx"})

    enforce_drift_policy({"app/src/renderer/src/App.tsx", str(MANIFEST_PATH)})
    enforce_drift_policy({"app/src/renderer/src/App.tsx", str(WAIVER_PATH)})
    enforce_drift_policy({"README.md"})
