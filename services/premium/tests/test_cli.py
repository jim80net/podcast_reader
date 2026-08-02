from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
import uvicorn

import podcast_reader_premium.cli as cli


def test_serve_uses_design_loopback_port(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    observed: dict[str, Any] = {}
    monkeypatch.setenv("PREMIUM_USER_CODE_PEPPER", "a-secure-test-pepper-with-at-least-32-bytes")
    monkeypatch.setattr(cli, "require_current_schema", lambda engine: None)
    monkeypatch.setattr(cli, "create_database", lambda settings: object())
    monkeypatch.setattr(cli, "create_app", lambda settings: object())
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: observed.update(kwargs))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "premium-dev",
            "--database",
            str(tmp_path / "premium.sqlite3"),
            "serve",
        ],
    )
    cli.main()
    assert observed == {"host": "127.0.0.1", "port": 8090, "workers": 1}


def test_serve_rejects_invalid_port(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PREMIUM_USER_CODE_PEPPER", "a-secure-test-pepper-with-at-least-32-bytes")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "premium-dev",
            "--database",
            str(tmp_path / "premium.sqlite3"),
            "serve",
            "--port",
            "0",
        ],
    )
    with pytest.raises(SystemExit, match="between 1 and 65535"):
        cli.main()
