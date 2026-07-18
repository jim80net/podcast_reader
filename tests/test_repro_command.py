from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType

    import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_script() -> ModuleType:
    path = REPO_ROOT / "scripts" / "repro.py"
    spec = importlib.util.spec_from_file_location("repro", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


repro = _load_script()


def _seed_ready_repo(root: Path) -> None:
    for package in ("app", "extension"):
        dependency = root / package / "node_modules/@playwright/test/package.json"
        dependency.parent.mkdir(parents=True)
        dependency.write_text("{}\n", encoding="utf-8")
    electron = root / "app/node_modules/electron/package.json"
    electron.parent.mkdir(parents=True, exist_ok=True)
    electron.write_text("{}\n", encoding="utf-8")
    manifest = root / "docs/walk-repros/example.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}\n", encoding="utf-8")
    for relative in repro.GOLDEN_FIXTURES:
        golden = root / relative
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text("fixture\n", encoding="utf-8")


def test_default_walk_plan_has_manifest_golden_and_browser_steps(tmp_path: Path) -> None:
    manifest = tmp_path / "docs/walk-repros/example.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}\n", encoding="utf-8")

    steps = repro.build_steps(tmp_path, ("walk",), "extension decoration")

    assert [step.label for step in steps] == [
        "verify durable walk manifests",
        "verify generated transcript goldens",
        "run artifact browser regressions",
    ]
    assert steps[0].argv[-1] == "docs/walk-repros/example.json"
    assert steps[2].argv[-2:] == ("--grep", "extension decoration")
    assert not any(step.needs_display for step in steps)


def test_app_and_extension_plans_own_build_and_display_wrapping(tmp_path: Path) -> None:
    steps = repro.build_steps(tmp_path, ("app", "extension"))
    assert [(step.suite, step.argv[:3], step.needs_display) for step in steps] == [
        ("app", ("npm", "run", "build"), False),
        ("app", ("npx", "--no-install", "playwright"), True),
        ("extension", ("npm", "run", "build"), False),
        ("extension", ("npx", "--no-install", "playwright"), True),
    ]


def test_headless_linux_materializes_xvfb_only_for_display_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(repro.sys, "platform", "linux")
    build = repro.Step("app", "build", REPO_ROOT, ("npm", "run", "build"))
    browser = repro.Step(
        "app", "browser", REPO_ROOT, ("npx", "playwright", "test"), needs_display=True
    )
    assert repro._materialize(build, {}) == build.argv
    assert repro._materialize(browser, {}) == (
        "xvfb-run",
        "-a",
        "npx",
        "playwright",
        "test",
    )


def test_preflight_reports_environment_gaps_before_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(repro, "_which", lambda _name: None)

    errors = repro.prerequisite_errors(tmp_path, ("walk", "app"), environ={}, platform="linux")

    assert "uv is missing" in errors[0]
    assert any("Node/npm/npx are missing" in error for error in errors)
    assert any("app dependencies are missing" in error for error in errors)
    assert any("Electron is missing" in error for error in errors)
    assert any("no durable walk manifests" in error for error in errors)
    assert sum("generated golden is missing" in error for error in errors) == 4
    assert any("no display or xvfb-run" in error for error in errors)


def test_preflight_ready_with_dependencies_and_xvfb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_ready_repo(tmp_path)
    monkeypatch.setattr(repro, "_which", lambda name: f"/tools/{name}")
    monkeypatch.setattr(repro, "_node_major", lambda _root: 24)
    monkeypatch.setattr(repro, "_python_ready", lambda _root: True)
    monkeypatch.setattr(repro, "_browser_executable", lambda root: root / "chromium")
    monkeypatch.setattr(repro, "_electron_executable", lambda root: root / "electron")

    assert (
        repro.prerequisite_errors(tmp_path, repro.SUITE_ORDER, environ={}, platform="linux") == []
    )


def test_check_only_distinguishes_unavailable_environment(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(repro, "prerequisite_errors", lambda *_args, **_kwargs: ["Node missing"])

    assert repro.main(("app", "--check-only")) == repro.ENVIRONMENT_UNAVAILABLE
    captured = capsys.readouterr()
    assert "ENVIRONMENT UNAVAILABLE" in captured.err
    assert "no tests were started" in captured.err
    assert "PRODUCT ASSERTION FAILED" not in captured.err


def test_failed_step_is_reported_as_product_assertion(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    step = repro.Step("walk", "focused proof", REPO_ROOT, ("false",))
    monkeypatch.setattr(
        repro.subprocess, "run", lambda *_args, **_kwargs: type("R", (), {"returncode": 7})()
    )

    assert repro.run_steps((step,), os.environ) == 1
    assert "PRODUCT ASSERTION FAILED" in capsys.readouterr().err


def test_disappearing_executable_is_environment_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    step = repro.Step("app", "focused proof", REPO_ROOT, ("vanished",))

    def missing(*_args: object, **_kwargs: object) -> object:
        raise FileNotFoundError("vanished")

    monkeypatch.setattr(repro.subprocess, "run", missing)
    assert repro.run_steps((step,), os.environ) == repro.ENVIRONMENT_UNAVAILABLE
    captured = capsys.readouterr()
    assert "ENVIRONMENT BECAME UNAVAILABLE" in captured.err
    assert "PRODUCT ASSERTION FAILED" not in captured.err


def test_ci_app_and_extension_jobs_use_unified_command() -> None:
    workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "python3 scripts/repro.py extension" in workflow
    assert "uv run python scripts/repro.py app" in workflow
