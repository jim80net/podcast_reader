from __future__ import annotations

import importlib.util
import socket
import sqlite3
from pathlib import Path
from types import ModuleType

import pytest


def _helper() -> ModuleType:
    path = Path(__file__).parents[1] / "scripts" / "dev_host.py"
    spec = importlib.util.spec_from_file_location("dev_host", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_serve_conflict_check_fails_closed_for_tcp_or_web_listener() -> None:
    helper = _helper()
    helper.assert_serve_listener_unused({"TCP": {}, "Web": {}}, 8443)
    with pytest.raises(RuntimeError, match="refusing deployment"):
        helper.assert_serve_listener_unused({"TCP": {"8443": {"HTTPS": True}}}, 8443)
    with pytest.raises(RuntimeError, match="refusing deployment"):
        helper.assert_serve_listener_unused(
            {"Web": {"host.example.ts.net:8443": {"Handlers": {}}}}, 8443
        )


def test_loopback_port_check_rejects_an_occupied_socket() -> None:
    helper = _helper()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
        occupied.bind(("127.0.0.1", 0))
        port = occupied.getsockname()[1]
        with pytest.raises(RuntimeError, match="already in use"):
            helper.assert_loopback_port_unused(port)


def test_unit_rendering_is_complete_and_rejects_injected_lines() -> None:
    helper = _helper()
    assert helper.render_unit("ExecStart=@COMMAND@\n", {"COMMAND": "/bin/true"}) == (
        "ExecStart=/bin/true\n"
    )
    with pytest.raises(ValueError, match="newline"):
        helper.render_unit("@COMMAND@", {"COMMAND": "/bin/true\nExecStart=/bin/false"})
    with pytest.raises(ValueError, match="unresolved"):
        helper.render_unit("@MISSING@", {})


def test_backup_restore_proof_preserves_every_table_count(tmp_path: Path) -> None:
    helper = _helper()
    source = tmp_path / "premium.sqlite3"
    backup = tmp_path / "backups" / "premium.sqlite3"
    with sqlite3.connect(source) as database:
        database.execute("CREATE TABLE users (id TEXT PRIMARY KEY)")
        database.execute("CREATE TABLE audit_log (id TEXT PRIMARY KEY)")
        database.execute("INSERT INTO users VALUES ('usr_one')")
        database.execute("INSERT INTO audit_log VALUES ('aud_one')")
        database.commit()
    proof = helper.backup_and_verify(source, backup)
    assert proof["integrity_check"] == "ok"
    assert proof["table_counts"] == {"audit_log": 1, "users": 1}
    assert len(proof["sha256"]) == 64
    assert backup.stat().st_mode & 0o777 == 0o600


def test_stripe_credentials_are_test_only_and_shell_safe() -> None:
    helper = _helper()
    result = helper.stripe_environment("sk_test_example", "price_example", "whsec_example")
    assert "STRIPE_API_KEY=sk_test_example" in result
    with pytest.raises(ValueError, match="test-mode"):
        helper.stripe_environment("sk_live_example", "price_example", "whsec_example")
    with pytest.raises(ValueError, match="single-line"):
        helper.stripe_environment("sk_test_example\nINJECTED=x", "price_example", "whsec_example")


@pytest.mark.parametrize(
    "origin,port",
    [
        ("http://host.example.ts.net:8443", 8443),
        ("https://host.example.ts.net:443", 8443),
        ("https://host.example.ts.net:8443/path", 8443),
    ],
)
def test_public_origin_must_match_private_https_listener(origin: str, port: int) -> None:
    helper = _helper()
    with pytest.raises(ValueError, match="canonical HTTPS"):
        helper._canonical_origin(origin, port)
