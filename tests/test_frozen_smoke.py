"""Focused contract tests for the standalone frozen-engine smoke harness."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType

    import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "packaging" / "frozen_smoke.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("frozen_smoke", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_boot_only_stops_after_authenticated_version_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    smoke = _load()
    engine_path = tmp_path / "podcast-reader-engine"
    engine_path.write_text("fixture", encoding="utf-8")
    data_dir = tmp_path / "data"
    calls: list[str] = []

    class FakeEngine:
        def __init__(self, binary: Path, actual_data_dir: Path, timeout: float) -> None:
            assert binary == engine_path.resolve()
            assert actual_data_dir == data_dir
            assert timeout == 60
            calls.append("engine")

        def shutdown(self) -> None:
            calls.append("shutdown")

    monkeypatch.setattr(smoke, "Engine", FakeEngine)
    monkeypatch.setattr(smoke, "pyproject_version", lambda: "0.3.0")
    monkeypatch.setattr(smoke, "assert_version", lambda _engine, _version: calls.append("version"))
    for name in (
        "assert_engine_state_permissions",
        "assert_web_assets",
        "assert_serve_guardian",
        "install_pack",
        "assert_pack_manifest",
        "assert_cuda_loader",
        "transcribe_fixture",
    ):
        monkeypatch.setattr(smoke, name, lambda *_args, step=name: calls.append(step))

    smoke.main([str(engine_path), "--boot-only", "--data-dir", str(data_dir)])

    assert calls == ["engine", "version", "shutdown"]
