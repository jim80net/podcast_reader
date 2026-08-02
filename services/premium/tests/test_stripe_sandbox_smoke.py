from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("extra_args", "message"),
    [
        (["--base-url", "https://premium.test?redirect=evil"], "canonical HTTPS origin"),
        (["--base-url", "https://user:pass@premium.test"], "canonical HTTPS origin"),
        (
            ["--base-url", "https://premium.test", "--timeout-seconds", "0"],
            "positive integer",
        ),
    ],
)
def test_sandbox_smoke_rejects_unsafe_cli_values(extra_args: list[str], message: str) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "stripe_sandbox_smoke.py"
    result = subprocess.run(
        [sys.executable, str(script), *extra_args, "--email", "reader@example.com"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert message in result.stderr
