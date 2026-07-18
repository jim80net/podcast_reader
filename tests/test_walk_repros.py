from __future__ import annotations

import importlib.util
import json
import stat
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_script() -> ModuleType:
    path = REPO_ROOT / "scripts" / "walk_repros.py"
    spec = importlib.util.spec_from_file_location("walk_repros", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


walk_repros = _load_script()


def test_capture_is_explicit_atomic_and_hashed(tmp_path: Path) -> None:
    source = tmp_path / "ephemeral"
    source.mkdir()
    (source / "repro.mjs").write_text("console.log('verified')\n", encoding="utf-8")
    (source / "ignored.png").write_bytes(b"not selected")
    output = tmp_path / "quarantine" / "walk"

    manifest_path = walk_repros.capture_walk(
        source_root=source,
        output=output,
        walk_id="2026-07-18-example",
        issue=99,
        includes=["repro.mjs"],
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact = manifest["findings"][0]["artifacts"][0]
    assert artifact == {
        "name": "repro.mjs",
        "sha256": "6a832efd2ff82400adc445cc4d57f4f1d44f5656e8ca63ecf65e55b2efc765e0",
        "size_bytes": 24,
        "disposition": "pending",
        "durable_paths": [],
        "rationale": "",
        "prior_failure_verified": False,
    }
    assert (output / "artifacts" / "repro.mjs").read_bytes() == (source / "repro.mjs").read_bytes()
    assert not (output / "artifacts" / "ignored.png").exists()
    assert stat.S_IMODE(manifest_path.stat().st_mode) == 0o600
    assert stat.S_IMODE((output / "artifacts" / "repro.mjs").stat().st_mode) == 0o600
    assert set(walk_repros.verify_manifest(manifest_path, repo_root=tmp_path)) == {
        "findings[0].summary must be non-empty",
        "findings[0].scenarios must inventory at least one verified scenario",
        "findings[0].artifacts[0].disposition must be final, not 'pending'",
        "findings[0].artifacts[0] requires at least one durable path",
        "findings[0].artifacts[0].rationale must be non-empty",
    }


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        ("../escape.mjs", b"safe", "traversal"),
        (".env", b"KEY=not-even-needed", "sensitive-looking"),
        ("repro.mjs", b"API_KEY=abcdefghijklmnop", "secret-like"),
    ],
)
def test_capture_refuses_unsafe_artifacts(
    tmp_path: Path, name: str, content: bytes, message: str
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    target = source / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)

    with pytest.raises(walk_repros.WalkReproError, match=message):
        walk_repros.capture_walk(
            source_root=source,
            output=tmp_path / "capture",
            walk_id="2026-07-18-example",
            issue=99,
            includes=[name],
        )
    assert not (tmp_path / "capture").exists()


def test_capture_refuses_symlinked_parent(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "repro.mjs").write_text("console.log('outside')\n", encoding="utf-8")
    try:
        (source / "linked").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("host does not permit symlink creation")

    with pytest.raises(walk_repros.WalkReproError, match="symlinked artifact path"):
        walk_repros.capture_walk(
            source_root=source,
            output=tmp_path / "capture",
            walk_id="2026-07-18-example",
            issue=99,
            includes=["linked/repro.mjs"],
        )


def test_verify_requires_prior_failure_and_hostile_control(tmp_path: Path) -> None:
    durable = tmp_path / "tests" / "repro.spec.ts"
    durable.parent.mkdir()
    durable.write_text("test('regression', () => {})\n", encoding="utf-8")
    manifest = {
        "schema": 1,
        "walk_id": "2026-07-18-example",
        "findings": [
            {
                "issue": 99,
                "summary": "A real finding.",
                "integrity_sensitive": True,
                "scenarios": ["Benign input remains accepted."],
                "hostile_controls": [],
                "artifacts": [
                    {
                        "name": "repro.mjs",
                        "sha256": "0" * 64,
                        "disposition": "regression-test",
                        "durable_paths": ["tests/repro.spec.ts"],
                        "rationale": "Normalized into a stable fixture.",
                        "prior_failure_verified": False,
                    }
                ],
            }
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    errors = walk_repros.verify_manifest(manifest_path, repo_root=tmp_path)

    assert errors == [
        "findings[0] is integrity-sensitive and requires a hostile control",
        "findings[0].artifacts[0] regression test must be proven against the prior implementation",
    ]


def test_issue_92_manifest_is_durable_without_temporary_source() -> None:
    manifest = REPO_ROOT / "docs" / "walk-repros" / "2026-07-16-search.json"
    assert walk_repros.verify_manifest(manifest) == []
