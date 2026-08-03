from __future__ import annotations

import re
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

    desktop_root = root / "app" / "src"
    premium_boundary = desktop_root / "main" / "premium"
    desktop_sources = [
        path
        for path in desktop_root.rglob("*")
        if path.is_file() and path.suffix in {".ts", ".tsx", ".js", ".json"}
    ]
    assert desktop_sources
    for path in desktop_sources:
        source = path.read_text(encoding="utf-8")
        if path.is_relative_to(premium_boundary):
            folded = source.casefold()
            assert "127.0.0.1" not in folded and "localhost" not in folded, path
            assert re.search(
                r"from\s+['\"](?:(?:\.\./)+|@[^/'\"]+/)?engine(?:[-/'\"])",
                folded,
            ) is None, path
        else:
            assert "premium" not in source.casefold(), path
