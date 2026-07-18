#!/usr/bin/env python3
"""Check or list the byte-exact transcript CSP script policy."""

from __future__ import annotations

import argparse

from podcast_reader.engine.web_surface import _TRANSCRIPT_SCRIPT_POLICY


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("check", "list"),
        nargs="?",
        default="check",
        help="validate policy (default), or list derived and pinned digests",
    )
    args = parser.parse_args(argv)
    policy = _TRANSCRIPT_SCRIPT_POLICY

    if args.command == "list":
        for pin in policy.pins:
            state = "ok" if pin.actual_sha256 == pin.sha256 else "MISMATCH"
            print(f"{pin.name}\t{pin.actual_sha256}\t{state}")
        for sequence in policy.sequence_names:
            print(f"sequence\t{','.join(sequence) or '<none>'}")

    if policy.errors:
        for error in policy.errors:
            print(f"CSP policy error: {error}")
        return 1
    if args.command == "check":
        print(
            f"CSP script policy valid: {len(policy.pins)} byte pins, "
            f"{len(policy.sequence_names)} exact sequences"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
