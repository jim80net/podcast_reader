"""Regression pins for the installed-app walkthrough contract."""

from pathlib import Path


def test_reader_waits_for_a_canonical_passage_not_a_control_status() -> None:
    walkthrough = (
        Path(__file__).parents[1] / "app" / "tests" / "install" / "walkthrough.mjs"
    ).read_text()

    assert ".locator('p[data-start][data-end]')" in walkthrough
    assert ".locator('p')" not in walkthrough
