from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any


def read_status(command: list[str]) -> object:
    result = subprocess.run(
        [*command, "serve", "status", "--json"],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return json.loads(result.stdout)


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Capture or assert the existing Tailscale Serve configuration without mutation"
    )
    parser.add_argument("mode", choices=["capture", "assert"])
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--tailscale-command", default="tailscale")
    args = parser.parse_args()
    command = [args.tailscale_command]
    observed = read_status(command)
    if args.mode == "capture":
        args.baseline.write_text(canonical(observed) + "\n", encoding="utf-8")
        print(f"captured private-web Serve configuration in {args.baseline}")
        return
    expected: Any = json.loads(args.baseline.read_text(encoding="utf-8"))
    if canonical(observed) != canonical(expected):
        raise SystemExit("private-web Serve configuration changed; deployment acceptance failed")
    print("private-web Serve configuration is byte-semantically unchanged")


if __name__ == "__main__":
    main()
