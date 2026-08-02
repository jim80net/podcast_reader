from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def _helper() -> ModuleType:
    path = Path(__file__).parents[1] / "scripts" / "assert_private_web_serve.py"
    spec = importlib.util.spec_from_file_location("assert_private_web_serve", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASELINE: dict[str, Any] = {
    "TCP": {"443": {"HTTPS": True}},
    "Web": {"host.example.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8092"}}}},
}


def test_private_web_acceptance_helper_has_no_mutating_serve_command() -> None:
    source = (Path(__file__).parents[1] / "scripts" / "assert_private_web_serve.py").read_text()
    assert '[*command, "serve", "status", "--json"]' in source
    assert '"--https"' not in source
    assert '"off"' not in source
    assert '"funnel"' not in source.casefold()


def test_conflict_check_rejects_existing_listener_without_mutation() -> None:
    helper = _helper()
    helper.assert_listener_available(BASELINE, 8443)
    with pytest.raises(SystemExit, match="already configured"):
        helper.assert_listener_available(BASELINE, 443)


def test_activation_assertion_removes_only_the_exact_new_listener() -> None:
    helper = _helper()
    activated = {
        "TCP": {**BASELINE["TCP"], "8443": {"HTTPS": True}},
        "Web": {
            **BASELINE["Web"],
            "host.example.ts.net:8443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8090"}}},
        },
    }
    assert helper.without_expected_listener(activated, 8443, "http://127.0.0.1:8090") == BASELINE
    with pytest.raises(SystemExit, match="exact loopback"):
        helper.without_expected_listener(activated, 8443, "http://127.0.0.1:9999")


def test_activation_assertion_fails_if_original_private_mapping_changed() -> None:
    helper = _helper()
    activated = {
        "TCP": {"443": {"HTTPS": True}, "8443": {"HTTPS": True}},
        "Web": {
            "host.example.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:9999"}}},
            "host.example.ts.net:8443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8090"}}},
        },
    }
    comparable = helper.without_expected_listener(activated, 8443, "http://127.0.0.1:8090")
    assert helper.canonical(comparable) != helper.canonical(BASELINE)
