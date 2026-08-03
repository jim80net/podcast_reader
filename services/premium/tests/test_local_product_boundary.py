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
    premium_consumer_files = {
        desktop_root / "main" / "index.ts",
        desktop_root / "main" / "ipc.ts",
        desktop_root / "main" / "ipc.test.ts",
        desktop_root / "preload" / "index.ts",
        desktop_root / "shared" / "ipc.ts",
        desktop_root / "renderer" / "src" / "premium-ad-slot.ts",
        desktop_root / "renderer" / "src" / "views" / "library.ts",
        desktop_root / "renderer" / "src" / "views" / "reader.ts",
        desktop_root / "renderer" / "src" / "views" / "settings.ts",
        desktop_root / "renderer" / "src" / "views" / "premium-account-section.ts",
    }
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
            assert (
                re.search(
                    r"from\s+['\"](?:(?:\.\./)+|@[^/'\"]+/)?engine(?:[-/'\"])",
                    folded,
                )
                is None
            ), path
        elif path not in premium_consumer_files:
            assert "premium" not in source.casefold(), path

    renderer_contract = (desktop_root / "shared" / "ipc.ts").read_text(encoding="utf-8")
    for credential_field in (
        r"\baccess_token\s*[?:]",
        r"\brefresh_token\s*[?:]",
        r"\bauthorization\s*[?:]",
        r"\bbearer\s*[?:]",
    ):
        assert re.search(credential_field, renderer_contract.casefold()) is None

    ad_slot = (desktop_root / "renderer" / "src" / "premium-ad-slot.ts").read_text(
        encoding="utf-8"
    ).casefold()
    for forbidden_surface in ("innerhtml", "webview", "<img", "script", "impression", "analytics"):
        assert forbidden_surface not in ad_slot
    manifests_text = "\n".join(path.read_text(encoding="utf-8").casefold() for path in manifests)
    for forbidden_dependency in ("doubleclick", "google-ads", "admob", "facebook-pixel"):
        assert forbidden_dependency not in manifests_text
