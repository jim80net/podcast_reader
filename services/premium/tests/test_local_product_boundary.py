from __future__ import annotations

from pathlib import Path


def test_premium_service_is_absent_from_local_engine_and_desktop_dependency_closure() -> None:
    root = Path(__file__).resolve().parents[3]
    manifests = [
        root / "pyproject.toml",
        root / "uv.lock",
        root / "app" / "package.json",
        root / "app" / "package-lock.json",
    ]
    forbidden = ("podcast-reader-premium", "podcast_reader_premium", "services/premium")
    for path in manifests:
        source = path.read_text(encoding="utf-8")
        assert not any(value in source for value in forbidden), path

    desktop_sources = [
        path
        for path in (root / "app" / "src").rglob("*")
        if path.is_file() and path.suffix in {".ts", ".tsx", ".js", ".json"}
    ]
    assert desktop_sources
    for path in desktop_sources:
        source = path.read_text(encoding="utf-8")
        assert "premium" not in source.casefold(), path
