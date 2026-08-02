from __future__ import annotations

from pathlib import Path


def test_private_web_acceptance_helper_has_no_mutating_serve_command() -> None:
    source = (Path(__file__).parents[1] / "scripts" / "assert_private_web_serve.py").read_text()
    assert '[*command, "serve", "status", "--json"]' in source
    assert '"--https"' not in source
    assert '"off"' not in source
    assert '"funnel"' not in source.casefold()
