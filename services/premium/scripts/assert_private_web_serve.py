from __future__ import annotations

import argparse
import copy
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


def _listener_web_keys(status: dict[str, Any], https_port: int) -> list[str]:
    suffix = f":{https_port}"
    web = status.get("Web", {})
    if not isinstance(web, dict):
        raise SystemExit("Tailscale Serve status has an invalid Web section")
    return [key for key in web if isinstance(key, str) and key.endswith(suffix)]


def assert_listener_available(status: object, https_port: int) -> None:
    if not isinstance(status, dict):
        raise SystemExit("Tailscale Serve status must be a JSON object")
    tcp = status.get("TCP", {})
    if not isinstance(tcp, dict):
        raise SystemExit("Tailscale Serve status has an invalid TCP section")
    if str(https_port) in tcp or https_port in tcp or _listener_web_keys(status, https_port):
        raise SystemExit(f"HTTPS listener {https_port} is already configured; stopping")


def without_expected_listener(status: object, https_port: int, target: str) -> object:
    if not isinstance(status, dict):
        raise SystemExit("Tailscale Serve status must be a JSON object")
    observed = copy.deepcopy(status)
    tcp = observed.get("TCP", {})
    if not isinstance(tcp, dict) or tcp.get(str(https_port)) != {"HTTPS": True}:
        raise SystemExit("premium Serve TCP listener is missing or not HTTPS-only")
    del tcp[str(https_port)]
    keys = _listener_web_keys(observed, https_port)
    if len(keys) != 1:
        raise SystemExit("premium Serve listener must have exactly one web mapping")
    web = observed["Web"]
    expected = {"Handlers": {"/": {"Proxy": target}}}
    if web[keys[0]] != expected:
        raise SystemExit("premium Serve listener does not target the exact loopback service")
    del web[keys[0]]
    return observed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Capture or assert the existing Tailscale Serve configuration without mutation"
    )
    parser.add_argument("mode", choices=["capture", "check-conflict", "assert"])
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--tailscale-command", default="tailscale")
    parser.add_argument("--new-https-port", type=int)
    parser.add_argument("--new-target")
    args = parser.parse_args()
    command = [args.tailscale_command]
    observed = read_status(command)
    if args.mode == "capture":
        args.baseline.write_text(canonical(observed) + "\n", encoding="utf-8")
        print(f"captured private-web Serve configuration in {args.baseline}")
        return
    if args.mode == "check-conflict":
        if args.new_https_port is None:
            parser.error("check-conflict requires --new-https-port")
        assert_listener_available(observed, args.new_https_port)
        print(f"HTTPS listener {args.new_https_port} is unused; no Serve mutation performed")
        return
    expected: Any = json.loads(args.baseline.read_text(encoding="utf-8"))
    if (args.new_https_port is None) != (args.new_target is None):
        parser.error("assert requires both --new-https-port and --new-target, or neither")
    comparable = observed
    if args.new_https_port is not None and args.new_target is not None:
        comparable = without_expected_listener(observed, args.new_https_port, args.new_target)
    if canonical(comparable) != canonical(expected):
        raise SystemExit("private-web Serve configuration changed; deployment acceptance failed")
    print("existing private-web Serve configuration is byte-semantically unchanged")


if __name__ == "__main__":
    main()
